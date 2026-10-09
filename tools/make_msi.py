"""Build the Windows Installer package: dist/photag-<version>.msi (for people and companies who install software from MSI files:
Group Policy, Intune, `msiexec /i photag-X.Y.Z.msi /qn`).

It holds exactly what photagSetup.exe installs (the app folder from PyInstaller, photag-backup.exe, LICENSE, THIRD_PARTY_NOTICES.md),
per user, without administrator rights, into %LOCALAPPDATA%\\Programs\\photag-msi (its own folder, so it never mixes its files with an
installation made by photagSetup.exe), with a Start menu shortcut. A newer MSI replaces an older one (MajorUpgrade). The in-app
updater keeps working in it: code updates go to <install folder>\\code, which is writable.

    py -3.12 tools/make_msi.py                  (needs dist/photag from the normal build and the WiX Toolset: dotnet tool install --global wix)
    py -3.12 tools/make_msi.py --wxs-only <out>  (just write the WiX source, for tests)
"""
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from xml.sax.saxutils import quoteattr

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app.version import __version__  # noqa: E402

UPGRADE_CODE = "{2B6C1F4E-8A3D-4C57-9E21-7D5B0A6C3F18}"       # never change: it is how a newer MSI finds and replaces an older one
STARTMENU_GUID = "{9C3E5A71-4B2D-4F86-A1E0-3D7C6B5F2E94}"
WIX_VERSION = "5.0.2"


def msi_version(v: str = __version__) -> str:
    """'14.0.0' -> '14.0.0'; a pre-release suffix is dropped (Windows Installer versions are numbers only, each part < 256 for the first two)."""
    m = re.match(r"\s*v?(\d+)(?:\.(\d+))?(?:\.(\d+))?", v)
    if not m:
        raise SystemExit(f"cannot read the version {v!r}")
    return ".".join(x or "0" for x in m.groups())


def sources() -> list[tuple[Path, str]]:
    """(file on disk, path inside the install folder) of everything the MSI installs."""
    app = ROOT / "dist" / "photag"
    if not (app / "photag.exe").is_file():
        raise SystemExit("dist/photag/photag.exe is missing: build it first (pyinstaller photag.spec)")
    out = [(f, f.relative_to(app).as_posix()) for f in sorted(app.rglob("*")) if f.is_file()]
    for extra in (ROOT / "dist" / "photag-backup.exe", ROOT / "LICENSE", ROOT / "THIRD_PARTY_NOTICES.md"):
        if extra.is_file():
            out.append((extra, extra.name))
    return out


