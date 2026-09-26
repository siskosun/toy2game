---
name: game-exp
description: Orchestrate trusted game experiments through either native game-exp MCP tools or the repository GitHub Issue-comment bridge, plus an authorized source-editing workflow. Use in ChatGPT or Codex when the user wants to create, continue, inspect, review, promote, select, integrate, archive, recover, or diagnose a game-exp-managed gameplay/prototype experiment. Preserve human Review/selection gates, reconcile asynchronous requests against the protected Ledger, respect experiment scope, and never bypass the Trusted Writer or protected refs.
---

# game-exp

Use game-exp as the experiment control plane. Prefer native `game_exp_*` MCP tools when available. If custom MCP Apps are unavailable but an authorized GitHub connector can read/write the repository, use the repository GitHub Bridge described in `references/workflow.md`. Use the host's authorized source-editing capability for source changes.

## Non-negotiable rules

1. Treat the protected game-exp Ledger as authoritative. Local files, labels, branch names, workflow UI, and `actor_claim` are not authority.
2. Never write protected Ledger/domain records or protected refs directly. Use the game-exp MCP tools and their trusted GitHub workflows.
3. Never report `ACCEPTED` as completion. Resolve the same request or re-read the experiment projection until the authoritative result is known.
4. Never auto-approve a human gate. A passing build, Candidate, Review prerequisites, or Rehearsal does not authorize PASS, PROMISING, SELECTED, or REJECTED on the user's behalf.
5. Do not create a new request id to escape `UNKNOWN`, stale-head, or request-id conflicts. Reconcile the original id first.
6. Do not weaken scope, Rulesets, retention, Rehearsal freshness, or Archive recovery semantics to make a workflow pass.
7. For archived experiments, treat the immutable final tag as the official source snapshot. Do not recreate the deleted `exp/*` branch to "restore" the experiment.
8. Keep game-exp orchestration self-contained. Do not invoke unrelated planning/handoff workflows, create `.ai/HANDOFF.md` or `.ai/STATE.md`, or add extra approval gates unless the repository's own checked-in instructions explicitly require them or the user explicitly asks for them. The game-exp lifecycle gates remain the control plane for experiment work.

## Experiment Board

When the user asks to open the game-exp panel, Board, dashboard, experiment list, or a named view:

1. If native MCP is available, call `game_exp_board`.
2. Otherwise build the same projection from one pinned protected `game-exp/ledger` snapshot through GitHub.
3. Render Chinese by default.
4. Default to `总览`; support `待处理` / `原型` / `分支图` / `归档` as named views.
5. Follow `references/board.md` for information hierarchy, ordering, labels, focus filters, and empty-state behavior.
6. When the user asks to narrow the Board, pass read-only focus filters to `game_exp_board`: `query`, `subject_id`, `lifecycle`, and/or `attention_only`. Treat `focus.experiment_ids` as a presentation subset only; the full pinned snapshot remains authoritative.
7. Keep all system-generated panel entries in Chinese. Preserve raw machine enums only for diagnostics; never make English enum names the primary UI text.
8. Treat `health=FAIL` as blocked and never recommend normal lifecycle work for it.
9. Do not derive authority from Issue labels, branch names, workflow UI, focus results, or the rendered Board.

Treat the Board as a structured read-only projection. The host may render it as text or richer UI; neither representation is authoritative.

When the user opens one experiment from the Board, prefer `game_exp_experiment_panel` over stitching together `game_exp_board` and `game_exp_experiment_get` in separate reads. The panel is a Chinese-ready read-only projection from one pinned Ledger snapshot and includes overview, hypothesis/criteria, activity, relationships, and current evidence ids.

## Start every workflow from authoritative state

- With MCP, use `game_exp_status` / `game_exp_experiment_get` / `game_exp_doctor` as appropriate. Before a new experiment is bound, use repo-level Doctor without `experiment_id`; use experiment-aware Doctor only after the experiment exists in the protected Ledger.
- Without MCP, read the same authoritative objects from the protected `game-exp/ledger` ref through GitHub. For one experiment, start with `experiments/EXP-N/state.json`, then follow only the current ids in that state to the corresponding records.
- Pin multi-file reads to one Ledger commit SHA whenever the connector supports an explicit ref; never combine files fetched from moving `game-exp/ledger` at different times.
- Before a mutation, confirm the current lifecycle, current Candidate/Review/Rehearsal/Integration/Archive ids, and `last_decision_id` when relevant.
- If repository-health checks require information the GitHub connector cannot read (for example an admin-only secret inventory), report that Doctor coverage is partial rather than inventing PASS. Trusted workflows remain the mutation gate.
- If the requested action conflicts with current lifecycle, explain the valid next gate instead of improvising a transition.

See `references/workflow.md` for the lifecycle/tool map and `references/github-bridge.md` for exact Bridge command schemas.

## Backend routing

1. Prefer native `game_exp_*` MCP tools when they are available.
2. Otherwise, if the host has an authorized GitHub connector with Issue-comment and repository access, use the GitHub Bridge.
3. If neither exists, stop at the missing capability; never simulate a lifecycle mutation locally.

For the GitHub Bridge:

