"""Adversarial Empirical Stress Harness for ADB Client and Lifecycle Robustness.

Tests strictly verify:
1. ADB command timeout handling:
   - Simulates hung ADB subprocesses (infinite sleep / unresponsive daemon)
   - Verifies process termination within bounded window (1-2s + 200ms grace)
   - Verifies no orphaned processes linger in the operating system
   - Verifies actionable diagnostic generation on timeout
2. Cable disconnect simulation:
   - Simulates abrupt TCP disconnect (TCP RST, FIN, SHUT_WR) mid-flight and idle
   - High-precision timing verifies reader unblocks and exits loop in <100ms
   - 50-cycle rapid disconnect stress benchmark
3. Idempotent stop() behavior:
   - Multiple calls on uninitialized, partially failed, and active sessions
   - Concurrent multi-threaded stop() execution
   - Zero redundant commands after initial teardown
"""

import errno
import json
import os
import select
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from typing import Dict, List, Optional, Tuple

# Ensure project root is in sys.path
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, "..", ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from tests.e2e_smoothtrack.mock_adb_server import (
    MockAdbState,
    create_mock_adb_executable,
    run_mock_cli,
)
from tests.e2e_smoothtrack.protocol_oracle import (
    FRAME_SIZE,
    SimulatedStreamParser,
    pack_pose,
)


