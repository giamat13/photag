"""The extra Develop tools (issue #6, batch 1): whites / blacks, texture, dehaze, film grain, vignette styles, tone curve, colour mixer,
black & white mix, colour grading and calibration. All of them work on a float RGB array (0..1) and are applied by
images.apply_tone() in a fixed order. Radii scale with the picture size, so a small preview looks like the full render.

Settings (all optional; a missing or zero value does nothing):
  sharp_radius 0..100 (50), sharp_detail 0..100 (25), sharp_mask 0..100 (0): how "sharpness" is applied (see sharpen())
  nr_lum 0..100, nr_color 0..100, nr_detail 0..100 (50): noise reduction (see denoise())
  whites, blacks, texture, dehaze        -100..100
  grain 0..100, grain_size 0..100 (default 25), grain_rough 0..100 (default 50)
  vignette (amount, -100..100, existing) + vignette_mid 0..100 (50), vignette_feather 0..100 (50), vignette_round -100..100 (0), vignette_hl 0..100
  curve:   {"rgb": [[x, y], ...], "r": [...], "g": [...], "b": [...]}  points 0..1, x ascending, monotone curve through them
  curve_p: [highlights, lights, darks, shadows]  -100..100 (parametric curve)
  mixer:   {"red": [hue, saturation, luminance], "orange": ..., ...}  8 bands, each -100..100
  bwmix:   {"red": v, "orange": v, ...}  -100..100, used when the photo is black & white
  grading: {"shadows": [hue 0..360, sat 0..100, lum -100..100], "mid": [...], "high": [...], "blend": 0..100 (50), "balance": -100..100}
  calib:   {"shadow_tint": -100..100, "red": [hue, sat], "green": [hue, sat], "blue": [hue, sat]}
"""
import numpy as np

BANDS = ("red", "orange", "yellow", "green", "aqua", "blue", "purple", "magenta")
CENTERS = (0.0, 30.0, 60.0, 120.0, 180.0, 240.0, 270.0, 300.0)
SCALAR_KEYS = ("whites", "blacks", "texture", "dehaze", "grain", "nr_lum", "nr_color")
VIGNETTE_STYLE = {"vignette_mid": 50.0, "vignette_feather": 50.0, "vignette_round": 0.0, "vignette_hl": 0.0}
GRAIN_DEFAULTS = {"grain_size": 25.0, "grain_rough": 50.0}
SHARP_DEFAULTS = {"sharp_radius": 50.0, "sharp_detail": 25.0, "sharp_mask": 0.0, "nr_detail": 50.0}
NESTED_KEYS = ("curve", "curve_p", "mixer", "bwmix", "grading", "calib")


def _smooth(x, a, b):
    t = np.clip((x - a) / (b - a), 0.0, 1.0)
    return t * t * (3 - 2 * t)


def _lum(a):
    return a[..., 0:1] * 0.2126 + a[..., 1:2] * 0.7152 + a[..., 2:3] * 0.0722


def _nz(v):
    return v not in (None, 0, 0.0)


def _list_nonzero(v):
    return isinstance(v, (list, tuple)) and any(_nz(x) for x in v)


def _dict_nonzero(d):
    if not isinstance(d, dict):
        return False
    for v in d.values():
        if isinstance(v, (list, tuple)):
            if any(_nz(x) for x in v):
                return True
        elif _nz(v):
            return True
    return False


def _curve_active(c):
    """A curve is active when one of its channels has points that are not the straight line."""
    if not isinstance(c, dict):
        return False
    for pts in c.values():
        if isinstance(pts, (list, tuple)) and any(isinstance(p, (list, tuple)) and len(p) == 2 and abs(float(p[0]) - float(p[1])) > 1e-4 for p in pts):
            return True
    return False


def active(ops: dict) -> bool:
    """Does `ops` ask for any of the tools of this module?"""
    from . import develop_local
    if develop_local.active(ops):
        return True
    if any(_nz(ops.get(k)) for k in SCALAR_KEYS):
        return True
    return (_curve_active(ops.get("curve")) or _list_nonzero(ops.get("curve_p")) or _dict_nonzero(ops.get("mixer")) or _dict_nonzero(ops.get("bwmix"))
            or _grading_active(ops.get("grading")) or _dict_nonzero(ops.get("calib")))


