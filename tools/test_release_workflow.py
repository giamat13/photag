"""Test: the release workflow's code-signing steps (static checks -- SignPath itself cannot run here) and tools/signing.py.

    py -3.12 tools/test_release_workflow.py
"""
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
res = []


def check(name, ok, extra=""):
    res.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{extra}]" if extra else ""))


wf = yaml.safe_load((ROOT / ".github" / "workflows" / "release.yml").read_text("utf-8"))
job = wf["jobs"]["build"]
steps = job["steps"]
name = lambda s: s.get("name") or s.get("run") or s.get("uses") or ""
idx = lambda needle: next(i for i, s in enumerate(steps) if needle in name(s))
signing = [s for s in steps if "Signing:" in name(s)]
store = [s for s in steps if "Store:" in name(s)]

check("signing is switched on only by repository variables + a secret (SIGNING env at job level)",
      "SIGNPATH_ORGANIZATION_ID" in job["env"]["SIGNING"] and "SIGNPATH_PROJECT_SLUG" in job["env"]["SIGNING"] and "SIGNPATH_API_TOKEN" in job["env"]["SIGNING"])
check("every signing step is conditional on it (an unconfigured build behaves exactly as before)", len(signing) == 7 and all(s.get("if") == "env.SIGNING == 'true'" for s in signing), len(signing))
check("no non-signing step depends on signing", all("if" not in s or "SIGNING" not in str(s["if"]) for s in steps if s not in signing))
check("the programs are signed AFTER PyInstaller and BEFORE the installer is built", idx("PyInstaller --noconfirm photag_backup.spec") < idx("Signing: collect the programs") < idx("Signing: put the signed programs back") < idx("Build the installer"))
check("the installer is signed right after it is built, before anything is packaged or published", idx("Build the installer") < idx("Signing: upload the installer") < idx("Signing: put the signed installer back") < idx("Put a copy of the installer in a ZIP") < idx("Build the code update") < idx("Build the portable ZIP") < idx("Publish the release"))
check("the ZIP copy of the installer contains the SIGNED installer (it is made after signing)", idx("Signing: put the signed installer back") < idx("Put a copy of the installer in a ZIP"))
check("the portable ZIP is built after the programs are signed (it contains photag.exe)", idx("Signing: put the signed programs back") < idx("Build the portable ZIP"))
sp = [s for s in signing if s.get("uses", "").startswith("signpath/")]
check("two SignPath requests: one for the programs, one for the installer", [s["with"]["artifact-configuration-slug"] for s in sp] == ["programs", "installer"])
check("each waits for the result and names an output folder", all(s["with"]["wait-for-completion"] is True and s["with"]["output-artifact-directory"] for s in sp))
check("the SignPath token is only ever referenced as the secret, never written in the file", all(x["with"]["api-token"] == "${{ secrets.SIGNPATH_API_TOKEN }}" for x in sp))
check("the publish step still ships the installer, the code zip, the portable ZIP and the install scripts", all(f in steps[-1]["run"] for f in ("photagSetup.exe", "photag-code-", "-portable.zip", "photag-install.bat", "photag-install.ps1")))
check("the Store package steps run only when the Store variables exist (an unconfigured build is unchanged)",
      len(store) == 2 and all(s.get("if") == "env.STORE == 'true'" for s in store) and all(v in job["env"]["STORE"] for v in ("STORE_IDENTITY_NAME", "STORE_PUBLISHER", "STORE_PUBLISHER_NAME")))
check("the Store package is built after the programs (it contains photag.exe) and before the release is published", idx("Signing: put the signed programs back") < idx("Store: build") < idx("Publish the release"))
check("the MSIX is kept as an artifact, never attached to the public release", "msix" not in steps[-1]["run"].lower())
plain = [name(x) for x in steps if x not in signing and x not in store]
check("without signing, the build is exactly the usual 14 steps in the usual order (installer, its ZIP copy, code zip, portable ZIP, install scripts, publish)",
      len(plain) == 14 and plain[:8] == ["actions/checkout@v4", "actions/setup-python@v5", "python -m pip install -r requirements-dev.txt", "Tag matches app/version.py",
                                         "python tools/make_version_info.py", "python -m PyInstaller --noconfirm photag.spec", "python -m PyInstaller --noconfirm photag_backup.spec",
                                         "choco install innosetup --no-progress -y"]
      and plain[8] == "Build the installer" and plain[9].startswith("Put a copy of the installer in a ZIP") and plain[10].startswith("Build the code update")
      and plain[11].startswith("Build the portable ZIP") and plain[12].startswith("Build the install scripts") and plain[13].startswith("Publish the release"), plain)

# ---- tools/signing.py
import signing  # noqa: E402

tmp = Path(tempfile.mkdtemp(prefix="photag_signing_test_"))
(tmp / "out" / "sub").mkdir(parents=True)
(tmp / "out" / "sub" / "photag.exe").write_bytes(b"signed-photag")
check("find_signed finds a signed file inside the returned folder", signing.find_signed(tmp / "out", "photag.exe").read_bytes() == b"signed-photag")
(tmp / "zipped").mkdir()
with zipfile.ZipFile(tmp / "zipped" / "result.zip", "w") as z:
    z.writestr("photag-backup.exe", b"signed-backup")
check("find_signed also handles a zip of the signed files", signing.find_signed(tmp / "zipped", "photag-backup.exe").read_bytes() == b"signed-backup")
try:
    signing.find_signed(tmp / "out", "missing.exe")
    check("a missing signed file stops the release", False)
except SystemExit as e:
    check("a missing signed file stops the release (clear message)", "missing.exe" in str(e))
try:
    signing.verify([tmp / "out" / "sub" / "photag.exe"])
    check("an unsigned file fails verification", False)
except SystemExit as e:
    check("an unsigned file (or no PowerShell to check with) fails verification, so nothing unsigned can ship", "not validly signed" in str(e) or "cannot check" in str(e), str(e)[:80])
try:
    signing.main(["nonsense"])
    check("unknown command exits", False)
except SystemExit:
    check("unknown command exits", True)
shutil.rmtree(tmp, ignore_errors=True)

n_fail = res.count(False)
print(f"\n{len(res) - n_fail}/{len(res)} passed")
sys.exit(1 if n_fail else 0)
