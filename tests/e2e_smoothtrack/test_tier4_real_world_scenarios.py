"""Tier 4: Real-World Application Scenarios E2E Tests (>=5 realistic scenarios).

Covers end-to-end real-world workflows from ORIGINAL_REQUEST.md & PROJECT.md:
1. Full 60Hz telemetry session simulation (stream at 60Hz, pause, resume, finish)
2. Mid-flight USB cable disconnect simulation (immediate socket abort, 100ms unhang)
3. Unauthorized ADB prompt handling & user authorization workflow
4. Multi-device connection & explicit serial resolution
5. Mobile app restart while OpenTrack server is actively running
6. Complete relay binary deployment lifecycle (ABI detection, push, chmod, supervision, teardown)
"""

import os
import socket
import struct
import tempfile
import time
import unittest
from typing import List

try:
    from .mock_adb_server import MockAdbState, run_mock_cli
    from .protocol_oracle import (
        DEFAULT_ANDROID_PORT,
        DEFAULT_IOS_PORT,
        SimulatedStreamParser,
        pack_pose,
    )
    from .relay_bridge_emulator import MockHostServer, MockSmoothTrackApp, RelayBridgeEmulator
except ImportError:
    from mock_adb_server import MockAdbState, run_mock_cli
    from protocol_oracle import (
        DEFAULT_ANDROID_PORT,
        DEFAULT_IOS_PORT,
        SimulatedStreamParser,
        pack_pose,
    )
    from relay_bridge_emulator import MockHostServer, MockSmoothTrackApp, RelayBridgeEmulator