def _grading_active(g):
    if not isinstance(g, dict):
        return False
    return any(isinstance(g.get(k), (list, tuple)) and len(g[k]) >= 3 and (_nz(g[k][1]) or _nz(g[k][2])) for k in ("shadows", "mid", "high"))


# ---------------------------------------------------------------- colour spaces
def rgb_to_hsv(a):
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    mx, mn = a.max(axis=2), a.min(axis=2)
    d = mx - mn
    h = np.zeros_like(mx)
    nz = d > 1e-6
    rc = nz & (mx == r)
    gc = nz & (mx == g) & ~rc
    bc = nz & ~rc & ~gc
    h[rc] = ((g - b)[rc] / d[rc]) % 6
    h[gc] = (b - r)[gc] / d[gc] + 2
    h[bc] = (r - g)[bc] / d[bc] + 4
    s = np.where(mx > 1e-6, d / np.maximum(mx, 1e-6), 0.0)
    return h * 60.0, s, mx


def hsv_to_rgb(h, s, v):
    h = (h % 360.0) / 60.0
    i = np.floor(h).astype(np.int32) % 6
    f = h - np.floor(h)
    p, q, t = v * (1 - s), v * (1 - s * f), v * (1 - s * (1 - f))
    r = np.choose(i, (v, q, p, p, t, v))
    g = np.choose(i, (t, v, v, q, p, p))
    b = np.choose(i, (p, p, t, v, v, q))
    return np.stack([r, g, b], axis=2)


def band_weights(h, width=45.0):
    """(8, H, W) soft weights of each colour band for the hue image h (degrees): full strength within +-width/2 of the band's centre,
    fading to nothing at +-width (so a pure blue is moved completely by the Blue band, a colour between two bands by both)."""
    out = []
    for c in CENTERS:
        d = np.abs(((h - c) + 180.0) % 360.0 - 180.0)
        out.append(np.clip(2.0 * (1.0 - d / width), 0.0, 1.0))
    return np.stack(out)


# ---------------------------------------------------------------- the tools
def whites_blacks(a, whites, blacks):
    lum = _lum(a)
    if blacks:
        a = a + (blacks / 100.0) * 0.14 * (1.0 - _smooth(lum, 0.0, 0.45))
    if whites:
        a = a + (whites / 100.0) * 0.22 * _smooth(lum, 0.55, 1.0) * (0.35 + 0.65 * lum)
    return a


def texture(a, amount, size):
    """Fine detail: positive adds contrast to small structures, negative smooths them (skin)."""
    from PIL import Image, ImageFilter
    lum = _lum(a)
    img = Image.fromarray((np.clip(lum[..., 0], 0, 1) * 255).astype(np.uint8), "L")
    r = max(0.8, size / 1200.0)
    blur = np.asarray(img.filter(ImageFilter.GaussianBlur(radius=r)), dtype=np.float32) / 255.0
    detail = (lum[..., 0] - blur)[..., None]
    if amount > 0:
        return a + detail * (amount / 100.0) * 1.6
    smooth = np.asarray(img.filter(ImageFilter.GaussianBlur(radius=r * 2.2)), dtype=np.float32)[..., None] / 255.0
    k = -amount / 100.0 * 0.85
    return a + (smooth - lum) * k


