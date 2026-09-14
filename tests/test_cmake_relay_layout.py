"""CMake-owned relay outputs and a single CI install copy."""

import os
import re
import unittest
import yaml

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
CMAKE_FILE = os.path.join(REPO_ROOT, "tracker-smoothtrack", "CMakeLists.txt")
WORKFLOW_FILE = os.path.join(REPO_ROOT, ".github", "workflows", "windows-11.yml")
PINNED_PLATFORM_TOOLS_URL = (
    "https://dl.google.com/android/repository/platform-tools_r36.0.0-win.zip"
)
PINNED_PLATFORM_TOOLS_SHA256 = (
    "12C2841F354E92A0EB2FD7BF6F0F9BF8538ABCE7BD6B060AC8349D6F6A61107C"
)
NDK_PATH_EXPR = "${{ steps.setup-ndk.outputs.ndk-path }}"

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
        self.assertEqual(len(adb_dests), 1)

        package = _workflow_step("Package install tree")
        self.assertNotIn(_SOURCE_RELAY_DIR, package)
        self.assertIn("modules\\android", package)
        self.assertIn("build\\tracker-smoothtrack\\android", package)

    def test_workflow_pins_ndk_r27c_and_platform_tools_sha256(self):
        with open(WORKFLOW_FILE, encoding="utf-8") as f:
            text = f.read()

        self.assertNotIn("platform-tools-latest-windows.zip", text)
        self.assertNotIn("platform-tools_r36.0.0-windows.zip", text)
        self.assertIn("nttld/setup-ndk", text)
        self.assertIn("ndk-version: r27c", text)
        self.assertNotIn("C:\\Program Files (x86)\\Android\\android-sdk\\ndk", text)
        self.assertIn("--target st-relay-android", text)
        self.assertIn(PINNED_PLATFORM_TOOLS_URL, text)
        self.assertIn(PINNED_PLATFORM_TOOLS_SHA256, text)
        url_at = text.index(PINNED_PLATFORM_TOOLS_URL)
        sha_at = text.index(PINNED_PLATFORM_TOOLS_SHA256)
        self.assertLess(abs(sha_at - url_at), 500)

        workflow = yaml.safe_load(text)
        steps = workflow["jobs"]["windows-11-x64"]["steps"]
        setup_ndk = next(s for s in steps if s.get("uses") == "nttld/setup-ndk@v1")
        self.assertEqual(setup_ndk["with"]["ndk-version"], "r27c")
        self.assertEqual(setup_ndk["with"]["add-to-path"], False)
        export_step = next(s for s in steps if s.get("name") == "Export Android NDK")
        self.assertEqual(export_step["env"]["ANDROID_NDK_ROOT"], NDK_PATH_EXPR)
        build_relay = next(s for s in steps if s.get("name") == "Build st-relay")
        self.assertEqual(build_relay["env"]["ANDROID_NDK_ROOT"], NDK_PATH_EXPR)

    def test_compile_step_exports_ndk_and_does_not_write_source_tree_relays(self):
        compile_step = _workflow_step("Export Android NDK")
        self.assertNotIn(_SOURCE_RELAY_DIR, compile_step)
        self.assertNotIn('Join-Path $relayDir "st-relay-arm64"', compile_step)
        self.assertNotIn('Join-Path $relayDir "st-relay-armv7"', compile_step)
        self.assertNotIn("-static", compile_step)
        self.assertIn("GITHUB_ENV", compile_step)
        self.assertIn("ANDROID_NDK_ROOT", compile_step)
        self.assertNotIn("ANDROID_NDK_LATEST_HOME", compile_step)
        self.assertNotIn("Program Files", compile_step)


if __name__ == "__main__":
    unittest.main(verbosity=2)
