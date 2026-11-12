# Free screen + microphone recorder (ffmpeg). Claude starts and stops it.
#   record.ps1 start   -> begins recording both screens + mic
#   record.ps1 stop    -> ends the recording
# Output: teb-job-acceptor\recordings\demo-<time>.ts (MPEG-TS stays readable after a hard stop)
param([ValidateSet('start','stop','status')][string]$Action = 'status')

$root    = Split-Path $PSScriptRoot -Parent
$outDir  = Join-Path $root 'recordings'
$pidFile = Join-Path $outDir '.recorder.pid'
New-Item -ItemType Directory -Force $outDir | Out-Null

# Microphone by its stable id (the display name has a symbol that breaks some shells)
$mic = '@device_cm_{33D9A762-90C8-11D0-BD43-00A0C911CE86}\wave_{DDDEEC91-5212-40AA-88B3-4896A2237D01}'

switch ($Action) {
  'start' {
    if (Test-Path $pidFile) { Write-Output "Already recording (pid $(Get-Content $pidFile))"; exit 0 }
    $file = Join-Path $outDir ("demo-{0}.ts" -f (Get-Date -Format 'yyyy-MM-dd_HHmm'))
    # Main screen only (left, 1920x1200) at 2 frames/second: enough to read pages,
    # light enough for this PC (recording both screens with Screenpipe running crashed it on 2026-11-04)
    $args = @('-hide_banner','-loglevel','error',
              '-f','gdigrab','-framerate','2','-offset_x','0','-offset_y','0','-video_size','1920x1200','-i','desktop',
              '-f','dshow','-i',"audio=$mic",
              '-c:v','libx264','-preset','ultrafast','-crf','30','-pix_fmt','yuv420p',
              '-c:a','aac','-b:a','64k','-flush_packets','1','-f','mpegts', $file)
    # Launch through WMI so ffmpeg is not tied to the shell that started it
    $exe = (Get-Command ffmpeg).Source
    $cmd = '"' + $exe + '" ' + (($args | ForEach-Object { if ($_ -match '[\s{}\\@]') { '"' + $_ + '"' } else { $_ } }) -join ' ')
    $startup = New-CimInstance -ClassName Win32_ProcessStartup -ClientOnly -Property @{ ShowWindow = [uint16]0 }
    $r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{ CommandLine = $cmd; ProcessStartupInformation = $startup }
    if ($r.ReturnValue -ne 0) { Write-Output "Could not start recorder (code $($r.ReturnValue))"; exit 1 }
    $r.ProcessId | Set-Content $pidFile
    Write-Output "Recording started -> $file (pid $($r.ProcessId))"
  }
  'stop' {
    if (-not (Test-Path $pidFile)) { Write-Output 'Not recording'; exit 0 }
    $id = Get-Content $pidFile
    Stop-Process -Id $id -ErrorAction SilentlyContinue
    Remove-Item $pidFile
    Start-Sleep -Seconds 1
    $last = Get-ChildItem $outDir -Filter 'demo-*.ts' | Sort-Object LastWriteTime | Select-Object -Last 1
    Write-Output "Recording stopped -> $($last.FullName) ($([math]::Round($last.Length/1MB,1)) MB)"
  }
  'status' {
    if (Test-Path $pidFile) { Write-Output "Recording (pid $(Get-Content $pidFile))" } else { Write-Output 'Not recording' }
  }
}

