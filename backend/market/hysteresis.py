"""Strategic hysteresis (§60): deterministic gates against shiny-object pivots.

A new opportunity must overcome switching cost + uncertainty margin + evidence for the
current strategy before anything more than a micro test is proposed. One signal is
never enough.
"""
from __future__ import annotations

EVIDENCE_ORDER = ["attention", "interest", "pain", "commercial_intent", "purchase", "revenue"]

MIN_DISTINCT_SIGNALS = 2          # a single market signal never justifies action (§139)
MIN_EVIDENCE_LEVEL = "pain"       # attention/interest alone are not a market (§66)
MIN_COMBINED_STRENGTH = 1.0       # sum of strengths across qualifying signals


def evidence_rank(level: str) -> int:
    try:
        return EVIDENCE_ORDER.index(level)
    except ValueError:
        return 0


def qualifies_for_micro_test(signals: list[dict]) -> bool:
    """Deterministic: may we PROPOSE a micro test? (Never kills the core strategy.)"""
    qualifying = [s for s in signals
                  if evidence_rank(s.get("evidence_level", "attention")) >= evidence_rank(MIN_EVIDENCE_LEVEL)
                  and float(s.get("strength", 0)) > 0]
    if len(qualifying) < MIN_DISTINCT_SIGNALS:
        return False
    return sum(float(s.get("strength", 0)) for s in qualifying) >= MIN_COMBINED_STRENGTH


def pivot_verdict(signals: list[dict]) -> str:
    """What the evidence supports. Strategy changes above micro_test always require
    experiment results + user confirmation — never a scan alone."""
    if not qualifies_for_micro_test(signals):
        return "NO_ACTION"
    return "PROPOSE_MICRO_TEST"