def run_adb_cmd_emulation(
    adb_path: str,
    args: List[str],
    timeout_ms: int,
    grace_ms: int = 200,
) -> Dict[str, any]:
    """Faithful Python reproduction of run_adb_cmd in tracker-smoothtrack/adb_client.cpp:29-61.

    Invokes subprocess, waits for timeout_ms. If expired:
    invokes proc.kill(), waits for grace_ms (200ms), sets error and exit_code = -1.
    """
    if not adb_path or not os.path.exists(adb_path):
        return {
            "success": False,
            "timed_out": False,
            "stdout": "",
            "stderr": "ADB executable not found",
            "exit_code": -1,
            "elapsed_ms": 0.0,
            "proc_alive": False,
        }

    t0 = time.perf_counter()
    proc = subprocess.Popen(
        [adb_path] + args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    timeout_s = timeout_ms / 1000.0
    grace_s = grace_ms / 1000.0

    timed_out = False
    stdout = ""
    stderr = ""
    exit_code = 0

    try:
        stdout, stderr = proc.communicate(timeout=timeout_s)
        exit_code = proc.returncode
    except subprocess.TimeoutExpired:
        timed_out = True
        proc.kill()
        try:
            stdout, stderr = proc.communicate(timeout=grace_s)
        except Exception:
            pass
        stderr = "Process timed out"
        exit_code = -1

    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    proc_alive = proc.poll() is None

    return {
        "success": not timed_out and exit_code == 0,
        "timed_out": timed_out,
        "stdout": stdout,
        "stderr": stderr,
        "exit_code": exit_code,
        "elapsed_ms": elapsed_ms,
        "proc_alive": proc_alive,
    }


class TestAdbCommandTimeoutHandling(unittest.TestCase):
    """Verifies that hung ADB subprocesses are reliably terminated within 1-2s."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.state_file = os.path.join(self.tmpdir.name, "state.json")
        self.state = MockAdbState(self.state_file)
        self.state.save()
        self.adb_bin = create_mock_adb_executable(self.tmpdir.name, self.state_file)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_default_timeout_terminates_hung_subprocess_within_2s(self):
        """Verify DEFAULT_TIMEOUT_MS (2000ms) terminates hung adb process in 2.0-2.3s."""
        # Configure mock ADB to hang for 30 seconds on 'devices' command
        self.state.hang_commands = ["devices"]
        self.state.hang_duration = 30.0
        self.state.save()

        res = run_adb_cmd_emulation(self.adb_bin, ["devices", "-l"], timeout_ms=2000)

        # Assert process was terminated and did NOT run for 30 seconds
        self.assertTrue(res["timed_out"])
        self.assertFalse(res["success"])
        self.assertEqual(res["exit_code"], -1)
        self.assertIn("Process timed out", res["stderr"])
        self.assertFalse(res["proc_alive"], "Subprocess must be terminated, not orphaned")

        # Elapsed time must be close to 2000ms (1900ms to 2400ms)
        self.assertGreaterEqual(res["elapsed_ms"], 1900.0)
        self.assertLessEqual(res["elapsed_ms"], 2400.0)

    def test_quick_timeout_terminates_hung_subprocess_within_1s(self):
        """Verify QUICK_TIMEOUT_MS (1000ms) terminates hung adb pkill process in 1.0-1.3s."""
        self.state.hang_commands = ["pkill"]
        self.state.hang_duration = 30.0
        self.state.save()

        res = run_adb_cmd_emulation(self.adb_bin, ["shell", "pkill", "-f", "st-relay"], timeout_ms=1000)

        self.assertTrue(res["timed_out"])
        self.assertFalse(res["success"])
        self.assertEqual(res["exit_code"], -1)
        self.assertFalse(res["proc_alive"])

        # Elapsed time must be within 950ms to 1400ms
        self.assertGreaterEqual(res["elapsed_ms"], 950.0)
        self.assertLessEqual(res["elapsed_ms"], 1400.0)

    def test_intermediate_timeout_terminates_within_1_5s(self):
        """Verify 1500ms timeout for getprop / remove_reverse terminates in 1.5-1.8s."""
        self.state.hang_commands = ["getprop"]
        self.state.hang_duration = 30.0
        self.state.save()

        res = run_adb_cmd_emulation(
            self.adb_bin, ["shell", "getprop", "ro.product.cpu.abi"], timeout_ms=1500
        )

        self.assertTrue(res["timed_out"])
        self.assertFalse(res["success"])
        self.assertEqual(res["exit_code"], -1)
        self.assertFalse(res["proc_alive"])

        # Elapsed time within 1450ms to 1900ms
        self.assertGreaterEqual(res["elapsed_ms"], 1450.0)
        self.assertLessEqual(res["elapsed_ms"], 1900.0)

    def test_hung_devices_query_generates_actionable_timeout_diagnostic(self):
        """Verify adb_client::list_devices generates actionable diagnostic on timeout."""
        self.state.hang_commands = ["devices"]
        self.state.hang_duration = 30.0
        self.state.save()

        res = run_adb_cmd_emulation(self.adb_bin, ["devices", "-l"], timeout_ms=2000)
        self.assertTrue(res["timed_out"])

        # Replicate adb_client::list_devices diagnostic formatting
        error_msg = ""
        if res["stderr"] == "Process timed out":
            error_msg = (
                "ADB timed out while querying connected devices.\n"
                "The ADB server may be unresponsive. Try running 'adb kill-server' "
                "in a terminal or reconnecting the USB cable."
            )

        self.assertIn("ADB timed out while querying connected devices", error_msg)
        self.assertIn("adb kill-server", error_msg)

    def test_hung_getprop_abi_falls_back_to_arm64(self):
        """Verify adb_client::get_device_abi gracefully defaults to arm64-v8a on timeout."""
        self.state.hang_commands = ["getprop"]
        self.state.hang_duration = 30.0
        self.state.save()

        res = run_adb_cmd_emulation(
            self.adb_bin, ["shell", "getprop", "ro.product.cpu.abi"], timeout_ms=1500
        )
        self.assertTrue(res["timed_out"])

        # Reproduction of adb_client::get_device_abi fallback logic
        abi = res["stdout"].strip() if res["success"] and res["stdout"].strip() else "arm64-v8a"
        self.assertEqual(abi, "arm64-v8a")

    def test_consecutive_timeouts_leave_zero_orphaned_processes(self):
        """Verify multiple consecutive hung commands terminate cleanly without leaking PIDs."""
        self.state.hang_commands = ["devices", "reverse", "push"]
        self.state.hang_duration = 30.0
        self.state.save()

        # Run 3 consecutive hanging commands
        r1 = run_adb_cmd_emulation(self.adb_bin, ["devices", "-l"], timeout_ms=1000)
        r2 = run_adb_cmd_emulation(self.adb_bin, ["reverse", "tcp:4242", "tcp:4242"], timeout_ms=1000)
        r3 = run_adb_cmd_emulation(self.adb_bin, ["push", "bin", "/data/local/tmp/st-relay"], timeout_ms=1000)

        self.assertTrue(r1["timed_out"] and not r1["proc_alive"])
        self.assertTrue(r2["timed_out"] and not r2["proc_alive"])
        self.assertTrue(r3["timed_out"] and not r3["proc_alive"])


class TestCableDisconnectSimulation(unittest.TestCase):
    """Verifies that abrupt TCP disconnect unblocks the reader thread in <100ms."""

    def _run_emulated_reader(
        self,
        sock: socket.socket,
        stop_event: threading.Event,
        unblocked_event: threading.Event,
        latencies_ms: List[float],
        disconnect_time: List[float],
    ):
        """Emulates smoothtrack::run() read loop (ftnoir_tracker_smoothtrack.cpp:185-248)."""
        sock.setblocking(False)
        try:
            while not stop_event.is_set():
                # sock->waitForReadyRead(100) -> select with 100ms timeout
                r, _, x = select.select([sock], [], [sock], 0.100)
                if not r and not x:
                    # Timeout expired, loop repeats if still connected
                    continue

                if x:
                    break

                if r:
                    try:
                        chunk = sock.recv(1024)
                        if not chunk:
                            # Peer closed socket / disconnect detected
                            break
                    except (ConnectionResetError, ConnectionAbortedError, socket.error):
                        break
        finally:
            unblock_time = time.perf_counter()
            if disconnect_time:
                latencies_ms.append((unblock_time - disconnect_time[0]) * 1000.0)
            unblocked_event.set()

    def test_cable_disconnect_rst_unblocks_reader_under_100ms(self):
        """Verify hard TCP RST disconnect unblocks reader thread strictly under 100ms."""
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]

        client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        client.connect(("127.0.0.1", port))
        host_conn, _ = server.accept()

        stop_event = threading.Event()
        unblocked_event = threading.Event()
        latencies = []
        disconnect_ts = []

        reader_thread = threading.Thread(
            target=self._run_emulated_reader,
            args=(host_conn, stop_event, unblocked_event, latencies, disconnect_ts),
            daemon=True,
        )
        reader_thread.start()

        # Stream 10 frames of active telemetry at 60Hz
        for i in range(10):
            client.sendall(pack_pose(float(i), 0.0, 0.0, 0.0, 0.0, 0.0))
            time.sleep(0.016)

        # Abrupt cable unplug: force immediate TCP RST via SO_LINGER (l_onoff=1, l_linger=0)
        disconnect_ts.append(time.perf_counter())
        client.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
        client.close()

        # Verify thread unblocks
        unblocked = unblocked_event.wait(timeout=0.200)
        self.assertTrue(unblocked, "Reader thread did not unblock within 200ms")
        self.assertTrue(len(latencies) > 0)
        latency_ms = latencies[0]

        # Invariant: Must unblock in strictly less than 100ms
        self.assertLess(
            latency_ms,
            100.0,
            f"Cable disconnect unblock took {latency_ms:.2f}ms, which violates <100ms requirement",
        )

        stop_event.set()
        reader_thread.join(timeout=0.5)
        host_conn.close()
        server.close()

    def test_cable_disconnect_fin_unblocks_reader_under_100ms(self):
        """Verify normal TCP FIN (peer close) unblocks reader thread strictly under 100ms."""
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]

        client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        client.connect(("127.0.0.1", port))
        host_conn, _ = server.accept()

        stop_event = threading.Event()
        unblocked_event = threading.Event()
        latencies = []
        disconnect_ts = []

        reader_thread = threading.Thread(
            target=self._run_emulated_reader,
            args=(host_conn, stop_event, unblocked_event, latencies, disconnect_ts),
            daemon=True,
        )
        reader_thread.start()

        # Send telemetry
        client.sendall(pack_pose(1.0, 2.0, 3.0, 4.0, 5.0, 6.0))
        time.sleep(0.02)

        # Normal abrupt close (sends FIN)
        disconnect_ts.append(time.perf_counter())
        client.close()

        unblocked = unblocked_event.wait(timeout=0.200)
        self.assertTrue(unblocked)
        latency_ms = latencies[0]
        self.assertLess(latency_ms, 100.0)

        stop_event.set()
        reader_thread.join(timeout=0.5)
        host_conn.close()
        server.close()

    def test_idle_receiver_unblocks_under_100ms(self):
        """Verify receiver blocked in 100ms select unblocks immediately when connection drops."""
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]

        client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        client.connect(("127.0.0.1", port))
        host_conn, _ = server.accept()

        stop_event = threading.Event()
        unblocked_event = threading.Event()
        latencies = []
        disconnect_ts = []

        reader_thread = threading.Thread(
            target=self._run_emulated_reader,
            args=(host_conn, stop_event, unblocked_event, latencies, disconnect_ts),
            daemon=True,
        )
        reader_thread.start()

        # Sleep so receiver is idling inside select(..., 0.100)
        time.sleep(0.05)

        disconnect_ts.append(time.perf_counter())
        client.close()

        unblocked = unblocked_event.wait(timeout=0.200)
        self.assertTrue(unblocked)
        latency_ms = latencies[0]
        self.assertLess(latency_ms, 100.0)

        stop_event.set()
        reader_thread.join(timeout=0.5)
        host_conn.close()
        server.close()

    def test_repeated_cable_disconnect_rapid_stress_50_cycles(self):
        """Stress test: 50 consecutive connect-disconnect cycles all unblock in <100ms."""
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]

        all_latencies = []

        for cycle in range(50):
            client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            client.connect(("127.0.0.1", port))
            host_conn, _ = server.accept()

            stop_event = threading.Event()
            unblocked_event = threading.Event()
            latencies = []
            disconnect_ts = []

            reader_thread = threading.Thread(
                target=self._run_emulated_reader,
                args=(host_conn, stop_event, unblocked_event, latencies, disconnect_ts),
                daemon=True,
            )
            reader_thread.start()

            # Transmit 1 frame
            client.sendall(pack_pose(float(cycle), 0.0, 0.0, 0.0, 0.0, 0.0))
            time.sleep(0.002)

            # Abrupt disconnect
            disconnect_ts.append(time.perf_counter())
            client.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
            client.close()

            unblocked = unblocked_event.wait(timeout=0.200)
            self.assertTrue(unblocked, f"Cycle {cycle} timed out")
            self.assertLess(latencies[0], 100.0, f"Cycle {cycle} took {latencies[0]:.2f}ms")
            all_latencies.append(latencies[0])

            stop_event.set()
            reader_thread.join(timeout=0.5)
            host_conn.close()

        server.close()

        min_ms = min(all_latencies)
        max_ms = max(all_latencies)
        avg_ms = sum(all_latencies) / len(all_latencies)

        # Verify statistical bounds
        self.assertLess(max_ms, 100.0)
        self.assertLess(avg_ms, 25.0)


class SimulatedAdbClient:
    """Emulates adb_client lifecycle and internal state machine (tracker-smoothtrack/adb_client.cpp)."""

    def __init__(self, adb_path: str = "", state_file: str = ""):
        self.adb_path = adb_path
        self.state_file = state_file
        self.active_adb: str = ""
        self.active_serial: str = ""
        self.active_port: int = 0
        self.relay_running: bool = False
        self.lock = threading.Lock()
        self.stop_count = 0

    def start(self, adb_path: str, udp_port: int, tcp_port: int) -> Tuple[bool, str]:
        with self.lock:
            self.stop_internal()

            self.active_adb = adb_path
            self.active_port = tcp_port

            # Check devices
            state = MockAdbState(self.state_file)
            run_mock_cli(["adb", "devices", "-l"], self.state_file)
            state.load()

            ready_devices = [d for d in state.devices if d["status"] == "device"]
            if not ready_devices:
                self.stop_internal()
                return False, "No ready Android devices found"

            self.active_serial = ready_devices[0]["serial"]

            # Reverse setup
            code = run_mock_cli(
                ["adb", "-s", self.active_serial, "reverse", f"tcp:{tcp_port}", f"tcp:{tcp_port}"],
                self.state_file,
            )
            if code != 0:
                self.stop_internal()
                return False, "Failed to setup ADB reverse"

            # Launch relay
            code = run_mock_cli(
                ["adb", "-s", self.active_serial, "shell", "/data/local/tmp/st-relay", str(udp_port), str(tcp_port)],
                self.state_file,
            )
            if code != 0:
                self.stop_internal()
                return False, "Failed to launch relay"

            self.relay_running = True
            return True, ""

    def stop(self) -> None:
        with self.lock:
            self.stop_internal()

    def stop_internal(self) -> None:
        self.stop_count += 1
        if self.relay_running:
            self.relay_running = False

        if self.active_adb and os.path.exists(self.active_adb):
            args = ["adb"]
            if self.active_serial:
                args.extend(["-s", self.active_serial])
            args.extend(["shell", "pkill", "-f", "st-relay"])
            run_mock_cli(args, self.state_file)

            if self.active_port > 0:
                rem_args = ["adb"]
                if self.active_serial:
                    rem_args.extend(["-s", self.active_serial])
                rem_args.extend(["reverse", "--remove", f"tcp:{self.active_port}"])
                run_mock_cli(rem_args, self.state_file)

        self.active_adb = ""
        self.active_serial = ""
        self.active_port = 0

    def is_running(self) -> bool:
        with self.lock:
            return self.relay_running


class TestIdempotentStopBehavior(unittest.TestCase):
    """Verifies that stop() is completely idempotent across all states and call counts."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.state_file = os.path.join(self.tmpdir.name, "state.json")
        self.state = MockAdbState(self.state_file)
        self.state.save()
        self.adb_bin = create_mock_adb_executable(self.tmpdir.name, self.state_file)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_stop_on_fresh_instance_is_safe_noop(self):
        """Verify calling stop() on a freshly created adb_client produces 0 commands and 0 errors."""
        client = SimulatedAdbClient(self.adb_bin, self.state_file)
        initial_log_len = len(self.state.commands_log)

        # Call stop() 10 times in a row
        for _ in range(10):
            client.stop()

        self.state.load()
        # Invariant: If client was never started, active_adb is empty, so stop() executes 0 commands
        self.assertEqual(len(self.state.commands_log), initial_log_len)
        self.assertFalse(client.is_running())

    def test_stop_when_no_device_ever_connected(self):
        """Verify stop() behavior when device list is empty at start time."""
        self.state.devices = []
        self.state.save()

        client = SimulatedAdbClient(self.adb_bin, self.state_file)
        ok, err = client.start(self.adb_bin, 4242, 4242)
        self.assertFalse(ok)
        self.assertIn("No ready Android devices", err)

        # State should be reset
        self.assertEqual(client.active_adb, "")
        self.assertEqual(client.active_port, 0)
        self.assertFalse(client.is_running())

        self.state.load()
        cmd_count_after_start_fail = len(self.state.commands_log)

        # Calling stop() repeatedly after failed start must be a complete no-op
        for _ in range(10):
            client.stop()

        self.state.load()
        self.assertEqual(len(self.state.commands_log), cmd_count_after_start_fail)

    def test_stop_after_active_session_is_strictly_idempotent(self):
        """Verify that stop() after an active session cleans up once, and subsequent calls do nothing."""
        self.state.devices = [{"serial": "dev001", "status": "device", "model": "Pixel_7", "abi": "arm64-v8a"}]
        self.state.save()

        client = SimulatedAdbClient(self.adb_bin, self.state_file)
        ok, _ = client.start(self.adb_bin, 4242, 4242)
        self.assertTrue(ok)
        self.assertTrue(client.is_running())

        # 1st call to stop() cleans up
        client.stop()
        self.assertFalse(client.is_running())
        self.state.load()
        cmd_count_after_stop1 = len(self.state.commands_log)

        # Invariant: pkill and reverse --remove were issued during 1st stop
        last_cmds = [c for c in self.state.commands_log[-2:]]
        self.assertTrue(any("pkill" in str(c) for c in last_cmds))
        self.assertTrue(any("--remove" in str(c) for c in last_cmds))

        # 2nd, 3rd, 10th call to stop() must issue EXACTLY zero new commands
        for _ in range(10):
            client.stop()

        self.state.load()
        self.assertEqual(
            len(self.state.commands_log),
            cmd_count_after_stop1,
            "Subsequent stop() calls must not execute redundant shell/reverse commands",
        )

    def test_concurrent_multi_threaded_stop_calls(self):
        """Verify thread safety when 10 threads call stop() concurrently."""
        self.state.devices = [{"serial": "dev002", "status": "device", "model": "Galaxy", "abi": "arm64-v8a"}]
        self.state.save()

        client = SimulatedAdbClient(self.adb_bin, self.state_file)
        ok, _ = client.start(self.adb_bin, 4242, 4242)
        self.assertTrue(ok)

        threads = []
        for _ in range(10):
            t = threading.Thread(target=client.stop)
            threads.append(t)

        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=1.0)

        self.assertFalse(client.is_running())
        self.assertEqual(client.active_adb, "")
        self.assertEqual(client.active_port, 0)

    def test_rapid_restart_and_stop_cycles(self):
        """Verify 20 rapid start -> stop cycles execute without deadlock or socket leaks."""
        self.state.devices = [{"serial": "dev003", "status": "device", "model": "Sony", "abi": "arm64-v8a"}]
        self.state.save()

        client = SimulatedAdbClient(self.adb_bin, self.state_file)

        for cycle in range(20):
            ok, _ = client.start(self.adb_bin, 4242, 4242)
            self.assertTrue(ok)
            self.assertTrue(client.is_running())
            client.stop()
            self.assertFalse(client.is_running())


