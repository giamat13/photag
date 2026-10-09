"""A live page about a release, in plain language: the local tests (from local-tests.json, written by run_local_tests.py), the commit and
push, and what GitHub is doing (the release workflow's jobs and steps), with a time estimate for each part and for the whole.

Open tools/release_status/status.html in a browser. It loads fresh data (status-data.js, written here every few seconds) without
reloading the page, its clocks tick every second, and it says so loudly when the data stops coming. Read-only: it never pushes,
releases or changes anything. The GitHub token (from git's credential store) stays in this process and is never written anywhere.

    py -3.12 tools/release_status/status_page.py            until the version of app/version.py is published (at most 3 hours)
    py -3.12 tools/release_status/status_page.py --once     write the data once and stop
"""
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
PAGE = HERE / "status.html"
DATA = HERE / "status-data.js"
PROGRESS = HERE / "local-tests.json"
REPO = "giamat13/photag"
DEFAULT_GH_SECONDS = 720
LOCAL_EVERY, GITHUB_EVERY = 3, 8          # seconds


def git(*args):
    r = subprocess.run(["git", *args], capture_output=True, text=True, cwd=str(ROOT))
    return r.stdout.strip()


def token():
    r = subprocess.run(["git", "credential", "fill"], input="protocol=https\nhost=github.com\n\n", capture_output=True, text=True)
    return next((l[9:] for l in r.stdout.splitlines() if l.startswith("password=")), "")


TOKEN = token()


