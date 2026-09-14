"""
Empirical Challenger 2 Adversarial Stress Suite for Milestone 2 Iteration 2.
Directly tests:
1. Pre-compilation clean (stale $out64 and $out32 purged prior to compilation, including read-only files and failed compiler scenarios).
2. Pre-packaging assertions (missing opentrack.exe, missing relay binary, 0-byte binaries, missing ADB libraries).
"""

import os
import sys
import shutil
import tempfile
import stat
import subprocess
import unittest
import yaml

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
WORKFLOW_FILE = os.path.join(REPO_ROOT, ".github", "workflows", "windows-11.yml")


def extract_workflow_step(step_name: str) -> str:
    with open(WORKFLOW_FILE, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    steps = data["jobs"]["windows-11-x64"]["steps"]
    for s in steps:
        if s.get("name") == step_name:
            return s.get("run", "")
    raise ValueError(f"Step '{step_name}' not found in {WORKFLOW_FILE}")


class TestM2ChallengerAdversarial(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.package_script = extract_workflow_step("Package install tree")
        cls.compile_script = extract_workflow_step("Compile Android SmoothTrack USB relay daemon")
        cls.powershell_exe = "powershell.exe"

    def run_ps_script(self, script_text: str, env_vars: dict) -> subprocess.CompletedProcess:
        temp_script_path = None
        try:
            with tempfile.NamedTemporaryFile("w", suffix=".ps1", delete=False, encoding="utf-8") as tf:
                tf.write(script_text)
                temp_script_path = tf.name

            env = os.environ.copy()
            env.update(env_vars)

            cmd = [
                self.powershell_exe,
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy", "Bypass",
                "-File", temp_script_path
            ]
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                env=env,
                cwd=env_vars.get("GITHUB_WORKSPACE", REPO_ROOT)
            )
            return result
        finally:
            if temp_script_path and os.path.exists(temp_script_path):
                try:
                    os.unlink(temp_script_path)
                except Exception:
                    pass

    # =========================================================================
    # 1. PRE-COMPILATION CLEANUP TESTS
    # =========================================================================

    def test_precompilation_clean_purges_stale_binaries_on_compiler_failure(self):
        """If stale $out64 and $out32 exist and the compiler subsequently FAILS,
        the pre-compilation clean must have already purged them so stale binaries
        do not linger and mask compilation failures."""
        with tempfile.TemporaryDirectory() as test_dir:
            ws = os.path.join(test_dir, "workspace")
            os.makedirs(ws)
            relay_dir = os.path.join(ws, "tracker-smoothtrack", "android")
            os.makedirs(relay_dir, exist_ok=True)
            with open(os.path.join(relay_dir, "relay.c"), "w", encoding="utf-8") as f:
                f.write("int main() { return 0; }\n")

            out64 = os.path.join(relay_dir, "st-relay-arm64")
            out32 = os.path.join(relay_dir, "st-relay-armv7")
            with open(out64, "wb") as f:
                f.write(b"STALE_OLD_ARM64_BINARY")
            with open(out32, "wb") as f:
                f.write(b"STALE_OLD_ARMV7_BINARY")

            self.assertTrue(os.path.exists(out64))
            self.assertTrue(os.path.exists(out32))

            mock_ndk = os.path.join(test_dir, "mock_ndk")
            llvm_bin = os.path.join(mock_ndk, "toolchains", "llvm", "prebuilt", "windows-x86_64", "bin")
            os.makedirs(llvm_bin, exist_ok=True)

            # A compiler wrapper that deliberately fails immediately without writing any output
            failing_bat = "@echo off\r\necho Compilation failed intentionally 1>&2\r\nexit /b 1\r\n"
            with open(os.path.join(llvm_bin, "aarch64-linux-android24-clang.cmd"), "w") as f:
                f.write(failing_bat)
            with open(os.path.join(llvm_bin, "armv7a-linux-androideabi24-clang.cmd"), "w") as f:
                f.write(failing_bat)

            env_vars = {
                "GITHUB_WORKSPACE": ws,
                "ANDROID_NDK_LATEST_HOME": mock_ndk
            }
            result = self.run_ps_script(self.compile_script, env_vars)
            # Must fail
            self.assertNotEqual(result.returncode, 0)
            # Stale files must NOT exist anymore! They must have been removed prior to compilation
            self.assertFalse(os.path.exists(out64), "Stale st-relay-arm64 was NOT purged prior to compilation!")
            self.assertFalse(os.path.exists(out32), "Stale st-relay-armv7 was NOT purged prior to compilation!")

    def test_precompilation_clean_purges_readonly_stale_binaries(self):
        """Verify that stale binaries marked Read-Only on Windows are purged successfully by -Force."""
        with tempfile.TemporaryDirectory() as test_dir:
            ws = os.path.join(test_dir, "workspace")
            os.makedirs(ws)
            relay_dir = os.path.join(ws, "tracker-smoothtrack", "android")
            os.makedirs(relay_dir, exist_ok=True)
            with open(os.path.join(relay_dir, "relay.c"), "w", encoding="utf-8") as f:
                f.write("int main() { return 0; }\n")

            out64 = os.path.join(relay_dir, "st-relay-arm64")
            out32 = os.path.join(relay_dir, "st-relay-armv7")
            with open(out64, "wb") as f:
                f.write(b"STALE_READONLY_ARM64")
            with open(out32, "wb") as f:
                f.write(b"STALE_READONLY_ARMV7")

            # Set READ-ONLY file attribute
            os.chmod(out64, stat.S_IREAD)
            os.chmod(out32, stat.S_IREAD)

            mock_ndk = os.path.join(test_dir, "mock_ndk")
            llvm_bin = os.path.join(mock_ndk, "toolchains", "llvm", "prebuilt", "windows-x86_64", "bin")
            os.makedirs(llvm_bin, exist_ok=True)

            mock_clang_bat = (
                "@echo off\r\n"
                ":loop\r\n"
                "if \"%~1\"==\"\" goto done\r\n"
                "if \"%~1\"==\"-o\" (set OUT=%~2& shift& shift& goto loop)\r\n"
                "shift\r\n"
                "goto loop\r\n"
                ":done\r\n"
                "echo FRESH_NEW_BINARY > \"%OUT%\"\r\n"
                "exit /b 0\r\n"
            )
            with open(os.path.join(llvm_bin, "aarch64-linux-android24-clang.cmd"), "w") as f:
                f.write(mock_clang_bat)
            with open(os.path.join(llvm_bin, "armv7a-linux-androideabi24-clang.cmd"), "w") as f:
                f.write(mock_clang_bat)

            env_vars = {
                "GITHUB_WORKSPACE": ws,
                "ANDROID_NDK_LATEST_HOME": mock_ndk
            }
            result = self.run_ps_script(self.compile_script, env_vars)
            self.assertEqual(result.returncode, 0, f"Clean of read-only files failed:\n{result.stdout}\n{result.stderr}")

            # Verify new files were written with fresh content
            with open(out64, "rb") as f:
                c64 = f.read()
            with open(out32, "rb") as f:
                c32 = f.read()

            self.assertIn(b"FRESH_NEW_BINARY", c64)
            self.assertIn(b"FRESH_NEW_BINARY", c32)

    # =========================================================================
    # 2. PRE-PACKAGING ASSERTION TESTS
    # =========================================================================

    def _setup_valid_staging_tree(self, ws: str, test_dir: str):
        install_dir = os.path.join(ws, "build", "install")
        os.makedirs(os.path.join(install_dir, "modules"), exist_ok=True)
        android_src_dir = os.path.join(ws, "tracker-smoothtrack", "android")
        os.makedirs(android_src_dir, exist_ok=True)
        mock_sdk = os.path.join(test_dir, "mock_sdk", "platform-tools")
        os.makedirs(mock_sdk, exist_ok=True)

        with open(os.path.join(install_dir, "opentrack.exe"), "wb") as f:
            f.write(b"OPENTRACK_EXE_VALID" * 10)
        with open(os.path.join(install_dir, "modules", "opentrack-tracker-smoothtrack.dll"), "wb") as f:
            f.write(b"SMOOTHTRACK_DLL" * 10)
        with open(os.path.join(android_src_dir, "st-relay-arm64"), "wb") as f:
            f.write(b"ARM64_VALID" * 10)
        with open(os.path.join(android_src_dir, "st-relay-armv7"), "wb") as f:
            f.write(b"ARMV7_VALID" * 10)
        with open(os.path.join(mock_sdk, "adb.exe"), "wb") as f:
            f.write(b"ADB_VALID" * 10)
        with open(os.path.join(mock_sdk, "AdbWinApi.dll"), "wb") as f:
            f.write(b"ADB_WIN_API_VALID" * 10)
        with open(os.path.join(mock_sdk, "AdbWinUsbApi.dll"), "wb") as f:
            f.write(b"ADB_WIN_USB_API_VALID" * 10)

        return install_dir, android_src_dir, mock_sdk

    def test_prepackaging_assertion_fails_when_opentrack_exe_missing(self):
        with tempfile.TemporaryDirectory() as test_dir:
            ws = os.path.join(test_dir, "workspace")
            os.makedirs(ws)
            install_dir, _, mock_sdk = self._setup_valid_staging_tree(ws, test_dir)
            os.unlink(os.path.join(install_dir, "opentrack.exe"))

            env_vars = {
                "GITHUB_WORKSPACE": ws,
                "ANDROID_HOME": os.path.join(test_dir, "mock_sdk"),
                "RUNNER_TEMP": test_dir
            }
            result = self.run_ps_script(self.package_script, env_vars)
            self.assertNotEqual(result.returncode, 0)
            combined = result.stdout + result.stderr
            self.assertTrue(
                "Missing required root artifact" in combined or "Pre-packaging verification failed" in combined,
                f"Expected error message not found: {combined}"
            )

    def test_prepackaging_assertion_fails_when_opentrack_exe_is_zero_byte(self):
        with tempfile.TemporaryDirectory() as test_dir:
            ws = os.path.join(test_dir, "workspace")
            os.makedirs(ws)
            install_dir, _, mock_sdk = self._setup_valid_staging_tree(ws, test_dir)
            with open(os.path.join(install_dir, "opentrack.exe"), "wb") as f:
                pass  # truncate to 0 bytes

            env_vars = {
                "GITHUB_WORKSPACE": ws,
                "ANDROID_HOME": os.path.join(test_dir, "mock_sdk"),
                "RUNNER_TEMP": test_dir
            }
            result = self.run_ps_script(self.package_script, env_vars)
            self.assertNotEqual(result.returncode, 0)
            combined = result.stdout + result.stderr
            self.assertTrue(
                "Required artifact is 0-byte" in combined or "Pre-packaging verification failed" in combined,
                f"Expected error message not found: {combined}"
            )

    def test_prepackaging_assertion_fails_when_st_relay_arm64_missing(self):
        with tempfile.TemporaryDirectory() as test_dir:
            ws = os.path.join(test_dir, "workspace")
            os.makedirs(ws)
            install_dir, android_src_dir, mock_sdk = self._setup_valid_staging_tree(ws, test_dir)
            os.unlink(os.path.join(android_src_dir, "st-relay-arm64"))

            env_vars = {
                "GITHUB_WORKSPACE": ws,
                "ANDROID_HOME": os.path.join(test_dir, "mock_sdk"),
                "RUNNER_TEMP": test_dir
            }
            result = self.run_ps_script(self.package_script, env_vars)
            self.assertNotEqual(result.returncode, 0)
            combined = result.stdout + result.stderr
            self.assertTrue(
                "Required relay binary missing from build output" in combined or "Missing required root artifact" in combined,
                f"Expected error message not found: {combined}"
            )

    def test_prepackaging_assertion_fails_when_st_relay_armv7_missing(self):
        with tempfile.TemporaryDirectory() as test_dir:
            ws = os.path.join(test_dir, "workspace")
            os.makedirs(ws)
            install_dir, android_src_dir, mock_sdk = self._setup_valid_staging_tree(ws, test_dir)
            os.unlink(os.path.join(android_src_dir, "st-relay-armv7"))

            env_vars = {
                "GITHUB_WORKSPACE": ws,
                "ANDROID_HOME": os.path.join(test_dir, "mock_sdk"),
                "RUNNER_TEMP": test_dir
            }
            result = self.run_ps_script(self.package_script, env_vars)
            self.assertNotEqual(result.returncode, 0)
            combined = result.stdout + result.stderr
            self.assertTrue(
                "Required relay binary missing from build output" in combined or "Missing required root artifact" in combined,
                f"Expected error message not found: {combined}"
            )

    def test_prepackaging_assertion_fails_when_st_relay_arm64_is_zero_byte(self):
        with tempfile.TemporaryDirectory() as test_dir:
            ws = os.path.join(test_dir, "workspace")
            os.makedirs(ws)
            install_dir, android_src_dir, mock_sdk = self._setup_valid_staging_tree(ws, test_dir)
            with open(os.path.join(android_src_dir, "st-relay-arm64"), "wb") as f:
                pass  # 0 bytes

            env_vars = {
                "GITHUB_WORKSPACE": ws,
                "ANDROID_HOME": os.path.join(test_dir, "mock_sdk"),
                "RUNNER_TEMP": test_dir
            }
            result = self.run_ps_script(self.package_script, env_vars)
            self.assertNotEqual(result.returncode, 0)
            combined = result.stdout + result.stderr
            self.assertTrue(
                "Required artifact is 0-byte" in combined or "Pre-packaging verification failed" in combined,
                f"Expected error message not found: {combined}"
            )

    def test_prepackaging_assertion_fails_when_st_relay_armv7_is_zero_byte(self):
        with tempfile.TemporaryDirectory() as test_dir:
            ws = os.path.join(test_dir, "workspace")
            os.makedirs(ws)
            install_dir, android_src_dir, mock_sdk = self._setup_valid_staging_tree(ws, test_dir)
            with open(os.path.join(android_src_dir, "st-relay-armv7"), "wb") as f:
                pass  # 0 bytes

            env_vars = {
                "GITHUB_WORKSPACE": ws,
                "ANDROID_HOME": os.path.join(test_dir, "mock_sdk"),
                "RUNNER_TEMP": test_dir
            }
            result = self.run_ps_script(self.package_script, env_vars)
            self.assertNotEqual(result.returncode, 0)
            combined = result.stdout + result.stderr
            self.assertTrue(
                "Required artifact is 0-byte" in combined or "Pre-packaging verification failed" in combined,
                f"Expected error message not found: {combined}"
            )

    def test_prepackaging_assertion_fails_when_adb_exe_is_zero_byte(self):
        with tempfile.TemporaryDirectory() as test_dir:
            ws = os.path.join(test_dir, "workspace")
            os.makedirs(ws)
            install_dir, android_src_dir, mock_sdk = self._setup_valid_staging_tree(ws, test_dir)
            with open(os.path.join(mock_sdk, "adb.exe"), "wb") as f:
                pass  # 0 bytes

            env_vars = {
                "GITHUB_WORKSPACE": ws,
                "ANDROID_HOME": os.path.join(test_dir, "mock_sdk"),
                "RUNNER_TEMP": test_dir
            }
            result = self.run_ps_script(self.package_script, env_vars)
            self.assertNotEqual(result.returncode, 0)
            combined = result.stdout + result.stderr
            self.assertTrue(
                "Required artifact is 0-byte" in combined or "Pre-packaging verification failed" in combined,
                f"Expected error message not found: {combined}"
            )

    def test_prepackaging_assertion_fails_when_adb_dll_is_zero_byte(self):
        with tempfile.TemporaryDirectory() as test_dir:
            ws = os.path.join(test_dir, "workspace")
            os.makedirs(ws)
            install_dir, android_src_dir, mock_sdk = self._setup_valid_staging_tree(ws, test_dir)
            with open(os.path.join(mock_sdk, "AdbWinApi.dll"), "wb") as f:
                pass  # 0 bytes

            env_vars = {
                "GITHUB_WORKSPACE": ws,
                "ANDROID_HOME": os.path.join(test_dir, "mock_sdk"),
                "RUNNER_TEMP": test_dir
            }
            result = self.run_ps_script(self.package_script, env_vars)
            self.assertNotEqual(result.returncode, 0)
            combined = result.stdout + result.stderr
            self.assertTrue(
                "Required artifact is 0-byte" in combined or "Pre-packaging verification failed" in combined,
                f"Expected error message not found: {combined}"
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
