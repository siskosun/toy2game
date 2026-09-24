# game-exp Phase 2 / Trusted Domain Core results

Repository: `siskosun/toy2game`  
Protocol baseline: V1.3 / Phase 1 `PROTOCOL_FREEZE_APPROVED`  
Validated main SHA at report update: `19c56f0b0e3010a63ee9c2ee20c621c43c3f85b5`

## Decision

**TRUSTED_DOMAIN_CORE_E2E_PASS**

The request transport, Trusted Writer, protected Ledger, domain state machine, Candidate/Review/Rehearsal/Integration/Archive controls, CLI and minimal Codex MCP adapter have all been exercised against the real fork. The pilot experiment EXP-21 completed the full lifecycle and was successfully restored from its immutable archived snapshot after the experiment branch was deleted.

This status means the current V1.3 domain workflow is operational on the target repository. It does **not** mean Codex MCP Apps UI is ready; the tested Codex host still keeps MCP Apps rendering behind disabled under-development feature flags.

## Phase 2A — request transport

Implemented and validated:

- stable `request_id`, payload digest and expected-Ledger-head binding;
- explicit `ACCEPTED / COMMITTED / CONFLICT / UNKNOWN / REJECTED`;
- lost-response recovery from the protected remote Ledger;
- same request + same payload remote-idempotent replay;
- same request + different payload conflict;
- local request journal is recovery metadata only;
- client never writes `game-exp/ledger` directly;
- Trusted Writer runs protected workflow source and uses the dedicated Deploy Key;
- ordinary `GITHUB_TOKEN` cannot update the protected Ledger.

Representative E2E: request `req-client-e2e-35952822864`.

## Phase 2B — Codex MCP transport

The stdio MCP adapter uses the official Python MCP SDK v2 and delegates to the validated request client.

Validated tools:

- `game_exp_status`
- `game_exp_doctor`
- `game_exp_request_get`
- `game_exp_request_submit`

Evidence:

| Gate | Result | Evidence |
|---|---|---|
| MCP SDK adapter tests on Linux/Windows/macOS | PASS | GitHub Actions run 35953142760 and subsequent MCP CI runs |
| Codex discovers and calls game-exp MCP | PASS | Codex CLI 0.155.1 real tool invocation |
| Codex read chain | PASS | `game_exp_status` returned authoritative repo/Ledger state |
| Codex write-request chain | PASS | request `req-codex-mcp-probe-20260924-1158` |
| MCP request reconciles to Ledger | PASS | Trusted Writer run 35953652747 -> `COMMITTED` |

Codex 0.155.1 feature probe:

- `apps`: stable / true
- `enable_mcp_apps`: under development / false
- `codex_apps_mcp_2026_07_28`: under development / false
- `mcp_2026_07_28`: under development / false

UI decision: **TOOLS_ONLY_FOR_NOW**. Production Experiment Board remains deferred until the Codex host exposes a stable MCP Apps surface.

## Phase 2C — Trusted Domain Core

### 1. Experiment Binding

Valid pilot: `EXP-21`.

- GitHub Issue: #21
- request: `req-bind-toy2game-21-20260924`
- Trusted Writer run: `35961077898`
- atomic Ledger commit: `3a5d83745ca2012139ef196c6ef57de784daa0d3`
- request payload digest: `sha256:4b8f569f1faea9e43cd37774db200b7c2ace2d1ef214b74ef7571b55e15d63ad`
- manifest digest: `sha256:5c09a1cb80cb9c2c26153819e7ae2af8b404c89e3bb1064dbee19a62ac8687ba`

The trusted side independently resolves repository id, Issue id/number and parent SHA. Request, binding, reconstructable manifest snapshot and ACTIVE state are committed atomically.

Failure-driven fix: EXP-19 exposed a validator mutation bug that made the stored payload differ from its digest. Validation was made pure and Trusted Writer now recomputes the request digest after domain planning and fails closed if domain code mutates the request.

### 2. Source Initialization

- first initialization run: `35961591104`
- initialization commit: `c8975f9e7e2b5460942d716025719e2a780a74a1`
- frozen Base Tag target: `59b4bbffc3c362f9549884a731097c98812f283e`
- experiment branch later advanced to `7ba6950b9b78bc6c4e78f1a65d63dcd0421021eb`

Verified:

- atomic creation of `exp/21` plus annotated `exp-base/21`;
- deterministic Manifest reconstruction from Ledger;
- replay does not reset a normally advanced experiment branch;
- an existing mismatched branch/tag is never force-overwritten.

### 3. Trusted Candidate

Current Candidate:

- id: `C-21-35966455441-1`
- source SHA: `7ba6950b9b78bc6c4e78f1a65d63dcd0421021eb`
- artifact digest: `sha256:844f1ff264c7e492c2d9498caed07cb53df1fd78e058ecd03898c3d4cd90c774`
- immutable Release: `game-exp-candidate-21-35966455441-1`
- Release id: `395410852`
- asset id: `585347393`

Build, artifact observation, attestation, immutable retention and Candidate registration are separated into trust stages. Project-reported checks cannot satisfy trusted required checks.

### 4. Human Review and PROMISING

Review:

- review id: `req-review-exp21-pass-20260924`
- outcome: PASS
- Candidate/artifact digest exactly match the current Candidate
- reviewer identity resolved from GitHub: `siskosun`, permission `admin`

