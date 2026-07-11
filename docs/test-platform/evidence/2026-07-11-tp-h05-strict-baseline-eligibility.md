# TP-H05 Strict Baseline Eligibility Evidence - 2026-07-11

## Status

| Field | Value |
|---|---|
| Task | `TP-H05` |
| Status | Complete |
| Base revision | `ddecb72` |
| Branch | `main` |
| Recorded at | `2026-07-11` |
| Live model required | No |
| Browser dogfood required | No; deterministic browser smoke belongs to TP-H06 and TP-H07 |

## Public contract

`BaselineEligibility.evaluate(run_id, lane_key)` is the single strict decision
module. The read-only HTTP interface is:

```text
GET /api/platform/v1/runs/{run_id}/baseline/eligibility?lane_key={lane_key}
```

It returns the selected lane, eligibility boolean, exact outcome counts, and a
stable list of `{code, message, details}` reasons. `POST .../baseline` evaluates
the same result immediately before inserting a baseline and returns
`BASELINE_PROMOTION_INELIGIBLE` with that complete DTO when rejected.

The evaluator requires:

- an already persisted report, without invoking historical report repair;
- the completed run attempt referenced by that report;
- project, workflow, run-plan, task-source, and selected target-revision
  provenance;
- a non-empty and complete episode grid for the selected lane on that exact run
  attempt;
- the exact terminal outcome `PASS` for every planned selected-lane episode.

Stable reason codes include `REPORT_NOT_PERSISTED`,
`RUN_ATTEMPT_NOT_COMPLETED`, `STRICT_PROVENANCE_INCOMPLETE`,
`SELECTED_LANE_NOT_FOUND`, `SELECTED_LANE_EMPTY`,
`SELECTED_LANE_INCOMPLETE`, and `SELECTED_LANE_OUTCOME_NOT_PASS`.

## Lane-specific behavior

A paired fixture has two complete planned episodes per lane. Its baseline lane
has two PASS outcomes while its candidate lane has one PASS and one ERROR. The
overall quality gate is `failed` because of the candidate error. The read model
and promotion endpoint both allow the baseline lane and reject the candidate
lane with the exact error count.

Separate single-lane cases prove that FAIL, ERROR, terminal CANCELLED, and a
missing attempt are all ineligible. Those fixtures use a configured quality
gate that returns `passed`, proving that a permissive gate cannot relax strict
selected-lane eligibility. The no-threshold behavior is covered by the same
public contract and remains independent as `not_configured`.

Imported terminal runs remain readable but return
`STRICT_PROVENANCE_INCOMPLETE`. Promotion request bodies reject unknown fields,
so `force`, `override`, or similar bypass flags cannot be accepted silently.

## Console behavior

Run Detail selects a concrete lane, fetches its eligibility, and sends the same
lane key on promotion. The action stays disabled while checking or when any
reason exists. Rejection messages are rendered directly from the structured
server response. The component test switches from the healthy baseline lane to
the failing candidate lane and back, proving both disabled and successful
states while the overall displayed gate remains failed.

## TDD and verification

| Phase | Result |
|---|---|
| API red | The selected-lane eligibility route returned 404 |
| API tracer green | Healthy baseline accepted and failing candidate rejected through read and create interfaces |
| Cancellation red | A terminal `state=cancelled, outcome=CANCELLED` attempt was initially counted as incomplete |
| Cancellation green | Terminal cancelled work is counted and rejected as a non-PASS outcome |
| UI red | No lane selector, eligibility status, rejection reason, or disabled promotion state existed |
| Focused backend | 11 passed, 1 existing Starlette/httpx2 deprecation warning, in 0.57 s |
| Focused frontend | 2 passed in 0.93 s |
| Frontend type check | `npm run platform:typecheck` passed |
| Full frontend regression | 55 passed across 19 files in 2.19 s |
| Full Test Platform regression | 296 passed, 1 existing Starlette/httpx2 deprecation warning, in 59.69 s |

No assertion was skipped or weakened. Existing baseline promotion expectations
that allowed an incomplete failing candidate were intentionally replaced by the
strict selected-lane contract.
