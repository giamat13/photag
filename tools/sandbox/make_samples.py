"""Create the small sample files the Windows Sandbox test imports (tools/sandbox/photos).

Synthetic images (no personal photos): gradients with some detail, EXIF dates, GPS for four of them, and a
3-second test video (needs imageio-ffmpeg, which is a photag requirement anyway).
    py -3.12 tools/sandbox/make_samples.py
"""
import random
import subprocess
from pathlib import Path

import piexif
from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent / "photos"
OUT.mkdir(exist_ok=True)


def deg(v):
    v = abs(v); d = int(v); m = int((v - d) * 60); s = round((v - d - m / 60) * 3600 * 100)
    return ((d, 1), (m, 1), (s, 100))


def make(name, seed, size, lat=None, lng=None, quality=92):
    rnd = random.Random(seed)
    im = Image.new("RGB", size)
    px = im.load()
    c1, c2 = [tuple(rnd.randrange(30, 230) for _ in range(3)) for _ in range(2)]
    for y in range(size[1]):
        t = y / size[1]
        for x in range(size[0]):
            u = x / size[0]
            px[x, y] = tuple(int(a + (b - a) * (t * .6 + u * .4)) for a, b in zip(c1, c2))
    d = ImageDraw.Draw(im)
    for _ in range(40):
        x, y, r = rnd.randrange(size[0]), rnd.randrange(size[1]), rnd.randrange(10, 80)
        d.ellipse([x - r, y - r, x + r, y + r], outline=tuple(rnd.randrange(256) for _ in range(3)), width=3)
    ex = {"0th": {}, "Exif": {piexif.ExifIFD.DateTimeOriginal: f"2024:0{1 + seed % 9}:15 10:00:00".encode()}, "GPS": {}}
    if lat is not None:
        ex["GPS"] = {piexif.GPSIFD.GPSLatitudeRef: b"N" if lat >= 0 else b"S", piexif.GPSIFD.GPSLatitude: deg(lat),
                     piexif.GPSIFD.GPSLongitudeRef: b"E" if lng >= 0 else b"W", piexif.GPSIFD.GPSLongitude: deg(lng)}
    im.save(OUT / name, quality=quality, exif=piexif.dump(ex))


make("telaviv_1.jpg", 1, (900, 600), 32.0853, 34.7818)
make("telaviv_2.jpg", 2, (900, 600), 32.0853, 34.7818)      # same place: one pin with a count
make("jerusalem.jpg", 3, (900, 600), 31.7683, 35.2137)
make("paris.jpg", 4, (900, 600), 48.8566, 2.3522)
make("nogps.jpg", 5, (900, 600))
make("big_q97_exif.jpg", 6, (3000, 2000), quality=97)       # big and high quality: compresses well
try:
    import imageio_ffmpeg
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=duration=3:size=320x240:rate=24",
                    "-pix_fmt", "yuv420p", "-c:v", "libx264", str(OUT / "test_video.mp4")], check=True)
except Exception as e:
    print("no test video:", e)
print(sorted(p.name for p in OUT.iterdir()))
