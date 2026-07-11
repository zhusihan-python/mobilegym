# MobileGym Manual Sequence Handoff - 2026-07-11

## Suggested Skills

- `handoff`: use again if the next session needs another compact checkpoint.
- `diagnose`: use if the next model deployment still fails; keep the loop focused on the model endpoint payload/response and the run execution path.
- `ego-browser` or equivalent browser automation: use only when validating the live Test Platform UI, Run Detail, Replay, or Report rendering.
- `tdd`: use if another execution-edge bug appears and needs a narrow regression test before code changes.

If these skills are unavailable in the next environment, follow the same workflows manually.

## Context

Repository: `/Users/allen/Projects/mobilegym`

The current work is Manual Sequence Workflow v1 for the Test Platform. The authoritative contract is already captured in:

- `docs/test-platform/MANUAL_SEQUENCE_V1.md`

Do not duplicate that contract in new docs. Read the contract and the source when continuing.

This handoff is stored in-repo, not in `/tmp`, so it can be read from another PC after syncing the workspace.

## Current Git State

Immediately before creating this handoff, the worktree was clean. This handoff file itself is new and may be uncommitted.

Latest relevant commits:

- `689de19 Reconcile missing serial sequence results`
- `a672e36 Show manual sequence policy facts`
- `05c1377 Group manual sequence report summaries`
- `688543a Make replay picker sequence aware`
- `a8dbefe Show manual sequence order in run detail`
- `00e7206 Add manual sequence workflow editor mode`
- `055171d Preserve manual sequence metadata on followup attempts`
- `b49f89f Isolate manual sequence episode state`

Earlier Manual Sequence v1 commits through M00-M09 are in the log before these. Use `git log --oneline -- docs/test-platform/MANUAL_SEQUENCE_V1.md test_platform web/test-platform tests` if you need the full chain.

## What Is Done

Manual Sequence v1 is implemented through the full console, planning, execution metadata, Run Detail, Replay picker, and Report presentation path:

- Workflow validation and compile preview support `linear_sequence`.
- Run plans and persisted episodes carry `sequence_index` and `sequence_group_id`.
- Serial execution runs manual sequence episodes in order with isolated state.
- Retry/resume preserve sequence metadata.
- Workflow Console supports manual ordered task selection and shows read-only policy facts:
  - `state_policy = isolated`
  - `failure_policy = continue`
- Run Detail episode identities show `Seq`, `Group`, and `Task`.
- Replay picker sorts sequence episodes by `sequence_index` and labels options like `Step 1: task.id`.
- Report payload and UI group sequence episodes under `sequence.groups`.

## M12 Dogfood Status

The in-repo Test Platform path works. The remaining blocker is the local model deployment, not Manual Sequence v1.

Reusable Test Platform entities from the dogfood project:

- Project: `Manual Sequence M12 Dogfood 2026-07-08`
- Project id: `50af9fbff8f148b483459654171b1039`
- Workflow: `WeChat smoke`
- Workflow id: `556f88938558413b9aaeacd8bb5e803d`
- Published workflow version id: `fa38f64b0bb346e5bf511226dff0f716`
- Definition hash: `sha256:c7c4cdfe94dab35328ca38598d10ee745abff9387cd3cf753299c97851fa50af`
- Target id: `8db54cfa9b884c72b74e74908872168d`
- Manual sequence order:
  - Step 1: `wechat.OpenBlacklist`
  - Step 2: `wechat.BlacklistContact`

### 2026-07-08 Smoke

Model: `autoglm-phone-9b` via LM Studio OpenAI-compatible endpoint.

Run id: `fe459ff6a6614a378ea9c6b51a6188ea`

Result:

- The run entered the simulator and started the first episode.
- LM Studio returned HTTP 400 because the model did not support image inputs.
- That dogfood exposed a backend fallout bug: after a missing serial result, the executor raised `ValueError: zip() argument 2 is shorter than argument 1`.
- Fixed in commit `689de19 Reconcile missing serial sequence results`.

### 2026-07-09 Smoke

Model: `autoglm-phone-9b-multilingual` loaded in LM Studio.

Run id: `4a615b04769148958da59faff7af6196`

Run config used:

- `agent = generic_v2`
- `model_base_url = http://127.0.0.1:1234/v1`
- `model_name = autoglm-phone-9b-multilingual`
- `image_url_format = data_url`

Result:

- Run state: `completed`
- Progress: `2 / 2` lane episodes completed
- Both episodes reached execution and produced `ERROR / EXECUTION_ERROR`
- Error text from both episode events:
  - `autoglm-phone-9b-multilingual does not support image inputs`
