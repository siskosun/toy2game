param([string]$Repo = 'siskosun/toy2game')

$ErrorActionPreference = 'Stop'

function Get-LedgerHead {
  (gh api "repos/$Repo/git/ref/heads/game-exp/ledger" --jq '.object.sha').Trim()
}

function Encode-Payload([string]$json) {
  [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($json))
}

function Dispatch-And-Wait([string]$requestId, [string]$expectedHead, [string]$payloadB64, [bool]$expectSuccess) {
  $before = [DateTimeOffset]::UtcNow
  gh workflow run game-exp-trusted-writer.yml --repo $Repo --ref main `
    -f request_id=$requestId -f expected_head=$expectedHead -f payload_b64=$payloadB64 | Out-Null

  Start-Sleep -Seconds 2
  $run = gh run list --repo $Repo --workflow game-exp-trusted-writer.yml --event workflow_dispatch --limit 20 --json databaseId,createdAt,status,conclusion | ConvertFrom-Json |
    Where-Object { [DateTimeOffset]$_.createdAt -ge $before.AddSeconds(-5) } | Select-Object -First 1
  if (-not $run) { throw "Could not locate workflow run for $requestId" }

  gh run watch $run.databaseId --repo $Repo --exit-status 2>$null
  $final = gh run view $run.databaseId --repo $Repo --json conclusion,url | ConvertFrom-Json

  if ($expectSuccess -and $final.conclusion -ne 'success') { throw "Expected success for $requestId: $($final.url)" }
  if (-not $expectSuccess -and $final.conclusion -eq 'success') { throw "Expected rejection for $requestId: $($final.url)" }
  return $final
}

$nonce = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
$request = "req-phase1-final-$nonce"
$payload1 = '{"schema_version":1,"type":"phase1-final-probe","value":"A"}'
$payload2 = '{"schema_version":1,"type":"phase1-final-probe","value":"B"}'
$b64a = Encode-Payload $payload1
$b64b = Encode-Payload $payload2

Write-Host '[1/5] Normal trusted write'
$head0 = Get-LedgerHead
Dispatch-And-Wait $request $head0 $b64a $true | Out-Null
$head1 = Get-LedgerHead
if ($head1 -eq $head0) { throw 'Ledger head did not advance.' }
$record = gh api "repos/$Repo/contents/operations/$request.json?ref=game-exp/ledger" --jq '.content' |
  ForEach-Object { [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String(($_ -replace '\s',''))) } | ConvertFrom-Json
if ($record.request_id -ne $request) { throw 'Committed record is missing or mismatched.' }

Write-Host '[2/5] Same request + same payload is idempotent'
Dispatch-And-Wait $request $head0 $b64a $true | Out-Null
if ((Get-LedgerHead) -ne $head1) { throw 'Idempotent replay created a second commit.' }

Write-Host '[3/5] Same request + different payload is rejected'
Dispatch-And-Wait $request $head1 $b64b $false | Out-Null
if ((Get-LedgerHead) -ne $head1) { throw 'Conflict replay changed Ledger.' }

Write-Host '[4/5] Stale expected_head is rejected'
$staleRequest = "req-phase1-stale-$nonce"
Dispatch-And-Wait $staleRequest $head0 $b64a $false | Out-Null
if ((Get-LedgerHead) -ne $head1) { throw 'Stale-head request changed Ledger.' }

Write-Host '[5/5] Lost-response retry reconciles to committed remote record'
Dispatch-And-Wait $request $head0 $b64a $true | Out-Null
if ((Get-LedgerHead) -ne $head1) { throw 'Recovery retry created a second commit.' }

Write-Host ''
Write-Host 'TRUSTED_WRITER_FINAL_GATE=PASS'
Write-Host "request_id=$request"
Write-Host "ledger_head=$head1"
