# TP-DF01 Real-Provider Dogfood - 2026-07-18

## Dogfood status

| Field | Value |
|---|---|
| Task | `TP-DF01` |
| Status | **Blocked by a Console defect** |
| Base revision | `c56052776085ac0369f644cdeff5680f292a464c` (`TP-EP10: close execution profiles release`) |
| Source revision | documentation commit containing this evidence |
| Branch | `main` |
| Recorded at | `2026-07-18` |
| Production composition | Yes: `test_platform.main`, `OpenAICompatibilityProbe`, real executor, real browser, real provider |
| Live provider | GLM-5V-Turbo through the OpenAI-compatible endpoint documented by the provider |
| Secret disposition | Operator-provided API key was transient and is intentionally absent from this record |

The real provider, execution, report, Baseline, persistence, and frozen Retry
identity paths worked. TP-DF01 does not pass as an operator workflow because
the Console hides the transient API-key input required by credential-bound Run
Plan v2 Retry/Resume. The backend correctly rejects the secretless request.

This finding does not invalidate the deterministic TP-EP10 release gate. It is
a live-provider usability and integration defect that the deterministic fake-
probe scenario did not expose.

## Runtime and scope

The dogfood used isolated temporary state and normal production composition:

- API: `python -m test_platform.main`, port `18788`, fresh SQLite database and
  Run root;
- Console and simulator: Vite on port `15174`;
- provider base URL: `https://open.bigmodel.cn/api/paas/v4`;
- model: `glm-5v-turbo`;
- image format: `data_url`;
- workflow: schema-v2 Single with the `candidate` Lane slot;
- task: `account.Railway12306ChangePassword`;
- target health: passed through the real browser metadata probe;
- browser: an isolated `ego-browser` task space.

The user-facing provider contract was checked against
<https://docs.bigmodel.cn/cn/guide/models/vlm/glm-5v-turbo>. No fake
Compatibility Probe or deterministic executor was injected.

## Frozen inputs

| Object | Identity |
|---|---|
| Project | `e627f04d235c4b399ec9e07930adcbb0` (`TP-DF01 Real Provider Dogfood`) |
| Workflow version | `13cc576ffa954f57a4c5feb485c3c2ae` (`TP-DF01 Real Provider Single`) |
| Target | `1d5111819d4f4a0dabdd7fd5177af4d7` (`TP-DF01 Live Simulator`) |
| Target Revision | `538302b1367a44de98620b99a9d99189` |
| Target Revision hash | `c6685470420d1ab977dea2a1184587e2837f093b8ce48d89b62a49ccc74e96dc` |
| Execution Profile | `3f7bc0a5a9b94547b9f06f6d81563035` (`TP-DF01 GLM-5V-Turbo Online`) |
| Execution Profile Revision | `f7e08ac508804e298e7247875aee95d0` |
| Public profile hash | `sha256:d004db75fdb78e2df2c8cec2979ebf1d8edc2801bfd353b68062c6003a446015` |
| Revision hash | `sha256:0b0220468939052621843819783343d920b094bf109a216ce2b4e6f3615c8bb2` |
| Lane fingerprint | `sha256:b2b16b76555b80540dd93d7d403fd76930bb0561ca86bda190fa240fed284d26` |

The Target reported simulator build `dev-unversioned` and data revision
`unpinned`. That is recorded as an environment warning, not treated as strict
release evidence for simulator content.

## Successful live-model path

Run `19e49438a1ee4a27bb8aaed64323d2c2`
(`TP-DF01 GLM-5V-Turbo Single`) completed through the Console:

| Check | Observed result |
|---|---|
| Compatibility Preflight | `passed / compatible / Live`, screenshot accepted, `578 ms` |
| Execution | `PASS`, 1/1 planned Lane episode, 13 agent steps |
| Functional report | 1 success, 0 failure, 0 error, success rate `1.0` |
| Report | schema v3, ID `838b7009564443f3a07550640184934e` |
| Completed Run Attempt | initial Attempt 1, ID `6e53a721a9e3490db76e48af20c10cca` |
| Frozen provenance | exact Target Revision, Profile Revision, revision hashes, and Lane fingerprint |
| Strict Baseline | promoted successfully |

The task changed the Railway12306 password and the rule judge accepted the
final simulator state. The report provenance selected the same completed Run
Attempt and repeated the exact frozen identity above.

Baseline `2cb3e0871a5643258a3f7d162946f494`
(`TP-DF01 GLM-5V-Turbo Baseline`) retained:

- source Run `19e49438a1ee4a27bb8aaed64323d2c2`;
- source report `838b7009564443f3a07550640184934e`, schema v3;
- strict provenance version 1;
- Profile Revision `f7e08ac508804e298e7247875aee95d0`;
- Run Attempt `6e53a721a9e3490db76e48af20c10cca`;
- the exact Profile Revision hash and Lane fingerprint.

After restarting the API against the same database and Run root, the historical
Run URL, completed state, Profile Revision, Target Revision, Lane fingerprint,
report provenance, strict eligibility, Baseline detail, and Baseline-to-source-
Run link remained readable.

## Real failure and frozen Retry evidence

Run `7e42aab47b1641a3ac1a61a99e2f92ca`
(`TP-DF01 Browser Crash Retry Validated`) used the same live provider and frozen
identity. A newly spawned execution-only Playwright browser process was
terminated after preflight; the user browser, Console, API, and simulator
server were not terminated.

