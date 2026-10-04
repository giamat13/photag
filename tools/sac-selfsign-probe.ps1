<#
 A second experiment, NOT part of photag, for a throw-away Windows Sandbox only: if you make your OWN code-signing certificate,
 trust it on this PC and sign photag with it, does Smart App Control let photag.exe start?

 It needs an installed photag (run photag-install.ps1 first, or pass -Dir), and an elevated PowerShell (Windows Sandbox's
 user is an administrator). It:
   1. creates a self-signed code-signing certificate,
   2. puts it in this PC's Trusted Root and Trusted Publishers stores,   <-- this makes THIS PC trust code signed by it. Do NOT
      run this on a PC you care about; delete the certificate afterwards.
   3. signs every .exe / .dll / .pyd under the photag folder with it (a .pyd is signed by briefly being named .dll),
   4. starts photag.exe and tells you whether it was blocked.
#>
param([string]$Dir = (Join-Path $env:LOCALAPPDATA "Programs\photag"))
$ErrorActionPreference = "Stop"
if (-not (Test-Path (Join-Path $Dir "photag.exe"))) { throw "photag.exe not found in $Dir -- run the install script first (or pass -Dir)." }

Write-Host "1/4 making a self-signed code-signing certificate..."
$cert = New-SelfSignedCertificate -Type CodeSigningCert -Subject "CN=photag test signing" -CertStoreLocation Cert:\CurrentUser\My -NotAfter (Get-Date).AddYears(2)

$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
Write-Host "2/4 trusting it on this PC (Trusted Root + Trusted Publishers). Running as administrator: $admin"
$cer = Join-Path $env:TEMP "photag-test.cer"
Export-Certificate -Cert $cert -FilePath $cer | Out-Null
$where = @()
foreach ($store in @("Root", "TrustedPublisher")) {
    try { Import-Certificate -FilePath $cer -CertStoreLocation "Cert:\LocalMachine\$store" | Out-Null; $where += "LocalMachine\$store" }
    catch {
        Write-Host "  LocalMachine\$store refused ($($_.Exception.Message.Trim())); trying the current user's store (Windows may ask you to confirm: answer Yes)..."
        certutil -user -f -addstore $store $cer | Out-Null
        if ($LASTEXITCODE -eq 0) { $where += "CurrentUser\$store" } else { Write-Host "  CurrentUser\$store refused too." -ForegroundColor Yellow }
    }
}
Write-Host "  trusted in: $($where -join ', ')"

Write-Host "3/4 signing the program files (this takes a minute)..."
$files = Get-ChildItem -LiteralPath $Dir -Recurse -File | Where-Object { $_.Extension -in ".exe", ".dll", ".pyd" }
$n = 0; $bad = 0
foreach ($f in $files) {
    $path = $f.FullName
    $tmp = $null
    if ($f.Extension -eq ".pyd") { $tmp = $path + ".dll"; Move-Item -LiteralPath $path -Destination $tmp -Force; $path = $tmp }
    try {
        $r = Set-AuthenticodeSignature -FilePath $path -Certificate $cert -HashAlgorithm SHA256
        if ($r.Status -notin "Valid", "UnknownError") { $bad++ }
    } catch { $bad++ }
    if ($tmp) { Move-Item -LiteralPath $tmp -Destination $f.FullName -Force }
    $n++
    if ($n % 50 -eq 0) { Write-Host ("  {0} / {1}" -f $n, $files.Count) }
}
Write-Host ("  signed {0} files ({1} could not be signed)" -f $n, $bad)
(Get-AuthenticodeSignature (Join-Path $Dir "photag.exe")).Status | ForEach-Object { Write-Host "  photag.exe signature status: $_" }

Write-Host "4/4 starting photag.exe..."
try {
    Start-Process -FilePath (Join-Path $Dir "photag.exe") -WorkingDirectory $Dir
    Start-Sleep -Seconds 8
    if (Get-Process -Name photag -ErrorAction SilentlyContinue) { Write-Host "photag IS RUNNING: a self-made, locally trusted signature gets past Smart App Control here. Tell Claude." -ForegroundColor Green }
    else { Write-Host "photag.exe started but is not running (it may have been blocked while loading a DLL). Check %APPDATA%\photag\startup.log and tell Claude." -ForegroundColor Yellow }
} catch {
    Write-Host "STILL BLOCKED: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "A locally trusted self-signed certificate is not enough for Smart App Control. Tell Claude."
}
Read-Host "Press Enter to close" | Out-Null
