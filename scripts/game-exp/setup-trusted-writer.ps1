param(
  [string]$Repo = 'siskosun/toy2game',
  [string]$KeyDir = 'C:\Users\57945\Documents\Codex\game-exp-toy2game-test\.game-exp-trusted'
)

$ErrorActionPreference = 'Stop'
$publicKeyPath = Join-Path $KeyDir 'writer.pub'
$privateKeyPath = Join-Path $KeyDir 'writer'

if (-not (Test-Path $publicKeyPath)) { throw "Missing public key: $publicKeyPath" }
if (-not (Test-Path $privateKeyPath)) { throw "Missing private key: $privateKeyPath" }

$existing = gh api "repos/$Repo/keys" | ConvertFrom-Json | Where-Object { $_.title -eq 'game-exp trusted writer' }
if (-not $existing) {
  $pub = (Get-Content $publicKeyPath -Raw).Trim()
  $payload = @{ title = 'game-exp trusted writer'; key = $pub; read_only = $false } | ConvertTo-Json -Compress
  $tmp = Join-Path $env:TEMP 'game-exp-deploy-key.json'
  [IO.File]::WriteAllText($tmp, $payload, (New-Object Text.UTF8Encoding($false)))
  gh api -X POST "repos/$Repo/keys" --input $tmp | Out-Null
  Remove-Item $tmp -Force
}

Get-Content $privateKeyPath -Raw | gh secret set GAME_EXP_WRITER_KEY --repo $Repo

$keys = gh api "repos/$Repo/keys" | ConvertFrom-Json
$key = $keys | Where-Object { $_.title -eq 'game-exp trusted writer' }
if (-not $key) { throw 'Deploy Key was not created.' }

$secretNames = gh secret list --repo $Repo --json name | ConvertFrom-Json | ForEach-Object name
if ($secretNames -notcontains 'GAME_EXP_WRITER_KEY') { throw 'Repository secret was not created.' }

Write-Host 'Trusted Writer credential setup complete.'
Write-Host "Deploy Key id: $($key.id)"
Write-Host 'Repository secret: GAME_EXP_WRITER_KEY'
Write-Host 'You may delete the local private key after the verification script passes.'
