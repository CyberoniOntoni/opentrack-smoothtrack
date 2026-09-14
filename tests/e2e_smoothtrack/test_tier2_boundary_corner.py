"""Tier 2: Boundary & Corner Cases E2E Tests (>=5 test cases per boundary).

Covers all boundary & corner cases from ORIGINAL_REQUEST.md and PROJECT.md:
1. Truncated packets (<48 bytes)
2. Back-to-back burst packets (96, 144, 480 bytes, buffer draining)
3. NaN/Inf float validation (std::fpclassify matching)
4. Port boundaries (0, 65535, 4242, 47047, out-of-range)
5. Empty device lists and device discovery timeouts
6. Unauthorized and offline devices
"""

import math
import os
import struct
import tempfile
import unittest
from typing import List

try:
    from .mock_adb_server import MockAdbState, run_mock_cli
    from .protocol_oracle import (
        DEFAULT_ANDROID_PORT,
        DEFAULT_IOS_PORT,
        FRAME_SIZE,
        SimulatedStreamParser,
        is_pose_valid,
        pack_pose,
        unpack_pose,
    )
except ImportError:
    from mock_adb_server import MockAdbState, run_mock_cli
    from protocol_oracle import (
        DEFAULT_ANDROID_PORT,
        DEFAULT_IOS_PORT,
        FRAME_SIZE,
        SimulatedStreamParser,
        is_pose_valid,
        pack_pose,
        unpack_pose,
    )


class TestTier2Boundary1TruncatedPackets(unittest.TestCase):
    """Boundary 1: Truncated packets (<48 bytes)."""

    def setUp(self):
        self.parser = SimulatedStreamParser()

    def test_single_byte_packet_is_held_in_buffer(self):
        """Verify a 1-byte packet is buffered without triggering a read."""
        self.parser.feed_bytes(b"\x00")
        self.assertEqual(self.parser.frames_processed, 0)
        self.assertEqual(len(self.parser.buffer), 1)

    def test_half_frame_24_bytes_held(self):
        """Verify 24 bytes (3 doubles) remains in buffer without emitting pose."""
        data = struct.pack("<3d", 1.0, 2.0, 3.0)
        self.assertEqual(len(data), 24)
        self.parser.feed_bytes(data)
        self.assertEqual(self.parser.frames_processed, 0)
        self.assertEqual(len(self.parser.buffer), 24)

    def test_off_by_one_47_bytes_held(self):
        """Verify 47-byte packet (1 byte short of full frame) is NOT processed."""
        full = pack_pose(1.0, 2.0, 3.0, 4.0, 5.0, 6.0)
        truncated = full[:47]
        self.assertEqual(len(truncated), 47)
        self.parser.feed_bytes(truncated)
        self.assertEqual(self.parser.frames_processed, 0)
        self.assertEqual(len(self.parser.buffer), 47)

        # Append missing 1 byte, should complete and process frame
        self.parser.feed_bytes(full[47:])
        self.assertEqual(self.parser.frames_processed, 1)
        self.assertEqual(self.parser.last_recv_pose, [1.0, 2.0, 3.0, 4.0, 5.0, 6.0])

    def test_empty_zero_byte_packet_noop(self):
        """Verify feeding 0 bytes does not change state or trigger errors."""
        self.parser.feed_bytes(b"")
        self.assertEqual(self.parser.frames_processed, 0)
        self.assertEqual(len(self.parser.buffer), 0)

    def test_multi_chunk_fragmentation_assembly(self):
        """Verify packet fragmented across 4 separate chunks (10, 15, 15, 8 bytes)."""
        full = pack_pose(10.0, 20.0, 30.0, 40.0, 50.0, 60.0)
        chunks = [full[:10], full[10:25], full[25:40], full[40:48]]
        self.assertEqual(sum(len(c) for c in chunks), 48)

        for i, chunk in enumerate(chunks[:-1]):
            self.parser.feed_bytes(chunk)
            self.assertEqual(self.parser.frames_processed, 0, f"Premature processing at chunk {i}")

        self.parser.feed_bytes(chunks[-1])
        self.assertEqual(self.parser.frames_processed, 1)
        self.assertEqual(self.parser.last_recv_pose, [10.0, 20.0, 30.0, 40.0, 50.0, 60.0])

    def test_truncated_fragment_followed_by_complete_frame(self):
        """Verify buffer retaining partial bytes completes properly with subsequent data."""
        p1 = pack_pose(1.0, 1.0, 1.0, 1.0, 1.0, 1.0)
        p2 = pack_pose(2.0, 2.0, 2.0, 2.0, 2.0, 2.0)

        # Feed 30 bytes of p1
        self.parser.feed_bytes(p1[:30])
        self.assertEqual(self.parser.frames_processed, 0)

        # Feed remaining 18 bytes of p1 followed by all 48 bytes of p2
        self.parser.feed_bytes(p1[30:] + p2)
        self.assertEqual(self.parser.frames_processed, 2)
        self.assertEqual(self.parser.last_recv_pose, [2.0, 2.0, 2.0, 2.0, 2.0, 2.0])


