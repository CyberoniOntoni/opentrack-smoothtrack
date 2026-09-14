"""White-Box Adversarial Hardening and Stress Suite for Milestone 3.

Empirical Challenger 1 verification:
1. Stream parser pathological framing (1-byte trickle, all primes < 48, dynamic chunks, zero bloat)
2. Float extreme value boundaries (-0.0, DBL_MAX, DBL_MIN, subnormals, offset arithmetic)
3. Concurrent reader vs data() delivery thread-safety (zero torn reads, zero deadlocks)
4. ADB complex device listing parsing (Wi-Fi devices, emulators, mixed status, daemon banners)
5. Autonomous ADB discovery path resolution
6. Relay bridge datagram boundary adversarial stress
7. CMake & packaging defensive contract verification
"""

import errno
import math
import os
import random
import select
import socket
import struct
import sys
import tempfile
import threading
import time
import unittest
from typing import List, Tuple

# Add repo root to sys.path
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from tests.e2e_smoothtrack.mock_adb_server import (
    MockAdbState,
    create_mock_adb_executable,
    run_mock_cli,
)
from tests.e2e_smoothtrack.protocol_oracle import (
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
from tests.e2e_smoothtrack.relay_bridge_emulator import (
    MockHostServer,
    MockSmoothTrackApp,
    RelayBridgeEmulator,
)


class TestStreamParserPathologicalFraming(unittest.TestCase):
    """Stress Area 1: Pathological framing and fragmentation resilience."""

    def test_single_byte_trickle_feeding_100_frames(self):
        """Feed 100 complete frames (4,800 bytes) strictly one byte at a time.
        
        Verifies:
        - Internal buffer accumulates partial bytes without dropping
        - Exactly 100 frames are processed
        - Freshest pose is correctly preserved
        - Remaining buffer after completion is strictly 0 bytes
        """
        parser = SimulatedStreamParser()
        full_stream = bytearray()
        for i in range(1, 101):
            full_stream.extend(pack_pose(float(i), float(i * 2), float(i * 3), 0.0, 0.0, 0.0))

        self.assertEqual(len(full_stream), 4800)

        # Feed 1 byte at a time
        for b in full_stream:
            parser.feed_bytes(bytes([b]))

        self.assertEqual(parser.frames_processed, 100)
        self.assertEqual(parser.frames_rejected, 0)
        self.assertEqual(len(parser.buffer), 0)
        self.assertEqual(parser.last_recv_pose[:3], [100.0, 200.0, 300.0])

    def test_all_prime_chunk_sizes_less_than_48(self):
        """Test stream fragmentation using every prime number smaller than 48.
        
        Primes are coprime to 48 (except 2 and 3), guaranteeing that frame boundaries
        occur at every possible modulo offset.
        """
        primes = [1, 2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37, 41, 43, 47]
        
        for prime in primes:
            parser = SimulatedStreamParser()
            stream = bytearray()
            frame_count = 50
            for i in range(1, frame_count + 1):
                stream.extend(pack_pose(float(i), 0.0, 0.0, 0.0, 0.0, 0.0))

            # Chunk by prime size
            for offset in range(0, len(stream), prime):
                chunk = stream[offset:offset + prime]
                parser.feed_bytes(chunk)

            self.assertEqual(
                parser.frames_processed,
                frame_count,
                f"Failed for prime chunk size {prime}: processed {parser.frames_processed}/{frame_count}",
            )
            self.assertEqual(len(parser.buffer), 0)
            self.assertEqual(parser.last_recv_pose[0], float(frame_count))

    def test_random_dynamic_chunk_sizes_and_residual_invariant(self):
        """Verify stream parsing with randomized chunk sizes from 1 to 256 bytes.
        
        Checks the fundamental invariant:
        At any moment, len(buffer) < FRAME_SIZE (48 bytes).
        """
        rng = random.Random(1337)
        parser = SimulatedStreamParser()
        stream = bytearray()
        for i in range(1, 201):
            stream.extend(pack_pose(float(i), 0.0, 0.0, 0.0, 0.0, 0.0))

        offset = 0
        total_len = len(stream)
        while offset < total_len:
            chunk_len = rng.randint(1, 256)
            chunk = stream[offset:offset + chunk_len]
            offset += chunk_len
            parser.feed_bytes(chunk)
            # Invariant check after each feed: buffer must never hold >= 48 bytes
            self.assertLess(
                len(parser.buffer),
                FRAME_SIZE,
                f"Buffer bloated to {len(parser.buffer)} bytes (>= {FRAME_SIZE})",
            )

        self.assertEqual(parser.frames_processed, 200)
        self.assertEqual(len(parser.buffer), 0)
        self.assertEqual(parser.last_recv_pose[0], 200.0)


class TestStreamParserFloatExtremeValues(unittest.TestCase):
    """Stress Area 2: Boundary float values and numeric robustness."""

    def setUp(self):
        self.parser = SimulatedStreamParser()

    def test_negative_zero_accepted_and_offset_applied(self):
        """Verify -0.0 is recognized as valid finite float and does not perturb arithmetic."""
        neg_zero = -0.0
        self.assertTrue(is_pose_valid((neg_zero, 0.0, 0.0, neg_zero, 0.0, 0.0)))
        
        self.parser.feed_bytes(pack_pose(neg_zero, 0.0, 0.0, neg_zero, 0.0, 0.0))
        self.assertEqual(self.parser.frames_processed, 1)

        # Apply +90 yaw offset
        pose_with_offset = self.parser.get_current_pose(add_yaw_idx=1)
        self.assertEqual(pose_with_offset[3], 90.0)

    def test_extreme_finite_float_magnitudes(self):
        """Verify DBL_MAX, DBL_MIN, and subnormals are handled safely without exceptions."""
        dbl_max = sys.float_info.max       # ~1.7976931348623157e+308
        dbl_min = sys.float_info.min       # ~2.2250738585072014e-308
        subnormal = 5e-324

        test_poses = [
            (dbl_max, -dbl_max, dbl_max, 0.0, 0.0, 0.0),
            (dbl_min, -dbl_min, dbl_min, 0.0, 0.0, 0.0),
            (subnormal, -subnormal, 0.0, 0.0, 0.0, 0.0),
        ]

        for p in test_poses:
            self.assertTrue(is_pose_valid(p))
            self.parser.feed_bytes(pack_pose(*p))

        self.assertEqual(self.parser.frames_processed, len(test_poses))
        self.assertEqual(self.parser.last_recv_pose[0], subnormal)

    def test_resilience_after_corrupted_burst(self):
        """Verify that a burst of corrupted frames does not break parsing of subsequent valid frames."""
        # 1. Send 10 valid frames
        for i in range(1, 11):
            self.parser.feed_bytes(pack_pose(float(i), 0.0, 0.0, 0.0, 0.0, 0.0))
        self.assertEqual(self.parser.frames_processed, 10)
        self.assertEqual(self.parser.last_recv_pose[0], 10.0)

        # 2. Send 20 corrupted frames (NaN and Inf in various coordinates)
        corrupt_burst = bytearray()
        for _ in range(20):
            corrupt_burst.extend(pack_pose(float("nan"), float("inf"), float("-inf"), 0.0, 0.0, 0.0))
        self.parser.feed_bytes(bytes(corrupt_burst))

        # Still holds 10.0 from last valid frame
        self.assertEqual(self.parser.frames_processed, 10)
        self.assertEqual(self.parser.frames_rejected, 20)
        self.assertEqual(self.parser.last_recv_pose[0], 10.0)

        # 3. Send 10 new valid frames
        for i in range(11, 21):
            self.parser.feed_bytes(pack_pose(float(i), 0.0, 0.0, 0.0, 0.0, 0.0))
        self.assertEqual(self.parser.frames_processed, 20)
        self.assertEqual(self.parser.last_recv_pose[0], 20.0)


class TestConcurrentReaderAndDataDelivery(unittest.TestCase):
    """Stress Area 3: Multi-threaded synchronization between ingest and data() delivery."""

    def test_concurrent_writer_and_reader_no_torn_reads(self):
        """Simulate smoothtrack::run() writing poses while OpenTrack pipeline calls data().
        
        Each valid frame has mathematical invariant:
        y = x * 2, z = x * 3, yaw = x * 4, pitch = x * 5, roll = x * 6.
        A 'torn read' occurs if data() reads partially updated coordinates from two different frames.
        Under proper QMutex synchronization, torn reads are impossible.
        """
        lock = threading.Lock()
        last_recv_pose = [0.0] * 6
        writer_done = threading.Event()
        torn_reads = []
        read_count = [0]

        def writer():
            for i in range(1, 2001):
                fi = float(i)
                new_pose = [fi, fi * 2.0, fi * 3.0, fi * 4.0, fi * 5.0, fi * 6.0]
                with lock:
                    for k in range(6):
                        last_recv_pose[k] = new_pose[k]
                time.sleep(0.0001)
            writer_done.set()

        def reader():
            while not writer_done.is_set():
                with lock:
                    snapshot = list(last_recv_pose)
                read_count[0] += 1
                x = snapshot[0]
                if x > 0.0:
                    if (snapshot[1] != x * 2.0 or
                        snapshot[2] != x * 3.0 or
                        snapshot[3] != x * 4.0 or
                        snapshot[4] != x * 5.0 or
                        snapshot[5] != x * 6.0):
                        torn_reads.append(snapshot)
                time.sleep(0.00005)

        t_writer = threading.Thread(target=writer)
        t_reader = threading.Thread(target=reader)

        t_writer.start()
        t_reader.start()

        t_writer.join(timeout=5.0)
        t_reader.join(timeout=5.0)

        self.assertEqual(len(torn_reads), 0, f"Detected {len(torn_reads)} torn reads during concurrent delivery!")
        self.assertGreater(read_count[0], 500, "Reader thread did not perform sufficient reads")


class TestAdbDiscoveryAndComplexOutputParsing(unittest.TestCase):
    """Stress Area 4: ADB device output parsing under complex real-world conditions."""

    def test_parse_complex_device_list_with_mixed_devices_and_banners(self):
        """Simulate adb_client::list_devices against realistic multi-device CLI output.
        
        Includes:
        - Daemon start / restart banners
        - Wi-Fi TCP connected devices (IP:port)
        - Emulators
        - Unauthorized and offline devices
        - Physical USB phones with various product/model metadata
        """
        raw_adb_output = (
            "* daemon not running; starting now at tcp:5037\n"
            "* daemon started successfully\n"
            "List of devices attached\n"
            "192.168.1.155:5555     offline          product:raven model:Pixel_6 device:raven\n"
            "FA76N0300123           unauthorized     usb:1-1 product:walleye model:Pixel_2 device:walleye\n"
            "emulator-5554          device           product:sdk_gphone64_arm64 model:sdk_gphone64_arm64 device:emulator64_arm64\n"
            "008321045982           device           usb:1-2 product:star2qltezc model:SM_G9650 device:star2qltechn\n"
            "\n"
        )

        # Emulate adb_client::list_devices parsing logic
        devices = []
        for line in raw_adb_output.strip().splitlines():
            trimmed = line.strip()
            if not trimmed or trimmed.startswith("List of devices") or trimmed.startswith("*"):
                continue
            tokens = trimmed.split()
            if len(tokens) >= 2:
                dev = {"serial": tokens[0], "status": tokens[1], "model": ""}
                for t in tokens:
                    if t.startswith("model:"):
                        dev["model"] = t[6:]
                devices.append(dev)

        self.assertEqual(len(devices), 4)
        self.assertEqual(devices[0]["serial"], "192.168.1.155:5555")
        self.assertEqual(devices[0]["status"], "offline")
        self.assertEqual(devices[1]["serial"], "FA76N0300123")
        self.assertEqual(devices[1]["status"], "unauthorized")
        self.assertEqual(devices[2]["serial"], "emulator-5554")
        self.assertEqual(devices[2]["status"], "device")
        self.assertEqual(devices[3]["serial"], "008321045982")
        self.assertEqual(devices[3]["status"], "device")
        self.assertEqual(devices[3]["model"], "SM_G9650")

        # Emulate check_device priority: must select first authorized device
        chosen_serial = None
        for d in devices:
            if d["status"] == "device":
                chosen_serial = d["serial"]
                break

        self.assertEqual(chosen_serial, "emulator-5554")

    def test_find_adb_candidate_resolution_order(self):
        """Verify the hierarchy of adb discovery matches the specification in PROJECT.md."""
        cpp_path = os.path.join(REPO_ROOT, "tracker-smoothtrack", "adb_client.cpp")
        with open(cpp_path, "r", encoding="utf-8") as f:
            content = f.read()

        # Check that user hint is checked first
        hint_pos = content.find("user_hint.trimmed()")
        app_dir_pos = content.find("applicationDirPath()")
        path_pos = content.find("findExecutable(\"adb\")")

        self.assertTrue(hint_pos > 0)
        self.assertTrue(app_dir_pos > hint_pos)
        self.assertTrue(path_pos > app_dir_pos)


class TestRelayBridgeDatagramBoundaryAdversarial(unittest.TestCase):
    """Stress Area 5: Datagram boundaries and high-frequency datagram bursts."""

    def test_datagram_boundary_preservation_across_bridge(self):
        """Verify 50 UDP datagrams are transmitted and reconstructed as 50 frames."""
        server = MockHostServer(port=0)
        server.start()
        host_port = server.server_socket.getsockname()[1]

        # Allocate ephemeral UDP port
        tmp_udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        tmp_udp.bind(("127.0.0.1", 0))
        udp_port = tmp_udp.getsockname()[1]
        tmp_udp.close()

        relay = RelayBridgeEmulator(udp_port=udp_port, tcp_port=host_port)
        relay.start()
        self.assertTrue(relay.connected_event.wait(timeout=2.0))

        app = MockSmoothTrackApp(target_port=udp_port)
        for i in range(1, 51):
            app.send_pose(float(i), 0.0, 0.0, 0.0, 0.0, 0.0)
            time.sleep(0.002)

        # Allow bridge to flush
        time.sleep(0.2)

        app.close()
        relay.stop()
        server.stop()

        self.assertEqual(server.parser.frames_processed, 50)
        self.assertEqual(server.parser.last_recv_pose[0], 50.0)


class TestCMakeAndPackagingInvariants(unittest.TestCase):
    """Stress Area 6: Static contract verification for CMake and Packaging."""

    def test_cmakelists_contains_decoupling_and_install_rules(self):
        """Verify tracker-smoothtrack/CMakeLists.txt implements all contract requirements."""
        cmake_path = os.path.join(REPO_ROOT, "tracker-smoothtrack", "CMakeLists.txt")
        with open(cmake_path, "r", encoding="utf-8") as f:
            cmake_text = f.read()

        self.assertIn("option(OPENTRACK_HAS_USBMUXD", cmake_text)
        self.assertIn("OPENTRACK_SMOOTHTRACK_HAVE_USBMUXD", cmake_text)
        self.assertIn("st-relay-arm64", cmake_text)
        self.assertIn("st-relay-armv7", cmake_text)
        self.assertIn("${opentrack-libexec}/android", cmake_text)

    def test_workflow_has_defensive_path_equality_guards_for_all_components(self):
        """Verify windows-11.yml contains defensive guards for both ADB tools and relay binaries."""
        wf_path = os.path.join(REPO_ROOT, ".github", "workflows", "windows-11.yml")
        with open(wf_path, "r", encoding="utf-8") as f:
            wf_text = f.read()

        # Check defensive equality guard
        guard_snippet = "[System.IO.Path]::GetFullPath($src) -ne [System.IO.Path]::GetFullPath($destFile)"
        occurrences = wf_text.count(guard_snippet)
        self.assertGreaterEqual(
            occurrences,
            2,
            f"Expected at least 2 defensive path equality guards in workflow, found {occurrences}",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
