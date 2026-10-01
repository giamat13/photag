"""Automatic English keywords + content search, with no setup.

CLIP ViT-B/32 (ONNX) runs on the onnxruntime that face detection already uses;
the model downloads itself on first use (~600 MB, like buffalo_l for faces).
Full precision on purpose: batched it is ~15 ms/photo on a CPU, faster than
the quantized files, which also drift noticeably from the real embeddings.
One pass over the library gives each photo:
  * keywords picked from a fixed English vocabulary -> consistent words, no
    made-up or echoed tags (what the old Ollama chat-prompt approach produced)
  * a 512-d embedding, so "Smart search" can find "kids playing on the beach"
    even when no keyword says so
Place keywords (city, country) come from GPS via an offline GeoNames table.
"""
import hashlib
import json
import os
import re
import time
import unicodedata
import urllib.request
import zipfile
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image

from . import db, images

HF = "https://huggingface.co/Xenova/clip-vit-base-patch32/resolve/main/"
MODEL_FILES = {  # local name -> remote path
    "vision.onnx": "onnx/vision_model.onnx",
    "text.onnx": "onnx/text_model.onnx",
    "tokenizer.json": "tokenizer.json",
}
GEO_FILES = {
    "cities15000.zip": "https://download.geonames.org/export/dump/cities15000.zip",
    "countryInfo.txt": "https://download.geonames.org/export/dump/countryInfo.txt",
}


def models_dir() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA") or Path.home())
    d = base / "photag" / "models" / "clip-vit-b32"
    d.mkdir(parents=True, exist_ok=True)
    return d


def model_ready() -> bool:
    d = models_dir()
    return all((d / f).exists() for f in MODEL_FILES)


