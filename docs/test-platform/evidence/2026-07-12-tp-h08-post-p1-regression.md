# TP-H08 Post-P1 Milestone D Regression Evidence - 2026-07-12

## Status

| Field | Value |
|---|---|
| Task | `TP-H08` |
| Status | Complete — green P1 hardening baseline |
| Source revision | `00da2cb` (`TP-H07: deterministic paired-comparison browser smoke`) |
| Branch | `main` |
| Working tree | Clean (`git status --short` empty) |
| Recorded at | `2026-07-12` |
| Live model required | No |

This record reruns every command required by TP-H00 at the P1 completion commit.
Unlike the TP-H00 baseline (`c61fedd`), which failed on four blockers, all seven
commands pass here. The four TP-H00 blockers were resolved by the intervening
P0/P1 slices and are no longer reproducible.

## Environment

| Dependency | Version / location |
|---|---|
| Operating system | macOS Darwin 27.0 (arm64) |
| Python | `3.14.6` (`.venv/bin/python`) |
| Node.js | `v25.9.0` |
| npm | `11.13.0` |
| Key Python packages | FastAPI, Pydantic, pytest, OpenAI (via `.venv`) |

The Python environment is the project's `.venv`, populated from the declared
`test_platform/requirements.txt`, `test_platform/requirements-dev.txt`, and
`bench_env/requirements.txt`. The `requests` and `pytest-asyncio` dependencies
that TP-H00 found undeclared are now declared and installed.

## Results

| Command | Result | Detail |
|---|---|---|
| `python -m pytest -c test_platform/pytest.ini test_platform/tests -q` | **PASS** | 301 passed, 1 warning, ~77 s |
| `python -m pytest -c bench_env/tests/pytest.ini bench_env/tests/common -m "not live" -q` | **PASS** | 226 passed, ~32 s |
| `npm run platform:test` | **PASS** | 19 files, 55 tests passed, ~1.6 s |
| `npm run platform:typecheck` | **PASS** | TypeScript emitted no diagnostics |
| `npm test` | **PASS** | 50 files, 177 tests passed, ~1.6 s |
| `npx tsc --noEmit` | **PASS** | TypeScript emitted no diagnostics |
| `npm run lint` | **PASS (0 errors, 73 warnings)** | store-getter check: no anti-patterns found |

## Deterministic acceptance coverage

The deterministic Manual Sequence smoke (TP-H06) and the paired comparison
browser smoke (TP-H07) are part of the Test Platform suite (CMD 1) and both
pass. They exercise the full public run lifecycle — launch, SSE, replay,
comparison classification, gate evaluation, cancellation, and restart recovery
— without any external model, API key, or target deployment.

## Warning classification

### W1: `react-hooks/exhaustive-deps` (73 warnings, 0 errors)

`npm run lint` returns exit 0 with 73 existing
`react-hooks/exhaustive-deps` warnings across `apps/` and `os/`. This is
unchanged from the TP-H00 record ([Warning classification](
2026-07-11-tp-h00-regression-baseline.md#warning-classification)) and is
classified as **existing warning debt** owned by the **Simulator/App layer**
(`apps/`, `os/`). The store-getter check reports no anti-patterns.

### W2: `StarletteDeprecationWarning` (httpx2)

FastAPI's TestClient emits one `StarletteDeprecationWarning` about `httpx2`.
This is unchanged from TP-H00 ([Non-blocking Python warnings](
2026-07-11-tp-h00-regression-baseline.md#non-blocking-python-warnings)) and is
classified as **accepted upstream compatibility debt** owned by the
**Starlette/FastAPI** dependency. It does not affect test results.

## TP-H00 blocker resolution

All four blockers were resolved by repair commit `972fba6` ("harden test
platform regression baseline"), verified in a clean Python 3.11 / Windows CP936
environment per the [TP-H00 blocker repair evidence](
2026-07-11-tp-h00-blocker-repair.md). They are not environment-dependent
workarounds.

| TP-H00 blocker | Status | Resolved by |
|---|---|---|
| B01: undeclared `requests` runtime dependency | Resolved | `972fba6` declared it in `bench_env/requirements.txt` |
| B02: `TargetRepository.list` shadows `list` on Python 3.11 | Resolved | `972fba6` added `from __future__ import annotations` to `repositories.py`, enabling postponed annotation evaluation; verified on Python 3.11 |
| B03: undeclared `pytest-asyncio` test dependency | Resolved | `972fba6` declared it in `bench_env/requirements.txt` |
| B04: Windows encoding in synthetic module writes | Resolved | `972fba6` added `encoding="utf-8"` to all `write_text` calls in `test_registry.py`; verified on Windows CP936 with UTF-8 mode disabled |

No live model result is claimed by this record.

## Redaction

No API keys, authorization headers, model credentials, private endpoints, or
local machine paths beyond the project root are included in this record.

## Gate verdict

The P1 hardening baseline is **green**. All TP-H00 commands pass at `00da2cb`.
P2 work (TP-H09+) may proceed.
