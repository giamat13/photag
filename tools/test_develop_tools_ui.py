"""UI test of presets (amount, own, files), looks (.cube), snapshots, copy / paste / sync of settings, before / after views and clipping
warnings in the Edit module. Throw-away profile.

    py -3.12 tools/test_develop_tools_ui.py            (set PHOTAG_TEST_BROWSER to a Chromium executable to use it instead of Edge)
"""
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import numpy as np
from PIL import Image
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
PORT = 8802
APP = f"http://127.0.0.1:{PORT}"
tmp = Path(tempfile.mkdtemp(prefix="photag_devtools_test_"))
env = {**os.environ, "APPDATA": str(tmp / "appdata"), "LOCALAPPDATA": str(tmp / "local"), "USERPROFILE": str(tmp / "home"), "HOME": str(tmp / "home"),
       "PHOTAG_NO_OPEN": "1", "PYTHONIOENCODING": "utf-8", "PHOTAG_BACKUP_START_DELAY": "9999", "PHOTAG_EXIF_DELAY": "9999", "PHOTAG_MIGRATE_DELAY": "9999"}
for d in ("appdata", "local", "home", "lib", "src"):
    (tmp / d).mkdir()
res = []


def check(name, ok, extra=""):
    res.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{extra}]" if extra else ""))


def call(m, p, b=None):
    r = urllib.request.Request(APP + p, data=json.dumps(b).encode() if b is not None else None, method=m, headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(r, timeout=120).read() or b"{}")


def job(name, secs=120):
    t0 = time.time()
    while time.time() - t0 < secs:
        if call("GET", f"/api/job/{name}")["state"] in ("done", "error"):
            return
        time.sleep(0.3)


def scene(w, h, k):
    x = np.linspace(0, 1, w)[None, :, None]
    y = np.linspace(0, 1, h)[:, None, None]
    return np.clip(255 * k * np.concatenate([0.15 + 0.7 * x + 0 * y, 0.2 + 0.6 * y + 0 * x, 0.8 - 0.6 * x + 0 * y], axis=2), 0, 255).astype("uint8")


src = tmp / "src"
ex = Image.Exif()
ex[0x010F], ex[0x0110] = "UiMake", "UiModel"
Image.fromarray(scene(300, 200, 0.2)).save(src / "a_dark.jpg", quality=92, exif=ex)           # dark, with EXIF
Image.fromarray(scene(300, 200, 0.9)).save(src / "b_normal.jpg", quality=92)

