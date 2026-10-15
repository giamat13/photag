"""AI keyword tagging with the user's own API key.

Providers: OpenAI, Anthropic (Claude), Google Gemini, OpenRouter (one key, hundreds of
models) and "custom" = any OpenAI-compatible endpoint (Groq, Together, Mistral, a local
Ollama / LM Studio ...). Only a small thumbnail (the library's 512 px JPEG) of each
photo is sent, and only when the user starts a run. Keys are stored encrypted
(keystore.py) in config.json and never sent back to the UI.
"""
import base64
import datetime
import json
import os
import re
import time
import urllib.error
import urllib.request

from . import config, db, images, keystore, render
from . import pool as cf        # not concurrent.futures: that pool refuses to start once the window was closed (pool.py)
from .net import ssl_context
from .config import PATHS

PROVIDERS = {
    "openai": {"base": "https://api.openai.com/v1", "kind": "openai"},
    "anthropic": {"base": "https://api.anthropic.com/v1", "kind": "anthropic"},
    "gemini": {"base": "https://generativelanguage.googleapis.com/v1beta", "kind": "gemini"},
    "openrouter": {"base": "https://openrouter.ai/api/v1", "kind": "openai"},
    "custom": {"base": "", "kind": "openai"},
}
LANG_NAMES = {"he": "Hebrew", "en": "English", "ar": "Arabic", "ru": "Russian", "es": "Spanish", "fr": "French",
              "de": "German", "it": "Italian", "pt": "Portuguese", "nl": "Dutch", "pl": "Polish", "uk": "Ukrainian",
              "tr": "Turkish", "zh": "Simplified Chinese", "ja": "Japanese", "ko": "Korean", "hi": "Hindi"}
WORKERS = 4
MAX_KEYWORDS = 12


class AIError(Exception):
    def __init__(self, status: int, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.status, self.retry_after = status, retry_after


# ---------------------------------------------------------------- settings
def get_settings() -> dict:
    c = config.get_ai()
    keys = c.get("keys", {})
    return {"provider": c.get("provider", "openai"), "model": c.get("model", ""), "language": c.get("language", "en"),
            "base_url": c.get("base_url", ""), "rank_mode": get_rank_mode(), "configured": _configured(),
            "providers": {p: {"has_key": p in keys, "hint": keys.get(p, {}).get("hint", "")} for p in PROVIDERS}}


def save_settings(provider: str, model: str, language: str, base_url: str, api_key: str | None):
    if provider not in PROVIDERS:
        raise ValueError("unknown provider")
    c = config.get_ai()
    keys = c.setdefault("keys", {})
    base_url = base_url.strip().rstrip("/")
    if provider == "custom" and base_url and not re.match(r"https?://", base_url):
        raise ValueError("base url must start with http:// or https://")
    if provider == "custom" and base_url != c.get("base_url", ""):
        keys.pop("custom", None)     # a saved key never follows a changed address
    if api_key and api_key.strip():
        k = api_key.strip()
        keys[provider] = {"v": keystore.protect(k), "hint": "…" + k[-4:]}
    c.update(provider=provider, model=model.strip(), language=language if language in LANG_NAMES else "en", base_url=base_url)
    config.set_ai(c)


def delete_key(provider: str):
    c = config.get_ai()
    c.get("keys", {}).pop(provider, None)
    config.set_ai(c)


def _key(provider: str, override: str | None = None) -> str:
    if override and override.strip():
        return override.strip()
    stored = config.get_ai().get("keys", {}).get(provider)
    return keystore.unprotect(stored["v"]) if stored else ""


def _base(provider: str, base_url: str = "") -> str:
    env = os.environ.get(f"PHOTAG_AI_BASE_{provider.upper()}")   # test hook: point a provider at a local mock
    b = env or (base_url if provider == "custom" else PROVIDERS[provider]["base"])
    return b.strip().rstrip("/")


# ---------------------------------------------------------------- HTTP
def _headers(kind: str, key: str) -> dict:
    if kind == "anthropic":
        return {"x-api-key": key, "anthropic-version": "2023-06-01"}
    if kind == "gemini":
        return {"x-goog-api-key": key}
    return {"Authorization": f"Bearer {key}"} if key else {}


def _http(method: str, url: str, headers: dict, body=None, timeout: float = 60, secret: str = ""):
    req = urllib.request.Request(url, json.dumps(body).encode() if body is not None else None, method=method,
                                 headers={"Content-Type": "application/json", **headers})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ssl_context()) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raw = e.read()[:3000].decode("utf-8", "replace")
        msg = raw
        try:
            j = json.loads(raw)
            err = j.get("error", j)
            msg = err.get("message") if isinstance(err, dict) else str(err)
        except Exception:
            pass
        ra = e.headers.get("Retry-After")
        raise AIError(e.code, (msg or raw or str(e.code))[:300].replace(secret, "***") if secret else (msg or raw)[:300],
                      float(ra) if ra and ra.replace(".", "", 1).isdigit() else None)
    except urllib.error.URLError as e:
        raise AIError(0, str(e.reason)[:200])
    except TimeoutError:
        raise AIError(0, "timeout")


