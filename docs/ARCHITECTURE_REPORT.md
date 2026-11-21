# Aider + PatchProof: Architectural Investigation & Engineering Blueprint

> **Role & Authority:** Principal Software Architect, Staff Engineer, AI Engineer, Security Engineer, Technical Lead  
> **Repository Foundation:** [Aider Upstream](https://github.com/Aider-AI/aider) (`aider-chat` v0.80+)  
> **Inspection Status:** Full Source Inspection Completed (Verified against upstream code in read-only analysis)  
> **Target Subsystem:** PatchProof (Original Deterministic Verification & Reliability Engine)  
> **Date:** November 2026  

---

## Executive Summary

Aider is a leading terminal-based AI pair-programming tool characterized by fast feedback loops, sophisticated repository mapping (tree-sitter + PageRank), multiple code modification formats (Search/Replace blocks, unified diffs), and Git-centric workflows. However, our deep source-code audit reveals a fundamental engineering gap: **Aider equates a shell exit status of zero (`exit_code == 0`) with patch correctness**.

Aider lacks:
1. Deterministic patch normalization and pre-flight semantic validation.
2. Sandbox isolation (test commands run directly on the host with full user privileges).
3. Regression detection (distinguishing newly broken tests from pre-existing failures).
4. Blast-radius and test discovery mechanisms.
5. Structured evidence collection (no test counts, execution traces, or tamper detection).

**PatchProof** is designed to address this gap as an independent, deterministic verification engine. It sits between AI code generation and Git persistence, ensuring that patches are evaluated through isolated execution, regression testing, AST blast-radius checks, and structured evidence reports.
