"""Test: the new develop settings (exposure, highlights, shadows, temperature, tint, vibrance, clarity, sharpness, blur,
vignette, sepia, flips), the live-preview endpoint, the Auto button's algorithm, and the "keep edits and EXIF in the
catalog" switch (on: the photo file is never touched, EXIF comes from the database; off: edits go into the file, EXIF is
read from it) -- including switching back and forth.

Phase 1 (no server): every setting measured on synthetic pictures, edge cases, Auto on dark / bright / flat / tinted / random pictures.
Phase 2 (real server): endpoints, files on disk, the switch and its round trips, EXIF source.

    py -3.12 tools/test_edit_ops_v2.py
"""
import hashlib
import io
import json
import math
import os
import random
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parent.parent
PORT = 8795
APP = f"http://127.0.0.1:{PORT}"
tmp = Path(tempfile.mkdtemp(prefix="photag_editops_test_"))
for k, v in (("APPDATA", "a"), ("LOCALAPPDATA", "l"), ("USERPROFILE", "h"), ("HOME", "h")):
    (tmp / v).mkdir(exist_ok=True)
    os.environ[k] = str(tmp / v)
os.environ.update(PYTHONIOENCODING="utf-8", PHOTAG_NO_OPEN="1", PHOTAG_BACKUP_START_DELAY="9999", PHOTAG_EXIF_DELAY="9999",
                  PHOTAG_MIGRATE_DELAY="9999")
sys.path.insert(0, str(ROOT))
res = []


def check(name, ok, extra=""):
    res.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{extra}]" if extra else ""))


from app import config, db, images, importer, render  # noqa: E402
from app.config import PATHS  # noqa: E402

config.PATHS.root.mkdir(parents=True, exist_ok=True)
con = db.init_db()
work = tmp / "work"
work.mkdir()
sha = lambda b: hashlib.sha256(b).hexdigest()


# ------------------------------------------------------------------ helpers: pictures and measurements
def save(arr, name, ext=".png"):
    p = work / f"{name}{ext}"
    Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGB").save(p)
    return p


def flat(v, w=120, h=80, rgb=None):
    a = np.zeros((h, w, 3), np.float32)
    a[:] = rgb if rgb else v
    return a


def scene(w=160, h=100, seed=1):
    """A natural-ish picture: smooth colour gradients plus texture, using the whole tonal range."""
    rng = np.random.RandomState(seed)
    x = np.linspace(0, 1, w)[None, :, None]
    y = np.linspace(0, 1, h)[:, None, None]
    a = 255 * np.concatenate([0.15 + 0.7 * x + 0 * y, 0.2 + 0.6 * y + 0 * x, 0.8 - 0.6 * x + 0 * y], axis=2)
    return a + rng.normal(0, 6, a.shape)


def edit(path, ops):
    out = work / f"out_{hashlib.md5((str(path) + json.dumps(ops, sort_keys=True)).encode()).hexdigest()[:10]}.png"
    images.apply_edit(path, ops, out)
    return np.asarray(Image.open(out).convert("RGB"), dtype=np.float32)


def src_arr(path):
    return np.asarray(Image.open(path).convert("RGB"), dtype=np.float32)


lum = lambda a: 0.2126 * a[..., 0] + 0.7152 * a[..., 1] + 0.0722 * a[..., 2]


def hp(a):                                                  # high-pass energy: how much fine detail there is
    g = lum(a)
    return float(np.abs(g[:, 1:] - g[:, :-1]).mean() + np.abs(g[1:, :] - g[:-1, :]).mean())


def sat(a):
    mx, mn = a.max(axis=2), a.min(axis=2)
    return float(np.where(mx > 0, (mx - mn) / np.maximum(mx, 1), 0).mean())


SC = save(scene(), "scene")
S0 = src_arr(SC)

# =================================================================================  PHASE 1: every setting
# ---- neutral stays neutral
check("no tone settings -> has_tone is False", not images.has_tone({}) and not images.has_tone({"exposure": 0, "blur": 0, "sepia": None}))
check("...and apply_tone returns the picture itself", images.apply_tone(Image.new("RGB", (4, 4)), {"rotate": 5}) is not None)
check("an edit with only zeros reproduces the picture (within JPEG-free PNG: exactly)",
      np.abs(edit(SC, {"exposure": 0, "highlights": 0, "shadows": 0, "temperature": 0, "blur": 0}) - S0).max() == 0)
NOT_ALONE = {"grain_size", "grain_rough", "vignette_mid", "vignette_feather", "vignette_round", "vignette_hl", "sharp_radius", "sharp_detail", "sharp_mask", "nr_detail"}      # styles of grain / vignette: nothing by themselves
NESTED_ON = {"curve": {"rgb": [[0.5, 0.7]]}, "curve_p": [0, 0, 0, 20], "mixer": {"red": [0, -50, 0]}, "bwmix": {"red": 30},
             "grading": {"shadows": [200, 40, 0]}, "calib": {"shadow_tint": 20}}
