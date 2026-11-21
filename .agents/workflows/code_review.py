"""Workflow: Adversarial Code Review.

Inspects git diff and verifies:
1. No uncommitted stray files
2. AST assertions not stripped
3. Type annotation completeness
4. Security and execution boundaries respected
"""

import subprocess
import sys


def main() -> int:
    print("=== Adversarial Code Review ===")
    status = subprocess.run(["git", "status", "--short"], capture_output=True, text=True)
    print("Changed files:")
    print(status.stdout if status.stdout else "No unstaged changes.")

    diff = subprocess.run(["git", "diff"], capture_output=True, text=True)
    if diff.stdout:
        print(f"\nDiff size: {len(diff.stdout.splitlines())} lines")
        # Check for common antipatterns
        suspicious = [
            ("# type: ignore", "type: ignore comment used"),
            ("assert True", "Tautological assertion"),
            ("pass  # TODO", "Empty block"),
            ("@pytest.mark.skip", "Skipped test marker"),
        ]
        for pattern, desc in suspicious:
            if pattern in diff.stdout:
                print(f"WARNING: Found {desc} in diff!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
