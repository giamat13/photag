"""Test: the extra Develop tools (app/develop_ops.py): whites / blacks, texture, dehaze, grain, vignette, tone curve, colour mixer,
B&W mix, colour grading, calibration, white-balance helpers.

    py -3.12 tools/test_develop_ops.py
"""
import sys
import tempfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from PIL import Image  # noqa: E402
from app import develop_ops as D, images  # noqa: E402

res = []


def check(name, ok, extra=""):
    res.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{extra}]" if extra else ""))


rng = np.random.default_rng(1)
grad = np.linspace(0, 1, 256, dtype=np.float32)
ramp = np.repeat(np.repeat(grad[None, :, None], 64, axis=0), 3, axis=2)               # a grey ramp 64 x 256
tex = np.clip(0.5 + rng.standard_normal((128, 128, 1)).astype(np.float32) * 0.04, 0, 1).repeat(3, axis=2)
neutral = lambda ops: not images.has_tone(ops)
check("neutral settings do nothing (has_tone is false)", neutral({}) and neutral({"curve": {"rgb": [[0, 0], [1, 1]]}, "mixer": {"red": [0, 0, 0]}, "grading": {"shadows": [200, 0, 0]}}))
check("every new setting is noticed", all(images.has_tone(o) for o in ({"whites": 5}, {"blacks": -5}, {"texture": 5}, {"dehaze": 5}, {"grain": 5}, {"curve_p": [0, 5, 0, 0]},
      {"curve": {"rgb": [[0, 0], [0.5, 0.6], [1, 1]]}}, {"mixer": {"blue": [0, -40, 0]}}, {"grading": {"shadows": [210, 30, 0]}}, {"calib": {"red": [10, 0]}})))

w = D.whites_blacks(ramp, 60, 0)
check("whites + brightens the bright end much more than the dark end", (w - ramp)[:, 230:].mean() > 0.04 and abs((w - ramp)[:, :40].mean()) < 0.005)
b = D.whites_blacks(ramp, 0, 60)
check("blacks + lifts the dark end, not the bright end", (b - ramp)[:, :40].mean() > 0.04 and abs((b - ramp)[:, 230:].mean()) < 0.005)

hp = lambda x: float(np.abs(x[..., 0] - D._smooth(x[..., 0], 0, 1) * 0).std())
t_up, t_dn = D.texture(tex, 80, 128), D.texture(tex, -80, 128)
check("texture + increases fine detail, - smooths it", t_up[..., 0].std() > tex[..., 0].std() * 1.3 and t_dn[..., 0].std() < tex[..., 0].std() * 0.8, (round(float(tex.std()), 4), round(float(t_up.std()), 4), round(float(t_dn.std()), 4)))

hazy = (0.55 + 0.25 * ramp).astype(np.float32)                                      # a flat, washed-out picture
dh = np.clip(D.dehaze(hazy, 80, 256), 0, 1)
check("dehaze + stretches a washed-out picture (more contrast)", dh.std() > hazy.std() * 1.4, (round(float(hazy.std()), 3), round(float(dh.std()), 3)))
check("dehaze - adds atmosphere (less contrast)", D.dehaze(ramp, -80, 256).std() < ramp.std() * 0.85)

flat = np.full((200, 300, 3), 0.5, dtype=np.float32)
g1, g2 = D.grain(flat, 60, 25, 50), D.grain(flat, 60, 25, 50)
check("grain adds noise, always the same noise for the same picture", g1.std() > 0.02 and np.array_equal(g1, g2), round(float(g1.std()), 4))
check("grain does not shift the average", abs(float(g1.mean()) - 0.5) < 0.01)
check("a bigger grain is coarser (neighbouring pixels agree more)", np.corrcoef(D.grain(flat, 60, 90, 50)[:, :-1, 0].ravel(), D.grain(flat, 60, 90, 50)[:, 1:, 0].ravel())[0, 1] > np.corrcoef(D.grain(flat, 60, 0, 50)[:, :-1, 0].ravel(), D.grain(flat, 60, 0, 50)[:, 1:, 0].ravel())[0, 1])

