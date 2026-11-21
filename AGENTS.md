# PatchProof Engineering Rules and Quality Standards

This document defines the permanent, binding engineering principles and quality gates for the **PatchProof** repository. All contributing agents and human engineers must adhere strictly to these rules.

---

## 1. Architecture & Verification First
- **Architecture before code:** No substantive implementation may begin without an agreed specification, interface definition, and threat model.
- **Evidence before assertions:** Never claim that code compiles, tests pass, or diffs are clean without executing the relevant verification commands and presenting exact outputs.
- **Small, reviewable increments:** Avoid monolithic multi-file changes. Prefer small, reviewable commits that fulfill a single verifiable purpose.

## 2. Test-Driven Development & Quality Gates
- **TDD Requirement:** For all non-trivial logic, write failing unit tests before or alongside implementation. Every bug fix must include a regression test reproducing the issue.
- **Ruff Quality Gate:** All code must pass `ruff check .` with 0 errors and 0 warnings.
- **Strict Typing Gate:** All Python code must pass `mypy --strict src/patchproof tests/` without untyped definitions, `type: ignore` abuses, or implicit `Any`.
- **Pytest Suite:** All test suites must pass `pytest -q` with 0 failures and 0 errors. Never represent unexecuted tests as passing.

## 3. Security & Sandboxing
- **Worktree vs. Sandbox Disambiguation:** A Git worktree is strictly a workspace file isolation mechanism. It is **NOT** a security sandbox. Code executed in a worktree runs on the host OS with the developer's full user privileges.
- **Execution Boundary:** Untrusted code execution, arbitrary test runs, and third-party dependencies must run in isolated Docker containers with `--network none`, CPU/memory caps, unprivileged UIDs, and read-only host mounts.
- **Credential Hygiene:** Host environment variables, cloud credentials (`~/.aws`, `~/.config`), and SSH keys must never be mounted into test containers or logged in transcripts.

## 4. Git Safety & Human Approval
- **Workspace-Limited Scope:** Operations must remain confined to this repository. Upstream Aider source code in temporary directories or other user repositories must never be modified.
- **Explicit Human Approval:** The agent must never auto-commit, push, or execute destructive Git commands (`git reset --hard`, `git clean -fd`, `git checkout .`) without explicit user review and authorization.
- **Pre-Execution Check:** Always inspect `git status` before beginning modifications.

## 5. Agent Coordination & Concurrency
- **Single Author per File:** Never allow multiple agents to modify the same file concurrently.
- **Independent Roles:** Code authoring and code review must be handled by separate agent roles. The reviewer must adopt an adversarial posture and actively search for edge cases and regressions.
