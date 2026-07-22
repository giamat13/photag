"""Optional smart tags for search, via a local Ollama vision model.
If Ollama isn't running the whole feature is skipped — nothing else depends on
it. We only tag *content* (scene/objects); people come from Google Takeout."""
import base64
import json
import urllib.error
import urllib.request

from . import db, images
from .config import OLLAMA_URL, OLLAMA_VISION_MODEL

PROMPT = ("תאר את התמונה בעד 8 תגיות קצרות בעברית שיעזרו לחפש אותה מאוחר יותר "
          "(אובייקטים, מקום, סוג אירוע, אווירה). החזר רק את התגיות מופרדות בפסיקים, בלי משפטים.")


def available() -> bool:
    """Is the Ollama daemon itself reachable (regardless of which models it has)."""
    try:
        with urllib.request.urlopen(f"{OLLAMA_URL}/api/tags", timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def pick_vision_model() -> str | None:
    """Pick a vision-capable model from what's actually installed. The
    configured OLLAMA_VISION_MODEL wins if it's present and vision-capable;
    otherwise we fall back to whatever vision model IS installed, since
    hardcoding one name (e.g. 'llava') breaks for anyone who pulled a
    different one (e.g. qwen2.5vl)."""
    try:
        with urllib.request.urlopen(f"{OLLAMA_URL}/api/tags", timeout=3) as r:
            models = json.loads(r.read()).get("models", [])
    except Exception:
        return None
    vision = [m["name"] for m in models if "vision" in (m.get("capabilities") or [])]
    if not vision:
        return None
    for name in vision:
        if name == OLLAMA_VISION_MODEL or name.split(":")[0] == OLLAMA_VISION_MODEL:
            return name
    return vision[0]


def _tag_image(sha: str, model: str) -> list[str]:
    tp = images.thumb_path(sha)
    if not tp.exists():
        return []
    b64 = base64.b64encode(tp.read_bytes()).decode()
    body = json.dumps({"model": model, "prompt": PROMPT,
                       "images": [b64], "stream": False}).encode()
    req = urllib.request.Request(f"{OLLAMA_URL}/api/generate", body,
                                 {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        txt = json.loads(r.read()).get("response", "")
    tags = [t.strip(" .\n\t#") for t in txt.replace("\n", ",").split(",")]
    return [t for t in tags if 1 < len(t) <= 30][:8]


def run_tagging(progress):
    con = db.connect()
    model = pick_vision_model()
    if not model:
        progress.state = "error"
        progress.error = (f"אין מודל ראייה מותקן ב-Ollama ({OLLAMA_URL}). "
                          f"הריצו בטרמינל: ollama pull {OLLAMA_VISION_MODEL}")
        return
    todo = con.execute("SELECT id, sha256 FROM photos WHERE tags_done=0 AND is_video=0 AND trashed=0").fetchall()
    progress.state = "tagging"; progress.total = len(todo); progress.done = 0; progress.msg = f"מודל: {model}"
    fails = 0
    for i, row in enumerate(todo, 1):
        try:
            for t in _tag_image(row["sha256"], model):
                con.execute("INSERT OR IGNORE INTO tags(name) VALUES(?)", (t,))
                tid = con.execute("SELECT id FROM tags WHERE name=?", (t,)).fetchone()["id"]
                con.execute("INSERT OR IGNORE INTO photo_tags(photo_id,tag_id,source) VALUES(?,?, 'ollama')",
                            (row["id"], tid))
            con.execute("UPDATE photos SET tags_done=1 WHERE id=?", (row["id"],))
        except urllib.error.HTTPError as e:
            # a bad model name / request fails the same way for every photo -> stop early, don't burn the whole library
            fails += 1
            if fails >= 3:
                progress.state = "error"
                progress.error = f"Ollama החזיר שגיאה ({e.code}) עבור מודל '{model}'. ודאו שהמודל מותקן ותקין."
                con.commit()
                return
        except Exception:
            fails += 1  # per-photo hiccup (bad thumb, timeout) -> skip and keep going
        progress.done = i
        if i % 10 == 0:
            progress.msg = f"מתייג ({model}) {i}/{len(todo)}"; con.commit()
    con.commit()
    progress.state = "done"
    progress.msg = f"התיוג הושלם ({model})" + (f" — {fails} תמונות נכשלו" if fails else "")
