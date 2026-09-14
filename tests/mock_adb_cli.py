"""Invoke tests/mock_adb wrappers as a subprocess."""

import os
import subprocess
import sys

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
MOCK_DIR = os.path.join(REPO_ROOT, "tests", "mock_adb")
ADB = os.path.join(MOCK_DIR, "adb.bat" if sys.platform == "win32" else "adb")


def mock_adb_env(extra=None):
    merged = os.environ.copy()
    py_dir = os.path.dirname(sys.executable)
    merged["PATH"] = py_dir + os.pathsep + merged.get("PATH", "")
    merged["PYTHON"] = sys.executable
    if extra:
        merged.update(extra)
    return merged


def run_mock_adb(args, env, timeout=5):
    return subprocess.run(
        [ADB] + list(args),
        capture_output=True,
        text=True,
        env=mock_adb_env(env),
        timeout=timeout,
    )
