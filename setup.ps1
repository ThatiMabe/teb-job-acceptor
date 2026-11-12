# One-time setup of the Work Acceptor on a new Windows PC.
# Double-click setup.cmd (it runs this). Safe to run again.
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
Set-Location $root
function Say($m) { Write-Host "`n== $m" -ForegroundColor Cyan }

if (-not (Test-Path "$root\.env")) {
  Write-Host "The .env file with the keys is missing from this folder. Copy the whole package again." -ForegroundColor Red
  exit 1
}

Say "1/6 Python"
$py = $null
foreach ($c in @('py -3.12','py -3.11','py -3','python')) {
  try { $v = & cmd /c "$c --version 2>&1"; if ($v -match 'Python 3\.(1[0-9])') { $py = $c; break } } catch {}
}
if (-not $py) {
  Write-Host "Installing Python 3.12 (a few minutes)..."
  winget install -e --id Python.Python.3.12 --silent --accept-package-agreements --accept-source-agreements
  $env:Path = [Environment]::GetEnvironmentVariable('Path','Machine') + ';' + [Environment]::GetEnvironmentVariable('Path','User')
  $py = 'py -3.12'
}
Write-Host "Using: $py"

Say "2/6 Google Chrome"
$chrome = @("$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
            "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
            "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $chrome) {
  Write-Host "Installing Google Chrome..."
  winget install -e --id Google.Chrome --silent --accept-package-agreements --accept-source-agreements
}
Write-Host "Chrome OK"

Say "3/6 Program files (Python packages)"
if (-not (Test-Path "$root\.venv\Scripts\python.exe")) { & cmd /c "$py -m venv `"$root\.venv`"" }
& "$root\.venv\Scripts\python.exe" -m pip install -q --disable-pip-version-check requests playwright
& "$root\.venv\Scripts\python.exe" -c "import requests, playwright; print('packages OK')"

Say "4/6 Checking the keys (DeepSeek, Telegram, Notion)"
& "$root\.venv\Scripts\python.exe" -X utf8 -c @"
import watcher, notion_log, requests
e = watcher.ENV
ok = requests.get(f"https://api.telegram.org/bot{e['TELEGRAM_BOT_TOKEN']}/getMe", timeout=20).json().get('ok')
print('Telegram OK' if ok else 'Telegram FAILED')
r = requests.get('https://api.notion.com/v1/users/me', headers=notion_log._headers(), timeout=20)
print('Notion OK' if r.ok else f'Notion FAILED {r.status_code}')
"@

Say "5/6 Scheduled tasks (24/7 + 18:00 report)"
& powershell -ExecutionPolicy Bypass -File "$root\install_task.ps1"

Say "6/6 Starting the agent (opens its own Chrome window)"
Start-ScheduledTask 'Work Acceptor'
Start-Sleep 10
& "$root\.venv\Scripts\python.exe" -X utf8 -c "import watcher, socket; watcher.telegram('✅ Work Acceptor installed on ' + socket.gethostname() + '. Next: load the extension and sign in on that PC.')"
Start-Process explorer.exe $root

Write-Host @"

=====================================================================
 ALMOST DONE. Three things to do by hand on THIS PC:

 1. In the new Chrome window (tabs: Kandua Pro + Standard Bank):
    open a new tab, go to  chrome://extensions , switch on
    Developer mode, then DRAG the 'extension' folder from the
    File Explorer window that just opened onto that page.

 2. Sign in to Kandua and Standard Bank in that same window.
    Keep the window OPEN and NOT minimised (behind others is fine).

 3. Stop this PC sleeping: Settings > System > Power >
    'When plugged in, put my device to sleep after' = Never.
=====================================================================
"@ -ForegroundColor Yellow
