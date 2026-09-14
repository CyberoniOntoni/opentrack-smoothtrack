"""Tier 5: mock adb hung start-server vs named timeout story (CLI only)."""

import os
import sys
import tempfile
import time
import unittest

TESTS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if TESTS_DIR not in sys.path:
    sys.path.insert(0, TESTS_DIR)

from mock_adb_cli import run_mock_adb as _run


class TestTier5MockAdbStartServerDelay(unittest.TestCase):
    def test_two_second_start_server_exceeds_old_quick_timeout_window(self):
        """DEFAULT used to be 2000ms; start-server now allows 20s. Mock sleeps 2s."""
        with tempfile.TemporaryDirectory() as tmp:
            env = {
                "MOCK_ADB_LOG": os.path.join(tmp, "adb.log"),
                "MOCK_ADB_START_SERVER_MS": "2000",
            }
            t0 = time.perf_counter()
            result = _run(["start-server"], env, timeout=10)
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            self.assertEqual(result.returncode, 0)
            self.assertGreaterEqual(elapsed_ms, 1900.0)
            self.assertLess(elapsed_ms, 5000.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
