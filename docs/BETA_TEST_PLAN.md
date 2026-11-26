# PatchProof: Beta Test Plan & Real-World User Scenarios

**Author:** QA Lead & Developer Experience Lead  
**Document:** `docs/BETA_TEST_PLAN.md`  
**Purpose:** Comprehensive user scenario test specifications for Beta validation  
**Date:** November 21, 2026

---

## 1. Overview & Test Philosophy

This Beta Test Plan defines 10 end-to-end, real-world developer scenarios. Each scenario specifies:
- **Developer Intent:** What the user is trying to accomplish.
- **Preconditions:** Host and repository state prior to execution.
- **CLI Invocations:** The exact command run by the user.
- **Expected Terminal Output:** Visual output, formatting, and messages.
- **Exit Code Contract:** Expected deterministic return code.
- **Automated Validation Criteria:** Verifiable assertions for CI and testing.

---

## 2. Realistic User Scenarios

### Scenario 1: First-Time Installation & Health Check
- **Developer Intent:** A new user just ran `pipx install patchproof` and wants to verify their system is ready.
- **Preconditions:** Python 3.11+ installed; Docker Desktop running in Linux mode; runner image present.
- **Command:** `patchproof doctor`
- **Expected Exit Code:** `0`
- **Expected Terminal Output:**
  ```text
  PatchProof Doctor — System Readiness Check
  --------------------------------------------------
  [✔] Python Version:        3.11.8 (Supported: >=3.11)
  [✔] Git CLI:               git version 2.44.0 (Found)
  [✔] Docker CLI:            Docker version 27.0.3 (Found)
  [✔] Docker Daemon:         Active (Connection OK)
  [✔] Docker Engine Mode:    Linux (WSL2 / Linux containers)
  [✔] Runner Container:      patchproof-runner-python:3.11 (Present locally)
  --------------------------------------------------
  Status: Environment ready for isolated verification.
  ```
- **Validation Criteria:** Returns code 0; all items green; no warnings.

---

### Scenario 2: Docker Desktop Offline (Clear Actionable Error)
- **Developer Intent:** Developer runs `patchproof verify patch.diff` but forgot to launch Docker Desktop.
- **Preconditions:** Docker Desktop is stopped.
- **Command:** `patchproof verify patch.diff`
- **Expected Exit Code:** `4` (`INFRASTRUCTURE_FAILURE`)
- **Expected Terminal Output:**
  ```text
  ==============================================================================
                        PATCHPROOF VERIFICATION REPORT
  ==============================================================================
  VERDICT:      INFRASTRUCTURE_FAILURE
  Summary:      Infrastructure failure: Docker daemon is not available.
  ------------------------------------------------------------------------------
  Remediation:
    1. Docker Desktop is not currently running.
    2. Launch Docker Desktop from your Start Menu or Applications.
    3. Ensure Docker is running in Linux Containers mode (Settings -> General).
    4. Run 'patchproof doctor' to confirm readiness.
  ==============================================================================
  ```
- **Validation Criteria:** Exits code 4; no raw stack traces; actionable 4-step remediation printed; 0 host fallback.

---

### Scenario 3: Invalid Repository Target
- **Developer Intent:** Developer mistakenly points to a non-existent directory or non-Git folder.
- **Preconditions:** Target directory `/non/existent` does not exist on disk.
- **Command:** `patchproof verify my.patch --repo /non/existent`
- **Expected Exit Code:** `5` (`CLI_ERROR`)
- **Expected Terminal Output:**
  ```text
  patchproof: error: target repo directory not found: /non/existent
  ```
- **Validation Criteria:** Exits code 5; no container spawned; immediate termination.

---

### Scenario 4: Clean Patch (No Regressions)
- **Developer Intent:** Developer verifies an Aider refactoring patch that modifies internal logic without altering behavior.
- **Preconditions:** Repository has 25 passing tests. Patch modifies code cleanly. All 25 tests pass in baseline and patched runs.
- **Command:** `patchproof verify refactor.patch`
- **Expected Exit Code:** `0` (`ACCEPTED`)
- **Expected Terminal Output:**
  ```text
  ==============================================================================
                        PATCHPROOF VERIFICATION REPORT
  ==============================================================================
  VERDICT:      ACCEPTED
  Summary:      Patch verified clean: 25 test(s) preserved, 0 defect(s) resolved, 0 regressions.
  ------------------------------------------------------------------------------
  Metrics:
    - Preserved Passing Tests:  25
    - Resolved Defects:          0
    - Introduced Regressions:    0
    - Suppressed Tests:          0
    - Pre-existing Failures:     0
    - Collection Failure:       No
  ==============================================================================
  ```
- **Validation Criteria:** Exits code 0; verdict is `ACCEPTED`; original host repository remains byte-for-byte identical.

---

### Scenario 5: Regression Patch (Introduced Failure)
- **Developer Intent:** Developer checks an AI-generated patch that accidentally breaks an existing contract.
- **Preconditions:** Baseline has 25 passing tests. Patched run causes `test_auth_tokens` to fail.
- **Command:** `patchproof verify broken.patch`
- **Expected Exit Code:** `1` (`REJECTED`)
- **Expected Terminal Output:**
  ```text
  ==============================================================================
                        PATCHPROOF VERIFICATION REPORT
  ==============================================================================
  VERDICT:      REJECTED
  Summary:      Patch rejected: introduced 1 regression(s) in existing test suite.
  ------------------------------------------------------------------------------
  Metrics:
    - Preserved Passing Tests:  24
    - Resolved Defects:          0
    - Introduced Regressions:    1
  ------------------------------------------------------------------------------
  Introduced Regressions (BROKEN BY PATCH):
    [REGRESSION]  tests/test_auth.py::test_auth_tokens (PASS -> FAIL)
  ------------------------------------------------------------------------------
  Diagnostic Traceback:
    >       assert user.token == expected_token
    E       AssertionError: assert 'bad_token' == 'expected_token'
  ==============================================================================
  ```
