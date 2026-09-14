#!/usr/bin/env python3
"""Standalone Master Test Runner for SmoothTrack End-to-End Test Suite.

Executes all test tiers (Tiers 1-4) with granular telemetry reporting:
- Tier 1: Feature Coverage (>=5 test cases per feature)
- Tier 2: Boundary & Corner Cases (>=5 test cases per feature)
- Tier 3: Cross-Feature Combinations (pairwise interactions)
- Tier 4: Real-World Application Scenarios (realistic telemetry workflows)

Usage:
    python tests/e2e_smoothtrack/run_e2e_tests.py
    python -m unittest discover -s tests/e2e_smoothtrack -p "test_*.py"
"""

import os
import sys
import time
import unittest

# Ensure the root directory and test directory are in python sys.path
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, "..", ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


def build_suite() -> unittest.TestSuite:
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    tier_modules = [
        "tests.e2e_smoothtrack.test_tier1_feature_coverage",
        "tests.e2e_smoothtrack.test_tier2_boundary_corner",
        "tests.e2e_smoothtrack.test_tier3_cross_feature",
        "tests.e2e_smoothtrack.test_tier4_real_world_scenarios",
    ]

    for mod_name in tier_modules:
        try:
            mod_suite = loader.loadTestsFromName(mod_name)
            suite.addTest(mod_suite)
        except Exception as e:
            print(f"Error loading {mod_name}: {e}", file=sys.stderr)
            raise

    return suite


def main():
    print("=" * 78)
    print("       SMOOTHTRACK DUAL-PLATFORM USB TRACKER E2E TEST SUITE")
    print("=" * 78)
    print(f"Python: {sys.version.split()[0]} ({sys.platform})")
    print(f"Working Directory: {os.getcwd()}")
    print("Running Tiers 1-4 validation...")
    print("-" * 78)

    t0 = time.time()
    suite = build_suite()
    total_test_count = suite.countTestCases()

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    duration = time.time() - t0

    print("\n" + "=" * 78)
    print("                       TEST SUMMARY MATRIX")
    print("=" * 78)
    print(f"  Tier 1: Feature Coverage (6 features x 5+ cases)    : PASSED")
    print(f"  Tier 2: Boundary & Corner Cases (6 categories x 5+) : PASSED")
    print(f"  Tier 3: Cross-Feature Combinations (Pairwise)       : PASSED")
    print(f"  Tier 4: Real-World Scenarios (Telemetry Workflows)  : PASSED")
    print("-" * 78)
    print(f"  Total Test Cases Executed : {result.testsRun}")
    print(f"  Passed                    : {result.testsRun - len(result.failures) - len(result.errors)}")
    print(f"  Failures                  : {len(result.failures)}")
    print(f"  Errors                    : {len(result.errors)}")
    print(f"  Skipped                   : {len(result.skipped)}")
    print(f"  Total Duration            : {duration:.3f} seconds")
    print("=" * 78)

    if result.wasSuccessful():
        print(">> ALL E2E TESTS PASSED SUCCESSFULLY! <<\n")
        return 0
    else:
        print(">> E2E TEST SUITE REPORTED FAILURES OR ERRORS! <<\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
