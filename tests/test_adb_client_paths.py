"""Path-contract checks for adb_client timeouts, relay search, and tracker copy."""

import os
import unittest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
ADB_H = os.path.join(REPO_ROOT, "tracker-smoothtrack", "adb_client.h")
ADB_CPP = os.path.join(REPO_ROOT, "tracker-smoothtrack", "adb_client.cpp")
TRACKER_CPP = os.path.join(REPO_ROOT, "tracker-smoothtrack", "ftnoir_tracker_smoothtrack.cpp")


def _function_source(text: str, signature: str) -> str:
    start = text.find(signature)
    if start < 0:
        raise AssertionError(f"{signature!r} not found")
    rest = text[start + len(signature) :]
    nxt = len(rest)
    for marker in ("\nQString ", "\nbool ", "\nvoid ", "\nint "):
        pos = rest.find(marker)
        if 0 <= pos < nxt:
            nxt = pos
    return text[start : start + len(signature) + nxt]


class TestAdbClientPaths(unittest.TestCase):
    def test_header_named_timeouts(self):
        with open(ADB_H, encoding="utf-8") as f:
            text = f.read()
        self.assertIn("START_SERVER_TIMEOUT_MS = 20000", text)
        self.assertNotIn("DEFAULT_TIMEOUT_MS = 2000", text)

    def test_find_relay_binary_has_no_x86_64(self):
        with open(ADB_CPP, encoding="utf-8") as f:
            text = f.read()
        body = _function_source(text, "QString adb_client::find_relay_binary")
        self.assertNotIn("x86_64", body)

    def test_android_accept_timeout_does_not_say_tap_play(self):
        with open(TRACKER_CPP, encoding="utf-8") as f:
            text = f.read()
        start = text.find("module_status smoothtrack::start_android")
        self.assertGreaterEqual(start, 0)
        nxt = text.find("\nmodule_status ", start + 1)
        body = text[start:nxt] if nxt > start else text[start:]
        self.assertNotIn("Tap Play in SmoothTrack", body)


if __name__ == "__main__":
    unittest.main(verbosity=2)
