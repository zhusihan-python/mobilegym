# TP-DF01-R1 Live-Provider Follow-up Retest - 2026-07-18

## Retest contract

| Field | Value |
|---|---|
| Task | `TP-DF01-R1` |
| Status | Complete - passed |
| Purpose | Recheck the one real-provider Console path that blocked TP-DF01 |
| Base revision | `0a77a5e66cc4d0f5cd8f12d04e95e9a4516d7be9` (`TP-DF02: fix credentialed follow-up console`) |
| Environment role | Fresh scratch database and Run root |
| Production composition | Required |
| Live provider | GLM-5V-Turbo through its OpenAI-compatible endpoint |
| Authorization | Targeted dogfood, transient provider credential use, scratch mutation/cleanup, evidence, and local commit |
| Not authorized | Push, deploy, production-data changes, or broader feature changes |

## Acceptance results

- [x] Normal `test_platform.main` uses the production compatibility probe and
  real executor against the live provider.
- [x] A credential-bound Run Plan v2 Run records a retryable Attempt 1
  execution error without changing its frozen Lane Binding.
- [x] The terminal Run page renders an initially empty transient
  `Model API key` input from the frozen execution snapshot.
- [x] An empty-key Console Retry is blocked without creating a Run Attempt.
- [x] Supplying the transient key through the Console queues Attempt 2 through
  the public Retry endpoint.
- [x] Attempt 2 records a live compatible preflight and completes successfully.
- [x] Target Revision, Execution Profile Revision/hash, and Lane fingerprint
  are identical across preview, Run, Retry, and final report provenance.
- [x] The credential value and private Credential Reference locator are absent
  from local storage, public APIs, Run artifacts, reports, and Baselines.
- [x] The isolated browser, API, Vite process, scratch database, Run artifacts,
  and temporary directories are closed or removed after evidence capture.

## Targeted runtime path

```text
fresh production stack
  -> credential-bound published GLM profile
  -> Console Single launch
  -> isolated execution-browser termination
  -> retryable Attempt 1 ERROR
  -> Console transient key input
  -> Console Retry
  -> live compatible Attempt 2 PASS
  -> provenance and secret scans
  -> exact cleanup
```

The provider credential is not recorded in this document.

## Scratch objects and frozen identity

| Object | Evidence |
|---|---|
| Project | `c2cdff8ced874737ad00aa09c3e3e114` |
| Target | `af66e02ff3fa4a3eaf81edaa357fbff8` |
| Target Revision | `fb2a0c8ddf92437b97cc4fc845087953` |
| Target Revision hash | `87ebc21caf27258f1d6627bc7f3a6fe7332202e8c660032c46eff6445db2c947` |
| Workflow | `0d1dd25601d7472385bbe05de055edce` |
| Workflow Version | `ca9d31db80df43c4b458ec7a644867d6` |
| Execution Profile | `87239a3f2d8a41879aeb3f1be7fcbe88` |
| Execution Profile Revision | `10c2fc5a64fd40199ed6f5303d64799f` |
| Execution Profile public hash | `sha256:d004db75fdb78e2df2c8cec2979ebf1d8edc2801bfd353b68062c6003a446015` |
| Execution Profile Revision hash | `sha256:c68fd088be8d8d1857bbfb7dc55fb0ea922b879d28e7049dc8ff76e2e8a785ae` |
| Candidate Lane fingerprint | `sha256:99b23f6f34eb17d0c5d97d01ccd771294fc035978ae5206968818c6087ab02cd` |
| Run | `53574d1e62c44a7ebc7df3de448ae28f` |
| Run Plan fingerprint | `sha256:1365f7c5d1b5cc5e0bd11bce1b613c05f9970c09253735303e15304b32e46e7a` |

The target was executable. Its only advisory warning was that the Simulator
data revision was unpinned; this did not affect the credentialed follow-up
contract.

## Attempt results

| Attempt | Trigger | Run Attempt ID | Compatibility | Episode result | Result |
|---|---|---|---|---|---|
| 1 | Initial Console launch | `48b0a80f7b6044c2883e2546252eb47d` | Live `passed` / `compatible`, GLM, `data_url`, uncached, 740 ms | `d337fc90d32c40c3af0e6a4a4e32b56f` | `ERROR` / `EXECUTION_ERROR` after terminating only the scratch execution browser |
| 2 | Console Retry | `45c96b369f2b41a49c6efc9850761130` | Live `passed` / `compatible`, GLM, `data_url`, uncached, 618 ms | `24639491255545bebf7ea579ad2e93cf` | `PASS` |

Attempt 1 completed with one retryable error. Its Retry preview selected exactly
one candidate episode with reason `retry_error` and displayed the frozen Target
Revision, Execution Profile Revision, and Lane fingerprint above.

On the terminal Run page, the transient key field was initially empty. Clicking
Retry without a key showed `Model API key is required for retry/resume because
run secrets are not persisted.` The public Run still contained one attempt.
Entering the key only in that field then queued Attempt 2 for the one selected
episode. While the retry ran the input disappeared; after completion and reload,
the rendered field was empty again.

The final Run state was `completed` with `pass=1`, `fail=0`, `error=0`,
`cancelled=0`, and `incomplete=0`.

## Report and provenance

| Field | Result |
|---|---|
| Report ID | `326325ac9cae4fa5896f668981cf1a14` |
| Schema | v3 |
| Selected Run Attempt | `45c96b369f2b41a49c6efc9850761130` (Attempt 2) |
| Functional summary | 1 success, 0 fail, 0 error; success rate 1.0 |
| Frozen identity | Exact Target/Profile revision IDs, hashes, and Lane fingerprint from the launch preview |

No new Baseline was created because this was a targeted Retry regression retest,
not a repeat of the already-passed TP-DF01 Baseline scenario. The public Baseline
collection was nevertheless included in the credential scan.

## Secret and residue checks

| Surface | Check | Result |
|---|---|---|
| Console local storage | Credential shape and private reference marker | Absent; only selected Project ID remained |
| Public APIs | Profile collection, Run, report, and Baseline collection | Credential shape and private reference marker absent |
| Scratch runtime | Credential-shape scan across database and Run root | Absent |
| Run artifacts | Private Credential Reference marker and transient locator across 120 files | Absent |
| Repository | Credential-shape scan before commit | Passed |

The key was supplied only through transient Console inputs for launch and Retry.
The private Credential Reference was never made public or copied into Run
artifacts.

## Cleanup

- The isolated ego-browser task space was completed with `keep: false`.
- The production API and Vite processes exited with `SIGINT`.
- TCP ports `18789` and `15175` had no listeners after shutdown.
- `/tmp/tp-df01-r1-runtime`, including the scratch SQLite database and Run root,
  was removed and its absence was rechecked.

## Disposition

TP-DF01-R1 passes. The TP-DF02 Console fix is confirmed against a real
credential-bound GLM execution: an empty-key follow-up fails closed without
creating an attempt, and a transient-key Retry preserves frozen execution
identity and completes successfully. This resolves the sole blocking TP-DF01
finding, so TP-DF01 can be marked **passed** without another dogfood run.
