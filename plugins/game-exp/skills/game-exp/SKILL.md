---
name: game-exp
description: Create and operate complete trusted game-exp repositories and gameplay/prototype experiments through MCP, CLI, or the GitHub Bridge. Use when the user wants to bootstrap a new game-exp project/repository; create, continue, inspect, review, promote, select, integrate, archive, recover, or diagnose an experiment; validate repository trust prerequisites; or recover cross-interface operations. Require PROJECT_READY trust setup before first-experiment onboarding, preserve human gates, and never bypass the Trusted Writer or protected refs.
---

# game-exp

Use game-exp as the experiment control plane. Route by available interface, not Harness brand: prefer native `game_exp_*` MCP tools, then the repository `game-exp` CLI when shell execution is available, then the authorized GitHub Bridge. All three interfaces share the same versioned operation/recovery semantics and re-enter the trusted execution boundary. Read `references/public-contract.md` before changing cross-interface behavior. Use the host's authorized source-editing capability for source changes.

## Non-negotiable rules

1. Treat the protected game-exp Ledger as authoritative. Local files, labels, branch names, workflow UI, and `actor_claim` are not authority.
2. Never write protected Ledger/domain records or protected refs directly. Use the game-exp MCP tools and their trusted GitHub workflows.
3. Never report `ACCEPTED` as completion. Resolve the same operation id with `game_exp_operation_get`, `game-exp get-operation`, or the Bridge evidence until the authoritative result is known.
4. After any mutation is `ACCEPTED` or `UNKNOWN`, enter recovery mode: another interface may query/resume that exact operation id, but must never create a replacement id or logical mutation.
5. An authorization failure is not a transport failure. Do not switch interfaces to retry the same logical mutation under a different authority context.
6. Never auto-approve a human gate. A passing build, Candidate, Review prerequisites, or Rehearsal does not authorize PASS, PROMISING, SELECTED, or REJECTED on the user's behalf.
7. Do not create a new request id to escape `UNKNOWN`, stale-head, request-id, execution-claim, or state-precondition conflicts. Reconcile the original id first.
8. Do not weaken scope, Rulesets, retention, Rehearsal freshness, or Archive recovery semantics to make a workflow pass.
9. For archived experiments, treat the immutable final tag as the official source snapshot. Do not recreate the deleted `exp/*` branch to "restore" the experiment.
10. Keep game-exp orchestration self-contained. Do not invoke unrelated planning/handoff workflows, create `.ai/HANDOFF.md` or `.ai/STATE.md`, or add extra approval gates unless the repository's own checked-in instructions explicitly require them or the user explicitly asks for them. The game-exp lifecycle gates remain the control plane for experiment work.

## Experiment Board

When the user asks to open the game-exp panel, Board, dashboard, experiment list, or a named view:

1. If native MCP is available, call `game_exp_board`.
2. Otherwise build the same projection from one pinned protected `game-exp/ledger` snapshot through GitHub.
3. Render Chinese by default.
4. Default to `总览`; support `待处理` / `原型` / `分支图` / `归档` as named views.
5. Follow `references/board.md` for information hierarchy, ordering, labels, focus filters, and empty-state behavior.
6. Render the Board's structured `repository`, `project`, `statistics`, `onboarding`, and `display` blocks before inventing host-specific summaries. These fields exist so different Harnesses show the same project readiness, visibility, access, trust checks, counts, and next action.
7. When `project.readiness=PROJECT_READY`, never suggest `project-init`, Ruleset setup, changing repository visibility, or other repository bootstrap work. On an empty ready repository, the primary action is `CREATE_FIRST_EXPERIMENT`.
8. When the user asks to narrow the Board, pass read-only focus filters to `game_exp_board`: `query`, `subject_id`, `lifecycle`, and/or `attention_only`. Treat `focus.experiment_ids` as a presentation subset only; the full pinned snapshot remains authoritative.
9. Keep all system-generated panel entries in Chinese. Preserve raw machine enums only for diagnostics; never make English enum names the primary UI text.
10. Treat `health=FAIL` as blocked and never recommend normal lifecycle work for it.
11. Do not derive authority from Issue labels, branch names, workflow UI, focus results, old chat context, cached preflight results, or the rendered Board.

