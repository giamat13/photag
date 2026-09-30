"""Generate app/ui/icon.png — the photag app icon (photo card + tag on charcoal).

Run:  python tools/make_icon.py
"""
from pathlib import Path
from PIL import Image, ImageChops, ImageDraw, ImageFilter

S = 4                 # supersampling factor
N = 512 * S           # canvas size
OUT = Path(__file__).resolve().parent.parent / "app" / "ui" / "icon.png"


def px(v):
    return int(round(v * N / 512))


def vgrad(size, top, bottom):
    w, h = size
    g = Image.new("RGBA", size)
    d = ImageDraw.Draw(g)
    for y in range(h):
        t = y / max(h - 1, 1)
        d.line([(0, y), (w, y)], fill=tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3)) + (255,))
    return g


def rounded_mask(size, box, r):
    m = Image.new("L", size, 0)
    ImageDraw.Draw(m).rounded_rectangle(box, r, fill=255)
    return m


def paste_masked(base, layer, mask):
    base.paste(layer, (0, 0), mask)


canvas = Image.new("RGBA", (N, N), (0, 0, 0, 0))

# --- background tile -------------------------------------------------------
tile_box = (px(16), px(16), px(496), px(496))
paste_masked(canvas, vgrad((N, N), (58, 58, 58), (27, 27, 27)), rounded_mask((N, N), tile_box, px(104)))

# --- photo card (tilted) ---------------------------------------------------
card_w, card_h = px(300), px(250)
card = Image.new("RGBA", (card_w, card_h), (0, 0, 0, 0))
cd = ImageDraw.Draw(card)
cd.rounded_rectangle((0, 0, card_w - 1, card_h - 1), px(22), fill=(236, 236, 236, 255))   # white border
inner = (px(14), px(14), card_w - px(14), card_h - px(14))
iw, ih = inner[2] - inner[0], inner[3] - inner[1]

pic = vgrad((iw, ih), (92, 158, 240), (24, 52, 104))                    # dusk sky
pd = ImageDraw.Draw(pic)
pd.ellipse((iw * 0.60, ih * 0.14, iw * 0.60 + px(46), ih * 0.14 + px(46)), fill=(255, 226, 150, 255))  # sun
pd.polygon([(0, ih), (0, ih * 0.66), (iw * 0.24, ih * 0.38), (iw * 0.46, ih * 0.70),
            (iw * 0.62, ih * 0.52), (iw * 0.86, ih * 0.74), (iw, ih * 0.60), (iw, ih)], fill=(22, 38, 70, 255))
pd.polygon([(0, ih), (0, ih * 0.82), (iw * 0.30, ih * 0.62), (iw * 0.55, ih * 0.86),
            (iw * 0.78, ih * 0.72), (iw, ih * 0.88), (iw, ih)], fill=(13, 22, 42, 255))
card_pic = Image.new("RGBA", (card_w, card_h), (0, 0, 0, 0))
card_pic.paste(pic, inner[:2], rounded_mask((iw, ih), (0, 0, iw - 1, ih - 1), px(12)))
card.alpha_composite(card_pic)
card = card.rotate(-8, resample=Image.BICUBIC, expand=True)

# drop shadow under the card
cx, cy = px(232), px(236)
pos = (cx - card.width // 2, cy - card.height // 2)
shadow = Image.new("RGBA", (N, N), (0, 0, 0, 0))
shadow.paste((0, 0, 0, 150), (pos[0], pos[1] + px(10)), card.split()[3])
shadow = shadow.filter(ImageFilter.GaussianBlur(px(14)))
canvas.alpha_composite(shadow)
canvas.alpha_composite(card, pos)

# --- tag (accent blue, with punched hole) ----------------------------------
tag = Image.new("RGBA", (N, N), (0, 0, 0, 0))
td = ImageDraw.Draw(tag)
pts = [(px(300), px(330)), (px(440), px(330)), (px(478), px(376)), (px(440), px(422)), (px(300), px(422))]
# rounded left corners via polygon + circles
td.polygon(pts, fill=(75, 152, 240, 255))
r = px(16)
td.pieslice((px(300) - r, px(330), px(300) + r, px(330) + 2 * r), 180, 270, fill=(75, 152, 240, 255))
td.pieslice((px(300) - r, px(422) - 2 * r, px(300) + r, px(422)), 90, 180, fill=(75, 152, 240, 255))
td.rectangle((px(300) - r, px(330) + r, px(300), px(422) - r), fill=(75, 152, 240, 255))
# punch the string hole
hole = Image.new("L", (N, N), 0)
ImageDraw.Draw(hole).ellipse((px(444) - px(11), px(376) - px(11), px(444) + px(11), px(376) + px(11)), fill=255)
tag.putalpha(ImageChops.subtract(tag.split()[3], hole))
# "#" glyph-ish lines on tag -> two short text bars
bd = ImageDraw.Draw(tag)
bd.rounded_rectangle((px(322), px(360), px(410), px(370)), px(5), fill=(255, 255, 255, 235))
bd.rounded_rectangle((px(322), px(382), px(384), px(392)), px(5), fill=(255, 255, 255, 150))

tag_shadow = Image.new("RGBA", (N, N), (0, 0, 0, 0))
tag_shadow.paste((0, 0, 0, 130), (0, px(8)), tag.split()[3])
tag_shadow = tag_shadow.filter(ImageFilter.GaussianBlur(px(9)))
canvas.alpha_composite(tag_shadow)
canvas.alpha_composite(tag)

# --- subtle top highlight on the tile --------------------------------------
hl = Image.new("RGBA", (N, N), (0, 0, 0, 0))
ImageDraw.Draw(hl).rounded_rectangle(tile_box, px(104), outline=(255, 255, 255, 38), width=px(3))
canvas.alpha_composite(hl)

out = canvas.resize((512, 512), Image.LANCZOS)
out.save(OUT, optimize=True)
print("wrote", OUT)
