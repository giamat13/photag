"""Test: the Microsoft Store package -- manifest, logos, version, the app's behaviour inside a Store package, and (on Windows,
where the SDK is) a real `makeappx pack`, which validates the manifest against the Appx schema."""
import os
import subprocess
import sys
import tempfile
import xml.dom.minidom as minidom
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
tmp = Path(tempfile.mkdtemp(prefix="photag_msix_test_"))
for k, v in (("APPDATA", "a"), ("LOCALAPPDATA", "l"), ("USERPROFILE", "h"), ("HOME", "h")):
    (tmp / v).mkdir(exist_ok=True)
    os.environ[k] = str(tmp / v)
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
res = []


def check(name, ok, extra=""):
    res.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{extra}]" if extra else ""))


import make_msix as mm  # noqa: E402

check("versions: 9.1.0 -> 9.1.0.0, 9.1 -> 9.1.0.0, a pre-release suffix is dropped, the last part is always 0",
      (mm.msix_version("9.1.0"), mm.msix_version("9.1"), mm.msix_version("9.2.0-beta.3"), mm.msix_version("v10.0.0")) == ("9.1.0.0", "9.1.0.0", "9.2.0.0", "10.0.0.0"))
xml = mm.manifest("12345Name.photag", 'CN=ABC-123, O="Q & R"', "A & B <c>")
dom = minidom.parseString(xml)
check("the manifest is well-formed XML, even with &, < and quotes in the publisher", dom is not None)
ident = dom.getElementsByTagName("Identity")[0]
check("identity: name, publisher (exactly as given) and a 4-part version", ident.getAttribute("Name") == "12345Name.photag" and ident.getAttribute("Publisher") == 'CN=ABC-123, O="Q & R"'
      and ident.getAttribute("Version").count(".") == 3 and ident.getAttribute("Version").endswith(".0"), ident.getAttribute("Version"))
check("the publisher display name is escaped", "A &amp; B &lt;c&gt;" in xml)
app = dom.getElementsByTagName("Application")[0]
check("it starts photag.exe as a full-trust desktop app", app.getAttribute("Executable") == "photag.exe" and app.getAttribute("EntryPoint") == "Windows.FullTrustApplication"
      and 'rescap:Capability Name="runFullTrust"' in xml)
check("it targets desktop Windows 10 1809+ on x64", 'Name="Windows.Desktop"' in xml and 'MinVersion="10.0.17763.0"' in xml and ident.getAttribute("ProcessorArchitecture") == "x64")
d = tmp / "pkg"
mm.write_logos(d)
from PIL import Image  # noqa: E402
sizes = {n: Image.open(d / "Assets" / n).size for n in mm.LOGOS}
check("all four logos exist with the sizes the Store wants", sizes == mm.LOGOS, sizes)
check("every logo the manifest names exists", all((d / x.replace("\\", "/")).is_file() for x in [dom.getElementsByTagName("Logo")[0].firstChild.data,
      dom.getElementsByTagName("uap:VisualElements")[0].getAttribute("Square150x150Logo"), dom.getElementsByTagName("uap:VisualElements")[0].getAttribute("Square44x44Logo"),
      dom.getElementsByTagName("uap:DefaultTile")[0].getAttribute("Wide310x150Logo")]))
r = subprocess.run([sys.executable, str(ROOT / "tools" / "make_msix.py")], capture_output=True, text=True, env={**os.environ, "PHOTAG_STORE_IDENTITY_NAME": ""})
check("without the Store values the build stops with a clear message", r.returncode != 0 and "PHOTAG_STORE_IDENTITY_NAME" in (r.stdout + r.stderr), (r.stdout + r.stderr)[-120:])

# ---- behaviour inside a Store package (the flag is forced by the environment here)
probe = """
import sys; sys.path.insert(0, %r)
from app import config, updater, backup_task
print(config.IN_STORE_PACKAGE, updater.check(force=True).get("store"), updater.check(force=True)["available"], backup_task.supported())
""" % str(ROOT)
for flag, want in (("1", "True True False False"), ("0", "False None False False")):
    r = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, env={**os.environ, "PHOTAG_STORE_PACKAGE": flag}, timeout=60)
    out = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else r.stderr[-200:]
    if flag == "1":
        check("in a Store package: flagged, the updater does no check at all and offers nothing, no scheduled backup task", out == want, out)
    else:
        check("outside one: not flagged (the normal updater runs; here the network is not needed for this check)", out.startswith("False"), out)

# ---- a real makeappx run validates the manifest against the schema (Windows with the SDK only)
mk = None
if sys.platform.startswith("win"):
    try:
        mk = mm.find_makeappx()
    except SystemExit as e:
        print("SKIP makeappx:", e)
if mk:
    stage = tmp / "stage"
    (stage).mkdir()
    (stage / "AppxManifest.xml").write_text(mm.manifest("Test.photag", "CN=Test", "Test"), encoding="utf-8")
    mm.write_logos(stage)
    (stage / "photag.exe").write_bytes(b"MZ")          # content does not matter for packing
    r = subprocess.run([mk, "pack", "/o", "/d", str(stage), "/p", str(tmp / "t.msix")], capture_output=True, text=True)
    check("makeappx accepts the manifest and packs it", r.returncode == 0 and (tmp / "t.msix").is_file(), (r.stdout + r.stderr)[-300:])
else:
    print("SKIP makeappx (not Windows / no SDK here)")

n = res.count(False)
print(f"\n{len(res) - n}/{len(res)} passed")
sys.exit(1 if n else 0)