# ---------------------------------------------------------------- models
_OPENAI_SKIP = re.compile(r"embed|whisper|tts|dall-e|moderation|transcribe|realtime|audio|image|search|codex|davinci|babbage|instruct|computer", re.I)


def _epoch(v) -> int:
    if isinstance(v, (int, float)):
        return int(v)
    try:
        return int(datetime.datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp())
    except Exception:
        return 0


def list_models(provider: str, key: str, base_url: str = "") -> list[dict]:
    kind, base = PROVIDERS[provider]["kind"], _base(provider, base_url)
    if not base:
        raise AIError(0, "no base url")
    h = _headers(kind, key)
    out = []
    if kind == "anthropic":
        for m in _http("GET", f"{base}/models?limit=100", h, secret=key).get("data", []):
            out.append({"id": m["id"], "name": m.get("display_name", m["id"]), "created": _epoch(m.get("created_at")), "vision": True})
    elif kind == "gemini":
        for m in _http("GET", f"{base}/models?pageSize=200", h, secret=key).get("models", []):
            mid = m["name"].removeprefix("models/")
            if "generateContent" in m.get("supportedGenerationMethods", []) and "gemini" in mid:
                out.append({"id": mid, "name": m.get("displayName", mid), "created": 0, "vision": True})
    else:
        for m in _http("GET", f"{base}/models", h, secret=key).get("data", []):
            mid = m.get("id", "")
            mods = (m.get("architecture") or {}).get("input_modalities")
            if provider == "openai" and (_OPENAI_SKIP.search(mid) or not re.match(r"(gpt-|o\d|chatgpt-)", mid)):
                continue
            out.append({"id": mid, "name": m.get("name", mid), "created": _epoch(m.get("created")),
                        "vision": ("image" in mods) if mods else None})
    return sorted(out, key=lambda m: m["id"])


def _ver(s: str) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", s)[:4])


def pick_auto(provider: str, models: list[dict]) -> str | None:
    """A sensible cheap + fast + vision-capable default from the provider's live model list."""
    if not models:
        return None
    newest = lambda ms: max(ms, key=lambda m: (m.get("created") or 0, _ver(m["id"]), m["id"]))["id"]
    ids = {m["id"] for m in models}
    if provider == "anthropic":
        for pat in ("haiku", "sonnet"):
            ms = [m for m in models if pat in m["id"]]
            if ms:
                return newest(ms)
    elif provider == "gemini":
        if "gemini-flash-latest" in ids:
            return "gemini-flash-latest"
        ms = [m for m in models if "flash" in m["id"] and not re.search(r"lite|image|tts|live|audio|thinking|exp|preview|8b", m["id"])]
        if ms:
            return max(ms, key=lambda m: (_ver(m["id"]), m["id"]))["id"]
    elif provider == "openai":
        ms = [m for m in models if m["id"].startswith("gpt-") and "mini" in m["id"]]
        alias = [m for m in ms if not re.search(r"-\d{4}-\d{2}-\d{2}$", m["id"])]
        if ms:
            return newest(alias or ms)
    elif provider == "openrouter":
        vis = [m for m in models if m.get("vision")]
        ms = [m for m in vis if re.search(r"flash|haiku|mini", m["id"]) and ":free" not in m["id"] and not re.search(r"preview|exp|lite|image", m["id"])]
        if ms or vis:
            return newest(ms or vis)
    elif provider == "custom":
        return models[0]["id"]
    return newest(models)


# ---------------------------------------------------------------- tagging a single image
NEAR_SECONDS = 2 * 3600          # photos taken within this time of a photo are "close in time" (an event, a trip day)
NEAR_PHOTOS = 12
NEAR_KEYWORDS = 15


