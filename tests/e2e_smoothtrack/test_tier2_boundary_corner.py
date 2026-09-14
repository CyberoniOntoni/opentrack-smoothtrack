"""Tier 2: mock adb empty-device and start-server delay (no constructed-string oracles)."""

import os
import sys
import tempfile
import time
import unittest

TESTS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if TESTS_DIR not in sys.path:
    sys.path.insert(0, TESTS_DIR)

from mock_adb_cli import run_mock_adb as _run


class TestTier2MockAdbBoundaries(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.log = os.path.join(self.tmpdir.name, "adb.log")
        self.env = {"MOCK_ADB_LOG": self.log}

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_empty_device_list_is_cli_output_not_a_local_string(self):
        env = dict(self.env)
        env["MOCK_ADB_DEVICES"] = ""
        result = _run(["devices", "-l"], env)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "")

    def test_start_server_delay_is_honored(self):
        env = dict(self.env)
        env["MOCK_ADB_START_SERVER_MS"] = "150"
        t0 = time.perf_counter()
        result = _run(["start-server"], env)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        self.assertEqual(result.returncode, 0)
        self.assertGreaterEqual(elapsed_ms, 100.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