def dehaze(a, amount, size):
    """Positive removes haze (dark-channel prior with a softened transmission), negative adds atmosphere."""
    from PIL import Image, ImageFilter
    if amount < 0:
        k = -amount / 100.0 * 0.45
        air = np.array([0.78, 0.82, 0.88], dtype=np.float32)
        return a * (1 - k) + air * k
    dc = a.min(axis=2)
    img = Image.fromarray((np.clip(dc, 0, 1) * 255).astype(np.uint8), "L")
    rad = max(1, int(size / 160)) * 2 + 1
    dark = np.asarray(img.filter(ImageFilter.MinFilter(rad)), dtype=np.float32) / 255.0
    # the airlight: the average colour of the haziest 0.1% of the picture
    flat = dark.ravel()
    n = max(1, int(flat.size * 0.001))
    idx = np.argpartition(flat, -n)[-n:]
    air = a.reshape(-1, 3)[idx].mean(axis=0)
    air = np.maximum(air, 0.5)
    omega = 0.95 * amount / 100.0
    t = 1.0 - omega * (dark / np.maximum(air.max(), 1e-3))
    t = np.asarray(Image.fromarray((np.clip(t, 0, 1) * 255).astype(np.uint8), "L").filter(ImageFilter.GaussianBlur(radius=max(1.0, size / 300.0))), dtype=np.float32) / 255.0
    t = np.maximum(t, 0.25)[..., None]
    out = (a - air) / t + air
    return out + (amount / 100.0) * 0.03 - 0.0                       # a hair of black so dehazed shadows keep some depth


def grain(a, amount, size, rough):
    """Resolution-independent film grain: luminance noise, strongest in the mid tones."""
    from PIL import Image, ImageFilter
    h, w = a.shape[:2]
    rng = np.random.default_rng(w * 7919 + h)
    sc = max(1.0, max(w, h) / 1600.0)
    sigma = (0.6 + size / 100.0 * 2.4) * sc
    n1 = rng.standard_normal((h, w)).astype(np.float32)
    img = Image.fromarray(np.clip(n1 * 40 + 128, 0, 255).astype(np.uint8), "L").filter(ImageFilter.GaussianBlur(radius=sigma))
    n = (np.asarray(img, dtype=np.float32) - 128.0) / 40.0
    n = n / max(float(n.std()), 1e-3)
    r = rough / 100.0
    n = n * (0.55 + 0.45 * r) + rng.standard_normal((h, w)).astype(np.float32) * 0.35 * r * 0.6
    lum = _lum(a)
    mid = 0.35 + 0.65 * (4.0 * lum * (1.0 - lum))
    return a + (n[..., None] * (amount / 100.0) * 0.16) * mid


def sharpen(im, amount, size, radius=50.0, detail=25.0, mask=0.0):
    """Sharpening of a PIL RGB image. amount 0..100 (the old "sharpness"), radius / detail / mask 0..100: with the resting values
    (50 / 25 / 0) the result is exactly the sharpening photag always had. A bigger radius sharpens coarser structures, detail
    lets the finest ones through (a lower value ignores faint noise), masking limits the effect to the edges."""
    from PIL import ImageFilter
    rad = max(0.6, size / 1800.0) * (0.4 + 1.2 * float(radius) / 100.0) / 1.0
    rad *= 1.0 / 1.0
    thr = int(max(0, round(2 + (25.0 - float(detail)) / 6.0)))
    out = im.filter(ImageFilter.UnsharpMask(radius=rad, percent=int(amount * 2.5), threshold=thr))
    if mask:
        lum = np.asarray(im.convert("L"), dtype=np.float32) / 255.0
        gy, gx = np.gradient(lum)
        g = np.sqrt(gx * gx + gy * gy)
        g = np.asarray(__import__("PIL.Image", fromlist=["Image"]).fromarray((np.clip(g / max(float(np.percentile(g, 99)), 1e-4), 0, 1) * 255).astype(np.uint8), "L")
                       .filter(ImageFilter.GaussianBlur(radius=max(0.8, size / 1500.0))), dtype=np.float32) / 255.0
        m = float(mask) / 100.0
        w = ((1.0 - m) + m * np.clip(g * 2.0, 0.0, 1.0))[..., None]
        a0, a1 = np.asarray(im, dtype=np.float32), np.asarray(out, dtype=np.float32)
        out = __import__("PIL.Image", fromlist=["Image"]).fromarray(np.clip(a0 + (a1 - a0) * w + 0.5, 0, 255).astype(np.uint8), "RGB")
    return out


