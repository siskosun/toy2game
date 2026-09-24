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

## Next gate

The next stage is **Trusted Domain Semantics**, not UI.

Port the frozen Phase 1 semantic rules into the trusted execution path operation-by-operation so that the trusted side validates and applies:

1. experiment binding / creation;
2. lifecycle decisions;
3. Candidate + Review bindings;
4. archive Prepare / Claim / Abort / Commit;
5. retention and rehearsal references.

Only after those mutations are controlled by the trusted semantic core should MCP request tools become the primary Harness entry point.

Experiment Board / Codex panel work remains out of scope for this stage.
