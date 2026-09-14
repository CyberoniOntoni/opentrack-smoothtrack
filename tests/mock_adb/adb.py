#!/usr/bin/env python3
"""Mock Android Debug Bridge CLI for SmoothTrack tests.

Controlled by environment variables (see module docstring in tests/test_mock_adb.py).
"""

from __future__ import annotations

import os
import shutil
import sys
import time


def _log_argv(args):
    log_path = os.environ.get("MOCK_ADB_LOG")
    if not log_path:
        return
    parent = os.path.dirname(log_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as fh:
        fh.write(" ".join(args) + "\n")


def _strip_serial(args):
    if len(args) >= 2 and args[0] == "-s":
        return args[2:]
    return args


def _shell_is_relay_launch(shell_args):
    return bool(shell_args) and shell_args[0] == "/data/local/tmp/st-relay"


def _shell_is_kill(shell_args):
    text = " ".join(shell_args)
    for token in ("pkill", "killall", "pidof"):
        if token in text.split() or token in text:
            return True
    parts = (
        text.replace("$(", " ")
        .replace(")", " ")
        .replace("||", " ")
        .replace("|", " ")
        .split()
    )
    return "kill" in parts


def main(argv):
    args = argv[1:]
    _log_argv(args)
    args = _strip_serial(args)
    if not args:
        return 0

    cmd = args[0]

    if cmd == "start-server":
        ms = int(os.environ.get("MOCK_ADB_START_SERVER_MS", "0") or "0")
        if ms > 0:
            time.sleep(ms / 1000.0)
        return 0

    if cmd == "devices":
        devices = os.environ.get(
            "MOCK_ADB_DEVICES",
            "emulator-5554 device product:sdk model:sdk",
        )
        sys.stdout.write(devices)
        if devices and not devices.endswith("\n"):
            sys.stdout.write("\n")
        return 0

    if cmd == "reverse":
        return 0

    if cmd == "push":
        if len(args) >= 3:
            src, dest = args[1], args[2]
            fs_root = os.environ.get("MOCK_ADB_FS")
            if fs_root and dest.rstrip("/").endswith("st-relay"):
                os.makedirs(fs_root, exist_ok=True)
                shutil.copy(src, os.path.join(fs_root, "st-relay"))
        return 0

    if cmd == "shell":
        shell_args = args[1:]
        if shell_args[:2] == ["getprop", "ro.product.cpu.abi"]:
            sys.stdout.write("arm64-v8a\n")
            return 0
        if shell_args and shell_args[0] == "chmod":
            return 0
        if _shell_is_relay_launch(shell_args):
            if os.environ.get("MOCK_ADB_RELAY_EXIT") == "1":
                sys.stderr.write("Failed to connect to TCP reverse tunnel\n")
                return 5
            try:
                while True:
                    time.sleep(3600)
            except KeyboardInterrupt:
                return 0
            return 0
        if _shell_is_kill(shell_args):
            return 0
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
