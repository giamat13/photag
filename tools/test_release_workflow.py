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
job, ijob, pjob = wf["jobs"]["build"], wf["jobs"]["installer"], wf["jobs"]["packages"]      # programs | installer | the other packages (the last two run at the same time)
steps, isteps, psteps = job["steps"], ijob["steps"], pjob["steps"]
pub = wf["jobs"]["publish"]
pub_run = pub["steps"][-1]["run"]
name = lambda s: s.get("name") or s.get("run") or s.get("uses") or ""
mk = lambda st: (lambda needle: next(i for i, s in enumerate(st) if needle in name(s)))
idx, iidx, pidx = mk(steps), mk(isteps), mk(psteps)
signing = [s for s in steps + isteps if "Signing:" in name(s)]
store = [s for s in psteps if "Store:" in name(s)]

check("signing is switched on only by repository variables + a secret (SIGNING env at job level, in the two jobs that sign)",
      all(k in j["env"]["SIGNING"] for j in (job, ijob) for k in ("SIGNPATH_ORGANIZATION_ID", "SIGNPATH_PROJECT_SLUG", "SIGNPATH_API_TOKEN")))
check("every signing step is conditional on it (an unconfigured build behaves exactly as before)", len(signing) == 7 and all(s.get("if") == "env.SIGNING == 'true'" for s in signing), len(signing))
check("no non-signing step depends on signing", all("if" not in s or "SIGNING" not in str(s["if"]) for s in steps + isteps + psteps if s not in signing))
check("the programs are signed AFTER PyInstaller and BEFORE they are handed on to the installer and packages jobs", idx("PyInstaller --noconfirm photag_backup.spec") < idx("Signing: collect the programs") < idx("Signing: put the signed programs back") < idx("Keep the built programs"))
check("the installer is signed right after it is built, before its ZIP copy and before it is handed to the publish job", iidx("Build the installer") < iidx("Signing: upload the installer") < iidx("Signing: put the signed installer back") < iidx("Put a copy of the installer in a ZIP") < iidx("Keep the release files"))
check("the ZIP copy of the installer contains the SIGNED installer (it is made after signing)", iidx("Signing: put the signed installer back") < iidx("Put a copy of the installer in a ZIP"))
check("the installer job and the packages job both start from the signed programs (artifact built-app) and wait only for the build job",
      ijob["needs"] == "build" and pjob["needs"] == "build" and all(any(s.get("uses", "").startswith("actions/download-artifact") and s["with"]["name"] == "built-app" for s in st) for st in (isteps, psteps))
      and steps[-1]["with"]["name"] == "built-app")
check("the portable ZIP is built after the programs are signed (it contains photag.exe)", pidx("Build the portable ZIP") > pidx("actions/download-artifact"))
sp = [s for s in signing if s.get("uses", "").startswith("signpath/")]
check("two SignPath requests: one for the programs, one for the installer", [s["with"]["artifact-configuration-slug"] for s in sp] == ["programs", "installer"])
check("each waits for the result and names an output folder", all(s["with"]["wait-for-completion"] is True and s["with"]["output-artifact-directory"] for s in sp))
check("the SignPath token is only ever referenced as the secret, never written in the file", all(x["with"]["api-token"] == "${{ secrets.SIGNPATH_API_TOKEN }}" for x in sp))
check("the Windows build runs in parallel with the Linux / macOS builds; a publish job joins them and attaches all", "needs" not in wf["jobs"]["build"] and wf["jobs"]["unix"]["uses"].endswith("unix-build.yml")
      and sorted(pub["needs"]) == ["build", "installer", "packages", "tests", "unix"] and "linux-x64.tar.gz" in pub_run and "macos-" in pub_run)
check("the release runs the tests next to the builds (a reusable workflow) and publishes only if they pass", wf["jobs"]["tests"]["uses"].endswith("tests.yml") and "tests" in pub["needs"])
check("the two Windows jobs hand their files to the publish job as artifacts release-windows-* (not matching the photag-* pattern of the others)",
      isteps[-1]["uses"].startswith("actions/upload-artifact") and isteps[-1]["with"]["name"] == "release-windows-installer"
      and psteps[-1]["uses"].startswith("actions/upload-artifact") and psteps[-1]["with"]["name"] == "release-windows-packages")
check("the publish step still ships the installer, the code zip, the portable ZIP and the install scripts", all(f in pub_run for f in ("photagSetup.exe", "photag-code-", "-portable.zip", "photag-install.bat", "photag-install.ps1")))
check("the Store package steps run only when the Store variables exist (an unconfigured build is unchanged)",
      len(store) == 2 and all(s.get("if") == "env.STORE == 'true'" for s in store) and all(v in pjob["env"]["STORE"] for v in ("STORE_IDENTITY_NAME", "STORE_PUBLISHER", "STORE_PUBLISHER_NAME")))
check("the Store package is built from the downloaded programs and before the release files are kept", pidx("actions/download-artifact") < pidx("Store: build") < pidx("Keep the release files"))
msi = [x for x in psteps if name(x).startswith("Build the MSI")]
check("the MSIX is kept as an artifact, never attached to the public release", "msix" not in pub_run.lower())
check("the MSI is built after the install scripts, can fail without stopping the release, and is attached when it was made",
      len(msi) == 1 and msi[0].get("continue-on-error") is True and pidx("Build the install scripts") < pidx("Build the MSI") < pidx("Keep the release files")
      and "photag-*.msi" in pub_run and "nullglob" in pub_run)
reports = [x for x in steps + psteps if name(x).startswith("Reports:")]
check("the build job and the packages job (the code zip holds app/) write the report token (from the REPORT_TOKEN secret); the build job before it builds the program",
      len(reports) == 2 and all("write_report_token.py" in str(r.get("run")) and "REPORT_TOKEN" in str(r.get("env")) for r in reports)
      and idx("Reports:") < min(i for i, x in enumerate(steps) if "PyInstaller" in str(x.get("run"))) and pidx("Reports:") < pidx("Build the code update"))
plain_b = [name(x) for x in steps if x not in signing]
plain_i = [name(x) for x in isteps if x not in signing]
plain_p = [name(x) for x in psteps if x not in store and x not in msi and x not in reports]
check("without signing, the build job is: setup, tag, report token, version info, PyInstaller x2, keep the programs", plain_b[:4] == ["actions/checkout@v4", "actions/setup-python@v5", "python -m pip install -r requirements-dev.txt", "Tag matches app/version.py"]
      and plain_b[6:8] == ["python -m PyInstaller --noconfirm photag.spec", "python -m PyInstaller --noconfirm photag_backup.spec"] and plain_b[-1].startswith("Keep the built programs"), plain_b)
check("without signing, the installer job builds the installer, its ZIP copy and keeps them", "Build the installer" in plain_i and plain_i[-2].startswith("Keep the release files") and any(x.startswith("Put a copy of the installer in a ZIP") for x in plain_i), plain_i)
check("without signing, the packages job builds the code zip, the portable ZIP and the install scripts, in that order",
      [x.split(" (")[0] for x in plain_p if x.startswith("Build")] == ["Build the code update", "Build the portable ZIP", "Build the install scripts"], plain_p)

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