def _kw(con, pid: int) -> list[str]:
    return [r["name"] for r in con.execute("SELECT t.name FROM tags t JOIN photo_tags pt ON pt.tag_id=t.id WHERE pt.photo_id=?", (pid,))]


def neighbour_keywords(con, pid: int, taken_at: int | None) -> list[str]:
    """The keywords of the photos taken close in time to this one (other photos), the most common first: they say what the event is."""
    if not taken_at:
        return []
    near = con.execute("SELECT id FROM photos WHERE id<>? AND is_video=0 AND trashed=0 AND taken_at BETWEEN ? AND ? "
                       "ORDER BY ABS(taken_at-?) LIMIT ?", (pid, taken_at - NEAR_SECONDS, taken_at + NEAR_SECONDS, taken_at, NEAR_PHOTOS)).fetchall()
    count: dict = {}
    for n in near:
        for k in _kw(con, n["id"]):
            count[k] = count.get(k, 0) + 1
    return [k for k, _ in sorted(count.items(), key=lambda kv: (-kv[1], kv[0]))][:NEAR_KEYWORDS]


def photo_context(con, pid: int) -> str:
    """What the catalog knows about a photo, as text for the AI: capture time, place, camera and exposure, caption, its own keywords,
    the people in it, and the keywords of the photos taken close in time. Empty when there is nothing."""
    import datetime
    r = con.execute("SELECT filename, taken_at, lat, lng, description, camera_make, camera_model, lens, width, height, rating, exif_json "
                    "FROM photos WHERE id=?", (pid,)).fetchone()
    if not r:
        return ""
    lines = []
    if r["taken_at"]:
        lines.append("Taken: " + datetime.datetime.utcfromtimestamp(r["taken_at"]).strftime("%Y-%m-%d %H:%M (%A)"))
    if r["lat"] is not None and r["lng"] is not None:
        lines.append(f"Location: {r['lat']:.4f}, {r['lng']:.4f}")
    cam = " ".join(x for x in (r["camera_make"], r["camera_model"]) if x)
    if cam or r["lens"]:
        lines.append("Camera: " + ", ".join(x for x in (cam, r["lens"]) if x))
    try:
        ex = json.loads(r["exif_json"]) if r["exif_json"] else {}
    except ValueError:
        ex = {}
    e = ex.get("Exif") or {}
    shot = [f"{lbl} {e[k]}" for k, lbl in (("ExposureTime", "shutter"), ("FNumber", "f/"), ("ISOSpeedRatings", "ISO"), ("FocalLength", "focal length mm"),
                                            ("Flash", "flash")) if e.get(k) not in (None, "")]
    if shot:
        lines.append("Exposure: " + ", ".join(shot))
    if r["width"] and r["height"]:
        lines.append(f"Size: {r['width']}x{r['height']}")
    if r["description"]:
        lines.append("Caption: " + str(r["description"])[:300])
    own = _kw(con, pid)
    if own:
        lines.append("Keywords already on this photo: " + ", ".join(own[:20]))
    people = [x["name"] for x in con.execute(
        "SELECT DISTINCT pe.name FROM people pe WHERE pe.id IN (SELECT person_id FROM photo_people WHERE photo_id=? UNION SELECT person_id FROM faces WHERE photo_id=?) "
        "AND pe.name IS NOT NULL", (pid, pid))]
    if people:
        lines.append("People in the photo: " + ", ".join(people[:10]))
    near = neighbour_keywords(con, pid, r["taken_at"])
    if near:
        lines.append("Keywords of photos taken close in time: " + ", ".join(near))
    return "\n".join(lines)


def _with_context(prompt: str, context: str) -> str:
    return prompt if not context else (prompt + "\n\nWhat is known about this photo (use it where it helps, but trust what you see in the picture):\n" + context)


def _prompt(language: str) -> str:
    lang = LANG_NAMES.get(language, "English")
    return ("You tag photos for a personal photo library search. Look at the photo and answer with ONLY a JSON object "
            f'of the form {{"keywords": ["...", "..."]}} containing 6 to {MAX_KEYWORDS} short keywords written in {lang}: '
            "the main subjects, scene and setting, activity, notable objects, season or time of day, and mood. "
            "Lowercase (where the language has case), one to three words each, no sentences, no hashtags, "
            "do not name real people, do not describe image quality.")


