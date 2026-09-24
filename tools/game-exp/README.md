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

## Tests

```bash
python -m unittest discover -s tools/game-exp/tests -v
```

The repository also contains a three-platform GitHub Actions workflow named `game-exp core tests`.

## Codex MCP

Phase 2B exposes the already-validated request core through the official Python MCP SDK v2. The MCP adapter does not write Git refs itself and does not add domain lifecycle authority.

Tools:

- `game_exp_status`: read repository/Ledger status.
- `game_exp_doctor`: inspect trust prerequisites.
- `game_exp_request_get`: reconcile one request from remote evidence.
- `game_exp_request_submit`: submit one operation envelope through the Trusted Writer.

`game_exp_request_submit` returning `ACCEPTED` means only that GitHub accepted the workflow dispatch. It does **not** mean the requested domain operation has been executed. Resolve it with `game_exp_request_get` until it becomes `COMMITTED`, `CONFLICT`, `REJECTED`, or remains `UNKNOWN`.

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

Codex CLI and the IDE extension share MCP configuration. The server uses stdio, so stdout is reserved for the MCP wire; operational logging must go to stderr.

This is intentionally a minimal MCP surface. Domain-specific tools such as `experiment_create`, `decision_submit`, and `archive_experiment` should not be exposed until the Trusted Core validates and applies those domain transitions rather than merely persisting an operation request.


## Source initialization

After a valid `experiment.bind` request has produced authoritative Binding/Manifest/State records in the protected Ledger, initialize source refs with:

```powershell
python tools/game-exp/cli.py --repo owner/repo initialize EXP-21
```

The client supplies only the canonical experiment ID. The trusted workflow reconstructs all other inputs from `game-exp/ledger`, verifies the original bound request digest and manifest digest, then atomically creates:

- `refs/heads/exp/<issue>` at a new initialization commit whose only change is the deterministic source manifest;
- an annotated `refs/tags/exp-base/<issue>` that still points to the frozen parent commit and records the initialization commit + initialization-plan digest in its tag message.

Re-running initialization is safe. If the experiment branch has advanced normally, the initializer verifies that the current branch is descended from the recorded initialization commit. Partial or mismatched refs fail closed and are never force-overwritten.
