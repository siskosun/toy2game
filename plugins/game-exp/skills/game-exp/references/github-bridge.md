# GitHub Bridge command reference

## Contents

- Envelope and placement
- Result markers and reconciliation
- Read action
- Lifecycle actions
- Archive actions

## Envelope and placement

Post the command as a top-level comment on the canonical experiment Issue. The Issue number must equal the numeric part of the experiment id.

Use exactly:

~~~text
/game-exp
{"schema_version":1,"request_id":"req_example","action":"status","experiment_id":"EXP-42"}
~~~

Rules:

- Use schema version 1.
- Use one stable request id per logical action.
- Do not include unknown keys.
- Do not put actor identity, tokens, secrets, or repository credentials in the JSON.
- For `bind`, `request_id` must equal `manifest.operation_id`.
- Human-gated commands may be posted only after the user explicitly made that decision.

## Result markers and reconciliation

The bridge posts:

- `<!-- game-exp-bridge:<request_id>:claim -->` before execution.
- `<!-- game-exp-bridge:<request_id>:result -->` after execution.

If a result marker exists, use that result and refresh the protected Ledger.

If a claim exists without a result, treat the bridge request as `UNKNOWN`. Inspect the bridge Actions run linked in the claim and refresh the protected Ledger. Do not post another command or create a new request id until the original outcome is resolved.

## Read action

### status

~~~json
{"schema_version":1,"request_id":"req_status_42","action":"status","experiment_id":"EXP-42"}
~~~

## Lifecycle actions

### bind

~~~json
{"schema_version":1,"request_id":"req_bind_42","action":"bind","manifest":{"schema_version":1,"experiment":{"host":"github.com","repository_id":"...","issue_id":"...","issue_number":"42"},"title":"...","operation_id":"req_bind_42","parent":{"experiment":null,"commit":"..."},"hypothesis":"...","success_criteria":["..."],"kill_criteria":["..."],"scope":{"allowed":["games/example/**"],"avoid":[".github/**","tools/game-exp/**","plugins/**",".game-exp/**"]},"runtime":{"godot":"...","export_templates":"...","addons_lock":"..."},"review":{"protocol":"blind-playtest-v1"},"created_at":"..."}}
~~~

### initialize

~~~json
{"schema_version":1,"request_id":"req_initialize_42","action":"initialize","experiment_id":"EXP-42"}
~~~

### candidate_build

~~~json
{"schema_version":1,"request_id":"req_candidate_42_1","action":"candidate_build","experiment_id":"EXP-42"}
~~~

### review_record

~~~json
{"schema_version":1,"request_id":"req_review_42_1","action":"review_record","experiment_id":"EXP-42","candidate_id":"C-42-...","outcome":"PASS","notes":"Human review notes."}
~~~

`outcome` is `PASS` or `FAIL`.

### decision_submit

~~~json
{"schema_version":1,"request_id":"req_decision_42_promising","action":"decision_submit","experiment_id":"EXP-42","to_state":"PROMISING","reason":"Human-approved promotion after Review PASS.","previous_decision_id":"req_previous_or_null"}
~~~

Use the current protected `last_decision_id`. JSON `null` is allowed when the authoritative state has no previous decision.

### rehearse

~~~json
{"schema_version":1,"request_id":"req_rehearse_42_1","action":"rehearse","experiment_id":"EXP-42"}
~~~

### integrate

~~~json
{"schema_version":1,"request_id":"req_integrate_42_1","action":"integrate","experiment_id":"EXP-42"}
~~~

### integrate_finalize

~~~json
{"schema_version":1,"request_id":"req_integrate_finalize_42_52","action":"integrate_finalize","experiment_id":"EXP-42","pr_number":"52"}
~~~

Run only after the Integration PR is actually merged.

## Archive actions

### archive

~~~json
{"schema_version":1,"request_id":"req_archive_42_1","action":"archive","experiment_id":"EXP-42","mode":"ATOMIC_DELETE"}
~~~

`mode` is `ATOMIC_DELETE` or `RETAIN_BRANCH`. Do not choose the mode on the user's behalf.

### archive_abort

~~~json
{"schema_version":1,"request_id":"req_archive_abort_42_1","action":"archive_abort","experiment_id":"EXP-42","archive_id":"A-42-1","reason":"Human requested abort before Claim."}
~~~

Abort is valid only while the Archive is still PREPARED.