Treat the Board as a structured read-only projection. The host may render it as text or richer UI; neither representation is authoritative.

If the host supports a self-contained inline interactive app surface, prefer the Chat inline UI contract in `references/chat-ui.md`. Keep all interactions read-only and local to presentation; lifecycle mutations still go through trusted game-exp tools and human gates. If inline UI is unavailable, fall back to the text Board contract.

When the user opens one experiment from the Board, prefer `game_exp_experiment_panel` over stitching together `game_exp_board` and `game_exp_experiment_get` in separate reads. The panel is a Chinese-ready read-only projection from one pinned Ledger snapshot and includes overview, hypothesis/criteria, activity, relationships, and current evidence ids.

When the user opens one prototype/subject group, prefer `game_exp_subject_panel`. It returns the stable subject identity, focused lifecycle/health/attention summary, recent activity, child experiment summaries, and relationship edges from the same pinned Ledger snapshot.

For multi-user work, keep `发起人` and `代码贡献者` distinct. The initiator comes from the Trusted Writer-verified binding actor. Contributors come from GitHub commit attribution and are display-only collaboration metadata; never use contributor status as authority.

## Complete project setup

When the user asks game-exp to create, bootstrap, prepare, or initialize a new repository/project, follow `references/project-setup.md` before first-experiment onboarding.

- Installing files is not project completion.
- After the repository and source exist, install game-exp files, commit/push them to `main`, then run `game_exp_project_preflight` or CLI `project-preflight`.
- If preflight reports `BLOCKED_PLAN`, `BLOCKED_PERMISSION`, or `BLOCKED_SOURCE`, stop and surface the material blocker. Do not create a degraded trust mode or call the project ready.
- Run `game_exp_project_init` only through local stdio MCP, or CLI `project-init`, with the repository administrator's GitHub identity.
- Shared/streamable HTTP MCP must never perform complete project initialization because setup generates a private Deploy Key and repository secret.
- A project is ready only when project-init returns `status=PASS`, `complete=true`, the Trusted Writer self-test passed, and final repo-level Doctor is PASS.
- If GitHub plan support is insufficient for a private repository, never change repository visibility automatically. The user must explicitly choose public visibility or a plan that supports private-repository rulesets.
- Only after `PROJECT_READY` may first-experiment onboarding begin.

## First-use onboarding

When the protected Ledger is empty, or the user explicitly says this is their first use, follow `references/onboarding.md`. Prefer the `project` and `onboarding` blocks already returned by `game_exp_board`; they combine current access and repo-level Doctor into one consistent next-step projection. If those fields are unavailable on an older client, fall back to `game_exp_access_check` plus repo-level Doctor. A repo-level Doctor PASS means `PROJECT_READY`; do not rerun or recommend `project-init` merely because the Ledger has zero experiments. Keep onboarding in plain Chinese and translate natural-language intent into the existing trusted workflow. Do not require the user to know Manifest fields, request ids, lifecycle enums, MCP tool names, or protected refs.

Onboarding completes only after the first experiment is authoritatively bound and initialized. An `ACCEPTED` dispatch alone is not completion.

## Start every workflow from authoritative state

- With MCP, use `game_exp_status` / `game_exp_experiment_get` / `game_exp_doctor` as appropriate. Before a new experiment is bound, use repo-level Doctor without `experiment_id`; use experiment-aware Doctor only after the experiment exists in the protected Ledger.
- Without MCP, read the same authoritative objects from the protected `game-exp/ledger` ref through GitHub. For one experiment, start with `experiments/EXP-N/state.json`, then follow only the current ids in that state to the corresponding records.
- Pin multi-file reads to one Ledger commit SHA whenever the connector supports an explicit ref; never combine files fetched from moving `game-exp/ledger` at different times.
- Before a mutation, confirm the current lifecycle, current Candidate/Review/Rehearsal/Integration/Archive ids, and `last_decision_id` when relevant.
- If repository-health checks require information the GitHub connector cannot read (for example an admin-only secret inventory), report that Doctor coverage is partial rather than inventing PASS. Trusted workflows remain the mutation gate.
- If the requested action conflicts with current lifecycle, explain the valid next gate instead of improvising a transition.

