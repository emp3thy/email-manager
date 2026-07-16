# register-task-windows.ps1 — register the always-on run with Windows Task Scheduler.
#
# Creates a task that runs scripts/autonomous-run.ps1 every 15 minutes, indefinitely,
# under your user account (so it can reach your local MCP servers + OAuth tokens).
#
# Usage (from a normal PowerShell prompt, no admin needed for a user task):
#   powershell -ExecutionPolicy Bypass -File scripts\register-task-windows.ps1
#
# To change the cadence, edit $IntervalMinutes. To remove the task:
#   Unregister-ScheduledTask -TaskName "EmailManager-Autonomous" -Confirm:$false

$ErrorActionPreference = 'Stop'

$TaskName        = 'EmailManager-Autonomous'
$IntervalMinutes = 15
$RepoRoot        = Split-Path -Parent $PSScriptRoot
$RunScript       = Join-Path $RepoRoot 'scripts\autonomous-run.ps1'

if (-not (Test-Path $RunScript)) {
    throw "Cannot find $RunScript"
}

$Action = New-ScheduledTaskAction `
    -Execute 'powershell.exe' `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$RunScript`"" `
    -WorkingDirectory $RepoRoot

# Repeat every N minutes forever, starting a minute from now.
$Trigger = New-ScheduledTaskTrigger -Once -At ((Get-Date).AddMinutes(1)) `
    -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes)

# Run only when a user is logged on (needed for interactive OAuth token access).
$Principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive

$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Principal $Principal `
    -Settings $Settings `
    -Description 'Always-on email processing: runs the autonomous clean-inbox workflow every 15 minutes.' `
    -Force

Write-Host "Registered scheduled task '$TaskName' (every $IntervalMinutes min)."
Write-Host "Remove with: Unregister-ScheduledTask -TaskName '$TaskName' -Confirm:`$false"
