"""Renk dönüşümleri: Kelvin, HSV ve ampul parametreleri arasında."""
import colorsys
import math

from .i18n import scene_name, t
from .scenes import SCENE_BY_ID, TEMP_PRESETS

OFF_COLOR = "#3a3a3a"


def to_hex(rgb):
    return "#%02x%02x%02x" % tuple(max(0, min(255, int(v))) for v in rgb)


def kelvin_to_rgb(k):
    t = k / 100
    if t <= 66:
        r, g = 255, 99.4708025861 * math.log(t) - 161.1195681661
    else:
        r, g = 329.698727446 * (t - 60) ** -0.1332047592, 288.1221695283 * (t - 60) ** -0.0755148492
    if t >= 66:
        b = 255
    elif t <= 19:
        b = 0
    else:
        b = 138.5177312231 * math.log(t - 10) - 305.0447927307
    return tuple(max(0, min(255, v)) for v in (r, g, b))


def hs_to_params(h, s):
    r, g, b = (round(v * 255) for v in colorsys.hsv_to_rgb(h, s, 1))
    w = min(r, g, b)
    p = {"r": r - w, "g": g - w, "b": b - w}
    if w:
        p["c"] = w  # soğuk beyaz kanalı pastel tonları daha temiz gösterir
    return p


def pilot_to_hs(p):
    c = p.get("c", 0) + p.get("w", 0)
    r, g, b = (min(255, p.get(k, 0) + c) / 255 for k in ("r", "g", "b"))
    h, s, _ = colorsys.rgb_to_hsv(r, g, b)
    return h, s


def pilot_color(p):
    if not p.get("state", True):
        return OFF_COLOR
    if p.get("sceneId"):
        return SCENE_BY_ID.get(p["sceneId"], ("", "#888888"))[1]
    if p.get("temp"):
        return to_hex(kelvin_to_rgb(p["temp"]))
    if any(k in p for k in ("r", "g", "b", "c", "w")):
        c = p.get("c", 0) + p.get("w", 0)
        return to_hex(tuple(min(255, p.get(k, 0) + c) for k in ("r", "g", "b")))
    return "#ffd38a"


def pilot_mode(p):
    if p.get("sceneId"):
        return "scene"
    if p.get("temp"):
        return "white"
    if any(k in p for k in ("r", "g", "b")):
        return "color"
    return "white"


def palette_rgb(entry):
    c = entry[0]
    if c.startswith("#"):
        return tuple(int(c[i:i + 2], 16) for i in (1, 3, 5))
    return kelvin_to_rgb(int(c))


HUE_NAMES = [(0.04, "c_red"), (0.11, "c_orange"), (0.19, "c_yellow"), (0.45, "c_green"), (0.54, "c_turquoise"),
             (0.70, "c_blue"), (0.82, "c_purple"), (0.96, "c_pink"), (1.01, "c_red")]


def describe_params(params):
    """Bir ışık ayarı için o anki dilde okunabilir ad üretir (ör. "Şömine", "Sıcak 2700 K", "Açık mavi")."""
    if "sceneId" in params:
        return scene_name(params["sceneId"])
    if "temp" in params:
        k = params["temp"]
        key = next((n for n, pk in reversed(TEMP_PRESETS) if k >= pk), "candle")
        return f"{t('preset_' + key)} {k} K"
    h, s = pilot_to_hs(params)
    if s < 0.12:
        return t("c_white")
    name = t(next(key for limit, key in HUE_NAMES if h < limit))
    return t("light_color", name=name, lname=name.lower()) if s < 0.55 else name