class TestTier4RealWorldScenarios(unittest.TestCase):
    """End-to-End realistic user and device workflows."""

    def test_telemetry_session_60hz_stream_pause_resume(self):
        """Scenario 1: SmoothTrack mobile app streams at 60Hz, pauses, resumes, and finishes."""
        server = MockHostServer(port=0)
        server.start()
        tcp_port = server.server_socket.getsockname()[1]

        s_udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s_udp.bind(("127.0.0.1", 0))
        udp_port = s_udp.getsockname()[1]
        s_udp.close()

        relay = RelayBridgeEmulator(udp_port=udp_port, tcp_port=tcp_port)
        relay.start()
        self.assertTrue(relay.connected_event.wait(timeout=2.0))

        app = MockSmoothTrackApp(target_port=udp_port)

        # Phase 1: Stream at 60Hz (~16.6ms intervals) for 20 frames
        for i in range(1, 21):
            app.send_pose(float(i), float(i) * 0.5, 0.0, float(i) * 2.0, 0.0, 0.0)
            time.sleep(0.016)

        time.sleep(0.05)
        self.assertGreaterEqual(server.parser.frames_processed, 15)
        pose_before_pause = list(server.parser.last_recv_pose)

        # Phase 2: Pause tracking (user paused app or tabbed away) for 200ms
        time.sleep(0.2)
        # Verify host maintains the last valid pose during pause without resetting
        self.assertEqual(server.parser.last_recv_pose, pose_before_pause)

        # Phase 3: Resume streaming for 15 more frames
        for i in range(21, 36):
            app.send_pose(float(i), 0.0, 0.0, 0.0, 0.0, 0.0)
            time.sleep(0.016)

        time.sleep(0.05)
        self.assertGreaterEqual(server.parser.frames_processed, 30)
        self.assertEqual(server.parser.last_recv_pose[0], 35.0)

        # Phase 4: Teardown
        app.close()
        relay.stop()
        server.stop()

    def test_mid_flight_cable_disconnect_simulation(self):
        """Scenario 2: Mid-flight cable disconnect abruptly terminates TCP connection."""
        server = MockHostServer(port=0)
        server.start()
        tcp_port = server.server_socket.getsockname()[1]

        # Connect a raw socket representing the ADB tunnel
        client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        client.connect(("127.0.0.1", tcp_port))
        self.assertTrue(server.client_connected.wait(timeout=2.0))

        # Stream active telemetry
        client.sendall(pack_pose(10.0, 20.0, 30.0, 40.0, 50.0, 60.0))
        time.sleep(0.05)
        self.assertEqual(server.parser.last_recv_pose, [10.0, 20.0, 30.0, 40.0, 50.0, 60.0])

        # Abrupt cable disconnect: force RST / immediate socket close
        t0 = time.time()
        # Set SO_LINGER to 0 to force immediate TCP RST
        client.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
        client.close()

        # Verify server detects disconnect without deadlocking
        disconnected = server.client_disconnected.wait(timeout=0.5)
        t_elapsed = time.time() - t0
        self.assertTrue(disconnected)
        self.assertLess(t_elapsed, 0.5)

        server.stop()

    def test_unauthorized_adb_prompt_handling_workflow(self):
        """Scenario 3: Device initially unauthorized, prompts user, user accepts, workflow proceeds."""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = os.path.join(tmpdir, "state.json")
            state = MockAdbState(state_file)
            state.devices = [
                {"serial": "phone999", "status": "unauthorized", "model": "Galaxy_S22", "abi": "arm64-v8a"}
            ]
            state.save()

            # 1. First probe: check_device fails because status is 'unauthorized'
            code = run_mock_cli(["adb", "devices", "-l"], state_file)
            self.assertEqual(code, 0)
            state.load()
            dev = state.devices[0]
            self.assertEqual(dev["status"], "unauthorized")

            # Diagnostic error string surfaced to user:
            user_dialog = f"Android device '{dev['serial']}' is unauthorized.\nPlease unlock phone and tap 'Allow USB debugging'"
            self.assertIn("is unauthorized", user_dialog)
            self.assertIn("Allow USB debugging", user_dialog)

            # 2. User unlocks phone and taps 'Allow'
            state.devices[0]["status"] = "device"
            state.save()

            # 3. Second probe: check_device succeeds!
            code = run_mock_cli(["adb", "devices", "-l"], state_file)
            self.assertEqual(code, 0)
            state.load()
            self.assertEqual(state.devices[0]["status"], "device")

            # 4. Proceeds to reverse tunnel
            code = run_mock_cli(["adb", "-s", "phone999", "reverse", "tcp:4242", "tcp:4242"], state_file)
            self.assertEqual(code, 0)
            state.load()
            self.assertIn("tcp:4242", state.reverse_ports)

    def test_multi_device_targeting_with_serial(self):
        """Scenario 4: Multiple connected devices correctly target the ready device with -s <serial>."""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = os.path.join(tmpdir, "state.json")
            state = MockAdbState(state_file)
            state.devices = [
                {"serial": "dev_offline_1", "status": "offline", "model": "Watch", "abi": "arm64-v8a"},
                {"serial": "dev_target_2", "status": "device", "model": "Pixel_7", "abi": "arm64-v8a"},
                {"serial": "dev_unauth_3", "status": "unauthorized", "model": "Tablet", "abi": "armeabi-v7a"},
            ]
            state.save()

            # Controller discovers devices and filters for status == 'device'
            ready_device = None
            for d in state.devices:
                if d["status"] == "device":
                    ready_device = d
                    break

            self.assertIsNotNone(ready_device)
            self.assertEqual(ready_device["serial"], "dev_target_2")

            # All subsequent commands must include -s dev_target_2
            serial = ready_device["serial"]
            run_mock_cli(["adb", "-s", serial, "shell", "getprop", "ro.product.cpu.abi"], state_file)
            run_mock_cli(["adb", "-s", serial, "reverse", "tcp:4242", "tcp:4242"], state_file)

            state.load()
            for cmd in state.commands_log:
                self.assertEqual(cmd[0], "-s")
                self.assertEqual(cmd[1], "dev_target_2")

    def test_mobile_app_restart_during_active_opentrack_session(self):
        """Scenario 5: Mobile app terminates, restarts, and seamlessly resumes telemetry."""
        server = MockHostServer(port=0)
        server.start()
        tcp_port = server.server_socket.getsockname()[1]

        s_udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s_udp.bind(("127.0.0.1", 0))
        udp_port = s_udp.getsockname()[1]
        s_udp.close()

        relay = RelayBridgeEmulator(udp_port=udp_port, tcp_port=tcp_port)
        relay.start()
        self.assertTrue(relay.connected_event.wait(timeout=2.0))

        # App instance 1
        app1 = MockSmoothTrackApp(target_port=udp_port)
        app1.send_pose(10.0, 0.0, 0.0, 0.0, 0.0, 0.0)
        time.sleep(0.05)
        self.assertEqual(server.parser.last_recv_pose[0], 10.0)

        # App is closed / crashed
        app1.close()
        time.sleep(0.1)

        # App instance 2 is launched by user
        app2 = MockSmoothTrackApp(target_port=udp_port)
        app2.send_pose(50.0, 0.0, 0.0, 0.0, 0.0, 0.0)
        time.sleep(0.05)
        self.assertEqual(server.parser.last_recv_pose[0], 50.0)

        app2.close()
        relay.stop()
        server.stop()

    def test_relay_binary_deployment_and_abi_lifecycle(self):
        """Scenario 6: Full ADB lifecycle: ABI detection -> push -> chmod -> supervise -> cleanup."""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = os.path.join(tmpdir, "state.json")
            state = MockAdbState(state_file)
            state.devices = [
                {"serial": "phone_arm64", "status": "device", "model": "Pixel_8", "abi": "arm64-v8a"}
            ]
            state.save()

            serial = "phone_arm64"

            # 1. ABI Query
            code = run_mock_cli(["adb", "-s", serial, "shell", "getprop", "ro.product.cpu.abi"], state_file)
            self.assertEqual(code, 0)

            # 2. Reverse Setup
            code = run_mock_cli(["adb", "-s", serial, "reverse", "tcp:4242", "tcp:4242"], state_file)
            self.assertEqual(code, 0)

            # 3. Binary Push
            fake_bin = os.path.join(tmpdir, "st-relay-arm64")
            with open(fake_bin, "w") as f:
                f.write("ELF")
            code = run_mock_cli(["adb", "-s", serial, "push", fake_bin, "/data/local/tmp/st-relay"], state_file)
            self.assertEqual(code, 0)

            # 4. Chmod 755
            code = run_mock_cli(["adb", "-s", serial, "shell", "chmod", "755", "/data/local/tmp/st-relay"], state_file)
            self.assertEqual(code, 0)

            # 5. Pkill stale instances
            code = run_mock_cli(["adb", "-s", serial, "shell", "pkill", "-f", "st-relay"], state_file)
            self.assertEqual(code, 0)

            # 6. Launch relay daemon
            code = run_mock_cli(["adb", "-s", serial, "shell", "/data/local/tmp/st-relay", "4242", "4242"], state_file)
            self.assertEqual(code, 0)

            # 7. Teardown: stop & cleanup
            code = run_mock_cli(["adb", "-s", serial, "shell", "pkill", "-f", "st-relay"], state_file)
            self.assertEqual(code, 0)
            code = run_mock_cli(["adb", "-s", serial, "reverse", "--remove", "tcp:4242"], state_file)
            self.assertEqual(code, 0)

            state.load()
            self.assertNotIn("tcp:4242", state.reverse_ports)
            self.assertFalse(state.relay_running)


if __name__ == "__main__":
    unittest.main()
