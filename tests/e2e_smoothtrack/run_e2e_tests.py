#!/usr/bin/env python3
"""Standalone runner for remaining SmoothTrack e2e tests."""

import os
import sys
import time
import unittest

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, "..", ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


def build_suite() -> unittest.TestSuite:
    return unittest.TestLoader().discover(SCRIPT_DIR, pattern="test_*.py")


def main():
    print("=" * 78)
    print("       SMOOTHTRACK DUAL-PLATFORM USB TRACKER E2E TEST SUITE")
    print("=" * 78)
    print(f"Python: {sys.version.split()[0]} ({sys.platform})")
    print(f"Working Directory: {os.getcwd()}")
    print("-" * 78)

    t0 = time.time()
    suite = build_suite()
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    duration = time.time() - t0

    status = "PASS" if result.wasSuccessful() else "FAIL"
    print("\n" + "=" * 78)
    print(f"  E2E result               : {status}")
    print(f"  Total Test Cases Executed : {result.testsRun}")
    print(f"  Failures                  : {len(result.failures)}")
    print(f"  Errors                    : {len(result.errors)}")
    print(f"  Skipped                   : {len(result.skipped)}")
    print(f"  Total Duration            : {duration:.3f} seconds")
    print("=" * 78)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
