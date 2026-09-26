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
| Open experiment Board / panel | `game_exp_board` | Read-only consistent Ledger snapshot |
| Collaboration notification feed | `game_exp_notifications` | Read-only, replayable, external delivery adapters dedupe by event_id |
| Build implementation brief | `game_exp_prototype_handoff` | Read-only handoff to Godot Prototype Studio; no lifecycle mutation |
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


## Backend routing

Use the first available backend that preserves the trust model:

1. Native game-exp MCP tools.
2. GitHub Bridge through the repository Issue that owns the experiment.
3. If neither backend is available, stop and report the missing capability.

The GitHub Bridge is for ChatGPT sessions where the normal GitHub connector is available but custom MCP Apps / Developer Mode are unavailable. It does not replace the protected Ledger or Trusted Writer.

For mutating bridge actions, add one Issue comment whose first line is exactly `/game-exp` and whose remaining body is one strict JSON object. Every mutation requires a stable `request_id`. Never create a second request id to escape an uncertain result.

| MCP intent | Bridge action |
|---|---|
| Bind Manifest | `bind` |
| Initialize refs | `initialize` |
| Build Candidate | `candidate_build` |
| Record Review | `review_record` |
| Lifecycle Decision | `decision_submit` |
| Rehearsal | `rehearse` |
| Create/reuse Integration PR | `integrate` |
| Finalize merged Integration PR | `integrate_finalize` |
| Archive | `archive` |
| Abort PREPARED Archive | `archive_abort` |
| Read one experiment | `status` |

Bridge invariants:

- The command must be posted on the GitHub Issue whose number equals the experiment number.
- The bridge independently resolves the Issue-comment author and repository write permission.
- Human-gated actions still require an explicit human decision before posting the bridge command.
- The bridge posts a request-scoped claim marker before execution and a result marker after execution.
- If a claim exists without a result, treat the outcome as `UNKNOWN`; inspect the referenced bridge run and authoritative Ledger before retrying.
- Do not post duplicate bridge comments while the original request is unresolved.

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

## Board views

The Board projection contains `overview`, `attention`, `prototypes`, `branches`, and `archive` views. Follow `board.md` for presentation. `attention.sections` is the action Inbox, experiment `activity` is the Ledger-derived timeline, and prototype groups carry recent activity plus relationship counts. New experiments should carry stable `manifest.subject`; legacy experiments may use scope-derived fallback identity. Optional `manifest.relationships` may declare `depends_on`, `blocks`, or `supersedes` links to existing valid experiments in the same repository.

## Collaboration and implementation boundaries

- Notification events are derived from the protected Ledger and must not become a second lifecycle state machine.
- game-exp exposes targets and stable event ids; external systems perform message delivery.
- Creative exploration defaults to sequential experiments rather than automatic parallel variant generation.
- Godot Prototype Studio owns prototype implementation, runtime verification, export, and requested playtest delivery.
- game-exp resumes at Candidate/Review after implementation evidence is returned.
