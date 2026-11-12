# Registers the Work Acceptor to run 24/7: starts at logon, restarts if it stops.
# Run once:  powershell -ExecutionPolicy Bypass -File install_task.ps1
$root = $PSScriptRoot
$py   = Join-Path $root '.venv\Scripts\pythonw.exe'   # no console window
$action   = New-ScheduledTaskAction -Execute $py -Argument "`"$root\agent_server.py`" --quiet" -WorkingDirectory $root
$triggers = @(
  (New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME),
  # watchdog: every 5 minutes; the agent exits at once if it is already running
  (New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 5))
)
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
  -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew `
  -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName 'Work Acceptor' -Action $action -Trigger $triggers -Settings $settings `
  -Description 'Work Acceptor agent: receives checks from the Chrome extension, alerts on new jobs (teb-job-acceptor\agent_server.py)' -Force | Out-Null
Write-Output "Registered 'Work Acceptor'. Start now with: Start-ScheduledTask 'Work Acceptor'"

# 18:00 daily report (Telegram + Notion)
$ra = New-ScheduledTaskAction -Execute $py -Argument "`"$root\daily_report.py`"" -WorkingDirectory $root
$rs = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
  -ExecutionTimeLimit (New-TimeSpan -Minutes 10)
Register-ScheduledTask -TaskName 'Work Acceptor Daily Report' -Action $ra -Trigger (New-ScheduledTaskTrigger -Daily -At 18:00) `
  -Settings $rs -Description 'Job Acceptor (TEB Construction) 18:00 report to Telegram and Notion' -Force | Out-Null
Write-Output "Registered 'Work Acceptor Daily Report' (18:00 daily)."
