import pytest

from wizkontrol.colors import (
    OFF_COLOR,
    describe_params,
    hs_to_params,
    kelvin_to_rgb,
    palette_rgb,
    pilot_color,
    pilot_mode,
    pilot_to_hs,
    to_hex,
)
from wizkontrol.i18n import set_language
from wizkontrol.scenes import SCENE_PALETTES, SCENES


def test_to_hex_clamps():
    assert to_hex((255, 0, 128)) == "#ff0080"
    assert to_hex((300, -5, 12.9)) == "#ff000c"


def test_kelvin_warm_is_redder_than_cool():
    warm, cool = kelvin_to_rgb(2200), kelvin_to_rgb(6500)
    assert warm[0] == 255 and warm[2] < cool[2]


@pytest.mark.parametrize("h,s", [(0.0, 1.0), (0.33, 1.0), (0.62, 0.5), (0.9, 0.2)])
def test_hs_roundtrip(h, s):
    rh, rs = pilot_to_hs(hs_to_params(h, s))
    assert rh == pytest.approx(h, abs=0.01)
    assert rs == pytest.approx(s, abs=0.01)


def test_saturated_color_uses_only_rgb_channels():
    assert hs_to_params(0.0, 1.0) == {"r": 255, "g": 0, "b": 0}


def test_white_point_uses_cold_white_channel():
    assert hs_to_params(0.5, 0.0) == {"r": 0, "g": 0, "b": 0, "c": 255}


def test_pilot_mode_detection():
    assert pilot_mode({"sceneId": 5, "dimming": 40}) == "scene"
    assert pilot_mode({"sceneId": 0, "temp": 2700}) == "white"
    assert pilot_mode({"sceneId": 0, "r": 255, "g": 0, "b": 0}) == "color"


def test_pilot_color_off():
    assert pilot_color({"state": False, "temp": 2700}) == OFF_COLOR


@pytest.mark.parametrize("lang,params,name", [
    ("tr", {"sceneId": 5}, "Şömine"),
    ("tr", {"temp": 2700}, "Sıcak 2700 K"),
    ("tr", {"temp": 4000}, "Doğal 4000 K"),
    ("tr", {"r": 255, "g": 0, "b": 0}, "Kırmızı"),
    ("tr", {"r": 0, "g": 0, "b": 0, "c": 255}, "Beyaz"),
    ("tr", hs_to_params(0.62, 0.4), "Açık mavi"),
    ("en", {"sceneId": 5}, "Fireplace"),
    ("en", hs_to_params(0.62, 0.4), "Light blue"),
    ("de", hs_to_params(0.62, 0.4), "Hellblau"),
    ("fr", hs_to_params(0.62, 0.4), "Bleu clair"),
])
def test_describe_params(lang, params, name):
    set_language(lang)
    try:
        assert describe_params(params) == name
    finally:
        set_language("tr")


def test_every_scene_has_a_valid_palette():
    for sid, _, _ in SCENES:
        palette = SCENE_PALETTES[sid]
        assert palette and all(w > 0 for _, w in palette)
        for entry in palette:
            assert all(0 <= v <= 255 for v in palette_rgb(entry))
