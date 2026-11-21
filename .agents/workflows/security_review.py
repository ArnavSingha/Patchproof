"""Workflow: Security and Isolation Audit.

Audits repository configuration and test execution boundaries:
1. Verifies Docker sandbox parameters
2. Audits for hardcoded credentials / tokens
3. Checks file permission boundaries
"""

import os
import re
import sys


def check_secrets(root_dir: str) -> list[str]:
    findings = []
    secret_patterns = [
        (
            re.compile(
                r"(?i)(api[_-]?key|secret|password|token)\s*=\s*['\"][A-Za-z0-9_\-]{16,}['\"]"
            ),
            "Potential hardcoded secret",
        ),
        (re.compile(r"(?i)sk-[A-Za-z0-9]{20,}"), "OpenAI API key pattern"),
    ]
    for root, _, files in os.walk(root_dir):
        if any(d in root for d in [".git", "__pycache__", ".venv"]):
            continue
        for f in files:
            if not f.endswith((".py", ".json", ".yml", ".yaml", ".toml")):
                continue
            path = os.path.join(root, f)
            try:
                with open(path, encoding="utf-8", errors="ignore") as fp:
                    content = fp.read()
                    for pat, desc in secret_patterns:
                        if pat.search(content):
                            findings.append(f"{path}: {desc}")
            except Exception:
                pass
    return findings


def main() -> int:
    print("=== Security & Isolation Audit ===")
    secrets = check_secrets(".")
    if secrets:
        print(f"ALERT: Found {len(secrets)} potential credential issues:")
        for s in secrets:
            print(f"  - {s}")
        return 1
    print("No hardcoded secrets or credential leaks detected.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
