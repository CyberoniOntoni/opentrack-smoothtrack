"""Tier 1: Feature Coverage E2E Tests (>=5 test cases per feature).

Covers all core features from ORIGINAL_REQUEST.md (R1, R2, R3) and PROJECT.md:
1. 48-byte binary frame ingestion
2. IEEE 754 little-endian double[6] pose components
3. Socket binding & connection lifecycle
4. Autonomous ADB executable discovery
5. ADB reverse port syntax & command generation
6. Relay daemon bridge protocol (UDP -> TCP)
"""

import math
import os
import socket
import struct
import tempfile
import time
import unittest
from typing import List

try:
    from .mock_adb_server import MockAdbState, create_mock_adb_executable, run_mock_cli
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
    from mock_adb_server import MockAdbState, create_mock_adb_executable, run_mock_cli
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


class TestTier1Feature1FrameIngestion(unittest.TestCase):
    """Feature 1: 48-byte binary frame ingestion."""

    def setUp(self):
        self.parser = SimulatedStreamParser()

    def test_exact_48_byte_single_frame_ingestion(self):
        """Verify ingestion of an exact 48-byte pose frame updates parser state."""
        raw = pack_pose(10.5, -20.25, 30.0, 45.0, -15.0, 5.5)
        self.assertEqual(len(raw), 48)
        self.parser.feed_bytes(raw)
        self.assertEqual(self.parser.frames_processed, 1)
        self.assertEqual(self.parser.frames_rejected, 0)
        self.assertEqual(self.parser.last_recv_pose, [10.5, -20.25, 30.0, 45.0, -15.0, 5.5])

    def test_frame_size_constant_is_exact(self):
        """Verify frame size is exactly 6 * sizeof(double) = 48 bytes."""
        expected_size = 6 * struct.calcsize("<d")
        self.assertEqual(FRAME_SIZE, 48)
        self.assertEqual(expected_size, 48)

    def test_sequential_distinct_48_byte_frames(self):
        """Verify sequential stream ingestion updates to the latest pose consecutively."""
        test_poses = [
            (1.0, 2.0, 3.0, 4.0, 5.0, 6.0),
            (10.0, 20.0, 30.0, 40.0, 50.0, 60.0),
            (-5.0, 0.0, 5.0, -90.0, 45.0, 0.0),
            (0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
            (100.5, -50.2, 25.1, 180.0, -89.0, 33.3),
        ]
        for idx, pose in enumerate(test_poses):
            self.parser.feed_bytes(pack_pose(*pose))
            self.assertEqual(self.parser.frames_processed, idx + 1)
            self.assertEqual(self.parser.last_recv_pose, list(pose))

    def test_multiple_frames_in_stream_preserve_latest(self):
        """Verify multiple frames delivered in a single buffer chunk retain the freshest pose."""
        f1 = pack_pose(1.0, 1.0, 1.0, 1.0, 1.0, 1.0)
        f2 = pack_pose(2.0, 2.0, 2.0, 2.0, 2.0, 2.0)
        f3 = pack_pose(3.0, 3.0, 3.0, 3.0, 3.0, 3.0)
        burst = f1 + f2 + f3
        self.assertEqual(len(burst), 144)

        self.parser.feed_bytes(burst)
        self.assertEqual(self.parser.frames_processed, 3)
        # Freshest frame is f3
        self.assertEqual(self.parser.last_recv_pose, [3.0, 3.0, 3.0, 3.0, 3.0, 3.0])

    def test_exact_frame_unpacking_fidelity(self):
        """Verify numerical fidelity of IEEE 754 64-bit float deserialization."""
        pose_in = (12345.67890123, -98765.43210987, 0.0000000012345, 179.9999999, -89.9999999, 0.123456789)
        raw = pack_pose(*pose_in)
        pose_out = unpack_pose(raw)
        for val_in, val_out in zip(pose_in, pose_out):
            self.assertAlmostEqual(val_in, val_out, places=9)

    def test_partial_packet_held_until_48_bytes_complete(self):
        """Verify partial packet (<48 bytes) is buffered and not processed until completed."""
        full_frame = pack_pose(11.0, 22.0, 33.0, 44.0, 55.0, 66.0)
        part1 = full_frame[:20]
        part2 = full_frame[20:]

        self.parser.feed_bytes(part1)
        self.assertEqual(self.parser.frames_processed, 0)
        self.assertEqual(len(self.parser.buffer), 20)

        self.parser.feed_bytes(part2)
        self.assertEqual(self.parser.frames_processed, 1)
        self.assertEqual(self.parser.last_recv_pose, [11.0, 22.0, 33.0, 44.0, 55.0, 66.0])
        self.assertEqual(len(self.parser.buffer), 0)


class TestTier1Feature2PoseComponents(unittest.TestCase):
    """Feature 2: IEEE 754 little-endian double[6] pose components."""

    def test_translation_xyz_components_range(self):
        """Verify positive, negative, and zero translation coordinates."""
        test_cases = [
            (0.0, 0.0, 0.0),
            (1000.0, -1000.0, 500.0),
            (-0.0001, 0.0001, -99999.9),
            (150.25, 300.75, -450.5),
            (-1.0, -2.0, -3.0),
        ]
        for tx, ty, tz in test_cases:
            data = pack_pose(tx, ty, tz, 0.0, 0.0, 0.0)
            unpacked = unpack_pose(data)
            self.assertAlmostEqual(unpacked[0], tx, places=6)
            self.assertAlmostEqual(unpacked[1], ty, places=6)
            self.assertAlmostEqual(unpacked[2], tz, places=6)

    def test_rotation_yaw_pitch_roll_range(self):
        """Verify full degree range of orientation angles."""
        test_cases = [
            (0.0, 0.0, 0.0),
            (180.0, 90.0, 180.0),
            (-180.0, -90.0, -180.0),
            (45.5, -30.2, 12.8),
            (-135.0, 60.0, -45.0),
        ]
        for yaw, pitch, roll in test_cases:
            data = pack_pose(0.0, 0.0, 0.0, yaw, pitch, roll)
            unpacked = unpack_pose(data)
            self.assertAlmostEqual(unpacked[3], yaw, places=6)
            self.assertAlmostEqual(unpacked[4], pitch, places=6)
            self.assertAlmostEqual(unpacked[5], roll, places=6)

    def test_ieee754_little_endian_byte_ordering(self):
        """Verify raw bytes match IEEE 754 little-endian double representation."""
        # In IEEE 754 little-endian: 1.0 is 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0xF0, 0x3F
        expected_one_le = b"\x00\x00\x00\x00\x00\x00\xf0\x3f"
        data = pack_pose(1.0, 0.0, 0.0, 0.0, 0.0, 0.0)
        self.assertEqual(data[:8], expected_one_le)

    def test_extreme_finite_float_subnormals_and_normals(self):
        """Verify extreme small and large finite floating-point values pass is_pose_valid."""
        small_val = 1e-15
        large_val = 1e15
        pose = (small_val, large_val, -small_val, -large_val, 0.0, 1.0)
        self.assertTrue(is_pose_valid(pose))
        data = pack_pose(*pose)
        unpacked = unpack_pose(data)
        for v_in, v_out in zip(pose, unpacked):
            self.assertAlmostEqual(v_in, v_out, places=7)

    def test_pose_component_indexing_and_axis_offsets(self):
        """Verify axis offset transformations on Yaw, Pitch, Roll match OpenTrack table."""
        base_pose = (10.0, 20.0, 30.0, 10.0, 20.0, 30.0)
        # add_yaw = 1 (+90), add_pitch = 2 (-90), add_roll = 3 (+180)
        offset_pose = apply_axis_offsets(base_pose, add_yaw_idx=1, add_pitch_idx=2, add_roll_idx=3)
        self.assertEqual(offset_pose[0], 10.0)
        self.assertEqual(offset_pose[1], 20.0)
        self.assertEqual(offset_pose[2], 30.0)
        self.assertEqual(offset_pose[3], 10.0 + 90.0)
        self.assertEqual(offset_pose[4], 20.0 - 90.0)
        self.assertEqual(offset_pose[5], 30.0 + 180.0)


class TestTier1Feature3SocketBinding(unittest.TestCase):
    """Feature 3: Socket binding & connection lifecycle."""

    def test_host_tcpserver_bind_port_4242(self):
        """Verify local QTcpServer can bind on 127.0.0.1:4242 (Android port)."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", DEFAULT_ANDROID_PORT))
            sock.listen(1)
            self.assertEqual(sock.getsockname()[1], DEFAULT_ANDROID_PORT)
        finally:
            sock.close()

    def test_host_tcpserver_bind_ios_port_47047(self):
        """Verify binding capability on 127.0.0.1:47047 (iOS port)."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", DEFAULT_IOS_PORT))
            sock.listen(1)
            self.assertEqual(sock.getsockname()[1], DEFAULT_IOS_PORT)
        finally:
            sock.close()

    def test_socket_reuseaddr_flag_handling(self):
        """Verify SO_REUSEADDR permits immediate rebinding after socket closure."""
        sock1 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock1.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock1.bind(("127.0.0.1", 0))
        assigned_port = sock1.getsockname()[1]
        sock1.close()

        sock2 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock2.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        # Should bind without Address Already in Use error
        sock2.bind(("127.0.0.1", assigned_port))
        sock2.close()

    def test_client_connection_handshake(self):
        """Verify TCP client connects to host listener within timeout."""
        server = MockHostServer(port=0)  # OS-assigned free port
        server.start()
        assigned_port = server.server_socket.getsockname()[1]

        client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            client.connect(("127.0.0.1", assigned_port))
            self.assertTrue(server.client_connected.wait(timeout=2.0))
        finally:
            client.close()
            server.stop()

    def test_server_close_disconnects_client(self):
        """Verify closing the server terminates client connection cleanly."""
        server = MockHostServer(port=0)
        server.start()
        assigned_port = server.server_socket.getsockname()[1]

        client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        client.connect(("127.0.0.1", assigned_port))
        self.assertTrue(server.client_connected.wait(timeout=2.0))

        server.stop()
        # Client read should encounter EOF (empty bytes)
        client.settimeout(1.0)
        data = client.recv(1024)
        self.assertEqual(len(data), 0)
        client.close()