def denoise(im, lum_amount, color_amount, detail, size):
    """Noise reduction of a PIL RGB image: the colour noise (blotches of wrong colour) is smoothed in the chroma channels, the
    luminance noise (grain) by an edge-aware blend with a blurred copy -- `detail` (0..100) decides how strong an edge must be to be kept."""
    from PIL import Image, ImageFilter
    y, cb, cr = im.convert("YCbCr").split()
    if color_amount:
        r = max(0.8, size / 500.0) * (0.3 + float(color_amount) / 50.0)
        cb, cr = cb.filter(ImageFilter.GaussianBlur(radius=r)), cr.filter(ImageFilter.GaussianBlur(radius=r))
    if lum_amount:
        ya = np.asarray(y, dtype=np.float32) / 255.0
        r = max(1.2, size / 700.0) * (0.6 + float(lum_amount) / 35.0)
        yb = np.asarray(y.filter(ImageFilter.GaussianBlur(radius=r)), dtype=np.float32) / 255.0
        thr = 0.02 + 0.16 * (1.0 - float(detail) / 100.0)
        wt = np.exp(-(((ya - yb) / thr) ** 2))
        k = min(1.0, float(lum_amount) / 100.0 * 1.05)
        y = Image.fromarray((np.clip(ya + (yb - ya) * k * wt, 0, 1) * 255.0 + 0.5).astype(np.uint8), "L")
    return Image.merge("YCbCr", (y, cb, cr)).convert("RGB")


def vignette(a, amount, mid, feather, rnd, hl):
    """Post-crop vignette with the Lightroom-like handles: midpoint (how far from the corners it starts), feather, roundness and
    highlight priority. With the handles at their defaults this is the original photag vignette."""
    h, w = a.shape[:2]
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    nx, ny = (xx - (w - 1) / 2) / (w / 2), (yy - (h - 1) / 2) / (h / 2)
    ell = np.sqrt(nx ** 2 + ny ** 2) / np.sqrt(2.0)                      # follows the picture's shape
    k = max(w, h) / 2.0
    rd = np.sqrt(((xx - (w - 1) / 2) / k) ** 2 + ((yy - (h - 1) / 2) / k) ** 2) / np.sqrt(2.0)    # a circle
    q = (rnd + 100.0) / 200.0                                            # 0 = very round ... 1 = follows the picture
    r = ell * q + rd * (1 - q) if rnd != 0 else ell
    start = 0.10 + 0.5 * (mid / 100.0)                                   # midpoint 50 -> 0.35, feather 50 -> 0.65: exactly the original vignette
    width = 0.25 + 0.8 * (feather / 100.0)
    m = (np.clip((r - start) / width, 0.0, 1.0) ** 2)[..., None]
    if hl:
        m = m * (1.0 - (hl / 100.0) * _smooth(_lum(a), 0.4, 1.0))
    v = amount / 100.0
    return a * (1 - (-v) * 0.9 * m) if v < 0 else a + v * 0.9 * m * (1 - a)


def _pchip(xs, ys, x):
    """Monotone cubic (Fritsch-Carlson) through the points, evaluated at x (numpy array)."""
    xs = np.asarray(xs, dtype=np.float64)
    ys = np.asarray(ys, dtype=np.float64)
    n = len(xs)
    if n == 1:
        return np.full_like(x, ys[0], dtype=np.float64)
    h = np.diff(xs)
    delta = np.diff(ys) / np.maximum(h, 1e-9)
    m = np.zeros(n)
    m[0], m[-1] = delta[0], delta[-1]
    for i in range(1, n - 1):
        m[i] = 0.0 if delta[i - 1] * delta[i] <= 0 else 2 * delta[i - 1] * delta[i] / (delta[i - 1] + delta[i])
    for i in range(n - 1):                                               # keep every segment monotone
        if abs(delta[i]) < 1e-12:
            m[i] = m[i + 1] = 0.0
        else:
            a_, b_ = m[i] / delta[i], m[i + 1] / delta[i]
            s = a_ * a_ + b_ * b_
            if s > 9:
                tt = 3.0 / np.sqrt(s)
                m[i], m[i + 1] = tt * a_ * delta[i], tt * b_ * delta[i]
    idx = np.clip(np.searchsorted(xs, x, side="right") - 1, 0, n - 2)
    t = (x - xs[idx]) / np.maximum(h[idx], 1e-9)
    h00, h10 = 2 * t ** 3 - 3 * t ** 2 + 1, t ** 3 - 2 * t ** 2 + t
    h01, h11 = -2 * t ** 3 + 3 * t ** 2, t ** 3 - t ** 2
    return h00 * ys[idx] + h10 * h[idx] * m[idx] + h01 * ys[idx + 1] + h11 * h[idx] * m[idx + 1]


