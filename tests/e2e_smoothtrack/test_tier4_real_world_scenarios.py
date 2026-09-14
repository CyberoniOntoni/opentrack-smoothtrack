"""Tier 4: mock adb deploy/launch sequence used by adb_client::start."""

import os
import sys
import tempfile
import unittest

TESTS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if TESTS_DIR not in sys.path:
    sys.path.insert(0, TESTS_DIR)

from mock_adb_cli import run_mock_adb as _run


class TestTier4MockAdbLifecycle(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.log = os.path.join(self.tmpdir.name, "adb.log")
        self.fs = os.path.join(self.tmpdir.name, "fs")
        os.makedirs(self.fs, exist_ok=True)
        self.env = {"MOCK_ADB_LOG": self.log, "MOCK_ADB_FS": self.fs}

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_start_sequence_push_chmod_getprop(self):
        src = os.path.join(self.tmpdir.name, "st-relay-arm64")
        with open(src, "wb") as fh:
            fh.write(b"RELAYBIN")
        steps = [
            _run(["start-server"], self.env),
            _run(["devices", "-l"], self.env),
            _run(["shell", "getprop", "ro.product.cpu.abi"], self.env),
            _run(["push", src, "/data/local/tmp/st-relay"], self.env),
            _run(["shell", "chmod", "755", "/data/local/tmp/st-relay"], self.env),
            _run(["shell", "pkill", "-f", "st-relay"], self.env),
        ]
        self.assertTrue(all(r.returncode == 0 for r in steps))
        self.assertEqual(steps[2].stdout.strip(), "arm64-v8a")
        dest = os.path.join(self.fs, "st-relay")
        with open(dest, "rb") as fh:
            self.assertEqual(fh.read(), b"RELAYBIN")

    def test_relay_immediate_failure_mode(self):
        env = dict(self.env)
        env["MOCK_ADB_RELAY_EXIT"] = "1"
        result = _run(["shell", "/data/local/tmp/st-relay", "4242", "4242"], env)
        self.assertEqual(result.returncode, 5)
        self.assertIn("Failed to connect to TCP reverse tunnel", result.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