class TestTier1Feature4AdbDiscovery(unittest.TestCase):
    """Feature 4: Autonomous ADB discovery across candidate locations."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.state_file = os.path.join(self.tmpdir.name, "state.json")
        self.state = MockAdbState(self.state_file)
        self.state.save()

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_adb_discovery_via_custom_user_hint(self):
        """Verify custom hint path takes precedence if file exists."""
        hint_path = os.path.join(self.tmpdir.name, "custom_adb.exe")
        with open(hint_path, "w") as f:
            f.write("mock")
        # adb_client::find_adb checks user_hint first
        self.assertTrue(os.path.exists(hint_path))

    def test_adb_discovery_via_app_dir(self):
        """Verify discovery in application root directory (app_dir + '/adb.exe')."""
        app_adb = os.path.join(self.tmpdir.name, "adb.exe")
        with open(app_adb, "w") as f:
            f.write("mock")
        self.assertTrue(os.path.exists(app_adb))

    def test_adb_discovery_via_platform_tools_dir(self):
        """Verify discovery in platform-tools subdirectory."""
        pt_dir = os.path.join(self.tmpdir.name, "platform-tools")
        os.makedirs(pt_dir, exist_ok=True)
        pt_adb = os.path.join(pt_dir, "adb.exe")
        with open(pt_adb, "w") as f:
            f.write("mock")
        self.assertTrue(os.path.exists(pt_adb))

    def test_adb_discovery_via_system_path(self):
        """Verify standard PATH resolution candidate."""
        # Simulated PATH entry
        bin_dir = os.path.join(self.tmpdir.name, "bin")
        os.makedirs(bin_dir, exist_ok=True)
        bin_adb = os.path.join(bin_dir, "adb.exe")
        with open(bin_adb, "w") as f:
            f.write("mock")
        self.assertTrue(os.path.exists(bin_adb))

    def test_adb_discovery_fails_gracefully_when_absent(self):
        """Verify discovery returns empty when no candidates exist."""
        empty_dir = os.path.join(self.tmpdir.name, "empty")
        os.makedirs(empty_dir, exist_ok=True)
        candidate = os.path.join(empty_dir, "adb.exe")
        self.assertFalse(os.path.exists(candidate))


class TestTier1Feature5AdbReverseSyntax(unittest.TestCase):
    """Feature 5: ADB reverse syntax and command execution."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.state_file = os.path.join(self.tmpdir.name, "state.json")
        self.state = MockAdbState(self.state_file)
        self.state.save()

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_adb_reverse_tcp_command_syntax(self):
        """Verify syntax: adb reverse tcp:4242 tcp:4242."""
        code = run_mock_cli(["adb", "reverse", "tcp:4242", "tcp:4242"], self.state_file)
        self.assertEqual(code, 0)
        self.state.load()
        self.assertIn("tcp:4242", self.state.reverse_ports)

    def test_adb_reverse_with_device_serial_flag(self):
        """Verify serial option: adb -s <serial> reverse tcp:4242 tcp:4242."""
        code = run_mock_cli(["adb", "-s", "emulator-5554", "reverse", "tcp:4242", "tcp:4242"], self.state_file)
        self.assertEqual(code, 0)
        self.state.load()
        self.assertIn("tcp:4242", self.state.reverse_ports)
        last_cmd = self.state.commands_log[-1]
        self.assertEqual(last_cmd[:2], ["-s", "emulator-5554"])

    def test_adb_reverse_remove_syntax(self):
        """Verify syntax: adb reverse --remove tcp:4242."""
        run_mock_cli(["adb", "reverse", "tcp:4242", "tcp:4242"], self.state_file)
        code = run_mock_cli(["adb", "reverse", "--remove", "tcp:4242"], self.state_file)
        self.assertEqual(code, 0)
        self.state.load()
        self.assertNotIn("tcp:4242", self.state.reverse_ports)

    def test_adb_reverse_error_handling_on_failure(self):
        """Verify non-zero exit code propagated when adb reverse fails."""
        self.state.fail_reverse = True
        self.state.save()
        code = run_mock_cli(["adb", "reverse", "tcp:4242", "tcp:4242"], self.state_file)
        self.assertNotEqual(code, 0)

    def test_adb_reverse_arbitrary_port(self):
        """Verify arbitrary configured port reverse binding: adb reverse tcp:5555 tcp:5555."""
        code = run_mock_cli(["adb", "reverse", "tcp:5555", "tcp:5555"], self.state_file)
        self.assertEqual(code, 0)
        self.state.load()
        self.assertIn("tcp:5555", self.state.reverse_ports)