def old_vignette(a, v):
    h, w = a.shape[:2]
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    r = np.sqrt(((xx - (w - 1) / 2) / (w / 2)) ** 2 + ((yy - (h - 1) / 2) / (h / 2)) ** 2) / np.sqrt(2.0)
    m = (np.clip((r - 0.35) / 0.65, 0.0, 1.0) ** 2)[..., None]
    v = v / 100.0
    return a * (1 - (-v) * 0.9 * m) if v < 0 else a + v * 0.9 * m * (1 - a)
img = np.full((120, 200, 3), 0.6, dtype=np.float32)
check("the vignette with its handles at the defaults is exactly the original one", np.allclose(D.vignette(img, -60, 50, 50, 0, 0), old_vignette(img, -60), atol=1e-6))
vm_a, vm_b = D.vignette(img, -80, 20, 50, 0, 0), D.vignette(img, -80, 80, 50, 0, 0)
check("midpoint: a higher midpoint keeps the dark corners smaller", vm_a[0, 0, 0] <= vm_b[0, 0, 0] and vm_a[40, 60, 0] < vm_b[40, 60, 0] + 1e-6)
bright = np.full((120, 200, 3), 0.95, dtype=np.float32)
check("highlight priority protects the bright areas", D.vignette(bright, -80, 50, 50, 0, 100)[0, 0, 0] > D.vignette(bright, -80, 50, 50, 0, 0)[0, 0, 0] + 0.05)

lut = D.curve_lut([[0, 0], [0.25, 0.15], [0.75, 0.85], [1, 1]])
check("a curve through its points: monotone, ends at 0 and 1", (np.diff(lut) >= -1e-6).all() and lut[0] == 0 and abs(lut[-1] - 1) < 1e-6 and abs(lut[256] - 0.15) < 0.01, (round(float(lut[256]), 3), round(float(lut[768]), 3)))
check("points that go backwards or repeat are cleaned, never inverting tones", (np.diff(D.curve_lut([[0.5, 0.9], [0.5, 0.1], [0.2, 0.8], [0.9, 0.2]])) >= -1e-6).all())
check("the straight line changes nothing", np.allclose(D.apply_curve(ramp, {"rgb": [[0, 0], [1, 1]]}, None), ramp, atol=2e-3))
s_curve = D.apply_curve(ramp, {"rgb": [[0, 0], [0.25, 0.15], [0.75, 0.85], [1, 1]]}, None)
check("an S curve adds contrast", s_curve.std() > ramp.std() and s_curve[0, 64, 0] < ramp[0, 64, 0] and s_curve[0, 192, 0] > ramp[0, 192, 0])
red_only = D.apply_curve(ramp, {"r": [[0, 0], [0.5, 0.75], [1, 1]]}, None)
check("a channel curve changes only that channel", red_only[0, 128, 0] > ramp[0, 128, 0] + 0.1 and np.allclose(red_only[..., 1:], ramp[..., 1:], atol=1e-6))
par = D.apply_curve(ramp, None, [0, 0, 0, 60])
check("the parametric shadows slider lifts the dark tones only", (par - ramp)[0, 20:50, 0].mean() > 0.03 and abs((par - ramp)[0, 230:, 0].mean()) < 0.01 and (np.diff(par[0, :, 0]) >= -1e-6).all())

def px(r, g, b):
    return np.array([[[r, g, b]]], dtype=np.float32)
blue, red, grey = px(0.2, 0.3, 0.9), px(0.9, 0.2, 0.2), px(0.5, 0.5, 0.5)
mb = D.mixer(blue, {"blue": [0, -100, 0]})
check("mixer: blue saturation -100 greys the blue, leaves the red", D.rgb_to_hsv(mb)[1][0, 0] < 0.1 and np.allclose(D.mixer(red, {"blue": [0, -100, 0]}), red, atol=1e-3))
hue_before = D.rgb_to_hsv(red)[0][0, 0]
hue_after = D.rgb_to_hsv(D.mixer(red, {"red": [60, 0, 0]}))[0][0, 0]
check("mixer: hue + on red moves the colour towards orange", 8 < ((hue_after - hue_before + 180) % 360 - 180) < 40, round(float(hue_after), 1))
check("mixer: grey stays grey", np.allclose(D.mixer(grey, {"red": [50, 100, 50], "blue": [-50, 100, -50]}), grey, atol=1e-3))
check("mixer: luminance of a band lightens that colour", D.mixer(blue, {"blue": [0, 0, 80]}).mean() > blue.mean() + 0.03)

