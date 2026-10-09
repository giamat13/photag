"""Develop tools that work on places and shapes (issue #6, batch 2-4): lens corrections, geometry (perspective), spot removal, red eye,
colour look-up tables (profiles) and local adjustments with masks. images.apply_tone() calls them in a fixed order; every position is
written as a fraction of the picture (0..1) and every radius as a fraction of its longer side, so a small preview looks like the
full-size render.

Settings (all optional):
  lens_dist -100..100, lens_vig -100..100, ca_r / ca_b -100..100 (manual lateral chromatic aberration), ca_auto true, defringe 0..100
  persp_v / persp_h -100..100, geo_aspect -100..100, geo_scale 100..200 (default 100), geo_x / geo_y -100..100
  spots:   [{"x","y","r","sx","sy","mode":"heal"|"clone","feather":0..100,"opacity":0..100}]   (sx / sy missing = the source is chosen for you)
  redeye:  [{"x","y","r","amount":0..100}]
  lut:     {"name": "<file in the LUT folder>", "amount": 0..100}
  masks:   [{"name","amount":0..100,"adj":{temp,tint,exposure,contrast,highlights,shadows,whites,blacks,texture,clarity,dehaze,hue,saturation,sharpness,noise},
             "comps":[{"kind":"brush"|"linear"|"radial"|"luminance"|"color"|"sky"|"subject"|"background","op":"add"|"subtract"|"intersect","invert":bool, ...}]}]
"""
import numpy as np

MAX_MASKS, MAX_COMPS, MAX_STROKES, MAX_POINTS, MAX_SPOTS = 8, 6, 40, 400, 60
LOCAL_ADJ = ("temp", "tint", "exposure", "contrast", "highlights", "shadows", "whites", "blacks", "texture", "clarity", "dehaze", "hue", "saturation", "sharpness", "noise")
NUM_KEYS = {"lens_dist": (-100, 100), "lens_vig": (-100, 100), "ca_r": (-100, 100), "ca_b": (-100, 100), "defringe": (0, 100),
            "persp_v": (-100, 100), "persp_h": (-100, 100), "geo_aspect": (-100, 100), "geo_scale": (100, 200), "geo_x": (-100, 100), "geo_y": (-100, 100)}
LIST_KEYS = ("masks", "spots", "redeye", "lut")


def _smooth(x, a, b):
    t = np.clip((x - a) / (b - a), 0.0, 1.0)
    return t * t * (3 - 2 * t)


def _lum(a):
    return a[..., 0] * 0.2126 + a[..., 1] * 0.7152 + a[..., 2] * 0.0722


def _num(v, lo, hi, default=0.0):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return default
    if x != x:
        return default
    return max(lo, min(hi, x))


def _pil(a):
    from PIL import Image
    return Image.fromarray((np.clip(a, 0, 1) * 255.0 + 0.5).astype(np.uint8), "RGB")


def _arr(im):
    return np.asarray(im.convert("RGB"), dtype=np.float32) / 255.0


# ------------------------------------------------------------------------------------------------ what is active / accepted
def _masks_active(m):
    return isinstance(m, list) and any(isinstance(x, dict) and x.get("comps") and any(abs(float(v or 0)) > 0 for v in (x.get("adj") or {}).values()) for x in m)


def active(ops: dict) -> bool:
    if any(ops.get(k) not in (None, 0, 0.0) for k in NUM_KEYS if k != "geo_scale") or ops.get("ca_auto"):
        return True
    if ops.get("geo_scale") not in (None, 100, 100.0):
        return True
    if ops.get("spots") or ops.get("redeye"):
        return True
    lut = ops.get("lut")
    if isinstance(lut, dict) and lut.get("name") and float(lut.get("amount", 100) or 0) > 0:
        return True
    return _masks_active(ops.get("masks"))


def _pt(p):
    return [round(_num(p[0], 0, 1), 4), round(_num(p[1], 0, 1), 4)] if isinstance(p, (list, tuple)) and len(p) >= 2 else None


