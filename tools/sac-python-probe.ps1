<#
 A 3-minute experiment, NOT part of photag: does Windows Smart App Control let a SIGNED Python interpreter run the unsigned
 native modules (.pyd files) that photag needs? If yes, photag can later be shipped as Python code + python.org's own signed
 python.exe -- with no unsigned photag.exe at all. If a module is blocked, that route is closed.

 It downloads python.org's embeddable Python 3.12 (signed by the Python Software Foundation) and a few PyPI wheels into
 %TEMP%\photag-probe (nothing else is touched, nothing is installed system-wide), then tries to run and import them,
 and prints PASS / BLOCKED for each. Delete the folder afterwards.
#>
$ErrorActionPreference = "Continue"
$ProgressPreference = "SilentlyContinue"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$root = Join-Path $env:TEMP "photag-probe"
if (Test-Path $root) { Remove-Item -Recurse -Force $root }
New-Item -ItemType Directory -Force -Path $root | Out-Null
$py = Join-Path $root "python\python.exe"
$results = New-Object System.Collections.ArrayList

function Get-File($url, $dest) {
    $w = New-Object Net.WebClient
    $w.DownloadFile($url, $dest)    # saved by this script itself: no Mark-of-the-Web
}
# PASS = ran fine.  BLOCKED = Windows said an Application Control policy blocked it.  OTHER = failed for another reason (e.g. not installed).
function Try-Run($label, [scriptblock]$cmd) {
    $out = (& $cmd 2>&1 | Out-String).Trim()
    $code = $LASTEXITCODE
    $verdict = "PASS"
    if ($code -ne 0) { $verdict = if ($out -match "Application Control|blocked|DLL load failed|policy") { "BLOCKED" } else { "OTHER" } }
    [void]$results.Add([pscustomobject]@{ Test = $label; Result = $verdict })
    $color = switch ($verdict) { "PASS" { "Green" } "BLOCKED" { "Red" } default { "Yellow" } }
    Write-Host ("{0,-38} {1}" -f $label, $verdict) -ForegroundColor $color
    if ($verdict -ne "PASS") {
        $tail = ($out -split "`n" | Select-Object -Last 3) -join " | "
        Write-Host ("    " + $tail) -ForegroundColor DarkGray
    }
    return ($verdict -eq "PASS")
}

Write-Host "1/4 downloading python.org's embeddable Python 3.12.10 (signed by the Python Software Foundation)..."
$zip = Join-Path $root "py.zip"
Get-File "https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip" $zip
Add-Type -AssemblyName System.IO.Compression.FileSystem
[IO.Compression.ZipFile]::ExtractToDirectory($zip, (Join-Path $root "python"))

Write-Host "2/4 can the signed python.exe itself run?"
if (-not (Try-Run "python.exe runs" { & $py -c "print('hello')" })) {
    Write-Host "`nEven python.exe is blocked: the Python route is closed on this PC." -ForegroundColor Red
    Read-Host "Press Enter to close" | Out-Null; exit 1
}

Write-Host "3/4 getting pip and a few wheels (pure downloads from pypi.org)..."
$pth = Get-ChildItem (Join-Path $root "python") -Filter "python*._pth" | Select-Object -First 1
$txt = (Get-Content $pth.FullName) -replace "^#import site", "import site"
$txt += "Lib\site-packages"
Set-Content $pth.FullName $txt
Get-File "https://bootstrap.pypa.io/get-pip.py" (Join-Path $root "get-pip.py")
& $py (Join-Path $root "get-pip.py") --no-warn-script-location 2>&1 | Out-Null
foreach ($pkg in @("pillow", "numpy", "pydantic", "fastapi", "uvicorn", "pywebview")) {
    # --only-binary: take ready-made wheels only (nothing is compiled on this PC)
    $o = (& $py -m pip install --only-binary=:all: --no-warn-script-location --disable-pip-version-check $pkg 2>&1 | Out-String).Trim()
    $okInstall = $LASTEXITCODE -eq 0
    Write-Host ("    pip install {0,-10} {1}" -f $pkg, $(if ($okInstall) { "ok" } else { "FAILED: " + (($o -split "`n" | Select-Object -Last 1)) }))
}

Write-Host "4/4 importing each (the unsigned native modules are the question)..."
foreach ($m in @("PIL.Image", "numpy", "pydantic_core", "fastapi", "uvicorn", "webview", "clr_loader")) {
    [void](Try-Run "import $m" { & $py -c "import $m" })
}
Write-Host ""
$blocked = @($results | Where-Object { $_.Result -eq "BLOCKED" })
$other = @($results | Where-Object { $_.Result -eq "OTHER" })
$pass = @($results | Where-Object { $_.Result -eq "PASS" })
if ($blocked.Count -gt 0) { Write-Host ("{0} module(s) BLOCKED by Windows: that route is closed (or needs those files signed). Copy this window to Claude." -f $blocked.Count) -ForegroundColor Red }
elseif ($other.Count -gt 0) { Write-Host ("{0} passed, {1} failed for another reason (usually not installed) -- NOT a block, but not proven either. Copy this window to Claude." -f $pass.Count, $other.Count) -ForegroundColor Yellow }
else { Write-Host ("ALL {0} PASS: a signed Python ran photag's native modules on this PC. Tell Claude." -f $pass.Count) -ForegroundColor Green }
Write-Host "(You can delete $root now.)"
Read-Host "Press Enter to close" | Out-Null
