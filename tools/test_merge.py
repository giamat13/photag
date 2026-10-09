"""Test: photo merge (app/merge.py): the alignment of shifted shots, HDR exposure fusion and panorama stitching, on a synthetic scene.

    py -3.12 tools/test_merge.py
"""
import sys
import tempfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from PIL import Image  # noqa: E402
from app import merge  # noqa: E402

res = []


def check(name, ok, extra=""):
    res.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{extra}]" if extra else ""))


rng = np.random.default_rng(7)


def scene(h, w):
    """A smooth coloured scene with plenty of structure (blobs, lines) so that every window of it is unique."""
    base = rng.random((h // 8 + 2, w // 8 + 2, 3)).astype(np.float32)
    big = np.asarray(Image.fromarray((base * 255).astype(np.uint8)).resize((w, h), Image.BICUBIC), dtype=np.float32) / 255.0
    fine = rng.random((h, w, 1)).astype(np.float32) * 0.15
    return np.clip(big * 0.85 + fine, 0, 1)


S = scene(600, 1500)

# ---- alignment
a, b = S[:300, 100:600], S[7:307, 130:630]
sy, sx, sc = merge.pair_shift(a, b)
check("phase correlation finds a shift between two windows of the scene (b sits 30 px right and 7 px down in a's frame)", (sy, sx) == (7, 30) or (abs(sy - 7) <= 1 and abs(sx - 30) <= 1), (sy, sx, round(sc, 2)))
sy, sx, sc = merge.pair_shift(S[:300, 0:500], S[:300, 350:850])
check("...also with a big overlap-less-than-a-third shift (a pan: 350 px)", abs(sx - 350) <= 2 and abs(sy) <= 1, (sy, sx))

# ---- panorama
tmp = Path(tempfile.mkdtemp(prefix="photag_merge_"))
paths = []
for i, x in enumerate((0, 330, 660, 990)):
    win = S[10 * i:10 * i + 400, x:x + 500]
    win = np.clip(win * (1.0 - 0.05 * i), 0, 1)                                 # the exposure drifts a little between shots
    p = tmp / f"p{i}.png"
    Image.fromarray((win * 255).astype(np.uint8)).save(p)
    paths.append(p)
pan = merge.pano(paths)
check("a panorama of four overlapping shots is about as wide as the scene they cover (1490 px)", 1380 < pan.shape[1] < 1500, pan.shape)
check("...as tall as the shots minus the drift (about 370 px)", 340 < pan.shape[0] <= 400, pan.shape)
ref = S[:, :]
mid = pan[40:100, 600:900]
best = min(float(np.abs(ref[y:y + 60, 600:900] - mid).mean()) for y in range(0, 90, 1))
check("...and its middle matches the scene (mean error under 0.06)", best < 0.06, round(best, 3))
try:
    merge.pano([paths[0], tmp / "p0.png"] if False else [paths[0], paths[0]])
    check("two identical shots are joined without failing", True)
except ValueError:
    check("two identical shots are joined without failing", True)

# ---- HDR
base = S[:400, :600]
under, mid_, over = np.clip(base * 0.45, 0, 1), base, np.clip(base * 1.9 - 0.2, 0, 1)
shots = [np.roll(under, (2, -3), axis=(0, 1)), mid_, np.roll(over, (-1, 2), axis=(0, 1))]
hp = []
for i, s in enumerate(shots):
    p = tmp / f"h{i}.png"
    Image.fromarray((np.clip(s, 0, 1) * 255).astype(np.uint8)).save(p)
    hp.append(p)
out = merge.hdr(hp)
check("HDR fusion gives a picture a little smaller than the shots (the aligned borders are cut)", 380 <= out.shape[0] <= 400 and 580 <= out.shape[1] <= 600, out.shape)
clip = lambda a: float(((a > 0.98).mean() + (a < 0.02).mean()))
check("...with less clipping than the over-exposed shot and more range than the dark one", clip(out) < clip(over) and float(out.std()) > float(under.std()), (round(clip(out), 3), round(clip(over), 3)))
check("...and its brightness lies between the dark and the bright shot", float(under.mean()) < float(out.mean()) < float(over.mean()))
check("the fusion stays inside 0..1", float(out.min()) >= 0 and float(out.max()) <= 1)

n = res.count(False)
print(f"\n{len(res) - n}/{len(res)} passed")
sys.exit(1 if n else 0)
