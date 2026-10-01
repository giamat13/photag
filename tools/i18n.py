"""photag translations: extract UI strings and check locale files.

    python tools/i18n.py extract   # rebuild app/ui/locales/_keys.json from the source
    python tools/i18n.py check     # verify every locales/<code>.json against it

English is the base language: every string shown to the user is an English key, either wrapped in t('...') in
app.js, static text in index.html, or a key passed to say()/say_parts()/fail()/err() in the backend. English needs
no file; every other language (Hebrew included) has app/ui/locales/<code>.json mapping each key to its translation,
keeping {placeholders} and HTML entities intact.
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
LETTERS = re.compile(r"[A-Za-z]{2,}")
# static text in index.html that is a name, not a sentence to translate
STATIC_IGNORE = {"photag"}
BACKEND_FILES = ("server.py", "importer.py", "faces.py", "aitag.py", "compress.py", "updater.py", "backup.py")


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
    """Text nodes and title / placeholder / aria-label attributes of index.html (not scripts, styles or icons)."""
    SKIP = {"script", "style", "svg", "symbol", "title"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.keys = set()
        self.stack = []

    def _add(self, s):
        s = s.strip()
        if s and LETTERS.search(s) and s not in STATIC_IGNORE:
            self.keys.add(s)

    def handle_starttag(self, tag, attrs):
        self.stack.append(tag)
        for k, v in attrs:
            if k in ("title", "placeholder", "aria-label") and v:
                self._add(v)

    def handle_endtag(self, tag):
        while self.stack and self.stack.pop() != tag:
            pass

    def handle_data(self, data):
        if not self.SKIP & set(self.stack):
            self._add(data)


def _html_keys() -> set[str]:
    p = _Static()
    p.feed((UI / "index.html").read_text("utf-8"))
    return p.keys


def _py_keys() -> set[str]:
    keys = set()
    pat = re.compile(r'''(?:\.say|\.fail|err\(\d+,)\s*\(?\s*"([^"]*)"|\(\s*"([^"]*[A-Za-z][^"]* [^"]*)",\s*\{|error_key=\s*"([^"]*)"|"error_key":\s*"([^"]*)"''')
    for f in BACKEND_FILES:
        for m in pat.finditer((ROOT / "app" / f).read_text("utf-8")):
            k = m.group(1) or m.group(2) or m.group(3) or m.group(4)
            if k and (LETTERS.search(k) or "{" in k):
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
        # a translation into another language must not still be Hebrew (Hebrew itself, of course, is)
        hebrew = [] if f.stem == "he" else [k for k in keys if isinstance(d.get(k), str) and HEB.search(d[k])]
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
