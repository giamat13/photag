"""Test: the place-and-shape Develop tools (app/develop_local.py): lens corrections, perspective, upright, spot removal, red eye, colour
look-up tables and local adjustments with masks.

    py -3.12 tools/test_develop_local.py
"""
import sys
import tempfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from PIL import Image, ImageDraw  # noqa: E402
from app import develop_local as L, develop_ops as D, images  # noqa: E402

res = []


def check(name, ok, extra=""):
    res.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{extra}]" if extra else ""))


def arr(im):
    return np.asarray(im.convert("RGB"), dtype=np.float32) / 255.0


def pil(a):
    return Image.fromarray((np.clip(a, 0, 1) * 255 + 0.5).astype(np.uint8))


rng = np.random.default_rng(3)
W, H = 320, 220

# ---- what is accepted
c = L.clean({"lens_dist": 900, "persp_v": -900, "geo_scale": 10, "masks": [{"comps": [{"kind": "evil"}], "adj": {}}] * 20, "spots": [{"x": 5, "y": -1, "r": 9}] * 100,
             "lut": {"name": "../../etc/passwd.cube", "amount": 500}})
check("numbers are clamped", c["lens_dist"] == 100 and c["persp_v"] == -100 and c["geo_scale"] == 100)
check("masks: unknown component kinds are dropped, at most 8 masks", len(c["masks"]) == 8 and all(m["comps"] == [] for m in c["masks"]))
check("spots: at most 60, positions clamped", len(c["spots"]) == 60 and c["spots"][0]["x"] == 1.0 and c["spots"][0]["y"] == 0.0 and c["spots"][0]["r"] == 0.25)
check("a LUT name loses its folders, its amount is clamped", c["lut"] == {"name": "passwd.cube", "amount": 100})
check("nothing set -> not active", not images.has_tone({}) and not images.has_tone({"geo_scale": 100, "masks": [], "spots": []}) and images.has_tone({"geo_scale": 120}))

# ---- lens distortion
grid = Image.new("RGB", (W, H), "white")
d = ImageDraw.Draw(grid)
for x in range(0, W, 20):
    d.line([(x, 0), (x, H)], fill="black", width=2)
for y in range(0, H, 20):
    d.line([(0, y), (W, y)], fill="black", width=2)
