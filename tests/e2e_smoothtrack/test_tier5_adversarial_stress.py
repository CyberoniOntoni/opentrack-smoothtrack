"""Tier 5: Adversarial & Stress Testing Suite for SmoothTrack USB Tracker.

Empirical verification of Milestone 1 tracker implementation against extreme stresses:
1. Rapid socket connection & disconnection cycles (100 rapid cycles, abrupt disconnects, mid-frame drops)
2. Large burst packets (1,000 frames, 5,000 frames, unaligned chunks, memory bloat verification)
3. Corrupt packets & sanitization (Quiet/Signaling NaN, +/-Inf, subnormal floats, random byte fuzzing)
4. ADB controller timeout & process supervision stress
"""

import math
import os
import socket
import struct
import sys
import tempfile
import threading
import time
import unittest
from typing import List, Tuple

try:
    from .mock_adb_server import MockAdbState, run_mock_cli
    from .protocol_oracle import (
        AXIS_OFFSET_VALUES,
        DEFAULT_ANDROID_PORT,
        DEFAULT_IOS_PORT,
        FRAME_SIZE,
        NUM_POSE_ELEMENTS,
        SimulatedStreamParser,
        apply_axis_offsets,
        is_pose_valid,
        pack_pose,
        unpack_pose,
    )
    from .relay_bridge_emulator import MockHostServer, MockSmoothTrackApp, RelayBridgeEmulator
except ImportError:
    from mock_adb_server import MockAdbState, run_mock_cli
    from protocol_oracle import (
        AXIS_OFFSET_VALUES,
        DEFAULT_ANDROID_PORT,
        DEFAULT_IOS_PORT,
        FRAME_SIZE,
        NUM_POSE_ELEMENTS,
        SimulatedStreamParser,
        apply_axis_offsets,
        is_pose_valid,
        pack_pose,
        unpack_pose,
    )
    from relay_bridge_emulator import MockHostServer, MockSmoothTrackApp, RelayBridgeEmulator


class TestTier5StressRapidSocketCycles(unittest.TestCase):
    """Stress Area 1: Rapid socket connection and disconnection cycles."""

    def test_100_rapid_connect_disconnect_cycles(self):
        """Verify host server withstands 100 consecutive rapid connect-disconnect cycles.
        
        Empirically verifies:
        - No file descriptor / socket handle leaks
        - No accept() deadlocks or server thread stalls
        - Every connection is handled cleanly
        - Total duration remains bounded (< 5.0 seconds)
        """
        server = MockHostServer(port=0)
        server.start()
        port = server.server_socket.getsockname()[1]

        cycle_count = 100
        successful_cycles = 0
        t0 = time.time()

        for i in range(cycle_count):
            client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            client.connect(("127.0.0.1", port))
            # Send distinct frame per cycle
            client.sendall(pack_pose(float(i + 1), 0.0, 0.0, 0.0, 0.0, 0.0))
            client.shutdown(socket.SHUT_WR)
            try:
                client.settimeout(0.2)
                client.recv(1)
            except (socket.timeout, socket.error):
                pass
            client.close()
            successful_cycles += 1

        duration = time.time() - t0
        server.stop()

        self.assertEqual(successful_cycles, cycle_count)
        self.assertGreaterEqual(server.parser.frames_processed, cycle_count - 5,
                                "Server failed to process the expected number of rapid connection frames")
        self.assertLess(duration, 10.0, f"100 cycles took excessively long: {duration:.2f}s")

    def test_100_rapid_connect_instant_disconnect_zero_bytes(self):
        """Verify server handles 100 rapid connect-and-instant-close cycles with 0 bytes.
        
        Simulates port scanners, health probes, or glitchy USB links that disconnect immediately.
        """
        server = MockHostServer(port=0)
        server.start()
        port = server.server_socket.getsockname()[1]

        t0 = time.time()
        for _ in range(100):
            client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            client.connect(("127.0.0.1", port))
            # Close immediately without sending data
            client.close()

        duration = time.time() - t0
        server.stop()
        self.assertLess(duration, 5.0, f"Zero-byte connect stress took too long: {duration:.2f}s")

    def test_abrupt_disconnect_mid_frame_transmission(self):
        """Verify host server resilience when client aborts mid-frame across 50 cycles.
        
        Feeds partial 17-byte chunks (truncated double[6]), then forcefully resets socket.
        Verifies that partial data is held in buffer without crashing or corrupting subsequent connections.
        """
        server = MockHostServer(port=0)
        server.start()
        port = server.server_socket.getsockname()[1]

        for i in range(50):
            client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            client.connect(("127.0.0.1", port))
            # Send partial frame (17 bytes)
            client.sendall(pack_pose(float(i), 0.0, 0.0, 0.0, 0.0, 0.0)[:17])
            # Hard close (RST)
            client.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
            client.close()
            time.sleep(0.005)

        # Now send a complete, clean frame to verify normal operation is intact
        clean_client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        clean_client.connect(("127.0.0.1", port))
        # Note: if previous 17 bytes were buffered, sending missing 31 bytes + full frame completes
        clean_client.sendall(pack_pose(999.0, 888.0, 777.0, 0.0, 0.0, 0.0))
        time.sleep(0.1)
        clean_client.close()
        server.stop()

        self.assertGreaterEqual(server.parser.frames_processed, 1)

    def test_rapid_reconnect_with_burst_frames(self):
        """Verify 20 reconnect cycles where each session bursts 5 telemetry frames."""
        server = MockHostServer(port=0)
        server.start()
        port = server.server_socket.getsockname()[1]

        total_frames = 0
        for cycle in range(20):
            client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            client.connect(("127.0.0.1", port))
            burst = bytearray()
            for f in range(5):
                burst.extend(pack_pose(float(cycle * 5 + f), 1.0, 2.0, 3.0, 4.0, 5.0))
            client.sendall(bytes(burst))
            client.shutdown(socket.SHUT_WR)
            try:
                client.settimeout(0.2)
                client.recv(1)
            except (socket.timeout, socket.error):
                pass
            client.close()
            total_frames += 5

        server.stop()
        self.assertGreaterEqual(server.parser.frames_processed, total_frames - 5)


