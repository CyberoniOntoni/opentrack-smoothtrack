"""Subprocess tests for tests/mock_adb — these fail until the CLI contract works."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
import unittest

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _TESTS_DIR not in sys.path:
    sys.path.insert(0, _TESTS_DIR)

from mock_adb_cli import ADB, mock_adb_env, run_mock_adb as _run


class TestMockAdbCli(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.log = os.path.join(self.tmpdir.name, "adb.log")
        self.fs = os.path.join(self.tmpdir.name, "fs")
        os.makedirs(self.fs, exist_ok=True)
        self.env = {
            "MOCK_ADB_LOG": self.log,
            "MOCK_ADB_FS": self.fs,
        }

    def tearDown(self):
        self.tmpdir.cleanup()

    def _log_lines(self):
        with open(self.log, encoding="utf-8") as fh:
            return [line.rstrip("\n") for line in fh]

    def test_argv_recorded_to_mock_adb_log(self):
        result = _run(["devices", "-l"], self.env)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(self._log_lines(), ["devices -l"])

    def test_devices_l_default_output(self):
        result = _run(["devices", "-l"], self.env)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(
            result.stdout.strip(),
            "emulator-5554 device product:sdk model:sdk",
        )

    def test_devices_l_honors_mock_adb_devices(self):
        env = dict(self.env)
        env["MOCK_ADB_DEVICES"] = "pixel-usb device product:oriole model:Pixel_6"
        result = _run(["devices", "-l"], env)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), env["MOCK_ADB_DEVICES"])

    def test_start_server_default_is_immediate(self):
        t0 = time.perf_counter()
        result = _run(["start-server"], self.env)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        self.assertEqual(result.returncode, 0)
        self.assertLess(elapsed_ms, 1000.0)

    def test_start_server_sleeps_mock_adb_start_server_ms(self):
        env = dict(self.env)
        env["MOCK_ADB_START_SERVER_MS"] = "250"
        t0 = time.perf_counter()
        result = _run(["start-server"], env, timeout=5)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        self.assertEqual(result.returncode, 0)
        self.assertGreaterEqual(elapsed_ms, 200.0)
        self.assertIn("start-server", self._log_lines())

    def test_reverse_tcp_logged_and_exits_zero(self):
        result = _run(["reverse", "tcp:4242", "tcp:4242"], self.env)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(self._log_lines(), ["reverse tcp:4242 tcp:4242"])

    def test_reverse_remove_exits_zero(self):
        result = _run(["reverse", "--remove", "tcp:4242"], self.env)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(self._log_lines(), ["reverse --remove tcp:4242"])

    def test_push_copies_src_to_mock_adb_fs(self):
        src = os.path.join(self.tmpdir.name, "st-relay-arm64")
        with open(src, "wb") as fh:
            fh.write(b"ELF-RELAY")
        result = _run(["push", src, "/data/local/tmp/st-relay"], self.env)
        self.assertEqual(result.returncode, 0)
        dest = os.path.join(self.fs, "st-relay")
        self.assertTrue(os.path.isfile(dest), "push must copy into $MOCK_ADB_FS/st-relay")
        with open(dest, "rb") as fh:
            self.assertEqual(fh.read(), b"ELF-RELAY")

    def test_shell_getprop_abi(self):
        result = _run(["shell", "getprop", "ro.product.cpu.abi"], self.env)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "arm64-v8a")

    def test_shell_chmod_exits_zero(self):
        result = _run(["shell", "chmod", "755", "/data/local/tmp/st-relay"], self.env)
        self.assertEqual(result.returncode, 0)

    def test_shell_kill_helpers_exit_zero(self):
        for args in (
            ["shell", "pkill", "-f", "st-relay"],
            ["shell", "killall", "st-relay"],
            ["shell", "pidof", "st-relay"],
            ["shell", "kill", "1"],
            [
                "shell",
                "sh",
                "-c",
                "pkill -f st-relay || killall st-relay || kill $(pidof st-relay) || true",
            ],
        ):
            with self.subTest(args=args):
                result = _run(args, self.env)
                self.assertEqual(result.returncode, 0)

    def test_relay_exit_one_prints_stderr_and_exits_five(self):
        env = dict(self.env)
        env["MOCK_ADB_RELAY_EXIT"] = "1"
        result = _run(["shell", "/data/local/tmp/st-relay", "4242", "4242"], env)
        self.assertEqual(result.returncode, 5)
        self.assertIn("Failed to connect to TCP reverse tunnel", result.stderr)

    def test_relay_sleeps_until_killed(self):
        proc = subprocess.Popen(
            [ADB, "shell", "/data/local/tmp/st-relay", "4242", "4242"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=mock_adb_env(self.env),
        )
        try:
            time.sleep(0.3)
            self.assertIsNone(proc.poll(), "relay must sleep until killed")
        finally:
            proc.kill()
            proc.wait(timeout=5)


if __name__ == "__main__":
    unittest.main(verbosity=2)