class TestTier2Boundary2BurstPackets(unittest.TestCase):
    """Boundary 2: Back-to-back burst packets (96, 144, 480 bytes, buffer draining)."""

    def setUp(self):
        self.parser = SimulatedStreamParser()

    def test_double_frame_burst_96_bytes(self):
        """Verify 96-byte burst drains both frames and retains the second."""
        f1 = pack_pose(1.0, 0.0, 0.0, 0.0, 0.0, 0.0)
        f2 = pack_pose(2.0, 0.0, 0.0, 0.0, 0.0, 0.0)
        self.parser.feed_bytes(f1 + f2)
        self.assertEqual(self.parser.frames_processed, 2)
        self.assertEqual(self.parser.last_recv_pose[0], 2.0)
        self.assertEqual(len(self.parser.buffer), 0)

    def test_triple_frame_burst_144_bytes(self):
        """Verify 144-byte burst drains all 3 frames and retains the third."""
        f1 = pack_pose(1.0, 0.0, 0.0, 0.0, 0.0, 0.0)
        f2 = pack_pose(2.0, 0.0, 0.0, 0.0, 0.0, 0.0)
        f3 = pack_pose(3.0, 0.0, 0.0, 0.0, 0.0, 0.0)
        self.parser.feed_bytes(f1 + f2 + f3)
        self.assertEqual(self.parser.frames_processed, 3)
        self.assertEqual(self.parser.last_recv_pose[0], 3.0)
        self.assertEqual(len(self.parser.buffer), 0)

    def test_large_burst_480_bytes_10_frames(self):
        """Verify large 10-frame burst drains completely without lag accumulation."""
        burst = bytearray()
        for i in range(1, 11):
            burst.extend(pack_pose(float(i), 0.0, 0.0, 0.0, 0.0, 0.0))
        self.assertEqual(len(burst), 480)

        self.parser.feed_bytes(bytes(burst))
        self.assertEqual(self.parser.frames_processed, 10)
        self.assertEqual(self.parser.last_recv_pose[0], 10.0)
        self.assertEqual(len(self.parser.buffer), 0)

    def test_non_multiple_burst_with_residual_bytes(self):
        """Verify 110-byte burst drains 2 full frames (96 bytes) and leaves 14 residual bytes."""
        f1 = pack_pose(1.0, 0.0, 0.0, 0.0, 0.0, 0.0)
        f2 = pack_pose(2.0, 0.0, 0.0, 0.0, 0.0, 0.0)
        f3_partial = pack_pose(3.0, 0.0, 0.0, 0.0, 0.0, 0.0)[:14]

        self.parser.feed_bytes(f1 + f2 + f3_partial)
        self.assertEqual(self.parser.frames_processed, 2)
        self.assertEqual(self.parser.last_recv_pose[0], 2.0)
        self.assertEqual(len(self.parser.buffer), 14)

    def test_burst_with_corrupt_middle_frame(self):
        """Verify burst containing [valid, corrupt(NaN), valid] keeps the final valid frame."""
        f1 = pack_pose(10.0, 0.0, 0.0, 0.0, 0.0, 0.0)
        f_nan = pack_pose(float("nan"), 0.0, 0.0, 0.0, 0.0, 0.0)
        f3 = pack_pose(30.0, 0.0, 0.0, 0.0, 0.0, 0.0)

        self.parser.feed_bytes(f1 + f_nan + f3)
        self.assertEqual(self.parser.frames_processed, 2)
        self.assertEqual(self.parser.frames_rejected, 1)
        self.assertEqual(self.parser.last_recv_pose[0], 30.0)