def curve_lut(points, param=None, n=1024):
    """A lookup table (n entries, 0..1 -> 0..1) from curve points and optional parametric amounts [highlights, lights, darks, shadows]."""
    pts = sorted(((min(1.0, max(0.0, float(p[0]))), min(1.0, max(0.0, float(p[1])))) for p in (points or []) if isinstance(p, (list, tuple)) and len(p) == 2))
    clean = []
    top = 0.0
    for x, y in pts:                                                     # x strictly ascending, y never going down (tones are never inverted)
        if clean and x <= clean[-1][0] + 1e-4:
            continue
        top = max(top, y)
        clean.append((x, top))
    if not clean or clean[0][0] > 1e-4:
        clean.insert(0, (0.0, 0.0))
    if clean[-1][0] < 1 - 1e-4:
        clean.append((1.0, 1.0))
    xs = np.linspace(0.0, 1.0, n)
    ys = _pchip([p[0] for p in clean], [p[1] for p in clean], xs)
    if param and any(_nz(v) for v in param):
        hl, li, da, sh = (list(param) + [0, 0, 0, 0])[:4]
        bump = lambda c, wd: np.exp(-((xs - c) / wd) ** 2)
        ys = ys + 0.18 * (hl / 100.0) * bump(0.88, 0.14) + 0.18 * (li / 100.0) * bump(0.65, 0.15) \
            + 0.18 * (da / 100.0) * bump(0.35, 0.15) + 0.18 * (sh / 100.0) * bump(0.12, 0.14)
        ys = np.maximum.accumulate(np.clip(ys, 0.0, 1.0))               # never invert the tones
    return np.clip(ys, 0.0, 1.0).astype(np.float32)


def apply_curve(a, curve, param):
    n = 1024
    out = a
    c = curve if isinstance(curve, dict) else {}
    if c.get("rgb") or _list_nonzero(param):
        lut = curve_lut(c.get("rgb"), param, n)
        out = np.interp(out, np.linspace(0, 1, n), lut).astype(np.float32)
    for i, key in enumerate(("r", "g", "b")):
        pts = c.get(key)
        if pts and any(isinstance(p, (list, tuple)) and len(p) == 2 and abs(float(p[0]) - float(p[1])) > 1e-4 for p in pts):
            lut = curve_lut(pts, None, n)
            out = out.copy() if out is a else out
            out[..., i] = np.interp(out[..., i], np.linspace(0, 1, n), lut)
    return out


def mixer(a, mx):
    """8-band colour mixer: hue (+-30 degrees), saturation and luminance per band."""
    h, s, v = rgb_to_hsv(np.clip(a, 0, 1))
    w = band_weights(h)
    dh = ds = dl = 0.0
    for i, name in enumerate(BANDS):
        vals = (mx or {}).get(name) or [0, 0, 0]
        vals = list(vals) + [0, 0, 0]
        dh = dh + w[i] * (float(vals[0]) / 100.0 * 30.0)
        ds = ds + w[i] * (float(vals[1]) / 100.0)
        dl = dl + w[i] * (float(vals[2]) / 100.0)
    h2 = h + dh
    s2 = np.clip(s * (1.0 + np.clip(ds, -1.0, 1.0)), 0.0, 1.0)
    v2 = np.clip(v + dl * 0.4 * s, 0.0, 1.0)
    return hsv_to_rgb(h2, s2, v2)