def parse_keywords(text: str) -> list[str]:
    items = None
    m = re.search(r"\{.*\}|\[.*\]", text, re.S)
    if m:
        try:
            j = json.loads(m.group(0))
            items = j.get("keywords", j.get("tags")) if isinstance(j, dict) else j
        except Exception:
            items = None
    if not isinstance(items, list):
        items = re.split(r"[\n,;]+", re.sub(r"[`\[\]{}\"]|keywords\s*:", " ", text, flags=re.I))
    out, seen = [], set()
    for it in items:
        k = re.sub(r"\s+", " ", str(it)).strip(" .#-*•\t\"'").strip().lower()
        if 1 < len(k) <= 40 and k not in seen:
            seen.add(k)
            out.append(k)
    return out[:MAX_KEYWORDS]


def tag_image(provider: str, base_url: str, key: str, model: str, jpeg: bytes, language: str, context: str = "") -> list[str]:
    kind, base = PROVIDERS[provider]["kind"], _base(provider, base_url)
    b64, text, h = base64.b64encode(jpeg).decode(), _with_context(_prompt(language), context), _headers(kind, key)
    if kind == "anthropic":
        r = _http("POST", f"{base}/messages", h, {"model": model, "max_tokens": 300, "messages": [{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}},
            {"type": "text", "text": text}]}]}, secret=key)
        out = "".join(b.get("text", "") for b in r.get("content", []) if b.get("type") == "text")
    elif kind == "gemini":
        r = _http("POST", f"{base}/models/{model}:generateContent", h, {
            "contents": [{"role": "user", "parts": [{"text": text}, {"inline_data": {"mime_type": "image/jpeg", "data": b64}}]}],
            "generationConfig": {"responseMimeType": "application/json"}}, secret=key)
        parts = ((r.get("candidates") or [{}])[0].get("content") or {}).get("parts", [])
        out = "".join(p.get("text", "") for p in parts)
    else:
        img = {"url": f"data:image/jpeg;base64,{b64}"}
        if provider == "openai":
            img["detail"] = "low"        # plenty for keywords, and far fewer input tokens
        r = _http("POST", f"{base}/chat/completions", h, {"model": model, "messages": [{"role": "user", "content": [
            {"type": "text", "text": text}, {"type": "image_url", "image_url": img}]}]}, secret=key)
        c = ((r.get("choices") or [{}])[0].get("message") or {}).get("content", "")
        out = c if isinstance(c, str) else "".join(p.get("text", "") for p in c)
    return parse_keywords(out)


def _tag_with_retry(args, cancel):
    for attempt in range(4):
        try:
            return tag_image(*args)
        except AIError as e:
            transient = e.status in (0, 408, 429, 500, 502, 503, 504, 529)
            if not transient or attempt == 3 or cancel():
                raise
            time.sleep(min(e.retry_after or 2 ** attempt, 20))


# ---------------------------------------------------------------- which photo is the best (#45)
RANK_MODES = ("ask", "always", "off")
MAX_COMPARE = 6                      # photos sent to the AI at once (each one costs tokens)


def _configured() -> bool:
    """The AI of AI tagging is set up: a saved key, or a custom (local) server."""
    c = config.get_ai()
    return c.get("provider", "openai") == "custom" or c.get("provider", "openai") in c.get("keys", {})


def get_rank_mode() -> str:
    """What Rank / Compare do about the AI: "ask" every time, "always" use it, or "off". Nothing chosen yet: ask when the AI is set up, else off."""
    m = config.get_ai().get("rank_mode")
    return m if m in RANK_MODES else ("ask" if _configured() else "off")


def set_rank_mode(mode: str):
    if mode not in RANK_MODES:
        raise ValueError("unknown mode")
    c = config.get_ai()
    c["rank_mode"] = mode
    config.set_ai(c)


def _compare_prompt(n: int, language: str) -> str:
    lang = LANG_NAMES.get(language, "English")
    return (f"You are helping a person choose the best photo among {n} photos of the same scene or subject, numbered 1 to {n} in the order they are given. "
            "Judge sharpness, exposure, composition, expressions (eyes open, natural smiles) and overall appeal. Answer with ONLY a JSON object of the form "
            f'{{"best": <the number of the best photo>, "reason": "<one or two short sentences in {lang} saying why it is the best>"}}.')