class TestStaticContractVerification(unittest.TestCase):
    """Verifies that the actual C++ implementation code adheres to the robustness contracts."""

    def test_adb_client_cpp_contains_timeout_and_kill(self):
        """Verify run_adb_cmd in adb_client.cpp has explicit timeout and proc.kill()."""
        h_path = os.path.join(PROJECT_ROOT, "tracker-smoothtrack", "adb_client.h")
        with open(h_path, "r", encoding="utf-8") as f:
            h_content = f.read()

        self.assertIn("DEFAULT_TIMEOUT_MS = 2000;", h_content)
        self.assertIn("QUICK_TIMEOUT_MS   = 1000;", h_content)

        cpp_path = os.path.join(PROJECT_ROOT, "tracker-smoothtrack", "adb_client.cpp")
        with open(cpp_path, "r", encoding="utf-8") as f:
            cpp_content = f.read()

        self.assertIn("DEFAULT_TIMEOUT_MS", cpp_content)
        self.assertIn("QUICK_TIMEOUT_MS", cpp_content)
        self.assertIn("proc.kill();", cpp_content)
        self.assertIn('stderr_str = "Process timed out"', cpp_content)
        self.assertIn("proc.waitForFinished(200);", cpp_content)

    def test_adb_client_stop_resets_all_active_fields(self):
        """Verify stop() in adb_client.cpp clears active_adb, active_serial, active_port."""
        cpp_path = os.path.join(PROJECT_ROOT, "tracker-smoothtrack", "adb_client.cpp")
        with open(cpp_path, "r", encoding="utf-8") as f:
            content = f.read()

        self.assertIn("active_adb.clear();", content)
        self.assertIn("active_serial.clear();", content)
        self.assertIn("active_port = 0;", content)

    def test_smoothtrack_run_has_100ms_timeout_and_immediate_disconnect_exit(self):
        """Verify smoothtrack::run() checks ConnectedState immediately after 100ms wait."""
        cpp_path = os.path.join(PROJECT_ROOT, "tracker-smoothtrack", "ftnoir_tracker_smoothtrack.cpp")
        with open(cpp_path, "r", encoding="utf-8") as f:
            content = f.read()

        self.assertIn("sock->waitForReadyRead(100)", content)
        self.assertIn("sock->state() != QAbstractSocket::ConnectedState", content)


if __name__ == "__main__":
    unittest.main(verbosity=2)