PROMISING:

- decision: `req-decision-exp21-promising-20260924`
- Trusted Writer run: `35969786199`

PROMISING rechecks the live immutable Release at decision time and freezes Candidate, Review and retention evidence into the Decision event.

### 5. Latest-main Rehearsal and SELECTED

Rehearsal is scope-filtered and run against exact latest main. Trusted finalize independently recomputes the integration tree and anchors it with a protected annotated `exp-rehearsal/*` tag.

The first SELECTED attempt correctly failed after main advanced with:

`DOMAIN_REHEARSAL_CONFLICT — Rehearsal is stale because main advanced`

A fresh latest-main Rehearsal was required. SELECTED subsequently passed only after re-verifying:

- current Candidate;
- current protected Rehearsal tag;
- integration commit/tree and two parents;
- original Rehearsal run completed successfully;
- main still matched the rehearsed main;
- Level-3 Candidate retention was still valid.

Selected decision: `req-decision-exp21-selected-v2-20260924`.

### 6. Trusted Integration

Integration PR: #35.

- Integration id: `I-21-PR-35`
- Candidate: `C-21-35966455441-1`
- Rehearsal: `R-21-35975305672-1`
- Integration PR head SHA: `60690049b622be7edbb636a4db98ae7c2ea35b61`
- trusted Rehearsal tree: `86ced712caa31ca6f967095c9e5fc26a28717181`
- merged main commit: `1dfea26f7d86545928552afddef3b2473b2588d6`
- merge tree: `86ced712caa31ca6f967095c9e5fc26a28717181`
- finalize run: `35975881342`

The merged PR tree exactly equals the trusted Rehearsal tree. Finalization independently verified the PR/merge/main ancestry before setting lifecycle to INTEGRATED.

### 7. Recoverable Archive

Archive id: `A-21-1`  
Mode: `ATOMIC_DELETE`  
Archive workflow run: `35978794796`

Observed state machine:

`PREPARED -> CLAIMED -> REF_COMMITTED -> COMMITTED`

Final result:

- lifecycle: `ARCHIVED`
- lifecycle sequence: 7
- archive lock: cleared
- final tag: `refs/tags/exp-final/21`
- annotated tag object: `44322048b94e19b76357be2e2939e4b8da5f5dac`
- official archived source SHA: `7ba6950b9b78bc6c4e78f1a65d63dcd0421021eb`
- `refs/heads/exp/21`: absent

The final tag metadata binds EXP-21, `A-21-1`, `ATOMIC_DELETE` and the exact source SHA. Ordinary users cannot delete the final tag or recreate `exp/21` because of repository Rulesets.

Archive semantics also cover:

- Abort only while PREPARED;
- Claim permanently disables Abort;
- ref conflicts never become COMMITTED;
- reruns recover the same archive instead of creating a new logical archive;
- RETAIN_BRANCH preserves the official final tag and post-archive branch drift is reported as `WARN / POST_ARCHIVE_BRANCH_DRIFT`.

### 8. Archived cold-handoff verification

Reusable workflow: `game-exp-archive-snapshot-verify.yml`.

EXP-21 run: `35979281530`.

From `exp-final/21`, with the original experiment branch already deleted:

- annotated final-tag resolution: PASS
- exact archived commit checkout: PASS
- canonical Manifest present: PASS
- `npm ci`: PASS
- project tests: PASS
- project build: PASS

This proves the archived snapshot is independently runnable after the active experiment branch is removed.

### 9. Post-Archive Doctor

Real command:

`game-exp doctor --experiment-id EXP-21`

Result: **PASS**.

It verified:

- Ledger ref;
- required Rulesets;
- exactly one write-capable Trusted Writer Deploy Key;
- repository secret;
- Immutable Releases;
- Archive final tag target/metadata;
- ATOMIC_DELETE branch absence.

For RETAIN_BRANCH, later branch movement yields `WARN / POST_ARCHIVE_BRANCH_DRIFT`; the immutable final tag remains the official archived snapshot.

## Repository trust controls

Active controls include:

- protected `game-exp/ledger`;
- PR-only protected `main`;
- `exp/*` cannot be created, deleted or force-updated by ordinary actors;
- `exp-base/*`, `exp-final/*`, `exp-candidate/**/*`, and `exp-rehearsal/**/*` are protected immutable refs;
- protected refs are bypassable only by the dedicated write Deploy Key;
- built-in GitHub Actions token is not a Trusted Writer.

## Current architecture

```text
Codex / CLI / future Skill
          |
          v
game-exp Client / MCP tools
          |
          v
controlled GitHub workflow dispatch
          |
          v
Trusted Domain Core + Trusted Writer
          |
          +--> protected game-exp/ledger
          +--> protected source / candidate / rehearsal / final refs
          +--> immutable Candidate retention
          |
          v
human Review / Decision gates
```

## Remaining productization work

The protocol/domain layer is no longer the main blocker. The next work should improve the Harness experience without weakening the trust boundaries:

1. expose domain-specific MCP tools instead of requiring generic operation JSON;
2. add a game-exp Skill that maps natural-language experiment work to those tools;
3. keep CLI for CI/debug/recovery;
4. defer production Experiment Board UI until Codex MCP Apps is stable and enabled;
5. add the Board later as a projection over the same Ledger/domain APIs, never as the authority.

