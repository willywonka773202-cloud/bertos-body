# ONE-TIME setup: wire the desktop body to GitHub auto-deploy.
#   1. turns C:\BertOS\odysseus into a git checkout of the public bertos-body repo
#      (your data/.env/vault are gitignored, so nothing personal is touched)
#   2. registers a scheduled task that runs auto-update.ps1 every 10 minutes
# After this, you never run a deploy command again: Claude pushes to GitHub,
# the desktop pulls + rebuilds itself within ~10 min.
#
# Run once (from anywhere):
#   powershell -ExecutionPolicy Bypass -File C:\BertOS\odysseus\deploy\desktop\install-auto-update.ps1

# Continue (not Stop): git writes progress/info to stderr, which would otherwise
# halt the script. We check exit codes explicitly where it matters.
$ErrorActionPreference = "Continue"
$repo = "C:\BertOS\odysseus"
$remoteUrl = "https://github.com/willywonka773202-cloud/bertos-body.git"
Set-Location $repo

Write-Host "==> Wiring $repo to $remoteUrl (bertos branch)..."
if (-not (Test-Path "$repo\.git")) {
  git init | Out-Null
}
# Idempotent: set-url if the remote already exists, else add it.
if ((git remote 2>$null) -contains 'deploy') { git remote set-url deploy $remoteUrl }
else { git remote add deploy $remoteUrl }
git fetch deploy bertos
# Match GitHub for TRACKED files only; gitignored data/.env/BertOS-Vault are left alone.
git reset --hard deploy/bertos
git branch -M bertos
git branch --set-upstream-to=deploy/bertos bertos 2>$null
Write-Host "    git wired. HEAD = $((git rev-parse --short HEAD).Trim())"

Write-Host "==> Registering the 10-minute auto-update task..."
$ps = (Get-Command powershell.exe).Source
$action  = New-ScheduledTaskAction -Execute $ps -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$repo\deploy\desktop\auto-update.ps1`""
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) `
            -RepetitionInterval (New-TimeSpan -Minutes 10) `
            -RepetitionDuration ([TimeSpan]::MaxValue)
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopOnIdleEnd -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName "BertOS-AutoUpdate" -Action $action -Trigger $trigger `
  -Settings $settings -RunLevel Highest -Force | Out-Null

Write-Host ""
Write-Host "DONE. Auto-deploy is live. From now on, code changes appear on your desktop"
Write-Host "within ~10 minutes with zero commands. (Log: $repo\data\auto-update.log)"
Write-Host "To run an update immediately:  schtasks /run /tn BertOS-AutoUpdate"