- Sequence report was correct:
  - `sequence.groups[0].sequence_group_id = manual_sequence`
  - Step 1: `wechat.OpenBlacklist`, outcome `ERROR`
  - Step 2: `wechat.BlacklistContact`, outcome `ERROR`
- The executor no longer emitted the previous secondary `ValueError`.

Conclusion: the platform path is healthy; LM Studio's current deployment for these AutoGLM models is not exposing image input capability.

## Validation Already Run

Focused regression after `689de19`:

```bash
.venv/bin/python -m pytest test_platform/tests/integration/test_serial_run_execution.py -q
.venv/bin/python -m pytest test_platform/tests/integration/test_cancel_run.py -q
```

Results:

- `test_serial_run_execution.py`: `3 passed`
- `test_cancel_run.py`: `9 passed`, with one existing Starlette/httpx2 deprecation warning

Earlier focused validations from M10-M11:

```bash
npx vitest run --config vitest.platform.config.ts tests/testPlatformRunObservatory.test.tsx
.venv/bin/python -m pytest test_platform/tests/unit/test_sequence_report.py test_platform/tests/integration/test_report_input.py -q
npx vitest run --config vitest.platform.config.ts tests/testPlatformReports.test.tsx
npm run platform:typecheck
```

Earlier console polish validation:

```bash
npx vitest run --config vitest.platform.config.ts tests/testPlatformWorkflowEditor.test.tsx tests/testPlatformComparisonPolicy.test.tsx
```

## How To Re-run The Smoke

1. Start LM Studio with a vision-capable OpenAI-compatible model.
2. Verify the OpenAI-compatible model endpoint:

```bash
curl -sS http://127.0.0.1:1234/v1/models
```

3. Start the Test Platform services:

```bash
npm run platform:api
npm run platform:web -- --port 5173
```

4. Launch a run against the existing published workflow version:

```bash
python3 - <<'PY'
import json, urllib.request, uuid

body = {
    "workflow_version_id": "fa38f64b0bb346e5bf511226dff0f716",
    "name": "M12 smoke",
    "overrides": {
        "seed": 0,
        "execution": {
            "agent": "generic_v2",
            "model_base_url": "http://127.0.0.1:1234/v1",
            "model_name": "<vision-capable-model-id>",
            "image_url_format": "data_url",
        },
    },
}

req = urllib.request.Request(
    "http://127.0.0.1:8787/api/platform/v1/runs",
    data=json.dumps(body).encode(),
    headers={
        "Content-Type": "application/json",
        "Idempotency-Key": str(uuid.uuid4()),
    },
    method="POST",
)
with urllib.request.urlopen(req, timeout=30) as resp:
    print(json.dumps(json.load(resp), indent=2)[:4000])
PY
```

5. Poll run detail and events:

```bash
python3 - <<'PY'
import json, time, urllib.request

run = "<run-id>"
base = f"http://127.0.0.1:8787/api/platform/v1/runs/{run}"
last_event = 0
for i in range(36):
    detail = json.load(urllib.request.urlopen(base, timeout=10))
    events = json.load(urllib.request.urlopen(base + "/events", timeout=10)).get("items", [])
    new = [e for e in events if int(e.get("sequence", 0)) > last_event]
    if events:
        last_event = max(int(e["sequence"]) for e in events)
    print(f"[{i:02d}] state={detail['state']} progress={detail['progress']} attempts={len(detail.get('episode_attempts', []))}", flush=True)
    for e in new[-6:]:
        payload = e.get("payload") or {}
        msg = payload.get("error") or payload.get("outcome") or payload.get("state") or ""
        print("   event", e.get("sequence"), e.get("type"), str(msg)[:240], flush=True)
    if detail["state"] in {"completed", "failed", "cancelled"}:
        break
    time.sleep(10)
PY
```

6. Check the sequence report:

```bash
curl -sS http://127.0.0.1:8787/api/platform/v1/runs/<run-id>/report
```

Expected for a working model:

- The first episode should record one or more steps, not fail immediately with "does not support image inputs".
- `sequence.groups` should still contain `manual_sequence` with Step 1 and Step 2 in order.

## Open Item

Find or deploy a model endpoint that accepts the screenshot payload sent by `generic_v2`.

The current failing error is model capability-level:

- `The provided messages contain images, but <model> does not support image inputs.`

If the next deployment still fails, inspect:

- `bench_env/agent/generic_v2.py`
- `bench_env/llm/openai_chat.py`
- LM Studio or alternate server's OpenAI-compatible vision message format support
- `image_url_format` (`data_url` vs `bare_base64`) only if the endpoint says it supports image inputs but rejects the specific image format

## Redaction Note

No API keys, passwords, or private credentials are included. Model endpoint URLs are local loopback addresses only.
