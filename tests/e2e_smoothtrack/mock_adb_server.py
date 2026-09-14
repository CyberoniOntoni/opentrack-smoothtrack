"""Mock ADB CLI Environment and Subprocess Emulator.

Provides a fully scriptable, executable Mock ADB CLI that mimics `adb.exe`
on Windows and POSIX systems. Used to test `adb_client` discovery, device checking,
reverse tunnel configuration, ABI querying, deployment, and cleanup.
"""

import json
import os
import sys
from typing import Dict, List, Optional


class MockAdbState:
    """Manages the configurable state and execution log of the Mock ADB process."""

    def __init__(self, state_file: str):
        self.state_file = state_file
        self.load()

    def load(self) -> None:
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.devices = data.get("devices", [
                        {"serial": "emulator-5554", "status": "device", "model": "Pixel_7", "abi": "arm64-v8a"}
                    ])
                    self.reverse_ports = data.get("reverse_ports", [])
                    self.pushed_files = data.get("pushed_files", [])
                    self.commands_log = data.get("commands_log", [])
                    self.fail_reverse = data.get("fail_reverse", False)
                    self.fail_push = data.get("fail_push", False)
                    self.relay_running = data.get("relay_running", False)
                    self.hang_commands = data.get("hang_commands", [])
                    self.hang_duration = data.get("hang_duration", 0.0)
                    return
            except Exception:
                pass
        # Default state
        self.devices = [
            {"serial": "emulator-5554", "status": "device", "model": "Pixel_7", "abi": "arm64-v8a"}
        ]
        self.reverse_ports = []
        self.pushed_files = []
        self.commands_log = []
        self.fail_reverse = False
        self.fail_push = False
        self.relay_running = False
        self.hang_commands = []
        self.hang_duration = 0.0

    def save(self) -> None:
        data = {
            "devices": self.devices,
            "reverse_ports": self.reverse_ports,
            "pushed_files": self.pushed_files,
            "commands_log": self.commands_log,
            "fail_reverse": self.fail_reverse,
            "fail_push": self.fail_push,
            "relay_running": self.relay_running,
            "hang_commands": self.hang_commands,
            "hang_duration": self.hang_duration,
        }
        with open(self.state_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def log_call(self, args: List[str]) -> None:
        self.commands_log.append(args)
        self.save()


def run_mock_cli(argv: List[str], state_file: str) -> int:
    """Dispatches mock ADB commands matching official Android Debug Bridge CLI behavior."""
    state = MockAdbState(state_file)
    state.log_call(argv[1:])

    args = argv[1:]
    if any(cmd in args for cmd in getattr(state, "hang_commands", [])):
        import time
        time.sleep(getattr(state, "hang_duration", 60.0))

    if not args:
        print("Android Debug Bridge version 1.0.41", file=sys.stderr)
        return 0

    serial_filter: Optional[str] = None
    if len(args) >= 2 and args[0] == "-s":
        serial_filter = args[1]
        args = args[2:]

    if not args:
        return 0

    cmd = args[0]

    if cmd == "devices":
        long_format = "-l" in args
        print("List of devices attached")
        for dev in state.devices:
            if long_format:
                print(f"{dev['serial']:<22} {dev['status']} usb:1-1 product:device model:{dev.get('model', 'device')} device:generic")
            else:
                print(f"{dev['serial']}\t{dev['status']}")
        return 0

    elif cmd == "reverse":
        if "--remove" in args:
            # reverse --remove tcp:4242
            idx = args.index("--remove")
            if idx + 1 < len(args):
                spec = args[idx + 1]
                state.reverse_ports = [p for p in state.reverse_ports if p != spec]
                state.save()
            return 0
        elif "--list" in args:
            for p in state.reverse_ports:
                print(f"(reverse) {p} {p}")
            return 0
        else:
            # adb reverse tcp:4242 tcp:4242
            if state.fail_reverse:
                print("error: cannot bind to socket: Address already in use", file=sys.stderr)
                return 1
            if len(args) >= 3:
                remote_spec = args[1]
                local_spec = args[2]
                state.reverse_ports.append(remote_spec)
                state.save()
                print(remote_spec)
                return 0
            return 1

    elif cmd == "push":
        # adb push <src> <dest>
        if state.fail_push:
            print("error: failed to copy file: permission denied", file=sys.stderr)
            return 1
        if len(args) >= 3:
            src, dest = args[1], args[2]
            state.pushed_files.append({"src": src, "dest": dest})
            state.save()
            print(f"{src}: 1 file pushed, 0 skipped. 10.0 MB/s")
            return 0
        return 1

    elif cmd == "shell":
        shell_args = args[1:]
        if not shell_args:
            return 0

        # ABI query: getprop ro.product.cpu.abi
        if shell_args == ["getprop", "ro.product.cpu.abi"]:
            dev = None
            if serial_filter:
                for d in state.devices:
                    if d["serial"] == serial_filter:
                        dev = d
                        break
            if not dev and state.devices:
                dev = state.devices[0]
            abi = dev.get("abi", "arm64-v8a") if dev else "arm64-v8a"
            print(abi)
            return 0

        # test -x /data/local/tmp/st-relay
        if len(shell_args) >= 3 and shell_args[0] == "test" and shell_args[1] == "-x":
            target = shell_args[2]
            has_pushed = any(p["dest"] == target for p in state.pushed_files)
            return 0 if has_pushed else 1

        # chmod 755 /data/local/tmp/st-relay
        if len(shell_args) >= 2 and shell_args[0] == "chmod":
            return 0

        # pkill -f st-relay
        if "pkill" in shell_args:
            state.relay_running = False
            state.save()
            return 0

        # Launch relay: /data/local/tmp/st-relay <udp> <tcp>
        if shell_args and "/data/local/tmp/st-relay" in shell_args[0]:
            state.relay_running = True
            state.save()
            print("st-relay: ready, bridging UDP -> TCP", file=sys.stderr)
            return 0

        return 0

    return 0


def create_mock_adb_executable(target_dir: str, state_file: str) -> str:
    """Creates an executable wrapper script (adb.cmd on Windows, adb on POSIX) in target_dir."""
    os.makedirs(target_dir, exist_ok=True)
    python_exe = sys.executable

    script_path = os.path.abspath(__file__)

    if sys.platform == "win32":
        wrapper_path = os.path.join(target_dir, "adb.cmd")
        content = f'@echo off\n"{python_exe}" "{script_path}" --state "{state_file}" %*\n'
    else:
        wrapper_path = os.path.join(target_dir, "adb")
        content = f'#!/bin/sh\nexec "{python_exe}" "{script_path}" --state "{state_file}" "$@"\n'

    with open(wrapper_path, "w", encoding="utf-8") as f:
        f.write(content)

    if sys.platform != "win32":
        os.chmod(wrapper_path, 0o755)

    return wrapper_path


if __name__ == "__main__":
    state_idx = -1
    for i, a in enumerate(sys.argv):
        if a == "--state" and i + 1 < len(sys.argv):
            state_idx = i
            break

    if state_idx >= 0:
        state_path = sys.argv[state_idx + 1]
        passed_argv = [sys.argv[0]] + sys.argv[1:state_idx] + sys.argv[state_idx + 2:]
        sys.exit(run_mock_cli(passed_argv, state_path))
    else:
        # Fallback state file
        default_state = os.path.join(os.path.dirname(__file__), "mock_adb_state.json")
        sys.exit(run_mock_cli(sys.argv, default_state))
