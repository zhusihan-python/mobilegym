"""Pair classification for baseline/candidate comparison.

Maps a (baseline_outcome, candidate_outcome, integrity_status) triple to one of
the VS-09 comparison classifications. The classification ORDER matters (per
TECHNICAL_ARCHITECTURE §22.5):

    1. unpaired          — one side is missing entirely
    2. pairing_violation — integrity check failed (instruction/projection/param mismatch)
    3. baseline_error    — baseline errored (candidate may be anything non-error)
    4. candidate_error   — candidate errored
    5. regression / fixed / stable_pass / stable_fail — outcome combinations

Integrity violations take precedence over outcome-based classifications because a
violated pair cannot produce a trustworthy functional verdict.
"""
from __future__ import annotations

from enum import Enum


class IntegrityStatus(str, Enum):
    """Why a pair may fail integrity (or OK if it passes)."""
    OK = "OK"
    INSTRUCTION_MISMATCH = "instruction_mismatch"
    PROJECTION_MISMATCH = "projection_mismatch"
    PARAM_MISMATCH = "param_mismatch"

    @property
    def is_violation(self) -> bool:
        return self != IntegrityStatus.OK


class PairClassification(str, Enum):
    UNPAIRED = "unpaired"
    PAIRING_VIOLATION = "pairing_violation"
    BASELINE_ERROR = "baseline_error"
    CANDIDATE_ERROR = "candidate_error"
    REGRESSION = "regression"
    FIXED = "fixed"
    STABLE_PASS = "stable_pass"
    STABLE_FAIL = "stable_fail"


def classify_pair(
    baseline_outcome: str | None,
    candidate_outcome: str | None,
    *,
    integrity_status: IntegrityStatus = IntegrityStatus.OK,
) -> PairClassification:
    """Classify a baseline/candidate episode pair.

    Args:
        baseline_outcome: "PASS" / "FAIL" / "ERROR" / "CANCELLED" / None (missing).
        candidate_outcome: same vocabulary.
        integrity_status: integrity check result. Any violation short-circuits
            to PAIRING_VIOLATION before outcome is considered.

    Returns:
        The PairClassification for this pair.
    """
    # 1. Unpaired: one side is missing entirely.
    if baseline_outcome is None or candidate_outcome is None:
        return PairClassification.UNPAIRED

    # 2. Pairing violation: integrity takes precedence over outcome.
    if integrity_status.is_violation:
        return PairClassification.PAIRING_VIOLATION

    b = baseline_outcome.upper()
    c = candidate_outcome.upper()

    # 3. Baseline error (candidate is anything non-error).
    if b == "ERROR":
        return PairClassification.BASELINE_ERROR
    # 4. Candidate error.
    if c == "ERROR":
        return PairClassification.CANDIDATE_ERROR

    # Cancelled episodes are treated as errors for classification (the run did
    # not produce a valid functional verdict on that side).
    if b == "CANCELLED":
        return PairClassification.BASELINE_ERROR
    if c == "CANCELLED":
        return PairClassification.CANDIDATE_ERROR

    # 5. Outcome combinations (PASS/FAIL only at this point).
    if b == "PASS" and c == "FAIL":
        return PairClassification.REGRESSION
    if b == "FAIL" and c == "PASS":
        return PairClassification.FIXED
    if b == "PASS" and c == "PASS":
        return PairClassification.STABLE_PASS
    if b == "FAIL" and c == "FAIL":
        return PairClassification.STABLE_FAIL

    # Fallback: unexpected outcome vocabulary.
    return PairClassification.UNPAIRED
