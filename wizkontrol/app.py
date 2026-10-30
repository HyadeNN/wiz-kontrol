"""Ana pencere, sistem tepsisi simgesi ve zamanlayıcılar."""
import colorsys
import ctypes
import gc
import ipaddress
import json
import logging
import math
import os
import queue
import re
import socket
import subprocess
import sys
import threading
import time
import tkinter as tk
import webbrowser
from datetime import datetime
from logging.handlers import RotatingFileHandler

import customtkinter as ctk
import pystray
from PIL import Image, ImageFont, ImageTk

from . import APP_NAME, __version__, updates
from .colors import (
    OFF_COLOR,
    describe_params,
    hs_to_params,
    kelvin_to_rgb,
    pilot_color,
    pilot_mode,
    pilot_to_hs,
    to_hex,
)
from .config import CONFIG_DIR, CONFIG_PATH, ICON_PATH, LOG_PATH, autostart_enabled, load_config, set_autostart
from .graphics import make_bulb_icon, make_wheel, scene_tile
from .i18n import LANGUAGES, detect_language, get_language, scene_name, set_language, t
from .protocol import PORT, Sender, discover, wiz_request
from .scenes import QUICK_COLORS, SCENE_PALETTES, SCENES, STATIC_SCENES, TEMP_PRESETS
from .screensync import SMOOTHING, ScreenSync, monitors

log = logging.getLogger(__name__)

IPC_PORT = 38998
PANEL = "#2b2b2b"
TILE_SIZE = (128, 48)
TILE_SUPERSAMPLE = 2  # keskin yazı için büyük çizilip küçültülür
SLEEP_MINUTES = (15, 30, 45, 60, 90, 120)
ACTIONS = ("on", "off", "wake", "fade_off")  # favoriler "fav:<ad>" olarak saklanır
# Eski sürümlerin (yalnızca Türkçe) kaydettiği program eylemleri
_LEGACY_ACTIONS = {"Aç": "on", "Kapat": "off", "Uyanma ışığı (30 dk)": "wake",
                   "Yavaşça kapat (30 dk)": "fade_off"}


def migrate_action(a):
    if a in _LEGACY_ACTIONS:
        return _LEGACY_ACTIONS[a]
    return "fav:" + a[2:] if a.startswith("★ ") else a


def trim_memory():
    """Çöp toplar ve kullanılmayan sayfaları işletim sistemine geri verir."""
    gc.collect()
    kernel32 = ctypes.windll.kernel32
    kernel32.GetCurrentProcess.restype = ctypes.c_void_p
    kernel32.SetProcessWorkingSetSize.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_size_t]
    kernel32.SetProcessWorkingSetSize(kernel32.GetCurrentProcess(), ctypes.c_size_t(-1).value,
                                      ctypes.c_size_t(-1).value)


def scene_font(size):
    """Sahne kutucukları için o anki dilin karakterlerini içeren kalın bir yazı tipi."""
    names = {"zh": ["msyhbd.ttc", "msyh.ttc"], "ja": ["YuGothB.ttc", "meiryob.ttc", "msgothic.ttc"]}
    for name in names.get(get_language(), []) + ["segoeuib.ttf"]:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size)


