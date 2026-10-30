"""Ekran senkronu: ekranın baskın rengini yakalar ve ampullere sürekli gönderir.

Ekran GDI ile küçültülerek okunur (ek bağımlılık gerekmez). Ampuller sabit bir kare hızında, yanıt
beklenmeden güncellenir; kaybolan bir paketin yerini zaten bir sonraki kare alır.
"""
import colorsys
import ctypes
import json
import logging
import socket
import threading
import time
from ctypes import wintypes

from . import protocol
from .colors import hs_to_params

log = logging.getLogger(__name__)

FPS = 12
SAMPLE_W, SAMPLE_H = 32, 18
KEEPALIVE = 2.0  # renk değişmese de bu sürede bir gönder
SMOOTHING = {"fast": 0.75, "balanced": 0.4, "soft": 0.15}  # her karede yeni rengin ağırlığı

_SRCCOPY = 0x00CC0020
_HALFTONE = 4


def dominant_color(data):
    """BGRA piksellerinden (r, g, b) ve genel parlaklığı (0-1) döndürür.

    Doygun pikseller daha ağır sayılır, böylece gri arayüzler ve siyah bantlar rengi soldurmaz.
    Ekran neredeyse tamamen siyahsa renk None olur.
    """
    tr = tg = tb = tw = peak = 0.0
    count = len(data) // 4
    for i in range(0, count * 4, 4):
        b, g, r = data[i], data[i + 1], data[i + 2]
        mx = max(r, g, b)
        peak += mx
        if mx < 18:
            continue
        sat = mx - min(r, g, b)
        w = 1 + sat * sat / 64
        tr += r * w
        tg += g * w
        tb += b * w
        tw += w
    level = peak / (count * 255) if count else 0.0
    return ((tr / tw, tg / tw, tb / tw) if tw else None), level


def frame_params(rgb, level, max_dim, vivid):
    """Bir karenin rengini ampul parametrelerine çevirir."""
    dimming = round(10 + (max_dim - 10) * min(1.0, level ** 0.6 * 1.3))
    if rgb is None:
        return {"dimming": 10}
    h, s, _ = colorsys.rgb_to_hsv(*(v / 255 for v in rgb))
    if vivid:
        s = min(1.0, s * 1.6)
    if s < 0.08:
        s = 0.0
    return dict({"c": 0, "w": 0}, **hs_to_params(h, s), dimming=dimming)


def params_changed(a, b):
    """İki kare arasındaki fark gözle görülür mü?"""
    if a is None or a.keys() != b.keys():
        return True
    color = sum(abs(a[k] - b[k]) for k in a if k != "dimming")
    return color > 6 or abs(a["dimming"] - b["dimming"]) >= 3


def monitors():
    """Bağlı ekranların (sol, üst, genişlik, yükseklik) listesi."""
    found = []
    proc = ctypes.WINFUNCTYPE(ctypes.c_int, wintypes.HMONITOR, wintypes.HDC, ctypes.POINTER(wintypes.RECT),
                              wintypes.LPARAM)

    def collect(_mon, _dc, rect, _data):
        r = rect.contents
        found.append((r.left, r.top, r.right - r.left, r.bottom - r.top))
        return 1

    ctypes.windll.user32.EnumDisplayMonitors(None, None, proc(collect), 0)
    return found


class _BitmapInfo(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]


