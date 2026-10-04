<#
 photag installer script (PowerShell). Downloads the latest portable photag from GitHub, checks its SHA-256, unpacks it into
 %LOCALAPPDATA%\Programs\photag, and makes shortcuts. It is plain text: read it before you run it.

 Why this exists: Windows Smart App Control blocks the unsigned photagSetup.exe outright, with no "run anyway" button. A script
 is not a program: Windows asks "Run / Cancel" for a downloaded .bat and lets a .ps1 be run explicitly, and a file that this script
 downloads itself and unpacks is never marked "from the internet" (no Mark-of-the-Web), which is what Smart App Control and
 SmartScreen look at. Whether photag.exe then starts depends on your Windows protection settings -- it is not guaranteed.

 Usage:   powershell -NoProfile -ExecutionPolicy Bypass -File photag-install.ps1 [options]
   -InstallDir <folder>   where to install (default: %LOCALAPPDATA%\Programs\photag)
   -ZipPath <file>        use this portable ZIP instead of downloading one (offline / testing)
   -Sha256 <hash>         the expected SHA-256 of -ZipPath (the download is checked against GitHub's own digest)
   -ListOnly              only say what would be downloaded
   -Uninstall             remove the program and the shortcuts (your data folder is kept)
   -NoLaunch -NoShortcuts -NoPause
 Your photos and catalog are never touched: they live in your library folder; the program's own "data" folder is kept on update.
#>
[CmdletBinding()]
param(
    [string]$InstallDir = (Join-Path $env:LOCALAPPDATA "Programs\photag"),
    [string]$ZipPath,
    [string]$Sha256,
    [string]$Repo = "giamat13/photag",
    [switch]$ListOnly,
    [switch]$Uninstall,
    [switch]$NoLaunch,
    [switch]$NoShortcuts,
    [switch]$NoPause
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "Continue"
try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 } catch { }

function Say($m) { Write-Host $m }
# A live progress line (and the Windows progress bar), so a long step visibly moves.
$script:lastTick = [Diagnostics.Stopwatch]::StartNew()
function Show-Progress([string]$what, [double]$done, [double]$total, [switch]$Force) {
    if (-not $Force -and $script:lastTick.ElapsedMilliseconds -lt 150) { return }
    $script:lastTick.Restart()
    $pct = if ($total -gt 0) { [math]::Min(100, [int](100 * $done / $total)) } else { 0 }
    $bar = ("#" * [int]($pct / 4)).PadRight(25, "-")
    $mb = "{0:N0} / {1:N0} MB" -f ($done / 1MB), ($total / 1MB)
    Write-Progress -Activity "photag installer" -Status "$what $pct%  ($mb)" -PercentComplete $pct
    Write-Host ("`r  {0} [{1}] {2,3}%  {3}   " -f $what, $bar, $pct, $mb) -NoNewline
}
function End-Progress { Write-Progress -Activity "photag installer" -Completed; Write-Host "" }
function Finish($code) {
    if (-not $NoPause) { Read-Host "Press Enter to close" | Out-Null }
    exit $code
}
function Fail($m) { Write-Host ""; Write-Host "photag installer: $m" -ForegroundColor Red; Finish 1 }

$exe = Join-Path $InstallDir "photag.exe"
$startMenu = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\photag.lnk"
$desktop = Join-Path ([Environment]::GetFolderPath("Desktop")) "photag.lnk"

try {
    if ($Uninstall) {
        if (Get-Process -Name photag -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq $exe }) { Fail "photag is running. Close it first." }
        if (Test-Path -LiteralPath $InstallDir) {
            Get-ChildItem -LiteralPath $InstallDir -Force | Where-Object { $_.Name -ne "data" } | Remove-Item -Recurse -Force
            Say "Removed the program from $InstallDir"
            if (Test-Path -LiteralPath (Join-Path $InstallDir "data")) { Say "Kept your data folder: $(Join-Path $InstallDir 'data')" }
            else { Remove-Item -LiteralPath $InstallDir -Force -ErrorAction SilentlyContinue }
        }
        foreach ($l in @($startMenu, $desktop)) { Remove-Item -LiteralPath $l -Force -ErrorAction SilentlyContinue }
        Say "Done."
        Finish 0
    }

    # ---- 1. the ZIP: given, or the latest release's portable ZIP
    $expected = $Sha256
    $cleanup = $null
    if (-not $ZipPath) {
        Say "Looking for the latest photag release on GitHub..."
        $rel = Invoke-RestMethod -Uri "https://api.github.com/repos/$Repo/releases/latest" -Headers @{ "User-Agent" = "photag-installer"; "Accept" = "application/vnd.github+json" }
        $asset = $rel.assets | Where-Object { $_.name -match '^photag-.*-portable\.zip$' } | Select-Object -First 1
        if (-not $asset) { Fail "The latest release ($($rel.tag_name)) has no portable ZIP." }
        if ($asset.digest -match '^sha256:([0-9a-fA-F]{64})$') { $expected = $Matches[1] }
        Say "Latest: $($rel.tag_name)  ($($asset.name), $([math]::Round($asset.size / 1MB)) MB)"
        if ($ListOnly) { Say "URL: $($asset.browser_download_url)"; Say "SHA256: $expected"; Finish 0 }
        $ZipPath = Join-Path $env:TEMP "photag-portable-$([guid]::NewGuid().ToString('N')).zip"
        $cleanup = $ZipPath
        Say "Step 1 of 4: downloading (about $([math]::Round($asset.size / 1MB)) MB; keep this window open)..."
        Add-Type -AssemblyName System.Net.Http
        $client = New-Object System.Net.Http.HttpClient
        $client.Timeout = [TimeSpan]::FromMinutes(120)
        $client.DefaultRequestHeaders.UserAgent.ParseAdd("photag-installer")
        $resp = $client.GetAsync($asset.browser_download_url, [System.Net.Http.HttpCompletionOption]::ResponseHeadersRead).GetAwaiter().GetResult()
        $resp.EnsureSuccessStatusCode() | Out-Null
        $total = [double]$asset.size
        if ($resp.Content.Headers.ContentLength) { $total = [double]$resp.Content.Headers.ContentLength }
        $inStream = $resp.Content.ReadAsStreamAsync().GetAwaiter().GetResult()
        $outStream = [IO.File]::Create($ZipPath)
        try {
            $buf = New-Object byte[] (1MB)
            $got = 0.0
            while (($n = $inStream.Read($buf, 0, $buf.Length)) -gt 0) {
                $outStream.Write($buf, 0, $n)
                $got += $n
                Show-Progress "Downloading" $got $total
            }
            Show-Progress "Downloading" $total $total -Force
        } finally { $outStream.Dispose(); $inStream.Dispose(); $client.Dispose(); End-Progress }
    } elseif ($ListOnly) {
        Say "Would install $ZipPath"
        Finish 0
    }
    if (-not (Test-Path -LiteralPath $ZipPath)) { Fail "The ZIP file was not found: $ZipPath" }

    # ---- 2. check it
    if ($expected) {
        Say "Step 2 of 4: checking the download (SHA-256)..."
        $actual = (Get-FileHash -LiteralPath $ZipPath -Algorithm SHA256).Hash
        if ($actual -ne $expected.ToUpper() -and $actual.ToLower() -ne $expected.ToLower()) { Fail "The ZIP does not match its SHA-256 (expected $expected, got $actual). Nothing was installed." }
        Say "SHA-256 checked."
    } else {
        Say "Note: no SHA-256 to check the ZIP against."
    }

    # ---- 3. unpack beside the install folder, then swap
    if (Get-Process -Name photag -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq $exe }) { Fail "photag is running. Close it, then run this again." }
    $stage = Join-Path $env:TEMP "photag-stage-$([guid]::NewGuid().ToString('N'))"
    Say "Step 3 of 4: unpacking..."
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $stageFull = [IO.Path]::GetFullPath($stage).TrimEnd("\") + "\"
    New-Item -ItemType Directory -Force -Path $stage | Out-Null
    $zip = [IO.Compression.ZipFile]::OpenRead($ZipPath)
    try {
        $all = 0.0; foreach ($e in $zip.Entries) { $all += $e.Length }
        $did = 0.0
        foreach ($e in $zip.Entries) {
            $target = [IO.Path]::GetFullPath((Join-Path $stage $e.FullName))
            if (-not $target.StartsWith($stageFull, [StringComparison]::OrdinalIgnoreCase)) { throw "The ZIP contains an unsafe path: $($e.FullName)" }
            if ($e.FullName.EndsWith("/")) { New-Item -ItemType Directory -Force -Path $target | Out-Null; continue }
            New-Item -ItemType Directory -Force -Path (Split-Path -Parent $target) | Out-Null
            [IO.Compression.ZipFileExtensions]::ExtractToFile($e, $target, $true)
            $did += $e.Length
            Show-Progress "Unpacking" $did $all
        }
        Show-Progress "Unpacking" $all $all -Force
    } finally { $zip.Dispose(); End-Progress }
    $src = Join-Path $stage "photag"
    if (-not (Test-Path -LiteralPath (Join-Path $src "photag.exe"))) { Remove-Item -LiteralPath $stage -Recurse -Force -ErrorAction SilentlyContinue; Fail "The ZIP does not contain photag\photag.exe." }

    Say "Step 4 of 4: installing into $InstallDir ..."
    New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
    Get-ChildItem -LiteralPath $InstallDir -Force | Where-Object { $_.Name -ne "data" } | Remove-Item -Recurse -Force
    Get-ChildItem -LiteralPath $src -Force | Where-Object { $_.Name -ne "data" } | Move-Item -Destination $InstallDir -Force
    New-Item -ItemType Directory -Force -Path (Join-Path $InstallDir "data") | Out-Null
    Remove-Item -LiteralPath $stage -Recurse -Force -ErrorAction SilentlyContinue
    if ($cleanup) { Remove-Item -LiteralPath $cleanup -Force -ErrorAction SilentlyContinue }

    # a file that came out of this script was never marked "from the internet"; make sure of it
    Get-ChildItem -LiteralPath $InstallDir -Filter *.exe -File | ForEach-Object { Remove-Item -LiteralPath $_.FullName -Stream Zone.Identifier -ErrorAction SilentlyContinue }
    Say "Installed in $InstallDir"

    # ---- 4. shortcuts
    if (-not $NoShortcuts) {
        $sh = New-Object -ComObject WScript.Shell
        foreach ($l in @($startMenu, $desktop)) {
            $s = $sh.CreateShortcut($l)
            $s.TargetPath = $exe
            $s.WorkingDirectory = $InstallDir
            $s.Save()
        }
        Say "Shortcuts made (Start menu and desktop)."
    }

    # ---- 5. start it
    if (-not $NoLaunch) {
        try {
            Start-Process -FilePath $exe -WorkingDirectory $InstallDir
            Say "photag is starting."
        } catch {
            Write-Host ""
            Write-Host "Windows would not start photag.exe: $($_.Exception.Message)" -ForegroundColor Yellow
            Write-Host "That is Smart App Control / an Application Control policy blocking an unsigned program. See the README (Windows says the installer is blocked)."
            Finish 2
        }
    }
    Finish 0
} catch {
    Fail $_.Exception.Message
}
