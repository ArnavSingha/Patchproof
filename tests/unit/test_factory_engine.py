"""Unit tests for PatchProof AI Software Factory Hybrid Loop Engine."""

from patchproof.factory.engine import FactoryEngine, GateResult
from patchproof.factory.models import FactoryLineage, HybridLoopState

LOW_RISK_DIFF = """\
--- a/docs/faq.md
+++ b/docs/faq.md
@@ -1,1 +1,2 @@
 # FAQ
+New FAQ item.
"""

HIGH_RISK_DIFF = """\
--- a/src/patchproof/core/anti_cheat.py
+++ b/src/patchproof/core/anti_cheat.py
@@ -10,1 +10,2 @@
 def audit():
+    pass
"""


def test_lineage_a_autonomous_ship() -> None:
    engine = FactoryEngine()

    def mock_generator(feedback: str | None) -> str:
        return LOW_RISK_DIFF

    def mock_gate(patch: str) -> GateResult:
        return GateResult(passed=True, diagnostics="")

    result = engine.run(
        run_id="run-1",
        generator_fn=mock_generator,
        gate_fn=mock_gate,
    )

    assert result.lineage == FactoryLineage.LINEAGE_A
    assert result.status == HybridLoopState.PASSED
    assert result.shipped_to_production is True
    assert result.operator_approval_required is False
    assert result.iterations == 1
    assert result.provenance_signature is not None


def test_lineage_a_self_healing_retry_loop() -> None:
    engine = FactoryEngine(max_retries=3)
    attempt = 0

    def mock_generator(feedback: str | None) -> str:
        nonlocal attempt
        attempt += 1
        if attempt == 1:
            assert feedback is None
        else:
            assert "SyntaxError" in (feedback or "")
        return LOW_RISK_DIFF

    def mock_gate(patch: str) -> GateResult:
        if attempt == 1:
            return GateResult(passed=False, diagnostics="SyntaxError: invalid token")
        return GateResult(passed=True, diagnostics="")

    result = engine.run(
        run_id="run-2",
        generator_fn=mock_generator,
        gate_fn=mock_gate,
    )

    assert result.status == HybridLoopState.PASSED
    assert result.iterations == 2
    assert result.shipped_to_production is True


def test_lineage_b_requires_operator_approval() -> None:
    engine = FactoryEngine()

    def mock_generator(feedback: str | None) -> str:
        return HIGH_RISK_DIFF

    def mock_gate(patch: str) -> GateResult:
        return GateResult(passed=True, diagnostics="")

    result = engine.run(
        run_id="run-3",
        generator_fn=mock_generator,
        gate_fn=mock_gate,
    )

    assert result.lineage == FactoryLineage.LINEAGE_B
    assert result.status == HybridLoopState.PASSED
    assert result.shipped_to_production is False
    assert result.operator_approval_required is True

    # Operator approves production deployment
    approved_result = engine.operator_approve(result)
    assert approved_result.shipped_to_production is True
    assert approved_result.operator_approval_required is False


def test_max_retries_routes_to_exception_queue() -> None:
    engine = FactoryEngine(max_retries=2)

    def mock_generator(feedback: str | None) -> str:
        return LOW_RISK_DIFF

    def mock_gate(patch: str) -> GateResult:
        return GateResult(passed=False, diagnostics="Persistent dynamic assertion failure")

    result = engine.run(
        run_id="run-4",
        generator_fn=mock_generator,
        gate_fn=mock_gate,
    )

    assert result.status == HybridLoopState.EXCEPTION_QUEUED
    assert result.iterations == 2
    assert result.shipped_to_production is False
    assert "Persistent dynamic assertion failure" in result.diagnostic_message
