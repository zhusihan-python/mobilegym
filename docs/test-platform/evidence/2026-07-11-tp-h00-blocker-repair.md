# TP-H00 Blocker Repair Evidence - 2026-07-11

## Status

| Field | Value |
|---|---|
| Scope | Repair blockers discovered by TP-H00 |
| Status | Complete; both required Python suites pass from declared dependencies in a clean environment |
| Base revision | `c61fedda8dd4c7604ce10681f6cabd23aa9d675f` |
| Repair commit | `972fba6` |
| Branch | `main` |
| Recorded at | `2026-07-11T17:09:00+08:00` |
| Live model required | No |

This record supplements, but does not rewrite, the failed baseline captured for
the unmodified base revision in
[`2026-07-11-tp-h00-regression-baseline.md`](2026-07-11-tp-h00-regression-baseline.md).

## Repairs

| Blocker | Repair |
|---|---|
| Undeclared `requests` runtime dependency | Added `requests>=2.31,<3` to `bench_env/requirements.txt` |
| Undeclared `pytest-asyncio` test dependency | Added `pytest-asyncio>=0.24,<2` to `bench_env/requirements.txt` |
| `TargetRepository.list` shadows built-in `list` during annotation evaluation | Enabled postponed annotations in `test_platform/persistence/repositories.py` |
| Synthetic Python modules use the Windows default encoding | Added explicit `encoding="utf-8"` to all synthetic source writes in `bench_env/tests/common/test_registry.py` |
| Newly exposed missing `Any` import | Imported `Any` in `test_platform/api/routes/workflows.py` |

No assertion was weakened, skipped, or rewritten. No execution behavior was
changed beyond making the affected modules importable and the test fixtures
platform-independent.

## Verification

A new isolated Python 3.11 environment was created after repair commit
`972fba6`. Its dependencies were installed and resolved only from
`test_platform/requirements-dev.txt` and `bench_env/requirements.txt`.
Re-running `pip install` against both declarations reported every requirement
satisfied, and `pip check` passed.

| Command / seam | Result |
|---|---|
| Import `TargetRepository` under Python 3.11 | PASS |
| `pytest ... bench_env/tests/common/test_registry.py -q` with locale encoding `cp936` and UTF-8 mode disabled | PASS: 15 tests in 1.66 s |
| `pytest -c bench_env/tests/pytest.ini bench_env/tests/common -m "not live" -q` | PASS: 226 tests in 33.73 s |
| `pytest -c test_platform/pytest.ini test_platform/tests -q` | PASS: 267 tests in 65.56 s; one existing Starlette/httpx2 deprecation warning |

The Test Platform suite used a short writable pytest `--basetemp` under the
workspace. A first run from the much longer task-isolation path produced Windows
`WinError 206` trajectory-path failures; rerunning from the short path made all
267 tests pass without source changes. This is classified as an environment
path-length limitation, not a product failure.

## Worktree hygiene

All generated pytest temporary directories were removed after verification. The
only remaining untracked content was the pre-existing user-owned `.agents/`
directory, which was not modified or committed. No secrets, credentials,
private endpoints, or absolute local-machine paths are recorded in this
evidence.
