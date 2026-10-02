"""Search by meaning, entirely on this computer: "beach at sunset", "dog in the snow".

CLIP ViT-B/32 (ONNX) runs on the onnxruntime that face detection already uses. Every photo gets a 512-number embedding
(stored in the catalog, table photo_clip) once; a search embeds the words the same way and ranks photos by similarity.
Nothing leaves the computer, apart from the one-time model download (~600 MB, only after the user agrees).
CLIP understands English; a query in another language is translated first, when the user has set up an AI provider
for AI tagging (only the query text is sent, to that provider), otherwise the UI asks for English words.
Photo-by-photo work: images.open_image -> 224px centre crop -> vision model; text: pure-Python CLIP tokenizer -> text model."""
import json
import os
import re
import unicodedata
import urllib.request
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image

from . import cloud, config, db, images
from .config import PATHS
from .net import ssl_context

HF = "https://huggingface.co/Xenova/clip-vit-base-patch32/resolve/main/"
MODEL_FILES = {  # local name -> remote path
    "vision.onnx": "onnx/vision_model.onnx",
    "text.onnx": "onnx/text_model.onnx",
    "tokenizer.json": "tokenizer.json",
}
MODEL_MB = 600
BATCH = 16


def models_dir() -> Path:
    if config.PORTABLE:
        d = config.PORTABLE / "data" / "models" / "clip-vit-b32"
    else:
        d = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "photag" / "models" / "clip-vit-b32"
    d.mkdir(parents=True, exist_ok=True)
    return d


def model_ready() -> bool:
    d = models_dir()
    return all((d / f).exists() for f in MODEL_FILES)