- Read authoritative state from `game-exp/ledger` through GitHub before deciding the next action.
- Post one top-level comment on the experiment's canonical Issue. The first line must be exactly `/game-exp`; the rest must be one strict JSON command from `references/github-bridge.md`.
- Use one stable `request_id` per logical action. Never post a second command with a new id merely because the bridge response is delayed or uncertain.
- After posting, read Issue comments for the matching `game-exp-bridge:<request_id>:claim` and `:result` markers and inspect the referenced Actions run when necessary.
- Treat claim-without-result as `UNKNOWN`. Reconcile against the Ledger or existing bridge run; do not resubmit blindly.
- The bridge author is independently resolved from the GitHub Issue comment and must have repository write permission. Do not place a different actor identity inside the command.
- Human-owned gates remain human-owned. The presence of the bridge does not authorize PASS, PROMISING, SELECTED, REJECTED, merge, or archive branch choice.

## Host capability boundary

- Keep game-exp focused on lifecycle control; do not turn its MCP into a generic source editor.
- In ChatGPT Work, use an authorized GitHub/code capability for experiment source edits and PR merge actions.
- In Codex, use normal repository editing/Git capabilities for source changes.
- If the host cannot edit the source repository, stop at the source-editing step and report that capability gap; do not bypass the protected workflow or broaden game-exp write authority to compensate.

## Create and implement a new experiment

1. Ensure there is a real GitHub Issue for the experiment. Resolve repository id, Issue id/number, and parent SHA from GitHub or provided authoritative context; never invent them.
2. Build the canonical Manifest with a stable `operation_id`. Include the user hypothesis, success/kill criteria, scope, runtime and review protocol. For every new experiment also include stable `subject`: use `{type: "game-prototype", id, name, root_path}` for one game prototype, or `{type: "repository", id: "repository", name, root_path: "."}` for repository-level work. Keep `subject.id` stable across later path/name changes. When the experiment has a real dependency/history relationship to an existing healthy bound experiment in the same repository, optionally include `relationships` using only `depends_on`, `blocks`, or `supersedes`; do not invent relationships. `scope` remains the security boundary and must not be used as the long-term subject identity. If criteria are materially ambiguous, ask only for the missing decision; otherwise draft concrete, falsifiable criteria from the request.
3. Execute Bind through the active backend: `game_exp_experiment_bind` with MCP, or Bridge action `bind`. The request id must equal `manifest.operation_id`.
4. If the result is `ACCEPTED` or `UNKNOWN`, reconcile the same logical request. With MCP use `game_exp_request_get`; with GitHub Bridge inspect its claim/result markers plus the protected Ledger. Continue only after the binding is committed/applied.
5. Execute Initialize through `game_exp_initialize` or Bridge action `initialize` to create the canonical experiment branch/base tag.
6. Work on the canonical `exp/<issue>` branch using the host's authorized source-editing capability (for example GitHub tools in ChatGPT Work or normal Codex Git operations). Keep changes inside Manifest `scope.allowed`; treat `scope.avoid` as forbidden. Do not recreate or rename canonical refs.
7. Run project checks appropriate to the repository before asking game-exp to build the Candidate.

## Candidate and human Review

1. Move the experiment to `REVIEW` through `game_exp_decision_submit` or Bridge action `decision_submit` when implementation is ready for evaluation.
2. Build the Candidate through `game_exp_candidate_build` or Bridge action `candidate_build`. Wait for the trusted Candidate workflow, then refresh the protected experiment state until a current Candidate is present.
3. Present the Candidate identity and relevant evidence to the user. Do not infer human quality from automated checks.
4. Record Review through `game_exp_review_record` or Bridge action `review_record` only after the user explicitly supplies the human outcome (`PASS` or `FAIL`) or explicitly instructs you to record an already-made human review.
5. A PASS Review does not automatically mean PROMISING. Submit the PROMISING decision through the active backend only after explicit user approval.

## Rehearsal, selection and Integration

1. From `PROMISING`, run Rehearsal through `game_exp_rehearse` or Bridge action `rehearse` against exact latest main.
2. Before SELECTED, refresh `game_exp_experiment_get`. If main advanced or Rehearsal is stale, run a new Rehearsal; never relax the freshness gate.
3. Submit the SELECTED decision through the active backend only after the user explicitly chooses/selects the Candidate.
4. If main advances after selection, a fresh Rehearsal may be required before Integration. Follow the trusted workflow result rather than reusing stale evidence.
5. Create/reuse the Integration PR through `game_exp_integrate` or Bridge action `integrate`. Treat the PR as the human-visible integration step; do not claim INTEGRATED yet.
6. Do not merge the Integration PR implicitly. If the user explicitly asks to merge and the host has an authorized GitHub merge action, the merge may be performed there; otherwise present the PR for human merge.
7. After the PR is actually merged, finalize through `game_exp_integrate_finalize` or Bridge action `integrate_finalize`, then verify the protected state becomes `INTEGRATED`.

## Archive

Archive is a destructive/recovery-sensitive workflow.

- If the user says to archive **and delete the experiment branch**, use `ATOMIC_DELETE`.
- If the user says to archive **and keep the branch**, use `RETAIN_BRANCH`.
- If the user asks only to "archive" and the intended branch behavior is not clear, ask for this one material choice before dispatching.
- Archive through `game_exp_archive` or Bridge action `archive`; do not manually create the final tag or delete the branch.
- `game_exp_archive_abort` / Bridge action `archive_abort` is valid only while the archive is still PREPARED. Once the trusted workflow claims it, Abort is permanently unavailable; recover the same archive instead of creating a second logical archive.
- After completion, refresh protected Ledger state and archive health through the best available backend. Report `ARCHIVED` only when the protected Ledger says so and final-ref checks are consistent.

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
