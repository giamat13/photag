"""Build the Microsoft Store package: dist/photag-<version>.msix (the app folder + photag-backup.exe + an AppxManifest.xml + logos).

Why: Windows 11 Smart App Control blocks every program that is not signed or known to Microsoft. A Store app is signed by
Microsoft itself when it is published, so it runs. This package is uploaded to Partner Center by hand (the Store signs it);
it is NOT meant to be double-clicked. The identity values come from the app's page in Partner Center
(Product management > Product identity):

    PHOTAG_STORE_IDENTITY_NAME     e.g. 12345Name.photag          (Package/Identity/Name)
    PHOTAG_STORE_PUBLISHER         e.g. CN=XXXXXXXX-XXXX-...       (Package/Identity/Publisher)
    PHOTAG_STORE_PUBLISHER_NAME    the publisher display name      (Package/Properties/PublisherDisplayName)

    py -3.12 tools/make_msix.py            (needs dist/photag from the normal build, and makeappx.exe from the Windows SDK)
    py -3.12 tools/make_msix.py --manifest-only <folder>      (just write the manifest + logos, for tests)

A Store build keeps its own folder read-only, so the app turns off its in-app updater and the scheduled background backup
there (the Store updates the app; see app/config.py: IN_STORE_PACKAGE).
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

NS = ('xmlns="http://schemas.microsoft.com/appx/manifest/foundation/windows10" '
      'xmlns:uap="http://schemas.microsoft.com/appx/manifest/uap/windows10" '
      'xmlns:rescap="http://schemas.microsoft.com/appx/manifest/foundation/windows10/restrictedcapabilities" '
      'IgnorableNamespaces="uap rescap"')


def msix_version(v: str = __version__) -> str:
    """'9.1.0' -> '9.1.0.0'. The Store reserves the last part (it must be 0); a pre-release suffix is dropped."""
    m = re.match(r"\s*v?(\d+)(?:\.(\d+))?(?:\.(\d+))?", v)
    if not m:
        raise SystemExit(f"cannot read the version {v!r}")
    a, b, c = (int(x or 0) for x in m.groups())
    return f"{a}.{b}.{c}.0"


def _text(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def manifest(name: str, publisher: str, publisher_name: str, version: str | None = None) -> str:
    q = quoteattr
    return f"""<?xml version="1.0" encoding="utf-8"?>
<Package {NS}>
  <Identity Name={q(name)} Publisher={q(publisher)} Version={q(version or msix_version())} ProcessorArchitecture="x64" />
  <Properties>
    <DisplayName>photag</DisplayName>
    <PublisherDisplayName>{_text(publisher_name)}</PublisherDisplayName>
    <Logo>Assets\\StoreLogo.png</Logo>
  </Properties>
  <Dependencies>
    <TargetDeviceFamily Name="Windows.Desktop" MinVersion="10.0.17763.0" MaxVersionTested="10.0.26100.0" />
  </Dependencies>
  <Resources>
    <Resource Language="en-us" />
  </Resources>
  <Applications>
    <Application Id="photag" Executable="photag.exe" EntryPoint="Windows.FullTrustApplication">
      <uap:VisualElements DisplayName="photag" Description="Photo manager: import, organise, edit and back up your photos"
        BackgroundColor="transparent" Square150x150Logo="Assets\\Square150x150Logo.png" Square44x44Logo="Assets\\Square44x44Logo.png">
        <uap:DefaultTile Wide310x150Logo="Assets\\Wide310x150Logo.png" />
      </uap:VisualElements>
    </Application>
  </Applications>
  <Capabilities>
    <rescap:Capability Name="runFullTrust" />
  </Capabilities>
</Package>
"""


LOGOS = {"StoreLogo.png": (50, 50), "Square44x44Logo.png": (44, 44), "Square150x150Logo.png": (150, 150), "Wide310x150Logo.png": (310, 150)}


def write_logos(folder: Path) -> None:
    from PIL import Image
    src = Image.open(ROOT / "app" / "ui" / "icon.png").convert("RGBA")
    out = folder / "Assets"
    out.mkdir(parents=True, exist_ok=True)
    for fname, (w, h) in LOGOS.items():
        side = min(w, h)
        icon = src.resize((side, side), Image.LANCZOS)
        canvas = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        canvas.paste(icon, ((w - side) // 2, (h - side) // 2), icon)
        canvas.save(out / fname)


def find_makeappx() -> str:
    env = os.environ.get("MAKEAPPX")
    if env:
        return env
    kits = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Windows Kits" / "10" / "bin"
    found = sorted(kits.glob("*/x64/makeappx.exe"), reverse=True) if kits.is_dir() else []
    if not found:
        raise SystemExit("makeappx.exe not found (install the Windows 10/11 SDK, or set MAKEAPPX)")
    return str(found[0])


def need(var: str) -> str:
    v = os.environ.get(var, "").strip()
    if not v:
        raise SystemExit(f"{var} is not set (copy it from the app's page in Partner Center)")
    return v


def main() -> Path:
    name, publisher, pub_name = need("PHOTAG_STORE_IDENTITY_NAME"), need("PHOTAG_STORE_PUBLISHER"), need("PHOTAG_STORE_PUBLISHER_NAME")
    dist = ROOT / "dist" / "photag"
    if not (dist / "photag.exe").is_file():
        raise SystemExit(f"{dist}\\photag.exe is missing: run the normal build first")
    stage = ROOT / "dist" / "photag-msix-stage"
    shutil.rmtree(stage, ignore_errors=True)
    shutil.copytree(dist, stage)
    backup_exe = ROOT / "dist" / "photag-backup.exe"
    if backup_exe.is_file():
        shutil.copy2(backup_exe, stage / "photag-backup.exe")
    shutil.copy2(ROOT / "LICENSE", stage / "LICENSE")
    shutil.copy2(ROOT / "THIRD_PARTY_NOTICES.md", stage / "THIRD_PARTY_NOTICES.md")
    (stage / "AppxManifest.xml").write_text(manifest(name, publisher, pub_name), encoding="utf-8")
    write_logos(stage)
    out = ROOT / "dist" / f"photag-{__version__}.msix"
    out.unlink(missing_ok=True)
    subprocess.run([find_makeappx(), "pack", "/o", "/d", str(stage), "/p", str(out)], check=True)
    shutil.rmtree(stage, ignore_errors=True)
    print(f"{out} ({out.stat().st_size / 1048576:.0f} MB) -- upload it to Partner Center (the Store signs it)")
    return out


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "--manifest-only":
        d = Path(sys.argv[2])
        d.mkdir(parents=True, exist_ok=True)
        (d / "AppxManifest.xml").write_text(manifest("Test.photag", "CN=TEST", "Test"), encoding="utf-8")
        write_logos(d)
    else:
        main()