bw_blue_dark = D.bw_mix(blue, {"blue": -100})[0, 0, 0]
bw_blue_plain = D.bw_mix(blue, {})[0, 0, 0]
check("B&W mix: darkening blue makes a blue sky darker, a red stays", bw_blue_dark < bw_blue_plain - 0.05 and abs(D.bw_mix(red, {"blue": -100})[0, 0, 0] - D.bw_mix(red, {})[0, 0, 0]) < 0.02)
check("B&W mix gives a grey picture (three equal channels)", np.allclose(D.bw_mix(blue, {"red": 40})[..., 0], D.bw_mix(blue, {"red": 40})[..., 2]))

dark, light = px(0.1, 0.1, 0.1), px(0.9, 0.9, 0.9)
gr = {"shadows": [220, 80, 0], "high": [40, 80, 0]}
gd, gl = D.grading(dark, gr)[0, 0], D.grading(light, gr)[0, 0]
check("grading: the shadows get the blue tint, the highlights the warm one", gd[2] > gd[0] + 0.03 and gl[0] > gl[2] + 0.03, (gd.round(2).tolist(), gl.round(2).tolist()))
check("grading: luminance of the highlights brightens only them", D.grading(light, {"high": [0, 0, 100]})[0, 0, 0] > 0.95 and abs(D.grading(dark, {"high": [0, 0, 100]})[0, 0, 0] - 0.1) < 0.01)
check("grading: balance moves the border between shadows and highlights", D.grading(px(0.5, 0.5, 0.5), {"shadows": [220, 80, 0], "balance": 100})[0, 0, 2] > D.grading(px(0.5, 0.5, 0.5), {"shadows": [220, 80, 0], "balance": -100})[0, 0, 2])

cal = D.calibration(red, {"red": [0, -100]})
check("calibration: red primary saturation -100 desaturates red", D.rgb_to_hsv(cal)[1][0, 0] < D.rgb_to_hsv(red)[1][0, 0] - 0.4)
check("calibration: shadow tint tints only the dark tones", abs(D.calibration(dark, {"shadow_tint": 100})[0, 0, 1] - 0.1) > 0.02 and np.allclose(D.calibration(light, {"shadow_tint": 100}), light, atol=1e-3))

warm = (0.62, 0.50, 0.38)
t, ti = D.wb_from_neutral(warm)
adj = np.array(warm) * np.array([1 + 0.3 * t / 100, 1 - 0.2 * ti / 100, 1 - 0.3 * t / 100])
check("the eyedropper: the picked warm colour comes out neutral", max(adj) - min(adj) < 0.04 and t < 0, (round(t, 1), round(ti, 1), adj.round(3).tolist()))
check("white-balance presets: tungsten cools, cloudy warms", D.WB_PRESETS["tungsten"][0] < 0 < D.WB_PRESETS["cloudy"][0])

# ---- sharpening detail and noise reduction (batch 2)
def pil(a):
    return Image.fromarray(np.clip(a * 255 + 0.5, 0, 255).astype(np.uint8))


edge = np.zeros((120, 160, 3), dtype=np.float32)
edge[:, 80:] = 0.7
edge = np.clip(edge + 0.15, 0, 1)
ei = pil(edge)
base_sharp = np.asarray(images.apply_tone(ei, {"sharpness": 40}), dtype=int)
check("sharpening with the resting radius / detail / masking is exactly the old sharpening",
      np.array_equal(base_sharp, np.asarray(images.apply_tone(ei, {"sharpness": 40, "sharp_radius": 50, "sharp_detail": 25, "sharp_mask": 0}), dtype=int)))