def clean(ops: dict) -> dict:
    """The settings of this module from `ops`, clamped; unknown names, shapes and anything over the limits are dropped."""
    out = {}
    for k, (lo, hi) in NUM_KEYS.items():
        if ops.get(k) is not None:
            out[k] = _num(ops[k], lo, hi, 100.0 if k == "geo_scale" else 0.0)
    if ops.get("ca_auto"):
        out["ca_auto"] = True
    sp = []
    for s in (ops.get("spots") or [])[:MAX_SPOTS]:
        if not isinstance(s, dict):
            continue
        d = {"x": _num(s.get("x"), 0, 1), "y": _num(s.get("y"), 0, 1), "r": _num(s.get("r"), 0.003, 0.25, 0.02),
             "mode": "clone" if s.get("mode") == "clone" else "heal", "feather": _num(s.get("feather"), 0, 100, 50), "opacity": _num(s.get("opacity"), 0, 100, 100)}
        if s.get("sx") is not None and s.get("sy") is not None:
            d["sx"], d["sy"] = _num(s["sx"], 0, 1), _num(s["sy"], 0, 1)
        sp.append(d)
    if sp:
        out["spots"] = sp
    re_ = [{"x": _num(s.get("x"), 0, 1), "y": _num(s.get("y"), 0, 1), "r": _num(s.get("r"), 0.003, 0.2, 0.02), "amount": _num(s.get("amount"), 0, 100, 100)}
           for s in (ops.get("redeye") or [])[:MAX_SPOTS] if isinstance(s, dict)]
    if re_:
        out["redeye"] = re_
    lut = ops.get("lut")
    if isinstance(lut, dict) and isinstance(lut.get("name"), str) and lut["name"]:
        name = lut["name"].replace("\\", "/").split("/")[-1][:80]
        if name and not name.startswith("."):
            out["lut"] = {"name": name, "amount": _num(lut.get("amount"), 0, 100, 100)}
    ms = []
    for m in (ops.get("masks") or [])[:MAX_MASKS]:
        if not isinstance(m, dict):
            continue
        comps = []
        for c in (m.get("comps") or [])[:MAX_COMPS]:
            cc = _clean_comp(c)
            if cc:
                comps.append(cc)
        adj = {k: _num((m.get("adj") or {}).get(k), -100, 100) for k in LOCAL_ADJ if (m.get("adj") or {}).get(k) is not None}
        if "exposure" in adj:
            adj["exposure"] = _num((m.get("adj") or {}).get("exposure"), -4, 4)
        ms.append({"name": str(m.get("name") or "")[:40], "amount": _num(m.get("amount"), 0, 100, 100), "adj": adj, "comps": comps})
    if ms:
        out["masks"] = ms
    return out


def _clean_comp(c):
    if not isinstance(c, dict):
        return None
    k = c.get("kind")
    d = {"kind": k, "op": c.get("op") if c.get("op") in ("add", "subtract", "intersect") else "add", "invert": bool(c.get("invert"))}
    if k == "linear":
        d.update({q: _num(c.get(q), 0, 1) for q in ("x1", "y1", "x2", "y2")})
    elif k == "radial":
        d.update({"cx": _num(c.get("cx"), 0, 1, .5), "cy": _num(c.get("cy"), 0, 1, .5), "rx": _num(c.get("rx"), 0.01, 1, .2), "ry": _num(c.get("ry"), 0.01, 1, .2),
                  "rot": _num(c.get("rot"), -180, 180), "feather": _num(c.get("feather"), 0, 100, 50)})
    elif k == "luminance":
        d.update({"lo": _num(c.get("lo"), 0, 1), "hi": _num(c.get("hi"), 0, 1, 1), "smooth": _num(c.get("smooth"), 0, 100, 40)})
    elif k == "color":
        col = c.get("color")
        d.update({"color": [_num(v, 0, 255) for v in (col if isinstance(col, (list, tuple)) and len(col) >= 3 else [128, 128, 128])[:3]], "tol": _num(c.get("tol"), 0, 100, 40)})
    elif k == "brush":
        st = []
        for s in (c.get("strokes") or [])[:MAX_STROKES]:
            if not isinstance(s, dict):
                continue
            pts = [p for p in (_pt(p) for p in (s.get("pts") or [])[:MAX_POINTS]) if p]
            if pts:
                st.append({"pts": pts, "size": _num(s.get("size"), 0.002, 0.5, 0.05), "feather": _num(s.get("feather"), 0, 100, 50), "flow": _num(s.get("flow"), 1, 100, 100),
                           "erase": bool(s.get("erase"))})
        d["strokes"] = st
    elif k in ("sky", "subject", "background"):
        pass
    else:
        return None
    return d


