# MobileGym Test Platform Operator Guide

## Document status

| Field | Value |
|---|---|
| Status | Current for the hardening release accepted 2026-07-13 |
| Release evidence | [`evidence/2026-07-13-tp-h16-release-acceptance.md`](evidence/2026-07-13-tp-h16-release-acceptance.md) |
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