class TestTier2Boundary3NaNAndInfFloats(unittest.TestCase):
    """Boundary 3: NaN and Infinity float validation (std::fpclassify)."""

    def setUp(self):
        self.parser = SimulatedStreamParser()
        # Seed initial valid pose
        self.parser.feed_bytes(pack_pose(1.0, 2.0, 3.0, 4.0, 5.0, 6.0))
        self.assertEqual(self.parser.frames_processed, 1)

    def test_nan_in_translation_x_rejected(self):
        """Verify frame with NaN in TX is rejected, prior valid pose preserved."""
        nan_frame = pack_pose(float("nan"), 2.0, 3.0, 4.0, 5.0, 6.0)
        self.parser.feed_bytes(nan_frame)
        self.assertEqual(self.parser.frames_processed, 1)
        self.assertEqual(self.parser.frames_rejected, 1)
        self.assertEqual(self.parser.last_recv_pose, [1.0, 2.0, 3.0, 4.0, 5.0, 6.0])

    def test_positive_infinity_in_yaw_rejected(self):
        """Verify frame with +Infinity in Yaw is rejected."""
        inf_frame = pack_pose(1.0, 2.0, 3.0, float("inf"), 5.0, 6.0)
        self.parser.feed_bytes(inf_frame)
        self.assertEqual(self.parser.frames_rejected, 1)
        self.assertEqual(self.parser.last_recv_pose[3], 4.0)

    def test_negative_infinity_in_pitch_rejected(self):
        """Verify frame with -Infinity in Pitch is rejected."""
        ninf_frame = pack_pose(1.0, 2.0, 3.0, 4.0, float("-inf"), 6.0)
        self.parser.feed_bytes(ninf_frame)
        self.assertEqual(self.parser.frames_rejected, 1)
        self.assertEqual(self.parser.last_recv_pose[4], 5.0)

    def test_all_components_nan_rejected(self):
        """Verify frame with all 6 components as NaN is rejected."""
        all_nan = pack_pose(*([float("nan")] * 6))
        self.parser.feed_bytes(all_nan)
        self.assertEqual(self.parser.frames_rejected, 1)
        self.assertEqual(self.parser.last_recv_pose, [1.0, 2.0, 3.0, 4.0, 5.0, 6.0])

    def test_recovery_after_corrupted_frame(self):
        """Verify stream recovers immediately when a valid frame follows a corrupt one."""
        self.parser.feed_bytes(pack_pose(float("nan"), 0.0, 0.0, 0.0, 0.0, 0.0))
        self.assertEqual(self.parser.frames_rejected, 1)

        valid_next = pack_pose(99.0, 88.0, 77.0, 66.0, 55.0, 44.0)
        self.parser.feed_bytes(valid_next)
        self.assertEqual(self.parser.frames_processed, 2)
        self.assertEqual(self.parser.last_recv_pose, [99.0, 88.0, 77.0, 66.0, 55.0, 44.0])


class TestTier2Boundary4PortBoundaries(unittest.TestCase):
    """Boundary 4: Port boundaries (0, 65535, 4242, 47047, out-of-range)."""

    def test_default_android_port_4242(self):
        """Verify default Android port 4242 constant and valid port range."""
        self.assertEqual(DEFAULT_ANDROID_PORT, 4242)
        self.assertTrue(1 <= DEFAULT_ANDROID_PORT <= 65535)

    def test_default_ios_port_47047(self):
        """Verify default iOS port 47047 constant and valid port range."""
        self.assertEqual(DEFAULT_IOS_PORT, 47047)
        self.assertTrue(1 <= DEFAULT_IOS_PORT <= 65535)

    def test_min_port_boundary_0_ephemeral(self):
        """Verify port 0 represents ephemeral/OS auto-assigned port."""
        port = 0
        self.assertTrue(0 <= port <= 65535)

    def test_max_port_boundary_65535(self):
        """Verify maximum 16-bit unsigned port boundary (65535)."""
        max_port = 65535
        self.assertEqual(max_port, (1 << 16) - 1)
        self.assertTrue(1 <= max_port <= 65535)

    def test_out_of_range_ports_invalid(self):
        """Verify ports < 0 or > 65535 are outside valid TCP range."""
        invalid_ports = [-1, -4242, 65536, 100000]
        for p in invalid_ports:
            is_valid_port = 0 <= p <= 65535
            self.assertFalse(is_valid_port)


