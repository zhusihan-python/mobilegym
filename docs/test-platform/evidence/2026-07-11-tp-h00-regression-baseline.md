# TP-H00 Current Regression Baseline Evidence - 2026-07-11

## Status

| Field | Value |
|---|---|
| Task | `TP-H00` |
| Status | Complete evidence / failed baseline: two undeclared dependencies, one Python 3.11 source defect, and one Windows test-infrastructure defect; Node/TypeScript checks passed |
| Source revision | `c61fedda8dd4c7604ce10681f6cabd23aa9d675f` |
| Branch | `main` |
| Recorded at | `2026-07-11T16:38:00+08:00` |
| Live model required | No |

This evidence-gathering task is complete. Both required Python commands were
attempted and every observed failure was reproduced and classified. The results
fail the Milestone D regression baseline and must not be used to claim a passing
baseline.

The subsequently authorized blocker repair and its passing working-tree
verification are recorded separately in
[`2026-07-11-tp-h00-blocker-repair.md`](2026-07-11-tp-h00-blocker-repair.md).

## Worktree

The source revision was tested with a dirty, documentation-only working tree:

```text
 M docs/README.md
?? .agents/
?? docs/test-platform/HARDENING_PLAN.md
?? docs/test-platform/HARDENING_PRD.md
?? docs/test-platform/PRODUCT_BACKLOG.md
```

`.agents/` was pre-existing user-owned untracked content and was not modified by
this task. The other listed changes are the Test Platform planning documents
created immediately before TP-H00. No product runtime source was modified before
or during the available checks.

Git also reported read-permission warnings for global ignore configuration and
existing `.pytest_cache` directories. Those warnings did not change the source
revision or the Node/TypeScript command results.

## Environment

| Dependency | Version / location |
|---|---|
| Operating system | `Microsoft Windows NT 10.0.26200.0` |
| PowerShell | `5.1.26100.8655` |
| Node.js | `v24.14.1` |
| npm | `11.16.0` via `npm.cmd` |
| Selected Python | `3.11.15` |
| Python environment | Isolated virtual environment outside the repository |
| Key installed Python packages | FastAPI `0.139.0`, Pydantic `2.13.4`, pytest `8.4.2`, OpenAI `2.24.0`; diagnostic-only `requests` `2.34.2` and `pytest-asyncio` `1.4.0` |
| Python dependency integrity | `pip check` passed |

The Python executable found first on PATH was MSYS2 Python `3.14.3`. It was not
used. The regression commands use the isolated Python 3.11 environment above,
into which the user installed the declared Test Platform and `bench_env`
dependencies. The environment itself is internally consistent according to
`pip check`.

## Dependency fingerprints

| File | SHA-256 |
|---|---|
| `package-lock.json` | `f27805845e7909d330ac85a4a5e5695cf13938c5122ef7c8be632b60c158d770` |
| `test_platform/requirements.txt` | `0bc34b1ca760460624fcfe3e22aa7e5190b06574bcacf3b5e6e8f8e1cd513869` |
| `test_platform/requirements-dev.txt` | `1fbea6233bc81fbe54ef9018371aeda3c499935714af05284c8e0c803a2d8700` |
| `bench_env/requirements.txt` | `95dc115085cdae7cae6e7f850f16587a1fdb53eff6ba19a85b946f73d0b22009` |

## Results

| Command | Result | Evidence |
|---|---|---|
| `npm run platform:test` | PASS | 18 test files, 54 tests passed; 0 failed; exit 0; 21.6 s shell duration |
| `npm run platform:typecheck` | PASS | TypeScript emitted no diagnostics; exit 0; 7.4 s |
| `npm test` | PASS | 50 test files, 177 tests passed; 0 failed; exit 0; 7.0 s shell duration |
| `npx tsc --noEmit` | PASS | TypeScript emitted no diagnostics; exit 0; 44.7 s |
| `npm run lint` | PASS WITH WARNINGS | 0 errors, 73 warnings, store-getter check passed; exit 0; 32.2 s |
| `python -m pytest -c test_platform/pytest.ini test_platform/tests -q` | FAIL (COLLECTION) | After diagnostic-only installation of `requests`, 34 collection errors in 3.03 s, all caused by the `TargetRepository` Python 3.11 annotation name collision; no tests ran |
| `python -m pytest -c bench_env/tests/pytest.ini bench_env/tests/common -m "not live" -q` | FAIL | After diagnostic-only dependency installation and use of a writable pytest temp directory: 222 passed, 4 failed in 33.70 s; all failures are Windows default-encoding defects in `test_registry.py` synthetic module generation |
| `PYTHONUTF8=1 python -m pytest ... bench_env/tests/common/test_registry.py -q` | DIAGNOSTIC PASS | 15 passed in 1.68 s; confirms the four official-command failures are encoding-sensitive test infrastructure rather than registry behavior |

## Warning classification

`npm run lint` returned exit code 0 with 73 existing
`react-hooks/exhaustive-deps` warnings across `apps/` and `os/`. It returned zero
errors, and `scripts/lint_store_getters.mjs` reported no store-getter
anti-patterns. TP-H00 records these as existing warning debt; it does not modify
unrelated App or OS code to remove them.