g0 = arr(grid)
check("distortion 0 changes nothing", np.array_equal(arr(L.optics(grid, {})), g0))
d1 = arr(L.distort(grid, 60))
check("distortion keeps the size and moves the picture (not at the corners)", d1.shape == g0.shape and np.abs(d1 - g0).mean() > 0.01 and np.abs(d1[:3, :3] - g0[:3, :3]).mean() < 0.2)
check("a positive and a negative amount move it in opposite ways", np.abs(arr(L.distort(grid, 60)) - arr(L.distort(grid, -60))).mean() > 0.01)
# ---- vignetting of the lens
flat = np.full((H, W, 3), 0.4, dtype=np.float32)
v = L.vignette_gain(flat, 100)
check("lens vignetting: + brightens the corners, the centre stays", v[0, 0, 0] > 0.6 and abs(v[H // 2, W // 2, 0] - 0.4) < 0.02 and L.vignette_gain(flat, -100)[0, 0, 0] < 0.2)
# ---- chromatic aberration: red channel scaled by 0.4% relative to green
base = np.zeros((H, W, 3), dtype=np.float32)
bi = Image.new("RGB", (W, H), "black")
dd = ImageDraw.Draw(bi)
for x in range(10, W, 24):
    dd.line([(x, 0), (x, H)], fill="white", width=3)
for y in range(10, H, 24):
    dd.line([(0, y), (W, y)], fill="white", width=3)
shifted = L._scale_channel(bi, 0, 1.004)
sr, sb = L.estimate_ca(shifted)
check("auto chromatic aberration finds the red scale (about 1/1.004)", abs(sr - 1 / 1.004) < 0.0015 and abs(sb - 1) < 0.0015, (round(sr, 4), round(sb, 4)))
fixed = L.optics(shifted, {"ca_auto": True})
sr2, _ = L.estimate_ca(fixed)
check("...and puts the red edges back (the remaining offset is far smaller)", abs(sr2 - 1) < abs(sr - 1) * 0.4, (round(sr, 4), round(sr2, 4)))
# ---- defringe
fr = np.zeros((H, W, 3), dtype=np.float32)
fr[:, : W // 2] = 0.9
fr[:, W // 2: W // 2 + 4] = (0.6, 0.2, 0.8)                                       # a purple fringe on an edge
out = L.defringe(fr, 100)
check("defringe drains the colour of a purple fringe on an edge", D.rgb_to_hsv(out[100:120, W // 2: W // 2 + 4])[1].mean() < D.rgb_to_hsv(fr[100:120, W // 2: W // 2 + 4])[1].mean() * 0.6)
check("...and leaves a flat colour alone", np.allclose(L.defringe(np.full((60, 60, 3), (0.6, 0.2, 0.8), dtype=np.float32), 100), 0.6 * 0 + np.array((0.6, 0.2, 0.8)), atol=0.02))

# ---- geometry
check("geometry with nothing set returns the same picture", L.geometry(grid, {}) is grid)
for ops in ({"persp_v": 60}, {"persp_h": -50}, {"geo_aspect": 40}, {"geo_scale": 140}, {"persp_v": 80, "geo_x": 100, "geo_y": -100}):
    gm = arr(L.geometry(Image.new("RGB", (W, H), (200, 120, 40)), ops))
    check(f"geometry {ops}: the frame stays completely covered (no empty corners)", gm.min() > 0.1 and gm.shape == (H, W, 3), round(float(gm.min()), 3))
vert = Image.new("RGB", (W, H), "white")
dv = ImageDraw.Draw(vert)
for x in range(20, W, 30):
    dv.line([(x, 0), (x, H)], fill="black", width=3)
tv = arr(L.geometry(vert, {"persp_v": 70}))
check("vertical perspective changes the picture", np.abs(tv - arr(vert)).mean() > 0.02)
check("scale zooms in (the centre stays, the edge moves)", np.abs(arr(L.geometry(grid, {"geo_scale": 150})) - g0).mean() > 0.02)

# ---- upright: a picture of vertical / horizontal lines, turned and keystoned by known amounts
def lines_pic(rot=0.0):
    im = Image.new("RGB", (480, 360), "white")
    q = ImageDraw.Draw(im)
    for x in range(30, 480, 45):
        q.line([(x, 0), (x, 360)], fill="black", width=3)
    for y in range(30, 360, 45):
        q.line([(0, y), (480, y)], fill="black", width=3)
    return im.rotate(rot, resample=Image.BICUBIC, fillcolor="white") if rot else im


up = L.upright(lines_pic(4.0), "level")
check("upright (level): a picture turned 4 degrees is turned back by about the same", abs(abs(up["rotate"]) - 4.0) < 1.2 and up["persp_v"] == 0, up)
flat_up = L.upright(lines_pic(0.0), "level")
check("upright (level): a true picture is left alone", abs(flat_up["rotate"]) < 0.6, flat_up)
key = L.geometry(lines_pic(0), {"persp_v": 45})
upv = L.upright(key, "vertical")
check("upright (vertical): keystoned vertical lines are straightened (the suggestion is about the opposite perspective)", upv["persp_v"] < -15, upv)

# ---- spots and red eye
a = np.full((200, 240, 3), (0.3, 0.5, 0.7), dtype=np.float32) + rng.standard_normal((200, 240, 1)).astype(np.float32) * 0.01
a[100:106, 120:126] = 0.05                                                         # a dust spot
fixed = L.spots(a, [{"x": 123 / 240, "y": 103 / 200, "r": 0.04, "mode": "heal", "feather": 40, "opacity": 100}])
check("spot removal (automatic source): the dust is gone", abs(fixed[103, 123, 0] - 0.3) < 0.06 and np.abs(fixed[:60] - a[:60]).max() < 1e-6)
fixed2 = L.spots(a, [{"x": 123 / 240, "y": 103 / 200, "r": 0.04, "mode": "clone", "sx": 40 / 240, "sy": 40 / 200, "feather": 30, "opacity": 100}])
check("spot removal (clone from a chosen place)", abs(fixed2[103, 123, 0] - 0.3) < 0.06)
half = L.spots(a, [{"x": 123 / 240, "y": 103 / 200, "r": 0.04, "mode": "heal", "feather": 40, "opacity": 40}])
check("opacity 40 % leaves a part of the dust", 0.08 < half[103, 123, 0] < 0.25, half[103, 123, 0])
edge = L.spots(a, [{"x": 0.0, "y": 0.0, "r": 0.05, "mode": "heal"}, {"x": 1.0, "y": 1.0, "r": 0.05, "mode": "clone"}])
check("spots at the very edge of the picture do not fail", edge.shape == a.shape)
eye = np.full((120, 120, 3), 0.5, dtype=np.float32)
eye[50:70, 50:70] = (0.9, 0.1, 0.1)
re = L.redeye(eye, [{"x": 60 / 120, "y": 60 / 120, "r": 0.12, "amount": 100}])
check("red eye: the red pupil becomes dark and neutral, the surroundings stay", re[60, 60, 0] < 0.25 and abs(re[60, 60, 0] - re[60, 60, 1]) < 0.15 and np.allclose(re[5, 5], 0.5))

# ---- look-up tables
size = 4
ident = np.array([[r / (size - 1), g / (size - 1), b / (size - 1)] for b in range(size) for g in range(size) for r in range(size)], dtype=np.float32).reshape(size, size, size, 3)
px = np.array([[[0.2, 0.6, 0.9], [0.8, 0.1, 0.4]]], dtype=np.float32)
check("an identity LUT changes nothing", np.allclose(L.apply_lut(px, size, ident, 100), px, atol=1e-5))
inv = 1 - ident
check("an inverting LUT inverts, and its amount blends", np.allclose(L.apply_lut(px, size, inv, 100), 1 - px, atol=1e-5) and np.allclose(L.apply_lut(px, size, inv, 50), 0.5 * px + 0.5 * (1 - px), atol=1e-5))
cube = "TITLE x\nLUT_3D_SIZE 2\n" + "\n".join(f"{r} {g} {b}" for b in (0, 1) for g in (0, 1) for r in (0, 1)) + "\n"
s2, t2 = L.parse_cube(cube)
check("a .cube file is read", s2 == 2 and np.allclose(t2[0, 0, 1], (1, 0, 0)))
bad = False
for text in ("LUT_3D_SIZE 2\n1 2 3\n", "LUT_1D_SIZE 4\n0 0 0\n", "garbage"):
    try:
        L.parse_cube(text)
    except ValueError:
        bad = True
    else:
        bad = False
        break
check("broken or 1D files are refused", bad)
check("a missing LUT file changes nothing", np.array_equal(L.lut(px, {"name": "does-not-exist.cube", "amount": 100}), px))

# ---- masks
pic = np.dstack([np.tile(np.linspace(0, 1, 200, dtype=np.float32)[None, :], (140, 1))] * 3)
lin = L.component({"kind": "linear", "x1": 0.25, "y1": 0.5, "x2": 0.75, "y2": 0.5, "invert": False}, pic)
check("linear mask: full before the first line, zero after the second", lin[:, 10].mean() > 0.99 and lin[:, 190].mean() < 0.01 and 0.3 < lin[:, 100].mean() < 0.7)
rad = L.component({"kind": "radial", "cx": 0.5, "cy": 0.5, "rx": 0.2, "ry": 0.2, "rot": 0, "feather": 50, "invert": False}, pic)
check("radial mask: full in the middle, zero far away", rad[70, 100] > 0.99 and rad[5, 5] < 0.01)
check("invert turns a mask over", abs(float(L.component({"kind": "radial", "cx": 0.5, "cy": 0.5, "rx": 0.2, "ry": 0.2, "rot": 0, "feather": 50, "invert": True}, pic)[70, 100])) < 0.01)
lum = L.component({"kind": "luminance", "lo": 0.7, "hi": 1.0, "smooth": 30, "invert": False}, pic)
check("luminance range: only the bright part", lum[:, 185].mean() > 0.9 and lum[:, 20].mean() < 0.05)
cp = np.zeros((100, 100, 3), dtype=np.float32)
cp[:, :50] = (0.8, 0.2, 0.2)
cp[:, 50:] = (0.2, 0.3, 0.8)
cm = L.component({"kind": "color", "color": [204, 51, 51], "tol": 40, "invert": False}, cp)
check("colour range: picks the red half, not the blue half", cm[:, :50].mean() > 0.8 and cm[:, 50:].mean() < 0.2, (round(float(cm[:, :50].mean()), 2), round(float(cm[:, 50:].mean()), 2)))
bm = L.component({"kind": "brush", "strokes": [{"pts": [[0.2, 0.5], [0.8, 0.5]], "size": 0.2, "feather": 30, "flow": 100, "erase": False}], "invert": False}, pic)
check("brush: paint along a stroke", bm[70, 100] > 0.9 and bm[5, 100] < 0.05 and bm[70, 5] < 0.05)
em = L.component({"kind": "brush", "strokes": [{"pts": [[0.2, 0.5], [0.8, 0.5]], "size": 0.2, "feather": 0, "flow": 100, "erase": False}, {"pts": [[0.5, 0.5]], "size": 0.08, "feather": 0, "flow": 100, "erase": True}], "invert": False}, pic)
check("brush: erase takes paint away", em[70, 100] < 0.1 and em[70, 50] > 0.9)
half_flow = L.component({"kind": "brush", "strokes": [{"pts": [[0.5, 0.5]], "size": 0.3, "feather": 0, "flow": 50, "erase": False}], "invert": False}, pic)
check("brush: flow 50 paints half strength", 0.4 < half_flow[70, 100] < 0.6, half_flow[70, 100])
m1 = {"comps": [{"kind": "linear", "op": "add", "x1": 0.5, "y1": 0.5, "x2": 0.5, "y2": 0.5, "invert": False}]}
add = L.mask_of({"comps": [{"kind": "radial", "op": "add", "cx": .3, "cy": .5, "rx": .1, "ry": .1, "rot": 0, "feather": 20, "invert": False},
                           {"kind": "radial", "op": "add", "cx": .7, "cy": .5, "rx": .1, "ry": .1, "rot": 0, "feather": 20, "invert": False}]}, pic)
sub = L.mask_of({"comps": [{"kind": "radial", "op": "add", "cx": .5, "cy": .5, "rx": .3, "ry": .3, "rot": 0, "feather": 20, "invert": False},
                           {"kind": "radial", "op": "subtract", "cx": .5, "cy": .5, "rx": .1, "ry": .1, "rot": 0, "feather": 20, "invert": False}]}, pic)
inter = L.mask_of({"comps": [{"kind": "radial", "op": "add", "cx": .5, "cy": .5, "rx": .3, "ry": .3, "rot": 0, "feather": 20, "invert": False},
                             {"kind": "luminance", "op": "intersect", "lo": 0.55, "hi": 1.0, "smooth": 10, "invert": False}]}, pic)
check("mask components: add joins, subtract cuts a hole, intersect keeps the overlap", add[70, 60] > .9 and add[70, 140] > .9 and add[70, 100] < .1 and sub[70, 100] < .1 and sub[70, 70] > .9 and inter[70, 120] > .9 and inter[70, 80] < .1)
sky = np.zeros((120, 160, 3), dtype=np.float32)
sky[:60] = (0.45, 0.65, 0.95)
sky[60:] = (0.25, 0.3, 0.12)
sm = L.sky_mask(sky)
check("sky guess: the blue top is sky, the ground is not", sm[:35].mean() > 0.6 and sm[90:].mean() < 0.1, (round(float(sm[:35].mean()), 2), round(float(sm[90:].mean()), 2)))
subj = np.full((120, 160, 3), (0.5, 0.55, 0.5), dtype=np.float32)
subj[40:90, 60:100] = (0.9, 0.2, 0.2)
sj = L.subject_mask(subj)
check("subject guess: the odd one out in the middle; background is the opposite", sj[65, 80] > 0.6 and sj[5, 5] < 0.1 and L.component({"kind": "background", "invert": False}, subj)[65, 80] < 0.4)

# ---- the whole pipeline
img = pil(pic)
base = arr(images.apply_tone(img, {"exposure": 0.0, "sharpness": 1}))
local = arr(images.apply_tone(img, {"masks": [{"name": "left", "amount": 100, "adj": {"exposure": 1.5},
                                               "comps": [{"kind": "linear", "op": "add", "x1": 0.1, "y1": 0.5, "x2": 0.4, "y2": 0.5, "invert": False}]}]}))
orig = arr(img)
check("a local exposure change shows where the mask is and not elsewhere", local[:, 15].mean() > orig[:, 15].mean() + 0.03 and abs(local[:, 190].mean() - orig[:, 190].mean()) < 0.01)
half_amt = arr(images.apply_tone(img, {"masks": [{"amount": 50, "adj": {"exposure": 1.5}, "comps": [{"kind": "linear", "x1": 0.1, "y1": 0.5, "x2": 0.4, "y2": 0.5}]}]}))
check("the mask's Amount scales the effect", orig[:, 15].mean() < half_amt[:, 15].mean() < local[:, 15].mean())
sat = arr(images.apply_tone(pil(np.dstack([np.full((60, 60), 0.6), np.full((60, 60), 0.4), np.full((60, 60), 0.3)])), {"masks": [{"adj": {"saturation": -100}, "comps": [{"kind": "radial", "cx": .5, "cy": .5, "rx": 1, "ry": 1, "feather": 0}]}]}))
check("a local saturation change greys the masked colour", np.ptp(sat[30, 30]) < 0.03, np.ptp(sat[30, 30]))
tmp = Path(tempfile.mkdtemp(prefix="photag_devlocal_"))
bigp = tmp / "p.jpg"
Image.fromarray((np.clip(a, 0, 1) * 255).astype(np.uint8)).save(bigp, quality=95)
o = images.apply_edit(bigp, {"spots": [{"x": .5, "y": .5, "r": .04}], "lens_dist": 20, "persp_v": 15, "lens_vig": 30, "defringe": 40, "ca_auto": True,
                             "redeye": [{"x": .3, "y": .3, "r": .03}], "masks": [{"adj": {"clarity": 30, "noise": 40}, "comps": [{"kind": "subject"}]}]}, tmp / "o.jpg")
check("apply_edit with all the place-and-shape tools writes a picture of the same size", Image.open(o).size == (240, 200))
pv = images.preview_tone(bigp, {"masks": [{"adj": {"exposure": 1}, "comps": [{"kind": "sky"}]}], "persp_h": 20})
check("the live preview takes them too", pv[:2] == b"\xff\xd8")
n = res.count(False)
print(f"\n{len(res) - n}/{len(res)} passed")
sys.exit(1 if n else 0)