def parse_pick(text: str, n: int) -> dict:
    best, reason = None, ""
    m = re.search(r"\{.*\}", text, re.S)
    if m:
        try:
            j = json.loads(m.group(0))
            best, reason = int(j.get("best")), str(j.get("reason", ""))
        except Exception:
            best = None
    if best is None:
        mm = re.search(r"\b([1-9])\b", text)
        best = int(mm.group(1)) if mm else None
        reason = reason or text
    if best is None or not 1 <= best <= n:
        raise AIError(0, "the answer did not name one of the photos")
    return {"best": best, "reason": re.sub(r"\s+", " ", reason).strip()[:500]}


def compare_images(provider: str, base_url: str, key: str, model: str, jpegs: list[bytes], language: str, contexts: list[str] | None = None) -> dict:
    text = _compare_prompt(len(jpegs), language)
    facts = [f"Photo {i}:\n{c}" for i, c in enumerate(contexts or [], 1) if c]
    if facts:
        text += "\n\nWhat is known about the photos (camera settings, time, keywords, people - use it together with what you see):\n" + "\n\n".join(facts)
    return parse_pick(_vision(provider, base_url, key, model, jpegs, text), len(jpegs))


def _vision(provider: str, base_url: str, key: str, model: str, jpegs: list[bytes], text: str) -> str:
    """One request with these photos (numbered) and a question; the text of the answer."""
    kind, base, h = PROVIDERS[provider]["kind"], _base(provider, base_url), _headers(PROVIDERS[provider]["kind"], key)
    b64s = [base64.b64encode(j).decode() for j in jpegs]
    if kind == "anthropic":
        content = []
        for i, b in enumerate(b64s, 1):
            content += [{"type": "text", "text": f"Photo {i}:"}, {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b}}]
        r = _http("POST", f"{base}/messages", h, {"model": model, "max_tokens": 400, "messages": [{"role": "user", "content": content + [{"type": "text", "text": text}]}]}, secret=key)
        out = "".join(b.get("text", "") for b in r.get("content", []) if b.get("type") == "text")
    elif kind == "gemini":
        parts = []
        for i, b in enumerate(b64s, 1):
            parts += [{"text": f"Photo {i}:"}, {"inline_data": {"mime_type": "image/jpeg", "data": b}}]
        r = _http("POST", f"{base}/models/{model}:generateContent", h, {"contents": [{"role": "user", "parts": parts + [{"text": text}]}],
                                                                     "generationConfig": {"responseMimeType": "application/json"}}, secret=key)
        out = "".join(p.get("text", "") for p in ((r.get("candidates") or [{}])[0].get("content") or {}).get("parts", []))
    else:
        content = []
        for i, b in enumerate(b64s, 1):
            img = {"url": f"data:image/jpeg;base64,{b}"}
            if provider == "openai":
                img["detail"] = "low"
            content += [{"type": "text", "text": f"Photo {i}:"}, {"type": "image_url", "image_url": img}]
        r = _http("POST", f"{base}/chat/completions", h, {"model": model, "messages": [{"role": "user", "content": content + [{"type": "text", "text": text}]}]}, secret=key)
        c = ((r.get("choices") or [{}])[0].get("message") or {}).get("content", "")
        out = c if isinstance(c, str) else "".join(p.get("text", "") for p in c)
    return out


_MODEL_CACHE: dict = {}


def rate_prompt() -> str:
    return ("Rate the quality of this photo for a personal photo library from 1 to 100: sharpness, exposure, composition, expressions (eyes open, natural smiles) "
            'and overall appeal. Answer with ONLY a JSON object of the form {"score": <a whole number from 1 to 100>}.')


def parse_score(text: str) -> int | None:
    m = re.search(r'"?score"?\s*[:=]\s*(\d{1,3})', text) or re.search(r"\b(\d{1,3})\b", text)
    n = int(m.group(1)) if m else 0
    return n if 1 <= n <= 100 else None


def rate_photo(row) -> int | None:
    """The AI of AI tagging gives one photo a score from 1 to 100 (a thumbnail is sent). None when the AI is not set up or does not answer."""
    cfg = get_settings()
    provider = cfg["provider"]
    key = _key(provider)
    if provider != "custom" and not key:
        return None
    ck = (provider, cfg["model"], cfg["base_url"])
    if ck not in _MODEL_CACHE:
        _MODEL_CACHE[ck] = cfg["model"] or pick_auto(provider, list_models(provider, key, cfg["base_url"]))
    model = _MODEL_CACHE[ck]
    if not model:
        return None
    tp = images.thumb_path(row["sha256"])
    if not tp.exists():
        images.make_thumb(PATHS.media / row["rel_path"], row["sha256"])
    if not tp.exists():
        return None
    return parse_score(_vision(provider, cfg["base_url"], key, model, [tp.read_bytes()], rate_prompt()))


