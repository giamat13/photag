"""Test: the WiX source of the MSI (tools/make_msi.py) -- well-formed, every file once, per user, upgradeable.

    py -3.12 tools/test_msi.py
"""
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT))
import make_msi  # noqa: E402

res = []


def check(name, ok, extra=""):
    res.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{extra}]" if extra else ""))


tmp = Path(tempfile.mkdtemp(prefix="photag_msi_"))
files = []
for rel in ("photag.exe", "_internal/python312.dll", "_internal/app/ui/index.html", "_internal/app/ui/locales/he.json", "_internal/a b & c/x'y.txt"):
    p = tmp / "photag" / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("x")
    files.append((p, rel))
src = make_msi.wxs(files, "14.0.0")
ns = {"w": "http://wixtoolset.org/schemas/v4/wxs"}
root = ET.fromstring(src.encode())
pkg = root.find("w:Package", ns)
check("the WiX source is well-formed XML (also with &, ' and spaces in names)", pkg is not None)
check("per user, no administrator", pkg.get("Scope") == "perUser")
check("the version and the fixed upgrade code", pkg.get("Version") == "14.0.0" and pkg.get("UpgradeCode") == make_msi.UPGRADE_CODE)
check("a newer MSI replaces an older one", pkg.find("w:MajorUpgrade", ns) is not None)
srcs = [f.get("Source") for f in root.iter("{%s}File" % ns["w"])]
check("every file once", sorted(srcs) == sorted(str(p) for p, _ in files), len(srcs))
dirs = {d.get("Name") for d in root.iter("{%s}Directory" % ns["w"])}
check("its own folder under Programs, with the sub-folders", {"Programs", "photag-msi", "_internal", "app", "ui", "locales", "a b & c"} <= dirs, dirs)
check("a Start menu shortcut to photag.exe", any(s.get("Target") == "[INSTALLFOLDER]photag.exe" for s in root.iter("{%s}Shortcut" % ns["w"])))
check("versions: pre-release suffix dropped, three parts", make_msi.msi_version("14.0.0-beta.2") == "14.0.0" and make_msi.msi_version("9.1") == "9.1.0")
ids = [c.get("Id") for c in root.iter("{%s}Component" % ns["w"])]
check("component ids are unique", len(ids) == len(set(ids)))
check("one component per folder, not per file (fast start of the installer)", len(ids) - 1 == len({rel.rsplit("/", 1)[0] if "/" in rel else "" for _, rel in files}), len(ids))
check("each component has exactly one key file", all(sum(1 for f in c.findall("w:File", ns) if f.get("KeyPath") == "yes") == 1 for c in root.iter("{%s}Component" % ns["w"]) if c.findall("w:File", ns)))
guids = [c.get("Guid") for c in root.iter("{%s}Component" % ns["w"])]
check("every component has its own fixed GUID (a component with several files cannot use '*')", all(guids) and "*" not in guids and len(set(guids)) == len(guids)
      and guids == [c.get("Guid") for c in ET.fromstring(make_msi.wxs(files, "14.0.1").encode()).iter("{%s}Component" % ns["w"])], guids[:2])
check("fast-install switches are set", any(p.get("Id") == "MSIFASTINSTALL" and p.get("Value") == "7" for p in pkg.findall("w:Property", ns)))
n = res.count(False)
print(f"\n{len(res) - n}/{len(res)} passed")
sys.exit(1 if n else 0)