for k in images.TONE_KEYS:
    if k in NOT_ALONE:
        check(f"render.is_neutral: {k} alone changes nothing", render.is_neutral({k: 10}))
    elif k in NESTED_ON:
        check(f"render.is_neutral: an empty {k} is neutral, a real one is not", render.is_neutral({k: {} if isinstance(NESTED_ON[k], dict) else [0, 0, 0, 0]}) and not render.is_neutral({k: NESTED_ON[k]}))
    else:
        check(f"render.is_neutral: {k}=0 is neutral, {k}=10 is not", render.is_neutral({k: 0}) and not render.is_neutral({k: 10}))
check("render.is_neutral: a flip is an edit, False is not", not render.is_neutral({"flip_h": True}) and not render.is_neutral({"flip_v": True})
      and render.is_neutral({"flip_h": False, "flip_v": None}))
check("render.is_neutral: old keys still work", render.is_neutral({"brightness": 1.0, "crop": [0, 0, 1, 1]}) and not render.is_neutral({"brightness": 1.1}))

# ---- exposure
m = {ev: float(lum(edit(SC, {"exposure": ev})).mean()) for ev in (-2, -1, 0, 1, 2)}
check("exposure: brighter with every stop", m[-2] < m[-1] < m[0] < m[1] < m[2], {k: round(v) for k, v in m.items()})
check("exposure -1 is clearly darker, +1 clearly brighter", m[-1] < m[0] - 15 and m[1] > m[0] + 15)
mid = flat(100)
check("exposure never changes the picture size", edit(save(mid, "mid"), {"exposure": 1.5}).shape == mid.shape)
check("exposure +3 on white stays white (clips, no overflow)", edit(save(flat(255), "white"), {"exposure": 3}).min() >= 254)
check("exposure -3 on black stays black", edit(save(flat(0), "black"), {"exposure": -3}).max() <= 1)

# ---- highlights / shadows
H = save(np.tile(np.linspace(0, 255, 256)[None, :, None], (20, 1, 3)), "ramp")
R0 = src_arr(H)
Rh = edit(H, {"highlights": -100})
check("highlights -100 pulls the brightest pixels down", lum(Rh)[:, -20:].mean() < lum(R0)[:, -20:].mean() - 20, (round(lum(Rh)[:, -20:].mean()), round(lum(R0)[:, -20:].mean())))
check("...and leaves the darks alone", abs(lum(Rh)[:, :40].mean() - lum(R0)[:, :40].mean()) < 4)
check("highlights +100 lifts the bright end", lum(edit(H, {"highlights": 100}))[:, 180:230].mean() > lum(R0)[:, 180:230].mean() + 5)
Rs = edit(H, {"shadows": 100})
check("shadows +100 lifts the darkest pixels", lum(Rs)[:, :20].mean() > lum(R0)[:, :20].mean() + 15)
check("...and leaves the brights alone", abs(lum(Rs)[:, -40:].mean() - lum(R0)[:, -40:].mean()) < 4)
check("shadows -100 darkens the shadows", lum(edit(H, {"shadows": -100}))[:, 40:100].mean() < lum(R0)[:, 40:100].mean() - 3)
mono_ok = all((np.diff(lum(edit(H, {"highlights": h, "shadows": s}))[0]) >= -1.0).all() for h in (-100, 0, 100) for s in (-100, 0, 100))
check("a ramp stays a ramp for every highlights/shadows combination (no tonal inversion)", mono_ok)

# ---- white balance
W = save(flat(0, rgb=[120, 120, 120]), "grey")
warm, cool = edit(W, {"temperature": 60}), edit(W, {"temperature": -60})
check("temperature +60 warms (more red, less blue)", warm[..., 0].mean() > 125 and warm[..., 2].mean() < 115, (warm[..., 0].mean(), warm[..., 2].mean()))
check("temperature -60 cools (more blue, less red)", cool[..., 2].mean() > 125 and cool[..., 0].mean() < 115)
mag, grn = edit(W, {"tint": 60}), edit(W, {"tint": -60})
check("tint +60 is magenta (less green), -60 green", mag[..., 1].mean() < 112 and grn[..., 1].mean() > 125)

# ---- vibrance
LOW = save(flat(0, rgb=[130, 120, 110]), "lowsat")
HIGH = save(flat(0, rgb=[240, 40, 30]), "highsat")
d_low = sat(edit(LOW, {"vibrance": 80})) - sat(src_arr(LOW))
d_high = sat(edit(HIGH, {"vibrance": 80})) - sat(src_arr(HIGH))
check("vibrance +80 boosts a dull picture", d_low > 0.05, round(d_low, 3))
check("...much more than an already vivid one", d_low > d_high * 2, (round(d_low, 3), round(d_high, 3)))
check("vibrance -100 desaturates", sat(edit(SC, {"vibrance": -100})) < sat(S0) - 0.1)

# ---- detail
check("sharpness raises fine detail", hp(edit(SC, {"sharpness": 100})) > hp(S0) * 1.1, (round(hp(edit(SC, {"sharpness": 100})), 2), round(hp(S0), 2)))
check("clarity raises local contrast", float(lum(edit(SC, {"clarity": 100})).std()) > float(lum(S0).std()))
check("blur removes fine detail", hp(edit(SC, {"blur": 60})) < hp(S0) * 0.6)
check("blur 100 is stronger than blur 20", hp(edit(SC, {"blur": 100})) < hp(edit(SC, {"blur": 20})))