| Attempt | Reason | Preflight | Episode result | Observation |
|---:|---|---|---|---|
| 1 | `initial` | compatible, live, `706 ms` | `ERROR` / `EXECUTION_ERROR` | executor recorded a retryable `TargetClosedError` |
| 2 | `retry` | compatible, live, `565 ms` | `PASS` | public Retry API received the transient secret and completed the task |

The Retry preview selected exactly one Lane episode with reason
`retry_error`. Its Target Revision, Profile Revision, Profile Revision hash,
and Lane fingerprint exactly matched the initial Run.

Attempt 2 was submitted through the same public Retry endpoint only after the
Console failure described below. This isolated the defect to the Console:
the backend accepted the preview token and transient secret, preserved the
frozen identity, ran a new live Compatibility Preflight, and completed. The
final report is schema v3, selects Retry Attempt
`06ab984611d942aa99856f0d161ae326`, and reports one success with complete
profile-aware provenance.

Direct API submission is diagnostic evidence, not a substitute for the broken
operator workflow.

## Blocking Console defect

### Reproduction

1. Complete a credential-bound profile-aware Run Plan v2 Run with a retryable
   episode error.
2. Open the Run detail page and wait for the Retry preview.
3. Confirm that the preview can execute and preserves the exact frozen Lane
   Binding.
4. Observe that the Console does not render the transient `Model API key`
   input.
5. Select `Retry run`.

Observed result: the backend returns `RUN_EXECUTION_SECRET_MISSING`. The Retry
is not queued through the Console.

Expected result: the Console derives the required credential slots from the
frozen public execution snapshot, renders a transient password input, submits
it only in the follow-up request, and clears it after submission.

### Root cause

`runRequiresModelApiKey` in
`web/test-platform/features/runs/RunDetailPage.tsx` looks for
`lane.runner_config.model_api_key_configured`.

The public Run Plan v2 contract instead exposes:

- each Lane's `execution_profile_revision_id` and
  `execution_snapshot_key`; and
- the non-secret declaration
  `execution_snapshots.*.public_spec.credentials.required_slots`, which
  contains `model_api_key` for this profile.

The serialized Lane has `effective_runner_config`, not `runner_config`, and
the public effective configuration intentionally does not contain a secret-
configured flag. The backend correctly reads the frozen execution snapshot's
required slots and fails closed when the transient key is absent.

### Proposed GitHub Issue

Title:

`[TP-DF01] Credentialed Retry/Resume hides API-key input for Run Plan v2`

Suggested labels: `bug`, `needs-triage`.

> *This was generated by AI during triage.*

Acceptance checks:

- profile-aware Retry and Resume derive required transient credential inputs
  from their referenced frozen execution snapshots;
- a required key is rendered for terminal credential-bound Run Plan v2 Runs;
- Retry/Resume submits the key transiently and clears it after the request;
- the key is absent from local storage, public API responses, Run artifacts,
  report provenance, Baselines, and incident URLs;
- credential-free and Legacy follow-ups remain unchanged;
- Console tests cover both Retry and Resume with the public Run Plan v2 shape.

The GitHub connector returned HTTP 403 (`Resource not accessible by
integration`). The signed-in GitHub browser session reached a `Page not found`
screen for the repository's new-Issue URL, and the repository navigation did
not expose Issues. No Issue was created, and repository settings were not
changed. This draft therefore remains the handoff until a maintainer enables
Issues or grants the integration write access.

## Secret-boundary verification

The credential value is not recorded here. Final checks after all Runs and the
Retry completed produced:

| Surface | Result |
|---|---|
| Browser local storage | only `test-platform.run-launch.recent-profile-revision-id` and `test-platform.selected-project-id` |
| Browser local storage secret-shape scan | no match |
| Browser local storage private Credential Reference scan | no match |
| Five public profile/Run/report responses | no secret-shape or private Credential Reference match |
| Complete temporary runtime root secret-shape scan | no match |
| Run artifact root private Credential Reference scans | no online or local locator match |

Only a private Credential Reference locator was persisted in the platform's
private profile storage. It was not present in public profile responses, Run
artifacts, report/Baseline provenance, or browser storage.

## Supplementary observations

| Observation | Disposition |
|---|---|
| A real local AutoGLM model accepted authentication but explicitly rejected screenshot input; Compatibility Check classified the response as generic `indeterminate` | Non-blocking diagnostic-quality follow-up; the online multimodal provider supplied the required release path |
| An online profile with `max_tokens = 1` still completed successfully | Inconclusive failure injection; not counted as Retry evidence |
| Stopping the Vite listener after the simulator page had loaded did not reliably fail the active browser execution | Inconclusive outage injection; not counted as Retry evidence |
| Target simulator data revision was unpinned | Environment warning; pin build/data revisions before using this Run as a simulator-content release baseline |

## Conclusion

The live GLM-5V-Turbo integration, production Compatibility Probe, real visual
execution, report v3, strict Baseline, restart recovery, frozen follow-up
identity, backend Retry, and secret-redaction boundaries all passed.

TP-DF01 remains **blocked** because a normal Console operator cannot Retry or
Resume a credential-bound Run Plan v2 Run. Fix and mechanically cover the
snapshot-based credential requirement in the Console, then rerun the failed
Console step and the final secret scans before marking TP-DF01 complete.
