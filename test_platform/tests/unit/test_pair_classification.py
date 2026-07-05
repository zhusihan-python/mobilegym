"""VS-09 Block A: pair classification (regression/fixed/stable/error/violation/unpaired)."""
from __future__ import annotations

from test_platform.domain.pair_classification import (
    IntegrityStatus,
    PairClassification,
    classify_pair,
)


def test_pass_fail_is_regression():
    assert classify_pair("PASS", "FAIL") == PairClassification.REGRESSION


def test_fail_pass_is_fixed():
    assert classify_pair("FAIL", "PASS") == PairClassification.FIXED


def test_pass_pass_is_stable_pass():
    assert classify_pair("PASS", "PASS") == PairClassification.STABLE_PASS


def test_fail_fail_is_stable_fail():
    assert classify_pair("FAIL", "FAIL") == PairClassification.STABLE_FAIL


def test_baseline_error_when_baseline_errors():
    assert classify_pair("ERROR", "PASS") == PairClassification.BASELINE_ERROR
    assert classify_pair("ERROR", "FAIL") == PairClassification.BASELINE_ERROR


def test_candidate_error_when_candidate_errors():
    assert classify_pair("PASS", "ERROR") == PairClassification.CANDIDATE_ERROR
    assert classify_pair("FAIL", "ERROR") == PairClassification.CANDIDATE_ERROR


def test_cancelled_treated_as_error():
    assert classify_pair("CANCELLED", "PASS") == PairClassification.BASELINE_ERROR
    assert classify_pair("PASS", "CANCELLED") == PairClassification.CANDIDATE_ERROR


def test_unpaired_when_one_side_missing():
    assert classify_pair(None, "PASS") == PairClassification.UNPAIRED
    assert classify_pair("PASS", None) == PairClassification.UNPAIRED
    assert classify_pair(None, None) == PairClassification.UNPAIRED


def test_pairing_violation_takes_precedence_over_outcome():
    """Integrity violation short-circuits BEFORE outcome classification — even
    a PASS/FAIL pair becomes PAIRING_VIOLATION, not regression."""
    assert classify_pair(
        "PASS", "FAIL", integrity_status=IntegrityStatus.INSTRUCTION_MISMATCH,
    ) == PairClassification.PAIRING_VIOLATION
    assert classify_pair(
        "PASS", "PASS", integrity_status=IntegrityStatus.PROJECTION_MISMATCH,
    ) == PairClassification.PAIRING_VIOLATION
    assert classify_pair(
        "FAIL", "PASS", integrity_status=IntegrityStatus.PARAM_MISMATCH,
    ) == PairClassification.PAIRING_VIOLATION


def test_pairing_violation_precedence_over_error():
    """Violation takes precedence over baseline/candidate error too."""
    assert classify_pair(
        "ERROR", "PASS", integrity_status=IntegrityStatus.PROJECTION_MISMATCH,
    ) == PairClassification.PAIRING_VIOLATION


def test_unpaired_takes_precedence_over_violation():
    """Unpaired is checked first (can't classify integrity with one side missing)."""
    assert classify_pair(
        None, "PASS", integrity_status=IntegrityStatus.INSTRUCTION_MISMATCH,
    ) == PairClassification.UNPAIRED


def test_integrity_status_is_violation_property():
    assert IntegrityStatus.OK.is_violation is False
    assert IntegrityStatus.INSTRUCTION_MISMATCH.is_violation is True
    assert IntegrityStatus.PROJECTION_MISMATCH.is_violation is True
    assert IntegrityStatus.PARAM_MISMATCH.is_violation is True