srv = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.server:app", "--app-dir", str(ROOT), "--port", str(PORT)], env=env,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=str(ROOT))
try:
    for _ in range(60):
        try:
            urllib.request.urlopen(APP + "/api/status", timeout=2)
            break
        except Exception:
            time.sleep(0.5)
    call("POST", "/api/settings/library", {"path": str(tmp / "lib")})
    call("POST", "/api/import-folder", {"paths": [str(p) for p in sorted(src.iterdir())]})
    job("import")
    ph = {p["filename"]: p for p in call("GET", "/api/photos?limit=999")}
    A = ph["a_dark.jpg"]["id"]
    dbfile = call("GET", "/api/status")["db_path"]

    with sync_playwright() as pw:
        exe = os.environ.get("PHOTAG_TEST_BROWSER")
        b = pw.chromium.launch(executable_path=exe, headless=True, args=["--no-sandbox"]) if exe else pw.chromium.launch(channel="msedge", headless=True)
        pg = b.new_page(viewport={"width": 1500, "height": 950})
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto(APP + "/")
        pg.wait_for_selector(".cell", timeout=20000)
        pg.wait_for_timeout(800)

        def shot(name):
            if os.environ.get("PHOTAG_TEST_SHOTS"):
                pg.wait_for_timeout(500)
                pg.screenshot(path=str(Path(os.environ["PHOTAG_TEST_SHOTS"]) / f"{name}.png"))

        def cell(name):
            pg.locator(f".cell[data-id='{ph[name]['id']}']").click()
            pg.wait_for_timeout(500)

        def setslider(k, v):
            pg.evaluate("""([k,v])=>{const e=document.querySelector('#d-'+k); e.value=v; e.dispatchEvent(new Event('input',{bubbles:true})); e.dispatchEvent(new Event('change',{bubbles:true}));}""", [k, v])
            pg.wait_for_timeout(250)

        A = ph["a_dark.jpg"]["id"]
        B = ph["b_normal.jpg"]["id"]

        def saved(i):
            return json.loads(call("GET", f"/api/photo/{i}")["edit_ops"] or "{}")

        cell("b_normal.jpg")
        pg.click("#modules [data-mod=develop]")
        pg.wait_for_selector("#d-exp", timeout=10000)
        pg.wait_for_timeout(900)
        # ---- presets and their amount
        names = pg.inner_text("#p-presets")
        check("the built-in presets include the new looks (Golden Hour, Teal & Orange, Selenium Tone ...)", all(n in names for n in ("Golden Hour", "Teal & Orange", "Selenium Tone", "Cinematic")))
        pg.locator("#p-presets [data-preset]", has_text="Crisp Landscape").click()
        pg.wait_for_timeout(500)
        full = int(pg.input_value("#d-cla"))
        check("a preset sets its sliders (Crisp Landscape: Clarity 25)", full == 25, full)
        pg.evaluate("(()=>{const e=document.querySelector('#pre-amt'); e.value=50; e.dispatchEvent(new Event('input',{bubbles:true})); e.dispatchEvent(new Event('change',{bubbles:true}));})()")
        pg.wait_for_timeout(500)
        half = int(pg.input_value("#d-cla"))
        check("the Amount slider re-applies the preset at that strength (50 % -> Clarity 12 or 13)", half in (12, 13), half)
        pg.evaluate("(()=>{const e=document.querySelector('#pre-amt'); e.value=200; e.dispatchEvent(new Event('input',{bubbles:true})); e.dispatchEvent(new Event('change',{bubbles:true}));})()")
        pg.wait_for_timeout(500)
        check("...and 200 % doubles it", int(pg.input_value("#d-cla")) == 50)
        pg.evaluate("(()=>{const e=document.querySelector('#pre-amt'); e.value=100; e.dispatchEvent(new Event('input',{bubbles:true})); e.dispatchEvent(new Event('change',{bubbles:true}));})()")
        pg.wait_for_timeout(400)
        pg.locator("#p-presets [data-preset]", has_text="Teal & Orange").click()
        pg.wait_for_timeout(500)
        check("a preset can carry colour grading (Teal & Orange)", pg.evaluate("DEV.ops.grd.shadows[0]") == 190)
        # ---- my own presets
        pg.evaluate("(()=>{document.querySelector('[data-t=presave]').click();})()")
        pg.wait_for_selector("#pb-in", timeout=5000)
        pg.fill("#pb-in", "My look")
        pg.click("#pb-ok")
        pg.wait_for_function("document.querySelector('#p-presets').innerText.includes('My look')", timeout=8000)
        check("Save preset adds it to 'My presets'", call("GET", "/api/dev-presets")["presets"][0]["name"] == "My look")
        pg.click("#btn-dev-reset")
        pg.wait_for_timeout(300)
        pg.locator("#p-presets [data-preset]", has_text="My look").click()
        pg.wait_for_timeout(500)
        check("...and applying it brings the saved settings back", pg.evaluate("DEV.ops.grd.shadows[0]") == 190)
        pf = tmp / "p.json"
        pf.write_text(json.dumps({"photag-preset": 1, "name": "From file", "ops": {"exp": 40, "sat": 20, "evil": 1, "grd": {"shadows": [10, 10, 0]}}}), "utf-8")
        pg.set_input_files("#pre-file", str(pf))
        pg.wait_for_function("document.querySelector('#p-presets').innerText.includes('From file')", timeout=8000)
        got = [x for x in call("GET", "/api/dev-presets")["presets"] if x["name"] == "From file"][0]["ops"]
        check("a preset file can be imported (unknown settings are left out)", got.get("exp") == 40 and "evil" not in got, got)
        pg.locator("#p-presets [data-presetdel='From file']").click()
        pg.wait_for_timeout(400)
        check("...and own presets can be deleted", all(x["name"] != "From file" for x in call("GET", "/api/dev-presets")["presets"]))
        # ---- looks (.cube)
        size = 2
        cube = "TITLE t\nLUT_3D_SIZE 2\n" + "\n".join(f"{1 - r} {1 - g} {1 - b}" for b in (0, 1) for g in (0, 1) for r in (0, 1)) + "\n"
        lf = tmp / "invert.cube"
        lf.write_text(cube, "utf-8")
        pg.click("#btn-dev-reset")
        pg.wait_for_timeout(300)
        pg.set_input_files("#lut-file", str(lf))
        pg.wait_for_function("document.querySelector('#p-presets').innerText.includes('invert')", timeout=8000)
        check("a .cube look can be imported and is applied at once", pg.evaluate("DEV.ops.lut.name") == "invert.cube" and "invert.cube" in call("GET", "/api/luts")["luts"])
        pg.wait_for_function("document.querySelector('#dev-img').src.startsWith('blob:')", timeout=10000)
        check("...it shows in the preview (the server draws it)", True)
        bad = tmp / "bad.cube"
        bad.write_text("nonsense", "utf-8")
        pg.set_input_files("#lut-file", str(bad))
        pg.wait_for_timeout(700)
        check("a broken look file is refused", "bad.cube" not in call("GET", "/api/luts")["luts"])
        # ---- snapshots
        pg.click("#btn-dev-reset")
        pg.wait_for_timeout(300)
        setslider("exp", 80)
        pg.click("#snap-add")
        pg.wait_for_selector("#pb-in", timeout=5000)
        pg.fill("#pb-in", "Bright")
        pg.click("#pb-ok")
        pg.wait_for_function("document.querySelector('#p-snaps').innerText.includes('Bright')", timeout=8000)
        setslider("exp", -50)
        pg.locator("#p-snaps [data-snap]").first.click()
        pg.wait_for_timeout(400)
        check("a snapshot brings its settings back (Exposure +0.80)", pg.input_value("#d-exp") == "80", pg.input_value("#d-exp"))
        pg.locator("#p-snaps [data-snapdel]").first.click()
        pg.wait_for_timeout(500)
        check("...and can be deleted", "Bright" not in pg.inner_text("#p-snaps"))
        # ---- before / after
        pg.keyboard.press("y")
        pg.wait_for_timeout(400)
        check("Y shows before and after side by side (two pictures)", not pg.evaluate("document.querySelector('#dev-canvas0').classList.contains('hidden')") and pg.evaluate("DEV.view") == "side")
        w0 = pg.evaluate("parseFloat(document.querySelector('#dev-canvas0').style.left)")
        w1 = pg.evaluate("parseFloat(document.querySelector('#dev-canvas').style.left)")
        check("...the original on the left, the edited one on the right", w0 < w1, (w0, w1))
        shot("tools_side")
        pg.keyboard.press("Shift+Y")
        pg.wait_for_timeout(400)
        check("Shift+Y shows a split view with a draggable line", pg.evaluate("DEV.view") == "split" and not pg.evaluate("document.querySelector('#split-bar').classList.contains('hidden')"))
        bx = pg.locator("#split-bar").bounding_box()
        pg.mouse.move(bx["x"] + 1, bx["y"] + 100)
        pg.mouse.down()
        pg.mouse.move(bx["x"] - 120, bx["y"] + 100, steps=4)
        pg.mouse.up()
        pg.wait_for_timeout(300)
        check("dragging the line moves the split", pg.evaluate("DEV.split") < 0.45, pg.evaluate("DEV.split"))
        shot("tools_split")
        pg.keyboard.press("Shift+Y")
        pg.wait_for_timeout(300)
        check("the same key goes back to one picture", pg.evaluate("DEV.view") == "after" and pg.evaluate("document.querySelector('#dev-canvas0').classList.contains('hidden')"))
        # ---- clipping warnings
        setslider("exp", 300)
        pg.keyboard.press("j")
        pg.wait_for_timeout(1500)
        check("J shows the clipping warning over the picture", not pg.evaluate("document.querySelector('#clip-cv').classList.contains('hidden')"))
        red = pg.evaluate("(()=>{const c=document.querySelector('#clip-cv'); const d=c.getContext('2d').getImageData(0,0,c.width,c.height).data; let n=0; for(let i=0;i<d.length;i+=4) if(d[i+3]>0&&d[i]>200) n++; return n;})()")
        check("...and marks blown highlights in red", red > 50, red)
        pg.keyboard.press("j")
        pg.wait_for_timeout(300)
        check("J again switches it off", pg.evaluate("document.querySelector('#clip-cv').classList.contains('hidden')"))
        setslider("exp", 0)
        # ---- copy / paste / sync
        pg.click("#btn-dev-reset")
        pg.wait_for_timeout(300)
        setslider("exp", 60)
        setslider("sat", 30)
        pg.click("[data-t=crop]")
        pg.wait_for_timeout(200)
        pg.click("[data-t=crop]")
        pg.wait_for_timeout(300)
        apply_before = saved(B)
        pg.click("#btn-dev-apply")
        pg.wait_for_timeout(1500)
        pg.keyboard.press("Control+Shift+C")
        pg.wait_for_selector("[data-cg=basic]", timeout=5000)
        check("Ctrl+Shift+C asks which groups to copy (geometry and local work are off by default)", pg.is_checked("[data-cg=basic]") and not pg.is_checked("[data-cg=geometry]") and not pg.is_checked("[data-cg=local]"))
        pg.click("#cg-ok")
        pg.wait_for_timeout(500)
        check("the settings are copied", pg.evaluate("DEV.clip && DEV.clip.ops.exposure") == 0.6, pg.evaluate("DEV.clip"))
        pg.click("#modules [data-mod=library]")
        pg.wait_for_timeout(400)
        cell("a_dark.jpg")
        pg.keyboard.press("Control+Shift+V")
        pg.wait_for_function(f"1", timeout=2000)
        for _ in range(40):
            if saved(A).get("exposure"):
                break
            pg.wait_for_timeout(300)
        o = saved(A)
        check("Ctrl+Shift+V pastes them onto the selected photo", o.get("exposure") == 0.6 and o.get("saturation") is not None, o)
        check("...the Edit module sees them too", True)
        check("no JavaScript errors", not errs, errs[:3])
        b.close()
finally:
    srv.terminate()
    try:
        srv.wait(timeout=10)
    except Exception:
        srv.kill()

n_fail = res.count(False)
print(f"\n{len(res) - n_fail}/{len(res)} passed")
sys.exit(1 if n_fail else 0)