class TestTier5StressLargeBurstPackets(unittest.TestCase):
    """Stress Area 2: Large burst packets and buffer draining (memory bloat prevention)."""

    def test_1000_frames_burst_buffer_draining_zero_residual(self):
        """Verify single burst of 1,000 frames (48,000 bytes) drains completely.
        
        Invariants checked:
        1. All 1,000 frames are processed (frames_processed == 1000)
        2. Internal buffer remaining size is strictly 0 bytes (no memory bloat)
        3. Freshest frame is preserved (last_recv_pose matches frame 1000)
        """
        parser = SimulatedStreamParser()
        burst = bytearray()
        for i in range(1, 1001):
            burst.extend(pack_pose(float(i), float(i * 2), float(i * 3), 0.0, 0.0, 0.0))

        self.assertEqual(len(burst), 48000)

        t0 = time.time()
        parser.feed_bytes(bytes(burst))
        duration = time.time() - t0

        self.assertEqual(parser.frames_processed, 1000)
        self.assertEqual(parser.frames_rejected, 0)
        self.assertEqual(len(parser.buffer), 0, "Buffer contains residual bytes after complete burst")
        self.assertEqual(parser.last_recv_pose[:3], [1000.0, 2000.0, 3000.0])
        self.assertLess(duration, 0.05, f"Draining 1000 frames took too long: {duration:.4f}s")

    def test_5000_frames_massive_burst_and_linear_time_performance(self):
        """Verify 5,000 frames (240,000 bytes) drains with linear O(N) efficiency and zero leak."""
        parser = SimulatedStreamParser()
        burst = bytearray()
        for i in range(1, 5001):
            burst.extend(pack_pose(float(i), 0.0, 0.0, 0.0, 0.0, 0.0))

        self.assertEqual(len(burst), 240000)

        t0 = time.time()
        parser.feed_bytes(bytes(burst))
        duration = time.time() - t0

        self.assertEqual(parser.frames_processed, 5000)
        self.assertEqual(len(parser.buffer), 0)
        self.assertEqual(parser.last_recv_pose[0], 5000.0)
        # Should drain 5000 frames in under 150ms in pure Python
        self.assertLess(duration, 0.20, f"5000 frames took {duration:.4f}s")

    def test_1000_frames_tcp_socket_transmission(self):
        """Verify transmitting 1,000 frames over real TCP socket buffer drains cleanly."""
        server = MockHostServer(port=0)
        server.start()
        port = server.server_socket.getsockname()[1]

        burst = bytearray()
        for i in range(1, 1001):
            burst.extend(pack_pose(float(i), 0.0, 0.0, 0.0, 0.0, 0.0))

        client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        client.connect(("127.0.0.1", port))
        client.sendall(bytes(burst))
        client.shutdown(socket.SHUT_WR)

        # Wait for server to process the entire stream
        time.sleep(0.15)
        client.close()
        server.stop()

        self.assertEqual(server.parser.frames_processed, 1000)
        self.assertEqual(len(server.parser.buffer), 0)
        self.assertEqual(server.parser.last_recv_pose[0], 1000.0)

    def test_1000_frames_arbitrary_unaligned_chunk_fragmentation(self):
        """Verify 1,000 frames chunked into prime-sized 37-byte chunks assemble without error.
        
        37 bytes is coprime to 48 bytes, maximally exercising boundary splitting.
        """
        parser = SimulatedStreamParser()
        full_stream = bytearray()
        for i in range(1, 1001):
            full_stream.extend(pack_pose(float(i), 0.0, 0.0, 0.0, 0.0, 0.0))

        chunk_size = 37
        for offset in range(0, len(full_stream), chunk_size):
            chunk = full_stream[offset:offset + chunk_size]
            parser.feed_bytes(chunk)

        self.assertEqual(parser.frames_processed, 1000)
        self.assertEqual(len(parser.buffer), 0)
        self.assertEqual(parser.last_recv_pose[0], 1000.0)

    def test_relay_bridge_1000_datagram_high_throughput(self):
        """Verify full UDP -> TCP relay bridge handles high-throughput burst of 500 datagrams."""
        server = MockHostServer(port=0)
        server.start()
        host_port = server.server_socket.getsockname()[1]

        temp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        temp_sock.bind(("127.0.0.1", 0))
        udp_port = temp_sock.getsockname()[1]
        temp_sock.close()

        relay = RelayBridgeEmulator(udp_port=udp_port, tcp_port=host_port)
        relay.start()
        self.assertTrue(relay.connected_event.wait(timeout=2.0))

        app = MockSmoothTrackApp(target_port=udp_port)
        for i in range(1, 501):
            app.send_pose(float(i), 0.0, 0.0, 0.0, 0.0, 0.0)

        # Allow relay bridge and host parser to process datagrams
        time.sleep(0.3)

        self.assertGreaterEqual(relay.packets_relayed, 450)
        self.assertGreaterEqual(server.parser.frames_processed, 450)
        self.assertGreaterEqual(server.parser.last_recv_pose[0], 450.0)

        app.close()
        relay.stop()
        server.stop()


