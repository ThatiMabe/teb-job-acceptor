# Builds dist\teb-job-acceptor.zip to copy to another PC (USB stick or similar).
# The zip CONTAINS YOUR KEYS (.env): DeepSeek/OpenCode Go, Telegram bot, Notion. Keep it private;
# don't email it or put it in a shared folder. Delete it once installed.
$root  = $PSScriptRoot
$stage = Join-Path $root 'dist\teb-job-acceptor'
Remove-Item (Join-Path $root 'dist') -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force $stage | Out-Null

$files = 'agent_server.py','watcher.py','notion_log.py','daily_report.py','sites.json','notion.json',
         'README.md','AGENTS.md','status.cmd','install_task.ps1','setup.ps1','setup.cmd'
foreach ($f in $files) { Copy-Item (Join-Path $root $f) $stage }
Copy-Item (Join-Path $root 'extension') $stage -Recurse
Copy-Item (Join-Path $root 'tests') $stage -Recurse

# Write the keys file from this PC's Hermes settings, without showing them
& (Join-Path $root '.venv\Scripts\python.exe') -X utf8 -c @"
import sys; sys.path.insert(0, r'$root')
import watcher, notion_log
keys = {k: watcher.ENV.get(k) for k in ('OPENCODE_GO_API_KEY','TELEGRAM_BOT_TOKEN','TELEGRAM_HOME_CHANNEL')}
keys['NOTION_API_KEY'] = notion_log._token()
if watcher.ENV.get('TELEGRAM_EXTRA_CHAT_IDS'): keys['TELEGRAM_EXTRA_CHAT_IDS'] = watcher.ENV['TELEGRAM_EXTRA_CHAT_IDS']
missing = [k for k, v in keys.items() if not v]
if missing: raise SystemExit('Missing keys: ' + ', '.join(missing))
open(r'$stage\.env', 'w', encoding='utf-8').write('\n'.join(f'{k}={v}' for k, v in keys.items()) + '\n')
print('keys written:', ', '.join(keys))
"@
$zip = Join-Path $root 'dist\teb-job-acceptor.zip'
Compress-Archive -Path $stage -DestinationPath $zip -Force
Write-Output "Package: $zip ($([math]::Round((Get-Item $zip).Length/1KB)) KB)"
