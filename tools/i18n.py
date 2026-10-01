"""photag translations: extract UI strings and check locale files.

    python tools/i18n.py extract   # rebuild app/ui/locales/_keys.json from the source
    python tools/i18n.py check     # verify every locales/<code>.json against it

The UI is written in Hebrew; every string shown to the user is a key, either
wrapped in t('...') in app.js, static text in index.html, or a key passed to
say()/say_parts()/fail()/err() in the backend. A locale file maps each key to
its translation and must keep {placeholders} and HTML entities intact.
"""
import json
import re
import sys
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UI = ROOT / "app" / "ui"
LOCALES = UI / "locales"
KEYS = LOCALES / "_keys.json"
HEB = re.compile(r"[֐-׿]")


def _js_keys() -> set[str]:
    """First argument of every t('...') / t("...") call in app.js."""
    src = (UI / "app.js").read_text("utf-8")
    keys = set()
    for m in re.finditer(r"""\bt\(\s*(["'])((?:\\.|(?!\1).)*)\1""", src):
        quote, body = m.group(1), m.group(2)
        keys.add(json.loads('"' + body.replace('\\' + quote, quote).replace('"', '\\"').replace('\\\\"', '\\"') + '"')
                 if quote == "'" else json.loads('"' + body + '"'))
    return keys


class _Static(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.keys = set()

    def handle_starttag(self, tag, attrs):
        for k, v in attrs:
            if k in ("title", "placeholder", "aria-label") and v and HEB.search(v):
                self.keys.add(v.strip())

    def handle_data(self, data):
        if HEB.search(data):
            self.keys.add(data.strip())


def _html_keys() -> set[str]:
    p = _Static()
    p.feed((UI / "index.html").read_text("utf-8"))
    return p.keys


def _py_keys() -> set[str]:
    keys = set()
    pat = re.compile(r"""(?:\.say|\.fail|\berr\(\d+,)\s*\(?\s*"([^"]*)"|\(\s*"([^"]*[֐-׿][^"]*)",\s*\{""")
    for f in ("server.py", "importer.py", "faces.py", "aitag.py", "compress.py", "updater.py"):
        for m in pat.finditer((ROOT / "app" / f).read_text("utf-8")):
            k = m.group(1) or m.group(2)
            if k and (HEB.search(k) or "{" in k):
                keys.add(k)
    return keys


def extract():
    keys = sorted(_js_keys() | _html_keys() | _py_keys())
    KEYS.parent.mkdir(exist_ok=True)
    KEYS.write_text(json.dumps(keys, ensure_ascii=False, indent=1) + "\n", "utf-8")
    print(f"{len(keys)} keys -> {KEYS.relative_to(ROOT)}")


def _tokens(s: str):
    return sorted(re.findall(r"\{\w+\}", s)), sorted(re.findall(r"&\w+;", s))


def check() -> bool:
    keys = json.loads(KEYS.read_text("utf-8"))
    ok = True
    for f in sorted(LOCALES.glob("*.json")):
        if f.name.startswith("_"):
            continue
        d = json.loads(f.read_text("utf-8"))
        missing = [k for k in keys if not isinstance(d.get(k), str) or not d[k].strip()]
        extra = [k for k in d if k not in keys]
        bad = [k for k in keys if k in d and isinstance(d[k], str) and _tokens(k) != _tokens(d[k])]
        hebrew = [k for k in keys if isinstance(d.get(k), str) and HEB.search(d[k])]
        status = "OK" if not (missing or bad or hebrew) else "PROBLEMS"
        ok &= status == "OK"
        print(f"{f.name:8s} {status}: {len(d)} entries, {len(missing)} missing, {len(bad)} placeholder mismatches, "
              f"{len(hebrew)} still Hebrew, {len(extra)} unused")
        for k in (missing + bad + hebrew)[:8]:
            print("    ", k[:90])
    return ok


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "check"
    if cmd == "extract":
        extract()
    else:
        sys.exit(0 if check() else 1)
