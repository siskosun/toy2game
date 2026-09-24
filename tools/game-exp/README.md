# game-exp Phase 2 request core

This directory contains the minimal trusted request client built on the frozen V1.3 protocol.

## Trust boundary

The CLI never writes `game-exp/ledger` directly.

```text
CLI / future MCP
    ↓
workflow_dispatch request
    ↓
game-exp trusted writer
    ↓
protected game-exp/ledger
```

GitHub Rulesets and the dedicated Deploy Key remain the write authority.

## Result states

- `ACCEPTED`: GitHub accepted a Trusted Writer workflow run. This is not yet a Ledger commit.
- `COMMITTED`: the remote Ledger contains the matching request record.
- `CONFLICT`: the request ID or expected Ledger head conflicts with remote state.
- `UNKNOWN`: the client cannot prove whether the remote mutation happened. Reconcile or retry with the same request ID.
- `REJECTED`: local validation or a proven remote rejection failed the request.

A network timeout during dispatch is `UNKNOWN`, not `REJECTED`.

## Commands

From the repository root:

```powershell
python tools/game-exp/cli.py --repo siskosun/toy2game --json status
python tools/game-exp/cli.py --repo siskosun/toy2game --json doctor
```

Submit a request:

```powershell
@'
{
  "hypothesis": "three roles improve readability"
}
'@ | Set-Content -Encoding utf8 $env:TEMP\game-exp-input.json

python tools/game-exp/cli.py --repo siskosun/toy2game --json request experiment.create `
  --input-file $env:TEMP\game-exp-input.json
```

The command returns a stable `request_id`. If the workflow result is uncertain:

```powershell
python tools/game-exp/cli.py --repo siskosun/toy2game --json reconcile <request_id>
```

Re-running `request` with the same request ID and identical payload reuses the original `expected_head`. A different payload with the same request ID is rejected locally before dispatch.

For PowerShell, prefer `--input-file` over inline JSON to avoid shell quoting changes.

## Local journal

The CLI keeps a non-authoritative recovery journal under the Git common directory:

```text
.git/game-exp/requests/
```

It exists only to remember the original payload digest, expected Ledger head and workflow URL. The remote Ledger remains authoritative.

## Project validation policy

Repository-specific build assumptions are defined in `.game-exp/project-policy.json`, not hard-coded into Candidate, Rehearsal, or archived-snapshot workflows.

Schema v1 currently supports the `node-npm` adapter and declares:

- install/test/build commands as argv arrays (no shell command strings);
- Candidate paths to package;
- Candidate paths that must exist in the trusted archive.

The trusted workflows load this policy from the immutable `github.workflow_sha`. Experiment branches cannot alter the policy used to validate themselves. The Candidate receipt records the policy digest, so changing project validation rules changes Candidate identity evidence.

This is the extension point for future adapters such as Godot. Adding an adapter should extend `project_policy.py` and its tests rather than duplicating lifecycle workflows.

## Tests

```bash
python -m unittest discover -s tools/game-exp/tests -v
```

The repository also contains a three-platform GitHub Actions workflow named `game-exp core tests`.

## Codex MCP

The Codex adapter uses the official Python MCP SDK v2 over stdio and delegates to the same validated Client / Trusted Domain Core. MCP never gets direct Git authority.

Normal Harness use should prefer domain tools:

Read / projection:
- `game_exp_status`
- `game_exp_board`
- `game_exp_doctor`
- `game_exp_experiment_get`
- `game_exp_request_get`

Lifecycle / workflow:
- `game_exp_experiment_bind`
- `game_exp_initialize`
- `game_exp_candidate_build`
- `game_exp_review_record`
- `game_exp_decision_submit`
- `game_exp_rehearse`
- `game_exp_integrate`
- `game_exp_integrate_finalize`
- `game_exp_archive`
- `game_exp_archive_abort`

Low-level fallback:
- `game_exp_request_submit`

## Codex Board and Skill

The repo-local `game-exp` plugin is enabled from `.codex/config.toml` and packages the game-exp Skill. Current plugin version: `0.1.2`.

Normal users do not need to remember MCP tool names. Examples:

- `打开 game-exp 面板`
- `继续 EXP-42，告诉我下一步`
- `这个 Candidate 我评审为 PASS`
- `把 EXP-42 晋级到 PROMISING`
- `归档 EXP-42，删除实验分支`

For `打开 game-exp 面板`, the Skill calls `game_exp_board` and renders one consistent protected-Ledger snapshot with:

- experiment / title;
- lifecycle;
- health;
- Candidate / Review;
- Rehearsal / Integration / Archive;
- next gate.

Board health is a safety gate, not decoration. A failed Binding/request/Manifest digest chain is rendered as `health=FAIL` and `DO_NOT_USE_RECREATE_EXPERIMENT`; the Skill must not recommend normal lifecycle work for that experiment.

The current Codex 0.155.1 host has MCP Apps rendering feature flags disabled/under development. Therefore this is a Harness-native structured/text Board, not a persistent graphical panel. The future graphical Board should reuse the same read-only projection.

Typical Codex flow:

1. Call `game_exp_experiment_get` before changing an existing experiment.
2. Use the domain tool for the next lifecycle action.
3. If a request-style tool returns `ACCEPTED` or `UNKNOWN`, resolve it with `game_exp_request_get`.
4. Re-read `game_exp_experiment_get` after the authoritative mutation lands.
5. Never infer success from a button/tool call alone; the protected Ledger and trusted workflows remain authoritative.

`game_exp_review_record` defaults to the current Candidate when candidate_id is omitted. `game_exp_decision_submit` defaults to the current protected `last_decision_id` when previous_decision_id is omitted, preserving optimistic-concurrency binding without forcing the user to copy IDs manually.

The MCP caller does not authenticate Review or Decision authority. The Trusted Writer independently resolves the GitHub actor and repository permission.

`game_exp_experiment_bind` uses `manifest.operation_id` as its request id. If a request_id is also supplied, it must match exactly.

Install the MCP dependency into an isolated local environment:

```powershell
uv venv .game-exp/mcp-venv
uv pip install --python .game-exp/mcp-venv/Scripts/python.exe -r tools/game-exp/requirements-mcp.txt
```

Register the local stdio server with Codex on Windows:

```powershell
$root = (Get-Location).Path
codex mcp add game-exp --env GAME_EXP_REPO=siskosun/toy2game -- `
  "$root\.game-exp\mcp-venv\Scripts\python.exe" `
  "$root\tools\game-exp\mcp_server.py"
