"""Regenerate the Python-package table in THIRD_PARTY_NOTICES.md from the installed packages.

    python tools/gen_notices.py

Walks the dependency closure of requirements.txt, reads each package's name, version and license from its
metadata, and rewrites the block between <!-- deps:begin --> and <!-- deps:end -->. Run it in the environment
the EXE is built from, and re-read the result: license metadata is sometimes missing or vague.
"""
from importlib import metadata as md
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parent.parent
OVERRIDES = {   # packages whose metadata has no usable license field (checked by hand against their repositories)
    "insightface": "MIT (code). The downloaded model weights are NOT MIT: non-commercial / research use only, see above",
    "clr-loader": "MIT",
}


def _license(dist) -> str:
    m = dist.metadata
    if m.get("License-Expression"):
        return m["License-Expression"]
    cls = [c.split("::")[-1].strip() for c in m.get_all("Classifier") or [] if c.startswith("License ::")]
    cls = [c for c in cls if c != "OSI Approved"]
    raw = (m.get("License") or "").strip().splitlines()
    raw = raw[0] if raw and len(raw[0]) < 80 else ""
    return "; ".join(dict.fromkeys(cls)) or raw or "unknown"


def closure() -> list[dict]:
    roots = [l.strip() for l in (ROOT / "requirements.txt").read_text("utf-8").splitlines() if l.strip() and not l.startswith("#")]
    seen: dict[str, dict | None] = {}

    def walk(name: str):
        key = canonicalize_name(name)
        if key in seen:
            return
        try:
            d = md.distribution(name)
        except md.PackageNotFoundError:
            seen[key] = None
            return
        seen[key] = {"name": d.metadata["Name"], "version": d.version, "license": OVERRIDES.get(key) or _license(d),
                     "url": d.metadata.get("Home-page") or ""}
        for r in d.requires or []:
            try:
                req = Requirement(r)
            except Exception:
                continue
            if req.marker and not req.marker.evaluate({"extra": ""}):
                continue
            walk(req.name)

    for r in roots:
        walk(Requirement(r).name)
    return sorted((v for v in seen.values() if v), key=lambda x: x["name"].lower())


def main():
    rows = closure()
    table = "| Package | Version | License |\n|---|---|---|\n" + "\n".join(f"| {r['name']} | {r['version']} | {r['license']} |" for r in rows)
    path = ROOT / "THIRD_PARTY_NOTICES.md"
    text = path.read_text("utf-8")
    a, b = text.index("<!-- deps:begin -->"), text.index("<!-- deps:end -->")
    path.write_text(text[:a] + "<!-- deps:begin -->\n" + table + "\n" + text[b:], "utf-8")
    print(f"{len(rows)} packages written to {path.name}")


if __name__ == "__main__":
    main()
