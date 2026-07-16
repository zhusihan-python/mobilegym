# TP-EP05 Profile-aware Target Comparison Evidence - 2026-07-16

## Status

| Field | Value |
|---|---|
| Task | `TP-EP05` |
| Status | Complete |
| Requirements | `EP-FR-003`, `EP-FR-005`, `EP-FR-008`, `EP-FR-009` |
| Source revision | implementation commit containing this evidence |
| Branch | `main` |
| Recorded at | `2026-07-16` |
| Live model required | No |
| Next task | `TP-EP06` not started |

## Delivered vertical slice

Paired Workflow v2 definitions now expose target-free `baseline` and
`candidate` Lane Slots plus a frozen comparison policy. Run Launch binds those
slots to two different exact Target Revisions and one identical Execution
Profile Revision. No mutable target or profile head is embedded in the Workflow
Version.

Preview classifies the requested comparison axes, compiles the shared Run Plan
v2, returns exact Lane fingerprints, and reports `same_app`, `same_device`, and
`same_data` violations without writing. Create repeats the checks and rejects a
constraint violation, confounded comparison, no-variation comparison, or stale
identity before any Run, attempt, artifact, event, idempotency, or dispatch
side effect.

Both Lanes reuse the same task source, seed, Episode templates, Prepared Episode,
parameters, instruction, state projection, time/location policy, evaluation,
and Judge protocol. Compatibility Preflight runs once for the shared effective
subject tuple and records both Lane keys on the initial Run Attempt. Existing
paired execution produces comparison classifications through the profile-aware
Run Plan v2 path while Run detail and Console reload retain both exact
Target/Profile identities.

## Console evidence

Paired Workflow authoring now emits schema v2 Lane Slots rather than bound v1
lanes. The Run Launch workspace detects the published Lane Slot shape, presents
Target Comparison, selects exact baseline/candidate Target Revisions and one
shared Execution Profile Revision, renders both Lane fingerprints and
constraint violations, and creates or reloads the exact profile-aware Run. The
public Run detail then retains both exact identities while rendering the
comparison regression and failed gate, switching replay from candidate to
baseline, and copying the immutable Lane/Episode/attempt incident link.

## TDD record

| Slice | Red | Green |
|---|---|---|
| Paired Workflow v2 | Validator rejected baseline/candidate Lane Slots | Target-free paired Lane Slots and a required comparison policy publish successfully |
| Target Comparison launch | HTTP schema accepted `single` only | Preview/create freeze two Targets, one Profile, one Prepared Episode, and both Lane fingerprints |
| Comparison boundaries | No profile-aware constraint/axis contract existed | Advisory preview, authoritative zero-write create gate, confounded/no-variation errors |
| Shared paired execution | Existing deterministic acceptance used Run Plan v1 | Run Plan v2 reuses one Prepared Episode and records a deterministic regression |
| Console authoring | Paired authoring embedded mutable target IDs in v1 lanes | Paired authoring emits Workflow v2 Lane Slots |
| Console launch | Run Launch bound only one candidate Lane Slot | Target Comparison binds two exact Targets and one shared Profile and reloads exact identity |
| Console public consumers | Existing observatory fixtures used Legacy Execution Identity | Reloaded profile-aware Run detail renders comparison/gate, switches replay Lanes without identity drift, and preserves immutable incident selection |

## Verification

| Phase | Result |
|---|---|
| Focused paired backend and Run Launch API | 31 passed, 2 existing warnings |
| Run Launch/profile secret compatibility regression | 21 passed, 1 existing warning |
| Workflow/report/replay compatibility regression | 41 passed, 1 existing warning |
| Focused Console | 7 passed across 3 files |
| Full Platform Console | 69 passed across 22 files |
| TypeScript type check | clean |
| Full Test Platform backend | 501 passed, 2 existing warnings |

Manual dogfood and a live model were not required. TP-EP06 has not started.
