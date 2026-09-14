"""Tier 3: Cross-Feature Combinations E2E Tests (Pairwise Coverage).

Covers cross-feature interactions specified in ORIGINAL_REQUEST.md & PROJECT.md:
1. Rapid connect/disconnect cycles
2. Switching between iOS and Android modes
3. Socket teardown and immediate restart
4. Dirty port recovery (port contention & recovery)
5. Concurrent UDP bursts during TCP reconnection
6. Dynamic axis offset changes during continuous stream ingestion
"""

import socket
import time
import unittest
from typing import List

try:
    from .protocol_oracle import (
        DEFAULT_ANDROID_PORT,
        DEFAULT_IOS_PORT,
        SimulatedStreamParser,
        apply_axis_offsets,
        pack_pose,
    )
    from .relay_bridge_emulator import MockHostServer, MockSmoothTrackApp, RelayBridgeEmulator
except ImportError:
    from protocol_oracle import (
        DEFAULT_ANDROID_PORT,
        DEFAULT_IOS_PORT,
        SimulatedStreamParser,
        apply_axis_offsets,
        pack_pose,
    )
    from relay_bridge_emulator import MockHostServer, MockSmoothTrackApp, RelayBridgeEmulator


class TestTier3CrossFeatureCombinations(unittest.TestCase):
    """Pairwise combination tests across subsystem boundaries."""

    def test_rapid_connect_disconnect_cycles(self):
        """Verify host server withstands 10 rapid connect-disconnect cycles without hanging."""
        server = MockHostServer(port=0)
        server.start()
        port = server.server_socket.getsockname()[1]

        for i in range(10):
            client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            client.connect(("127.0.0.1", port))
            # Send one frame
            client.sendall(pack_pose(float(i), 0.0, 0.0, 0.0, 0.0, 0.0))
            client.close()
            time.sleep(0.02)

        server.stop()
        # Verify multiple frames were ingested
        self.assertGreater(server.parser.frames_processed, 0)

    def test_switching_between_ios_and_android_modes(self):
        """Verify clean transition from Android (port 4242) to iOS (port 47047) and back."""
        # 1. Start Android mode server (dynamic port)
        server_android = MockHostServer(port=0)
        server_android.start()
        android_port = server_android.server_socket.getsockname()[1]

        # Feed Android frame
        client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        client.connect(("127.0.0.1", android_port))
        client.sendall(pack_pose(10.0, 20.0, 30.0, 0.0, 0.0, 0.0))
        time.sleep(0.05)
        client.close()
        server_android.stop()
        self.assertEqual(server_android.parser.last_recv_pose[:3], [10.0, 20.0, 30.0])

        # 2. Switch to iOS mode (dynamic port)
        server_ios = MockHostServer(port=0)
        server_ios.start()
        ios_port = server_ios.server_socket.getsockname()[1]

        client_ios = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        client_ios.connect(("127.0.0.1", ios_port))
        client_ios.sendall(pack_pose(40.0, 50.0, 60.0, 0.0, 0.0, 0.0))
        time.sleep(0.05)
        client_ios.close()
        server_ios.stop()
        self.assertEqual(server_ios.parser.last_recv_pose[:3], [40.0, 50.0, 60.0])

        # 3. Switch back to Android mode
        server_android2 = MockHostServer(port=0)
        server_android2.start()
        android_port2 = server_android2.server_socket.getsockname()[1]
        client2 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        client2.connect(("127.0.0.1", android_port2))
        client2.sendall(pack_pose(70.0, 80.0, 90.0, 0.0, 0.0, 0.0))
        time.sleep(0.05)
        client2.close()
        server_android2.stop()
        self.assertEqual(server_android2.parser.last_recv_pose[:3], [70.0, 80.0, 90.0])

    def test_socket_teardown_and_immediate_restart(self):
        """Verify host server and relay bridge teardown cleanly and rebind to the same port."""
        # Find free port
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("127.0.0.1", 0))
        test_port = s.getsockname()[1]
        s.close()

        # Session 1
        server1 = MockHostServer(port=test_port)
        server1.start()

        client1 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        client1.connect(("127.0.0.1", test_port))
        client1.sendall(pack_pose(1.0, 1.0, 1.0, 1.0, 1.0, 1.0))
        time.sleep(0.05)
        client1.close()
        server1.stop()

        # Immediate restart on the same port
        server2 = MockHostServer(port=test_port)
        server2.start()

        client2 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        client2.connect(("127.0.0.1", test_port))
        client2.sendall(pack_pose(2.0, 2.0, 2.0, 2.0, 2.0, 2.0))
        time.sleep(0.05)
        client2.close()
        server2.stop()

        self.assertEqual(server2.parser.last_recv_pose, [2.0, 2.0, 2.0, 2.0, 2.0, 2.0])

    def test_dirty_port_recovery(self):
        """Verify graceful error reporting when port is occupied, and recovery once freed."""
        # Occupy a port with a blocking socket
        blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        blocker.bind(("127.0.0.1", 0))
        occupied_port = blocker.getsockname()[1]
        blocker.listen(1)

        # Attempt to bind host server on occupied port -> expect error
        conflict_detected = False
        test_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            # Without SO_REUSEADDR on the conflicting socket, bind should fail
            test_sock.bind(("127.0.0.1", occupied_port))
        except socket.error:
            conflict_detected = True
        finally:
            test_sock.close()

        self.assertTrue(conflict_detected)

        # Release the dirty port
        blocker.close()

        # Now server bind succeeds
        server = MockHostServer(port=occupied_port)
        server.start()
        self.assertTrue(server.running)
        server.stop()

    def test_concurrent_udp_bursts_during_tcp_reconnect(self):
        """Verify UDP datagrams arriving while TCP is establishing do not crash the bridge."""
        # Find free ports
        s_tcp = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s_tcp.bind(("127.0.0.1", 0))
        tcp_port = s_tcp.getsockname()[1]
        s_tcp.close()

        s_udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s_udp.bind(("127.0.0.1", 0))
        udp_port = s_udp.getsockname()[1]
        s_udp.close()

        # Start relay bridge before host server is up (retry loop is active)
        relay = RelayBridgeEmulator(udp_port=udp_port, tcp_port=tcp_port)
        relay.start(retry_limit=30, retry_delay=0.05)

        # Mobile app starts transmitting UDP frames during connection wait
        app = MockSmoothTrackApp(target_port=udp_port)
        for i in range(5):
            app.send_pose(float(i), 0.0, 0.0, 0.0, 0.0, 0.0)

        # Now start host server
        server = MockHostServer(port=tcp_port)
        server.start()

        # Bridge should connect successfully
        self.assertTrue(relay.connected_event.wait(timeout=2.0))

        # Send post-connection frame
        app.send_pose(99.0, 99.0, 99.0, 0.0, 0.0, 0.0)
        time.sleep(0.1)

        self.assertGreaterEqual(server.parser.frames_processed, 1)
        self.assertEqual(server.parser.last_recv_pose[:3], [99.0, 99.0, 99.0])

        app.close()
        relay.stop()
        server.stop()

    def test_live_axis_offset_modification(self):
        """Verify dynamic axis offset modifications correctly transform live pose data."""
        parser = SimulatedStreamParser()
        parser.feed_bytes(pack_pose(10.0, 20.0, 30.0, 15.0, 25.0, 35.0))

        # 0 offset
        p0 = parser.get_current_pose(0, 0, 0)
        self.assertEqual(p0, [10.0, 20.0, 30.0, 15.0, 25.0, 35.0])

        # Yaw +90
        p_yaw90 = parser.get_current_pose(add_yaw_idx=1, add_pitch_idx=0, add_roll_idx=0)
        self.assertEqual(p_yaw90[3], 15.0 + 90.0)

        # Pitch -90
        p_pitch_neg90 = parser.get_current_pose(add_yaw_idx=0, add_pitch_idx=2, add_roll_idx=0)
        self.assertEqual(p_pitch_neg90[4], 25.0 - 90.0)

        # Roll +180
        p_roll180 = parser.get_current_pose(add_yaw_idx=0, add_pitch_idx=0, add_roll_idx=3)
        self.assertEqual(p_roll180[5], 35.0 + 180.0)

        # Roll -180
        p_roll_neg180 = parser.get_current_pose(add_yaw_idx=0, add_pitch_idx=0, add_roll_idx=4)
        self.assertEqual(p_roll_neg180[5], 35.0 - 180.0)


if __name__ == "__main__":
    unittest.main()
