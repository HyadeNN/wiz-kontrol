"""Uygulama simgesi, renk çarkı ve gradient sahne kutucuklarının çizimi."""
import colorsys
import math

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter

from .colors import palette_rgb


def make_bulb_icon(color, size=64):
    S = size * 4
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((S * 0.18, S * 0.04, S * 0.82, S * 0.68), fill=color)
    d.rounded_rectangle((S * 0.35, S * 0.66, S * 0.65, S * 0.84), radius=S * 0.03, fill="#c8c8c8")
    d.rounded_rectangle((S * 0.39, S * 0.84, S * 0.61, S * 0.95), radius=S * 0.05, fill="#9a9a9a")
    return img.resize((size, size), Image.LANCZOS)


def scene_tile(name, palette, w, h, state, font, radius):
    """Sahne kutucuğu: renkler, süre ağırlıklarıyla orantılı bantlar halinde yumuşakça geçer."""
    cols = [palette_rgb(p) for p in palette]
    total = sum(p[1] for p in palette)
    centers, acc = [], 0
    for _, wt in palette:
        centers.append((acc + wt / 2) / total)
        acc += wt
    row = []
    for x in range(w):
        t = x / (w - 1)
        if t <= centers[0]:
            c = cols[0]
        elif t >= centers[-1]:
            c = cols[-1]
        else:
            j = next(i for i in range(len(centers) - 1) if centers[i] <= t <= centers[i + 1])
            f = (t - centers[j]) / (centers[j + 1] - centers[j])
            f = f * f * (3 - 2 * f)
            c = tuple(a + (b - a) * f for a, b in zip(cols[j], cols[j + 1]))
        row.append(tuple(int(v) for v in c))
    img = Image.new("RGB", (w, 1))
    img.putdata(row)
    img = img.resize((w, h)).convert("RGBA")
    shade = Image.linear_gradient("L").resize((w, h)).point(lambda v: v * 0.35)
    img = Image.composite(Image.new("RGBA", (w, h), (0, 0, 0, 255)), img, shade)
    if state in ("hover", "active"):
        img = ImageEnhance.Brightness(img).enhance(1.12)
    d = ImageDraw.Draw(img)
    if state == "active":
        d.rounded_rectangle((2, 2, w - 3, h - 3), radius=radius - 2, outline="white", width=max(4, h // 16))
    while d.textlength(name, font=font) > w * 0.9 and font.size > h // 5:  # uzun adlar kutuya sığsın
        font = font.font_variant(size=font.size - 2)
    tw = d.textlength(name, font=font)
    tx, ty = (w - tw) / 2, h / 2
    glow = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(glow).text((tx, ty + 2), name, font=font, fill=(0, 0, 0, 200), anchor="lm")
    img = Image.alpha_composite(img, glow.filter(ImageFilter.GaussianBlur(3)))
    ImageDraw.Draw(img).text((tx, ty), name, font=font, fill="white", anchor="lm")
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, w - 1, h - 1), radius=radius, fill=255)
    img.putalpha(mask)
    return img


def make_wheel(size, bg):
    S = size * 2
    R, c = S / 2 - 2, S / 2
    img = Image.new("RGB", (S, S), bg)
    px = img.load()
    for y in range(S):
        dy = y - c
        for x in range(S):
            dx = x - c
            d = math.hypot(dx, dy)
            if d <= R:
                r, g, b = colorsys.hsv_to_rgb((math.atan2(-dy, dx) / (2 * math.pi)) % 1, d / R, 1)
                px[x, y] = (int(r * 255), int(g * 255), int(b * 255))
    return img.resize((size, size), Image.LANCZOS)
