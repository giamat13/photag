# Runs INSIDE Windows Sandbox (started by photag.wsb): installs photag like a user would, exercises it,
# uninstalls it and writes a report to C:\photag-results\report.txt (a folder shared with the host).
$ErrorActionPreference = 'Continue'
$test = 'C:\photag-test'; $inst = 'C:\photag-installer'; $out = 'C:\photag-results'
$report = Join-Path $out 'report.txt'
Remove-Item $report -ErrorAction SilentlyContinue
$script:fail = 0
function Say($m) { Add-Content -Path $report -Value $m -Encoding UTF8 }
function Check($name, $ok, $extra = '') {
  if (-not $ok) { $script:fail++ }
  Say ("{0} {1}{2}" -f $(if ($ok) { 'PASS' } else { 'FAIL' }), $name, $(if ($extra) { "  [$extra]" } else { '' }))
}
function Api($method, $path, $body = $null, $timeoutSec = 120) {
  $p = @{ Uri = "http://127.0.0.1:8756$path"; Method = $method; TimeoutSec = $timeoutSec; UseBasicParsing = $true }
  if ($body -ne $null) { $p.Body = ($body | ConvertTo-Json -Depth 6); $p.ContentType = 'application/json' }
  try { return Invoke-RestMethod @p } catch { return @{ __error = $_.Exception.Message } }
}
function WaitJob($name, $timeoutSec = 300) {
  $t = Get-Date
  while (((Get-Date) - $t).TotalSeconds -lt $timeoutSec) {
    $j = Api 'GET' "/api/job/$name"
    if ($j.state -in 'done', 'error', 'idle') { return $j }
    Start-Sleep -Milliseconds 400
  }
  return @{ state = 'timeout' }
}

Say "photag clean-machine test, $(Get-Date -Format s)"
Say "Windows: $((Get-CimInstance Win32_OperatingSystem).Caption) build $([Environment]::OSVersion.Version)"
$wv = (Get-ItemProperty 'HKLM:\SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}' -ErrorAction SilentlyContinue).pv
Say "WebView2 runtime: $(if ($wv) { $wv } else { 'NOT FOUND' })"
Say "Python on PATH: $(if (Get-Command python -ErrorAction SilentlyContinue) { 'yes (unexpected)' } else { 'no (clean)' })"
Say ""

