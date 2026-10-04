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
$results = @()

function Get-File($url, $dest) {
    $w = New-Object Net.WebClient
    $w.DownloadFile($url, $dest)    # saved by this script itself: no Mark-of-the-Web
}
function Try-Run($label, [scriptblock]$cmd) {
    $out = & $cmd 2>&1 | Out-String
    $ok = $LASTEXITCODE -eq 0
    $script:results += [pscustomobject]@{ Test = $label; Result = $(if ($ok) { "PASS" } else { "BLOCKED/FAILED" }); Detail = $(if ($ok) { "" } else { ($out.Trim() -split "`n" | Select-Object -Last 2) -join " | " }) }
    Write-Host ("{0,-38} {1}" -f $label, $(if ($ok) { "PASS" } else { "BLOCKED/FAILED" }))
    if (-not $ok) { Write-Host ("    " + ($out.Trim() -split "`n" | Select-Object -Last 2) -join " | ") -ForegroundColor Yellow }
    return $ok
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
& $py -m pip install --no-warn-script-location --disable-pip-version-check pillow numpy pydantic fastapi uvicorn pywebview 2>&1 | Select-Object -Last 3 | ForEach-Object { Write-Host "    $_" }

Write-Host "4/4 importing each (the unsigned native modules are the question)..."
foreach ($m in @("PIL.Image", "numpy", "pydantic_core", "fastapi", "uvicorn", "webview")) {
    Try-Run "import $m" { & $py -c "import $m" } | Out-Null
}
Write-Host ""
$bad = @($results | Where-Object { $_.Result -ne "PASS" })
if ($bad.Count -eq 0) { Write-Host "ALL PASS: a signed Python can run photag's native modules on this PC. Tell Claude." -ForegroundColor Green }
else { Write-Host ("{0} of {1} were blocked/failed -- copy this whole window to Claude." -f $bad.Count, $results.Count) -ForegroundColor Yellow }
Write-Host "(You can delete $root now.)"
Read-Host "Press Enter to close" | Out-Null