def _download(url: str, dest: Path, progress, cancelled):
    if dest.exists():
        return
    tmp = dest.with_suffix(dest.suffix + ".part")
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "photag"}), timeout=60, context=ssl_context()) as r, open(tmp, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        while chunk := r.read(1 << 20):
            if cancelled():
                f.close(); tmp.unlink(missing_ok=True)
                raise InterruptedError
            f.write(chunk); done += len(chunk)
            if total:
                progress.total, progress.done = total, done
                progress.say("Downloading the search model ({done}/{total} MB)", done=done >> 20, total=total >> 20)
    tmp.replace(dest)


def ensure_models(progress, cancelled=lambda: False):
    d = models_dir()
    progress.state = "downloading"
    for name, remote in MODEL_FILES.items():
        _download(HF + remote, d / name, progress, cancelled)
    _sessions.cache_clear()


# ---------- CLIP BPE tokenizer (pure Python, matches the HF tokenizers' output) ----------
def _bytes_to_unicode():
    bs = list(range(ord("!"), ord("~") + 1)) + list(range(ord("¡"), ord("¬") + 1)) + list(range(ord("®"), ord("ÿ") + 1))
    cs = bs[:]
    n = 0
    for b in range(256):
        if b not in bs:
            bs.append(b); cs.append(256 + n); n += 1
    return dict(zip(bs, map(chr, cs)))


_SPLIT = re.compile(r"<\|startoftext\|>|<\|endoftext\|>|'s|'t|'re|'ve|'m|'ll|'d|[^\W\d_]+|\d|(?:[^\s\w]|_)+")
BOS, EOS, CTX = 49406, 49407, 77


class Tokenizer:
    def __init__(self, vocab: dict, merges: list):
        self.vocab = vocab
        self.ranks = {(m[0], m[1]) if not isinstance(m, str) else tuple(m.split(" ")): i for i, m in enumerate(merges)}
        self.b2u = _bytes_to_unicode()
        self.cache = {}

    @classmethod
    def from_file(cls, path: Path):
        m = json.loads(path.read_text("utf-8"))["model"]
        return cls(m["vocab"], m["merges"])

    def _bpe(self, token: str) -> list[str]:
        if token in self.cache:
            return self.cache[token]
        word = list(token[:-1]) + [token[-1] + "</w>"]
        while len(word) > 1:
            rank, i = min((self.ranks.get((a, b), 1 << 30), i) for i, (a, b) in enumerate(zip(word, word[1:])))
            if rank == 1 << 30:
                break
            a, b = word[i], word[i + 1]
            out, j = [], 0
            while j < len(word):
                if j < len(word) - 1 and word[j] == a and word[j + 1] == b:
                    out.append(a + b); j += 2
                else:
                    out.append(word[j]); j += 1
            word = out
        self.cache[token] = word
        return word

    def encode(self, text: str) -> list[int]:
        text = re.sub(r"\s+", " ", unicodedata.normalize("NFC", text)).strip().lower()
        ids = [BOS]
        for tok in _SPLIT.findall(text):
            u = "".join(self.b2u[b] for b in tok.encode("utf-8"))
            ids += [self.vocab[p] for p in self._bpe(u) if p in self.vocab]
        return ids[:CTX - 1] + [EOS]


# ---------- models ----------
@lru_cache(maxsize=1)
def _sessions():
    import onnxruntime as ort
    d = models_dir()
    opt = ort.SessionOptions()
    opt.intra_op_num_threads = max(1, (os.cpu_count() or 4) - 1)
    mk = lambda f: ort.InferenceSession(str(d / f), opt, providers=["CPUExecutionProvider"])
    return mk("vision.onnx"), mk("text.onnx"), Tokenizer.from_file(d / "tokenizer.json")


_MEAN = np.array([0.48145466, 0.4578275, 0.40821073], np.float32).reshape(3, 1, 1)
_STD = np.array([0.26862954, 0.26130258, 0.27577711], np.float32).reshape(3, 1, 1)


def _pixels(path: Path) -> np.ndarray:
    im = images.open_image(path)
    w, h = im.size
    s = 224 / min(w, h)
    im = im.resize((max(224, round(w * s)), max(224, round(h * s))), Image.BICUBIC)
    w, h = im.size
    l, t = (w - 224) // 2, (h - 224) // 2
    a = np.asarray(im.crop((l, t, l + 224, t + 224)), np.float32).transpose(2, 0, 1) / 255.0
    return (a - _MEAN) / _STD


def _norm(x: np.ndarray) -> np.ndarray:
    return x / np.linalg.norm(x, axis=-1, keepdims=True)


def embed_images(paths: list[Path]) -> np.ndarray:
    vis, _, _ = _sessions()
    return _norm(vis.run(None, {"pixel_values": np.stack([_pixels(p) for p in paths])})[0])


def embed_texts(texts: list[str]) -> np.ndarray:
    _, txt, tok = _sessions()
    ids = [tok.encode(t) for t in texts]
    n = max(map(len, ids))
    arr = np.array([i + [EOS] * (n - len(i)) for i in ids], np.int64)   # padded with EOS: the pooling takes the first EOS
    return _norm(txt.run(None, {"input_ids": arr})[0])


# ---------- the index ----------
def counts(con) -> dict:
    total = con.execute("SELECT COUNT(*) FROM photos WHERE is_video=0 AND trashed=0").fetchone()[0]
    done = con.execute("SELECT COUNT(*) FROM photos p JOIN photo_clip c ON c.photo_id=p.id AND c.sha=p.sha256 "
                       "WHERE p.is_video=0 AND p.trashed=0").fetchone()[0]
    return {"ready": model_ready(), "total": total, "indexed": done, "model_mb": MODEL_MB}


def _todo(con):
    return con.execute("SELECT p.id, p.sha256, p.rel_path FROM photos p LEFT JOIN photo_clip c ON c.photo_id=p.id "
                       "WHERE p.is_video=0 AND p.trashed=0 AND (c.photo_id IS NULL OR c.sha IS NOT p.sha256) "
                       "ORDER BY p.taken_at DESC").fetchall()


def run_index(download: bool, progress):
    """Download the model if the user agreed, then embed every photo that has no (current) embedding."""
    cancelled = lambda: bool(getattr(progress, "cancel", False))
    try:
        if not model_ready():
            if not download:
                return progress.fail("The search model is not installed yet")
            ensure_models(progress, cancelled)
        con = db.init_db()
        todo = _todo(con)
        progress.state = "analyzing"; progress.total = len(todo); progress.done = 0
        progress.say("Indexing photos for search {done}/{total}", done=0, total=len(todo))
        done = waiting = 0
        for i in range(0, len(todo), BATCH):
            if cancelled():
                break
            part = todo[i:i + BATCH]
            usable, vecs = [], []
            for r in part:                            # one unreadable photo must not sink the batch
                if cloud.is_online_only(PATHS.media / r["rel_path"]):
                    waiting += 1                  # only in the cloud (OneDrive): not downloaded for this, indexed once it is here
                    continue
                try:
                    vecs.append(_pixels(PATHS.media / r["rel_path"])); usable.append(r)
                except Exception:
                    con.execute("INSERT INTO photo_clip(photo_id,sha,emb) VALUES(?,?,NULL) ON CONFLICT(photo_id) DO UPDATE SET sha=excluded.sha, emb=NULL",
                                (r["id"], r["sha256"]))
            if vecs:
                vis, _, _ = _sessions()
                E = _norm(vis.run(None, {"pixel_values": np.stack(vecs)})[0]).astype(np.float16)
                for r, e in zip(usable, E):
                    con.execute("INSERT INTO photo_clip(photo_id,sha,emb) VALUES(?,?,?) ON CONFLICT(photo_id) DO UPDATE SET sha=excluded.sha, emb=excluded.emb",
                                (r["id"], r["sha256"], e.tobytes()))
            done += len(part)
            progress.done = done
            progress.say("Indexing photos for search {done}/{total}", done=done, total=len(todo))
            con.commit()
        con.commit()
        _CACHE["key"] = None
        progress.state = "done"
        if cancelled():
            progress.say("Indexing was stopped: {n} photos indexed", n=done)
        elif waiting:
            progress.say("Search is ready: {n} photos indexed; {w} files only in the cloud (OneDrive) were skipped", n=done - waiting, w=waiting)
        else:
            progress.say("Search is ready: {n} photos indexed", n=done)
    except InterruptedError:
        progress.state = "done"; progress.say("The download was cancelled")
    except Exception as e:
        progress.fail("Search indexing failed: {error}", error=str(e))


_CACHE = {"key": None, "data": None}


def _library(con):
    key = tuple(con.execute("SELECT COUNT(*), COALESCE(SUM(photo_id),0) FROM photo_clip WHERE emb IS NOT NULL").fetchone())
    if _CACHE["key"] != key:
        rows = con.execute("SELECT photo_id, emb FROM photo_clip WHERE emb IS NOT NULL").fetchall()
        ids = np.array([r["photo_id"] for r in rows], np.int64)
        E = np.stack([np.frombuffer(r["emb"], np.float16) for r in rows]).astype(np.float32) if rows else np.zeros((0, 512), np.float32)
        _CACHE.update(key=key, data=(ids, E))
    return _CACHE["data"]


def rank_embeddings(ids: np.ndarray, E: np.ndarray, q: np.ndarray, limit: int = 600) -> dict[int, float]:
    """photo_id -> similarity for the photos that fit the query: clearly above the library's typical similarity
    (z-score) and above a floor; a small library always gets at least its best few that pass the floor."""
    if not len(ids):
        return {}
    sims = E @ q
    z = (sims - sims.mean()) / max(float(sims.std()), 1e-3)
    keep = np.where((z >= 2.0) & (sims >= 0.2))[0]
    if len(keep) < 12:
        top = np.argsort(-sims)[:12]
        keep = np.array(sorted(set(keep.tolist()) | {int(k) for k in top if sims[k] >= 0.21}), dtype=np.int64)
    keep = keep[np.argsort(-sims[keep])][:limit]
    return {int(ids[k]): round(float(sims[k]), 4) for k in keep}


def search(con, query: str, limit: int = 600) -> dict[int, float]:
    """photo_id -> similarity for a free-text English query ("kids on the beach")."""
    if not model_ready():
        return {}
    ids, E = _library(con)
    if not len(ids):
        return {}
    q = _norm(embed_texts([query, f"a photo of {query}"]).mean(0))
    return rank_embeddings(ids, E, q, limit)


def is_english(text: str) -> bool:
    return all(ord(c) < 0x250 or not c.isalpha() for c in text)      # Latin letters only