# ---- 1. install
$setup = Join-Path $inst 'photagSetup.exe'
Check 'installer file is there' (Test-Path $setup) $setup
$t = Get-Date
$p = Start-Process $setup -ArgumentList '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', "/LOG=$out\install.log" -Wait -PassThru
Check 'silent install succeeds' ($p.ExitCode -eq 0) ("exit {0}, {1:n0}s" -f $p.ExitCode, ((Get-Date) - $t).TotalSeconds)
$dir = Join-Path $env:LOCALAPPDATA 'Programs\photag'
foreach ($f in 'photag.exe', 'photag-backup.exe', 'LICENSE', 'THIRD_PARTY_NOTICES.md') { Check "installed file: $f" (Test-Path (Join-Path $dir $f)) }
Check 'Start-menu shortcut' (Test-Path (Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\photag.lnk'))

# ---- 2. first start (cold: nothing unpacked, no settings, no models)
$t = Get-Date
$app = Start-Process (Join-Path $dir 'photag.exe') -PassThru
$up = $false
while (((Get-Date) - $t).TotalSeconds -lt 240) {
  try { $s = Invoke-RestMethod 'http://127.0.0.1:8756/api/status' -TimeoutSec 3; $up = $true; break } catch { Start-Sleep -Seconds 1 }
  if ($app.HasExited) { break }
}
$secs = ((Get-Date) - $t).TotalSeconds
Check 'the app starts and its server answers' $up ("{0:n0}s, exited={1}" -f $secs, $app.HasExited)
Check 'first start is quick (< 12 s)' ($up -and $secs -lt 12) ("{0:n0}s" -f $secs)
$slog = Join-Path $env:APPDATA 'photag\startup.log'
if (Test-Path $slog) { Copy-Item $slog (Join-Path $out 'startup.log') -Force; Say "startup.log copied to results" }
$hasWin = $false
for ($i = 0; $i -lt 30 -and -not $hasWin; $i++) {
  $hasWin = @(Get-Process photag -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowHandle -ne 0 }).Count -gt 0
  if (-not $hasWin) { Start-Sleep -Seconds 1 }
}
Check 'the window opens (within 30 s)' $hasWin

if ($up) {
  Check 'version reported' ($s.version -ne $null) $s.version
  # ---- 3. work with real files
  $lib = 'C:\photag-lib'; New-Item -ItemType Directory -Force $lib | Out-Null
  Api 'POST' '/api/settings/library' @{ path = $lib } | Out-Null
  $files = @(Get-ChildItem (Join-Path $test 'photos') -File | ForEach-Object { $_.FullName })
  Api 'POST' '/api/import-folder' @{ paths = $files; keywords = @(); album = $null } | Out-Null
  $j = WaitJob 'import'
  $ph = Api 'GET' '/api/photos?limit=99'
  Check 'import works' ($j.state -eq 'done' -and @($ph).Count -eq $files.Count) ("{0} of {1}" -f @($ph).Count, $files.Count)
  $bad = 0; foreach ($x in $ph) { try { (Invoke-WebRequest "http://127.0.0.1:8756/thumb/$($x.id)" -UseBasicParsing -TimeoutSec 60) | Out-Null } catch { $bad++ } }
  Check 'thumbnails (photos and the video: needs the bundled ffmpeg)' ($bad -eq 0) "$bad failed"
  $map = Api 'POST' '/api/map' @{ ids = @($ph | ForEach-Object { $_.id }) }
  Check 'map: photos with GPS found' (@($map.points).Count -ge 3) @($map.points).Count
  $img = $ph | Where-Object { $_.filename -eq 'big_q97_exif.jpg' } | Select-Object -First 1
  if ($img) {
    Api 'POST' "/api/photo/$($img.id)/compress" @{ options = @{} } | Out-Null
    $j = WaitJob 'compress'
    Check 'photo compression with verification (bundled ffmpeg SSIM)' ($j.state -eq 'done' -and $j.result.applied) $j.error
  }
  Api 'POST' '/api/backup/run' | Out-Null
  $j = WaitJob 'backup'
  $b = Api 'GET' '/api/backup'
  Check 'backup (catalog + photos and videos)' ($j.state -eq 'done' -and @($b.snapshots).Count -ge 1 -and $b.snapshots[0].includes_media) $j.error
  if ($img) {
    Api 'PATCH' '/api/photos' @{ ids = @($img.id); rating = 5 } | Out-Null
    Api 'POST' '/api/backup/restore' @{ name = $b.snapshots[0].name; media = $true } | Out-Null
    $j = WaitJob 'backup'
    $r = (Api 'GET' "/api/photo/$($img.id)").rating
    Check 'restore from the backup (rating back to 0)' ($j.state -eq 'done' -and $r -eq 0) $j.error
  }
  $u = Api 'GET' '/api/update/check?force=1'
  Check 'update check reaches GitHub (404 = no release yet is fine)' ($u.available -eq $false -and ($u.error -like 'HTTP 404*' -or $u.error -eq $null)) $u.error

  # ---- 4. the background backup: scheduled task + Run entry + the small program
  Start-Sleep -Seconds 5
  $tq = schtasks /Query /TN photag-backup /V /FO LIST 2>&1 | Out-String
  Set-Content (Join-Path $out 'task.txt') $tq
  Check 'scheduled task photag-backup exists (created without admin rights)' ($tq -match 'photag-backup' -and $tq -match 'photag-backup.exe')
  $run = (Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' -ErrorAction SilentlyContinue).'photag-backup'
  Check 'Run entry at sign-in exists' ($run -like '*photag-backup.exe*') $run
  $h = (Api 'GET' '/api/backup').health
  Check 'app reports the task as registered' ($h.task.registered -eq $true -and $h.task.start_when_available -eq $true) ($h.task | ConvertTo-Json -Compress)
  $t = Get-Date
  $bp = Start-Process (Join-Path $dir 'photag-backup.exe') -ArgumentList '--backup', '--force' -Wait -PassThru
  Check 'photag-backup.exe --backup --force runs and exits' ($bp.ExitCode -eq 0) ("exit {0}, {1:n1}s" -f $bp.ExitCode, ((Get-Date) - $t).TotalSeconds)
  $blog = Join-Path $lib 'backups\backup.log'
  if (Test-Path $blog) { Say ("backup.log: " + (Get-Content $blog -Tail 1)) }
  Check 'no photag-backup.exe left running' ((Get-Process 'photag-backup' -ErrorAction SilentlyContinue) -eq $null)
}

# ---- 4b. code update inside the real packaged exe: a newer `app` package next to photag.exe wins, a broken one is rolled back
function StopApp { Get-Process photag -ErrorAction SilentlyContinue | Stop-Process -Force; Start-Sleep -Seconds 3 }
function WaitApi($sec = 60) { $t = Get-Date; while (((Get-Date) - $t).TotalSeconds -lt $sec) { try { return (Invoke-RestMethod 'http://127.0.0.1:8756/api/status' -TimeoutSec 3) } catch { Start-Sleep -Seconds 1 } }; return $null }
if ($up) {
  StopApp
  $builtin = $s.version
  $code = Join-Path $dir 'code'
  Remove-Item $code -Recurse -Force -ErrorAction SilentlyContinue
  Copy-Item (Join-Path $test 'codeupdate\good') $code -Recurse
  Start-Process (Join-Path $dir 'photag.exe') | Out-Null
  $s2 = WaitApi
  Check 'code update: the downloaded code is used by the packaged exe' ($s2 -ne $null -and $s2.version -eq '9.9.9') ("{0} (built in: {1})" -f $(if ($s2) { $s2.version } else { 'no answer' }), $builtin)
  Say ('startup.log (good code): ' + ((Get-Content (Join-Path $env:APPDATA 'photag\startup.log') -Tail 12) -join ' | '))
  Say ('code folder: ' + ((Get-ChildItem $code -Recurse | ForEach-Object { $_.FullName.Substring($code.Length) }) -join ', '))
  Check 'code update: a good start was confirmed (counter back to 0)' ((Get-Content (Join-Path $code '.boots') -ErrorAction SilentlyContinue) -eq '0') (Get-Content (Join-Path $code '.boots') -ErrorAction SilentlyContinue)
  StopApp
  Remove-Item $code -Recurse -Force -ErrorAction SilentlyContinue
  Copy-Item (Join-Path $test 'codeupdate\broken') $code -Recurse
  Start-Process (Join-Path $dir 'photag.exe') | Out-Null
  $s3 = WaitApi 90
  Check 'code update: code that cannot be loaded is rolled back and the built-in version starts' ($s3 -ne $null -and $s3.version -eq $builtin) ("{0}" -f $(if ($s3) { $s3.version } else { 'no answer' }))
  Check 'code update: the broken code was put aside (code.bad)' (Test-Path (Join-Path $dir 'code.bad'))
  if (Test-Path (Join-Path $env:APPDATA 'photag\startup.log')) { Say ("startup.log (last lines): " + ((Get-Content (Join-Path $env:APPDATA 'photag\startup.log') -Tail 4) -join ' | ')) }
  StopApp
  Remove-Item (Join-Path $dir 'code'), (Join-Path $dir 'code.bad') -Recurse -Force -ErrorAction SilentlyContinue
}

# ---- 5. close the app, uninstall, check what stays
Get-Process photag -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Seconds 2
$un = Get-ChildItem $dir -Filter 'unins*.exe' -ErrorAction SilentlyContinue | Select-Object -First 1
Check 'uninstaller exists' ($un -ne $null)
if ($un) { $p = Start-Process $un.FullName -ArgumentList '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART' -Wait -PassThru; Check 'silent uninstall succeeds' ($p.ExitCode -eq 0) $p.ExitCode }
Start-Sleep -Seconds 3
Check 'program files removed' (-not (Test-Path (Join-Path $dir 'photag.exe')))
schtasks /Query /TN photag-backup 2>&1 | Out-Null
Check 'scheduled task removed by the uninstaller' ($LASTEXITCODE -ne 0)
$run = (Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' -ErrorAction SilentlyContinue).'photag-backup'
Check 'Run entry removed by the uninstaller' ($run -eq $null) $run
Check 'photos, catalog and backups are NOT deleted by the uninstaller' ((Test-Path 'C:\photag-lib\catalog.db') -and (Test-Path 'C:\photag-lib\backups'))
Check 'settings are kept' (Test-Path (Join-Path $env:APPDATA 'photag\config.json'))

Say ""
Say ("RESULT: {0}" -f $(if ($script:fail -eq 0) { 'ALL PASSED' } else { "$($script:fail) FAILED" }))
Set-Content (Join-Path $out 'DONE.txt') (Get-Date -Format s)
