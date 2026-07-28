import ctypes
import os
from pathlib import Path

import customtkinter as ctk


APP_COLORS = {
    "background": "#2C2C2F",
    "surface": "#363639",
    "surface_deep": "#242426",
    "surface_muted": "#313134",
    "surface_hover": "#383839",
    "border": "#45454A",
    "border_strong": "#525257",
    "text": "#F0F0F2",
    "text_strong": "#FFFFFF",
    "text_muted": "#A8A8B0",
    "text_subtle": "#76767D",
    "primary": "#3EAAE4",
    "primary_hover": "#5BB8E8",
    "primary_tint": "#263D49",
    "accent": "#FF8A3D",
    "accent_hover": "#FF9B5F",
    "accent_tint": "#493225",
    "success": "#34D399",
    "success_tint": "#233E35",
    "danger": "#F87171",
    "danger_tint": "#472D2D",
}

FONT_PATH = Path(__file__).resolve().parent / "assets" / "fonts" / "DMSans.ttf"
_APP_FONT_FAMILY = "Segoe UI"
_FONT_RESOURCE_LOADED = False


def initialize_app_font():
    global _APP_FONT_FAMILY
    global _FONT_RESOURCE_LOADED

    if not _FONT_RESOURCE_LOADED and os.name == "nt" and FONT_PATH.exists():
        try:
            loaded = ctypes.windll.gdi32.AddFontResourceExW(
                str(FONT_PATH),
                0x10,
                0,
            )
        except (AttributeError, OSError):
            loaded = 0
        if loaded:
            _APP_FONT_FAMILY = "DM Sans 14pt"
            _FONT_RESOURCE_LOADED = True
    ctk.ThemeManager.theme["CTkFont"]["family"] = _APP_FONT_FAMILY
    return _APP_FONT_FAMILY


def get_app_font_family():
    return _APP_FONT_FAMILY


def app_font(size, weight="normal"):
    return ctk.CTkFont(
        family=get_app_font_family(),
        size=size,
        weight=weight,
    )
