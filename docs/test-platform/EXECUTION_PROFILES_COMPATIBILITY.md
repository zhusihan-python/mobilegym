# Execution Profiles Compatibility Window

## Document status

| Field | Value |
|---|---|
| Status | Active compatibility contract from TP-EP09 on 2026-07-17 |
| Normal launch path | Console Run Launch; profile-aware Run Plan v2 |
| Legacy creation path | `POST /api/platform/v1/runs`; Legacy Execution Identity |
| Exit model | Criteria-based; no automatic sunset date |
| Current evidence | [`evidence/2026-07-17-tp-ep09-console-migration-legacy-compatibility.md`](evidence/2026-07-17-tp-ep09-console-migration-legacy-compatibility.md) |

## Purpose

The compatibility window lets supported HTTP clients migrate from loose inline
execution settings to immutable Execution Profile Revisions without falsifying
historical provenance. It is not an alternate Console workflow and does not
convert Legacy Execution Identity into profile-aware identity.

## Supported paths during the window

The normal product path is:

- publish a Workflow v2 version with Lane Slots;
- publish an Execution Profile Revision;
- select exact Workflow, Target, and Execution Profile revisions through
  `POST /api/platform/v1/projects/{project_id}/run-launch/preview` and
  `POST /api/platform/v1/projects/{project_id}/run-launch`;
- create Run Plan v2 with exact frozen Lane Bindings.

The legacy adapter remains supported for existing HTTP clients:

- `POST /api/platform/v1/runs` accepts the existing Workflow v1 and inline
  execution contract;
- the created Run is Run Plan v1 and reads as `Legacy Execution Identity`;
- Retry and Resume continue to use the Run's frozen inline configuration;
- imports, report v1/v2, existing Baselines, replay, and incident links remain
  readable and exportable;
- no reader, follow-up, import, report, baseline, replay, or incident view may
  infer or display a synthetic Execution Profile Revision.

The window does not promise that new Console features will expose the legacy
creation path. The Console's Runs and Workflows pages direct operators to Run
Launch.

## Security boundaries

- Raw secret values are transient request inputs. They are not stored in
  browser preferences, profile public specs, Run Plans, reports, Baselines, or
  exported artifacts.
- Credential Reference private locators remain private persistence data and are
  not browser launch preferences or public response fields.
- Browser launch persistence is limited to a recent Execution Profile Revision
  ID. Deprecated `test-platform.launch.*` keys are scrubbed on Console load.
- The one-time preference conversion accepts only the old non-secret
  `generic_v2` endpoint, model name, and Image Input Format. It opens a visible
  draft only after explicit operator action and never publishes it.
- Legacy inline runner configuration is historical execution data, not proof of
  an Execution Profile Revision. Current profile state must never be used to
  infer its identity.

## Exit criteria

The legacy create-run adapter remains available until every criterion below is
recorded in release evidence and a separate removal change is explicitly
authorized:

1. Every supported Console, CLI, CI, and external HTTP client is inventoried
   and uses Workflow v2 plus the profile-aware Run Launch endpoints for new
   Runs.
2. Normal request logs show zero successful legacy `POST /runs` creations for
   at least 30 consecutive days. Local-only installations without retained
   logs must instead provide an explicit signed-off consumer inventory covering
   every supported caller.
3. Run Plan v1 read, Retry/Resume, legacy import, report v1/v2 read/export,
   historical Baseline, replay, and incident compatibility tests remain green.
4. Operators have a migration note and a rollback plan for any client that
   still sends inline execution settings.
5. Product and architecture owners approve a separately scoped removal slice;
   completing TP-EP09 or TP-EP10 alone does not authorize removal.

Meeting these criteria permits removal of **new Legacy creation** only. Versioned
readers and historical follow-up/export behavior remain unless a later,
separately accepted retention policy changes that contract.

## Rollback

If profile-aware Console launch must be rolled back, restore the prior Console
build only after confirming it will not persist raw launch secrets. The backend
legacy adapter remains the compatibility fallback during this window. Never
rewrite Run Plan v1, report v1/v2, or historical Baseline records into synthetic
profile-aware records as part of rollback or recovery.
