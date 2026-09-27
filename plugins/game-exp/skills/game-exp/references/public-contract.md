# game-exp public cross-interface contract

## Purpose

MCP, CLI and the GitHub Bridge are replaceable interfaces. They do not define experiment truth and they do not gain write authority merely by sharing code.

The stable contract is:

```text
Harness
  -> MCP / CLI / GitHub Bridge
  -> versioned command/query contract
  -> trusted execution boundary
  -> domain rules
  -> protected game-exp Ledger
```

## Contract version

Current public contract: `1.0`.

A client may tolerate new result fields within the same major version. Mutating requests remain strict and reject unknown request fields. An unsupported major version must fail closed rather than guessing.

Use `game_exp_capabilities` or `game-exp capabilities` to inspect business features and contract version. Repository access returned there is only a current snapshot for UX; every write is re-authorized at the trusted boundary.

## Self-describing Manifest contract

A new Harness must be able to create the first experiment without reading another repository or historical Ledger examples.

Use `game_exp_experiment_template` or CLI `experiment-template` to read:

- the current repository's `.game-exp/project-policy.json`;
- the recommended Manifest schema version;
- the runtime shape derived from that project policy;
- the default human review protocol;
- which fields are user-owned versus resolved by the Agent.

Manifest schema v2 is recommended. Its runtime block is generic and repository-policy based:

```json
{
  "runtime": {
    "adapter": "node-npm",
    "policy_path": ".game-exp/project-policy.json"
  }
}
```

The adapter value comes from the current repository project policy. Future project-policy adapters can reuse the same Manifest v2 runtime shape.

Manifest schema v1 remains accepted for existing experiments and uses the legacy Godot-specific runtime object. New experiments should not emit Godot placeholder fields for non-Godot repositories.

The example Manifest returned by `experiment-template` contains unresolved placeholders and is explicitly non-bindable. The Agent must resolve the real Issue identity, parent SHA, stable operation id, scope, subject and timestamp before Bind.

## Stable operation identity

Every logical mutation has one stable `request_id` / operation id.

- Same id + same request: replay or return the existing operation.
- Same id + different request: `CONFLICT`.
- Lost response, timeout, or `UNKNOWN`: query the same id with `game_exp_operation_get` / `game-exp get-operation`.
- Never create a new id to escape uncertainty.
- Authorization failure is terminal for that attempted authority context. Do not retry the same logical mutation through another interface to bypass it.

For asynchronous lifecycle workers (Initialize, Candidate, Rehearsal, Integration, Integration Finalize, Archive), the stable id first commits an `execution.claim` in the protected Ledger. That claim binds:

- experiment id;
- action;
- exact action arguments;
- authoritative experiment-state digest;
- Trusted Writer-verified GitHub actor.

Only a workflow run whose action/arguments match that committed claim may perform effects. Duplicate runs with the same request id are remotely deduplicated.

## Routing and recovery

Before any mutation has been submitted, prefer interfaces in this order:

1. native game-exp MCP;
2. game-exp CLI when shell execution is available;
3. GitHub Bridge when an authorized GitHub Issue-comment path is available.

Interface availability is not permission.

### MCP identity modes

Local stdio MCP and the CLI normally execute under the local GitHub credential and therefore have one concrete local principal.

Streamable HTTP is different: a shared server credential is not automatically the caller's identity. game-exp therefore treats HTTP MCP as read-only by default unless the deployment explicitly sets `GAME_EXP_MCP_TRUSTED_SINGLE_PRINCIPAL=1` for a genuinely single-principal endpoint. Multi-user HTTP deployments require a future per-request identity binding; until then use per-user local MCP/CLI or GitHub Bridge for writes.

Supporting MCP transport does not imply equivalent authorization semantics.

After a mutation returns `ACCEPTED` or `UNKNOWN`, enter recovery mode. You may use another interface to query or resume the same operation id, but you must not create a replacement logical operation.

Example:

```text
MCP candidate_build req-123
  -> reply lost

CLI:
game-exp get-operation req-123
game-exp resume-operation req-123
```

This is valid because the operation identity is unchanged.

## Result semantics

- `PASS`: a read/projection/check succeeded, or an asynchronous worker completed successfully.
- `WARN`: result exists but has an explicit non-fatal condition.
- `ACCEPTED`: accepted/claimed/dispatched/running; not completion.
- `COMMITTED`: protected Ledger contains the matching request record.
- `CONFLICT`: identity, state precondition, request digest, or concurrency fact differs.
- `REJECTED`: validated failure; do not retry unchanged.
- `UNKNOWN`: outcome cannot yet be proved; recover by the same operation id.

## Cancellation

There is no generic `cancel-operation` in public contract v1.0. A claimed/running async operation is recovered or allowed to complete; arbitrary cancellation could leave external effects ambiguous.

Archive keeps its existing dedicated `archive_abort`, valid only while the Archive is PREPARED and has not been claimed. Once claimed, recover the same Archive instead of cancelling it.


Transport wrappers may differ between MCP, CLI and Bridge. The business statuses and recovery semantics must not.

## Trusted execution boundary

Local MCP/CLI validation is advisory. Authoritative mutations are checked again by trusted GitHub workflows / Trusted Writer.

The trusted side verifies as applicable:

- current GitHub identity and repository permission;
- request id and payload digest;
- protected Ledger head/state;
- lifecycle and previous decision id;
- Candidate/source/rehearsal/integration evidence;
- archive lock/ref evidence;
- exact asynchronous execution claim.

A local CLI executable being modifiable does not grant Ledger write authority.

## Human decisions

Human Review and lifecycle decisions remain tied to concrete evidence.

A Review must identify the Candidate and its artifact digest. Promotion and selection re-check the current Candidate, Review, retention and/or Rehearsal. A human approval for an earlier Candidate does not silently apply to a later Candidate.

Never convert a generic `approved=true` flag into authoritative human evidence.

## Capability probing

Route by available interface, not Harness brand. Do not maintain a Codex/Cursor/Claude/Qoder business-logic fork.

Typical probing:

```text
game_exp_* MCP available?
  -> MCP
else game-exp CLI available?
  -> CLI
else authorized GitHub Bridge available?
  -> Bridge
else
  -> report missing execution capability
```

A newly supported Harness should normally require no Domain Core changes. Installation, credentials, UI integration and compatibility verification may still require small host-specific setup.

## Notifications

Notification events are deterministic projections of committed Ledger snapshots.

Adapters must:

1. page with `cursor` until `next_cursor` is empty;
2. persist the returned `checkpoint_cursor` only after all pages are processed;
3. use that checkpoint as `after` on the next poll;
4. deduplicate by `event_id`;
5. respect viewer access;
6. handle `CURSOR_EXPIRED` explicitly.

Delivery retries and delivered/read state stay outside game-exp.

## Dependency review

`depends_on` is currently a coarse experiment relationship. Lifecycle alone does not prove whether a dependency is still satisfied.

If an active experiment depends on an experiment that becomes REJECTED or ARCHIVED, game-exp emits `DEPENDENCY_REVIEW_REQUIRED` with supporting evidence. It does not automatically reject the downstream experiment.

A later contract version may introduce explicit dependency predicates such as “integrated capability”, “immutable source snapshot”, or “continued upstream delivery” if real usage requires them.

## Prototype handoff

`game_exp_prototype_handoff` emits Handoff schema v2 for Godot Prototype Studio.

The return evidence must bind:

- experiment id;
- handoff id;
- exact source SHA;
- build id / producer;
- checks and their environment;
- artifact locations and digests;
- whether each artifact location is portable across Harnesses.

A local filesystem path alone is not sufficient cross-Harness evidence unless it is explicitly marked `portable=false`.

A2A may later transport the same task/artifact semantics if Godot Prototype Studio becomes an independent Agent. A2A transport must not redefine game-exp human approvals, idempotency or evidence validity.