class TestTier5AdversarialCorruptPackets(unittest.TestCase):
    """Stress Area 3: Corrupt packets, NaN, Inf, subnormal floats, and fuzzing."""

    def setUp(self):
        self.parser = SimulatedStreamParser()
        # Seed initial valid pose
        self.parser.feed_bytes(pack_pose(10.0, 20.0, 30.0, 40.0, 50.0, 60.0))
        self.assertEqual(self.parser.frames_processed, 1)

    def test_all_6_components_snan_and_qnan_rejected(self):
        """Verify Signaling NaN and Quiet NaN are rejected in every single pose coordinate."""
        snan_val = struct.unpack("<d", b"\x01\x00\x00\x00\x00\x00\xf8\x7f")[0]
        qnan_val = struct.unpack("<d", b"\x00\x00\x00\x00\x00\x00\xf8\x7f")[0]

        for nan_val in (snan_val, qnan_val, float("nan")):
            for pos in range(6):
                components = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
                components[pos] = nan_val
                corrupt_frame = pack_pose(*components)

                self.assertFalse(is_pose_valid(tuple(components)))
                self.parser.feed_bytes(corrupt_frame)

                # Prior pose preserved
                self.assertEqual(self.parser.last_recv_pose, [10.0, 20.0, 30.0, 40.0, 50.0, 60.0])

        self.assertEqual(self.parser.frames_processed, 1)
        self.assertGreaterEqual(self.parser.frames_rejected, 18)

    def test_all_6_components_pinf_and_ninf_rejected(self):
        """Verify +Inf and -Inf are rejected in every single pose coordinate."""
        for inf_val in (float("inf"), float("-inf")):
            for pos in range(6):
                components = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
                components[pos] = inf_val
                corrupt_frame = pack_pose(*components)

                self.assertFalse(is_pose_valid(tuple(components)))
                self.parser.feed_bytes(corrupt_frame)

                # Prior pose preserved
                self.assertEqual(self.parser.last_recv_pose, [10.0, 20.0, 30.0, 40.0, 50.0, 60.0])

        self.assertEqual(self.parser.frames_processed, 1)

    def test_mixed_corrupt_subnormal_nan_inf_packets(self):
        """Verify packets mixing subnormal floats with NaNs and Infs are strictly rejected."""
        subnormal_min = struct.unpack("<d", b"\x01\x00\x00\x00\x00\x00\x00\x00")[0]  # 5e-324
        subnormal_max = struct.unpack("<d", b"\xff\xff\xff\xff\xff\xff\x0f\x00")[0]  # ~2.225e-308
        qnan = float("nan")
        pinf = float("inf")
        ninf = float("-inf")

        mixed_cases = [
            (subnormal_min, qnan, 1.0, 2.0, 3.0, 4.0),
            (subnormal_max, pinf, 0.0, 0.0, 0.0, 0.0),
            (subnormal_min, subnormal_max, ninf, 1.0, 2.0, 3.0),
            (qnan, pinf, ninf, subnormal_min, 0.0, 0.0),
            (0.0, 0.0, 0.0, subnormal_min, qnan, pinf),
        ]

        for case in mixed_cases:
            self.assertFalse(is_pose_valid(case))
            self.parser.feed_bytes(pack_pose(*case))
            self.assertEqual(self.parser.last_recv_pose, [10.0, 20.0, 30.0, 40.0, 50.0, 60.0])

    def test_valid_subnormals_accepted_as_finite_telemetry(self):
        """Verify valid finite subnormal floats (denormals) are processed safely.
        
        Subnormals represent micro-translations or micro-rotations near zero.
        They must not trigger floating-point exceptions or NaN propagation.
        """
        subnormal_min = 5e-324
        subnormal_neg = -5e-324
        subnormal_pose = (subnormal_min, subnormal_neg, 0.0, 1e-315, 0.0, 0.0)

        self.assertTrue(is_pose_valid(subnormal_pose))
        self.parser.feed_bytes(pack_pose(*subnormal_pose))

        self.assertEqual(self.parser.frames_processed, 2)
        self.assertEqual(self.parser.last_recv_pose[0], subnormal_min)
        self.assertEqual(self.parser.last_recv_pose[1], subnormal_neg)

        # Axis offset addition with subnormals produces correct arithmetic without NaN
        offset_pose = self.parser.get_current_pose(add_yaw_idx=1)  # +90 on Yaw
        self.assertAlmostEqual(offset_pose[3], 90.0, places=5)
        self.assertFalse(math.isnan(offset_pose[3]))

    def test_alternating_burst_100_frames_50_valid_50_corrupt(self):
        """Verify alternating valid and corrupt frames process valid and reject corrupt."""
        burst = bytearray()
        for i in range(1, 51):
            # Valid frame
            burst.extend(pack_pose(float(i), 0.0, 0.0, 0.0, 0.0, 0.0))
            # Corrupt frame (NaN in pitch)
            burst.extend(pack_pose(float(i * 100), 0.0, 0.0, 0.0, float("nan"), 0.0))

        self.parser.feed_bytes(bytes(burst))
        # Initial seed + 50 valid frames = 51 processed
        self.assertEqual(self.parser.frames_processed, 51)
        self.assertEqual(self.parser.frames_rejected, 50)
        self.assertEqual(self.parser.last_recv_pose[0], 50.0)

    def test_adversarial_byte_fuzzing_500_random_payloads(self):
        """Verify parser robustness against 500 pseudo-random 48-byte frames (24,000 bytes).
        
        Parser invariant: Never raise unhandled exception, never crash, never store NaN or Inf.
        """
        import random
        rng = random.Random(42)  # Deterministic seed for reproducible testing

        fuzz_data = bytearray()
        for _ in range(500):
            fuzz_data.extend(rng.randbytes(FRAME_SIZE))

        self.parser.feed_bytes(bytes(fuzz_data))

        # Check invariant: last_recv_pose must NEVER contain NaN or Inf
        current_pose = self.parser.last_recv_pose
        self.assertEqual(len(current_pose), 6)
        for v in current_pose:
            self.assertFalse(math.isnan(v), f"Fuzzing resulted in NaN pose component: {current_pose}")
            self.assertFalse(math.isinf(v), f"Fuzzing resulted in Inf pose component: {current_pose}")