def pick_best(ids: list[int]) -> dict:
    """Ask the AI (the same provider, key and model as AI tagging) which of these photos is the best, and why."""
    cfg = get_settings()
    provider = cfg["provider"]
    key = _key(provider)
    if provider != "custom" and not key:
        raise AIError(401, "No API key is set for the selected provider")
    model = cfg["model"] or pick_auto(provider, list_models(provider, key, cfg["base_url"]))
    if not model:
        raise AIError(0, "No suitable model found. Choose a model manually in AI tagging settings")
    con, jpegs, used, contexts = db.connect(), [], [], []
    for pid in ids[:MAX_COMPARE]:
        row = con.execute("SELECT id, sha256, rel_path, orig_backup, edited, edit_ops FROM photos WHERE id=? AND is_video=0", (pid,)).fetchone()
        if not row:
            continue
        tp = images.thumb_path(row["sha256"])
        if not tp.exists():
            images.make_thumb(render.current_path(row), row["sha256"])
        if tp.exists():
            jpegs.append(tp.read_bytes())
            used.append(pid)
            contexts.append(photo_context(con, pid))
    if len(jpegs) < 2:
        raise AIError(0, "no thumbnail")
    for attempt in range(4):
        try:
            r = compare_images(provider, cfg["base_url"], key, model, jpegs, cfg["language"], contexts)
            return {"best_id": used[r["best"] - 1], "reason": r["reason"], "model": model}
        except AIError as e:
            if e.status not in (0, 408, 429, 500, 502, 503, 504, 529) or attempt == 3:
                raise
            time.sleep(min(e.retry_after or 2 ** attempt, 20))


# ---------------------------------------------------------------- "AI Suggest": develop settings for one photo (#46)
_EDIT_RANGES = {"exposure": (-2.0, 2.0), "contrast": (0.7, 1.4), "highlights": (-80, 60), "shadows": (-40, 80), "temperature": (-50, 50),
                "tint": (-40, 40), "vibrance": (-30, 60), "sharpness": (0, 60)}


def _edit_prompt(language: str) -> str:
    lang = LANG_NAMES.get(language, "English")
    return ("You are a careful photo editor. Look at this photo and suggest develop settings that improve it without overdoing it: "
            "natural colours, recover blown highlights and dark shadows, correct a colour cast, keep the mood of the scene. Answer with ONLY a JSON object: "
            '{"exposure": <stops, -2..2>, "contrast": <factor, 0.7..1.4, 1 = unchanged>, "highlights": <-80..60>, "shadows": <-40..80>, '
            '"temperature": <-50..50, positive = warmer>, "tint": <-40..40, positive = more magenta>, "vibrance": <-30..60>, "sharpness": <0..60>, '
            f'"reason": "<one or two short sentences in {lang} saying what you changed and why>"}}.')


def parse_edit(text: str) -> dict:
    """The suggested settings (clamped to safe ranges) and the reason, from the model's answer."""
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise AIError(0, "the answer did not contain settings")
    try:
        j = json.loads(m.group(0))
    except Exception:
        raise AIError(0, "the answer did not contain settings")
    ops = {}
    for k, (lo, hi) in _EDIT_RANGES.items():
        try:
            v = float(j.get(k, 1.0 if k == "contrast" else 0.0))
        except (TypeError, ValueError):
            v = 1.0 if k == "contrast" else 0.0
        v = max(lo, min(hi, v))
        ops[k] = round(v, 3) if k in ("contrast", "exposure") else int(round(v))
    return {"ops": ops, "reason": re.sub(r"\s+", " ", str(j.get("reason", ""))).strip()[:400]}


