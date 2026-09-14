"""Tier 3: mock adb serial targeting plus reverse remove."""

import os
import sys
import tempfile
import unittest

TESTS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if TESTS_DIR not in sys.path:
    sys.path.insert(0, TESTS_DIR)

from mock_adb_cli import run_mock_adb as _run


class TestTier3MockAdbSerialAndTeardown(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.log = os.path.join(self.tmpdir.name, "adb.log")
        self.env = {"MOCK_ADB_LOG": self.log}

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_serial_flag_is_logged_with_reverse(self):
        result = _run(
            ["-s", "emulator-5554", "reverse", "tcp:4242", "tcp:4242"],
            self.env,
        )
        self.assertEqual(result.returncode, 0)
        with open(self.log, encoding="utf-8") as fh:
            lines = [line.rstrip("\n") for line in fh]
        self.assertEqual(lines, ["-s emulator-5554 reverse tcp:4242 tcp:4242"])

    def test_reverse_remove_after_install(self):
        _run(["reverse", "tcp:4242", "tcp:4242"], self.env)
        result = _run(["reverse", "--remove", "tcp:4242"], self.env)
        self.assertEqual(result.returncode, 0)
        with open(self.log, encoding="utf-8") as fh:
            lines = [line.rstrip("\n") for line in fh]
        self.assertEqual(
            lines,
            ["reverse tcp:4242 tcp:4242", "reverse --remove tcp:4242"],
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
