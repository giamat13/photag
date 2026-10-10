"""Photo merge: HDR (exposure fusion of bracketed shots) and panorama (overlapping shots side by side).

Both work on the pictures as they are (the edits of a photo are included), line them up by phase correlation of their edges (a shift,
no rotation or lens model: handheld bracketing and a level pan work, anything harder does not), and write one JPEG that is added to the
library. Exposure fusion needs no tone-mapping step: every pixel is taken from the shot where it is best exposed, sharp and colourful.
"""
import math

import numpy as np

MAX_HDR, MAX_PANO = 9, 12
WORK = 1800                      # long side the shots are shrunk to before merging (keeps memory in check; a panorama is capped at ~14000 px wide)


def _pil():
    from PIL import Image
    return Image


def load(path, long_side=WORK):
    from . import images
    im = images.open_image(path).convert("RGB")
    s = long_side / max(im.size)
    if s < 1:
        im = im.resize((max(2, round(im.width * s)), max(2, round(im.height * s))), _pil().LANCZOS)
    return np.asarray(im, dtype=np.float32) / 255.0


def _edges(a, n=512):
    """Exposure-robust picture of the structure: gradient magnitude of the gray, normalised, at most n px on the long side."""
    g = a.mean(axis=2)
    Image = _pil()
    s = n / max(g.shape)
    if s < 1:
        g = np.asarray(Image.fromarray(g, "F").resize((max(8, round(g.shape[1] * s)), max(8, round(g.shape[0] * s))), Image.BILINEAR), dtype=np.float32)
    gy, gx = np.gradient(g)
    m = np.sqrt(gx * gx + gy * gy)
    return (m - m.mean()) / (m.std() + 1e-6), (s if s < 1 else 1.0)