def suggest_edit(pid: int) -> dict:
    """Ask the AI of AI tagging what this photo needs. Nothing is changed: the window puts the answer on the sliders as a preview."""
    cfg = get_settings()
    provider = cfg["provider"]
    key = _key(provider)
    if provider != "custom" and not key:
        raise AIError(401, "No API key is set for the selected provider")
    model = cfg["model"] or pick_auto(provider, list_models(provider, key, cfg["base_url"]))
    if not model:
        raise AIError(0, "No suitable model found. Choose a model manually in AI tagging settings")
    con = db.connect()
    row = con.execute("SELECT id, sha256, rel_path, orig_backup, edited, edit_ops FROM photos WHERE id=? AND is_video=0", (pid,)).fetchone()
    if not row:
        raise AIError(0, "no thumbnail")
    src = PATHS.media / (row["orig_backup"] or row["rel_path"])          # the picture without edits: the suggestion is for the original look
    try:
        import io
        im = images.open_image(src)
        im.thumbnail((1024, 1024))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=85)
        jpeg = buf.getvalue()
    except Exception:
        raise AIError(0, "no thumbnail")
    text = _with_context(_edit_prompt(cfg["language"]), photo_context(con, pid))
    for attempt in range(4):
        try:
            r = parse_edit(_vision(provider, cfg["base_url"], key, model, [jpeg], text))
            return {**r, "model": model}
        except AIError as e:
            if e.status not in (0, 408, 429, 500, 502, 503, 504, 529) or attempt == 3:
                raise
            time.sleep(min(e.retry_after or 2 ** attempt, 20))


# ---------------------------------------------------------------- query translation (search by meaning)
def can_translate() -> bool:
    c = get_settings()
    return c["provider"] == "custom" or bool(c["providers"].get(c["provider"], {}).get("has_key"))


def translate_query(text: str) -> str:
    """The search words in English, through the provider chosen for AI tagging (only this text is sent)."""
    cfg = get_settings()
    provider = cfg["provider"]
    key = _key(provider)
    if provider != "custom" and not key:
        raise AIError(401, "no API key")
    model = cfg["model"] or pick_auto(provider, list_models(provider, key, cfg["base_url"]))
    if not model:
        raise AIError(0, "no model")
    kind, base, h = PROVIDERS[provider]["kind"], _base(provider, cfg["base_url"]), _headers(PROVIDERS[provider]["kind"], key)
    ask = ("Translate this photo search query into short, plain English for an image search. "
           "Answer with ONLY the English words, no quotes, no explanation.\n\n" + text[:200])
    if kind == "anthropic":
        r = _http("POST", f"{base}/messages", h, {"model": model, "max_tokens": 60, "messages": [{"role": "user", "content": ask}]}, secret=key)
        out = "".join(b.get("text", "") for b in r.get("content", []) if b.get("type") == "text")
    elif kind == "gemini":
        r = _http("POST", f"{base}/models/{model}:generateContent", h, {"contents": [{"role": "user", "parts": [{"text": ask}]}]}, secret=key)
        out = "".join(p.get("text", "") for p in ((r.get("candidates") or [{}])[0].get("content") or {}).get("parts", []))
    else:
        r = _http("POST", f"{base}/chat/completions", h, {"model": model, "messages": [{"role": "user", "content": ask}]}, secret=key)
        c = ((r.get("choices") or [{}])[0].get("message") or {}).get("content", "")
        out = c if isinstance(c, str) else "".join(p.get("text", "") for p in c)
    out = re.sub(r"\s+", " ", out).strip().strip("\"'`.")
    if not out:
        raise AIError(0, "empty answer")
    return out[:120]


# ---------------------------------------------------------------- probe (settings dialog)
def probe(provider: str, base_url: str, api_key: str | None) -> dict:
    key = _key(provider, api_key)
    if provider != "custom" and not key:
        raise AIError(401, "no API key")
    models = list_models(provider, key, base_url)
    return {"models": [{"id": m["id"], "name": m["name"], "vision": m["vision"]} for m in models], "auto": pick_auto(provider, models)}


# ---------------------------------------------------------------- the job
def _set_ai_tags(con, pid: int, names: list[str]):
    con.execute("DELETE FROM photo_tags WHERE photo_id=? AND source='ai'", (pid,))
    for n in names:
        row = con.execute("SELECT id FROM tags WHERE name=? COLLATE NOCASE ORDER BY id LIMIT 1", (n,)).fetchone()
        if row:
            tid = row["id"]
        else:
            tid = con.execute("INSERT INTO tags(name) VALUES(?)", (n,)).lastrowid
        con.execute("INSERT OR IGNORE INTO photo_tags(photo_id,tag_id,source) VALUES(?,?, 'ai')", (pid, tid))
    db.apply_tag_links(con, [pid])                       # linked keywords (#39)