See `references/workflow.md` for the lifecycle/tool map and `references/github-bridge.md` for exact Bridge command schemas.

## Backend routing

Before a mutation has been submitted, use interface probing rather than Harness-name routing:

1. Prefer native game-exp MCP tools.
2. Otherwise, if the host can execute the repository CLI, use `game-exp` / `python tools/game-exp/cli.py`.
3. Otherwise, if the host has an authorized GitHub connector with Issue-comment and repository access, use the GitHub Bridge.
4. If none exists, stop at the missing capability; never simulate a lifecycle mutation locally.

Use `game_exp_capabilities` or `game-exp capabilities` to inspect the public contract and business features. Treat any returned repository permission as a current UX snapshot only; every write is independently authorized again at the trusted execution boundary.

Every mutation requires one stable operation/request id. The same logical operation keeps that id across MCP, CLI and Bridge.

If a mutation returns `ACCEPTED` or `UNKNOWN`:

- do not submit a new logical operation;
- use `game_exp_operation_get` / `game-exp get-operation` to query the same id;
- for an already committed async execution claim with no worker run, `game_exp_operation_resume` / `game-exp resume-operation` may resume that same id;
- if the authoritative experiment state changed after the claim, treat the resume as a conflict rather than silently dispatching against new state.

For the GitHub Bridge:

- Read authoritative state from `game-exp/ledger` through GitHub before deciding the next action.
- Post one top-level comment on the experiment's canonical Issue. The first line must be exactly `/game-exp`; the rest must be one strict JSON command from `references/github-bridge.md`.
- Use one stable `request_id` per logical action. Async Bridge actions first commit the same trusted `execution.claim` used by MCP/CLI, then dispatch the worker with that same id.
- After posting, read Issue comments for matching `game-exp-bridge:<request_id>:claim` and `:result` markers and inspect the referenced Actions run when necessary.
- Treat claim-without-result as `UNKNOWN`. Reconcile against the Ledger or existing run; do not resubmit blindly.
- The bridge author is independently resolved from the GitHub Issue comment and must have repository write permission.
- Human-owned gates remain human-owned. The bridge does not authorize PASS, PROMISING, SELECTED, REJECTED, merge, or archive branch choice.

## Harness conformance screening

When changing game-exp routing, recovery, transport identity, human-gate handling, or other cross-interface semantics, use the Conformance Simulator described in `references/conformance.md` before real-repository validation.

- Use `game_exp_conformance_suite` to inspect the fixed standing suite.
- Use `game_exp_conformance_start` only in an explicitly configured synthetic conformance session.
- Exercise the normal `game_exp_*` tools against that session; do not invent simulator-only lifecycle commands.
- Use `game_exp_conformance_result` to evaluate one scenario.
- Use `game_exp_conformance_compare` only when baseline and candidate reports share the exact same `suite_digest`.
- A conformance PASS means only `eligible_for_real_repo_test=true`. It is never Candidate, Review, Rehearsal, Integration, Archive, or release evidence.
- If the standing evaluator changes, rerun incumbent and candidate under the new suite. Never compare reports from different suite digests.
- Keep simulator mode isolated from production. A synthetic session must never contact or mutate GitHub.

## Collaboration notifications

When the user asks what changed, who started a new experiment, or wants collaborator notifications, use `game_exp_notifications` and follow `references/notifications.md`.

Notifications are Ledger-derived, replayable projections. game-exp does not send external messages itself. A ChatGPT task, Feishu/Slack/email bridge, or other adapter may deliver them. Process every page using `cursor`, persist `checkpoint_cursor` only after all pages succeed, resume later with `after`, and deduplicate by stable `event_id`.

Do not treat code-contributor identity as lifecycle authority.

## Exploration threads

Follow `references/exploration-thread.md`. Do not automatically fan one creative question into multiple parallel prototype branches. Prototype experiments may change the underlying concept substantially, so default to one active experiment per exploration thread and preserve later experiments as sequential history through subject identity and explicit relationships.

## Prototype implementation handoff