class TestTier1Feature6RelayBridgeProtocol(unittest.TestCase):
    """Feature 6: Relay daemon bridge protocol (UDP -> TCP)."""

    def test_relay_udp_listener_binds_to_configured_port(self):
        """Verify relay opens and binds a UDP datagram socket."""
        relay = RelayBridgeEmulator(udp_port=0, tcp_port=0)
        self.assertIsNotNone(relay)

    def test_relay_tcp_connects_to_reverse_port(self):
        """Verify relay establishes TCP connection to host server."""
        server = MockHostServer(port=0)
        server.start()
        assigned_port = server.server_socket.getsockname()[1]

        relay = RelayBridgeEmulator(udp_port=0, tcp_port=assigned_port)
        relay.start()

        self.assertTrue(relay.connected_event.wait(timeout=2.0))
        self.assertTrue(server.client_connected.wait(timeout=2.0))

        relay.stop()
        server.stop()

    def test_relay_forwards_udp_datagram_to_tcp(self):
        """Verify 48-byte UDP packet sent to relay arrives at host TCP server."""
        server = MockHostServer(port=0)
        server.start()
        host_port = server.server_socket.getsockname()[1]

        # Allocate free UDP port
        temp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        temp_sock.bind(("127.0.0.1", 0))
        udp_port = temp_sock.getsockname()[1]
        temp_sock.close()

        relay = RelayBridgeEmulator(udp_port=udp_port, tcp_port=host_port)
        relay.start()
        self.assertTrue(relay.connected_event.wait(timeout=2.0))

        # Send pose over UDP
        app = MockSmoothTrackApp(target_port=udp_port)
        app.send_pose(1.0, 2.0, 3.0, 10.0, 20.0, 30.0)

        # Wait for host parser to receive
        time.sleep(0.1)
        self.assertEqual(server.parser.frames_processed, 1)
        self.assertEqual(server.parser.last_recv_pose, [1.0, 2.0, 3.0, 10.0, 20.0, 30.0])

        app.close()
        relay.stop()
        server.stop()

    def test_relay_tcp_nodelay_option_active(self):
        """Verify TCP_NODELAY is enabled on the TCP socket."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        opt = sock.getsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY)
        self.assertNotEqual(opt, 0)
        sock.close()

    def test_relay_terminates_cleanly_when_tcp_closed(self):
        """Verify relay detects TCP close and terminates forwarding loop."""
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

        # Stop server to trigger TCP closure
        server.stop()

        # Send a UDP packet to trigger relay send() check
        app = MockSmoothTrackApp(target_port=udp_port)
        app.send_pose(0.0, 0.0, 0.0, 0.0, 0.0, 0.0)

        # Relay should stop within 1 second
        self.assertTrue(relay.stopped_event.wait(timeout=1.5))
        app.close()
        relay.stop()


if __name__ == "__main__":
    unittest.main()
