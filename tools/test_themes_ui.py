"""Test: themes (themes.js + the picker) in a real browser. Edge on Windows, or PHOTAG_TEST_BROWSER=<chromium>.

    py -3.12 tools/test_themes_ui.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
PORT = 8798
APP = f"http://127.0.0.1:{PORT}"
tmp = Path(tempfile.mkdtemp(prefix="photag_themes_"))
for d in ("a", "l", "h"):
    (tmp / d).mkdir()
env = {**os.environ, "APPDATA": str(tmp / "a"), "LOCALAPPDATA": str(tmp / "l"), "USERPROFILE": str(tmp / "h"), "HOME": str(tmp / "h"), "PYTHONIOENCODING": "utf-8"}
res = []


def check(name, ok, extra=""):
    res.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{extra}]" if extra else ""))


def call(m, p, b=None):
    r = urllib.request.Request(APP + p, data=json.dumps(b).encode() if b is not None else None, method=m, headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(r, timeout=30).read() or b"{}")


srv = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.server:app", "--app-dir", str(ROOT), "--port", str(PORT)], env=env,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=str(ROOT))
try:
    for _ in range(80):
        try:
            urllib.request.urlopen(APP + "/api/status", timeout=2)
            break
        except Exception:
            time.sleep(0.5)
    call("POST", "/api/settings/library", {"path": str(tmp / "lib")})
    with sync_playwright() as pw:
        exe = os.environ.get("PHOTAG_TEST_BROWSER")
        br = pw.chromium.launch(executable_path=exe, headless=True, args=["--no-sandbox"]) if exe else pw.chromium.launch(channel="msedge", headless=True)
        ctx = br.new_context(viewport={"width": 1400, "height": 900})
        pg = ctx.new_page()
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto(APP + "/")
        pg.wait_for_function("window.PhotagTheme", timeout=20000)
        ev = pg.evaluate
        ids = ev("PhotagTheme.THEMES.map(t => t.id)")
        check("at least 16 built-in themes, including Classic (the look before 15.0.0) and Slate", len(ids) >= 16 and "classic" in ids and "slate" in ids, ids)
        check("the default is Slate: pills, cards, small-capital titles", ev("[document.documentElement.dataset.tabs, document.documentElement.dataset.cells, document.documentElement.dataset.titles]") == ["pills", "cards", "caps"])
        center0 = ev("getComputedStyle(document.body).backgroundColor")
        ev("PhotagTheme.apply('classic')")
        check("Classic: words for the modules, flat cells, plain titles", ev("[document.documentElement.dataset.tabs, document.documentElement.dataset.cells, document.documentElement.dataset.titles]") == ["words", "flat", "plain"])
        check("Classic has exactly the colours of 14.x (panel #343434, accent #4b98f0)", ev("getComputedStyle(document.documentElement).getPropertyValue('--pnl').trim()") == "#343434" and ev("getComputedStyle(document.documentElement).getPropertyValue('--acc').trim()") == "#4b98f0")
        bad = []
        for i in ids:
            ev(f"PhotagTheme.apply('{i}', {{save: false}})")
            r = ev("""(() => { const cs = getComputedStyle(document.documentElement), pnl = getComputedStyle(document.querySelector('#left') || document.body).backgroundColor;
                const c = n => cs.getPropertyValue(n).trim(); const sw = document.createElement('i'); document.body.appendChild(sw);
                const col = v => { sw.style.color = v; return getComputedStyle(sw).color; }; const o = {};
                o.t1 = PhotagTheme.contrast(col(c('--t1')), col(c('--pnl'))); o.t2 = PhotagTheme.contrast(col(c('--t2')), col(c('--pnl'))); o.t3 = PhotagTheme.contrast(col(c('--t3')), col(c('--pnl')));
                o.acc = PhotagTheme.contrast(col(c('--acc')), col(c('--pnl'))); o.centre = getComputedStyle(document.body).backgroundColor; sw.remove(); return o; })()""")
            if r["t1"] < 4.5 or r["t2"] < 4.5 or r["t3"] < 3 or r["acc"] < 2.2:
                bad.append((i, {k: round(v, 1) if isinstance(v, float) else v for k, v in r.items()}))
            if r["centre"] != center0:
                bad.append((i, "photo surface changed", r["centre"]))
        check("in every theme the text on the panels is readable (WCAG 4.5:1 for the main text) and the photo surface stays the same gray", not bad, bad[:3])
        errs0 = len(errs)
        ev("PhotagTheme.apply('hc-dark', {save: true})")
        pg.reload(); pg.wait_for_function("window.PhotagTheme", timeout=20000)
        check("the choice is kept after a reload", ev("document.documentElement.dataset.themeId") == "hc-dark")
        srv_theme = call("GET", "/api/ui-theme")
        check("...and the server has it too (for the day photag has to use another port)", srv_theme.get("id") == "hc-dark", srv_theme)
        ev("PhotagTheme.apply('light')")
        check("Light: the light base with its own accent", ev("document.documentElement.dataset.theme") == "light")
        ev("PhotagTheme.apply('colorblind')")
        check("Colour-blind friendly: the colour labels use a safe palette", ev("getComputedStyle(document.documentElement).getPropertyValue('--green').trim()") == "#009e73")
        ev("PhotagTheme.setCustom({name: 'Mine', bg: '#203040', acc: '#ff00aa', light: false}); PhotagTheme.apply('custom')")
        check("a custom theme from a background and an accent", ev("getComputedStyle(document.documentElement).getPropertyValue('--acc').trim()") == "#ff00aa" and ev("document.documentElement.dataset.themeId") == "custom")
        check("a theme file is read safely (only the four fields), and a wrong file is refused", ev("PhotagTheme.parseThemeFile('{\"photag-theme\":1,\"name\":\"X\",\"bg\":\"#112233\",\"acc\":\"#445566\",\"evil\":\"<script>\"}')") == {"name": "X", "bg": "#112233", "acc": "#445566", "light": False}
              and ev("PhotagTheme.parseThemeFile('{\"bg\":\"red\"}')") is None and ev("PhotagTheme.parseThemeFile('nonsense')") is None)
        # the picker
        ev("PhotagTheme.apply('slate'); themeDialog(); 0")
        pg.wait_for_selector(".th-card", timeout=5000)
        n = pg.locator(".th-card").count()
        check("the picker shows every theme as a card (and the custom one)", n >= len(ids) + 1, n)
        pg.locator(".th-card[data-th=forest]").click()
        check("a click on a card applies that theme at once", ev("document.documentElement.dataset.themeId") == "forest" and "on" in pg.get_attribute(".th-card[data-th=forest]", "class"))
        pg.click("#th-close")
        pg.keyboard.press("Control+k"); pg.wait_for_selector("#pl-q"); pg.fill("#pl-q", "themes")
        check("Ctrl+K finds the themes", "Themes" in pg.inner_text("#pl-list"), pg.inner_text("#pl-list")[:80])
        pg.keyboard.press("Escape")
        # the viewer follows
        sample = tmp / "v.jpg"
        from PIL import Image
        Image.new("RGB", (80, 60), (10, 90, 160)).save(sample, "JPEG")
        tok = call("POST", "/api/viewer/open", {"path": str(sample)})["token"]
        ev("PhotagTheme.apply('rose')")
        vp = ctx.new_page()
        vp.goto(f"{APP}/viewer.html?t={tok}")
        vp.wait_for_function("document.querySelector('#name').textContent === 'v.jpg'", timeout=15000)
        acc = vp.evaluate("getComputedStyle(document.documentElement).getPropertyValue('--acc').trim()")
        check("the viewer window uses the same theme (accent of Rose)", acc == "#ff7a9c", acc)
        check("no JavaScript errors", not errs, errs[:2])
        br.close()
finally:
    srv.terminate()
    try:
        srv.wait(timeout=10)
    except Exception:
        srv.kill()
shutil.rmtree(tmp, ignore_errors=True)
n = res.count(False)
print(f"\n{len(res) - n}/{len(res)} passed")
sys.exit(1 if n else 0)