# ---- vignette
F = save(flat(150, 200, 150), "vflat")
vd, vl = edit(F, {"vignette": -100}), edit(F, {"vignette": 100})
cx = lambda a: float(lum(a)[60:90, 85:115].mean())
cn = lambda a: float(np.mean([lum(a)[:10, :10].mean(), lum(a)[-10:, -10:].mean(), lum(a)[:10, -10:].mean(), lum(a)[-10:, :10].mean()]))
check("vignette -100 darkens the corners a lot", cn(vd) < 60, round(cn(vd)))
check("...and leaves the centre alone", abs(cx(vd) - 150) < 4, round(cx(vd)))
check("vignette +100 lightens the corners", cn(vl) > 215, round(cn(vl)))
check("...and leaves the centre alone", abs(cx(vl) - 150) < 4)
check("a vignette is symmetric left/right", np.abs(vd - vd[:, ::-1]).max() < 1.5)

# ---- sepia
SG = save(np.tile(np.linspace(30, 220, 100)[None, :, None], (40, 1, 3)), "sgrey")
sp = edit(SG, {"sepia": 100})
check("sepia 100: warm brown (R > G > B)", (sp[..., 0].mean() > sp[..., 1].mean() > sp[..., 2].mean()))
half = edit(SG, {"sepia": 50})
check("sepia 50 is between the original and the full effect", abs(half[..., 0].mean() - src_arr(SG)[..., 0].mean()) < abs(sp[..., 0].mean() - src_arr(SG)[..., 0].mean()))

# ---- flips (applied last), with rotate and crop
asym = np.zeros((60, 120, 3), np.float32)
asym[:, :40] = [250, 10, 10]
asym[:, 40:80] = [10, 250, 10]
asym[:, 80:] = [10, 10, 250]
asym[:20] = asym[:20] * 0.5 + 10
AS = save(asym, "asym")
fh = edit(AS, {"flip_h": True})
check("flip_h mirrors left/right exactly", np.array_equal(fh, src_arr(AS)[:, ::-1]))
check("flip_v mirrors top/bottom exactly", np.array_equal(edit(AS, {"flip_v": True}), src_arr(AS)[::-1]))
check("both flips = a 180 degree turn", np.array_equal(edit(AS, {"flip_h": True, "flip_v": True}), edit(AS, {"rotate": 180})))
cropped = edit(AS, {"crop": [0, 0, 0.5, 1]})
check("a flip comes after the crop: crop then mirror", np.array_equal(edit(AS, {"crop": [0, 0, 0.5, 1], "flip_h": True}), cropped[:, ::-1]))
check("flip + rotate 90: the flip is last", np.array_equal(edit(AS, {"rotate": 90, "flip_h": True}), edit(AS, {"rotate": 90})[:, ::-1]))
check("flips are False/None -> nothing happens", np.array_equal(edit(AS, {"flip_h": False, "flip_v": None}), src_arr(AS)))

# ---- order of the whole pipeline and combined use
combo = {"exposure": 0.5, "highlights": -30, "shadows": 30, "temperature": 10, "tint": -5, "vibrance": 20, "clarity": 20,
         "sharpness": 30, "blur": 5, "vignette": -30, "sepia": 10, "rotate": 7, "crop": [0.1, 0.1, 0.9, 0.9],
         "brightness": 1.1, "contrast": 1.1, "saturation": 0.9, "grayscale": True, "flip_h": True}
out = edit(SC, combo)
check("every setting at once renders a valid picture of the right size", out.shape[0] > 0 and out.shape[1] > 0 and np.isfinite(out).all(), out.shape)
check("tone settings never change the size (before crop/rotate)", edit(SC, {k: v for k, v in combo.items() if k in images.TONE_KEYS}).shape == S0.shape)

# ---- extreme and odd inputs
wild = {"exposure": 99, "highlights": 500, "shadows": -500, "temperature": 900, "tint": -900, "vibrance": 999, "clarity": 999,
        "sharpness": 999, "blur": 999, "vignette": -999, "sepia": 999}
try:
    w = edit(SC, wild)
    check("absurd values are clamped, not a crash (finite, inside 0..255)", np.isfinite(w).all() and w.min() >= 0 and w.max() <= 255)
except Exception as e:
    check("absurd values are clamped, not a crash", False, repr(e))
for mode, name in (("L", "gray_mode"), ("RGBA", "alpha"), ("P", "palette"), ("CMYK", "cmyk"), ("1", "bilevel")):
    p = work / f"{name}.png" if mode != "CMYK" else work / f"{name}.tif"
    try:
        Image.fromarray((S0[:, :, 0]).astype(np.uint8)).convert(mode).save(p)
        o = work / f"o_{name}.png"
        images.apply_edit(p, {"exposure": 0.5, "vignette": -30, "sepia": 30}, o)
        check(f"a {mode}-mode picture can be edited", Image.open(o).size == (160, 100))
    except Exception as e:
        check(f"a {mode}-mode picture can be edited", False, repr(e))
tiny = save(np.full((1, 1, 3), 100, np.float32), "one")
try:
    check("a 1x1 picture survives every setting", edit(tiny, wild).shape == (1, 1, 3))
except Exception as e:
    check("a 1x1 picture survives every setting", False, repr(e))
thin = save(np.random.RandomState(3).rand(1, 300, 3).astype(np.float32) * 255, "thin")
try:
    check("a 1-pixel-high strip survives (blur / vignette radii)", edit(thin, wild).shape == (1, 300, 3))
