"""AI keyword tagging with the user's own API key.

Providers: OpenAI, Anthropic (Claude), Google Gemini, OpenRouter (one key, hundreds of
models) and "custom" = any OpenAI-compatible endpoint (Groq, Together, Mistral, a local
Ollama / LM Studio ...). Only a small thumbnail (the library's 512 px JPEG) of each
photo is sent, and only when the user starts a run. Keys are stored encrypted
(keystore.py) in config.json and never sent back to the UI.
"""
import base64
import concurrent.futures as cf
import datetime
import json
import os
import re
import time
import urllib.error
import urllib.request

from . import config, db, images, keystore
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
            "base_url": c.get("base_url", ""),
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


def tag_image(provider: str, base_url: str, key: str, model: str, jpeg: bytes, language: str) -> list[str]:
    kind, base = PROVIDERS[provider]["kind"], _base(provider, base_url)
    b64, text, h = base64.b64encode(jpeg).decode(), _prompt(language), _headers(kind, key)
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
        todo = con.execute(f"SELECT id, sha256, rel_path FROM photos WHERE {where} ORDER BY taken_at DESC", args).fetchall()
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
                images.make_thumb(PATHS.media / row["rel_path"], row["sha256"])
            if not tp.exists():
                raise AIError(0, "no thumbnail")
            return _tag_with_retry((provider, cfg["base_url"], key, model, tp.read_bytes(), cfg["language"]), cancelled)

        ok = fails = 0
        last_err = ""
        handled = set()
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
                        _set_ai_tags(con, row["id"], names); ok += 1
                    except AIError as e:
                        fails += 1; last_err = str(e)
                        if e.status in (401, 403) or (ok == 0 and fails >= 3):
                            for g in futs:
                                g.cancel()
                            con.commit()
                            return progress.fail("AI tagging stopped: {error}", error=last_err)
                    except Exception as e:                      # a photo we couldn't read: skip it
                        fails += 1; last_err = str(e)
                    progress.done = ok + fails
                    progress.say("Tagging with {model} · {done}/{total}", model=model, done=progress.done, total=len(todo))
                    if progress.done % 10 == 0:
                        con.commit()
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