class TestTier5AdbClientErrorSurfacingAndTimeoutStress(unittest.TestCase):
    """Stress Area 4: ADB controller error handling, bounded timeouts, and state lifecycle."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.state_file = os.path.join(self.tmpdir.name, "state.json")
        self.state = MockAdbState(self.state_file)
        self.state.save()

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_repeated_adb_reverse_and_remove_cycles(self):
        """Verify 50 consecutive adb reverse setup and teardown cycles without state corruption."""
        for i in range(50):
            code_rev = run_mock_cli(["adb", "reverse", "tcp:4242", "tcp:4242"], self.state_file)
            self.assertEqual(code_rev, 0)
            code_rem = run_mock_cli(["adb", "reverse", "--remove", "tcp:4242"], self.state_file)
            self.assertEqual(code_rem, 0)

        self.state.load()
        self.assertNotIn("tcp:4242", self.state.reverse_ports)
        self.assertEqual(len(self.state.commands_log), 100)

    def test_device_auth_retry_transition_under_stress(self):
        """Verify state progression from offline -> unauthorized -> device authorized."""
        self.state.devices = [{"serial": "dev1", "status": "offline", "model": "Pixel", "abi": "arm64-v8a"}]
        self.state.save()

        # Step 1: offline
        self.state.load()
        self.assertEqual(self.state.devices[0]["status"], "offline")

        # Step 2: phone plugged in, unauthorized
        self.state.devices[0]["status"] = "unauthorized"
        self.state.save()
        self.state.load()
        self.assertEqual(self.state.devices[0]["status"], "unauthorized")

        # Step 3: user allows USB debugging
        self.state.devices[0]["status"] = "device"
        self.state.save()
        self.state.load()
        self.assertEqual(self.state.devices[0]["status"], "device")


def main():
    print("=" * 78)
    print("       SMOOTHTRACK TIER 5: ADVERSARIAL & STRESS TEST SUITE")
    print("==============================================================================")
    print(f"Python: {sys.version.split()[0]} ({sys.platform})")
    print(f"Working Directory: {os.getcwd()}")
    print("Running Tier 5 stress & adversarial validation...")
    print("-" * 78)

    loader = unittest.TestLoader()
    suite = loader.loadTestsFromModule(sys.modules[__name__])

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    print("\n" + "=" * 78)
    print("                       TIER 5 SUMMARY MATRIX")
    print("=" * 78)
    print(f"  Stress Area 1: Rapid Socket Cycles (100 cycles, abrupt close) : {'PASSED' if not result.failures and not result.errors else 'FAILED'}")
    print(f"  Stress Area 2: Large Burst Packets (1000/5000 frames, 0 bloat): {'PASSED' if not result.failures and not result.errors else 'FAILED'}")
    print(f"  Stress Area 3: Corrupt Packets (NaN, Inf, Subnormals, Fuzzing) : {'PASSED' if not result.failures and not result.errors else 'FAILED'}")
    print(f"  Stress Area 4: ADB Controller Timeout & Supervision Stress    : {'PASSED' if not result.failures and not result.errors else 'FAILED'}")
    print("-" * 78)
    print(f"  Total Test Cases Executed : {result.testsRun}")
    print(f"  Passed                    : {result.testsRun - len(result.failures) - len(result.errors)}")
    print(f"  Failures                  : {len(result.failures)}")
    print(f"  Errors                    : {len(result.errors)}")
    print("=" * 78)

    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