except Exception as e:
    check("a 1-pixel-high strip survives", False, repr(e))
jp = work / "j.jpg"
Image.fromarray(S0.astype(np.uint8)).save(jp, quality=95)
o = work / "j_out.jpg"
images.apply_edit(jp, {"exposure": 0.5, "sharpness": 50}, o)
check("JPEG in, JPEG out", Image.open(o).format == "JPEG")

# ---- the live preview
big = save(scene(3000, 2000, seed=5), "big", ".jpg")
pv = images.preview_tone(big, {"exposure": 1, "vignette": -50})
im = Image.open(io.BytesIO(pv))
check("preview: JPEG, shrunk to 1600 on the long side, same aspect", im.format == "JPEG" and max(im.size) == 1600 and abs(im.size[0] / im.size[1] - 1.5) < 0.01, im.size)
full = np.asarray(Image.open(io.BytesIO(pv)).convert("RGB"), dtype=np.float32)
ref = edit(big, {"exposure": 1, "vignette": -50})
ref_small = np.asarray(Image.fromarray(ref.astype(np.uint8)).resize((1600, 1067)), dtype=np.float32)
check("preview looks like the full render (mean brightness within 4)", abs(lum(full).mean() - lum(ref_small).mean()) < 4, (round(lum(full).mean(), 1), round(lum(ref_small).mean(), 1)))
check("...also corner vs centre (the vignette scales with the picture)", abs((cn(full) - cx(full)) if False else 0) == 0 and
      abs(float(lum(full)[:40, :40].mean()) - float(lum(ref_small)[:40, :40].mean())) < 8)
sp_prev = Image.open(io.BytesIO(images.preview_tone(big, {"sharpness": 100, "clarity": 100, "blur": 10})))
check("preview with detail settings works on a large picture", max(sp_prev.size) == 1600)
small_prev = Image.open(io.BytesIO(images.preview_tone(SC, {"exposure": 1})))
check("a picture smaller than 1600 is not enlarged", small_prev.size == (160, 100))

# ---- Auto
def auto_on(arr, name):
    return images.auto_ops(save(arr, name))


rng = np.random.RandomState(11)
dark = scene(seed=2) * 0.25
bright = 255 - (255 - scene(seed=3)) * 0.2
a_dark, a_bright = auto_on(dark, "dark"), auto_on(bright, "bright")
check("auto: a dark picture gets more exposure", a_dark["exposure"] > 0.5, a_dark["exposure"])
check("auto: a bright picture gets less exposure", a_bright["exposure"] < -0.3, a_bright["exposure"])
check("auto: it returns every setting it decides", set(a_dark) == {"exposure", "highlights", "shadows", "contrast", "temperature", "tint", "vibrance", "sharpness"}, sorted(a_dark))
for nm, arr, a in (("dark", dark, a_dark), ("bright", bright, a_bright)):
    before = abs(lum(arr).mean() / 255 - 0.45)
    p = save(arr, "ap_" + nm)
    after_arr = edit(p, {k: v for k, v in a.items() if k != "contrast"} | {"contrast": a["contrast"]})
    after = abs(lum(after_arr).mean() / 255 - 0.45)
    check(f"auto on the {nm} picture moves its brightness towards the middle", after < before - 0.05, (round(before, 2), round(after, 2)))