def api(path):
    req = urllib.request.Request("https://api.github.com/repos/" + REPO + path, headers={
        "Authorization": "Bearer " + TOKEN, "Accept": "application/vnd.github+json", "User-Agent": "photag-status"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


def epoch(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp() if s else None


def version():
    m = re.search(r'__version__\s*=\s*"([^"]+)"', (ROOT / "app" / "version.py").read_text("utf-8"))
    return m.group(1) if m else "?"


# ---- plain-language names of what GitHub does (job names and step names come from .github/workflows)
JOBS = {
    "build": ("בניית התוכנה להתקנה (Windows)", "בונה את התוכנה עצמה, את קובץ ההתקנה, את גרסת ה-ZIP, את ה-MSI ואת חבילת החנות"),
    "tests / unix (ubuntu-latest)": ("בדיקות ב-Linux", "מוודא שהתוכנה עובדת גם על Linux"),
    "tests / unix (macos-latest)": ("בדיקות ב-Mac", "מוודא שהתוכנה עובדת גם על Mac"),
    "tests / test (1)": ("בדיקות שרת וייבוא (חלק 1 מ-2)", "מריץ בדיקות על ייבוא, גיבוי, עדכון וספרייה"),
    "tests / test (2)": ("בדיקות שרת וייבוא (חלק 2 מ-2)", "מריץ בדיקות על ייבוא, גיבוי, עדכון וספרייה"),
    "tests / ui": ("בדיקות ממשק (בדפדפן)", "פותח את התוכנה בדפדפן ולוחץ בה כמו משתמש, לוודא שהכול נראה ועובד"),
    "unix / build (ubuntu-latest)": ("בניית גרסת Linux", "בונה את התוכנה ל-Linux"),
    "unix / build (macos-latest)": ("בניית גרסת Mac", "בונה את התוכנה ל-Mac"),
    "publish": ("פרסום הגרסה", "אוסף את כל הקבצים ומפרסם אותם — זה השלב האחרון"),
}
STEPS = [  # (a part of the step name, plain text)
    ("pip install", "מתקין ספריות"), ("Tag matches", "בודק שמספר הגרסה נכון"), ("Reports:", "מכין את שליחת הדיווחים"),
    ("make_version_info", "מכין את פרטי הגרסה"), ("photag_backup.spec", "בונה את תוכנית הגיבוי"), ("photag.spec", "בונה את התוכנה (photag.exe)"),
    ("Signing", "חותם דיגיטלית על הקבצים (כדי ש-Windows יסמוך עליהם)"), ("choco install innosetup", "מתקין את כלי יצירת קובץ ההתקנה"),
    ("Build the installer", "בונה את קובץ ההתקנה (photagSetup.exe)"), ("Put a copy of the installer", "אורז את קובץ ההתקנה גם ב-ZIP"),
    ("Build the code update", "בונה עדכון קוד קטן (עדכון בלי התקנה מחדש)"), ("portable ZIP", "בונה גרסה ניידת (ZIP)"),
    ("install scripts", "בונה סקריפטים להתקנה"), ("Build the MSI", "בונה חבילת MSI"), ("Store: build", "בונה חבילה לחנות של מיקרוסופט"),
    ("Store: keep", "שומר את חבילת החנות"), ("Collect the release files", "אוסף את קבצי הגרסה"),
    ("Keep the release files", "מעביר את הקבצים לשלב הפרסום"), ("Translations are complete", "בודק שכל התרגומים קיימים"),
    ("System libraries", "מתקין רכיבי מערכת"), ("Tests that do not need Windows", "מריץ בדיקות כלליות"),
    ("Chromium for the UI", "מתקין דפדפן לבדיקות"), ("UI tests on Linux", "בדיקות ממשק"), ("Server, import, backup", "בדיקות שרת, ייבוא, גיבוי ועדכון"),
    ("Test of \"Report", "בדיקת דיווח תקלה"), ("UI smoke test", "בדיקת ממשק ראשית"), ("UI test of", "בדיקת ממשק"),
    ("The packaged program starts", "בודק שהתוכנה הארוזה נפתחת"), ("Build the program", "בונה את התוכנה"), ("Pack it", "אורז"),
    ("upload-artifact", "מעלה את הקבצים"), ("Take the Windows files", "אוסף את קבצי Windows, Linux ו-Mac"),
    ("Put them together", "מרכיב את הגרסה"), ("Publish the release", "מפרסם את הגרסה ב-GitHub"),
]
PLUMBING = ("Set up job", "Complete job", "Post Run", "Run actions/checkout", "Run actions/setup-python")


def plain_step(name):
    for key, text in STEPS:
        if key in name:
            return text, ("" if name == text else name)
    return name, ""


def state_of(o):
    if o.get("status") != "completed":
        return "run" if o.get("status") == "in_progress" else "todo"
    return "done" if o.get("conclusion") in ("success", "neutral", "skipped") else "fail"


class Cache:
    gh_at = 0.0
    gh = {}


def github(remote):
    """Runs, jobs and releases, at most every GITHUB_EVERY seconds."""
    if time.time() - Cache.gh_at < GITHUB_EVERY and Cache.gh.get("remote") == remote:
        return Cache.gh
    out = {"remote": remote, "error": "", "runs": [], "rels": [], "jobs": [], "cur": None}
    try:
        out["runs"] = api("/actions/runs?per_page=15")["workflow_runs"]
        out["rels"] = api("/releases?per_page=4")
        rel_runs = [r for r in out["runs"] if r["name"] == "release"]
        out["cur"] = next((r for r in rel_runs if r["head_sha"] == remote), None) if remote else None
        if out["cur"]:
            out["jobs"] = api(f"/actions/runs/{out['cur']['id']}/jobs?per_page=50")["jobs"]
    except Exception as e:
        out["error"] = f"GitHub לא ענה ({e}). מנסה שוב."
        out["runs"] = Cache.gh.get("runs", []) if Cache.gh else []
    Cache.gh, Cache.gh_at = out, time.time()
    return out


def collect():
    now = time.time()
    target = "v" + version()
    # ---- local tests
    try:
        prog = json.loads(PROGRESS.read_text("utf-8"))
    except (OSError, ValueError):
        prog = None
    local, local_left, local_state, local_now = [], 0.0, "todo", ""
    if prog:
        for i, t in enumerate(prog["tests"]):
            left = 0.0
            if t["state"] == "run":
                left = max(0.0, t["estimate"] - (now - t["began"]))
                local_now = f"בדיקה {i + 1} מתוך {len(prog['tests'])}: {t['name']}"
            elif t["state"] == "todo":
                left = t["estimate"]
            local_left += left
            local.append({"name": t["name"], "state": t["state"], "summary": t.get("summary", ""), "seconds": t.get("seconds"),
                          "estimate": t["estimate"], "began": t.get("began"), "detail": t.get("detail", "")})
        sts = [t["state"] for t in prog["tests"]]
        local_state = "fail" if "fail" in sts else ("done" if all(s == "done" for s in sts) else "run")

    # ---- git
    dirty = len([l for l in git("status", "--porcelain").splitlines() if l.strip()])
    head = git("rev-parse", "HEAD")
    remote = git("ls-remote", "origin", "refs/heads/main").split("\t")[0] if time.time() - Cache.gh_at >= GITHUB_EVERY else Cache.gh.get("remote", "")
    pushed = bool(remote) and remote == head

    # ---- GitHub
    gh = github(remote if pushed else "")
    cur, rels = gh["cur"], gh["rels"]
    rel_runs = [r for r in gh["runs"] if r["name"] == "release"]
    prev = [r for r in rel_runs if r["status"] == "completed" and r["conclusion"] == "success"][:3]
    typical = sum(epoch(r["updated_at"]) - epoch(r["created_at"]) for r in prev) / len(prev) if prev else DEFAULT_GH_SECONDS
    published = any(r["tag_name"] == target and not r["draft"] for r in rels)
    gh_state = "done" if published else (state_of(cur) if cur else "todo")
    cur_began = epoch(cur["created_at"]) if cur else None
    if published or gh_state == "fail":
        gh_left = 0.0
    elif cur:
        gh_left = max(0.0, typical - (now - cur_began))
    else:
        gh_left = typical

    jobs, hero_run = [], ""
    for j in gh["jobs"]:
        plain, desc = JOBS.get(j["name"], (j["name"], ""))
        steps = []
        for s in j.get("steps", []):
            text, small = plain_step(s["name"])
            steps.append({"text": text, "small": small, "state": state_of(s), "plumbing": s["name"].startswith(PLUMBING)})
        done_n = sum(1 for s in steps if s["state"] == "done")
        running = next((s for s in steps if s["state"] == "run" and not s["plumbing"]), None) or next((s for s in steps if s["state"] == "run"), None)
        if running and not hero_run and not running["plumbing"]:
            hero_run = f"{plain}: {running['text']}"
        jobs.append({"name": plain, "desc": desc, "state": state_of(j), "done": done_n, "total": len(steps), "began": epoch(j.get("started_at")),
                     "now": running["text"] if running else "", "steps": [s for s in steps if not s["plumbing"]]})

    stages = [
        {"name": "בודקים שהכול עובד אצלך במחשב", "plain": "מריצים אוטומטית עשרות בדיקות על התוכנה כדי לוודא ששום דבר לא נשבר.", "state": local_state,
         "note": local_now},
        {"name": "שומרים את השינויים", "plain": "שומרים את העבודה בהיסטוריה של הפרויקט (זה נקרא commit).", "state": "done" if dirty == 0 else "todo",
         "note": "" if dirty == 0 else f"{dirty} קבצים עוד לא נשמרו"},
        {"name": "מעלים ל-GitHub", "plain": "שולחים את הקוד לאינטרנט כדי שאפשר יהיה לבנות ממנו גרסה (זה נקרא push).", "state": "done" if pushed else "todo", "note": ""},
        {"name": "מבקשים מ-GitHub לבנות גרסה", "plain": "לוחצים על \"שחרר\": GitHub מתחיל לעבוד לבד.", "state": "done" if cur else "todo", "note": ""},
        {"name": "GitHub בודק ובונה", "plain": "מריצים את הבדיקות שוב על שרתים של GitHub, ובונים את כל קובצי ההתקנה.",
         "state": gh_state if (cur or published) else "todo", "note": hero_run},
        {"name": f"הגרסה {target} באוויר", "plain": "מופיעה בדף ההורדות, ואפשר לעדכן אליה מתוך photag.", "state": "done" if published else ("fail" if gh_state == "fail" else "todo"), "note": ""},
    ]
    n_done = sum(1 for s in stages if s["state"] == "done")
    left = (local_left if local_state != "done" else 0.0) + gh_left
    failing = [s["name"] for s in stages if s["state"] == "fail"]
    if published:
        hero, sub = f"הגרסה {target} באוויר! ✓", "אפשר לעדכן מתוך photag (עזרה ← חיפוש עדכונים)."
    elif failing:
        hero, sub = "משהו נכשל", "הסעיף האדום למטה מראה איפה. אפשר לספר לי ואטפל בזה."
    elif local_state == "run":
        hero, sub = "עכשיו: בודקים שהכול עובד במחשב שלך", local_now
    elif not pushed:
        hero, sub = "עכשיו: מעלים את השינויים", "ואז GitHub יתחיל לבנות."
    elif not cur:
        hero, sub = "עכשיו: ממתינים שה-שחרור יתחיל ב-GitHub", ""
    else:
        hero, sub = "עכשיו: GitHub בונה את הגרסה", hero_run

    return {
        "at": now, "target": target, "hero": hero, "sub": sub, "published": published, "failed": bool(failing),
        "eta_end": None if (published or failing) else now + left, "local_left": local_left, "gh_left": gh_left, "typical": typical,
        "percent": int(100 * n_done / len(stages)), "stages": stages, "local": local, "jobs": jobs, "gh_began": cur_began,
        "error": gh["error"], "recent": [r["tag_name"] for r in rels[:3]],
    }


TEMPLATE = r"""<!doctype html><html lang="he" dir="rtl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>מצב שחרור photag</title><style>
:root{--bg:#f4f6f8;--card:#fff;--tx:#1b1f24;--mut:#5d6874;--ok:#1a7f37;--run:#0969da;--bad:#cf222e;--warn:#9a6700;--todo:#8a949f;--ln:#e1e5ea}
@media (prefers-color-scheme:dark){:root{--bg:#12161b;--card:#1b2128;--tx:#e6eaee;--mut:#9aa6b2;--ok:#3fb950;--run:#58a6ff;--bad:#f85149;--warn:#d29922;--todo:#6b7783;--ln:#2b333c}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--tx);font:16px/1.55 system-ui,Segoe UI,Arial,sans-serif}
main{max-width:820px;margin:0 auto;padding:18px 14px 50px}
.live{display:flex;align-items:center;gap:8px;font-size:14px;color:var(--mut)}
.dot{width:10px;height:10px;border-radius:50%;background:var(--ok);animation:pulse 1.2s infinite}
.live.stale .dot{background:var(--warn);animation:none}.live.dead .dot{background:var(--bad);animation:none}
@keyframes pulse{0%{box-shadow:0 0 0 0 rgba(26,127,55,.55)}70%{box-shadow:0 0 0 9px rgba(26,127,55,0)}100%{box-shadow:0 0 0 0 rgba(26,127,55,0)}}
.warn{background:rgba(210,153,34,.15);border:1px solid var(--warn);color:var(--tx);border-radius:10px;padding:10px 12px;margin:10px 0}
.warn.dead{background:rgba(248,81,73,.13);border-color:var(--bad)}
h1{font-size:15px;font-weight:600;color:var(--mut);margin:14px 0 2px}
.hero{background:var(--card);border:1px solid var(--ln);border-radius:14px;padding:16px 18px;margin:8px 0}
.hero .big{font-size:23px;font-weight:700;line-height:1.3}.hero .sub{color:var(--mut);margin-top:2px}
.eta{margin-top:10px;font-size:19px;font-weight:600}.eta small{display:block;font-size:14px;font-weight:400;color:var(--mut)}
.bar{height:16px;background:var(--ln);border-radius:9px;overflow:hidden;margin:12px 0 4px;position:relative}
.bar i{display:block;height:100%;background:var(--ok)}
.ready .bar i{transition:width .8s}
.bar.go i{background:repeating-linear-gradient(135deg,var(--ok) 0 14px,#2ea44f 14px 28px);background-size:40px 40px;animation:move 1s linear infinite}
@keyframes move{to{background-position:40px 0}}
.muted{color:var(--mut);font-size:14px}.card{background:var(--card);border:1px solid var(--ln);border-radius:14px;padding:14px 16px;margin:14px 0}
.card>b{display:block;margin-bottom:6px}.how{font-size:14px;color:var(--mut)}
.stage{display:flex;gap:12px;padding:10px 0;border-top:1px solid var(--ln)}.stage:first-of-type{border-top:0}
.num{flex:none;width:28px;height:28px;border-radius:50%;display:grid;place-items:center;font-weight:700;color:#fff;background:var(--todo)}
.stage.done .num{background:var(--ok)}.stage.run .num{background:var(--run);animation:pulse2 1.4s infinite}.stage.fail .num{background:var(--bad)}
@keyframes pulse2{50%{opacity:.55}}
.stage .t{font-weight:600}.stage .p{color:var(--mut);font-size:14px}.stage .n{color:var(--run);font-size:14px;font-weight:600}
.stage.todo .t{color:var(--todo)}
.job{border-top:1px solid var(--ln);padding:8px 0}.job:first-of-type{border-top:0}
.job summary{cursor:pointer;display:flex;gap:8px;align-items:baseline;flex-wrap:wrap;list-style:none}
.job summary::-webkit-details-marker{display:none}
.chip{font-size:12px;border-radius:99px;padding:1px 9px;color:#fff;background:var(--todo)}.chip.done{background:var(--ok)}.chip.run{background:var(--run)}.chip.fail{background:var(--bad)}
.mini{height:6px;background:var(--ln);border-radius:4px;overflow:hidden;margin:6px 0}.mini i{display:block;height:100%;background:var(--run)}.mini.done i{background:var(--ok)}
ul{list-style:none;margin:6px 0 2px;padding:0}li{display:flex;gap:8px;padding:2px 0;align-items:baseline}
li .ic{width:1.2em;text-align:center;font-weight:700;flex:none}li.done .ic{color:var(--ok)}li.run .ic{color:var(--run)}li.fail .ic{color:var(--bad)}li.todo{color:var(--todo)}li.run{font-weight:600}
small{color:var(--mut);font-weight:400}pre{white-space:pre-wrap;color:var(--bad);font-size:12px;margin:2px 0}
.err{color:var(--bad)}details>summary{cursor:pointer}
</style></head><body><main>
<div class="live" id="live"><span class="dot"></span><span id="livetxt">טוען…</span></div>
<div id="stale"></div>
<h1 id="title"></h1>
<div class="hero"><div class="big" id="hero">…</div><div class="sub" id="sub"></div>
<div class="eta" id="eta"></div>
<div class="bar" id="bar"><i id="barfill" style="width:0"></i></div><div class="muted" id="pct"></div></div>
<div class="card"><b>איך נראית גרסה חדשה — בקיצור</b><div class="how">בודקים שהכול עובד ← שומרים ← מעלים לאינטרנט ← GitHub בונה את קובצי ההתקנה ← הגרסה מתפרסמת. הדף הזה מראה איפה אנחנו, ובכל שלב מה בדיוק קורה. אין צורך לעשות כלום.</div></div>
<div class="card"><b>השלבים</b><div id="stages"></div></div>
<div class="card"><b>בדיקות במחשב שלך</b><div class="how">כל בדיקה פותחת עותק זמני של התוכנה, מנסה אותה ומוודאת שהיא עונה נכון. שום דבר אמיתי שלך לא נוגעים בו.</div><ul id="local"></ul></div>
<div class="card"><b>מה GitHub עושה עכשיו</b><div class="how">GitHub מריץ הרבה עבודות במקביל על מחשבים שלו. לחצו על עבודה כדי לראות את השלבים שלה.</div><div id="jobs"></div></div>
<div class="card muted" id="recent"></div>
</main><script src="status-data.js"></script><script>
const $ = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const IC = {done:'✓', run:'●', todo:'○', fail:'✕'}, CHIP = {done:'הסתיים', run:'רץ עכשיו', todo:'ממתין', fail:'נכשל'};
const mmss = s => { s = Math.max(0, Math.round(s)); return Math.floor(s/60) + ':' + String(s%60).padStart(2,'0'); };
let D = null, loadedAt = 0;
// Only what really changed is touched: a part whose HTML is the same as last time is left alone, so nothing flickers, restarts its
// animation, loses the scroll position or closes a job the reader opened.
const setHtml = (el, html) => { if(el._h !== html){ el._h = html; el.innerHTML = html; } };
const setText = (el, t) => { if(el.textContent !== t) el.textContent = t; };
const opened = {};                                   // which jobs the reader opened or closed by hand
document.addEventListener('toggle', e => { const d = e.target; if(d.dataset && d.dataset.job) opened[d.dataset.job] = d.open; }, true);
function render(){
  if(!D) return;
  setText($('title'), 'שחרור גרסה ' + D.target.replace('v','') + ' של photag');
  setText($('hero'), D.hero); setText($('sub'), D.sub || '');
  if($('barfill').style.width !== D.percent + '%') $('barfill').style.width = D.percent + '%';
  setText($('pct'), D.percent + '% מהדרך');
  $('bar').classList.toggle('go', !D.published && !D.failed);
  setHtml($('stages'), D.stages.map((s,i) => `<div class="stage ${s.state}"><div class="num">${s.state==='done'?'✓':s.state==='fail'?'✕':i+1}</div><div><div class="t">${esc(s.name)}</div><div class="p">${esc(s.plain)}</div>${s.note?`<div class="n">${esc(s.note)}</div>`:''}</div></div>`).join(''));
  setHtml($('local'), D.local.length ? D.local.map(t => {
    const run = t.state==='run', note = run ? `<small data-began="${t.began}" data-est="${t.estimate}"></small>` : (t.state==='todo' ? `<small>בערך ${Math.round(t.estimate)} שנ׳</small>` : `<small>${esc(t.summary)}${t.seconds?` · ${Math.round(t.seconds)} שנ׳`:''}</small>`);
    return `<li class="${t.state}"><span class="ic">${IC[t.state]}</span><span>${esc(t.name)} ${note}${t.detail?`<pre>${esc(t.detail)}</pre>`:''}</span></li>`; }).join('')
    : '<li class="todo"><span class="ic">○</span><span>עוד לא הורצו בדיקות במחשב (tools/release_status/run_local_tests.py)</span></li>');
  const box = $('jobs');
  if(!D.jobs.length){ setHtml(box, '<div class="muted">עוד לא התחיל. אחרי ההעלאה והפעלת השחרור יופיעו כאן העבודות של GitHub.</div>'); }
  else {
    if(box._h !== 'jobs'){ box.innerHTML = ''; box._h = 'jobs'; }
    D.jobs.forEach((j, i) => {                       // one block per job, each updated on its own
      let el = box.children[i];
      if(!el){ el = document.createElement('div'); el.className = 'job'; box.appendChild(el); }
      const open = j.name in opened ? opened[j.name] : j.state === 'run';
      setHtml(el, `<details data-job="${esc(j.name)}" ${open?'open':''}><summary><span class="chip ${j.state}">${CHIP[j.state]}</span><b>${esc(j.name)}</b><small>${j.done}/${j.total}${j.state==='run'&&j.began?` · <span data-began="${j.began}"></span>`:''}</small></summary>
        <div class="muted">${esc(j.desc)}${j.now?` — <b style="color:var(--run)">עכשיו: ${esc(j.now)}</b>`:''}</div>
        <div class="mini ${j.state}"><i style="width:${j.total?Math.round(100*j.done/j.total):0}%"></i></div>
        <ul>${j.steps.map(s => `<li class="${s.state}"><span class="ic">${IC[s.state]}</span><span>${esc(s.text)} ${s.small?`<small>${esc(s.small)}</small>`:''}</span></li>`).join('')}</ul></details>`);
    });
    while(box.children.length > D.jobs.length) box.lastChild.remove();
  }
  setHtml($('recent'), 'גרסאות שפורסמו לאחרונה: ' + esc((D.recent||[]).join(' · ')) + (D.error ? `<div class="err">${esc(D.error)}</div>` : ''));
}
function tick(){
  const now = Date.now()/1000;
  if(!D) return;
  const age = now - D.at, live = $('live');
  const cls = 'live' + (age > 60 ? ' dead' : age > 15 ? ' stale' : '');
  if(live.className !== cls) live.className = cls;
  setText($('livetxt'), age < 8 ? 'חי · מתעדכן' : 'עודכן לפני ' + Math.round(age) + ' שניות');
  setHtml($('stale'), age > 60 ? '<div class="warn dead"><b>הנתונים לא מתעדכנים כבר ' + mmss(age) + ' דקות.</b> כנראה התוכנית שמעדכנת את הדף נעצרה, ולכן מה שמוצג כאן ישן. הפעילו אותה מחדש: <code>py -3.12 tools/release_status/status_page.py</code> או בקשו ממני.</div>'
    : age > 15 ? '<div class="warn"><b>הנתונים לא התעדכנו ' + Math.round(age) + ' שניות.</b> הדף חי ומחכה לעדכון הבא; אם זה נמשך — התוכנית שמעדכנת אותו אולי נתקעה.</div>' : '');
  if(D.eta_end){
    const left = D.eta_end - now;
    setHtml($('eta'), (left > 0 ? 'עוד בערך <b>' + mmss(left) + '</b> דקות' : 'מתארך קצת יותר מהצפוי… עוד רגע')
      + `<small>בדיקות במחשב: ~${mmss(D.local_left)} · בנייה ב-GitHub: ~${mmss(D.gh_left)} (לפי שחרורים קודמים, בדרך כלל ~${mmss(D.typical)})</small>`);
  } else setText($('eta'), D.published ? 'סיימנו!' : '');
  document.querySelectorAll('[data-began][data-est]').forEach(e => setText(e, 'רץ ' + Math.round(now - e.dataset.began) + ' שנ׳ מתוך ~' + Math.round(e.dataset.est)));
  document.querySelectorAll('span[data-began]:not([data-est])').forEach(e => setText(e, 'רץ ' + mmss(now - e.dataset.began)));
}
function load(){
  const s = document.createElement('script'); s.src = 'status-data.js?t=' + Date.now();
  s.onload = () => { if(window.STATUS && (!D || window.STATUS.at !== D.at)){ D = window.STATUS; render(); } s.remove(); };
  s.onerror = () => s.remove(); document.head.appendChild(s);
}
D = window.STATUS || null; render(); tick();
requestAnimationFrame(() => requestAnimationFrame(() => document.body.classList.add('ready')));
setInterval(load, 2000); setInterval(tick, 1000);
</script></body></html>"""


def write(data):
    tmp = DATA.with_suffix(".tmp")
    tmp.write_text("window.STATUS = " + json.dumps(data, ensure_ascii=False) + ";\n", "utf-8")
    for _ in range(5):
        try:
            os.replace(tmp, DATA)
            return
        except PermissionError:               # the browser is reading it this very moment
            time.sleep(0.2)


def main():
    PAGE.write_text(TEMPLATE, "utf-8")
    if "--once" in sys.argv:
        write(collect())
        print(PAGE)
        return
    end = time.time() + 3 * 3600
    while time.time() < end:
        try:
            d = collect()
            write(d)
            if d["published"]:
                time.sleep(10)
                write(collect())
                break
        except Exception as e:
            print("error:", e, flush=True)
        time.sleep(LOCAL_EVERY)


if __name__ == "__main__":
    main()
