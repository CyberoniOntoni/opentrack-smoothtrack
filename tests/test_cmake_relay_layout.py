"""CMake-owned relay outputs and a single CI install copy."""

import os
import re
import unittest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
CMAKE_FILE = os.path.join(REPO_ROOT, "tracker-smoothtrack", "CMakeLists.txt")
WORKFLOW_FILE = os.path.join(REPO_ROOT, ".github", "workflows", "windows-11.yml")

_SOURCE_RELAY_DIR = 'Join-Path $env:GITHUB_WORKSPACE "tracker-smoothtrack\\android"'


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


def _workflow_step(name: str) -> str:
    with open(WORKFLOW_FILE, encoding="utf-8") as f:
        text = f.read()
    marker = f"- name: {name}"
    start = text.find(marker)
    if start < 0:
        raise AssertionError(f"step {name!r} not found")
    run_at = text.find("run: |", start)
    if run_at < 0:
        raise AssertionError(f"run block for {name!r} not found")
    body_start = text.find("\n", run_at) + 1
    rest = text[body_start:]
    next_step = re.search(r"\n      - name:", rest)
    return rest if not next_step else rest[: next_step.start()]


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

        package = _workflow_step("Package install tree")
        self.assertNotIn(_SOURCE_RELAY_DIR, package)
        self.assertIn("modules\\android", package)
        self.assertIn("build\\tracker-smoothtrack\\android", package)

    def test_compile_step_exports_ndk_and_does_not_write_source_tree_relays(self):
        compile_step = _workflow_step("Compile Android SmoothTrack USB relay daemon")
        self.assertNotIn(_SOURCE_RELAY_DIR, compile_step)
        self.assertNotIn('Join-Path $relayDir "st-relay-arm64"', compile_step)
        self.assertNotIn('Join-Path $relayDir "st-relay-armv7"', compile_step)
        self.assertNotIn("-static", compile_step)
        self.assertIn("GITHUB_ENV", compile_step)
        self.assertIn("ANDROID_NDK_ROOT", compile_step)


if __name__ == "__main__":
    unittest.main(verbosity=2)