## Python collection blockers

The Python suites were rerun with the pytest cache provider disabled to separate
source failures from the existing inaccessible `.pytest_cache` directories.
After the user installed `requests` and `pytest-asyncio` as diagnostic-only
environment overrides, Test Platform collection still failed with 34 errors and
one Starlette/httpx2 deprecation warning. The `bench_env` suite completed 226
test bodies, with 222 passing and four failing.

### TP-H00-B01: undeclared `requests` runtime dependency

`bench_env/task/utils.py:5` imports `requests` at module import time, and
`url_content_contains()` calls `requests.get()` at line 548. `requests` is not
declared by `bench_env/requirements.txt`, `test_platform/requirements.txt`, or
`test_platform/requirements-dev.txt`; consequently installing all declared
requirements does not produce a collectable test environment. The observed
exception is:

```text
ModuleNotFoundError: No module named 'requests'
```

This is classified as a dependency-manifest product defect, not merely a local
environment installation omission. The user installed `requests 2.34.2`
manually as a diagnostic step; `pip check` remained clean. This exposed the
remaining source blocker in Test Platform and the next dependency-manifest
blocker in `bench_env`, but it does not make the recorded source revision
reproducible from its declared dependencies.

### TP-H00-B02: `TargetRepository.list` shadows built-in `list`

`test_platform/persistence/repositories.py` does not enable postponed annotation
evaluation. Within `TargetRepository`, `def list(...)` is defined at line 208;
the later `record_revision()` annotation `warnings: list[str]` at line 252 is
therefore evaluated against the class-local method rather than the built-in
generic on Python 3.11. The observed exception is:

```text
TypeError: 'function' object is not subscriptable
```

This is classified as a source-code product defect and a collection blocker.
TP-H00 records it without modifying runtime code.

### TP-H00-B03: undeclared `pytest-asyncio` test dependency

After `requests` was installed diagnostically, loading
`bench_env/tests/conftest.py` reached its top-level `import pytest_asyncio` at
line 22 and failed with:

```text
ModuleNotFoundError: No module named 'pytest_asyncio'
```

The same file uses `@pytest_asyncio.fixture` at line 47, and the task testing
guide lists `pytest-asyncio` as a prerequisite, but `bench_env/requirements.txt`
does not declare it. Neither Test Platform requirements file declares it. This
is classified as a second dependency-manifest product defect. The user installed
`pytest-asyncio 1.4.0` diagnostically; `pip check` remained clean and the suite
then collected and ran, but the source revision remains unreproducible from its
declared requirements.

### TP-H00-B04: synthetic Python modules use the Windows default encoding

Four `bench_env/tests/common/test_registry.py` tests failed:

- `TestLoading::test_legacy_loads_all_classes`
- `TestLoading::test_hybrid_merges_both_layouts`
- `TestLoading::test_duplicate_across_layouts_raises`
- `TestLoading::test_reexport_is_ignored`

The `_make_suite()` helper writes synthetic Python modules through
`Path.write_text(...)` at lines 119, 121, 125, and 127 without an explicit
encoding. The selected Python reports locale encoding `cp936` and UTF-8 mode
disabled. A non-ASCII em dash in `_LEGACY_TASKS_PY` is therefore written as
CP936 bytes, but Python source import decodes the generated file as UTF-8 and
raises `SyntaxError` on byte `0xA1`.

As a diagnostic control, the complete `test_registry.py` file was rerun with
`PYTHONUTF8=1`; all 15 tests passed. This classifies the four official-command
failures as a cross-platform test-infrastructure defect, not a `TaskRegistry`
product behavior failure. TP-H00 records the defect without modifying tests.

### Non-blocking Python warnings

- FastAPI's TestClient emitted one `StarletteDeprecationWarning` about `httpx2`.
- The original pytest invocation could not write existing repository-local
  `.pytest_cache` directories. The cache-disabled reproduction proves this is
  not the cause of the 35 collection errors.
- pytest-xdist could not use the sandboxed Windows account's default temporary
  directory. Pointing `--basetemp` at a writable task-local directory allowed
  all 226 `bench_env` tests to execute; this is an environment limitation rather
  than a test failure.

## Outcome and required follow-up

TP-H00 evidence gathering is complete, but this revision has no passing strict
baseline. Before TP-H01 begins, a separately authorized repair task must:

1. declare the runtime `requests` dependency;
2. declare the `pytest-asyncio` test dependency;
3. correct the `TargetRepository.list` annotation name collision for Python
   3.11;
4. make synthetic Python source writes explicitly UTF-8;
5. rerun both Python suites from declared dependencies and without relying on
   `PYTHONUTF8=1`.

The regression baseline cannot be recorded as passing unless every required
suite passes from declared dependencies. Overall gate verdicts must not override
this fact.

## Redaction

No API keys, authorization headers, model credentials, private endpoints, or
prompt contents are included in this record.
