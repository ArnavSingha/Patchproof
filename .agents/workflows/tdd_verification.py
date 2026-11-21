"""Workflow: TDD and Verification Pipeline.

Runs the static and dynamic quality gate across PatchProof:
1. ruff check .
2. mypy --strict src/patchproof tests/
3. pytest -q
"""

import subprocess
import sys


def run_check(label: str, cmd: list[str]) -> bool:
    print(f"=== Running {label} ===")
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.stdout:
        print(res.stdout.strip())
    if res.stderr:
        print(res.stderr.strip(), file=sys.stderr)
    print(f"Exit code: {res.returncode}\n")
    return res.returncode == 0


def main() -> int:
    checks = [
        ("Ruff Linter", ["ruff", "check", "."]),
        ("Mypy Strict Typing", ["mypy", "--strict", "src/patchproof", "tests/"]),
        ("Pytest Test Suite", ["pytest", "-q"]),
    ]
    all_passed = True
    for label, cmd in checks:
        if not run_check(label, cmd):
            all_passed = False
            print(f"FAILED: {label}")
            break

    if all_passed:
        print("ALL QUALITY GATES PASSED")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
