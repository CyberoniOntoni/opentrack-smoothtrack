"""Tier 1: mock adb CLI coverage (devices, reverse, push)."""

import os
import sys
import tempfile
import unittest

TESTS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if TESTS_DIR not in sys.path:
    sys.path.insert(0, TESTS_DIR)

from mock_adb_cli import ADB, run_mock_adb as _run


class TestTier1MockAdbCommands(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.log = os.path.join(self.tmpdir.name, "adb.log")
        self.fs = os.path.join(self.tmpdir.name, "fs")
        os.makedirs(self.fs, exist_ok=True)
        self.env = {"MOCK_ADB_LOG": self.log, "MOCK_ADB_FS": self.fs}

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_devices_and_reverse_are_logged(self):
        self.assertTrue(os.path.isfile(ADB), f"missing mock adb wrapper: {ADB}")
        devices = _run(["devices", "-l"], self.env)
        reverse = _run(["reverse", "tcp:4242", "tcp:4242"], self.env)
        self.assertEqual(devices.returncode, 0)
        self.assertEqual(reverse.returncode, 0)
        with open(self.log, encoding="utf-8") as fh:
            lines = [line.rstrip("\n") for line in fh]
        self.assertEqual(lines, ["devices -l", "reverse tcp:4242 tcp:4242"])

    def test_push_installs_relay_into_mock_fs(self):
        src = os.path.join(self.tmpdir.name, "st-relay-arm64")
        with open(src, "wb") as fh:
            fh.write(b"RELAY")
        result = _run(["push", src, "/data/local/tmp/st-relay"], self.env)
        self.assertEqual(result.returncode, 0)
        dest = os.path.join(self.fs, "st-relay")
        self.assertTrue(os.path.isfile(dest))
        with open(dest, "rb") as fh:
            self.assertEqual(fh.read(), b"RELAY")


if __name__ == "__main__":
    unittest.main(verbosity=2)