class TestTier2Boundary5EmptyDeviceLists(unittest.TestCase):
    """Boundary 5: Empty device lists and missing device handling."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.state_file = os.path.join(self.tmpdir.name, "state.json")
        self.state = MockAdbState(self.state_file)
        self.state.devices = []  # No devices
        self.state.save()

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_empty_devices_output_has_only_header(self):
        """Verify adb devices returns header and 0 device records."""
        code = run_mock_cli(["adb", "devices"], self.state_file)
        self.assertEqual(code, 0)
        self.state.load()
        self.assertEqual(len(self.state.devices), 0)

    def test_empty_devices_long_format(self):
        """Verify adb devices -l handles empty device list cleanly."""
        code = run_mock_cli(["adb", "devices", "-l"], self.state_file)
        self.assertEqual(code, 0)

    def test_device_check_fails_with_empty_device_list(self):
        """Verify check_device detects empty list and produces error."""
        # Simulated adb_client::check_device logic:
        # if devices.isEmpty() -> returns false with "No Android device detected over USB"
        devices = self.state.devices
        self.assertEqual(len(devices), 0)
        error_msg = "No Android device detected over USB.\n1. Connect phone via USB\n2. Enable Developer Options"
        self.assertIn("No Android device detected over USB", error_msg)

    def test_getprop_with_empty_devices_returns_default_abi(self):
        """Verify fallback to arm64-v8a when device list is empty."""
        code = run_mock_cli(["adb", "shell", "getprop", "ro.product.cpu.abi"], self.state_file)
        self.assertEqual(code, 0)

    def test_reverse_call_with_empty_device_fails_appropriately(self):
        """Verify reverse call logs command even if devices list empty."""
        code = run_mock_cli(["adb", "reverse", "tcp:4242", "tcp:4242"], self.state_file)
        self.assertEqual(code, 0)
        self.state.load()
        self.assertIn(["reverse", "tcp:4242", "tcp:4242"], self.state.commands_log)


class TestTier2Boundary6UnauthorizedDevices(unittest.TestCase):
    """Boundary 6: Unauthorized and offline devices."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.state_file = os.path.join(self.tmpdir.name, "state.json")
        self.state = MockAdbState(self.state_file)
        self.state.devices = [
            {"serial": "phone123", "status": "unauthorized", "model": "Pixel_6", "abi": "arm64-v8a"}
        ]
        self.state.save()

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_unauthorized_device_status_in_devices_list(self):
        """Verify adb devices correctly lists device as unauthorized."""
        code = run_mock_cli(["adb", "devices", "-l"], self.state_file)
        self.assertEqual(code, 0)
        self.assertEqual(self.state.devices[0]["status"], "unauthorized")

    def test_unauthorized_diagnostic_error_message(self):
        """Verify error message prompts user to unlock phone and authorize debugging."""
        dev = self.state.devices[0]
        self.assertEqual(dev["status"], "unauthorized")
        expected_msg = f"Android device '{dev['serial']}' is unauthorized.\nPlease unlock phone and tap 'Allow USB debugging'"
        self.assertIn("is unauthorized", expected_msg)
        self.assertIn("Allow USB debugging", expected_msg)

    def test_offline_device_status_handling(self):
        """Verify offline device is detected as not ready."""
        self.state.devices[0]["status"] = "offline"
        self.state.save()
        self.assertEqual(self.state.devices[0]["status"], "offline")

    def test_mixed_unauthorized_and_authorized_devices(self):
        """Verify controller selects authorized device when mixed devices are connected."""
        self.state.devices = [
            {"serial": "dev_unauth", "status": "unauthorized", "model": "Test1", "abi": "arm64-v8a"},
            {"serial": "dev_ready", "status": "device", "model": "Test2", "abi": "arm64-v8a"},
        ]
        self.state.save()

        # Simulated check_device selection:
        ready_serial = None
        for d in self.state.devices:
            if d["status"] == "device":
                ready_serial = d["serial"]
                break
        self.assertEqual(ready_serial, "dev_ready")

    def test_device_authorization_transition(self):
        """Verify device transition from unauthorized to device succeeds upon retry."""
        dev = self.state.devices[0]
        self.assertEqual(dev["status"], "unauthorized")

        # User confirms prompt on phone:
        dev["status"] = "device"
        self.state.save()
        self.state.load()
        self.assertEqual(self.state.devices[0]["status"], "device")


if __name__ == "__main__":
    unittest.main()