def phase_shift(g1, g2):
    """(sy, sx) with g1[n] ~ g2[n - s] (the shift that lines g2 up with g1) and a sharpness score; both arrays the same size."""
    h, w = g1.shape
    win = np.outer(np.hanning(h), np.hanning(w)).astype(np.float32)
    f1, f2 = np.fft.rfft2(g1 * win), np.fft.rfft2(g2 * win)
    r = f1 * np.conj(f2)
    r /= np.abs(r) + 1e-9
    c = np.fft.irfft2(r, s=(h, w))
    py, px = np.unravel_index(int(np.argmax(c)), c.shape)
    score = float(c[py, px] / (np.abs(c).mean() + 1e-9))
    return (py if py <= h // 2 else py - h), (px if px <= w // 2 else px - w), score


def _ncc(a, b):
    a = a - a.mean()
    b = b - b.mean()
    d = np.sqrt((a * a).sum() * (b * b).sum())
    return float((a * b).sum() / d) if d > 1e-9 else -1.0


def _overlap(g1, g2, sy, sx):
    """The parts of g1 and g2 that cover the same scene when g2 sits at (sy, sx) in g1's frame; None when they barely overlap."""
    h, w = g1.shape
    y0, y1, x0, x1 = max(0, sy), min(h, h + sy), max(0, sx), min(w, w + sx)
    if y1 - y0 < h * 0.25 or x1 - x0 < w * 0.1:
        return None
    return g1[y0:y1, x0:x1], g2[y0 - sy:y1 - sy, x0 - sx:x1 - sx]


def pair_shift(a1, a2, max_frac=1.0):
    """Where picture 2 sits in the frame of picture 1: (sy, sx) in pixels of the (shrunk) pictures. Tries the four wrap-arounds of the
    phase correlation and keeps the one whose overlap matches best."""
    e1, k = _edges(a1)
    e2, _ = _edges(a2)
    h, w = e1.shape
    sy, sx, _score = phase_shift(e1, e2[:h, :w] if e2.shape != e1.shape else e2)
    best, bs = -9.0, (0, 0)
    for cy in (sy, sy - h if sy > 0 else sy + h):
        for cx in (sx, sx - w if sx > 0 else sx + w):
            if abs(cx) > w * max_frac or abs(cy) > h * max_frac:
                continue
            ov = _overlap(e1, e2, cy, cx)
            if ov is None:
                continue
            sc = _ncc(*ov)
            if sc > best:
                best, bs = sc, (cy, cx)
    return round(bs[0] / k), round(bs[1] / k), best


# ------------------------------------------------------------------------------------------------ HDR (exposure fusion)
def _lap4(g):
    return np.abs(4 * g - np.roll(g, 1, 0) - np.roll(g, -1, 0) - np.roll(g, 1, 1) - np.roll(g, -1, 1))


def _reduce(a):
    """Half the size: the mean of every 2 x 2 block (an odd last row / column is repeated)."""
    h, w = a.shape[:2]
    if h % 2:
        a = np.concatenate([a, a[-1:]], axis=0)
    if w % 2:
        a = np.concatenate([a, a[:, -1:]], axis=1)
    return (a[0::2, 0::2] + a[1::2, 0::2] + a[0::2, 1::2] + a[1::2, 1::2]) * 0.25


def _expand(a, shape):
    Image = _pil()
    h, w = shape
    out = []
    for c in range(a.shape[2] if a.ndim == 3 else 1):
        ch = a[..., c] if a.ndim == 3 else a
        out.append(np.asarray(Image.fromarray(np.ascontiguousarray(ch, dtype=np.float32), "F").resize((w, h), Image.BILINEAR), dtype=np.float32))
    return np.stack(out, axis=2) if a.ndim == 3 else out[0]


def _gauss_pyr(a, n):
    p = [a]
    for _ in range(n - 1):
        p.append(_reduce(p[-1]))
    return p


def _lap_pyr(a, n):
    g = _gauss_pyr(a, n)
    return [g[i] - _expand(g[i + 1], g[i].shape[:2]) for i in range(n - 1)] + [g[-1]]


def fuse(shots):
    """Exposure fusion (Mertens, Kautz, Van Reeth): contrast x saturation x well-exposedness weights, blended through pyramids."""
    n = len(shots)
    h, w = shots[0].shape[:2]
    levels = int(max(2, min(7, np.log2(min(h, w)) - 2)))
    ws = []
    for a in shots:
        c = _lap4(a.mean(axis=2)) + 1e-6
        s = a.std(axis=2) + 1e-6
        e = np.prod(np.exp(-((a - 0.5) ** 2) / (2 * 0.2 ** 2)), axis=2) + 1e-6
        ws.append(c * s * e)
    tot = np.sum(ws, axis=0)
    ws = [x / tot for x in ws]
    acc = None
    for a, wt in zip(shots, ws):
        lp = _lap_pyr(a, levels)
        gp = _gauss_pyr(wt, levels)
        cur = [l * g[..., None] for l, g in zip(lp, gp)]
        acc = cur if acc is None else [x + y for x, y in zip(acc, cur)]
    out = acc[-1]
    for i in range(levels - 2, -1, -1):
        out = acc[i] + _expand(out, acc[i].shape[:2])
    return np.clip(out, 0, 1)


def hdr(paths, progress=None):
    shots = [load(p) for p in paths]
    h, w = min(s.shape[0] for s in shots), min(s.shape[1] for s in shots)
    shots = [s[:h, :w] for s in shots]
    ref = sorted(range(len(shots)), key=lambda i: float(shots[i].mean()))[len(shots) // 2]
    shifts = []
    for i, s in enumerate(shots):
        if i == ref:
            shifts.append((0, 0))
            continue
        sy, sx, sc = pair_shift(shots[ref], s, max_frac=0.06)
        shifts.append((sy, sx) if sc > 0.15 else (0, 0))
    aligned = [np.roll(s, (sy, sx), axis=(0, 1)) for s, (sy, sx) in zip(shots, shifts)]
    my = max([abs(sy) for sy, _ in shifts] + [0])
    mx = max([abs(sx) for _, sx in shifts] + [0])
    if my or mx:
        aligned = [a[my:h - my or None, mx:w - mx or None] for a in aligned]
    return fuse(aligned)


# ------------------------------------------------------------------------------------------------ panorama
def pano(paths, progress=None):
    shots = [load(p, WORK) for p in paths]
    h, w = shots[0].shape[:2]
    shots = [s[:h, :w] if s.shape[:2] != (h, w) else s for s in shots]
    pos = [(0, 0)]
    gains = [1.0]
    for i in range(1, len(shots)):
        sy, sx, sc = pair_shift(shots[i - 1], shots[i])
        if sc < 0.1:
            raise ValueError("The pictures do not overlap enough to be joined")
        pos.append((pos[-1][0] + sy, pos[-1][1] + sx))
        ov = _overlap(shots[i - 1].mean(axis=2), shots[i].mean(axis=2), sy, sx)
        g = float(ov[0].mean() / max(ov[1].mean(), 1e-4)) if ov else 1.0
        gains.append(gains[-1] * float(np.clip(g, 0.7, 1.4)))
    ys, xs = [p[0] for p in pos], [p[1] for p in pos]
    y0, x0 = min(ys), min(xs)
    H, W = max(ys) - y0 + h, max(xs) - x0 + w
    if W > 16000 or H > 16000:
        raise ValueError("The panorama would be too big")
    acc = np.zeros((H, W, 3), dtype=np.float32)
    wsum = np.zeros((H, W), dtype=np.float32)
    ramp = np.minimum(np.arange(w) + 1, np.arange(w)[::-1] + 1).astype(np.float32)
    rampy = np.minimum(np.arange(h) + 1, np.arange(h)[::-1] + 1).astype(np.float32)
    weight = np.minimum(ramp[None, :], rampy[:, None]) ** 2
    for s, (py, px), g in zip(shots, pos, gains):
        yy, xx = py - y0, px - x0
        acc[yy:yy + h, xx:xx + w] += s * g * weight[..., None]
        wsum[yy:yy + h, xx:xx + w] += weight
    out = acc / np.maximum(wsum, 1e-6)[..., None]
    covered = wsum > 0
    full_rows = np.where(covered.mean(axis=1) > 0.995)[0]
    full_cols = np.where(covered[full_rows[0]:full_rows[-1] + 1].mean(axis=0) > 0.995)[0] if len(full_rows) else []
    if len(full_rows) and len(full_cols):
        out = out[full_rows[0]:full_rows[-1] + 1, full_cols[0]:full_cols[-1] + 1]
    return np.clip(out, 0, 1)


MAX_COLLAGE = 12


def collage(paths, cell: int = 900, gap: int = 12):
    """The photos in a tidy grid (as square as it gets, each cropped to fill its cell, white gaps). Only reads the photos."""
    from PIL import Image, ImageOps
    n = len(paths)
    cols = math.ceil(math.sqrt(n))
    rows = math.ceil(n / cols)
    sheet = Image.new("RGB", (cols * cell + (cols + 1) * gap, rows * cell + (rows + 1) * gap), (255, 255, 255))
    for i, p in enumerate(paths):
        with Image.open(p) as im:
            im = ImageOps.exif_transpose(im).convert("RGB")
            im = ImageOps.fit(im, (cell, cell), Image.LANCZOS)
        r, c = divmod(i, cols)
        sheet.paste(im, (gap + c * (cell + gap), gap + r * (cell + gap)))
    return np.asarray(sheet, dtype=np.float32) / 255.0


def run(ids, kind, progress):
    """Background job (see server._start): merge the chosen photos and add the result to the library."""
    import tempfile
    from pathlib import Path
    from PIL import Image
    from . import db, importer, render
    progress.state = "exporting"
    try:
        con = db.connect()
        paths, names = [], []
        for pid in ids:
            r = con.execute("SELECT * FROM photos WHERE id=?", (pid,)).fetchone()
            if r and not r["is_video"]:
                p = render.current_path(r)
                if p and Path(p).exists():
                    paths.append(Path(p))
                    names.append(r["filename"])
        lo, hi = (2, MAX_HDR) if kind == "hdr" else (2, MAX_COLLAGE) if kind == "collage" else (2, MAX_PANO)
        if len(paths) < lo:
            progress.fail("Choose at least two photos to merge")
            return
        if len(paths) > hi:
            progress.fail("At most {n} photos can be merged at once", n=hi)
            return
        progress.total, progress.done = 3, 0
        progress.say("Lining the photos up…")
        progress.done = 1
        try:
            out = hdr(paths, progress) if kind == "hdr" else collage(paths) if kind == "collage" else pano(paths, progress)
        except ValueError as e:
            progress.fail("The photos could not be merged: {why}", why=str(e))
            return
        progress.done = 2
        progress.say("Adding the result to the library…")
        with tempfile.TemporaryDirectory(prefix="photag-merge-") as td:
            stem = Path(names[0]).stem
            f = Path(td) / f"{stem}-{ {'hdr': 'HDR', 'collage': 'Collage'}.get(kind, 'Pano') }.jpg"
            Image.fromarray((np.clip(out, 0, 1) * 255 + 0.5).astype(np.uint8)).save(f, "JPEG", quality=94)
            taken = con.execute("SELECT taken_at FROM photos WHERE id=?", (ids[0],)).fetchone()
            pid, _new = importer._ingest_file(con, f, taken=taken["taken_at"] if taken else None)
            con.commit()
        progress.done = 3
        progress.result = {"id": pid}
        progress.say("Done")
        progress.state = "done"
    except Exception as e:                                                    # never leave the job hanging
        progress.fail("The photos could not be merged: {why}", why=str(e)[:200])