# ------------------------------------------------------------------------------------------------ lens corrections
def distort(im, amount):
    """Barrel (< 0) / pincushion (> 0) correction: the corners stay where they are, the middle of the edges moves."""
    from PIL import Image
    w, h = im.size
    k = float(amount) / 100.0 * 0.22
    n = 24
    xs = [int(round(v)) for v in np.linspace(0, w, n + 1)]
    ys = [int(round(v)) for v in np.linspace(0, h, n + 1)]

    def src(x, y):
        u, v = (x - w / 2) / (w / 2), (y - h / 2) / (h / 2)
        f = (1 + k * (u * u + v * v) / 2) / (1 + k)
        return (min(max(w / 2 + u * f * w / 2, 0), w), min(max(h / 2 + v * f * h / 2, 0), h))
    mesh = []
    for j in range(n):
        for i in range(n):
            x0, x1, y0, y1 = xs[i], xs[i + 1], ys[j], ys[j + 1]
            mesh.append(((x0, y0, x1, y1), (*src(x0, y0), *src(x0, y1), *src(x1, y1), *src(x1, y0))))
    return im.transform(im.size, Image.MESH, mesh, Image.BICUBIC)


def _scale_channel(im, band, s):
    """Radial scale of one colour channel about the centre (lateral chromatic aberration)."""
    from PIL import Image
    w, h = im.size
    chans = list(im.split())
    cx, cy = w / 2, h / 2
    chans[band] = chans[band].transform(im.size, Image.AFFINE, (1 / s, 0, cx * (1 - 1 / s), 0, 1 / s, cy * (1 - 1 / s)), Image.BICUBIC)
    return Image.merge("RGB", chans)


def _grad(img):
    a = np.asarray(img, dtype=np.float32) / 255.0
    gy, gx = np.gradient(a)
    return np.sqrt(gx * gx + gy * gy)


def estimate_ca(im):
    """The radial scale of the red and blue channels (relative to green) that lines their edges up with the green ones."""
    from PIL import Image
    sm = im.copy()
    sm.thumbnail((480, 480))
    r, g, b = sm.split()
    gg = _grad(g)
    gg = gg - gg.mean()
    out = []
    for band in (r, b):
        best, bs = -2.0, 1.0
        for s in np.linspace(0.994, 1.006, 25):
            w, h = band.size
            sc = band.transform(band.size, Image.AFFINE, (1 / s, 0, w / 2 * (1 - 1 / s), 0, 1 / s, h / 2 * (1 - 1 / s)), Image.BICUBIC)
            gb = _grad(sc)
            gb = gb - gb.mean()
            c = float((gb * gg).sum() / (np.sqrt((gb * gb).sum() * (gg * gg).sum()) + 1e-9))
            if c > best:
                best, bs = c, float(s)
        out.append(bs)
    return out[0], out[1]


def defringe(a, amount):
    """Purple and green fringes along contrasty edges are drained of colour."""
    from PIL import ImageFilter, Image
    from . import develop_ops as D
    lum = (np.clip(_lum(a), 0, 1) * 255).astype(np.uint8)
    im = Image.fromarray(lum, "L")
    contrast = (np.asarray(im.filter(ImageFilter.MaxFilter(5)), dtype=np.float32) - np.asarray(im.filter(ImageFilter.MinFilter(5)), dtype=np.float32)) / 255.0
    edge = _smooth(contrast, 0.08, 0.35)
    edge = np.asarray(Image.fromarray((edge * 255).astype(np.uint8), "L").filter(ImageFilter.MaxFilter(3)).filter(ImageFilter.GaussianBlur(1.2)), dtype=np.float32) / 255.0
    h, s, v = D.rgb_to_hsv(np.clip(a, 0, 1))
    purple = _smooth(h, 245, 275) * (1 - _smooth(h, 320, 340))
    green = _smooth(h, 70, 90) * (1 - _smooth(h, 150, 175))
    w = np.clip(purple + 0.6 * green, 0, 1) * edge * _smooth(s, 0.15, 0.5) * (amount / 100.0)
    return D.hsv_to_rgb(h, s * (1 - w), v)


def vignette_gain(a, amount):
    """Lens vignetting: positive brightens the corners, negative darkens them."""
    h, w = a.shape[:2]
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    r2 = (((xx - w / 2) / (w / 2)) ** 2 + ((yy - h / 2) / (h / 2)) ** 2) / 2
    return a * (1 + float(amount) / 100.0 * 0.8 * r2)[..., None]