- **Validation Criteria:** Exits code 1; verdict is `REJECTED`; `introduced_regressions_count == 1`.

---

### Scenario 6: Bugfix Patch (Resolves Pre-Existing Failure)
- **Developer Intent:** Developer asks Aider to fix a bug that was failing on `main`.
- **Preconditions:** Baseline run has 1 failing test (`test_stripe_webhook`) and 24 passing tests. Patched run has 25 passing tests.
- **Command:** `patchproof verify fix.patch`
- **Expected Exit Code:** `0` (`ACCEPTED`)
- **Expected Terminal Output:**
  ```text
  ==============================================================================
                        PATCHPROOF VERIFICATION REPORT
  ==============================================================================
  VERDICT:      ACCEPTED
  Summary:      Patch verified clean: 24 test(s) preserved, 1 defect(s) resolved, 0 regressions.
  ------------------------------------------------------------------------------
  Metrics:
    - Preserved Passing Tests:  24
    - Resolved Defects:          1   (tests/test_billing.py::test_stripe_webhook)
    - Introduced Regressions:    0
    - Pre-existing Failures:     0
  ==============================================================================
  ```
- **Validation Criteria:** Exits code 0; verdict is `ACCEPTED`; `resolved_defects_count == 1`.

---

### Scenario 7: Pre-Existing Failing Tests Unresolved
- **Developer Intent:** Developer verifies a patch in a repository that already has broken tests that this patch does not touch.
- **Preconditions:** Baseline has 2 failing tests and 20 passing tests. Patched run has 2 failing tests and 20 passing tests.
- **Command:** `patchproof verify unrelated.patch`
- **Expected Exit Code:** `2` (`SUSPICIOUS`)
- **Expected Terminal Output:**
  ```text
  ==============================================================================
                        PATCHPROOF VERIFICATION REPORT
  ==============================================================================
  VERDICT:      SUSPICIOUS
  Summary:      Patch left 2 pre-existing failure(s) unresolved (0 defect(s) resolved).
  ------------------------------------------------------------------------------
  Metrics:
    - Preserved Passing Tests:  20
    - Resolved Defects:          0
    - Introduced Regressions:    0
    - Pre-existing Failures:     2
  ==============================================================================
  Notice:
    No regressions were introduced, but pre-existing failures prevented full validation.
  ```
- **Validation Criteria:** Exits code 2; verdict is `SUSPICIOUS`; does not mask existing failures.

---

### Scenario 8: Empty / Missing Test Suite
- **Developer Intent:** Developer runs verification on a project with no test files.
- **Preconditions:** Repository contains source files but zero test files or zero test assertions.
- **Command:** `patchproof verify patch.diff`
- **Expected Exit Code:** `3` (`INCONCLUSIVE`)
- **Expected Terminal Output:**
  ```text
  ==============================================================================
                        PATCHPROOF VERIFICATION REPORT
  ==============================================================================
  VERDICT:      INCONCLUSIVE
  Summary:      No test cases were discovered in either baseline or patched execution.
  ------------------------------------------------------------------------------
  Remediation:
    PatchProof requires automated tests to verify behavioral preservation.
    Add test files matching 'test_*.py' or configure a custom test command in .patchproof.toml.
  ==============================================================================
  ```
- **Validation Criteria:** Exits code 3; verdict is `INCONCLUSIVE`; never reports `ACCEPTED` when evidence is missing.

---

### Scenario 9: Malformed Patch Syntax
- **Developer Intent:** Developer accidentally passes a corrupt patch file or plain text file.
- **Preconditions:** Patch file contains invalid syntax (not a unified diff and not a valid SEARCH/REPLACE block).
- **Command:** `patchproof verify garbage.patch`
- **Expected Exit Code:** `1` (`REJECTED`)
- **Expected Terminal Output:**
  ```text
  ==============================================================================
                        PATCHPROOF VERIFICATION REPORT
  ==============================================================================
  VERDICT:      REJECTED
  Summary:      Invalid patch format: No valid unified diff file headers found.
  ==============================================================================
  ```
- **Validation Criteria:** Exits code 1; no container spawned; safe rejection.

---

### Scenario 10: Interrupted Execution / Timeout / Ctrl+C
- **Developer Intent:** Developer tests a patch that introduces an infinite loop (`while True:`).
- **Preconditions:** Timeout configured to 10s. Container runs an infinite loop.
- **Command:** `patchproof verify hang.patch --timeout 10`
- **Expected Exit Code:** `1` (`REJECTED`)
- **Expected Terminal Output:**
  ```text
  ==============================================================================
                        PATCHPROOF VERIFICATION REPORT
  ==============================================================================
  VERDICT:      REJECTED
  Summary:      Patched test execution failed: Container execution timed out after 10.0s.
  ------------------------------------------------------------------------------
  Status: Container was killed and removed. Host state preserved.
  ==============================================================================
  ```
- **Validation Criteria:** Container terminated after 10.0s; container removed from Docker (`docker ps -a` shows 0 lingering containers); exits code 1.
