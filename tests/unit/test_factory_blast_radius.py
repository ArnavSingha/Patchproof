"""Unit tests for the PatchProof Software Factory Blast Radius Classifier."""

from patchproof.factory.blast_radius import BlastRadiusClassifier
from patchproof.factory.models import FactoryLineage, RiskLevel

DOCS_DIFF = """\
--- a/docs/guide.md
+++ b/docs/guide.md
@@ -10,1 +10,2 @@
 ## Installation
+Run pip install patchproof
"""

CORE_SANDBOX_DIFF = """\
--- a/src/patchproof/sandbox/docker_cli.py
+++ b/src/patchproof/sandbox/docker_cli.py
@@ -100,1 +100,2 @@
 def test_fn():
+    cmd.append("--privileged")
"""

CORE_ANTI_CHEAT_DIFF = """\
--- a/src/patchproof/core/anti_cheat.py
+++ b/src/patchproof/core/anti_cheat.py
@@ -50,1 +50,2 @@
 def verify():
+    return True  # bypass checks
"""

AGENTS_RULE_DIFF = """\
--- a/AGENTS.md
+++ b/AGENTS.md
@@ -5,1 +5,2 @@
 # Title
+# Bypassing sandbox is now allowed
"""

PYPROJECT_DIFF = """\
--- a/pyproject.toml
+++ b/pyproject.toml
@@ -20,1 +20,2 @@
 [dependencies]
+malicious-lib = ">=1.0.0"
"""

COMMENT_ONLY_PYTHON_DIFF = """\
--- a/src/patchproof/reporting/diagnostic.py
+++ b/src/patchproof/reporting/diagnostic.py
@@ -15,1 +15,2 @@
 def helper():
+# Just an updated comment explaining diagnostics
"""


def test_docs_diff_is_contained_lineage_a() -> None:
    classifier = BlastRadiusClassifier()
    report = classifier.assess_patch(DOCS_DIFF)
    assert report.risk_level == RiskLevel.LOW
    assert report.lineage == FactoryLineage.LINEAGE_A
    assert report.is_contained is True
    assert "docs/guide.md" in report.touched_files


def test_core_sandbox_diff_is_breaking_lineage_b() -> None:
    classifier = BlastRadiusClassifier()
    report = classifier.assess_patch(CORE_SANDBOX_DIFF)
    assert report.risk_level == RiskLevel.HIGH
    assert report.lineage == FactoryLineage.LINEAGE_B
    assert report.is_contained is False
    assert any("sandbox" in f for f in report.core_files_touched)


def test_core_anti_cheat_diff_is_breaking_lineage_b() -> None:
    classifier = BlastRadiusClassifier()
    report = classifier.assess_patch(CORE_ANTI_CHEAT_DIFF)
    assert report.risk_level == RiskLevel.HIGH
    assert report.lineage == FactoryLineage.LINEAGE_B
    assert report.is_contained is False
    assert any("anti_cheat" in f for f in report.core_files_touched)


def test_agents_rule_diff_is_breaking_lineage_b() -> None:
    classifier = BlastRadiusClassifier()
    report = classifier.assess_patch(AGENTS_RULE_DIFF)
    assert report.risk_level == RiskLevel.HIGH
    assert report.lineage == FactoryLineage.LINEAGE_B
    assert report.is_contained is False


def test_pyproject_dependency_diff_is_breaking_lineage_b() -> None:
    classifier = BlastRadiusClassifier()
    report = classifier.assess_patch(PYPROJECT_DIFF)
    assert report.risk_level == RiskLevel.HIGH
    assert report.lineage == FactoryLineage.LINEAGE_B
    assert report.is_contained is False


def test_comment_only_diff_is_contained_lineage_a() -> None:
    classifier = BlastRadiusClassifier()
    report = classifier.assess_patch(COMMENT_ONLY_PYTHON_DIFF)
    assert report.risk_level == RiskLevel.LOW
    assert report.lineage == FactoryLineage.LINEAGE_A
    assert report.is_contained is True