def optics(im, ops):
    """PIL stage: distortion and chromatic aberration."""
    g = lambda k: float(ops.get(k) or 0)
    if g("lens_dist"):
        im = distort(im, g("lens_dist"))
    sr = sb = 1.0
    if ops.get("ca_auto"):
        sr, sb = estimate_ca(im)
    sr *= 1 + g("ca_r") / 100.0 * 0.004
    sb *= 1 + g("ca_b") / 100.0 * 0.004
    if abs(sr - 1) > 1e-5:
        im = _scale_channel(im, 0, sr)
    if abs(sb - 1) > 1e-5:
        im = _scale_channel(im, 2, sb)
    return im


# ------------------------------------------------------------------------------------------------ geometry
def _homography(ops):
    g = lambda k, d=0.0: float(ops.get(k) if ops.get(k) is not None else d)
    asp = g("geo_aspect") / 100.0 * 0.25
    zoom = g("geo_scale", 100.0) / 100.0
    gv, hv = g("persp_h") / 100.0 * 0.35, g("persp_v") / 100.0 * 0.35

    def build(z, ox, oy):
        return np.array([[1 / (z * (1 + asp)), 0, ox], [0, (1 + asp) / z, oy], [gv, hv, 1.0]])
    z = zoom
    fit = bool(gv or hv or asp)
    if fit:
        for _ in range(80):
            m = build(z, 0, 0)
            ok = True
            for cx, cy in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
                p = m @ np.array([cx, cy, 1.0])
                if p[2] <= 0.05 or abs(p[0] / p[2]) > 1.0001 or abs(p[1] / p[2]) > 1.0001:
                    ok = False
                    break
            if ok:
                break
            z *= 1.02
    ox = -g("geo_x") / 100.0 * 0.5
    oy = -g("geo_y") / 100.0 * 0.5
    m = build(z, 0, 0)
    for _ in range(40):                                                                  # keep the picture covering the frame whatever the offset
        mm = build(z, ox, oy)
        bad = False
        for cx, cy in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
            p = mm @ np.array([cx, cy, 1.0])
            if p[2] <= 0.05 or abs(p[0] / p[2]) > 1.0001 or abs(p[1] / p[2]) > 1.0001:
                bad = True
        if not bad:
            break
        ox *= 0.9
        oy *= 0.9
    return build(z, ox, oy)


def geometry(im, ops):
    """Vertical / horizontal perspective, aspect, scale (zoom in) and offset. The picture always keeps covering the frame."""
    from PIL import Image
    if not any(ops.get(k) not in (None, 0, 0.0) for k in ("persp_v", "persp_h", "geo_aspect", "geo_x", "geo_y")) and ops.get("geo_scale") in (None, 100, 100.0):
        return im
    w, h = im.size
    hn = _homography(ops)
    s = np.array([[w / 2, 0, w / 2], [0, h / 2, h / 2], [0, 0, 1.0]])
    m = s @ hn @ np.linalg.inv(s)
    m = m / m[2, 2]
    return im.transform(im.size, Image.PERSPECTIVE, tuple(m.ravel()[:8]), Image.BICUBIC)


# ------------------------------------------------------------------------------------------------ upright (what the picture needs)
def _lean(gray, kind):
    """Mean lean (radians) of the near-vertical ('v') or near-horizontal ('h') edges of a float gray picture, and how much evidence there is."""
    gy, gx = np.gradient(gray)
    mag = np.sqrt(gx * gx + gy * gy)
    th = np.arctan2(gy, gx)
    d = ((th + np.pi / 2) % np.pi) - np.pi / 2 if kind == "v" else (th % np.pi) - np.pi / 2
    sel = (np.abs(d) < np.radians(20)) & (mag > max(float(np.percentile(mag, 90)), 1e-3))
    if sel.sum() < 30:
        return 0.0, 0.0
    wgt = mag[sel]
    return float((d[sel] * wgt).sum() / wgt.sum()), float(wgt.sum())