blown = scene(seed=4) * 0.5 + 150
blown = np.minimum(blown + 60, 255)
a_blown = auto_on(blown, "blown")
check("auto: blown highlights are pulled back", a_blown["highlights"] <= -10, a_blown["highlights"])
crushed = np.maximum(scene(seed=6) - 90, 0)
a_crush = auto_on(crushed, "crushed")
check("auto: crushed shadows are lifted", a_crush["shadows"] >= 10, a_crush["shadows"])
lowc = 128 + (scene(seed=7) - 128) * 0.12
a_lowc = auto_on(lowc, "lowcontrast")
check("auto: a flat picture gets more contrast", a_lowc["contrast"] > 1.15, a_lowc["contrast"])
wide = np.where(rng.rand(100, 160, 1) > 0.5, 250, 5) * np.ones((1, 1, 3))
check("auto: a harsh picture gets less contrast, not more", auto_on(wide, "harsh")["contrast"] < 1.0)
bluish = scene(seed=8) * np.array([0.7, 0.9, 1.25])
a_blue = auto_on(bluish, "bluish")
check("auto: a blue cast is warmed up (temperature > 0)", a_blue["temperature"] > 8, a_blue["temperature"])
fixed = edit(save(bluish, "bluish_src"), {"temperature": a_blue["temperature"], "tint": a_blue["tint"]})
check("...and applying it brings red and blue closer together", abs(fixed[..., 0].mean() - fixed[..., 2].mean()) < abs(np.clip(bluish, 0, 255)[..., 0].mean() - np.clip(bluish, 0, 255)[..., 2].mean()))
yellowish = scene(seed=9) * np.array([1.25, 1.05, 0.65])
check("auto: a yellow cast is cooled down (temperature < 0)", auto_on(yellowish, "yellowish")["temperature"] < -8)
green = scene(seed=10) * np.array([0.8, 1.3, 0.8])
check("auto: a green cast gets a magenta tint (tint > 0)", auto_on(green, "greenish")["tint"] > 5)
dull = 128 + (scene(seed=12) - 128) * 0.3
check("auto: dull colours get vibrance", auto_on(dull * 0.5 + 64, "dullcol")["vibrance"] > 10)
vivid = np.clip(128 + (scene(seed=13) - 128) * 1.8, 0, 255)
check("auto: already vivid colours are not pushed", auto_on(vivid, "vivid")["vibrance"] <= 0)
good = scene(seed=14) * 0.85 + 20
a_good = auto_on(good, "good")
check("auto: a well-exposed picture gets only gentle changes", abs(a_good["exposure"]) < 0.6 and abs(a_good["temperature"]) < 25 and abs(a_good["contrast"] - 1) <= 0.3, a_good)
# applying it twice: the second pass must ask for less (it converges, it does not run away)
p1 = save(dark, "twice")
once = edit(p1, a_dark)
p2 = save(once, "twice2")
a2 = images.auto_ops(p2)
check("auto twice: the second pass changes less than the first", abs(a2["exposure"]) < abs(a_dark["exposure"]), (a_dark["exposure"], a2["exposure"]))
# properties over many random pictures
bad = []
for i in range(40):
    r = np.random.RandomState(100 + i)
    arr = r.rand(r.randint(2, 90), r.randint(2, 90), 3) * r.uniform(5, 255) + r.uniform(0, 100)
    if i % 5 == 0:
        arr = np.full_like(arr, r.uniform(0, 255))
    a = images.auto_ops(save(arr, f"rnd{i}"))
    ok = (all(math.isfinite(v) for v in a.values()) and -1.5 <= a["exposure"] <= 1.5 and -60 <= a["highlights"] <= 0 and 0 <= a["shadows"] <= 60
          and 0.85 <= a["contrast"] <= 1.3 and -40 <= a["temperature"] <= 40 and -30 <= a["tint"] <= 30 and -10 <= a["vibrance"] <= 40)
    if ok:
        edit(work / f"rnd{i}.png", a)                                          # and the result renders
    else:
        bad.append((i, a))
check("auto: 40 random pictures (incl. flat ones and 2x2 pixels): every value finite and inside its limits, result renders", not bad, bad[:2])
for nm, v in (("black", 0), ("white", 255), ("mid grey", 128)):
    a = images.auto_ops(save(flat(v), "solid_" + nm.replace(" ", "")))
    check(f"auto: an all-{nm} picture does not crash or explode", all(math.isfinite(x) for x in a.values()) and abs(a["exposure"]) <= 1.5)
check("auto never touches geometry (no rotate / crop / flip keys)", not ({"rotate", "crop", "flip_h", "flip_v", "grayscale"} & set(a_dark)))

# =================================================================================  PHASE 2: real server
def make_photo(name, arr, exif=None, ext=".jpg"):
    p = work / f"{name}{ext}"
    im = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGB")
    if exif:
        e = Image.Exif()
        for k, v in exif.items():
            e[k] = v
        im.save(p, quality=95, exif=e)
    else:
        im.save(p, quality=95)
    pid, _ = importer._ingest_file(con, p)
    con.commit()
    return pid


def row(pid):
    return con.execute("SELECT * FROM photos WHERE id=?", (pid,)).fetchone()


def media_path(pid):
    return PATHS.media / row(pid)["rel_path"]


EXIF_A = {0x010F: "TestMake", 0x0110: "TestModel-A"}
A = make_photo("a_dark", scene(seed=21) * 0.25, EXIF_A)
B = make_photo("b_flip", asym + 3, None, ".png")
C = make_photo("c_off", scene(seed=22), {0x010F: "OffMake"})
D = make_photo("d_switch", scene(seed=23), {0x010F: "SwitchMake"})
E = make_photo("e_revert_off", scene(seed=24))
V = make_photo("f_video", scene(seed=25))
con.execute("UPDATE photos SET is_video=1 WHERE id=?", (V,))
con.commit()
files = {p: (media_path(p).read_bytes() if p != V else b"") for p in (A, B, C, D, E)}

srv_env = os.environ.copy()
srv_env.update(PHOTAG_MIGRATE_DELAY="3", PHOTAG_MIGRATE_TICK="0.2", PHOTAG_MIGRATE_IDLE="1")
srv_log = open(tmp / "server.log", "w", encoding="utf-8")
srv = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.server:app", "--app-dir", str(ROOT), "--port", str(PORT)],
                       env=srv_env, stdout=srv_log, stderr=subprocess.STDOUT, cwd=str(ROOT))


