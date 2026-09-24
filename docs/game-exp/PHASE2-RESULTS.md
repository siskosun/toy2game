# game-exp Phase 2 request-core results

Repository: `siskosun/toy2game`  
Protocol baseline: V1.3 / Phase 1 `PROTOCOL_FREEZE_APPROVED`  
Validated main SHA: `e654b651fcd8072b3136a76678c0f32f054733fe`

## Decision

**REQUEST_CORE_READY**

The minimal trusted request layer is ready for the next implementation stage. CLI requests no longer write authoritative Git refs directly: they submit a stable request envelope to the protected Trusted Writer, keep only a local recovery journal, and resolve final state from the protected remote Ledger.

This is **not yet the full Trusted Domain Core**. A `COMMITTED` request currently proves that the request record is durably and correctly committed to the Ledger. It does not yet mean that domain operations such as experiment creation, decision transitions, archive, Candidate, or Review have been semantically applied.

## Implemented

- restricted canonical JSON shared by the client and Trusted Writer;
- stable `request_id`, payload digest and expected-Ledger-head binding;
- explicit result states:
  - `ACCEPTED`
  - `COMMITTED`
  - `CONFLICT`
  - `UNKNOWN`
  - `REJECTED`
- dispatch uncertainty is `UNKNOWN`, never silently treated as a rejection;
- local non-authoritative request journal under the Git common directory;
- same request ID + same payload reuses the original expected head;
- same request ID + different payload is rejected;
- already-committed replays resolve from the remote Ledger without dispatching another writer job;
- `status`, `doctor`, `request`, and `reconcile` CLI entry points;
- client never directly pushes `game-exp/ledger`;
- Trusted Writer executes only protected control source pinned to the workflow source SHA.

## Automated evidence

| Gate | Result | Evidence |
|---|---|---|
| Phase 2 core tests on Linux/Windows/macOS | PASS | https://github.com/siskosun/toy2game/actions/runs/35952207986 |
| Shared-core Trusted Writer regression | PASS | https://github.com/siskosun/toy2game/actions/runs/35952248853 |
| Status success exit contract | PASS | PR core tests, including run 35952676240 |
| Remote-idempotent client replay tests | PASS | PR core tests, including run 35952682841 |
| Real request submission | PASS | client E2E run 35952822864 |
| Reconcile to protected remote Ledger | PASS | client E2E run 35952822864 |
| Idempotent replay without Ledger-head movement | PASS | client E2E run 35952822864 |
| E2E evidence artifact | PASS | artifact `game-exp-client-e2e-35952822864`, SHA-256 `dff011fb66fc85c19a5023532cff90caa03a4ffcf4fc7b12cac561f656b522a8` |

The successful E2E request ID is `req-client-e2e-35952822864`.

## Failure-driven fixes

The E2E sequence deliberately exposed and fixed several real integration problems before this status was approved:

1. Hosted-runner `doctor` cannot read admin-only Deploy Key / repository-secret settings with its restricted token. Those checks are therefore `UNKNOWN`, not false `FAIL`; authoritative Ledger and Ruleset checks still pass.
2. `status` originally returned valid data but no explicit `status: PASS`, causing a non-zero CLI exit code in strict shell pipelines. This is fixed and regression-tested.
3. A committed request replay originally caused an unnecessary second Trusted Writer dispatch. The client now reads the protected Ledger first and returns `COMMITTED / replayed=true` without another dispatch.
4. Local GitHub connectivity on the Windows development machine repeatedly stalled for ~21 seconds. The client now records the request before dispatch and maps an uncertain dispatch outcome to `UNKNOWN`, preserving the original request ID and expected head for safe retry.

## Current trust boundary

```text
Codex / CLI / future MCP
          |
          v
  game-exp request client
          |
          v
 GitHub workflow dispatch
          |
          v
 Trusted Writer + Deploy Key
          |
          v
 protected game-exp/ledger
```

The local journal is recovery metadata only. GitHub's protected Ledger remains authoritative.

## Phase 2B: Codex MCP evidence

The minimal MCP adapter is now real, not a design-only layer. It uses the official Python MCP SDK v2 over stdio and delegates to the same validated request client; it does not gain direct Git authority.

| Gate | Result | Evidence |
|---|---|---|
| MCP SDK adapter tests on Linux/Windows/macOS | PASS | GitHub Actions run 35953142760 |
| Core tests remain independent from optional MCP SDK | PASS | GitHub Actions run 35953142763 |
| Codex discovers game-exp MCP server/tool | PASS | Codex 0.155.1 emitted an MCP tool call for `game_exp_status` |
| Codex read chain | PASS | `game_exp_status` returned repo `siskosun/toy2game` and authoritative Ledger head |
| Codex write-request chain | PASS | request `req-codex-mcp-probe-20260924-1158` |
| Codex request accepted -> remote reconciliation | PASS | Trusted Writer run 35953652747 -> `COMMITTED` |
| Codex MCP transport-probe digest | PASS | `sha256:1f0601fd998ad8f8f5f941af4a70c4af0d95f49415a29c2cdd531c7302b26c35` |

The current MCP surface is deliberately narrow:

- `game_exp_status`
- `game_exp_doctor`
- `game_exp_request_get`
- `game_exp_request_submit`

A submitted request is still an operation envelope. `ACCEPTED` is not domain execution, and `COMMITTED` currently proves durable request-record commitment, not that an experiment lifecycle mutation has been applied.

### Codex panel capability spike

On the tested Windows machine, Codex CLI 0.155.1 reports:

- `apps`: stable / true
- `enable_mcp_apps`: under development / false
- `codex_apps_mcp_2026_07_28`: under development / false
- `mcp_2026_07_28`: under development / false

Decision: **TOOLS_ONLY_FOR_NOW**.

Do not build the production Experiment Board as an MCP Apps iframe yet. Preserve headless tools and complete the Trusted Domain Core first. A future UI can consume the same domain projection once the relevant Codex host capability is stable and enabled.

## Next gate

The next stage is **Trusted Domain Semantics**, not UI.

Port the frozen Phase 1 semantic rules into the trusted execution path operation-by-operation so that the trusted side validates and applies:

1. experiment binding / creation;
2. lifecycle decisions;
3. Candidate + Review bindings;
4. archive Prepare / Claim / Abort / Commit;
5. retention and rehearsal references.

Only after those mutations are controlled by the trusted semantic core should domain-specific MCP tools such as `experiment_create`, `decision_submit`, and `archive_experiment` be exposed.

The generic request MCP transport is already validated as the current Harness entry point. Experiment Board / Codex panel work remains out of scope until domain semantics exist and the Codex MCP Apps host capability is stable.
