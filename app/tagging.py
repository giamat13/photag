"""Optional smart tags for search, via a local Ollama vision model.
If Ollama isn't running the whole feature is skipped — nothing else depends on
it. We only tag *content* (scene/objects); people come from Google Takeout."""
import base64
import ctypes
import json
import os
import subprocess
import urllib.error
import urllib.request

from . import db, images
from .config import OLLAMA_URL, OLLAMA_VISION_MODEL

PROMPT = ("תאר את התמונה בעד 8 תגיות קצרות בעברית ובעד 8 תגיות קצרות באנגלית שיעזרו לחפש אותה מאוחר יותר "
          "(אובייקטים, מקום, סוג אירוע, אווירה). החזר בדיוק בפורמט הבא, בלי משפטים נוספים:\n"
          "עברית: תגית1, תגית2, ...\n"
          "English: tag1, tag2, ...")

RECOMMENDED_SMALL_VISION_MODEL = "moondream"  # ~1.7GB, runs on almost anything


def available() -> bool:
    """Is the Ollama daemon itself reachable (regardless of which models it has)."""
    try:
        with urllib.request.urlopen(f"{OLLAMA_URL}/api/tags", timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def _ram_bytes() -> int:
    """Currently AVAILABLE physical RAM (not total) -> reflects what other apps
    (browser, this app itself, OS) are already using, not just the machine's
    spec sheet. Windows-only app -> GlobalMemoryStatusEx via ctypes; falls back
    to a conservative guess if that ever fails (e.g. dev on non-Windows)."""
    try:
        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_uint32), ("dwMemoryLoad", ctypes.c_uint32),
                        ("ullTotalPhys", ctypes.c_uint64), ("ullAvailPhys", ctypes.c_uint64),
                        ("ullTotalPageFile", ctypes.c_uint64), ("ullAvailPageFile", ctypes.c_uint64),
                        ("ullTotalVirtual", ctypes.c_uint64), ("ullAvailVirtual", ctypes.c_uint64),
                        ("ullAvailExtendedVirtual", ctypes.c_uint64)]
        stat = MEMORYSTATUSEX(dwLength=ctypes.sizeof(MEMORYSTATUSEX))  # type: ignore[call-arg]
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))  # type: ignore[attr-defined]
        return stat.ullAvailPhys
    except Exception:
        return 4 * 1024**3


def _gpu_vram_bytes() -> int:
    """Best-effort NVIDIA VRAM check via nvidia-smi (already on PATH if a GPU is
    installed). 0 if there's no NVIDIA GPU or nvidia-smi isn't available -> CPU-only."""
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=3)
        return int(out.stdout.strip().splitlines()[0]) * 1024 * 1024
    except Exception:
        return 0


MODEL_RAM_OVERHEAD = 1.6  # a loaded model needs weights + KV-cache/context + runtime, not just its file size


def _model_size_budget() -> int:
    """How large a model file (bytes) this machine can realistically run right
    now, leaving headroom for everything else already using RAM. A GPU with
    enough VRAM to hold the model makes it fast regardless of size; without
    one, inference runs on CPU/RAM and large models get impractically slow, so
    cap CPU-only picks by core count as well as available RAM."""
    vram = _gpu_vram_bytes()
    if vram:
        return int(vram / MODEL_RAM_OVERHEAD)
    ram = _ram_bytes()
    cores = os.cpu_count() or 4
    cpu_cap = 3 * 1024**3 if cores <= 4 else 6 * 1024**3
    return min(int(ram / MODEL_RAM_OVERHEAD), cpu_cap)


def _choose_model(vision: list[dict], budget: int) -> str:
    """Pure decision, no I/O -> unit-testable without a live Ollama/GPU/RAM.
    Preferred OLLAMA_VISION_MODEL wins if it's in the list and fits the budget;
    otherwise the largest model that fits (best quality this hardware can
    handle); if nothing fits, the smallest overall (least-bad best effort)."""
    fitting = [m for m in vision if m.get("size", 0) <= budget]
    pool = fitting or vision
    for m in pool:
        if m["name"] == OLLAMA_VISION_MODEL or m["name"].split(":")[0] == OLLAMA_VISION_MODEL:
            return m["name"]
    if fitting:
        return max(fitting, key=lambda m: m.get("size", 0))["name"]
    return min(vision, key=lambda m: m.get("size", 0))["name"]


