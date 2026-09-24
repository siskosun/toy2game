# game-exp Trusted Writer setup

Repository: `siskosun/toy2game`

The repository rulesets are already configured so that only a write-capable Deploy Key can bypass the game-exp Ledger and protected ref rules. The GitHub Actions built-in token was tested and is rejected by the Ledger ruleset.

## One-time manual credential setup

The private key was generated locally and is intentionally not committed. It is under:

`C:\\Users\\57945\\Documents\\Codex\\game-exp-toy2game-test\\.game-exp-trusted\\writer`

The matching public key is:

`C:\\Users\\57945\\Documents\\Codex\\game-exp-toy2game-test\\.game-exp-trusted\\writer.pub`

Run these commands yourself in PowerShell. Do not paste the private key into chat.

```powershell
$repo = 'siskosun/toy2game'
$keyDir = 'C:\\Users\\57945\\Documents\\Codex\\game-exp-toy2game-test\\.game-exp-trusted'

$pub = (Get-Content "$keyDir\\writer.pub" -Raw).Trim()
gh api -X POST "repos/$repo/keys" `
  -f title='game-exp trusted writer' `
  -f key="$pub" `
  -F read_only=false

Get-Content "$keyDir\\writer" -Raw |
  gh secret set GAME_EXP_WRITER_KEY --repo $repo
```

After the secret is stored successfully, the local private key can be deleted.

## Verify the trusted writer

```powershell
$repo = 'siskosun/toy2game'
$head = gh api "repos/$repo/git/ref/heads/game-exp/ledger" --jq '.object.sha'
$payload = '{"type":"trusted-writer-probe","schema_version":1}'
$b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($payload))

gh workflow run game-exp-trusted-writer.yml --repo $repo --ref main `
  -f request_id='req-trusted-writer-probe-1' `
  -f expected_head=$head `
  -f payload_b64=$b64
```

The run is valid only if:
- the workflow concludes successfully;
- `operations/req-trusted-writer-probe-1.json` exists on `game-exp/ledger`;
- the Ledger ruleset remains active;
- an ordinary GitHub token still cannot update `game-exp/ledger`.

## Current trust boundary

Until the Deploy Key and repository secret are configured, the repository is intentionally fail-closed: the Ledger is protected, but no trusted writer can mutate it.
