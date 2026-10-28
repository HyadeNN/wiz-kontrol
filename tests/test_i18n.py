import string

import pytest

from wizkontrol import i18n
from wizkontrol.app import migrate_action
from wizkontrol.scenes import SCENES


def fields(text):
    return {f for _, f, _, _ in string.Formatter().parse(text) if f}


@pytest.mark.parametrize("key", sorted(i18n.S))
def test_every_string_is_translated(key):
    values = i18n.S[key]
    assert len(values) == len(i18n.CODES), f"{key}: {len(values)} translations for {len(i18n.CODES)} languages"
    assert all(v.strip() for v in values)


@pytest.mark.parametrize("key", sorted(k for k in i18n.S if k != "light_color"))
def test_placeholders_match_across_languages(key):
    reference = fields(i18n.S[key][0])
    for code, text in zip(i18n.CODES, i18n.S[key]):
        assert fields(text) == reference, f"{key} [{code}]"


def test_light_color_uses_known_placeholders():
    for text in i18n.S["light_color"]:
        assert fields(text) and fields(text) <= {"name", "lname"}


def test_seven_day_names_per_language():
    for text in i18n.S["days"]:
        assert len(text.split()) == 7


def test_every_scene_has_a_name_in_every_language():
    for sid, _, _ in SCENES:
        assert len(i18n.SCENE_NAMES[sid]) == len(i18n.CODES)


def test_unknown_language_falls_back_to_english():
    i18n.set_language("xx")
    try:
        assert i18n.t("quit") == "Quit"
    finally:
        i18n.set_language("tr")


@pytest.mark.parametrize("old,new", [
    ("Aç", "on"), ("Kapat", "off"), ("Uyanma ışığı (30 dk)", "wake"), ("Yavaşça kapat (30 dk)", "fade_off"),
    ("★ Akşam", "fav:Akşam"), ("on", "on"), ("fav:Akşam", "fav:Akşam"),
])
def test_legacy_schedule_actions_are_migrated(old, new):
    assert migrate_action(old) == new
