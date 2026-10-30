import json
import socket

import pytest

from wizkontrol import protocol, screensync
from wizkontrol.screensync import dominant_color, frame_params, params_changed


def pixels(*rgb, n=100):
    """BGRA byte buffer of n pixels per given color."""
    return b"".join(bytes((b, g, r, 255)) * n for r, g, b in rgb)


def test_solid_color_is_returned_as_is():
    rgb, level = dominant_color(pixels((200, 40, 40)))
    assert rgb == pytest.approx((200, 40, 40))
    assert level == pytest.approx(200 / 255)


def test_saturated_pixels_outweigh_gray_ui():
    rgb, _ = dominant_color(pixels((128, 128, 128), n=900) + pixels((0, 0, 255), n=100))
    assert rgb[2] > rgb[0] * 2


def test_black_bars_do_not_tint_the_color_but_lower_level():
    full, full_level = dominant_color(pixels((0, 200, 0)))
    boxed, boxed_level = dominant_color(pixels((0, 0, 0), n=500) + pixels((0, 200, 0), n=500))
    assert boxed == pytest.approx(full)
    assert boxed_level == pytest.approx(full_level / 2)


def test_black_screen_has_no_color_and_dims():
    rgb, level = dominant_color(pixels((3, 3, 3)))
    assert rgb is None and level < 0.05
    assert frame_params(None, level, 80, True) == {"dimming": 10}


def test_frame_params_always_resets_white_channels():
    params = frame_params((255, 0, 0), 1.0, 100, False)
    assert params == {"r": 255, "g": 0, "b": 0, "c": 0, "w": 0, "dimming": 100}


def test_vivid_increases_saturation():
    plain = frame_params((200, 120, 120), 0.8, 100, False)
    vivid = frame_params((200, 120, 120), 0.8, 100, True)
    assert vivid["c"] < plain["c"]


def test_brightness_stays_within_limits():
    assert frame_params((255, 0, 0), 1.0, 40, False)["dimming"] == 40
    assert frame_params((255, 0, 0), 0.0, 40, False)["dimming"] == 10


def test_small_changes_are_not_sent():
    a = {"r": 100, "g": 50, "b": 0, "c": 0, "w": 0, "dimming": 50}
    assert not params_changed(a, dict(a, r=102))
    assert params_changed(a, dict(a, r=120))
    assert params_changed(a, dict(a, dimming=55))
    assert params_changed(None, a)
    assert params_changed(a, {"dimming": 10})


def test_sync_loop_sends_captured_color(monkeypatch):
    class FakeCapture:
        def __init__(self, rect):
            pass

        def grab(self):
            return pixels((255, 0, 0))

        def close(self):
            pass

    receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    receiver.bind(("127.0.0.1", 0))
    receiver.settimeout(2)
    monkeypatch.setattr(protocol, "PORT", receiver.getsockname()[1])
    monkeypatch.setattr(screensync, "Capture", FakeCapture)
    sync = screensync.ScreenSync(["127.0.0.1"], (0, 0, 10, 10), vivid=False)
    sync.start()
    try:
        msg = json.loads(receiver.recv(4096))
    finally:
        sync.stop()
        sync.join(1)
        receiver.close()
    assert msg["method"] == "setPilot" and msg["params"]["r"] == 255 and msg["params"]["b"] == 0
    assert not sync.is_alive()