def run_aitag(ids: list[int] | None, only_untagged: bool, progress):
    con = db.init_db()
    cfg = get_settings()
    provider = cfg["provider"]
    try:
        key = _key(provider)
        if provider != "custom" and not key:
            return progress.fail("No API key is set for the selected provider")
        progress.state = "preparing"; progress.say("Connecting to the AI provider…")
        model = cfg["model"]
        if not model:
            model = pick_auto(provider, list_models(provider, key, cfg["base_url"]))
            if not model:
                return progress.fail("No suitable model found. Choose a model manually in AI tagging settings")
        where, args = "is_video=0 AND trashed=0", []
        if ids:
            where += f" AND id IN ({','.join('?' * len(ids))})"; args += ids
        if only_untagged:
            where += " AND NOT EXISTS(SELECT 1 FROM photo_tags pt WHERE pt.photo_id=photos.id)"
        todo = con.execute(f"SELECT id, sha256, rel_path, orig_backup, edited, edit_ops FROM photos WHERE {where} ORDER BY taken_at DESC", args).fetchall()
        if not todo:
            progress.state = "done"; return progress.say("No photos to tag")
        progress.state = "tagging"; progress.total = len(todo); progress.done = 0
        progress.say("Tagging with {model} · {done}/{total}", model=model, done=0, total=len(todo))
        cancelled = lambda: bool(getattr(progress, "cancel", False))

        def work(row):
            if cancelled():          # a queued photo must not be sent after the user pressed stop
                return None
            tp = images.thumb_path(row["sha256"])
            if not tp.exists():
                images.make_thumb(render.current_path(row), row["sha256"])
            if not tp.exists():
                raise AIError(0, "no thumbnail")
            try:                    # own connection (this runs in a worker thread); keywords given a moment ago to the photos taken close in time are seen too
                wc = db.connect()
                try:
                    context = photo_context(wc, row["id"])
                finally:
                    wc.close()
            except Exception:
                context = ""
            return _tag_with_retry((provider, cfg["base_url"], key, model, tp.read_bytes(), cfg["language"], context), cancelled)

        ok = fails = kws = 0
        last_err = ""
        failures = []                  # "file: reason", shown on the progress screen
        handled = set()
        progress.extra = {"ok": 0, "failed": 0, "keywords": 0, "model": model, "last_error": "", "failures": []}      # live numbers for the progress screen
        with cf.ThreadPoolExecutor(WORKERS) as ex:
            futs = {ex.submit(work, r): r for r in todo}
            try:
                for f in cf.as_completed(futs):
                    row = futs[f]
                    handled.add(f)
                    try:
                        names = f.result()
                        if names is None:       # skipped because of a cancel
                            continue
                        _set_ai_tags(con, row["id"], names); ok += 1; kws += len(names)
                    except AIError as e:
                        fails += 1; last_err = str(e); failures.append(f"{os.path.basename(row['rel_path'])}: {last_err[:80]}")
                        if e.status in (401, 403) or (ok == 0 and fails >= 3):
                            for g in futs:
                                g.cancel()
                            con.commit()
                            return progress.fail("AI tagging stopped: {error}", error=last_err)
                    except Exception as e:                      # a photo we couldn't read: skip it
                        fails += 1; last_err = str(e); failures.append(f"{os.path.basename(row['rel_path'])}: {last_err[:80]}")
                    progress.done = ok + fails
                    progress.extra = {"ok": ok, "failed": fails, "keywords": kws, "model": model, "last_error": last_err[:160], "failures": failures[-20:]}
                    progress.say("Tagging with {model} · {done}/{total}", model=model, done=progress.done, total=len(todo))
                    con.commit()                                # each photo's tags are visible to the window right away, not only at the end
                    if cancelled():
                        for g in futs:
                            g.cancel()
                        for g in futs:                      # requests already sent still get saved
                            if g not in handled and not g.cancelled():
                                try:
                                    names = g.result()
                                    if names is not None:
                                        _set_ai_tags(con, futs[g]["id"], names); ok += 1
                                except Exception:
                                    pass
                        break
            finally:
                con.commit()
        progress.state = "done"
        if cancelled():
            progress.say_parts(("Tagging was stopped", {}), ("Tagged {n} photos", {"n": ok}))
        else:
            progress.say_parts(("Tagged {n} photos with {model}", {"n": ok, "model": model}),
                               *([("{n} failed", {"n": fails})] if fails else []))
    except AIError as e:
        con.commit()
        progress.fail("AI tagging failed: {error}", error=str(e))
    except Exception as e:
        con.commit()
        progress.fail("AI tagging failed: {error}", error=str(e))