class App:
    def __init__(self, ipc_sock, start_hidden):
        os.makedirs(CONFIG_DIR, exist_ok=True)
        self.cfg = load_config()
        set_language(self.cfg.get("language") or detect_language())
        for s in self.cfg["schedules"]:
            s["action"] = migrate_action(s["action"])
        self.ipc_sock = ipc_sock
        self.sync = None
        self.sync_restore = None
        self.reset_armed = False
        self.ui_q = queue.Queue()
        self.pilot = {}
        self.online = set()
        self.last_user_action = 0
        self.hue, self.sat = 0.6, 1.0
        self.fade = None
        self.sleep_deadline = None
        self.fired = set()
        self.last_minute = None
        self.first_sync = True
        self.poll_now = threading.Event()
        self.sender = Sender(self._on_send_result)
        self.sender.start()

        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")
        self.root = ctk.CTk()
        self.root.title(APP_NAME)
        self.root.geometry("520x800")
        self.root.minsize(480, 680)
        self.scale = ctk.ScalingTracker.get_window_scaling(self.root)
        self.font = ctk.CTkFont(family="Segoe UI", size=13)
        self.font_b = ctk.CTkFont(family="Segoe UI", size=13, weight="bold")
        self.font_h = ctk.CTkFont(family="Segoe UI", size=16, weight="bold")
        self.font_s = ctk.CTkFont(family="Segoe UI", size=11)

        if not os.path.exists(ICON_PATH):
            make_bulb_icon("#ffc94d", 256).save(ICON_PATH, sizes=[(16, 16), (32, 32), (48, 48), (256, 256)])
        self.root.after(250, lambda: self.root.iconbitmap(ICON_PATH))

        self.build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.build_tray()

        threading.Thread(target=self._discover_worker, daemon=True).start()
        threading.Thread(target=self._poll_worker, daemon=True).start()
        threading.Thread(target=self._ipc_worker, args=(ipc_sock,), daemon=True).start()
        self.root.after(50, self._drain)
        self.root.after(1000, self._tick)
        if self.cfg.get("check_updates", True):
            threading.Thread(target=self._update_worker, daemon=True).start()
        if start_hidden:
            self.root.withdraw()

    # ---------- yardımcılar
    def ui(self, fn):
        """Başka bir iş parçacığından arayüz işlemi sıraya koyar."""
        self.ui_q.put(fn)

    def _drain(self):
        try:
            while True:
                self.ui_q.get_nowait()()
        except queue.Empty:
            pass
        self.root.after(50, self._drain)

    def save_config(self):
        tmp = CONFIG_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.cfg, f, ensure_ascii=False, indent=2)
        os.replace(tmp, CONFIG_PATH)

    def set_status(self, text, error=False):
        self.status.configure(text=text, text_color="#f87171" if error else "gray60")

    def bulb_ip(self, mac):
        return self.cfg["bulbs"].get(mac, {}).get("ip")

    def target_macs(self, sel=None):
        sel = self.cfg.get("selected") if sel is None else sel
        if sel == "all":
            return list(self.cfg["bulbs"])
        return [sel] if sel in self.cfg["bulbs"] else []

    def target_ips(self, sel=None):
        return [ip for ip in (self.bulb_ip(m) for m in self.target_macs(sel)) if ip]

    # ---------- arayüz
    def build_ui(self):
        root = self.root
        root.grid_columnconfigure(0, weight=1)
        root.grid_rowconfigure(2, weight=1)

        head = ctk.CTkFrame(root, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 6))
        head.grid_columnconfigure(1, weight=1)
        self.swatch = ctk.CTkFrame(head, width=34, height=34, corner_radius=17, fg_color=OFF_COLOR)
        self.swatch.grid(row=0, column=0, padx=(0, 10))
        self.bulb_menu = ctk.CTkOptionMenu(head, values=[t("searching")], command=self.on_bulb_select,
                                           dynamic_resizing=False, font=self.font_b)
        self.bulb_menu.grid(row=0, column=1, sticky="ew")
        self.power = ctk.CTkSwitch(head, text="", command=self.on_power, width=50,
                                   switch_width=52, switch_height=26, progress_color="#f5b43c")
        self.power.grid(row=0, column=2, padx=(12, 0))

        dimf = ctk.CTkFrame(root, fg_color="transparent")
        dimf.grid(row=1, column=0, sticky="ew", padx=16, pady=(4, 8))
        dimf.grid_columnconfigure(0, weight=1)
        self.dim_label = ctk.CTkLabel(dimf, text=t("brightness"), font=self.font, anchor="w")
        self.dim_label.grid(row=0, column=0, sticky="w")
        self.dim = ctk.CTkSlider(dimf, from_=10, to=100, number_of_steps=90, command=self.on_dim,
                                 height=20, button_color="#f5b43c", button_hover_color="#ffd27a",
                                 progress_color="#f5b43c")
        self.dim.grid(row=1, column=0, sticky="ew", pady=(4, 0))
        self.dim.set(50)

        self.tabs = ctk.CTkTabview(root, fg_color=PANEL, segmented_button_selected_color="#3a6ea5")
        self.tabs.grid(row=2, column=0, sticky="nsew", padx=12, pady=(0, 6))
        self.tab_names = {k: t("tab_" + k) for k in ("white", "color", "scenes", "sync", "favs", "time", "settings")}
        for name in self.tab_names.values():
            self.tabs.add(name)
        tab = lambda k: self.tabs.tab(self.tab_names[k])
        self.build_white_tab(tab("white"))
        self.build_color_tab(tab("color"))
        self.build_scene_tab(tab("scenes"))
        self.build_sync_tab(tab("sync"))
        self.build_fav_tab(tab("favs"))
        self.build_time_tab(tab("time"))
        self.build_settings_tab(tab("settings"))

        self.status = ctk.CTkLabel(root, text=t("status_searching"), font=self.font_s, text_color="gray60", anchor="w")
        self.status.grid(row=3, column=0, sticky="ew", padx=18, pady=(0, 8))
        self.update_btn = ctk.CTkButton(root, text="", font=self.font_b, height=30, fg_color="#2f7d32",
                                        hover_color="#3c9a5f")  # yeni sürüm çıkınca görünür

    def build_white_tab(self, tab):
        tab.grid_columnconfigure(0, weight=1)
        self.temp_label = ctk.CTkLabel(tab, text=t("color_temp", k=2700), font=self.font_b, anchor="w")
        self.temp_label.grid(row=0, column=0, sticky="w", padx=8, pady=(12, 6))
        w, h = int(390 * self.scale), int(16 * self.scale)
        self.grad = tk.Canvas(tab, width=w, height=h, bg=PANEL, highlightthickness=0)
        self.grad.grid(row=1, column=0, padx=8, sticky="ew")
        self.grad.bind("<Configure>", lambda e: self._draw_gradient())
        self.temp = ctk.CTkSlider(tab, from_=2200, to=6500, number_of_steps=43, command=self.on_temp, height=20)
        self.temp.grid(row=2, column=0, sticky="ew", padx=2, pady=(6, 14))
        self.temp.set(2700)
        pf = ctk.CTkFrame(tab, fg_color="transparent")
        pf.grid(row=3, column=0, sticky="ew", padx=4)
        for i, (key, k) in enumerate(TEMP_PRESETS):
            pf.grid_columnconfigure(i, weight=1)
            col = kelvin_to_rgb(k)
            ctk.CTkButton(pf, text=f"{t('preset_' + key)}\n{k} K", font=self.font_s, height=52, corner_radius=10,
                          fg_color=to_hex(col), hover_color=to_hex(tuple(v * 0.85 for v in col)),
                          text_color="#222222", command=lambda k=k: self.set_temp(k)
                          ).grid(row=0, column=i, padx=3, sticky="ew")

    def _draw_gradient(self):
        c = self.grad
        c.delete("all")
        w, h = c.winfo_width(), c.winfo_height()
        for x in range(w):
            k = 2200 + (6500 - 2200) * x / max(1, w - 1)
            c.create_line(x, 0, x, h, fill=to_hex(kelvin_to_rgb(k)))

    def build_color_tab(self, tab):
        tab.grid_columnconfigure(0, weight=1)
        self.wheel_size = int(270 * self.scale)
        self.wheel_img = ImageTk.PhotoImage(make_wheel(self.wheel_size, PANEL))
        self.wheel = tk.Canvas(tab, width=self.wheel_size, height=self.wheel_size, bg=PANEL,
                               highlightthickness=0, cursor="hand2")
        self.wheel.grid(row=0, column=0, pady=(12, 10))
        self.wheel.create_image(0, 0, image=self.wheel_img, anchor="nw")
        r = int(9 * self.scale)
        self.marker = self.wheel.create_oval(0, 0, 2 * r, 2 * r, outline="white", width=max(2, int(2.5 * self.scale)))
        self.marker_r = r
        self.wheel.bind("<Button-1>", self.on_wheel)
        self.wheel.bind("<B1-Motion>", self.on_wheel)
        self._place_marker()
        qf = ctk.CTkFrame(tab, fg_color="transparent")
        qf.grid(row=1, column=0)
        for i, (h, s) in enumerate(QUICK_COLORS):
            col = to_hex(tuple(v * 255 for v in colorsys.hsv_to_rgb(h, s, 1)))
            ctk.CTkButton(qf, text="", width=36, height=36, corner_radius=18, fg_color=col, hover_color=col,
                          border_width=2, border_color="#555555", command=lambda h=h, s=s: self.set_color(h, s)
                          ).grid(row=0, column=i, padx=4)
        hf = ctk.CTkFrame(tab, fg_color="transparent")
        hf.grid(row=2, column=0, pady=(12, 0))
        self.hex_entry = ctk.CTkEntry(hf, width=110, placeholder_text="#FF8800", font=self.font)
        self.hex_entry.grid(row=0, column=0, padx=(0, 6))
        self.hex_entry.bind("<Return>", lambda e: self.apply_hex())
        ctk.CTkButton(hf, text=t("apply"), width=80, font=self.font, command=self.apply_hex).grid(row=0, column=1)
        ctk.CTkLabel(tab, text=t("wheel_hint"), font=self.font_s,
                     text_color="gray60").grid(row=3, column=0, pady=(10, 0))

    def build_scene_tab(self, tab):
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(2, weight=1)
        sf = ctk.CTkFrame(tab, fg_color="transparent")
        sf.grid(row=0, column=0, sticky="ew", padx=6, pady=(8, 4))
        sf.grid_columnconfigure(1, weight=1)
        self.speed_label = ctk.CTkLabel(sf, text=t("speed", v=100), font=self.font, anchor="w")
        self.speed_label.grid(row=0, column=0)
        self.speed = ctk.CTkSlider(sf, from_=10, to=200, number_of_steps=19, command=self.on_speed, height=18)
        self.speed.grid(row=0, column=1, sticky="ew", padx=(6, 0))
        self.speed.set(100)
        ff = ctk.CTkFrame(tab, fg_color="transparent")
        ff.grid(row=1, column=0, sticky="ew", padx=6, pady=(0, 4))
        ff.grid_columnconfigure(0, weight=1)
        self.scene_search = ctk.CTkEntry(ff, placeholder_text=t("scene_search"), font=self.font)
        self.scene_search.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        self.scene_search.bind("<KeyRelease>", lambda e: self.apply_scene_filter())
        self.scene_filter = ctk.CTkSegmentedButton(ff, font=self.font_s, command=self.apply_scene_filter, values=[
            t("filter_all"), t("filter_static"), t("filter_dynamic")])
        self.scene_filter.set(t("filter_all"))
        self.scene_filter.grid(row=0, column=1)
        grid = ctk.CTkScrollableFrame(tab, fg_color="transparent")
        grid.grid(row=2, column=0, sticky="nsew")
        self.scene_tiles = {}
        self.hover_scene = None
        self.tile_font = scene_font(round(12 * self.scale * TILE_SUPERSAMPLE))
        for i, (sid, _, _) in enumerate(SCENES):
            grid.grid_columnconfigure(i % 3, weight=1)
            normal = self.tile_image(sid, "normal")
            lbl = ctk.CTkLabel(grid, text="", image=normal)
            lbl.grid(row=i // 3, column=i % 3, padx=2, pady=3)
            lbl.bind("<Button-1>", lambda e, sid=sid: self.set_scene(sid))
            lbl.bind("<Enter>", lambda e, sid=sid: self._hover_scene(sid))
            lbl.bind("<Leave>", lambda e: self._hover_scene(None))
            self.scene_tiles[sid] = (lbl, normal)
        self.active_scene = None

    def build_sync_tab(self, tab):
        tab.grid_columnconfigure(1, weight=1)
        sync = self.cfg["sync"]
        ctk.CTkLabel(tab, text=t("sync_intro"), font=self.font, wraplength=int(440 * self.scale),
                     justify="left").grid(row=0, column=0, columnspan=2, sticky="w", padx=8, pady=(14, 10))
        self.sync_btn = ctk.CTkButton(tab, text=t("sync_start"), font=self.font_h, height=46,
                                      command=self.toggle_sync)
        self.sync_btn.grid(row=1, column=0, columnspan=2, sticky="ew", padx=8, pady=(0, 14))
        self.monitor_rects = monitors()
        self.monitor_names = [f"{i + 1}: {w}×{h}" for i, (_, _, w, h) in enumerate(self.monitor_rects)]
        ctk.CTkLabel(tab, text=t("sync_monitor"), font=self.font).grid(row=2, column=0, sticky="w", padx=8, pady=5)
        self.sync_monitor = ctk.CTkOptionMenu(tab, values=self.monitor_names or ["-"], font=self.font,
                                              command=lambda _: self.save_sync_settings(), dynamic_resizing=False)
        index = sync["monitor"] if sync["monitor"] < len(self.monitor_names) else 0
        self.sync_monitor.set((self.monitor_names or ["-"])[index])
        self.sync_monitor.grid(row=2, column=1, sticky="ew", padx=8, pady=5)
        self.smooth_names = {t("smooth_" + k): k for k in SMOOTHING}
        ctk.CTkLabel(tab, text=t("sync_smooth"), font=self.font).grid(row=3, column=0, sticky="w", padx=8, pady=5)
        self.sync_smooth = ctk.CTkOptionMenu(tab, values=list(self.smooth_names), font=self.font,
                                             command=lambda _: self.save_sync_settings(), dynamic_resizing=False)
        self.sync_smooth.set(t("smooth_" + sync["smoothing"]))
        self.sync_smooth.grid(row=3, column=1, sticky="ew", padx=8, pady=5)
        self.sync_vivid = ctk.CTkCheckBox(tab, text=t("sync_vivid"), font=self.font, command=self.save_sync_settings)
        if sync["vivid"]:
            self.sync_vivid.select()
        self.sync_vivid.grid(row=4, column=0, columnspan=2, sticky="w", padx=8, pady=8)
        ctk.CTkLabel(tab, text=t("sync_note"), font=self.font_s, text_color="gray60", justify="left",
                     wraplength=int(440 * self.scale)).grid(
            row=5, column=0, columnspan=2, sticky="w", padx=8, pady=(8, 0))

    def build_fav_tab(self, tab):
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(1, weight=1)
        ef = ctk.CTkFrame(tab, fg_color="transparent")
        ef.grid(row=0, column=0, sticky="ew", padx=6, pady=(10, 6))
        ef.grid_columnconfigure(0, weight=1)
        self.fav_name = ctk.CTkEntry(ef, placeholder_text=t("fav_placeholder"), height=36, font=self.font)
        self.fav_name.grid(row=0, column=0, sticky="ew")
        self.fav_name.bind("<Return>", lambda e: self.save_favorite())
        ctk.CTkButton(ef, text=t("fav_save"), font=self.font_b, height=36,
                      command=self.save_favorite).grid(row=0, column=1, padx=(6, 0))
        self.fav_list = ctk.CTkScrollableFrame(tab, fg_color="transparent")
        self.fav_list.grid(row=1, column=0, sticky="nsew")
        self.fav_list.grid_columnconfigure(1, weight=1)
        self.render_favorites()

    def build_time_tab(self, tab):
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(4, weight=1)

        ctk.CTkLabel(tab, text=t("sleep_title"), font=self.font_b, anchor="w").grid(
            row=0, column=0, sticky="w", padx=8, pady=(10, 4))
        sf = ctk.CTkFrame(tab, fg_color="transparent")
        sf.grid(row=1, column=0, sticky="ew", padx=6)
        self.sleep_choices = {t("minutes", n=n): n for n in SLEEP_MINUTES}
        self.sleep_minutes = ctk.CTkOptionMenu(sf, values=list(self.sleep_choices), width=100, font=self.font)
        self.sleep_minutes.set(t("minutes", n=30))
        self.sleep_minutes.grid(row=0, column=0)
        self.sleep_fade = ctk.CTkCheckBox(sf, text=t("sleep_fade"), font=self.font)
        self.sleep_fade.select()
        self.sleep_fade.grid(row=0, column=1, padx=10)
        self.sleep_btn = ctk.CTkButton(sf, text=t("start"), width=80, command=self.toggle_sleep, font=self.font_b)
        self.sleep_btn.grid(row=0, column=2)
        self.sleep_label = ctk.CTkLabel(tab, text="", font=self.font_s, text_color="#f5b43c", anchor="w")
        self.sleep_label.grid(row=2, column=0, sticky="w", padx=8)

        af = ctk.CTkFrame(tab, fg_color="#333333", corner_radius=10)
        af.grid(row=3, column=0, sticky="ew", padx=6, pady=(10, 6))
        af.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(af, text=t("sch_add_title"), font=self.font_b).grid(
            row=0, column=0, columnspan=3, sticky="w", padx=10, pady=(8, 4))
        self.sch_time = ctk.CTkEntry(af, width=70, placeholder_text="07:30", font=self.font)
        self.sch_time.grid(row=1, column=0, padx=(10, 6))
        self.sch_action = ctk.CTkOptionMenu(af, values=self.action_choices(), font=self.font, dynamic_resizing=False)
        self.sch_action.grid(row=1, column=1, sticky="ew")
        ctk.CTkButton(af, text=t("add"), width=70, command=self.add_schedule, font=self.font_b).grid(
            row=1, column=2, padx=(6, 10))
        df = ctk.CTkFrame(af, fg_color="transparent")
        df.grid(row=2, column=0, columnspan=3, pady=(6, 10), padx=6)
        self.sch_days = []
        for i, d in enumerate(t("days").split()):
            cb = ctk.CTkCheckBox(df, text=d, width=48, checkbox_width=18, checkbox_height=18, font=self.font_s)
            cb.select()
            cb.grid(row=0, column=i, padx=1)
            self.sch_days.append(cb)

        self.sch_list = ctk.CTkScrollableFrame(tab, fg_color="transparent")
        self.sch_list.grid(row=4, column=0, sticky="nsew")
        self.sch_list.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(tab, text=t("sch_note"), font=self.font_s,
                     text_color="gray60").grid(row=5, column=0, pady=(2, 4))
        self.render_schedules()

    def build_settings_tab(self, outer):
        outer.grid_columnconfigure(0, weight=1)
        outer.grid_rowconfigure(0, weight=1)
        tab = ctk.CTkScrollableFrame(outer, fg_color="transparent")
        tab.grid(row=0, column=0, sticky="nsew")
        tab.grid_columnconfigure(0, weight=1)
        self.autostart = ctk.CTkCheckBox(tab, text=t("autostart"), font=self.font,
                                         command=self.on_autostart)
        self.autostart.grid(row=0, column=0, sticky="w", padx=10, pady=(14, 8))
        if autostart_enabled():
            self.autostart.select()
        self.tray_close = ctk.CTkCheckBox(tab, text=t("tray_close"), font=self.font,
                                          command=self.on_tray_close)
        self.tray_close.grid(row=1, column=0, sticky="w", padx=10, pady=8)
        if self.cfg.get("close_to_tray", True):
            self.tray_close.select()
        lf = ctk.CTkFrame(tab, fg_color="transparent")
        lf.grid(row=2, column=0, sticky="w", padx=10, pady=(6, 0))
        ctk.CTkLabel(lf, text=t("language"), font=self.font).grid(row=0, column=0, padx=(0, 10))
        lang_menu = ctk.CTkOptionMenu(lf, values=[t("lang_auto")] + [n for _, n in LANGUAGES],
                                      command=self.on_language, font=self.font, width=150)
        code = self.cfg.get("language")
        lang_menu.set(dict(LANGUAGES).get(code, t("lang_auto")))
        lang_menu.grid(row=0, column=1)
        bf = ctk.CTkFrame(tab, fg_color="transparent")
        bf.grid(row=3, column=0, sticky="ew", padx=6, pady=(10, 4))
        bf.grid_columnconfigure(0, weight=1)
        self.rename_entry = ctk.CTkEntry(bf, placeholder_text=t("rename_placeholder"), font=self.font)
        self.rename_entry.grid(row=0, column=0, sticky="ew", padx=4)
        self.rename_entry.bind("<Return>", lambda e: self.rename_bulb())
        ctk.CTkButton(bf, text=t("rename"), width=120, command=self.rename_bulb, font=self.font).grid(
            row=0, column=1, padx=4)
        ctk.CTkButton(bf, text=t("rediscover"), command=self.rediscover, font=self.font).grid(
            row=2, column=0, columnspan=2, sticky="ew", padx=4, pady=(8, 0))
        self.ip_entry = ctk.CTkEntry(bf, placeholder_text=t("ip_placeholder"), font=self.font)
        self.ip_entry.grid(row=1, column=0, sticky="ew", padx=4, pady=(8, 0))
        self.ip_entry.bind("<Return>", lambda e: self.add_bulb_by_ip())
        ctk.CTkButton(bf, text=t("add"), width=120, command=self.add_bulb_by_ip, font=self.font).grid(
            row=1, column=1, padx=4, pady=(8, 0))
        self.bulb_rows = ctk.CTkFrame(tab, fg_color="transparent")
        self.bulb_rows.grid(row=4, column=0, sticky="ew", padx=6, pady=(10, 4))
        self.bulb_rows.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(tab, text=t("help"),
                     font=self.font_s, justify="left", text_color="gray55").grid(row=5, column=0, sticky="w", padx=12)
        uf = ctk.CTkFrame(tab, fg_color="transparent")
        uf.grid(row=6, column=0, sticky="ew", padx=6, pady=(14, 0))
        uf.grid_columnconfigure((0, 1), weight=1)
        self.auto_updates = ctk.CTkCheckBox(uf, text=t("auto_updates"), font=self.font, command=self.on_auto_updates)
        if self.cfg.get("check_updates", True):
            self.auto_updates.select()
        self.auto_updates.grid(row=0, column=0, columnspan=2, sticky="w", padx=4, pady=(0, 8))
        ctk.CTkButton(uf, text=t("check_updates"), font=self.font, command=self.check_updates).grid(
            row=1, column=0, sticky="ew", padx=4)
        ctk.CTkButton(uf, text=t("source_code"), font=self.font,
                      command=lambda: webbrowser.open(updates.REPO_URL)).grid(row=1, column=1, sticky="ew", padx=4)
        ctk.CTkButton(uf, text=t("open_folder"), font=self.font, command=lambda: os.startfile(CONFIG_DIR)).grid(
            row=2, column=0, sticky="ew", padx=4, pady=(8, 0))
        self.reset_btn = ctk.CTkButton(uf, text=t("reset_profile"), font=self.font, fg_color="#5a2a2a",
                                       hover_color="#7a3434", command=self.reset_profile)
        self.reset_btn.grid(row=2, column=1, sticky="ew", padx=4, pady=(8, 0))
        ctk.CTkLabel(tab, text=f"{APP_NAME} {__version__}", font=self.font_s, text_color="gray55").grid(
            row=7, column=0, sticky="w", padx=12, pady=(10, 4))

    # ---------- ampul listesi
    def bulb_names(self):
        return {b.get("name") or mac: mac for mac, b in self.cfg["bulbs"].items()}

    def refresh_bulb_menu(self):
        names = self.bulb_names()
        values = list(names) + ([t("all_bulbs")] if len(names) > 1 else [])
        if not values:
            self.bulb_menu.configure(values=[t("no_bulb_found")])
            self.bulb_menu.set(t("no_bulb_found"))
            self.cfg["selected"] = None
            self.refresh_info()
            return
        self.bulb_menu.configure(values=values)
        sel = self.cfg.get("selected")
        if sel == "all" and len(names) > 1:
            self.bulb_menu.set(t("all_bulbs"))
        else:
            if sel not in self.cfg["bulbs"]:
                self.cfg["selected"] = sel = next(iter(self.cfg["bulbs"]))
            self.bulb_menu.set(self.cfg["bulbs"][sel].get("name") or sel)
        self.refresh_info()

    def refresh_info(self):
        for w in self.bulb_rows.winfo_children():
            w.destroy()
        if not self.cfg["bulbs"]:
            ctk.CTkLabel(self.bulb_rows, text=t("no_bulbs_yet"), font=self.font_s, text_color="gray60").grid(sticky="w")
        for i, (mac, b) in enumerate(self.cfg["bulbs"].items()):
            state = t("online") if mac in self.online else t("offline")
            text = (f"{b.get('name')}  ({state})\nIP {b.get('ip')}:{PORT}   MAC {mac}\n"
                    f"Model {b.get('module', '?')}   {t('firmware')} {b.get('fw', '?')}\n"
                    f"Home {b.get('home', '?')}   Room {b.get('room', '?')}")
            ctk.CTkLabel(self.bulb_rows, text=text, font=self.font_s, justify="left", anchor="w",
                         text_color="gray70").grid(row=i, column=0, sticky="w", padx=6, pady=4)
            ctk.CTkButton(self.bulb_rows, text=t("toggle"), width=84, font=self.font_s,
                          command=lambda m=mac: self.toggle_bulb(m)).grid(row=i, column=1, padx=3)
            ctk.CTkButton(self.bulb_rows, text=t("delete"), width=64, font=self.font_s, fg_color="#5a2a2a",
                          hover_color="#7a3434", command=lambda m=mac: self.delete_bulb(m)).grid(
                row=i, column=2, padx=3)

    def on_bulb_select(self, choice):
        self.cfg["selected"] = "all" if choice == t("all_bulbs") else self.bulb_names().get(choice)
        self.save_config()
        self.first_sync = True
        self.poll_now.set()

    def rename_bulb(self):
        mac = self.cfg.get("selected")
        if mac not in self.cfg["bulbs"]:
            self.set_status(t("err_rename_single"), error=True)
            return
        name = self.rename_entry.get().strip()
        if not name:
            self.set_status(t("err_rename_empty"), error=True)
            self.rename_entry.focus_set()
            return
        if name == t("all_bulbs") or self.bulb_names().get(name, mac) != mac:
            self.set_status(t("err_name_taken"), error=True)
            return
        self.cfg["bulbs"][mac]["name"] = name
        self.save_config()
        self.rename_entry.delete(0, "end")
        self.root.focus_set()
        self.refresh_bulb_menu()
        self.render_schedules()
        self.set_status(t("renamed", name=name))

    def rediscover(self):
        self.cfg["hidden"] = []  # elle başlatılan arama silinen ampulleri de geri getirir
        self.set_status(t("status_searching"))
        threading.Thread(target=self._discover_worker, daemon=True).start()

    def add_bulb_by_ip(self):
        ip = self.ip_entry.get().strip()
        try:
            ipaddress.IPv4Address(ip)
        except ValueError:
            self.set_status(t("err_ip"), error=True)
            return
        threading.Thread(target=self._add_ip_worker, args=(ip,), daemon=True).start()

    def _add_ip_worker(self, ip):
        r = wiz_request(ip, "getPilot")
        res = (r or {}).get("result", {})
        if "mac" not in res:
            self.ui(lambda: self.set_status(t("add_failed", ip=ip), error=True))
            return
        info = (wiz_request(ip, "getSystemConfig") or {}).get("result", {})

        def apply():
            mac = res["mac"]
            if mac in self.cfg["hidden"]:
                self.cfg["hidden"].remove(mac)
            self.remember_bulb(mac, ip, info)
            self.online.add(mac)
            self.ip_entry.delete(0, "end")
            self.root.focus_set()
            self.save_config()
            self.refresh_bulb_menu()
            self.set_status(t("add_ok", ip=ip))
            self.poll_now.set()
        self.ui(apply)

    def remember_bulb(self, mac, ip, info):
        b = self.cfg["bulbs"].setdefault(mac, {"name": t("bulb_default", id=mac[-4:].upper())})
        b["ip"] = ip
        if info:
            b.update(module=info.get("moduleName", "?"), fw=info.get("fwVersion", "?"),
                     home=info.get("homeId", "?"), room=info.get("roomId", "?"))

    def toggle_bulb(self, mac):
        ip = self.bulb_ip(mac)

        def work():
            r = wiz_request(ip, "getPilot")
            if r and "result" in r:
                wiz_request(ip, "setPilot", {"state": not r["result"].get("state")})
                self.poll_now.set()
            else:
                self.ui(lambda: self.set_status(t("send_failed", ip=ip), error=True))
        threading.Thread(target=work, daemon=True).start()

    def delete_bulb(self, mac):
        del self.cfg["bulbs"][mac]
        self.online.discard(mac)
        self.cfg["hidden"].append(mac)
        if self.cfg.get("selected") == mac:
            self.cfg["selected"] = None
        self.save_config()
        self.first_sync = True
        self.refresh_bulb_menu()
        self.render_schedules()
        self.poll_now.set()

    def _discover_worker(self):
        found = {m: f for m, f in discover().items() if m not in self.cfg["hidden"]}
        for mac, b in list(self.cfg["bulbs"].items()):  # yayın engelliyse kayıtlı adresleri doğrudan dene
            if mac not in found and b.get("ip"):
                res = (wiz_request(b["ip"], "getPilot", timeout=0.4, retries=2) or {}).get("result", {})
                if res.get("mac") == mac:
                    found[mac] = {"ip": b["ip"], "pilot": res}
        log.info("Bulunan ampuller: %s", {m: f["ip"] for m, f in found.items()})
        for mac, f in found.items():
            if mac not in self.cfg["bulbs"] or "home" not in self.cfg["bulbs"][mac]:
                sc = wiz_request(f["ip"], "getSystemConfig") or {}
                f["sys"] = sc.get("result", {})

        def apply():
            for mac, f in found.items():
                self.remember_bulb(mac, f["ip"], f.get("sys"))
            self.online = set(found)
            self.save_config()
            self.refresh_bulb_menu()
            if found:
                self.set_status(t("found_n", n=len(found)))
                self.poll_now.set()
            else:
                self.set_status(t("not_found"), error=True)
        self.ui(apply)

    # ---------- durum takibi
    def _poll_worker(self):
        fails = 0
        while True:
            macs = self.target_macs()
            mac = macs[0] if macs else None
            ip = self.bulb_ip(mac)
            if ip:
                r = wiz_request(ip, "getPilot", timeout=0.5, retries=2)
                if r and "result" in r:
                    fails = 0
                    self.ui(lambda m=mac, res=r["result"]: self.on_pilot(m, res))
                else:
                    fails += 1
                    if fails == 2:
                        self.ui(lambda m=mac: self.on_lost(m))
                    if fails % 12 == 2:
                        self._discover_worker()
            self.poll_now.wait(2.5)
            self.poll_now.clear()

    def on_lost(self, mac):
        self.online.discard(mac)
        self.set_status(t("lost"), error=True)
        self.refresh_info()

    def on_pilot(self, mac, res):
        was_offline = mac not in self.online
        self.online.add(mac)
        if was_offline:
            self.refresh_info()
        if self.sync or time.time() - self.last_user_action < 3:
            return
        self.pilot = dict(res)
        self.sync_controls()
        self.set_status(t("connected", ip=self.bulb_ip(mac), rssi=res.get("rssi", "?")))

    def sync_controls(self):
        p = self.pilot
        self.power.select() if p.get("state") else self.power.deselect()
        if p.get("dimming"):
            self.dim.set(p["dimming"])
            self.dim_label.configure(text=t("brightness_v", v=p["dimming"]))
        mode = pilot_mode(p)
        if mode == "white":
            self.temp.set(p.get("temp", 2700))
            self.temp_label.configure(text=t("color_temp", k=p.get("temp", 2700)))
        elif mode == "color":
            self.hue, self.sat = pilot_to_hs(p)
            self._place_marker()
        elif mode == "scene" and p.get("speed"):
            self.speed.set(p["speed"])
            self.speed_label.configure(text=t("speed", v=p["speed"]))
        self.highlight_scene(p.get("sceneId") if mode == "scene" else None)
        if self.first_sync:
            self.first_sync = False
            self.tabs.set(self.tab_names[{"white": "white", "color": "color", "scene": "scenes"}[mode]])
        self.refresh_visuals()

    def refresh_visuals(self):
        col = pilot_color(self.pilot)
        self.swatch.configure(fg_color=col)
        if col != getattr(self, "_tray_color", None):
            self._tray_color = col
            self.tray.icon = make_bulb_icon(col)
        self.tray.update_menu()

    # ---------- komut gönderme
    def send(self, params, user=True, ips=None):
        if self.sync:  # başka bir komut ışığı devralır
            self.stop_sync(restore=False)
        ips = self.target_ips() if ips is None else ips
        if not ips:
            self.set_status(t("no_target"), error=True)
            return
        if user:
            self.last_user_action = time.time()
            if self.fade and self.fade["kind"] == "wake":
                self.fade = None
        for ip in ips:
            self.sender.submit(ip, dict(params))
        p = self.pilot
        if "sceneId" in params:
            for k in ("temp", "r", "g", "b", "c", "w"):
                p.pop(k, None)
        elif "temp" in params:
            for k in ("sceneId", "r", "g", "b", "c", "w"):
                p.pop(k, None)
        elif any(k in params for k in ("r", "g", "b", "c")):
            for k in ("sceneId", "temp", "r", "g", "b", "c", "w"):
                p.pop(k, None)
        p.update(params)
        if any(k != "state" for k in params):
            p["state"] = True
        self.power.select() if p.get("state") else self.power.deselect()
        self.refresh_visuals()

    def _on_send_result(self, ip, params, resp):
        if resp is None:
            self.ui(lambda: self.set_status(t("send_failed", ip=ip), error=True))
        elif "error" in resp:
            msg = resp["error"].get("message", t("error"))
            if "sceneId" in params:
                msg = t("scene_unsupported")
            self.ui(lambda: self.set_status(t("rejected", msg=msg), error=True))

    def toggle_power(self):
        self.send({"state": not self.pilot.get("state", False)})

    def on_power(self):
        self.send({"state": bool(self.power.get())})

    def on_dim(self, v):
        v = int(round(v))
        self.dim_label.configure(text=t("brightness_v", v=v))
        if self.sync:
            self.sync.max_dim = v
            return
        if self.fade and self.fade["kind"] == "sleep":
            remaining = self.fade["t0"] + self.fade["dur"] - time.time()
            self.fade.update(p0={"dimming": v}, t0=time.time(), dur=max(10, remaining))
        self.send({"dimming": v} if self.pilot.get("state") else {"state": True, "dimming": v})

    def cur_dim(self):
        return int(round(self.dim.get()))

    def on_temp(self, v):
        self.set_temp(int(round(v / 100) * 100), from_slider=True)

    def set_temp(self, k, from_slider=False):
        if not from_slider:
            self.temp.set(k)
        self.temp_label.configure(text=t("color_temp", k=k))
        self.highlight_scene(None)
        self.send({"temp": k, "dimming": self.cur_dim()})

    def _place_marker(self):
        R = self.wheel_size / 2 - 2
        c = self.wheel_size / 2
        a = self.hue * 2 * math.pi
        x, y = c + math.cos(a) * self.sat * R, c - math.sin(a) * self.sat * R
        r = self.marker_r
        self.wheel.coords(self.marker, x - r, y - r, x + r, y + r)

    def on_wheel(self, e):
        c = self.wheel_size / 2
        R = c - 2
        dx, dy = e.x - c, e.y - c
        if e.type == tk.EventType.ButtonPress and math.hypot(dx, dy) > R + 4:
            return
        self.set_color((math.atan2(-dy, dx) / (2 * math.pi)) % 1, min(1.0, math.hypot(dx, dy) / R))

    def set_color(self, h, s):
        self.hue, self.sat = h, s
        self._place_marker()
        self.highlight_scene(None)
        self.send(dict(hs_to_params(h, s), dimming=self.cur_dim()))

    def apply_hex(self):
        m = re.fullmatch(r"#?([0-9a-fA-F]{6})", self.hex_entry.get().strip())
        if not m:
            self.set_status(t("err_hex"), error=True)
            return
        r, g, b = (int(m.group(1)[i:i + 2], 16) / 255 for i in (0, 2, 4))
        h, s, _ = colorsys.rgb_to_hsv(r, g, b)
        self.set_color(h, s)

    def set_scene(self, sid):
        self.highlight_scene(sid)
        self.send({"sceneId": sid, "speed": int(self.speed.get()), "dimming": self.cur_dim()})

    def on_speed(self, v):
        v = int(round(v))
        self.speed_label.configure(text=t("speed", v=v))
        if self.active_scene:
            self.send({"sceneId": self.active_scene, "speed": v, "dimming": self.cur_dim()})

    def apply_scene_filter(self, *_):
        query = self.scene_search.get().strip().casefold()
        mode = self.scene_filter.get()
        shown = 0
        for sid, _, _ in SCENES:
            lbl = self.scene_tiles[sid][0]
            kind_ok = mode == t("filter_all") or (sid in STATIC_SCENES) == (mode == t("filter_static"))
            if kind_ok and query in scene_name(sid).casefold():
                lbl.grid(row=shown // 3, column=shown % 3, padx=2, pady=3)
                shown += 1
            else:
                lbl.grid_remove()

    def tile_image(self, sid, state):
        """Kutucuğu ekrandaki boyutunda üretir; hover ve aktif haller yalnızca gerektiğinde çizilir."""
        w, h = round(TILE_SIZE[0] * self.scale), round(TILE_SIZE[1] * self.scale)
        k = TILE_SUPERSAMPLE
        img = scene_tile(scene_name(sid), SCENE_PALETTES[sid], w * k, h * k, state, self.tile_font,
                         round(11 * self.scale * k))
        return ctk.CTkImage(img.resize((w, h), Image.LANCZOS), size=TILE_SIZE)

    def _paint_scene(self, sid):
        if sid not in self.scene_tiles:
            return
        lbl, normal = self.scene_tiles[sid]
        st = "active" if sid == self.active_scene else "hover" if sid == self.hover_scene else "normal"
        lbl.configure(image=normal if st == "normal" else self.tile_image(sid, st))

    def _hover_scene(self, sid):
        old, self.hover_scene = self.hover_scene, sid
        self._paint_scene(old)
        self._paint_scene(sid)

    def highlight_scene(self, sid):
        if sid == self.active_scene:
            return
        old, self.active_scene = self.active_scene, sid
        self._paint_scene(old)
        self._paint_scene(sid)

    # ---------- favoriler
    def current_params(self):
        p = self.pilot
        mode = pilot_mode(p)
        dim = self.cur_dim()
        if mode == "scene":
            return {"sceneId": p["sceneId"], "speed": p.get("speed", 100), "dimming": dim}
        if mode == "color":
            return dict({k: p[k] for k in ("r", "g", "b", "c", "w") if k in p}, dimming=dim)
        return {"temp": p.get("temp", 2700), "dimming": dim}

    def save_favorite(self):
        if not self.pilot:
            self.set_status(t("need_connection"), error=True)
            return
        favs = self.cfg["favorites"]
        params = self.current_params()
        name = self.fav_name.get().strip()
        if not name:
            base, names = describe_params(params), {f["name"] for f in favs}
            name, n = base, 2
            while name in names:
                name, n = f"{base} {n}", n + 1
        fav = {"name": name, "params": params}
        existing = next((i for i, f in enumerate(favs) if f["name"] == name), None)
        if existing is None:
            favs.append(fav)
        else:
            favs[existing] = fav
        self.save_config()
        self.fav_name.delete(0, "end")
        self.root.focus_set()
        self.render_favorites()
        self.set_status(t("fav_saved", name=name))

    def apply_favorite(self, fav, ips=None, user=True):
        params = fav["params"]
        if "sceneId" in params:
            self.highlight_scene(params["sceneId"])
        self.send(dict(params), user=user, ips=ips)
        if user:
            self.last_user_action = 0
            self.first_sync = True
            self.poll_now.set()

    def move_favorite(self, i, step):
        favs = self.cfg["favorites"]
        favs[i], favs[i + step] = favs[i + step], favs[i]
        self.save_config()
        self.render_favorites()

    def rename_favorite(self, i):
        name = self.fav_name.get().strip()
        if not name:
            self.set_status(t("err_rename_empty"), error=True)
            self.fav_name.focus_set()
            return
        favs = self.cfg["favorites"]
        if any(f["name"] == name for f in favs):
            self.set_status(t("err_name_taken"), error=True)
            return
        old = favs[i]["name"]
        favs[i]["name"] = name
        for s in self.cfg["schedules"]:
            if s["action"] == "fav:" + old:
                s["action"] = "fav:" + name
        self.save_config()
        self.fav_name.delete(0, "end")
        self.root.focus_set()
        self.render_favorites()
        self.render_schedules()

    def delete_favorite(self, i):
        del self.cfg["favorites"][i]
        self.save_config()
        self.render_favorites()

    def render_favorites(self):
        for w in self.fav_list.winfo_children():
            w.destroy()
        favs = self.cfg["favorites"]
        if not favs:
            ctk.CTkLabel(self.fav_list, text=t("no_favs"),
                         font=self.font_s, text_color="gray60").grid(row=0, column=0, columnspan=3, pady=20)
        for i, fav in enumerate(favs):
            col = pilot_color(fav["params"])
            ctk.CTkFrame(self.fav_list, width=26, height=26, corner_radius=13, fg_color=col).grid(
                row=i, column=0, padx=(4, 8), pady=4)
            ctk.CTkButton(self.fav_list, text=fav["name"], anchor="w", font=self.font, fg_color="#363636",
                          hover_color="#444444", command=lambda f=fav: self.apply_favorite(f)).grid(
                row=i, column=1, sticky="ew", pady=4)
            bf = ctk.CTkFrame(self.fav_list, fg_color="transparent")
            bf.grid(row=i, column=2, padx=(6, 4), pady=4)
            for col, (text, step) in enumerate((("↑", -1), ("↓", 1))):
                ok = 0 <= i + step < len(favs)
                ctk.CTkButton(bf, text=text, width=28, font=self.font_b, fg_color="#363636", hover_color="#444444",
                              state="normal" if ok else "disabled",
                              command=lambda i=i, s=step: self.move_favorite(i, s)).grid(row=0, column=col, padx=1)
            ctk.CTkButton(bf, text=t("rename"), width=70, font=self.font_s, fg_color="#363636", hover_color="#444444",
                          command=lambda i=i: self.rename_favorite(i)).grid(row=0, column=2, padx=2)
            ctk.CTkButton(bf, text=t("delete"), width=64, font=self.font_s, fg_color="#5a2a2a",
                          hover_color="#7a3434", command=lambda i=i: self.delete_favorite(i)).grid(row=0, column=3)
        if hasattr(self, "sch_action"):
            self.sch_action.configure(values=self.action_choices())
        if hasattr(self, "tray"):
            self.tray.update_menu()

    # ---------- zamanlayıcılar
    def action_label(self, action):
        return f"★ {action[4:]}" if action.startswith("fav:") else t("action_" + action)

    def action_choices(self):
        return [self.action_label(a) for a in ACTIONS] + [f"★ {f['name']}" for f in self.cfg["favorites"]]

    def action_from_label(self, label):
        return next((a for a in ACTIONS if t("action_" + a) == label), None) or "fav:" + label[2:]

    def toggle_sleep(self):
        if self.sleep_deadline:
            self.cancel_sleep()
            return
        if not self.target_ips():
            self.set_status(t("no_target"), error=True)
            return
        minutes = self.sleep_choices[self.sleep_minutes.get()]
        self.sleep_deadline = time.time() + minutes * 60
        if self.sleep_fade.get():
            self.start_fade("sleep", {"dimming": self.cur_dim()}, {"dimming": 10}, minutes * 60, self.target_ips())
        self.sleep_btn.configure(text=t("cancel"), fg_color="#7a3434", hover_color="#934040")
        self._update_sleep_label()

    def cancel_sleep(self):
        self.sleep_deadline = None
        if self.fade and self.fade["kind"] == "sleep":
            self.fade = None
        self.sleep_btn.configure(text=t("start"), fg_color=ctk.ThemeManager.theme["CTkButton"]["fg_color"],
                                 hover_color=ctk.ThemeManager.theme["CTkButton"]["hover_color"])
        self.sleep_label.configure(text="")

    def _update_sleep_label(self):
        if self.sleep_deadline:
            left = max(0, int(self.sleep_deadline - time.time()))
            self.sleep_label.configure(text=t("sleep_left", t=f"{left // 60:02d}:{left % 60:02d}"))

    def start_fade(self, kind, p0, p1, seconds, ips, end_off=False):
        self.fade = {"kind": kind, "p0": p0, "p1": p1, "t0": time.time(), "dur": seconds, "ips": ips,
                     "end_off": end_off, "last": 0}

    def _step_fade(self):
        f = self.fade
        if not f:
            return
        now = time.time()
        prog = min(1.0, (now - f["t0"]) / f["dur"])
        if prog < 1 and now - f["last"] < 5:
            return
        f["last"] = now
        params = {k: int(round(f["p0"][k] + (f["p1"][k] - f["p0"][k]) * prog)) for k in f["p1"] if k in f["p0"]}
        if "temp" in params:
            params["temp"] = int(round(params["temp"] / 100) * 100)
        if params:
            self.send(params, user=False, ips=f["ips"])
        if "dimming" in params:
            self.dim.set(params["dimming"])
            self.dim_label.configure(text=t("brightness_v", v=params["dimming"]))
        if prog >= 1:
            self.fade = None
            if f["end_off"]:
                self.send({"state": False}, user=False, ips=f["ips"])

    def add_schedule(self):
        tm = self.sch_time.get().strip()
        if not re.fullmatch(r"([01]?\d|2[0-3]):[0-5]\d", tm):
            self.set_status(t("err_time"), error=True)
            return
        hh, mm = tm.split(":")
        days = [i for i, cb in enumerate(self.sch_days) if cb.get()]
        if not days:
            self.set_status(t("err_no_day"), error=True)
            return
        target = self.cfg.get("selected")
        if not target:
            self.set_status(t("no_target"), error=True)
            return
        self.cfg["schedules"].append({"id": int(time.time() * 1000), "time": f"{int(hh):02d}:{mm}",
                                      "action": self.action_from_label(self.sch_action.get()), "days": days,
                                      "target": target, "enabled": True})
        self.cfg["schedules"].sort(key=lambda s: s["time"])
        self.save_config()
        self.render_schedules()
        self.set_status(t("sch_added"))

    def render_schedules(self):
        for w in self.sch_list.winfo_children():
            w.destroy()
        names = {mac: b.get("name") for mac, b in self.cfg["bulbs"].items()}
        for i, s in enumerate(self.cfg["schedules"]):
            day_names = t("days").split()
            days = t("every_day") if len(s["days"]) == 7 else ", ".join(day_names[d] for d in s["days"])
            who = t("all_bulbs") if s["target"] == "all" else names.get(s["target"], "?")
            row = ctk.CTkFrame(self.sch_list, fg_color="#363636", corner_radius=8)
            row.grid(row=i, column=0, sticky="ew", pady=3, padx=2)
            row.grid_columnconfigure(1, weight=1)
            ctk.CTkLabel(row, text=s["time"], font=self.font_h).grid(row=0, column=0, rowspan=2, padx=10, pady=6)
            ctk.CTkLabel(row, text=self.action_label(s["action"]), font=self.font_b, anchor="w").grid(
                row=0, column=1, sticky="w")
            ctk.CTkLabel(row, text=f"{days}  •  {who}", font=self.font_s, text_color="gray60", anchor="w").grid(
                row=1, column=1, sticky="w")
            sw = ctk.CTkSwitch(row, text="", width=40, command=lambda s=s: self.toggle_schedule(s))
            sw.select() if s.get("enabled", True) else sw.deselect()
            sw.grid(row=0, column=2, rowspan=2)
            ctk.CTkButton(row, text=t("delete"), width=64, font=self.font_s, fg_color="#5a2a2a", hover_color="#7a3434",
                          command=lambda s=s: self.delete_schedule(s)).grid(row=0, column=3, rowspan=2, padx=8)

    def toggle_schedule(self, s):
        s["enabled"] = not s.get("enabled", True)
        self.save_config()

    def delete_schedule(self, s):
        self.cfg["schedules"].remove(s)
        self.save_config()
        self.render_schedules()

    def fire_schedule(self, s):
        ips = self.target_ips(s["target"])
        if not ips:
            return
        a = s["action"]
        if a == "on":
            self.send({"state": True}, user=False, ips=ips)
        elif a == "off":
            self.send({"state": False}, user=False, ips=ips)
        elif a == "wake":
            self.send({"temp": 2200, "dimming": 10}, user=False, ips=ips)
            self.start_fade("wake", {"temp": 2200, "dimming": 10}, {"temp": 4200, "dimming": 100}, 30 * 60, ips)
        elif a == "fade_off":
            self.start_fade("off", {"dimming": self.cur_dim()}, {"dimming": 10}, 30 * 60, ips, end_off=True)
        elif a.startswith("fav:"):
            fav = next((f for f in self.cfg["favorites"] if f["name"] == a[4:]), None)
            if fav:
                self.apply_favorite(fav, ips=ips, user=False)
        self.set_status(t("sch_ran", time=s["time"], action=self.action_label(a)))
        self.poll_now.set()

    def _tick(self):
        now = datetime.now()
        minute = now.strftime("%Y-%m-%d %H:%M")
        if minute != self.last_minute:
            self.last_minute = minute
            hm = now.strftime("%H:%M")
            for s in self.cfg["schedules"]:
                key = (s["id"], minute)
                if s.get("enabled", True) and s["time"] == hm and now.weekday() in s["days"] and key not in self.fired:
                    self.fired.add(key)
                    self.fire_schedule(s)
        self._step_fade()
        if self.sleep_deadline:
            if time.time() >= self.sleep_deadline:
                ips = self.fade["ips"] if self.fade and self.fade["kind"] == "sleep" else self.target_ips()
                self.cancel_sleep()
                self.send({"state": False}, user=False, ips=ips)
                self.set_status(t("sleep_done"))
            else:
                self._update_sleep_label()
        self.root.after(1000, self._tick)

    # ---------- ayarlar
    def on_autostart(self):
        try:
            set_autostart(bool(self.autostart.get()))
        except OSError as e:
            self.set_status(t("autostart_failed", e=e), error=True)

    def on_language(self, choice):
        code = next((c for c, n in LANGUAGES if n == choice), None)
        if code == self.cfg.get("language"):
            return
        self.cfg["language"] = code
        self.save_config()
        self.restart()

    def restart(self):
        """Arayüzü yeni dille kurmak için uygulamayı yeniden başlatır."""
        self.ipc_sock.close()
        env = dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT="1")
        args = [sys.executable] if getattr(sys, "frozen", False) else [sys.executable, "-m", "wizkontrol"]
        subprocess.Popen(args, env=env, close_fds=True)
        self.quit()

    def on_auto_updates(self):
        self.cfg["check_updates"] = bool(self.auto_updates.get())
        self.save_config()

    def check_updates(self):
        threading.Thread(target=self._update_worker, args=(True,), daemon=True).start()

    def _update_worker(self, report=False):
        release = updates.latest_release()

        def apply():
            if release and updates.is_newer(release[0]):
                tag, url = release
                self.update_btn.configure(text=t("update_available", v=tag), command=lambda: webbrowser.open(url))
                self.update_btn.grid(row=4, column=0, sticky="ew", padx=14, pady=(0, 10))
            elif report and release:
                self.set_status(t("up_to_date", v=__version__))
            elif report:
                self.set_status(t("update_failed"), error=True)
        self.ui(apply)

    def reset_profile(self):
        if not self.reset_armed:
            self.reset_armed = True
            self.set_status(t("reset_confirm"), error=True)
            self.root.after(6000, lambda: setattr(self, "reset_armed", False))
            return
        if os.path.exists(CONFIG_PATH):
            os.remove(CONFIG_PATH)
        self.restart()

    def save_sync_settings(self):
        name = self.sync_monitor.get()
        index = self.monitor_names.index(name) if name in self.monitor_names else 0
        self.cfg["sync"] = {"monitor": index, "smoothing": self.smooth_names[self.sync_smooth.get()],
                            "vivid": bool(self.sync_vivid.get())}
        self.save_config()
        if self.sync:  # motoru durdurmadan uygula; ekran değiştiyse bir sonraki başlatmada geçerli olur
            self.sync.alpha = SMOOTHING[self.cfg["sync"]["smoothing"]]
            self.sync.vivid = self.cfg["sync"]["vivid"]

    def toggle_sync(self):
        if self.sync:
            self.stop_sync()
        else:
            self.start_sync()

    def start_sync(self):
        ips = self.target_ips()
        if not ips or not self.monitor_rects:
            self.set_status(t("no_target"), error=True)
            return
        cfg = self.cfg["sync"]
        rect = self.monitor_rects[min(cfg["monitor"], len(self.monitor_rects) - 1)]
        was_on = self.pilot.get("state", True)
        self.sync_restore = dict(self.current_params()) if was_on else {"state": False}
        self.fade = None
        self.sync = ScreenSync(ips, rect, cfg["smoothing"], cfg["vivid"], self.cur_dim(),
                               on_color=lambda c: self.ui(lambda: self.on_sync_color(c)))
        self.sync.start()
        self.power.select()
        self.sync_btn.configure(text=t("sync_stop"), fg_color="#7a3434", hover_color="#934040")
        self.set_status(t("sync_on"))
        self.tray.update_menu()

    def on_sync_color(self, rgb):
        if self.sync:
            self.swatch.configure(fg_color=to_hex(rgb))

    def stop_sync(self, restore=True, wait=False):
        sync, self.sync = self.sync, None
        if not sync:
            return
        sync.stop()
        sync.join(0.5)
        self.sync_btn.configure(text=t("sync_start"), fg_color=ctk.ThemeManager.theme["CTkButton"]["fg_color"],
                                hover_color=ctk.ThemeManager.theme["CTkButton"]["hover_color"])
        if restore and self.sync_restore:
            if wait:  # uygulama kapanırken arka plan göndericisine güvenilemez
                for ip in sync.ips:
                    wiz_request(ip, "setPilot", self.sync_restore)
            else:
                self.send(self.sync_restore, ips=sync.ips)
            self.dim.set(self.sync_restore.get("dimming", self.cur_dim()))
        self.tray.update_menu()
        self.poll_now.set()

    def on_tray_close(self):
        self.cfg["close_to_tray"] = bool(self.tray_close.get())
        self.save_config()

    # ---------- sistem tepsisi ve pencere
    def build_tray(self):
        Menu, Item = pystray.Menu, pystray.MenuItem

        def dim_item(v):
            return Item(f"%{v}", lambda: self.ui(lambda: (self.dim.set(v), self.on_dim(v))))

        def fav_item(f):
            return Item(f["name"], lambda: self.ui(lambda: self.apply_favorite(f)))

        def favs():
            items = [fav_item(f) for f in self.cfg["favorites"]]
            return items or [Item(t("tray_empty"), None, enabled=False)]

        menu = Menu(
            Item(t("tray_show"), lambda: self.ui(self.show_window), default=True),
            Item(lambda item: t("action_off") if self.pilot.get("state") else t("action_on"),
                 lambda: self.ui(self.toggle_power)),
            Item(t("sync_tray"), lambda: self.ui(self.toggle_sync), checked=lambda item: bool(self.sync)),
            Item(t("brightness"), Menu(*[dim_item(v) for v in (10, 25, 50, 75, 100)])),
            Item(t("favorites"), Menu(favs)),
            Menu.SEPARATOR,
            Item(t("quit"), lambda: self.ui(self.quit)),
        )
        self.tray = pystray.Icon("WizKontrol", make_bulb_icon("#ffc94d"), APP_NAME, menu)
        self.tray.run_detached()

    def show_window(self):
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()
        self.poll_now.set()

    def on_close(self):
        if self.cfg.get("close_to_tray", True):
            self.root.withdraw()
            self.root.after(500, trim_memory)
        else:
            self.quit()

    def quit(self):
        self.stop_sync(wait=True)
        try:
            self.tray.stop()
        finally:
            self.root.destroy()

    def _ipc_worker(self, sock):
        while True:
            try:
                data, _ = sock.recvfrom(64)
            except OSError:
                return
            if data == b"show":
                self.ui(self.show_window)

    def run(self):
        self.refresh_bulb_menu()
        self.root.after(5000, trim_memory)
        self.root.mainloop()


def single_instance():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.bind(("127.0.0.1", IPC_PORT))
        return s
    except OSError:
        s.sendto(b"show", ("127.0.0.1", IPC_PORT))
        sys.exit(0)


def main():
    os.makedirs(CONFIG_DIR, exist_ok=True)
    handler = RotatingFileHandler(LOG_PATH, maxBytes=256_000, backupCount=1, encoding="utf-8")
    logging.basicConfig(level=logging.INFO, handlers=[handler],
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    log.info("%s %s başlıyor", APP_NAME, __version__)
    App(single_instance(), "--tray" in sys.argv).run()
