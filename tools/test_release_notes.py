"""Test: release notes are grouped as Features / Fixes / Small fixes (CLAUDE.md), the update dialog's notes merge by group, and the
notes file of the current version follows the structure (from 12.1.0 on)."""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app import updater  # noqa: E402
from app.version import __version__  # noqa: E402

res = []


def check(n, ok, extra=""):
    res.append(bool(ok)); print(("PASS " if ok else "FAIL ") + n + (f"  [{extra}]" if extra else ""))


sec, rest = updater.split_notes("# photag 1\n\nintro\n\n## Features\n- a\n\n## Fixes\n- b\n\n## Other\n- x\n\n## Small fixes\n- c")
check("split: the three sections, a title, and another heading ends a section", sec == {"Features": "- a", "Fixes": "- b", "Small fixes": "- c"} and rest.startswith("# photag 1"), (sec, rest))
check("an old-style body has no section", updater.split_notes("- just a list\n- of things")[0] == {})
m = updater.merge_notes([("v3", "## Fixes\n- f3\n\n## Small fixes\n- s3"), ("v2", "## Features\n- g2\n\n## Fixes\n- f2"), ("v1", "- legacy")])
check("merge: features first, then fixes (newest first), then small fixes, old notes last under their version",
      m.index("## Features") < m.index("## Fixes") < m.index("## Small fixes") < m.index("## v1") and m.index("- f3") < m.index("- f2") and "- legacy" in m, m)
check("merge: nothing in a section means no heading", "## Features" not in updater.merge_notes([("v3", "## Fixes\n- f3")]))


def ver(v):
    return tuple(int(x) for x in re.findall(r"\d+", v.split("-")[0])[:3])


f = ROOT / "docs" / f"release-notes-v{__version__}.md"
check("the notes file of this version exists", f.is_file(), str(f))
if f.is_file() and ver(__version__) >= (12, 1, 0):
    text = f.read_text("utf-8")
    heads = re.findall(r"^## (.*?)\s*$", text, re.M)
    order = [updater.SECTIONS.index(h) for h in heads if h in updater.SECTIONS]
    check("only the standard sections", heads and all(h in updater.SECTIONS for h in heads), heads)
    check("in the order Features, Fixes, Small fixes", order == sorted(order) and len(set(order)) == len(order), heads)
    check("starts with the title line", text.lstrip().startswith(f"# photag {__version__}"))
    sec, _ = updater.split_notes(text)
    check("no empty section", len(sec) == len(heads), (list(sec), heads))
n = res.count(False); print(f"\n{len(res) - n}/{len(res)} passed"); sys.exit(1 if n else 0)
