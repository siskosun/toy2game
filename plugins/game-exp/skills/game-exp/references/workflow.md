# game-exp workflow reference

## Lifecycle

Canonical lifecycle:

`ACTIVE -> REVIEW -> PROMISING -> SELECTED -> INTEGRATED -> ARCHIVED`

Additional paths:

- `REVIEW -> ACTIVE`
- `REVIEW -> REJECTED`
- `PROMISING -> ACTIVE`
- `PROMISING -> REJECTED`
- supported non-terminal lifecycles may be archived through the dedicated Archive protocol

`INTEGRATED` and `ARCHIVED` are reserved for dedicated trusted operations, not generic lifecycle decisions.

## Tool map

| User intent | Preferred MCP tool | Notes |
|---|---|---|
| Inspect repo/Ledger | `game_exp_status` | Read-only |
| Inspect experiment | `game_exp_experiment_get` | Read-only authoritative projection |
| Diagnose trust/archive health | `game_exp_doctor` | Read-only |
| Bind Manifest | `game_exp_experiment_bind` | Manifest operation id is idempotency key |
| Initialize source refs | `game_exp_initialize` | Idempotent trusted workflow |
| Build Candidate | `game_exp_candidate_build` | Build/observe/attest/retain/register |
| Record human review | `game_exp_review_record` | Human outcome only; trusted GitHub actor rechecked |
| Change lifecycle | `game_exp_decision_submit` | Human gate for PROMISING/SELECTED/REJECTED |
| Latest-main rehearsal | `game_exp_rehearse` | Scope-filtered, trusted tree recompute |
| Create Integration PR | `game_exp_integrate` | Does not itself mean INTEGRATED |
| Finalize merged Integration PR | `game_exp_integrate_finalize` | Verifies merged tree/ancestry |
| Recoverable Archive | `game_exp_archive` | `ATOMIC_DELETE` or `RETAIN_BRANCH` |
| Abort PREPARED Archive | `game_exp_archive_abort` | Forbidden after Claim |
| Reconcile request | `game_exp_request_get` | Use for ACCEPTED/UNKNOWN/lost response |
| Low-level request | `game_exp_request_submit` | Recovery/unsupported cases only; prefer domain tools |

## Human-owned gates

The agent must not decide these from automated evidence:

1. Review PASS/FAIL.
2. Promotion to PROMISING.
3. Selection to SELECTED.
4. Rejection when the user has not explicitly made that decision.
5. Archive branch-retention choice when the user has not made the destructive intent clear.

## Trust boundaries

- Protected Ledger is authoritative.
- Trusted Writer independently resolves GitHub identity/permissions for human-gated operations.
- Candidate required checks must be trusted-observed.
- PROMISING rechecks current immutable Candidate retention.
- SELECTED rechecks current successful Rehearsal and Candidate retention.
- Integration finalization verifies merged PR tree against trusted Rehearsal tree.
- Archive finalization requires the recoverable Archive state machine and verified refs.
- The Experiment Board, Issue labels, and future UI are projections only.
