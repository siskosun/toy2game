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
