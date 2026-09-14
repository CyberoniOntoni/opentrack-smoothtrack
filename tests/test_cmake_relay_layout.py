"""CMake-owned relay outputs and a single CI install copy."""

import os
import re
import unittest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
CMAKE_FILE = os.path.join(REPO_ROOT, "tracker-smoothtrack", "CMakeLists.txt")
WORKFLOW_FILE = os.path.join(REPO_ROOT, ".github", "workflows", "windows-11.yml")


def _ps_array_items(script: str, var_name: str):
    match = re.search(rf"\${var_name}\s*=\s*@\((.*?)\)", script, re.S)
    if not match:
        raise AssertionError(f"${var_name} array not found in workflow")
    items = []
    for raw in match.group(1).split(","):
        line = re.sub(r"#.*", "", raw).strip()
        if line:
            items.append(line)
    return items


class TestCMakeRelayLayout(unittest.TestCase):
    def test_cmake_builds_relays_in_binary_dir_not_source_tree(self):
        with open(CMAKE_FILE, encoding="utf-8") as f:
            text = f.read()
        self.assertNotIn(
            "CMAKE_CURRENT_SOURCE_DIR}/android/st-relay",
            text,
            "CMake must not install(FILES) source-tree st-relay prebuilts",
        )
        self.assertIn("CMAKE_CURRENT_BINARY_DIR}/android", text)

    def test_workflow_copies_adb_once_and_relays_to_modules_android(self):
        with open(WORKFLOW_FILE, encoding="utf-8") as f:
            text = f.read()

        adb_dests = _ps_array_items(text, "adbDests")
        self.assertEqual(
            adb_dests,
            ["$install"],
            f"adb trio must copy to exactly one destination (install root), got {adb_dests}",
        )

        relay_dests = _ps_array_items(text, "relayDests")
        self.assertEqual(len(relay_dests), 1, f"relays must copy once, got {relay_dests}")
        dest = relay_dests[0]
        self.assertRegex(
            dest,
            r'modules[\\/]android',
            f"relays must copy to modules\\android, got {dest}",
        )
        self.assertNotEqual(dest, "$install")
        self.assertNotIn('Join-Path $install "android"', dest)


if __name__ == "__main__":
    unittest.main(verbosity=2)
