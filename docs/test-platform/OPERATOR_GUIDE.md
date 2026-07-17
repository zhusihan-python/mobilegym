# MobileGym Test Platform Operator Guide

## Document status

| Field | Value |
|---|---|
| Status | Current through the TP-EP10 Execution Profiles release on 2026-07-17 |
| Current evidence | [`evidence/2026-07-17-tp-ep10-execution-profiles-release-acceptance.md`](evidence/2026-07-17-tp-ep10-execution-profiles-release-acceptance.md) |
| Live model required for acceptance | No |

## Local startup

The Test Platform service binds to loopback by default:

```bash
python -m test_platform.main
```

Non-loopback binding is blocked unless the operator opts in explicitly:

```bash
TEST_PLATFORM_HOST=0.0.0.0 \
TEST_PLATFORM_ALLOW_NON_LOOPBACK=true \
python -m test_platform.main
```

When exposing the service beyond loopback, also configure a local secret value
through `TEST_PLATFORM_AUTH_TOKEN`; report exports redact this configured value
before serialization.

## Normal profile-aware launch

The Console's normal launch path is **Run Launch**. Workflow authoring defines
tasks, evaluation policy, and Workflow v2 Lane Slots; it does not select a
Target, Agent, model, endpoint, Image Input Format, or API key.

1. Create and health-check a Target.
2. Create an Execution Profile draft, review its typed public subject settings,
   bind any Credential References, and publish an immutable revision.
3. Publish a Workflow v2 version with the required Lane Slots.
4. Open **Run Launch**, select the exact published Workflow, Target, and
   Execution Profile revisions, then preview the resolved Lane Bindings.
5. Supply any requested secret values transiently and create the Run.

Run Launch creates only profile-aware Run Plan v2 identity. Browser persistence
for launch is limited to the most recently selected Execution Profile Revision
ID. Target/model/endpoint/Image Input Format settings, Credential Reference
payloads, and secret values are not persisted by the launch UI.

## Reviewing deprecated saved launch preferences

On first Console load after this migration, deprecated
`test-platform.launch.*` browser keys are removed. If the old values contain a
complete `generic_v2` endpoint, model name, and Image Input Format, the Console
retains only an in-memory, non-secret copy for the current page session.

Open **Execution Profiles** and choose **Review saved launch preferences** to
copy that data into a visible new-profile draft. Review and save the draft,
then bind Credential References and publish through the normal profile
lifecycle. The migration never copies an API key or Credential Reference
payload and never publishes automatically. Cancelling or reloading discards the
one-time in-memory copy.

## Legacy create-run compatibility

The Console no longer exposes loose inline execution launch fields. Supported
HTTP clients may continue to call `POST /api/platform/v1/runs` during the
documented compatibility window. That adapter intentionally creates Run Plan
v1 with **Legacy Execution Identity**; reads and Retry/Resume preserve the same
identity and never synthesize an Execution Profile Revision.

See [`EXECUTION_PROFILES_COMPATIBILITY.md`](EXECUTION_PROFILES_COMPATIBILITY.md)
for the supported legacy surface, security boundaries, and exit criteria.

## Importing a legacy CLI run

Legacy run roots are directories that contain `results.jsonl` and usually also
`meta.json`, `summary.json`, `errors.jsonl`, and `trajectory/`.

Import through the console Runs page, or call the API directly:

```bash
curl -X POST http://127.0.0.1:8787/api/platform/v1/runs/import \
  -H 'content-type: application/json' \
  -d '{
    "project_id": "<project-id>",
    "source_path": "/absolute/path/to/runs/20260706_legacy",
    "name": "Imported CLI run"
  }'
```

The importer creates a platform logical run and a symlink under `runs/<run_id>`
that points at the source directory. It does not rewrite source artifacts.
Imported runs show an explicit provenance warning because workflow, target
revision, and task source metadata may be unavailable. Runs with missing
provenance cannot be promoted as strict baselines.

## Run Explorer

The Vite dev server exposes Run Explorer data through `/api/runs`.
Discovery is recursive and includes:

- flat CLI runs such as `runs/20260706_legacy`;
- legacy nested runs such as `runs/<agent>/<timestamp>`;
- platform lane attempts such as `runs/<run_id>/lanes/candidate/attempts/0001`.

Open a platform lane attempt from Run Detail, or directly:

```text
/run_explorer.html?run=<run_id>%2Flanes%2Fcandidate%2Fattempts%2F0001
```

Imported legacy runs can be opened with:

```text
/run_explorer.html?run=<imported-run-id>
```

## Hardening release regression commands

```bash
python -m pytest -c test_platform/pytest.ini test_platform/tests -q
python -m pytest -c bench_env/tests/pytest.ini bench_env/tests/common -m "not live" -q
npm run platform:test
npm test
npm run platform:typecheck
npx tsc --noEmit
npm run lint
```

In a restricted Windows sandbox where pytest-xdist cannot create its default
temporary root, append a writable workspace-local path such as
`--basetemp .tmp/test-platform-bench` to the `bench_env` command. This is an
execution-environment workaround, not a release requirement on normal hosts.