def _download(url: str, dest: Path, progress=None, label="{done}/{total} MB"):
    if dest.exists():
        return
    tmp = dest.with_suffix(dest.suffix + ".part")
    with urllib.request.urlopen(url, timeout=60) as r, open(tmp, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        while chunk := r.read(1 << 20):
            f.write(chunk); done += len(chunk)
            if progress is not None and total:
                progress.total, progress.done = total, done
                progress.say(label, done=done >> 20, total=total >> 20)
    tmp.replace(dest)


def ensure_models(progress=None):
    d = models_dir()
    for name, remote in MODEL_FILES.items():
        _download(HF + remote, d / name, progress, "מוריד מודל תיוג ({done}/{total} MB)")
    for name, url in GEO_FILES.items():
        try:
            _download(url, d / name, progress, "מוריד מפת מקומות ({done}/{total} MB)")
        except Exception:
            pass  # places are a bonus; tagging works without them


# ---------- CLIP BPE tokenizer (pure Python, matches HF tokenizers' output) ----------
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
    def __init__(self, path: Path):
        m = json.loads(path.read_text("utf-8"))["model"]
        self.vocab = m["vocab"]
        merges = [tuple(x.split(" ")) if isinstance(x, str) else tuple(x) for x in m["merges"]]
        self.ranks = {p: i for i, p in enumerate(merges)}
        self.b2u = _bytes_to_unicode()
        self.cache = {}

    def _bpe(self, token: str) -> list[str]:
        if token in self.cache:
            return self.cache[token]
        word = list(token[:-1]) + [token[-1] + "</w>"]
        while len(word) > 1:
            pairs = [(self.ranks.get((a, b), 1 << 30), i) for i, (a, b) in enumerate(zip(word, word[1:]))]
            rank, i = min(pairs)
            if rank == 1 << 30:
                break
            a, b = word[i], word[i + 1]
            out, j = [], 0
            while j < len(word):  # merge every occurrence of the pair
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
    return mk("vision.onnx"), mk("text.onnx"), Tokenizer(d / "tokenizer.json")


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
    batch = np.stack([_pixels(p) for p in paths])
    return _norm(vis.run(None, {"pixel_values": batch})[0])


def embed_texts(texts: list[str]) -> np.ndarray:
    _, txt, tok = _sessions()
    ids = [tok.encode(t) for t in texts]
    n = max(map(len, ids))
    arr = np.array([i + [EOS] * (n - len(i)) for i in ids], np.int64)  # pad with EOS: pooling takes the first EOS
    return _norm(txt.run(None, {"input_ids": arr})[0])


# ---------- vocabulary ----------
# "keyword" or "keyword|prompt" when the bare word is ambiguous for CLIP.
VOCAB = """
beach|a photo of a sandy beach
sea|a photo of the sea
ocean waves|a photo of ocean waves
lake|a photo of a lake
river|a photo of a river
waterfall
pool|a photo of a swimming pool
mountains|a photo of mountains
hills|a photo of green hills
desert|a photo of a desert
forest|a photo of a forest
trees|a photo of trees
park|a photo of a city park
garden|a photo of a garden
field|a photo of an open field
flowers|a photo of flowers
snow|a photo of snow
sunset|a photo of a sunset
sunrise|a photo of a sunrise
night|a photo taken at night
sky|a photo of the sky with clouds
rain|a photo taken in the rain
city|a photo of a city
street|a photo of a street
skyline|a photo of a city skyline
buildings|a photo of buildings
architecture|a photo of architecture
bridge|a photo of a bridge
church|a photo of a church
mosque|a photo of a mosque
synagogue|a photo of a synagogue
castle|a photo of a castle
ruins|a photo of ancient ruins
museum|a photo inside a museum
market|a photo of a market
shop|a photo inside a shop
restaurant|a photo inside a restaurant
cafe|a photo of a cafe
bar|a photo of a bar
kitchen|a photo of a kitchen
living room|a photo of a living room
bedroom|a photo of a bedroom
bathroom|a photo of a bathroom
office|a photo of an office
classroom|a photo of a classroom
school|a photo of a school
playground|a photo of a playground
stadium|a photo of a stadium
airport|a photo of an airport
train station|a photo of a train station
hotel|a photo of a hotel room
camping|a photo of camping with a tent
indoors|a photo taken indoors
outdoors|a photo taken outdoors
portrait|a portrait photo of a person
selfie|a selfie
group photo|a group photo of people
crowd|a photo of a crowd of people
baby|a photo of a baby
kids|a photo of children
family|a family photo
couple|a photo of a couple
friends|a photo of friends together
elderly|a photo of an elderly person
smiling|a photo of people smiling
birthday|a photo of a birthday party
birthday cake|a photo of a birthday cake with candles
wedding|a photo of a wedding
party|a photo of a party
concert|a photo of a concert
holiday dinner|a photo of a holiday dinner table
graduation|a photo of a graduation ceremony
christmas|a photo of christmas decorations
fireworks|a photo of fireworks
celebration|a photo of a celebration
ceremony|a photo of a ceremony
army|a photo of soldiers in uniform
sports|a photo of people playing sports
soccer|a photo of soccer
basketball|a photo of basketball
swimming|a photo of people swimming
running|a photo of people running
cycling|a photo of cycling
hiking|a photo of hiking on a trail
skiing|a photo of skiing
surfing|a photo of surfing
dancing|a photo of people dancing
playing music|a photo of someone playing a musical instrument
reading|a photo of someone reading a book
cooking|a photo of someone cooking
eating|a photo of people eating
shopping|a photo of shopping
travel|a travel photo
vacation|a vacation photo
road trip|a photo of a road trip
dog|a photo of a dog
cat|a photo of a cat
bird|a photo of a bird
horse|a photo of a horse
fish|a photo of fish
farm animals|a photo of farm animals
wildlife|a photo of wild animals
zoo|a photo taken at a zoo
insect|a photo of an insect
food|a photo of food
pizza|a photo of pizza
burger|a photo of a burger
sushi|a photo of sushi
salad|a photo of a salad
dessert|a photo of a dessert
ice cream|a photo of ice cream
fruit|a photo of fruit
vegetables|a photo of vegetables
bread|a photo of bread
coffee|a photo of a cup of coffee
drinks|a photo of drinks
wine|a photo of wine
breakfast|a photo of breakfast
car|a photo of a car
motorcycle|a photo of a motorcycle
bicycle|a photo of a bicycle
bus|a photo of a bus
train|a photo of a train
airplane|a photo of an airplane
boat|a photo of a boat
ship|a photo of a ship
toys|a photo of toys
lego|a photo of lego
balloons|a photo of balloons
gift|a photo of wrapped gifts
book|a photo of books
computer|a photo of a computer
phone|a photo of a mobile phone
tv|a photo of a television
furniture|a photo of furniture
clothes|a photo of clothes
shoes|a photo of shoes
art|a photo of a painting or artwork
drawing|a photo of a child's drawing
sculpture|a photo of a sculpture
graffiti|a photo of graffiti
sign|a photo of a sign with text
flag|a photo of a flag
candles|a photo of candles
christmas tree|a photo of a christmas tree
document|a photo of a paper document
receipt|a photo of a receipt
whiteboard|a photo of a whiteboard
screenshot|a screenshot of a phone or computer screen
text|an image of text
map|an image of a map
black and white|a black and white photo
close-up|a close-up photo
landscape|a landscape photo
aerial view|an aerial photo taken from above
underwater|an underwater photo
reflection|a photo of a reflection in water
silhouette|a silhouette photo
blurry|a blurry photo
dark|a very dark photo
""".strip().splitlines()
KEYWORDS = [v.split("|")[0] for v in VOCAB]
PROMPTS = [v.split("|")[-1] if "|" in v else f"a photo of {v}" for v in VOCAB]


@lru_cache(maxsize=1)
def vocab_embeddings() -> np.ndarray:
    d = models_dir()
    key = hashlib.sha1("\n".join(PROMPTS).encode()).hexdigest()[:12]
    cache = d / f"vocab_{key}.npy"
    if cache.exists():
        return np.load(cache)
    embs = np.concatenate([embed_texts(PROMPTS[i:i + 32]) for i in range(0, len(PROMPTS), 32)])
    np.save(cache, embs)
    return embs


# ---------- calibrated keyword picking ----------
# Raw CLIP similarities are compressed (true matches ~0.25-0.30, noise ~0.20)
# and some prompts ("travel", "outdoors", "kids") score high on nearly every
# photo. So each keyword is judged against how the rest of the library scores
# on it (z-score): a photo gets "beach" when it looks much more like a beach
# than photos usually do. Small libraries lean on label_stats.json - per-keyword
# averages measured on a ~3,000-photo family library - until they have enough
# photos of their own.
Z_MIN, SIM_MIN, MAX_TAGS = 2.3, 0.235, 5
TOP_Z, TOP_SIM = 1.8, 0.25     # a single clear best keyword still counts
PRIOR_WEIGHT = 500             # photos' worth of confidence in the shipped stats
STATS_FILE = Path(__file__).with_name("label_stats.json")


def label_stats(sims: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Per-keyword mean/std, blended with the shipped prior by library size."""
    n = len(sims)
    mu = sims.mean(0) if n else np.zeros(len(KEYWORDS))
    sd = sims.std(0) if n > 1 else np.zeros(len(KEYWORDS))
    try:
        prior = json.loads(STATS_FILE.read_text("utf-8"))
    except Exception:
        prior = {"mean": {}, "std": {}}
    for j, k in enumerate(KEYWORDS):
        if k in prior["mean"]:
            w = PRIOR_WEIGHT / (PRIOR_WEIGHT + n)
            mu[j] = w * prior["mean"][k] + (1 - w) * mu[j]
            sd[j] = w * prior["std"][k] + (1 - w) * sd[j]
    return mu, np.maximum(sd, 1e-3)


def pick_keywords(sims: np.ndarray, z: np.ndarray) -> list[int]:
    order = np.argsort(-z)
    picked = [j for j in order[:MAX_TAGS * 3] if z[j] >= Z_MIN and sims[j] >= SIM_MIN][:MAX_TAGS]
    if not picked and z[order[0]] >= TOP_Z and sims[order[0]] >= TOP_SIM:
        picked = [int(order[0])]
    return picked


# ---------- places (offline reverse geocoding) ----------
@lru_cache(maxsize=1)
def _places():
    d = models_dir()
    try:
        countries = {}
        for line in (d / "countryInfo.txt").read_text("utf-8").splitlines():
            if line and not line.startswith("#"):
                f = line.split("\t")
                countries[f[0]] = f[4]
        names, cc, lat, lng, pop = [], [], [], [], []
        with zipfile.ZipFile(d / "cities15000.zip") as z:
            for line in z.read("cities15000.txt").decode("utf-8").splitlines():
                f = line.split("\t")
                names.append(re.sub(r"^(West|East) ", "", f[2]))  # ascii English name; "West Jerusalem" -> "Jerusalem"
                cc.append(f[8]); lat.append(float(f[4])); lng.append(float(f[5])); pop.append(int(f[14] or 0))
        return names, cc, np.radians(lat), np.radians(lng), np.array(pop), countries
    except Exception:
        return None


def place_keywords(lat: float, lng: float) -> list[str]:
    P = _places()
    if not P:
        return []
    names, cc, la, lo, pop, countries = P
    a, b = np.radians(lat), np.radians(lng)
    km = 6371 * np.hypot((lo - b) * np.cos((la + a) / 2), la - a)   # equirectangular: plenty for "nearest city"
    # GeoNames also lists districts ("Old City", neighbourhoods) as cities; people
    # name a place after the big city around them -> most populous one close by.
    near = np.where(km <= km.min() + 4)[0]
    i = int(near[np.argmax(pop[near])])
    out = []
    if km[i] <= 15:
        out.append(names[i])
    if km[i] <= 60 and cc[i] in countries:
        out.append(countries[cc[i]])
    return out


# ---------- catalog storage ----------
def _emb_rows(con):
    rows = con.execute("SELECT e.photo_id, e.emb FROM clip_emb e JOIN photos p ON p.id=e.photo_id "
                       "WHERE p.trashed=0 AND e.emb IS NOT NULL").fetchall()
    ids = np.array([r["photo_id"] for r in rows], np.int64)
    E = np.frombuffer(b"".join(r["emb"] for r in rows), np.float16).reshape(-1, 512).astype(np.float32) if rows else np.zeros((0, 512), np.float32)
    return ids, E


def _set_tags(con, photo_id: int, names: list[str], source: str, rejected: set):
    con.execute("DELETE FROM photo_tags WHERE photo_id=? AND source=?", (photo_id, source))
    for t in names:
        if (photo_id, t) in rejected:
            continue
        con.execute("INSERT OR IGNORE INTO tags(name) VALUES(?)", (t,))
        tid = con.execute("SELECT id FROM tags WHERE name=?", (t,)).fetchone()["id"]
        con.execute("INSERT OR IGNORE INTO photo_tags(photo_id,tag_id,source) VALUES(?,?,?)", (photo_id, tid, source))


def pending_count(con) -> int:
    return con.execute("SELECT COUNT(*) FROM photos p LEFT JOIN clip_emb e ON e.photo_id=p.id "
                       "WHERE p.trashed=0 AND p.is_video=0 AND (e.photo_id IS NULL OR e.sha256<>p.sha256)").fetchone()[0]


def run_autotag(progress):
    """Embed new/changed photos, then (re)assign keywords and places for the
    whole library - cheap once embeddings exist, and keeps the calibration
    consistent as the library grows. Keywords the user removed stay removed."""
    from .config import PATHS
    con = db.init_db()
    try:
        if not model_ready():
            progress.state = "downloading"
        ensure_models(progress)
        todo = con.execute(
            "SELECT p.id, p.sha256 FROM photos p LEFT JOIN clip_emb e ON e.photo_id=p.id "
            "WHERE p.trashed=0 AND p.is_video=0 AND (e.photo_id IS NULL OR e.sha256<>p.sha256)").fetchall()
        progress.state = "tagging"; progress.total = len(todo); progress.done = 0
        progress.say("מנתח תמונות…")
        for i in range(0, len(todo), 16):
            batch, arrs = todo[i:i + 16], []
            for r in batch:
                try:
                    tp = images.thumb_path(r["sha256"])
                    if not tp.exists():
                        rel = con.execute("SELECT rel_path FROM photos WHERE id=?", (r["id"],)).fetchone()["rel_path"]
                        images.make_thumb(PATHS.media / rel, r["sha256"])
                    arrs.append(_pixels(tp))
                except Exception:
                    arrs.append(None)   # unreadable -> remembered with no embedding, not retried until it changes
            ok = [a for a in arrs if a is not None]
            embs = iter(_norm(_sessions()[0].run(None, {"pixel_values": np.stack(ok)})[0]) if ok else [])
            for r, a in zip(batch, arrs):
                e = next(embs).astype(np.float16).tobytes() if a is not None else None
                con.execute("INSERT OR REPLACE INTO clip_emb(photo_id, sha256, emb) VALUES(?,?,?)", (r["id"], r["sha256"], e))
            progress.done = min(len(todo), i + 16)
            progress.say("מנתח תמונות {done}/{total}", done=progress.done, total=progress.total)
            con.commit()

        progress.say("משייך מילות מפתח…")
        ids, E = _emb_rows(con)
        rejected = {(r["photo_id"], r["tag"]) for r in con.execute("SELECT photo_id, tag FROM autotag_rejected")}
        n_tags = 0
        if len(ids):
            S = E @ vocab_embeddings().T
            mu, sd = label_stats(S)
            Z = (S - mu) / sd
            for k, pid in enumerate(ids.tolist()):
                names = [KEYWORDS[j] for j in pick_keywords(S[k], Z[k])]
                _set_tags(con, pid, names, "auto", rejected); n_tags += len(names)
        for r in con.execute("SELECT id, lat, lng FROM photos WHERE trashed=0 AND lat IS NOT NULL AND lng IS NOT NULL").fetchall():
            _set_tags(con, r["id"], place_keywords(r["lat"], r["lng"]), "place", rejected)
        con.execute("DELETE FROM tags WHERE id NOT IN (SELECT tag_id FROM photo_tags)")
        con.commit()
        progress.state = "done"
        progress.say_parts(("תויגו {n} תמונות", {"n": len(ids)}), ("{n} מילות מפתח", {"n": n_tags}))
    except Exception as e:
        con.commit()
        progress.fail("התיוג האוטומטי נכשל: {error}", error=str(e))


# ---------- smart search + suggestions ----------
_EMB_CACHE = {"key": None}


def _library(con):
    key = con.execute("SELECT COUNT(*), COALESCE(SUM(photo_id),0) FROM clip_emb WHERE emb IS NOT NULL").fetchone()
    key = tuple(key)
    if _EMB_CACHE["key"] != key:
        _EMB_CACHE.update(key=key, data=_emb_rows(con))
    return _EMB_CACHE["data"]


def search(con, query: str, limit: int = 600) -> dict[int, float]:
    """photo_id -> relevance for a free-text English query ("kids on the beach")."""
    if not model_ready():
        return {}
    ids, E = _library(con)
    if not len(ids):
        return {}
    q = _norm(embed_texts([query, f"a photo of {query}"]).mean(0))
    sims = E @ q
    z = (sims - sims.mean()) / max(sims.std(), 1e-3)
    keep = np.where((z >= 2.0) & (sims >= 0.2))[0]
    keep = keep[np.argsort(-sims[keep])][:limit]
    return {int(ids[k]): round(float(sims[k]), 4) for k in keep}


def suggestions(con, photo_id: int, n: int = 9) -> list[str]:
    """Best vocabulary keywords for one photo (for the Keywording panel)."""
    if not model_ready():
        return []
    r = con.execute("SELECT emb FROM clip_emb WHERE photo_id=? AND emb IS NOT NULL", (photo_id,)).fetchone()
    if not r:
        return []
    ids, E = _library(con)
    e = np.frombuffer(r["emb"], np.float16).astype(np.float32)
    V = vocab_embeddings()
    mu, sd = label_stats(E @ V.T)
    z = (V @ e - mu) / sd
    return [KEYWORDS[j] for j in np.argsort(-z)[:n]]
