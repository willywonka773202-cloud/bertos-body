# BertOS body auto-updater.
# Pulls the latest bertos-body from GitHub and rebuilds the Docker stack ONLY
# when there's a new commit. Registered as a 10-minute scheduled task by
# install-auto-update.ps1, so you never run a deploy command again — Claude
# pushes to GitHub and this picks it up. Logs to data\auto-update.log.
#
# Your data/, .env, and BertOS-Vault are gitignored, so `git reset --hard`
# only ever touches code — your mail/calendar/memory/secrets are never disturbed.

$ErrorActionPreference = "SilentlyContinue"
$repo = "C:\BertOS\odysseus"
Set-Location $repo
$log = Join-Path $repo "data\auto-update.log"
function L($m) { "$(Get-Date -Format s)  $m" | Out-File -Append -Encoding utf8 $log }

# A lock so two ticks can't rebuild at once.
$lock = Join-Path $repo "data\.auto-update.lock"
if (Test-Path $lock) {
  $age = (Get-Date) - (Get-Item $lock).LastWriteTime
  if ($age.TotalMinutes -lt 20) { return }  # another run is in progress
}
New-Item -ItemType File -Path $lock -Force | Out-Null

try {
  git fetch deploy bertos 2>$null
  $local  = (git rev-parse HEAD 2>$null).Trim()
  $remote = (git rev-parse deploy/bertos 2>$null).Trim()
  if ($local -and $remote -and ($local -ne $remote)) {
    L "update $local -> $remote"
    git reset --hard deploy/bertos 2>&1 | Out-Null
    docker compose -f docker-compose.yml -f deploy\desktop\docker-compose.bertos.yml up -d --build 2>&1 | Out-Null
    tailscale serve --bg 7777 2>$null
    L "rebuilt + serving (now at $remote)"
  }
} finally {
  Remove-Item $lock -Force -ErrorAction SilentlyContinue
}