def bw_mix(a, mx):
    """Black & white with per-colour brightness: darken the sky (blue), lighten foliage (green)..."""
    c = np.clip(a, 0, 1)
    h, s, v = rgb_to_hsv(c)
    w = band_weights(h)
    gain = 0.0
    for i, name in enumerate(BANDS):
        gain = gain + w[i] * (float((mx or {}).get(name) or 0) / 100.0)
    lum = _lum(c)[..., 0]
    g = np.clip(lum * (1.0 + gain * s * 0.9), 0.0, 1.0)
    return np.repeat(g[..., None], 3, axis=2)


def grading(a, g):
    """Colour grading: a tint and brightness change for the shadows, mid tones and highlights, with blending and balance."""
    lum = _lum(np.clip(a, 0, 1))
    blend = float(g.get("blend", 50)) / 100.0
    pivot = 0.5 + 0.25 * float(g.get("balance", 0)) / 100.0
    width = 0.12 + 0.38 * blend
    ws = 1.0 - _smooth(lum, pivot - width, pivot)
    wh = _smooth(lum, pivot, pivot + width)
    wm = np.clip(1.0 - ws - wh, 0.0, 1.0)
    out = a.copy()
    for key, wt in (("shadows", ws), ("mid", wm), ("high", wh)):
        v = g.get(key)
        if not (isinstance(v, (list, tuple)) and len(v) >= 3):
            continue
        hue, sat, lm = float(v[0]), float(v[1]) / 100.0, float(v[2]) / 100.0
        if sat:
            tint = hsv_to_rgb(np.array([[hue]], dtype=np.float32), np.array([[1.0]], dtype=np.float32), np.array([[1.0]], dtype=np.float32))[0, 0]
            out = out + wt * sat * (tint - 0.5) * 0.45
        if lm:
            out = out + wt * lm * 0.25
    return out


def calibration(a, c):
    """Camera-calibration style primaries: shift the hue / saturation of the red, green and blue primaries, tint the shadows."""
    out = a
    h, s, v = rgb_to_hsv(np.clip(a, 0, 1))
    dh = ds = 0.0
    for name, centre in (("red", 0.0), ("green", 120.0), ("blue", 240.0)):
        vals = (c.get(name) or [0, 0]) + [0, 0]
        d = np.abs(((h - centre) + 180.0) % 360.0 - 180.0)
        w = np.clip(1.0 - d / 70.0, 0.0, 1.0)
        dh = dh + w * (float(vals[0]) / 100.0 * 35.0)
        ds = ds + w * (float(vals[1]) / 100.0)
    if _dict_nonzero({k: c.get(k) for k in ("red", "green", "blue")}):
        out = hsv_to_rgb(h + dh, np.clip(s * (1.0 + ds), 0.0, 1.0), v)
    st = float(c.get("shadow_tint") or 0) / 100.0
    if st:
        lum = _lum(np.clip(out, 0, 1))
        out = out + (1.0 - _smooth(lum, 0.0, 0.5)) * st * 0.06 * np.array([0.6, -1.0, 0.6], dtype=np.float32)
    return out


# ---------------------------------------------------------------- the white-balance helpers (the page also has them; kept here for tests and Auto)
WB_PRESETS = {"daylight": (0, 0), "cloudy": (18, 6), "shade": (32, 8), "tungsten": (-45, 6), "fluorescent": (-12, 24), "flash": (6, 2)}


def wb_from_neutral(rgb):
    """temperature, tint (the units of the sliders) that make the picked colour neutral grey."""
    r, g, b = (max(float(x), 1e-3) for x in rgb)
    t = (b - r) / (0.3 * (r + b))
    ti = (1.0 - (1 + 0.3 * t) * r / g) / 0.2
    return max(-100.0, min(100.0, t * 100.0)), max(-100.0, min(100.0, ti * 100.0))