def _fetch_vision_models() -> list[dict]:
    try:
        with urllib.request.urlopen(f"{OLLAMA_URL}/api/tags", timeout=3) as r:
            models = json.loads(r.read()).get("models", [])
    except Exception:
        return []
    return [m for m in models if "vision" in (m.get("capabilities") or [])]


def pick_vision_model() -> str | None:
    """Pick a vision-capable model from what's actually installed, sized to fit
    this machine's RAM/GPU/CPU (see _model_size_budget / _choose_model)."""
    vision = _fetch_vision_models()
    return _choose_model(vision, _model_size_budget()) if vision else None


def pick_vision_model_info() -> tuple[str | None, bool]:
    """(model_name, fits_budget). model_name is None if nothing vision-capable
    is installed (fits_budget is meaningless/True in that case)."""
    vision = _fetch_vision_models()
    if not vision:
        return None, True
    budget = _model_size_budget()
    model = _choose_model(vision, budget)
    size = next((m.get("size", 0) for m in vision if m["name"] == model), 0)
    return model, size <= budget


def _split_tags(s: str) -> list[str]:
    tags = [t.strip(" .\n\t#") for t in s.replace("\n", ",").split(",")]
    return [t for t in tags if 1 < len(t) <= 30][:8]


def _tag_image(sha: str, model: str) -> dict[str, list[str]]:
    tp = images.thumb_path(sha)
    if not tp.exists():
        return {"he": [], "en": []}
    b64 = base64.b64encode(tp.read_bytes()).decode()
    # small context/output window: one image + a short tag list, not a chat ->
    # the model's default context (often much larger) would burn RAM for nothing.
    body = json.dumps({"model": model, "prompt": PROMPT, "images": [b64], "stream": False,
                       "options": {"num_ctx": 2048, "num_predict": 200}}).encode()
    req = urllib.request.Request(f"{OLLAMA_URL}/api/generate", body,
                                 {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        txt = json.loads(r.read()).get("response", "")
    he = en = ""
    for line in txt.splitlines():
        low = line.strip().lower()
        if low.startswith("עברית") or low.startswith("hebrew"):
            he = line.split(":", 1)[-1]
        elif low.startswith("english") or low.startswith("אנגלית"):
            en = line.split(":", 1)[-1]
    if not he and not en:
        he = txt  # model ignored the format -> fall back to treating it all as Hebrew tags
    return {"he": _split_tags(he), "en": _split_tags(en)}


SEVERE_RAM_OVERSHOOT = 2.0  # model needs >2x the safe budget -> refuse rather than likely OOM mid-run


def run_tagging(progress):
    con = db.connect()
    vision = _fetch_vision_models()
    if not vision:
        progress.state = "error"
        progress.error = (f"אין מודל ראייה מותקן ב-Ollama ({OLLAMA_URL}). "
                          f"הריצו בטרמינל: ollama pull {OLLAMA_VISION_MODEL}")
        return
    budget = _model_size_budget()
    model = _choose_model(vision, budget)
    size = next((m.get("size", 0) for m in vision if m["name"] == model), 0)
    if size > budget * SEVERE_RAM_OVERSHOOT:
        progress.state = "error"
        progress.error = (f"אין מספיק זיכרון פנוי כרגע להריץ את {model} "
                          f"({size/1024**3:.1f}GB דרושים, {budget/1024**3:.1f}GB פנויים) — "
                          f"סגרו תוכנות אחרות או התקינו מודל ראייה קטן יותר "
                          f"({RECOMMENDED_SMALL_VISION_MODEL}) בהגדרות ונסו שוב.")
        return
    warn = ("" if size <= budget else
            f" ⚠ המודל גדול על הזיכרון הפנוי כרגע — סגרו תוכנות אחרות "
            f"או התקינו מודל ראייה קטן יותר ({RECOMMENDED_SMALL_VISION_MODEL}) בהגדרות")
    todo = con.execute(
        "SELECT id, sha256 FROM photos "
        "WHERE (tags_done=0 OR tags_en_done=0) AND is_video=0 AND trashed=0").fetchall()
    if not todo:
        progress.state = "done"; progress.total = 0; progress.done = 0
        progress.msg = "כל התמונות כבר מתויגות (עברית ואנגלית) ✓"
        return
    progress.state = "tagging"; progress.total = len(todo); progress.done = 0
    progress.msg = f"מודל: {model}{warn}"
    fails = 0
    for i, row in enumerate(todo, 1):
        try:
            result = _tag_image(row["sha256"], model)
            for tag_list, source in ((result["he"], "ollama"), (result["en"], "ollama-en")):
                for t in tag_list:
                    con.execute("INSERT OR IGNORE INTO tags(name) VALUES(?)", (t,))
                    tid = con.execute("SELECT id FROM tags WHERE name=?", (t,)).fetchone()["id"]
                    con.execute("INSERT OR IGNORE INTO photo_tags(photo_id,tag_id,source) VALUES(?,?,?)",
                                (row["id"], tid, source))
            con.execute("UPDATE photos SET tags_done=1, tags_en_done=1 WHERE id=?", (row["id"],))
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
            progress.msg = f"מתייג ({model}) {i}/{len(todo)}{warn}"; con.commit()
    con.commit()
    progress.state = "done"
    progress.msg = f"התיוג הושלם ({model})" + (f" — {fails} תמונות נכשלו" if fails else "")


def pull_model(name: str, progress):
    """Stream `ollama pull <name>` progress via Ollama's own /api/pull, so a
    user whose current vision model doesn't fit their RAM can install a
    smaller one from Settings instead of a terminal."""
    progress.state = "pulling"; progress.msg = f"מוריד {name}…"; progress.done = 0; progress.total = 0
    body = json.dumps({"name": name, "stream": True}).encode()
    req = urllib.request.Request(f"{OLLAMA_URL}/api/pull", body, {"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=600) as r:
            for line in r:
                if not line.strip():
                    continue
                obj = json.loads(line)
                if obj.get("error"):
                    progress.state = "error"; progress.error = obj["error"]
                    return
                if "total" in obj and "completed" in obj:
                    progress.total = obj["total"]; progress.done = obj["completed"]
                progress.msg = obj.get("status", progress.msg)
        progress.state = "done"; progress.msg = f"{name} הותקן ✓"
    except Exception as e:
        progress.state = "error"; progress.error = f"הורדת המודל נכשלה: {e}"


if __name__ == "__main__":
    # ponytail check: exercise _choose_model against synthetic hardware/model
    # scenarios, not just whatever happens to be installed on one real machine.
    GB = 1024**3

    def _m(name, size_gb):
        return {"name": name, "size": int(size_gb * GB)}

    models = [_m("tiny:1b", 1), _m("mid:7b", 6), _m("big:34b", 20)]

    assert _choose_model(models, budget=24 * GB) == "big:34b", "plenty of budget -> best model"
    assert _choose_model(models, budget=8 * GB) == "mid:7b", "34b too big -> largest that fits"
    assert _choose_model(models, budget=int(0.5 * GB)) == "tiny:1b", "nothing fits -> smallest, least-bad bet"

    OLLAMA_VISION_MODEL = "mid"  # reassigns this module's global -> _choose_model sees it
    assert _choose_model(models, budget=24 * GB) == "mid:7b", "preferred model wins even over a bigger option"
    OLLAMA_VISION_MODEL = "big"
    assert _choose_model(models, budget=8 * GB) == "mid:7b", "preferred model ignored if it doesn't fit budget"

    print("tagging._choose_model: all checks passed")
