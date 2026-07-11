# TP-H09 Screenshot-Model Compatibility Check Evidence - 2026-07-12

## Status

| Field | Value |
|---|---|
| Task | `TP-H09` |
| Status | Complete |
| Source revision | implementation commit containing this evidence |
| Branch | `main` |
| Recorded at | `2026-07-12` |
| Live model required | No (production adapter tested via MockTransport) |

## What was built

A typed screenshot-model compatibility check that lets an operator verify a
model endpoint can receive and accept the exact screenshot image format used by
`generic_v2`, before launching a run.

### Layered design

- **Domain** (`test_platform/domain/model_compatibility.py`): stable codes,
  `CompatibilityResult`, `CompatibilityProbe` protocol, and a pure
  `classify_response` function. No SDK dependencies.
- **Adapter** (`test_platform/adapters/model_compatibility.py`):
  `OpenAICompatibilityProbe` owns the OpenAI SDK, client factory
  (`max_retries=0`, `httpx.Timeout`), message building, and provider
  exception/body inspection. Delegates classification to the domain.
- **Fake** (`test_platform/testing/fake_compat.py`): `FakeCompatibilityProbe`
  for route-layer wiring tests only — not used as production adapter evidence.
- **Endpoint** (`test_platform/api/routes/compatibility.py`):
  `POST /model-compatibility/check` with a strict request schema.
- **Error handler** (`test_platform/api/errors.py`): `RequestValidationError`
  handler that strips `input`/`ctx` from 422 responses.

### Shared message building

A new helper `bench_env/agent/screenshot_message.py` extracts the
`build_screenshot_user_message` function previously inline in
`GenericAgentV2.build_messages()`. Both the agent and the compatibility probe
call it, then apply `_format_image_url_messages` for provider format
adaptation. This guarantees the probe uses the same message-building
implementation as real inference.

### Bounded minimal request

The probe sends one non-streaming `chat.completions.create` with:
- `max_tokens=1` (minimal generation)
- `max_retries=0` (no SDK retry)
- `httpx.Timeout(timeout_seconds)` (bounded I/O timeout)
- A fixed 64x64 PNG probe image (large enough to avoid VLM minimum-size
  rejections)

The OpenAI client is used as a context manager (`with ... as client:`),
ensuring the underlying httpx connection pool is closed after each check.

## Error classification priority

1. `authentication_failure` (401/403)
2. `missing_model` (404 + model keyword in body)
3. `unsupported_image_format` (400 + format/base64/content-part keyword)
4. `unsupported_vision` (400/422 + vision/multimodal keyword)
5. `timeout` (APITimeoutError)
6. `unreachable` (APIConnectionError)
7. `indeterminate` (429/5xx/unclassifiable)

A regression test verifies that a body mentioning both "image" and "base64
format" classifies as `unsupported_image_format`, not `unsupported_vision`.

## Secrets discipline

- `CompatibilityResult` has no `api_key` field — structurally impossible to leak.
- Explanations use allowlisted templates; provider body/exception text never
  reaches the response or logs.
- Logs record only `code`, `latency_ms`, `checked_model`, `checked_image_format`.
- 422 validation errors are sanitized: only `loc` + `type`, never `input`/`ctx`.
- URLs with userinfo are rejected at validation time.
- `model_api_key` is never persisted to localStorage.

Sentinel tests verify (`sk-sentinel-secret`):
- Not in any success/failure/422 response body
- Not in caplog
- Not in the SQLite database
- Not in the browser DOM
- Table counts unchanged before/after (no runs/events/artifacts/reports created)
- No Execution Profile persisted or versioned

## Production adapter verification

The `OpenAICompatibilityProbe` is tested via `httpx.MockTransport`, so the real
SDK call chain runs end-to-end without live network:

- Verifies the SDK received correct `Authorization` header, `model`, and
  `image_url` content part (data URL and bare base64 formats)
- Covers success + every stable failure code
- Verifies `max_tokens=1` is sent

## UI

- "Test connection" button with `type="button"` (does not submit the launch form)
- Only enabled for `generic_v2` agent; other agents see a disabled hint
- Stale results cleared on any form field change
- Request token guards against race conditions (old results don't overwrite new)
- Result displays code, explanation, latency, checked model, checked image format

## Verification

| Phase | Result |
|---|---|
| Unit tests (classification + adapter MockTransport + PNG verify) | 29 passed |
| Integration tests (endpoint + sentinel + no-persistence + default wiring + import guard) | 14 passed |
| Frontend component tests (incl. race condition) | 4 passed |
| bench_env image_url_format (no regression) | 2 passed |
| Full Test Platform regression | 344 passed |
| Frontend vitest | 59 passed |
| TypeScript type check | clean |
| `git diff --check` | clean |

## Observable demo

Open the Runs page, select a `generic_v2` workflow, fill in a model endpoint,
and click "Test connection". A compatible endpoint returns `compatible` with
latency; a vision-rejecting or auth-failing endpoint returns the matching stable
code and explanation — all without creating a run.

## Review gap fixes

Three P1 issues found in post-commit review were closed before final sign-off:

1. **Production wiring**: `create_app(settings)` now constructs
   `OpenAICompatibilityProbe()` by default (was `None` → always 503). A
   composition guard test verifies the endpoint works without DI injection.
2. **Valid probe PNG**: the hardcoded byte array was corrupt (chunk boundary
   misalignment). Replaced with a Pillow-generated 64x64 PNG, verified by
   `Image.verify()` + size/format assertions.
3. **Precise failure codes**: the timeout test no longer accepts
   `TIMEOUT | UNREACHABLE`. Split into independent `ConnectTimeout` → `timeout`,
   `ReadTimeout` → `timeout`, and `ConnectError` → `unreachable` cases, each
   asserting a single code. The `closed` flag is now asserted to verify client
   lifecycle.

Additional hardening: `model_name` is trimmed and rejected if empty; a frontend
race test verifies that a late stale response does not overwrite a newer result
and that field changes clear stale results. The production adapter's direct
dependencies (`openai`, `httpx`, `Pillow`) are declared in
`test_platform/requirements.txt` and verified by an import guard test. The
composition guard test no longer makes real network calls — it uses `isinstance`
+ monkeypatched `check()` to verify wiring.