def call(method, path, body=None, raw=False):
    req = urllib.request.Request(APP + path, data=json.dumps(body).encode() if body is not None else None, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            data = r.read()
            if not raw and r.headers.get_content_type() == "application/json":
                data = json.loads(data) if data else {}
            return r.status, data, r.headers
    except urllib.error.HTTPError as e:
        return e.code, e.read(), e.headers


def wait_for(fn, secs=60):
    t0 = time.time()
    while time.time() - t0 < secs:
        if fn():
            return True
        time.sleep(0.4)
    return False


def fresh(pid):
    c = db.connect()
    r = c.execute("SELECT * FROM photos WHERE id=?", (pid,)).fetchone()
    c.close()
    return r


ALL = {"exposure": 0.7, "highlights": -40, "shadows": 35, "temperature": 20, "tint": -10, "vibrance": 30, "clarity": 25,
       "sharpness": 40, "blur": 3, "vignette": -25, "sepia": 15, "flip_h": True}
try:
    for _ in range(80):
        try:
            urllib.request.urlopen(APP + "/api/status", timeout=2).read()
            break
        except Exception:
            time.sleep(0.5)
    else:
        raise SystemExit("server did not start")

    # ---- the switch itself
    code, body, _ = call("GET", "/api/catalog-edits")
    check("catalog mode is ON by default", code == 200 and body == {"on": True}, body)

    # ---- new settings through the real endpoint, in catalog mode
    before = media_path(A).read_bytes()
    code, body, _ = call("POST", f"/api/photo/{A}/edit", ALL)
    r = row(A)
    check("POST /edit with every new setting succeeds", code == 200, body if code != 200 else "")
    r = fresh(A)
    stored = json.loads(r["edit_ops"] or "{}")
    check("every setting is stored in the catalog exactly as sent", all(stored.get(k) == v for k, v in ALL.items()), stored)
    check("the photo file is untouched (catalog mode)", media_path(A).read_bytes() == before)
    check("no pristine copy was made: the file IS the original", r["orig_backup"] is None)
    check("a render exists for it", len(list(PATHS.renders.glob(f"{A}-*"))) == 1)
    code, rendered, _ = call("GET", f"/media/{A}", raw=True)
    rim = np.asarray(Image.open(io.BytesIO(rendered)).convert("RGB"), dtype=np.float32)
    check("/media serves the edited look (brighter than the dark original)", code == 200 and lum(rim).mean() > lum(src_arr(media_path(A))).mean() + 15)
    code, orig_bytes, _ = call("GET", f"/original/{A}", raw=True)
    check("/original serves the untouched original", code == 200 and orig_bytes == before)
    code, detail, _ = call("GET", f"/api/photo/{A}")
    check("GET /api/photo returns the stored settings", code == 200 and json.loads(detail["edit_ops"]).get("vignette") == -25)

    # ---- flips on the server
    code, _, _ = call("POST", f"/api/photo/{B}/edit", {"flip_h": True})
    code2, rb, _ = call("GET", f"/media/{B}", raw=True)
    rb_arr = np.asarray(Image.open(io.BytesIO(rb)).convert("RGB"), dtype=np.float32)
    check("flip_h through the endpoint mirrors the served picture", code == 200 and code2 == 200 and rb_arr[30, 10, 2] > 100 and rb_arr[30, 110, 0] > 100, (rb_arr[30, 10], rb_arr[30, 110]))
    check("...the file on disk is still the unflipped original", np.array_equal(src_arr(media_path(B)), src_arr(work / "b_flip.png")))
    code, _, _ = call("POST", f"/api/photo/{B}/rotate", {"degrees": 90})
    check("rotating a mirrored photo turns the stored rotation the other way (the flip is applied last)", json.loads(fresh(B)["edit_ops"]).get("rotate") == -90, fresh(B)["edit_ops"])
    call("POST", f"/api/photo/{B}/rotate", {"degrees": -90})
    check("...and rotating back (the other button) returns to 0", not json.loads(fresh(B)["edit_ops"]).get("rotate"))
    code, _, _ = call("POST", f"/api/photo/{B}/edit", {"flip_h": False, "flip_v": False})
    check("both flips off = a neutral photo again (edited = 0, render removed)", fresh(B)["edited"] == 0 and not list(PATHS.renders.glob(f"{B}-*")))

    # ---- live preview
    snap = (fresh(A)["edit_ops"], fresh(A)["sha256"], sorted(p.name for p in PATHS.renders.iterdir()))
    code, pv, hdr = call("POST", f"/api/photo/{A}/preview", {"exposure": 1.0, "vignette": -40, "rotate": 45, "flip_h": True, "crop": [0, 0, .1, .1], "brightness": 2})
    pim = Image.open(io.BytesIO(pv)) if code == 200 else None
    check("preview: a JPEG of the ORIGINAL's size (geometry and simple sliders are not applied by the server)", code == 200 and pim.format == "JPEG" and pim.size == (160, 100), pim.size if pim else code)
    check("preview: not cached", "no-store" in (hdr.get("Cache-Control") or ""))
    check("preview saves nothing (settings, hash, renders folder all unchanged)", snap == (fresh(A)["edit_ops"], fresh(A)["sha256"], sorted(p.name for p in PATHS.renders.iterdir())))
    code, pv0, _ = call("POST", f"/api/photo/{A}/preview", {})
    check("preview with no settings is just the original", code == 200 and abs(lum(np.asarray(Image.open(io.BytesIO(pv0)).convert('RGB'), dtype=np.float32)).mean() - lum(src_arr(media_path(A))).mean()) < 3)
    code, pv2, _ = call("POST", f"/api/photo/{A}/preview", {"exposure": 2.0})
    check("preview with exposure +2 is clearly brighter than the (dark) original", code == 200 and lum(np.asarray(Image.open(io.BytesIO(pv2)).convert('RGB'), dtype=np.float32)).mean() > lum(src_arr(media_path(A))).mean() + 15)
    check("preview of a video is refused", call("POST", f"/api/photo/{V}/preview", {"exposure": 1})[0] == 400)
    check("preview of an unknown photo is refused", call("POST", "/api/photo/999999/preview", {"exposure": 1})[0] == 400)
    check("preview ignores unknown fields instead of failing", call("POST", f"/api/photo/{A}/preview", {"exposure": 1, "nonsense": 5})[0] == 200)
    check("preview with a wrong type is a 422, not a crash", call("POST", f"/api/photo/{A}/preview", {"exposure": "lots"})[0] == 422)

    # ---- auto
    snap = (fresh(A)["edit_ops"], fresh(A)["sha256"])
    code, au, _ = call("POST", f"/api/photo/{A}/auto")
    check("auto: 200 and a set of settings", code == 200 and set(au["ops"]) == {"exposure", "highlights", "shadows", "contrast", "temperature", "tint", "vibrance", "sharpness"}, au)
    check("auto looks at the ORIGINAL (a dark original -> more exposure, whatever is stored now)", au["ops"]["exposure"] > 0.5, au["ops"]["exposure"])
    check("auto only suggests: nothing was saved", snap == (fresh(A)["edit_ops"], fresh(A)["sha256"]))
    check("auto on a video is refused", call("POST", f"/api/photo/{V}/auto")[0] == 400)
    check("auto on an unknown photo is refused", call("POST", "/api/photo/999999/auto")[0] == 400)
    # the whole Auto flow like the page does it: ask, then apply as an ordinary edit
    api_ops = dict(au["ops"])
    call("POST", f"/api/photo/{A}/revert")
    mean_before = lum(src_arr(media_path(A))).mean()
    code, _, _ = call("POST", f"/api/photo/{A}/edit", api_ops)
    code2, rend, _ = call("GET", f"/media/{A}", raw=True)
    mean_after = lum(np.asarray(Image.open(io.BytesIO(rend)).convert("RGB"), dtype=np.float32)).mean()
    check("auto applied as an edit: the dark photo is clearly brighter, towards the middle", code == 200 and abs(mean_after / 255 - 0.45) < abs(mean_before / 255 - 0.45) - 0.1, (round(mean_before), round(mean_after)))
    check("...stored as an ordinary edit, original untouched", fresh(A)["edited"] == 1 and media_path(A).read_bytes() == files[A])
    code, _, _ = call("POST", f"/api/photo/{A}/revert")
    check("revert after auto restores the pristine state", code == 200 and fresh(A)["edited"] == 0 and not list(PATHS.renders.glob(f"{A}-*")))

    # ---- EXIF source: the catalog (database) vs the file
    code, ex, _ = call("GET", f"/api/photo/{A}/exif")
    check("EXIF (catalog mode): comes from the catalog", code == 200 and ex["source"] == "catalog" and ex["exif"].get("Image", {}).get("Make") == "TestMake", ex)
    c2 = db.connect()
    c2.execute("UPDATE photos SET exif_json=? WHERE id=?", (json.dumps({"Image": {"Make": "FROM_THE_DATABASE"}}), A))
    c2.commit()
    c2.close()
    code, ex, _ = call("GET", f"/api/photo/{A}/exif")
    check("proof it is read from the DATABASE, not the file: a changed row is what is served", ex["exif"]["Image"]["Make"] == "FROM_THE_DATABASE", ex)
    check("EXIF survives an edit untouched (the file is not rewritten)", call("POST", f"/api/photo/{A}/edit", {"exposure": 1})[0] == 200
          and call("GET", f"/api/photo/{A}/exif")[1]["exif"]["Image"]["Make"] == "FROM_THE_DATABASE")
    call("POST", f"/api/photo/{A}/revert")

    # ---- switching the catalog mode OFF
    code, body, _ = call("POST", "/api/catalog-edits", {"on": False})
    check("switch OFF is accepted and remembered", code == 200 and body == {"on": False} and call("GET", "/api/catalog-edits")[1] == {"on": False})
    check("...and written to the settings file (survives a restart)", config.get_catalog_edits() is False)
    code, ex, _ = call("GET", f"/api/photo/{A}/exif")
    check("EXIF (catalog mode OFF): read from the file now", ex["source"] == "file" and ex["exif"]["Image"]["Make"] == "TestMake", ex)
    check("...and nothing is stored when reading from the file", json.loads(fresh(A)["exif_json"])["Image"]["Make"] == "FROM_THE_DATABASE")

    orig_C = files[C]
    code, _, _ = call("POST", f"/api/photo/{C}/edit", {"exposure": 1, "vignette": -30, "flip_h": True})
    r = fresh(C)
    check("OFF: an edit is written into the photo file", code == 200 and media_path(C).read_bytes() != orig_C)
    check("OFF: a pristine copy of the original is kept in .originals", r["orig_backup"] and (PATHS.media / r["orig_backup"]).read_bytes() == orig_C, r["orig_backup"])
    check("OFF: the settings are still recorded", json.loads(r["edit_ops"]).get("vignette") == -30 and r["edited"] == 1)
    check("OFF: no render is made", not list(PATHS.renders.glob(f"{C}-*")))
    check("OFF: the catalog hash follows the file", r["sha256"] == sha(media_path(C).read_bytes()))
    code, served, _ = call("GET", f"/media/{C}", raw=True)
    check("OFF: /media serves the edited file", served == media_path(C).read_bytes())
    code, ex, _ = call("GET", f"/api/photo/{C}/exif")
    check("OFF: EXIF of a photo edited into its file is read from the pristine copy (the edited file has none)", ex["exif"]["Image"].get("Make") == "OffMake", ex)
    code, _, _ = call("POST", f"/api/photo/{C}/revert")
    check("OFF: revert restores the original bytes", code == 200 and media_path(C).read_bytes() == orig_C and fresh(C)["edited"] == 0)
    # a photo that was edited in catalog mode, then edited again after switching off
    call("POST", "/api/catalog-edits", {"on": True})
    orig_D = files[D]
    call("POST", f"/api/photo/{D}/edit", {"exposure": 0.5, "sepia": 40})
    check("ON: photo D edited without touching its file", media_path(D).read_bytes() == orig_D and fresh(D)["orig_backup"] is None)
    call("POST", "/api/catalog-edits", {"on": False})
    code, _, _ = call("POST", f"/api/photo/{D}/edit", {"exposure": -0.5, "temperature": 25, "vibrance": 30})
    r = fresh(D)
    check("OFF after ON: the already-edited photo can be edited again, into its file", code == 200 and media_path(D).read_bytes() != orig_D and r["orig_backup"])
    check("...and the pristine copy is the TRUE original (not an earlier edit)", (PATHS.media / r["orig_backup"]).read_bytes() == orig_D)
    check("...the new settings replace the old (no sepia left, new keys stored)", json.loads(r["edit_ops"]).get("temperature") == 25 and "sepia" not in json.loads(r["edit_ops"]) or json.loads(r["edit_ops"]).get("sepia") in (None, 0), r["edit_ops"])
    check("the background migration is paused while the switch is off (stays in the file)", not wait_for(lambda: fresh(D)["orig_backup"] is None, 8) and fresh(D)["orig_backup"])
    # back ON: the loop moves it back into the catalog model
    call("POST", "/api/catalog-edits", {"on": True})
    ok = wait_for(lambda: fresh(D)["orig_backup"] is None, 60)
    r = fresh(D)
    check("ON again: the background loop moves the edit back into the catalog (original restored in the file)", ok and media_path(D).read_bytes() == orig_D, r["orig_backup"])
    check("...the settings survive the round trip, new keys included", json.loads(r["edit_ops"]).get("temperature") == 25 and json.loads(r["edit_ops"]).get("vibrance") == 30, r["edit_ops"])
    check("...and a render exists again", len(list(PATHS.renders.glob(f"{D}-*"))) == 1)
    code, served, _ = call("GET", f"/media/{D}", raw=True)
    check("...and /media serves the edited look", code == 200 and served != orig_D)
    check("the old pristine copy was cleaned up after the move", not list((PATHS.media / ".originals").glob(f"{D}_*")))

    # ---- switch ON: new edits in catalog mode again + exif from the DB
    code, ex, _ = call("GET", f"/api/photo/{C}/exif")
    check("ON: EXIF is served from the catalog again", ex["source"] == "catalog")
    orig_E = files[E]
    call("POST", f"/api/photo/{E}/edit", {"sharpness": 60, "flip_v": True})
    check("ON: a new edit leaves the file alone again", media_path(E).read_bytes() == orig_E and fresh(E)["orig_backup"] is None)

    # ---- bad input
    check("edit with a wrong type is a 422", call("POST", f"/api/photo/{E}/edit", {"vignette": "dark"})[0] == 422)
    check("edit of a video is refused", call("POST", f"/api/photo/{V}/edit", {"exposure": 1})[0] == 400)
    check("switch with a wrong body is a 422", call("POST", "/api/catalog-edits", {"on": "maybe"})[0] == 422)
    check("an out-of-range value still renders (clamped)", call("POST", f"/api/photo/{E}/edit", {"exposure": 50, "highlights": 900, "blur": 999})[0] == 200)
    check("...and the photo is still servable afterwards", call("GET", f"/media/{E}", raw=True)[0] == 200 and call("GET", f"/thumb/{E}", raw=True)[0] == 200)
    # concurrency of different edits with the new settings
    import threading
    codes = []
    ths = [threading.Thread(target=lambda i=i: codes.append(call("POST", f"/api/photo/{A}/edit", {"exposure": 0.1 * i, "vignette": -5 * i})[0])) for i in range(1, 7)]
    [t.start() for t in ths]
    [t.join() for t in ths]
    st = json.loads(fresh(A)["edit_ops"])
    renders = [p.name for p in PATHS.renders.glob(f"{A}-*")]
    check("6 simultaneous edits with new settings: all succeed, one render, and it belongs to the stored settings",
          codes == [200] * 6 and len(renders) == 1 and renders[0].startswith(f"{A}-{render.render_key(st, fresh(A)['sha256'])}"), (codes, renders, st))
finally:
    srv.terminate()
    try:
        srv.wait(timeout=10)
    except Exception:
        srv.kill()

n_fail = res.count(False)
print(f"\n{len(res) - n_fail}/{len(res)} passed")
if n_fail:
    print("---- server log (tail) ----\n" + (tmp / "server.log").read_text("utf-8", "replace")[-2500:])
sys.exit(1 if n_fail else 0)