check("the sharpening styles alone change nothing", neutral({"sharp_radius": 80, "sharp_detail": 90, "sharp_mask": 70, "nr_detail": 10}))
wide = np.asarray(images.apply_tone(ei, {"sharpness": 60, "sharp_radius": 100}), dtype=int)
narrow = np.asarray(images.apply_tone(ei, {"sharpness": 60, "sharp_radius": 0}), dtype=int)
check("a different radius sharpens differently", np.abs(wide - narrow).max() > 0)
flat = pil(np.full((100, 100, 3), 0.5, dtype=np.float32) + (rng.standard_normal((100, 100, 1)).astype(np.float32) * 0.01))
sh_free = np.abs(np.asarray(images.apply_tone(flat, {"sharpness": 100}), dtype=float) - np.asarray(flat, dtype=float)).mean()
sh_mask = np.abs(np.asarray(images.apply_tone(flat, {"sharpness": 100, "sharp_mask": 100}), dtype=float) - np.asarray(flat, dtype=float)).mean()
check("masking keeps the sharpening off flat areas (only edges are sharpened)", sh_mask < sh_free, (round(sh_free, 3), round(sh_mask, 3)))
gb = np.tile(np.linspace(0.25, 0.8, 240, dtype=np.float32)[None, :, None], (160, 1, 3))
noise = rng.standard_normal((160, 240, 1)).astype(np.float32) * 0.05
gn = pil(np.clip(gb + noise, 0, 1))
err = lambda im: np.abs(np.asarray(im, dtype=float) / 255 - gb).mean()
check("luminance noise reduction cuts the noise strongly", err(images.apply_tone(gn, {"nr_lum": 100})) < err(gn) * 0.6, (round(err(gn), 4), round(err(images.apply_tone(gn, {'nr_lum': 100})), 4)))
check("...more of it with a bigger amount", err(images.apply_tone(gn, {"nr_lum": 30})) > err(images.apply_tone(gn, {"nr_lum": 80})))
cn = pil(np.clip(gb + rng.standard_normal((160, 240, 3)).astype(np.float32) * 0.05, 0, 1))
check("colour noise reduction smooths the colour blotches", err(images.apply_tone(cn, {"nr_color": 100})) < err(cn) * 0.85)
stp = np.zeros((120, 160, 3), dtype=np.float32)
stp[:, 80:] = 0.8
sn = pil(np.clip(stp + 0.1 + rng.standard_normal((120, 160, 1)).astype(np.float32) * 0.04, 0, 1))
r = np.asarray(images.apply_tone(sn, {"nr_lum": 100, "nr_detail": 80}), dtype=float) / 255
check("noise reduction keeps a strong edge sharp", r[:, 84:90].mean() - r[:, 70:76].mean() > 0.7 and abs(r[:, 79].mean() - r[:, 70:76].mean()) < 0.25)
check("the clean values are kept: noise-reduction amounts above 100 are clamped by the server", D.clean({"nr_lum": 500, "sharp_mask": -4})["nr_lum"] == 100 and D.clean({"sharp_mask": -4})["sharp_mask"] == 0)

tmp = Path(tempfile.mkdtemp(prefix="photag_devops_"))
Image.fromarray((np.clip(ramp * 255, 0, 255)).astype(np.uint8)).save(tmp / "a.png")
Image.fromarray(np.repeat(np.repeat(np.linspace(30, 220, 200)[None, :, None], 3, axis=2), 120, axis=0).astype(np.uint8)).save(tmp / "b.jpg", "JPEG")
ops = {"whites": 20, "texture": 30, "dehaze": 15, "grain": 20, "vignette": -30, "vignette_mid": 30, "curve": {"rgb": [[0, 0], [0.3, 0.2], [1, 1]]},
       "mixer": {"blue": [0, -20, 0]}, "grading": {"shadows": [210, 20, 0]}, "calib": {"red": [5, 5]}}
out = images.apply_edit(tmp / "b.jpg", {**ops, "grayscale": True, "bwmix": {"red": 30}}, tmp / "o.jpg")
im = Image.open(out)
check("apply_edit with all the new tools writes a picture of the same size", im.size == (200, 120) and im.mode == "RGB")
pv = images.preview_tone(tmp / "b.jpg", ops)
check("the live preview takes the same settings (JPEG bytes)", pv[:2] == b"\xff\xd8")
n = res.count(False)
print(f"\n{len(res) - n}/{len(res)} passed")
sys.exit(1 if n else 0)