When a game experiment needs implementation, runtime verification, export, or requested playable delivery, use `game_exp_prototype_handoff` and follow `references/prototype-handoff.md`. Handoff v2 binds the task to a Ledger snapshot and requires returned evidence to identify source SHA, build identity, check environment, artifact digest/location, and artifact portability.

For Godot work, hand the returned brief to Godot Prototype Studio. game-exp remains responsible for experiment identity, scope, lifecycle and human gates; Godot Prototype Studio remains responsible for implementation and playable delivery.

## Host capability boundary

- Keep game-exp focused on lifecycle control; do not turn its MCP into a generic source editor.
- Local stdio MCP/CLI use the local GitHub principal. Streamable HTTP write operations fail closed unless the endpoint is explicitly configured as a trusted single-principal endpoint; a shared HTTP server credential is not caller identity.
- In ChatGPT Work, use an authorized GitHub/code capability for experiment source edits and PR merge actions.
- In Codex, use normal repository editing/Git capabilities for source changes.
- If the host cannot edit the source repository, stop at the source-editing step and report that capability gap; do not bypass the protected workflow or broaden game-exp write authority to compensate.

## Create and implement a new experiment

1. Ensure there is a real GitHub Issue for the experiment. Resolve repository id, Issue id/number, and parent SHA from GitHub or provided authoritative context; never invent them.
2. Build the canonical Manifest with a stable `operation_id`. Include the user hypothesis, success/kill criteria, scope, runtime and review protocol. For every new experiment also include stable `subject`: use `{type: "game-prototype", id, name, root_path}` for one game prototype, or `{type: "repository", id: "repository", name, root_path: "."}` for repository-level work. Keep `subject.id` stable across later path/name changes. When the experiment has a real dependency/history relationship to an existing healthy bound experiment in the same repository, optionally include `relationships` using only `depends_on`, `blocks`, or `supersedes`; do not invent relationships. `scope` remains the security boundary and must not be used as the long-term subject identity. If criteria are materially ambiguous, ask only for the missing decision; otherwise draft concrete, falsifiable criteria from the request.
3. Execute Bind through the active backend: `game_exp_experiment_bind` with MCP, or Bridge action `bind`. The request id must equal `manifest.operation_id`.
4. If the result is `ACCEPTED` or `UNKNOWN`, reconcile the same logical request. With MCP use `game_exp_operation_get`; with CLI use `game-exp get-operation`; with GitHub Bridge inspect its claim/result markers plus the protected Ledger. Continue only after the binding is committed/applied.
5. Execute Initialize through `game_exp_initialize`, CLI `initialize`, or Bridge action `initialize` with a new stable request id for that Initialize operation. Do not dispatch the underlying workflow without first committing its trusted execution claim.
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

- Lost response: use the original operation id with `game_exp_operation_get` or `game-exp get-operation`. The backward-compatible MCP alias `game_exp_request_get` resolves the same operation id.
- An async execution claim that is committed but has no worker run may be resumed with `game_exp_operation_resume` / `game-exp resume-operation`; this resumes the same id, not a new operation.
- Duplicate request with same payload: accept the authoritative idempotent replay; do not create another request.
- Duplicate request with different payload: hard `CONFLICT`.
- A committed async claim is bound to an authoritative experiment-state digest. If state changes before resume, return `EXECUTION_PRECONDITION_CHANGED`; never dispatch against the newer state implicitly.
- Authorization failure: do not fall back to another interface as a retry mechanism.
- Stale lifecycle decision: refresh `game_exp_experiment_get` and create a genuinely new decision only if the human still wants that decision against the new state.
- Stale Rehearsal: a new Rehearsal is a new logical operation with a new id because its intended base/evidence has changed.
- Archive `REF_CONFLICT`: do not force refs. Preserve the archive lock and use the trusted recovery workflow/evidence.

## User-facing status format

Keep status concise and decision-oriented. Report:

- experiment id and lifecycle;
- current Candidate/Review/Rehearsal/Integration/Archive id when relevant;
- what is authoritative vs merely dispatched;
- the next human gate or automated step;
- any conflict/unknown condition that blocks progression.

Do not dump raw operation JSON unless the user asks for debugging detail.