def apply(a, ops, size):
    """The tools of this module, in order: detail first (texture, dehaze), then tone (whites / blacks, curves), colour, grading, vignette, grain."""
    g = lambda k, d=0.0: float(ops.get(k) if ops.get(k) not in (None, "") else d)
    if g("texture"):
        a = texture(a, g("texture"), size)
    if g("dehaze"):
        a = dehaze(a, g("dehaze"), size)
    if g("whites") or g("blacks"):
        a = whites_blacks(a, g("whites"), g("blacks"))
    if _curve_active(ops.get("curve")) or _list_nonzero(ops.get("curve_p")):
        a = apply_curve(np.clip(a, 0, 1), ops.get("curve"), ops.get("curve_p"))
    if _dict_nonzero(ops.get("calib")):
        a = calibration(a, ops["calib"])
    if _dict_nonzero(ops.get("mixer")):
        a = mixer(a, ops["mixer"])
    if _grading_active(ops.get("grading")):
        a = grading(a, ops["grading"])
    if ops.get("grayscale") and _dict_nonzero(ops.get("bwmix")):
        a = bw_mix(a, ops["bwmix"])
    return a


# ---------------------------------------------------------------- what the server accepts
_RANGES = {"whites": (-100, 100), "blacks": (-100, 100), "texture": (-100, 100), "dehaze": (-100, 100), "grain": (0, 100), "grain_size": (0, 100),
           "grain_rough": (0, 100), "vignette_mid": (0, 100), "vignette_feather": (0, 100), "vignette_round": (-100, 100), "vignette_hl": (0, 100),
           "sharp_radius": (0, 100), "sharp_detail": (0, 100), "sharp_mask": (0, 100), "nr_lum": (0, 100), "nr_color": (0, 100), "nr_detail": (0, 100)}


def _num(v, lo, hi, default=0.0):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return default
    if x != x:                                              # NaN
        return default
    return max(lo, min(hi, x))


def clean(ops: dict) -> dict:
    """The settings of this module from `ops`, with every number clamped and only known names kept (nothing else is trusted)."""
    out = {}
    for k, (lo, hi) in _RANGES.items():
        if ops.get(k) is not None:
            out[k] = _num(ops[k], lo, hi)
    c = ops.get("curve")
    if isinstance(c, dict):
        cc = {}
        for ch in ("rgb", "r", "g", "b"):
            pts = c.get(ch)
            if isinstance(pts, (list, tuple)):
                good = [[round(_num(p[0], 0, 1), 4), round(_num(p[1], 0, 1), 4)] for p in pts[:16] if isinstance(p, (list, tuple)) and len(p) == 2]
                if good:
                    cc[ch] = good
        if cc:
            out["curve"] = cc
    if isinstance(ops.get("curve_p"), (list, tuple)):
        out["curve_p"] = [_num(v, -100, 100) for v in list(ops["curve_p"])[:4]]
    for key, size, lo, hi in (("mixer", 3, -100, 100), ("bwmix", 1, -100, 100)):
        d = ops.get(key)
        if isinstance(d, dict):
            dd = {}
            for b in BANDS:
                v = d.get(b)
                if size == 1:
                    if v is not None:
                        dd[b] = _num(v, lo, hi)
                elif isinstance(v, (list, tuple)):
                    dd[b] = [_num(x, lo, hi) for x in list(v)[:3]]
            if dd:
                out[key] = dd
    g = ops.get("grading")
    if isinstance(g, dict):
        gg = {}
        for k in ("shadows", "mid", "high"):
            v = g.get(k)
            if isinstance(v, (list, tuple)) and len(v) >= 3:
                gg[k] = [_num(v[0], 0, 360), _num(v[1], 0, 100), _num(v[2], -100, 100)]
        for k, lo, hi, dflt in (("blend", 0, 100, 50), ("balance", -100, 100, 0)):
            if g.get(k) is not None:
                gg[k] = _num(g[k], lo, hi, dflt)
        if gg:
            out["grading"] = gg
    from . import develop_local
    out.update(develop_local.clean(ops))
    cal = ops.get("calib")
    if isinstance(cal, dict):
        cc = {}
        if cal.get("shadow_tint") is not None:
            cc["shadow_tint"] = _num(cal["shadow_tint"], -100, 100)
        for k in ("red", "green", "blue"):
            v = cal.get(k)
            if isinstance(v, (list, tuple)):
                cc[k] = [_num(x, -100, 100) for x in list(v)[:2]]
        if cc:
            out["calib"] = cc
    return out
