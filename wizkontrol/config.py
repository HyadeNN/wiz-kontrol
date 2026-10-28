"""Kullanıcı ayarlarının saklanması ve Windows ile otomatik başlatma."""
import json
import os
import sys

CONFIG_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "WizKontrol")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")
ICON_PATH = os.path.join(CONFIG_DIR, "icon.ico")
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def load_config():
    cfg = {"bulbs": {}, "selected": None, "favorites": [], "schedules": [], "close_to_tray": True}
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            cfg.update(json.load(f))
    except (OSError, ValueError):
        pass
    return cfg


def autostart_command():
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --tray'
    pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    return f'"{pyw}" -m wizkontrol --tray'


def autostart_enabled():
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, "WizKontrol")
            return True
    except OSError:
        return False


def set_autostart(on):
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if on:
            winreg.SetValueEx(k, "WizKontrol", 0, winreg.REG_SZ, autostart_command())
        else:
            try:
                winreg.DeleteValue(k, "WizKontrol")
            except OSError:
                pass