```

Verify:

```powershell
codex mcp list
```

Codex CLI and the IDE extension share MCP configuration. The server uses stdio, so stdout is reserved for the MCP wire.

The Harness-native text Board is implemented and validated. Only the graphical MCP Apps Board remains deferred: on the tested Codex 0.155.1 host, MCP Apps rendering remains behind disabled under-development feature flags. Any future graphical Board must consume the same read-only projection without becoming the authority.


## Source initialization

After a valid `experiment.bind` request has produced authoritative Binding/Manifest/State records in the protected Ledger, initialize source refs with:

```powershell
python tools/game-exp/cli.py --repo owner/repo initialize EXP-21
```

The client supplies only the canonical experiment ID. The trusted workflow reconstructs all other inputs from `game-exp/ledger`, verifies the original bound request digest and manifest digest, then atomically creates:

- `refs/heads/exp/<issue>` at a new initialization commit whose only change is the deterministic source manifest;
- an annotated `refs/tags/exp-base/<issue>` that still points to the frozen parent commit and records the initialization commit + initialization-plan digest in its tag message.

Re-running initialization is safe. If the experiment branch has advanced normally, the initializer verifies that the current branch is descended from the recorded initialization commit. Partial or mismatched refs fail closed and are never force-overwritten.


## Trusted Integration

Integration is deliberately two-phase. A selected experiment is not considered integrated merely because a Rehearsal exists.

Create or reuse the exact Integration PR:

```powershell
python tools/game-exp/cli.py --repo siskosun/toy2game --json integrate EXP-21
```

The proposal workflow requires the current lifecycle to be `SELECTED`, requires the current Rehearsal to target the current `main`, and creates a deterministic branch:

`game-exp/integration/<issue>/<rehearsal-id>`

The proposal commit has exactly one parent (the rehearsed main) and its tree is exactly the trusted Rehearsal `integration_tree_sha`. The workflow opens a normal PR against `main`; it does not mark the experiment integrated and it does not bypass the protected-main PR rule.

After that PR is actually merged, finalize it:

```powershell
python tools/game-exp/cli.py --repo siskosun/toy2game --json integrate-finalize EXP-21 --pr-number 77
```

The trusted finalize workflow independently verifies the merged PR, head tree, merge tree, merge ancestry in current main, workflow identity, current Candidate and current Rehearsal. Only then does the protected Ledger receive `integration.register` and lifecycle change from `SELECTED` to `INTEGRATED`.

If `main` advances before the Integration PR is prepared, rerun `rehearse EXP-21` first. SELECTED experiments are allowed to refresh their Rehearsal without changing lifecycle.
