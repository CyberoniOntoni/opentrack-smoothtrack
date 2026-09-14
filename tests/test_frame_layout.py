"""Single 48-byte pose frame layout check against relay.c."""

import os
import struct
import unittest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
RELAY_C = os.path.join(REPO_ROOT, "tracker-smoothtrack", "android", "relay.c")


class TestFrameLayout(unittest.TestCase):
    def test_packet_size_is_48_bytes_six_doubles(self):
        with open(RELAY_C, encoding="utf-8") as fh:
            src = fh.read()
        self.assertIn("#define PACKET_SIZE 48", src)
        self.assertIn("6 * sizeof(double)", src)
        self.assertEqual(6 * struct.calcsize("<d"), 48)


if __name__ == "__main__":
    unittest.main(verbosity=2)
