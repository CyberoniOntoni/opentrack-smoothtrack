"""ADB lifecycle checks against the mock CLI (no Python run_adb_cmd clone)."""

import os
import sys
import tempfile
import unittest

TESTS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if TESTS_DIR not in sys.path:
    sys.path.insert(0, TESTS_DIR)

from mock_adb_cli import run_mock_adb as _run


class TestMockAdbKillAndRelay(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.env = {"MOCK_ADB_LOG": os.path.join(self.tmpdir.name, "adb.log")}

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_portable_kill_shell_exits_zero(self):
        kill = (
            "pkill -f st-relay || killall st-relay || "
            "kill $(pidof st-relay) || kill $(pidof /data/local/tmp/st-relay) || true"
        )
        result = _run(["shell", "sh", "-c", kill], self.env)
        self.assertEqual(result.returncode, 0)

    def test_getprop_and_devices_do_not_invent_abi_in_python(self):
        result = _run(["shell", "getprop", "ro.product.cpu.abi"], self.env)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "arm64-v8a")


if __name__ == "__main__":
    unittest.main(verbosity=2)