def wxs(files: list[tuple[Path, str]], version: str = None) -> str:
    """The WiX (v4/v5 schema) source: a directory tree with one component per folder, the Start menu shortcut and the upgrade rules."""
    version = version or msi_version()
    tree: dict = {}
    for _, rel in files:
        node = tree
        for part in rel.split("/")[:-1]:
            node = node.setdefault(part, {})
    dir_ids: dict[str, str] = {"": "INSTALLFOLDER"}
    lines: list[str] = []

    def emit_dirs(node: dict, prefix: str, indent: str):
        for name in sorted(node):
            rel = f"{prefix}/{name}" if prefix else name
            did = f"d{len(dir_ids)}"
            dir_ids[rel] = did
            lines.append(f"{indent}<Directory Id={quoteattr(did)} Name={quoteattr(name)}>")
            emit_dirs(node[name], rel, indent + "  ")
            lines.append(f"{indent}</Directory>")

    emit_dirs(tree, "", "          ")
    # one component per FOLDER (not per file): Windows Installer costs every component before it shows the progress bar -- with thousands
    # of files that was minutes of "Please wait while Windows configures photag" without a bar. Fine here: the whole product is replaced together.
    by_dir: dict[str, list[tuple[int, Path]]] = {}
    for i, (src, rel) in enumerate(files):
        by_dir.setdefault(rel.rsplit("/", 1)[0] if "/" in rel else "", []).append((i, src))
    comps = []
    for n, d in enumerate(sorted(by_dir)):
        fl = "".join(f'<File Id="f{i}" Source={quoteattr(str(src))}{" KeyPath=" + chr(34) + "yes" + chr(34) if k == 0 else ""} />' for k, (i, src) in enumerate(by_dir[d]))
        comps.append(f'      <Component Id="c{n}" Directory={quoteattr(dir_ids[d])}>{fl}</Component>')
    icon = ROOT / "app" / "ui" / "icon.ico"
    return f"""<?xml version="1.0" encoding="utf-8"?>
<Wix xmlns="http://wixtoolset.org/schemas/v4/wxs">
  <Package Name="photag" Manufacturer="photag" Version="{version}" UpgradeCode="{UPGRADE_CODE}" Scope="perUser" Compressed="yes" Language="1033">
    <SummaryInformation Description="photag {version}" />
    <MajorUpgrade DowngradeErrorMessage="A newer version of photag is already installed." AllowSameVersionUpgrades="yes" />
    <MediaTemplate EmbedCab="yes" CompressionLevel="medium" />
    <!-- 1: no restore point, 2: only the file costing that is needed, 4: fewer progress messages (thousands of files: the progress bar itself was slow) -->
    <Property Id="MSIFASTINSTALL" Value="7" />
    <Icon Id="photag.ico" SourceFile={quoteattr(str(icon))} />
    <Property Id="ARPPRODUCTICON" Value="photag.ico" />
    <Property Id="ARPURLINFOABOUT" Value="https://github.com/giamat13/photag" />
    <StandardDirectory Id="LocalAppDataFolder">
      <Directory Id="ProgramsDir" Name="Programs">
        <Directory Id="INSTALLFOLDER" Name="photag-msi">
{chr(10).join(lines)}
        </Directory>
      </Directory>
    </StandardDirectory>
    <StandardDirectory Id="ProgramMenuFolder">
      <Component Id="StartMenu" Guid="{STARTMENU_GUID}">
        <Shortcut Id="StartShortcut" Name="photag" Target="[INSTALLFOLDER]photag.exe" WorkingDirectory="INSTALLFOLDER" Icon="photag.ico" />
        <RegistryValue Root="HKCU" Key="Software\\photag\\msi" Name="startmenu" Type="integer" Value="1" KeyPath="yes" />
      </Component>
    </StandardDirectory>
    <ComponentGroup Id="AppFiles">
{chr(10).join(comps)}
    </ComponentGroup>
    <Feature Id="Main" Title="photag" Level="1">
      <ComponentGroupRef Id="AppFiles" />
      <ComponentRef Id="StartMenu" />
    </Feature>
  </Package>
</Wix>
"""


def find_wix() -> str | None:
    w = shutil.which("wix")
    if w:
        return w
    home = Path(os.environ.get("USERPROFILE") or Path.home())
    for c in (home / ".dotnet" / "tools" / "wix.exe", home / ".dotnet" / "tools" / "wix"):
        if c.is_file():
            return str(c)
    return None


def main(argv: list[str]) -> int:
    if argv[:1] == ["--wxs-only"]:
        out = Path(argv[1])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(wxs(sources()), "utf-8")
        print(out)
        return 0
    wix = find_wix()
    if not wix:
        subprocess.run(["dotnet", "tool", "install", "--global", "wix", "--version", WIX_VERSION], check=True)
        wix = find_wix()
    if not wix:
        raise SystemExit("the WiX Toolset (wix) is not installed")
    build = ROOT / "build" / "msi"
    build.mkdir(parents=True, exist_ok=True)
    src = build / "photag.wxs"
    src.write_text(wxs(sources()), "utf-8")
    out = ROOT / "dist" / f"photag-{__version__}.msi"
    out.unlink(missing_ok=True)
    # `wix build` does not run the ICE validation (that is `wix msi validate`, which would want a registry key path for every file of a
    # per-user install -- only relevant for repairs)
    subprocess.run([wix, "build", str(src), "-arch", "x64", "-o", str(out)], check=True)
    print(f"{out}  {out.stat().st_size / 1048576:.1f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
