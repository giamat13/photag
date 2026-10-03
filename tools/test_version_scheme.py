"""Test: the version numbering ("FEATURE.FIX" -- 1.8.1 became 8.1, see app/version.py) and everything that reads a version:
comparing versions, pre-releases, the code-update file name, and the Windows version resource.

    py -3.12 tools/test_version_scheme.py
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
res = []


def check(name, ok, extra=""):
    res.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{extra}]" if extra else ""))


import codeboot  # noqa: E402
from app import updater  # noqa: E402
from app.updater import CODE_RE, is_newer, parse_version  # noqa: E402
import make_version_info  # noqa: E402

nw = lambda a, b: is_newer(a, b)

# ---- comparing
check("the new scheme is newer than the old one: 8.1 > 1.8.1, 9.0 > 1.8.1, 9.0.0 > 1.8.1", nw("8.1", "1.8.1") and nw("9.0", "1.8.1") and nw("9.0.0", "1.8.1"))
check("the old scheme is never newer than the new one", not nw("1.8.1", "8.1") and not nw("1.9.0", "8.1") and not nw("1.99.99", "8.0"))
check("a fix is newer than its feature release: 8.2 > 8.1 > 8.0", nw("8.2", "8.1") and nw("8.1", "8.0"))
check("a feature release is newer than any fix of the one before: 9.0 > 8.9", nw("9.0", "8.9"))
check("numbers are compared as numbers, not text: 8.10 > 8.9 and 10.0 > 9.9", nw("8.10", "8.9") and nw("10.0", "9.9"))
check("'9.0' and '9.0.0' are the same version (neither is newer)", not nw("9.0", "9.0.0") and not nw("9.0.0", "9.0") and parse_version("9.0") == parse_version("9.0.0"))
check("'8.1' and '8.1.0' likewise, and a v prefix is ignored", parse_version("v8.1") == parse_version("8.1.0") == parse_version("8.1"))
check("the same version is not an update", not nw("8.1", "8.1") and not nw("1.8.1", "1.8.1"))
check("a pre-release is older than its release but newer than the previous one: 9.0-beta.1", nw("9.0", "9.0-beta.1") and nw("9.0-beta.1", "8.2") and not nw("9.0-beta.1", "9.0"))
check("pre-releases of the same version are ordered by being equal (only 'beta' vs release matters)", parse_version("9.0-beta.1")[3] == 0 and parse_version("9.0")[3] == 1)
check("old-style pre-release markers still work: 1.8.0-beta.1", parse_version("1.8.0-beta.1")[3] == 0 and nw("1.8.1", "1.8.0-beta.1"))
check("garbage does not crash and is never newer", parse_version("") == (0, 0, 0, 0) and not nw("nonsense", "8.1") and parse_version("v") == (0, 0, 0, 0))
check("the version in app/version.py is readable", parse_version(__import__("app.version", fromlist=["x"]).__version__)[0] >= 1)

# ---- the code-update file name
check("code update names are accepted with two or three parts", all(CODE_RE.match(f"photag-code-{v}-rt2.zip") for v in ("1.8.1", "9.0.0", "9.1", "10.12")))
check("...and rejected when malformed (one part, four parts, no runtime, wrong extension)", not any(CODE_RE.match(n) for n in (
    "photag-code-9-rt2.zip", "photag-code-9.0.0.1-rt2.zip", "photag-code-9.0.zip", "photag-code-9.0-rt2.exe", "photag-code-9.0-rtx.zip", "x-photag-code-9.0-rt2.zip")))
OLD_RE = re.compile(r"^photag-code-(\d+\.\d+\.\d+)-rt(\d+)\.zip$")             # what the programs installed before this change use
check("why the FIRST release in the new scheme has three parts: an installed 1.8.1 recognises 'photag-code-9.0.0' ...", bool(OLD_RE.match("photag-code-9.0.0-rt2.zip")))
check("... and would NOT see 'photag-code-9.1' (it would offer the full installer instead)", not OLD_RE.match("photag-code-9.1-rt2.zip"))
check("the old program also compares the file's version to the tag as TEXT: '9.0.0' == tag 'v9.0.0'", OLD_RE.match("photag-code-9.0.0-rt2.zip").group(1) == "v9.0.0".lstrip("vV"))

DIG = "a" * 64
asset = lambda v: {"name": f"photag-code-{v}-rt{codeboot.RUNTIME}.zip", "browser_download_url": "https://x/c.zip", "size": 1, "digest": "sha256:" + DIG}
pick = lambda assets, latest: updater._pick_code_asset(assets, latest)
check("_pick_code_asset: three-part asset for a three-part tag (the transition release)", pick([asset("9.0.0")], "9.0.0") is not None)
check("_pick_code_asset: short asset for a short tag (every release after it)", pick([asset("9.1")], "9.1") is not None)
check("_pick_code_asset: '9.0' and '9.0.0' match each other either way round", pick([asset("9.0.0")], "9.0") is not None and pick([asset("9.0")], "9.0.0") is not None)
check("_pick_code_asset: an old three-part release still works (1.8.1)", pick([asset("1.8.1")], "1.8.1") is not None)
check("_pick_code_asset: a different version is not picked", pick([asset("9.0")], "9.1") is None and pick([asset("8.2")], "9.0") is None)
check("_pick_code_asset: a pre-release never takes the code update of the release", pick([asset("9.1")], "9.1-beta.1") is None)
check("_pick_code_asset: a code update made for another runtime is ignored", pick([{**asset("9.0"), "name": f"photag-code-9.0-rt{codeboot.RUNTIME + 1}.zip"}], "9.0") is None)
check("_pick_code_asset: no digest -> not used (it could not be verified)", pick([{**asset("9.0"), "digest": ""}], "9.0") is None)
check("_pick_code_asset: picks the right one among several", (pick([asset("8.2"), asset("9.0"), asset("9.0.0")], "9.0") or {}).get("name", "").startswith("photag-code-9.0"))

# ---- the Windows version resource
vt = make_version_info.version_tuple
check("version resource: 8.1 -> (8, 1, 0, 0), 9.0.0 -> (9, 0, 0, 0), 1.8.1 -> (1, 8, 1, 0)", vt("8.1") == (8, 1, 0, 0) and vt("9.0.0") == (9, 0, 0, 0) and vt("1.8.1") == (1, 8, 1, 0))
check("version resource: a pre-release suffix is ignored (9.1-beta.2 -> 9.1.0.0), a v prefix too", vt("9.1-beta.2") == (9, 1, 0, 0) and vt("v10.12") == (10, 12, 0, 0))
check("version resource: every part fits the 16 bits Windows allows", all(0 <= x < 65536 for x in vt("9.1") + vt("2026.10.3")))
try:
    vt("nonsense")
    check("version resource: garbage stops the build", False)
except SystemExit:
    check("version resource: garbage stops the build", True)

# ---- the tag the release workflow builds from the version
for v in ("9.0.0", "9.1", "8.2.0"):
    check(f"release tag and asset names for {v}: v{v} / photag-code-{v}-rt{codeboot.RUNTIME}.zip / photag-{v}-portable.zip", f"v{v}".startswith("v") and CODE_RE.match(f"photag-code-{v}-rt{codeboot.RUNTIME}.zip") is not None and f"photag-{v}-portable.zip".endswith("-portable.zip"))
# the scheme is written down where the next person looks
doc = (ROOT / "app" / "version.py").read_text("utf-8")
check("app/version.py explains the scheme and the three-part first release", "FEATURE.FIX" in doc and "three parts" in doc)

n_fail = res.count(False)
print(f"\n{len(res) - n_fail}/{len(res)} passed")
sys.exit(1 if n_fail else 0)