def upright(im, mode="auto"):
    """Suggested rotate (degrees), persp_v and persp_h for a picture: 'level' straightens the horizon / verticals, 'vertical' also
    removes converging verticals, 'full' also converging horizontals, 'auto' picks the smallest change that makes the lines true."""
    from PIL import Image
    sm = im.convert("RGB")
    sm.thumbnail((360, 360))

    def score(ops, rot=0.0, halves=False):
        t = geometry(sm, ops)
        if rot:
            t = t.rotate(rot, resample=Image.BICUBIC)
        g = np.asarray(t.convert("L"), dtype=np.float32) / 255.0
        h, w = g.shape
        g = g[int(h * .08):int(h * .92), int(w * .08):int(w * .92)]
        h, w = g.shape
        if halves:
            lv, le = _lean(g[:, :w // 2], "v")
            rv, re_ = _lean(g[:, w // 2:], "v")
            th, te = _lean(g[:h // 2], "h")
            bh, be = _lean(g[h // 2:], "h")
            return abs(lv - rv) * min(le, re_) ** .5 * 0 + abs(lv - rv), abs(th - bh)
        v, ve = _lean(g, "v")
        hh, he = _lean(g, "h")
        return abs(v) * ve ** .5 + abs(hh) * he ** .5, 0

    out = {"rotate": 0.0, "persp_v": 0.0, "persp_h": 0.0}
    best = min(np.linspace(-8, 8, 33), key=lambda r: score({}, r)[0])
    base = score({}, 0)[0]
    if score({}, best)[0] < base * 0.98:
        out["rotate"] = -float(best)
    if mode in ("vertical", "full", "auto"):
        cand = np.linspace(-60, 60, 25)
        bv = min(cand, key=lambda p: score({"persp_v": p}, 0, True)[0])
        if score({"persp_v": bv}, 0, True)[0] < score({}, 0, True)[0] * 0.8:
            out["persp_v"] = float(bv)
    if mode in ("full", "auto"):
        cand = np.linspace(-60, 60, 25)
        bh_ = min(cand, key=lambda p: score({"persp_h": p}, 0, True)[1])
        if score({"persp_h": bh_}, 0, True)[1] < score({}, 0, True)[1] * 0.8:
            out["persp_h"] = float(bh_)
    if mode == "level":
        out["persp_v"] = out["persp_h"] = 0.0
    return {k: round(v, 1) for k, v in out.items()}


# ------------------------------------------------------------------------------------------------ spots, red eye
def _disc(h, w, cx, cy, r, feather):
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    d = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2) / max(r, 1.0)
    f = max(feather / 100.0, 0.05)
    return 1.0 - _smooth(d, 1.0 - f, 1.0)


def _auto_source(a, cx, cy, r):
    h, w = a.shape[:2]
    best, bp = 1e9, None
    ring = _ring(a, cx, cy, r)
    for k in range(16):
        ang = k * np.pi / 8
        for dist in (2.6, 4.0):
            sx, sy = cx + np.cos(ang) * r * dist, cy + np.sin(ang) * r * dist
            if sx - r < 0 or sy - r < 0 or sx + r >= w or sy + r >= h:
                continue
            c = float(np.abs(_ring(a, sx, sy, r) - ring).mean())
            c += float(a[int(sy - r):int(sy + r) + 1, int(sx - r):int(sx + r) + 1].std()) * 0.3
            if c < best:
                best, bp = c, (sx, sy)
    return bp


def _ring(a, cx, cy, r):
    h, w = a.shape[:2]
    x0, x1, y0, y1 = int(max(cx - 1.6 * r, 0)), int(min(cx + 1.6 * r, w - 1)), int(max(cy - 1.6 * r, 0)), int(min(cy + 1.6 * r, h - 1))
    sub = a[y0:y1 + 1, x0:x1 + 1]
    yy, xx = np.mgrid[y0:y1 + 1, x0:x1 + 1].astype(np.float32)
    d = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2)
    m = (d > r * 1.05) & (d < r * 1.6)
    if not m.any():
        return np.zeros(3, dtype=np.float32)
    return sub[m].mean(axis=0)


def spots(a, items):
    """Remove spots: each target disc is replaced by a disc copied from the source (heal: with the colour of the surroundings, clone: as it is)."""
    h, w = a.shape[:2]
    big = max(h, w)
    out = a.copy()
    for s in items:
        r = max(2.0, float(s["r"]) * big)
        cx, cy = float(s["x"]) * w, float(s["y"]) * h
        if "sx" in s:
            sx, sy = float(s["sx"]) * w, float(s["sy"]) * h
        else:
            src = _auto_source(out, cx, cy, r)
            if not src:
                continue
            sx, sy = src
        ir = int(np.ceil(r * 1.2))
        x0, y0 = int(round(cx)) - ir, int(round(cy)) - ir
        sx0, sy0 = int(round(sx)) - ir, int(round(sy)) - ir
        if min(x0, y0, sx0, sy0) < 0 or max(x0, sx0) + 2 * ir + 1 > w or max(y0, sy0) + 2 * ir + 1 > h:
            x0c, y0c = max(x0, 0), max(y0, 0)
            sx0 = sx0 + (x0c - x0)
            sy0 = sy0 + (y0c - y0)
            x0, y0 = x0c, y0c
            n = min(2 * ir + 1, w - max(x0, sx0), h - max(y0, sy0))
            if n < 3 or sx0 < 0 or sy0 < 0:
                continue
        else:
            n = 2 * ir + 1
        patch = out[sy0:sy0 + n, sx0:sx0 + n].copy()
        if s.get("mode") != "clone":
            patch = patch + (_ring(out, cx, cy, r) - _ring(out, sx, sy, r))
        m = _disc(n, n, cx - x0, cy - y0, r, float(s.get("feather", 50)))[..., None] * (float(s.get("opacity", 100)) / 100.0)
        tgt = out[y0:y0 + n, x0:x0 + n]
        out[y0:y0 + n, x0:x0 + n] = tgt + (patch - tgt) * m
    return out


def redeye(a, items):
    """Red pupils: where red clearly dominates inside the disc, it is replaced by a dark neutral."""
    h, w = a.shape[:2]
    out = a.copy()
    for s in items:
        r = max(2.0, float(s["r"]) * max(h, w))
        x0, x1, y0, y1 = int(max(s["x"] * w - r * 1.3, 0)), int(min(s["x"] * w + r * 1.3, w)), int(max(s["y"] * h - r * 1.3, 0)), int(min(s["y"] * h + r * 1.3, h))
        if x1 - x0 < 2 or y1 - y0 < 2:
            continue
        sub = out[y0:y1, x0:x1]
        m = _disc(y1 - y0, x1 - x0, s["x"] * w - x0, s["y"] * h - y0, r, 40)
        red = np.clip((sub[..., 0] - np.maximum(sub[..., 1], sub[..., 2]) * 1.35) * 4.0, 0, 1)
        k = (m * red * (float(s.get("amount", 100)) / 100.0))[..., None]
        neutral = np.minimum((sub[..., 1:2] + sub[..., 2:3]) / 2.0, sub[..., 0:1]) * 0.55
        out[y0:y1, x0:x1] = sub + (np.repeat(neutral, 3, axis=2) - sub) * k
    return out


# ------------------------------------------------------------------------------------------------ colour look-up tables (.cube)
def lut_dir():
    from .config import PATHS
    return PATHS.root / "luts"


def parse_cube(text):
    """A .cube file -> (size, array [b][g][r][3] floats 0..1). Raises ValueError for anything else."""
    size, rows = None, []
    dmin, dmax = np.zeros(3), np.ones(3)
    for line in text.splitlines():
        line = line.split("#")[0].strip()
        if not line:
            continue
        up = line.upper()
        if up.startswith("LUT_3D_SIZE"):
            size = int(line.split()[1])
        elif up.startswith("DOMAIN_MIN"):
            dmin = np.array([float(x) for x in line.split()[1:4]])
        elif up.startswith("DOMAIN_MAX"):
            dmax = np.array([float(x) for x in line.split()[1:4]])
        elif up.startswith(("TITLE", "LUT_1D", "LUT_3D_INPUT", "LUT_3D_OUTPUT")):
            if up.startswith("LUT_1D"):
                raise ValueError("1D LUTs are not supported")
        else:
            parts = line.split()
            if len(parts) == 3:
                rows.append([float(x) for x in parts])
    if not size or size < 2 or size > 65 or len(rows) != size ** 3:
        raise ValueError("not a valid 3D .cube file")
    arr = (np.array(rows, dtype=np.float32) - dmin) / np.maximum(dmax - dmin, 1e-6)
    return size, arr.reshape(size, size, size, 3)


_LUT_CACHE: dict = {}


def load_lut(name):
    p = lut_dir() / name
    try:
        st = p.stat()
    except OSError:
        return None
    key = (str(p), st.st_mtime_ns)
    if key not in _LUT_CACHE:
        try:
            _LUT_CACHE.clear()
            _LUT_CACHE[key] = parse_cube(p.read_text("utf-8", errors="replace"))
        except (ValueError, OSError):
            return None
    return _LUT_CACHE[key]


def apply_lut(a, size, table, amount):
    """Trilinear lookup. table[b, g, r] as in the .cube order (red varies fastest)."""
    x = np.clip(a, 0, 1) * (size - 1)
    i0 = np.floor(x).astype(int)
    i1 = np.minimum(i0 + 1, size - 1)
    f = x - i0
    out = 0
    for cb in (0, 1):
        for cg in (0, 1):
            for cr in (0, 1):
                ib = (i1 if cb else i0)[..., 2]
                ig = (i1 if cg else i0)[..., 1]
                ir = (i1 if cr else i0)[..., 0]
                w = (f[..., 2] if cb else 1 - f[..., 2]) * (f[..., 1] if cg else 1 - f[..., 1]) * (f[..., 0] if cr else 1 - f[..., 0])
                out = out + table[ib, ig, ir] * w[..., None]
    k = float(amount) / 100.0
    return a + (out - a) * k


def lut(a, spec):
    got = load_lut(spec["name"]) if isinstance(spec, dict) and spec.get("name") else None
    if not got:
        return a
    return apply_lut(a, got[0], got[1], spec.get("amount", 100))


# ------------------------------------------------------------------------------------------------ masks
def _fit_small(a, n=360):
    """A small copy of the picture for the guesses (sky, subject)."""
    from PIL import Image
    h, w = a.shape[:2]
    s = n / max(h, w)
    if s >= 1:
        return a
    return np.asarray(Image.fromarray((np.clip(a, 0, 1) * 255).astype(np.uint8), "RGB").resize((max(2, int(w * s)), max(2, int(h * s))), Image.BILINEAR), dtype=np.float32) / 255.0


def _resize_mask(m, h, w):
    from PIL import Image
    if m.shape == (h, w):
        return m
    return np.asarray(Image.fromarray((np.clip(m, 0, 1) * 255).astype(np.uint8), "L").resize((w, h), Image.BICUBIC), dtype=np.float32) / 255.0


def sky_mask(a):
    s = _fit_small(a)
    h, w = s.shape[:2]
    yy = np.linspace(0, 1, h, dtype=np.float32)[:, None]
    top = 1 - _smooth(yy, 0.25, 0.85)
    lum = _lum(s)
    blue = _smooth(s[..., 2] - np.maximum(s[..., 0], s[..., 1]), 0.02, 0.18)
    cloud = _smooth(lum, 0.6, 0.85) * (1 - _smooth(s.max(axis=2) - s.min(axis=2), 0.05, 0.2))
    gy, gx = np.gradient(lum)
    flat = 1 - _smooth(np.sqrt(gx * gx + gy * gy), 0.01, 0.06)
    m = np.clip((blue + 0.8 * cloud) * flat * (0.25 + 0.75 * top), 0, 1) * (top > 0.02)
    from PIL import Image, ImageFilter
    m = np.asarray(Image.fromarray((m * 255).astype(np.uint8), "L").filter(ImageFilter.GaussianBlur(max(2, w / 60))), dtype=np.float32) / 255.0
    return _smooth(m, 0.15, 0.55)


def subject_mask(a):
    from PIL import Image, ImageFilter
    s = _fit_small(a)
    h, w = s.shape[:2]
    border = np.concatenate([s[:max(1, h // 10)].reshape(-1, 3), s[-max(1, h // 10):].reshape(-1, 3), s[:, :max(1, w // 10)].reshape(-1, 3), s[:, -max(1, w // 10):].reshape(-1, 3)]).mean(axis=0)
    dist = np.sqrt(((s - border) ** 2).sum(axis=2))
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    centre = np.exp(-(((xx - w / 2) / (w * 0.45)) ** 2 + ((yy - h / 2) / (h * 0.45)) ** 2))
    m = dist / max(float(np.percentile(dist, 98)), 1e-3) * (0.4 + 0.6 * centre)
    m = np.asarray(Image.fromarray((np.clip(m, 0, 1) * 255).astype(np.uint8), "L").filter(ImageFilter.GaussianBlur(max(2, w / 50))), dtype=np.float32) / 255.0
    return _smooth(m, 0.25, 0.6)


def _brush(c, h, w):
    from PIL import Image, ImageDraw, ImageChops, ImageFilter
    sc = min(1.0, 1024.0 / max(h, w))
    mh, mw = max(2, int(h * sc)), max(2, int(w * sc))
    big = max(mh, mw)
    m = Image.new("L", (mw, mh), 0)
    for s in c.get("strokes", []):
        layer = Image.new("L", (mw, mh), 0)
        d = ImageDraw.Draw(layer)
        rad = max(1.0, s["size"] * big / 2)
        fill = int(255 * s["flow"] / 100.0)
        pts = [(p[0] * mw, p[1] * mh) for p in s["pts"]]
        for i, p in enumerate(pts):
            if i:
                d.line([pts[i - 1], p], fill=fill, width=max(1, int(rad * 2)))
            d.ellipse([p[0] - rad, p[1] - rad, p[0] + rad, p[1] + rad], fill=fill)
        fr = s["feather"] / 100.0 * rad * 0.9
        if fr > 0.3:
            layer = layer.filter(ImageFilter.GaussianBlur(fr))
        m = ImageChops.subtract(m, layer) if s["erase"] else ImageChops.lighter(m, layer)
    return _resize_mask(np.asarray(m, dtype=np.float32) / 255.0, h, w)


def component(c, a):
    h, w = a.shape[:2]
    k = c["kind"]
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    nx, ny = xx / max(w - 1, 1), yy / max(h - 1, 1)
    if k == "linear":
        dx, dy = (c["x2"] - c["x1"]) * w, (c["y2"] - c["y1"]) * h
        n2 = dx * dx + dy * dy
        if n2 < 1:
            m = np.ones((h, w), dtype=np.float32)
        else:
            t = ((xx - c["x1"] * w) * dx + (yy - c["y1"] * h) * dy) / n2
            m = 1 - _smooth(t, 0.0, 1.0)
    elif k == "radial":
        th = np.radians(c["rot"])
        ux, uy = (nx - c["cx"]) * w, (ny - c["cy"]) * h
        rx, ry = c["rx"] * max(w, h), c["ry"] * max(w, h)
        px, py = ux * np.cos(th) + uy * np.sin(th), -ux * np.sin(th) + uy * np.cos(th)
        d = np.sqrt((px / rx) ** 2 + (py / ry) ** 2)
        f = max(c["feather"] / 100.0, 0.03)
        m = 1 - _smooth(d, 1.0 - f, 1.0 + 0.0001)
    elif k == "luminance":
        lum = _lum(np.clip(a, 0, 1))
        sm = max(c["smooth"] / 100.0 * 0.3, 0.005)
        m = _smooth(lum, c["lo"] - sm, c["lo"] + sm) * (1 - _smooth(lum, c["hi"] - sm, c["hi"] + sm))
    elif k == "color":
        ref = np.array(c["color"], dtype=np.float32) / 255.0
        from . import develop_ops as D
        hr, sr, vr = (float(x[0, 0]) for x in D.rgb_to_hsv(ref.reshape(1, 1, 3)))
        h_, s_, v_ = D.rgb_to_hsv(np.clip(a, 0, 1))
        dh = np.abs(((h_ - hr) + 180.0) % 360.0 - 180.0) / 180.0
        dist = np.sqrt((dh * 2.0) ** 2 * min(sr, 1.0) + (s_ - sr) ** 2 * 0.5 + (v_ - vr) ** 2 * 0.25)
        tol = 0.05 + c["tol"] / 100.0 * 0.6
        m = 1 - _smooth(dist, tol * 0.5, tol)
    elif k == "brush":
        m = _brush(c, h, w)
    elif k == "sky":
        m = _resize_mask(sky_mask(a), h, w)
    elif k == "subject":
        m = _resize_mask(subject_mask(a), h, w)
    else:                                                                                  # background
        m = 1 - _resize_mask(subject_mask(a), h, w)
    m = m.astype(np.float32)
    return 1 - m if c.get("invert") else m


def mask_of(m, a):
    """The combined mask of one local adjustment: components are added, subtracted or intersected in order."""
    out = None
    for c in m["comps"]:
        mc = component(c, a)
        if out is None:
            out = mc
        elif c["op"] == "subtract":
            out = out * (1 - mc)
        elif c["op"] == "intersect":
            out = out * mc
        else:
            out = out + mc - out * mc
    return out


def apply_masks(a, masks, local_fn):
    """local_fn(array, adjustments) -> the adjusted array; each mask blends it in, weighted by the mask and its Amount."""
    for m in clean({"masks": masks}).get("masks", []):
        if not m.get("comps") or not any(abs(float(v or 0)) > 0 for v in (m.get("adj") or {}).values()):
            continue
        w = mask_of(m, a)
        if w is None:
            continue
        k = (w * (float(m.get("amount", 100)) / 100.0))[..., None]
        if float(k.max()) <= 0.001:
            continue
        a = a + (local_fn(a, m["adj"]) - a) * k
    return a