class Capture:
    """Bir ekran bölgesini GDI ile SAMPLE_W × SAMPLE_H boyutuna küçülterek okur."""

    def __init__(self, rect):
        self.rect = rect
        self.user32 = ctypes.WinDLL("user32")
        gdi = self.gdi32 = ctypes.WinDLL("gdi32")
        self.user32.GetDC.restype = wintypes.HDC
        self.user32.GetDC.argtypes = [wintypes.HWND]
        self.user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
        gdi.CreateCompatibleDC.restype = wintypes.HDC
        gdi.CreateCompatibleDC.argtypes = [wintypes.HDC]
        gdi.CreateCompatibleBitmap.restype = wintypes.HBITMAP
        gdi.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
        gdi.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
        gdi.SetStretchBltMode.argtypes = [wintypes.HDC, ctypes.c_int]
        gdi.StretchBlt.argtypes = [wintypes.HDC] + [ctypes.c_int] * 4 + [wintypes.HDC] + [ctypes.c_int] * 4 + [
            wintypes.DWORD]
        gdi.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT, ctypes.c_void_p,
                                  ctypes.POINTER(_BitmapInfo), wintypes.UINT]
        gdi.DeleteObject.argtypes = [wintypes.HGDIOBJ]
        gdi.DeleteDC.argtypes = [wintypes.HDC]
        self.screen = self.user32.GetDC(None)
        self.mem = gdi.CreateCompatibleDC(self.screen)
        self.bitmap = gdi.CreateCompatibleBitmap(self.screen, SAMPLE_W, SAMPLE_H)
        gdi.SelectObject(self.mem, self.bitmap)
        gdi.SetStretchBltMode(self.mem, _HALFTONE)
        self.info = _BitmapInfo(ctypes.sizeof(_BitmapInfo), SAMPLE_W, -SAMPLE_H, 1, 32)
        self.buffer = ctypes.create_string_buffer(SAMPLE_W * SAMPLE_H * 4)

    def grab(self):
        """Pikselleri döndürür; ekran okunamıyorsa (kilit ekranı, UAC) None."""
        x, y, w, h = self.rect
        if not self.gdi32.StretchBlt(self.mem, 0, 0, SAMPLE_W, SAMPLE_H, self.screen, x, y, w, h, _SRCCOPY):
            return None
        if not self.gdi32.GetDIBits(self.mem, self.bitmap, 0, SAMPLE_H, self.buffer, ctypes.byref(self.info), 0):
            return None
        return self.buffer.raw

    def close(self):
        self.gdi32.DeleteObject(self.bitmap)
        self.gdi32.DeleteDC(self.mem)
        self.user32.ReleaseDC(None, self.screen)


class ScreenSync(threading.Thread):
    """Ekranı okuyup ampullere gönderen arka plan döngüsü. Ayar alanları çalışırken değiştirilebilir."""

    def __init__(self, ips, rect, smoothing="balanced", vivid=True, max_dim=100, on_color=None):
        super().__init__(daemon=True)
        self.ips, self.rect = ips, rect
        self.alpha = SMOOTHING[smoothing]
        self.vivid, self.max_dim = vivid, max_dim
        self.on_color = on_color
        self._stop_event = threading.Event()

    def stop(self):
        self._stop_event.set()

    def run(self):
        capture = Capture(self.rect)
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        smooth, level, last, last_sent, last_ui = None, 0.0, None, 0.0, 0.0
        log.info("Ekran senkronu başladı: %s, %s", self.rect, self.ips)
        try:
            while not self._stop_event.is_set():
                started = time.perf_counter()
                raw = capture.grab()
                if raw is not None:
                    rgb, lvl = dominant_color(raw)
                    level += (lvl - level) * self.alpha
                    if rgb is not None:
                        smooth = rgb if smooth is None else tuple(
                            s + (v - s) * self.alpha for s, v in zip(smooth, rgb))
                    params = frame_params(smooth if rgb is not None else None, level, self.max_dim, self.vivid)
                    if params_changed(last, params) or started - last_sent > KEEPALIVE:
                        msg = json.dumps({"method": "setPilot", "params": params}).encode()
                        for ip in self.ips:
                            try:
                                sock.sendto(msg, (ip, protocol.PORT))
                            except OSError as e:
                                log.warning("Senkron paketi gönderilemedi (%s): %s", ip, e)
                        last, last_sent = params, started
                    if self.on_color and smooth is not None and started - last_ui > 0.25:
                        last_ui = started
                        self.on_color(smooth)
                self._stop_event.wait(max(0.0, 1 / FPS - (time.perf_counter() - started)))
        finally:
            capture.close()
            sock.close()
            log.info("Ekran senkronu durdu")
