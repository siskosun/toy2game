---
name: game-exp
description: Orchestrate trusted game experiments through the game-exp MCP tools and normal Codex coding workflow. Use when the user wants to create, continue, inspect, review, promote, select, integrate, archive, recover, or diagnose a game-exp-managed gameplay/prototype experiment, especially in Codex. Preserve human Review/selection gates, reconcile asynchronous requests against the protected Ledger, respect experiment scope, and never bypass the Trusted Writer or protected refs.
---

# game-exp

Use game-exp as the experiment control plane. Use normal Codex editing/Git capabilities for source changes, and use the `game_exp_*` MCP tools for authoritative experiment lifecycle operations.

## Non-negotiable rules

1. Treat the protected game-exp Ledger as authoritative. Local files, labels, branch names, workflow UI, and `actor_claim` are not authority.
2. Never write protected Ledger/domain records or protected refs directly. Use the game-exp MCP tools and their trusted GitHub workflows.
3. Never report `ACCEPTED` as completion. Resolve the same request or re-read the experiment projection until the authoritative result is known.
4. Never auto-approve a human gate. A passing build, Candidate, Review prerequisites, or Rehearsal does not authorize PASS, PROMISING, SELECTED, or REJECTED on the user's behalf.
5. Do not create a new request id to escape `UNKNOWN`, stale-head, or request-id conflicts. Reconcile the original id first.
6. Do not weaken scope, Rulesets, retention, Rehearsal freshness, or Archive recovery semantics to make a workflow pass.
7. For archived experiments, treat the immutable final tag as the official source snapshot. Do not recreate the deleted `exp/*` branch to "restore" the experiment.

## Start every workflow from authoritative state

- Call `game_exp_status` when repository/Ledger identity is not already established.
- For an existing experiment, call `game_exp_experiment_get` before choosing a mutation.
- Use `game_exp_doctor` when trust controls, archived refs, or repository health are relevant.
- If the requested action conflicts with the current lifecycle, explain the current state and the valid next gate instead of improvising a transition.

See `references/workflow.md` for the lifecycle/tool map.

## Create and implement a new experiment

1. Ensure there is a real GitHub Issue for the experiment. Resolve repository id, Issue id/number, and parent SHA from GitHub or provided authoritative context; never invent them.
2. Build the canonical Manifest with a stable `operation_id`. Include the user hypothesis, success/kill criteria, scope, runtime and review protocol. If criteria are materially ambiguous, ask only for the missing decision; otherwise draft concrete, falsifiable criteria from the request.
3. Call `game_exp_experiment_bind`. The request id must equal `manifest.operation_id`.
4. If the result is `ACCEPTED` or `UNKNOWN`, reconcile that same request with `game_exp_request_get`. Continue only after the binding is committed/applied.
5. Call `game_exp_initialize` to create the canonical experiment branch/base tag.
6. Work on the canonical `exp/<issue>` branch using normal Codex coding/Git operations. Keep changes inside Manifest `scope.allowed`; treat `scope.avoid` as forbidden. Do not recreate or rename canonical refs.
7. Run project checks appropriate to the repository before asking game-exp to build the Candidate.

## Candidate and human Review

1. Move the experiment to `REVIEW` with `game_exp_decision_submit` when implementation is ready for evaluation.
2. Call `game_exp_candidate_build`. Wait for the trusted Candidate workflow to finish, then refresh with `game_exp_experiment_get` until a current Candidate is present.
3. Present the Candidate identity and relevant evidence to the user. Do not infer human quality from automated checks.
4. Call `game_exp_review_record` only after the user explicitly supplies the human outcome (`PASS` or `FAIL`) or explicitly instructs you to record an already-made human review.
5. A PASS Review does not automatically mean PROMISING. Call `game_exp_decision_submit(... to_state="PROMISING")` only after explicit user approval to promote.

## Rehearsal, selection and Integration

1. From `PROMISING`, call `game_exp_rehearse` to test the current Candidate against exact latest main with the trusted scope-filtered Rehearsal.
2. Before SELECTED, refresh `game_exp_experiment_get`. If main advanced or Rehearsal is stale, run a new Rehearsal; never relax the freshness gate.
3. Call `game_exp_decision_submit(... to_state="SELECTED")` only after the user explicitly chooses/selects the Candidate.
4. If main advances after selection, a fresh Rehearsal may be required before Integration. Follow the trusted workflow result rather than reusing stale evidence.
5. Call `game_exp_integrate` to create/reuse the trusted Integration PR. Treat the PR as the human-visible integration step; do not claim INTEGRATED yet.
6. Do not merge the Integration PR implicitly. If the user explicitly asks to merge and the host has an authorized GitHub merge action, the merge may be performed there; otherwise present the PR for human merge.
7. After the PR is actually merged, call `game_exp_integrate_finalize` with the PR number. Verify the experiment projection becomes `INTEGRATED`.

## Archive

Archive is a destructive/recovery-sensitive workflow.

- If the user says to archive **and delete the experiment branch**, use `ATOMIC_DELETE`.
- If the user says to archive **and keep the branch**, use `RETAIN_BRANCH`.
- If the user asks only to "archive" and the intended branch behavior is not clear, ask for this one material choice before dispatching.
- Call `game_exp_archive`; do not manually create the final tag or delete the branch.
- `game_exp_archive_abort` is valid only while the archive is still PREPARED. Once the trusted workflow claims it, Abort is permanently unavailable; recover the same archive instead of creating a second logical archive.
- After completion, call `game_exp_experiment_get` and `game_exp_doctor(experiment_id=...)`. Report `ARCHIVED` only when the protected Ledger says so and the final-ref checks are consistent.

## Request/result handling

Interpret game-exp results conservatively:

- `ACCEPTED`: workflow dispatch accepted; operation is not yet complete.
- `COMMITTED`: the request record is durably committed. For domain mutations, also confirm `domain_status=APPLIED` or the expected experiment projection.
- `PASS`: a read/doctor/projection check passed; this is not a lifecycle mutation result.
- `UNKNOWN`: outcome is unresolved. Reconcile the same request and inspect authoritative remote state.
- `CONFLICT`: stop the attempted mutation, refresh experiment/Ledger state, and explain the conflicting precondition or identity.
- `REJECTED`: do not retry unchanged. Surface the trusted domain error and fix the cause.

When a workflow tool returns only a run URL/status, poll by re-reading the experiment projection rather than inventing completion.

## Recovery behavior

- Lost response: use the original request id with `game_exp_request_get`.
- Duplicate request with same payload: accept the authoritative idempotent result; do not create another request.
- Duplicate request with different payload: treat as a hard conflict.
- Stale lifecycle decision: refresh `game_exp_experiment_get` and bind a new decision to the current `last_decision_id` only if the user still wants that decision.
- Stale Rehearsal: run a new Rehearsal on current main.
- Archive `REF_CONFLICT`: do not force refs. Preserve the archive lock and use the trusted recovery workflow/evidence.

## User-facing status format

Keep status concise and decision-oriented. Report:

- experiment id and lifecycle;
- current Candidate/Review/Rehearsal/Integration/Archive id when relevant;
- what is authoritative vs merely dispatched;
- the next human gate or automated step;
- any conflict/unknown condition that blocks progression.

Do not dump raw operation JSON unless the user asks for debugging detail.
