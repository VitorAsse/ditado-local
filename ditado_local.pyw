import ctypes
import gc
import math
import os
import queue
import sys
import threading
import time
import tkinter as tk
import uuid
from pathlib import Path

import customtkinter as ctk
import numpy as np
import pyperclip
import pystray
import sounddevice as sd
import soxr
from PIL import Image, ImageDraw
from pynput import keyboard as pynput_keyboard


APP_ROOT = Path(os.environ["LOCALAPPDATA"]) / "faster-whisper"
GPU_LIBS = APP_ROOT / "gpu-libs"
DLL_DIRECTORY_HANDLE = None
CUDA_RUNTIME_DLLS = (
    "cublas64_12.dll",
    "cublasLt64_12.dll",
    "cudnn_ops_infer64_8.dll",
    "cudnn_cnn_infer64_8.dll",
)
CUDA_PROBE_HANDLES = []

if GPU_LIBS.exists():
    os.environ["PATH"] = f"{GPU_LIBS}{os.pathsep}{os.environ.get('PATH', '')}"
    if hasattr(os, "add_dll_directory"):
        DLL_DIRECTORY_HANDLE = os.add_dll_directory(str(GPU_LIBS))


def is_cuda_runtime_available():
    if CUDA_PROBE_HANDLES:
        return True
    loaded_handles = []
    try:
        for library_name in CUDA_RUNTIME_DLLS:
            loaded_handles.append(ctypes.WinDLL(library_name))
    except OSError:
        return False
    CUDA_PROBE_HANDLES.extend(loaded_handles)
    return True


from faster_whisper import WhisperModel

from ditado_ai import (
    OllamaClient,
    apply_custom_corrections,
    correction_prompt,
    select_voice_skill,
)
from ditado_audio import PlaybackMuteController
from ditado_storage import AppConfig, HistoryStore


SAMPLE_RATE = 16_000
OVERLAY_WIDTH = 380
OVERLAY_HEIGHT = 92
INSTANCE_NAMESPACE = "".join(
    character
    if character.isalnum() or character in {"_", "-"}
    else "_"
    for character in os.environ.get(
        "DITADO_INSTANCE_NAMESPACE",
        "DitadoLocalFasterWhisper",
    )[:80]
)
MUTEX_NAME = f"Local\\{INSTANCE_NAMESPACE}Mutex"
SHOW_EVENT_NAME = f"Local\\{INSTANCE_NAMESPACE}ShowWindow"
WAIT_OBJECT_0 = 0
EVENT_ALL_ACCESS = 0x1F0003


TRANSCRIPTION_PROFILES = {
    "balanced": {
        "model": "dropbox-dash/faster-whisper-large-v3-turbo",
        "beam_size": 3,
        "display_name": "Equilibrado",
        "backend_name": "Whisper Large v3 Turbo",
        "description": "Large v3 Turbo com busca equilibrada e revisão local opcional",
    },
    "max_precision": {
        "model": "large-v3",
        "beam_size": 5,
        "display_name": "Precisão máxima",
        "backend_name": "Whisper Large v3",
        "description": "Large v3 com busca completa para áudios mais difíceis",
    },
}

TRANSCRIPTION_PROFILE_IDS_BY_LABEL = {
    profile["display_name"]: profile_id
    for profile_id, profile in TRANSCRIPTION_PROFILES.items()
}

TRANSCRIPTION_LANGUAGES = {
    "auto": {"display_name": "Detectar automaticamente", "code": None},
    "pt": {"display_name": "Português", "code": "pt"},
    "en": {"display_name": "Inglês", "code": "en"},
    "es": {"display_name": "Espanhol", "code": "es"},
    "fr": {"display_name": "Francês", "code": "fr"},
    "de": {"display_name": "Alemão", "code": "de"},
    "it": {"display_name": "Italiano", "code": "it"},
    "ar": {"display_name": "Árabe", "code": "ar"},
}
TRANSCRIPTION_LANGUAGE_IDS_BY_LABEL = {
    language["display_name"]: language_id
    for language_id, language in TRANSCRIPTION_LANGUAGES.items()
}


def resolve_transcription_profile(profile_name):
    return TRANSCRIPTION_PROFILES.get(
        profile_name,
        TRANSCRIPTION_PROFILES["balanced"],
    )


def resolve_transcription_language(language_name):
    return TRANSCRIPTION_LANGUAGES.get(
        language_name,
        TRANSCRIPTION_LANGUAGES["auto"],
    )["code"]


def ensure_single_instance():
    show_event = ctypes.windll.kernel32.CreateEventW(
        None,
        True,
        False,
        SHOW_EVENT_NAME,
    )
    mutex = ctypes.windll.kernel32.CreateMutexW(None, False, MUTEX_NAME)
    if ctypes.windll.kernel32.GetLastError() == 183:
        if "--background" not in sys.argv:
            ctypes.windll.kernel32.SetEvent(show_event)
        ctypes.windll.kernel32.CloseHandle(mutex)
        ctypes.windll.kernel32.CloseHandle(show_event)
        sys.exit(0)
    return mutex, show_event


class FloatingOverlay:
    def __init__(self, root):
        self.root = root
        self.window = tk.Toplevel(root)
        self.window.withdraw()
        self.window.overrideredirect(True)
        self.window.attributes("-topmost", True)
        self.window.configure(bg="#010101")
        try:
            self.window.wm_attributes("-transparentcolor", "#010101")
        except tk.TclError:
            pass

        self.canvas = tk.Canvas(
            self.window,
            width=OVERLAY_WIDTH,
            height=OVERLAY_HEIGHT,
            bg="#010101",
            highlightthickness=0,
        )
        self.canvas.pack()
        self._rounded_rectangle(
            2,
            2,
            OVERLAY_WIDTH - 2,
            OVERLAY_HEIGHT - 2,
            24,
            fill="#15151B",
            outline="#30303A",
            width=1,
        )
        self.canvas.create_oval(18, 19, 72, 73, fill="#24242D", outline="")

        self.bar_ids = []
        for index in range(7):
            x = 29 + index * 5
            self.bar_ids.append(
                self.canvas.create_rectangle(x, 41, x + 3, 51, fill="#9B87F5", outline="")
            )

        self.title_id = self.canvas.create_text(
            92,
            33,
            anchor="w",
            text="Ouvindo...",
            fill="#FFFFFF",
            font=("Segoe UI", 12, "bold"),
        )
        self.subtitle_id = self.canvas.create_text(
            92,
            58,
            anchor="w",
            text="Solte para transcrever",
            fill="#A6A6B3",
            font=("Segoe UI", 9),
        )
        self.dot_id = self.canvas.create_oval(348, 41, 359, 52, fill="#9B87F5", outline="")
        self.state = "hidden"
        self.level = 0.0
        self.phase = 0.0
        self.visible = False
        self._position()
        self.window.update_idletasks()
        self._prevent_activation()
        self._animate()

    def _rounded_rectangle(self, x1, y1, x2, y2, radius, **kwargs):
        points = [
            x1 + radius, y1,
            x2 - radius, y1,
            x2, y1,
            x2, y1 + radius,
            x2, y2 - radius,
            x2, y2,
            x2 - radius, y2,
            x1 + radius, y2,
            x1, y2,
            x1, y2 - radius,
            x1, y1 + radius,
            x1, y1,
        ]
        return self.canvas.create_polygon(points, smooth=True, splinesteps=18, **kwargs)

    def _position(self):
        screen_width = self.window.winfo_screenwidth()
        screen_height = self.window.winfo_screenheight()
        x = (screen_width - OVERLAY_WIDTH) // 2
        y = screen_height - OVERLAY_HEIGHT - 82
        self.window.geometry(f"{OVERLAY_WIDTH}x{OVERLAY_HEIGHT}+{x}+{y}")

    def _prevent_activation(self):
        try:
            hwnd = ctypes.windll.user32.GetParent(self.window.winfo_id())
            current_style = ctypes.windll.user32.GetWindowLongW(hwnd, -20)
            ctypes.windll.user32.SetWindowLongW(
                hwnd,
                -20,
                current_style | 0x08000000 | 0x00000080,
            )
        except Exception:
            pass

    def show(self, state, title, subtitle, color):
        self.state = state
        self.canvas.itemconfigure(self.title_id, text=title)
        self.canvas.itemconfigure(self.subtitle_id, text=subtitle)
        self.canvas.itemconfigure(self.dot_id, fill=color)
        for bar in self.bar_ids:
            self.canvas.itemconfigure(bar, fill=color)
        if not self.visible:
            self.visible = True
            self._position()
            self.window.deiconify()
            self.window.lift()

    def hide(self):
        self.visible = False
        self.state = "hidden"
        self.level = 0.0
        self.window.withdraw()

    def set_level(self, level):
        self.level = max(0.0, min(1.0, level))

    def _animate(self):
        self.phase += 0.32
        if self.visible:
            for index, bar in enumerate(self.bar_ids):
                if self.state in {"recording", "agent_recording"}:
                    movement = 0.35 + 0.65 * abs(math.sin(self.phase + index * 0.72))
                    height = 8 + int(self.level * 34 * movement)
                elif self.state in {"processing", "agent_processing"}:
                    height = 8 + int(16 * abs(math.sin(self.phase + index * 0.55)))
                else:
                    height = 8 + int(5 * abs(math.sin(self.phase + index * 0.45)))
                x1, _, x2, _ = self.canvas.coords(bar)
                center_y = 46
                self.canvas.coords(bar, x1, center_y - height / 2, x2, center_y + height / 2)
        self.root.after(45, self._animate)


class DitadoLocalApp:
    CTRL_KEYS = {pynput_keyboard.Key.ctrl, pynput_keyboard.Key.ctrl_l, pynput_keyboard.Key.ctrl_r}
    LEFT_AGENT_KEYS = {pynput_keyboard.Key.ctrl_l, pynput_keyboard.Key.alt_l}

    def __init__(self):
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("dark-blue")

        self.start_hidden = "--background" in sys.argv
        self.config = AppConfig()
        self.history = HistoryStore(limit=int(self.config.get("history_limit", 150)))
        self.ollama = OllamaClient()
        self.playback_mute = PlaybackMuteController()

        self.root = ctk.CTk()
        self.root.title("Ditado local")
        self.root.geometry("790x770")
        self.root.minsize(740, 690)
        self.root.configure(fg_color="#09090D")

        self.events = queue.SimpleQueue()
        self.model_lock = threading.Lock()
        self.model = None
        self.model_profile_id = None
        initial_profile_id = self.config.get("transcription_profile", "balanced")
        initial_profile = resolve_transcription_profile(initial_profile_id)
        initial_language_id = self.config.get("transcription_language", "auto")
        initial_language = TRANSCRIPTION_LANGUAGES.get(
            initial_language_id,
            TRANSCRIPTION_LANGUAGES["auto"],
        )
        self.model_backend = (
            f"Preparando {initial_profile['backend_name']}..."
        )
        self.agent_backend = "Preparando agente local..."
        self.recording = False
        self.processing = False
        self.recording_mode = "dictation"
        self.closing = False
        self.stream = None
        self.audio_chunks = []
        self.input_sample_rate = SAMPLE_RATE
        self.current_level = 0.0
        self.target_window = None
        self.agent_selected_text = ""
        self.selection_ready = threading.Event()
        self.keys_down = set()
        self.dictation_chord_active = False
        self.agent_chord_active = False
        self.suppress_hotkeys_until = 0.0
        self.ignore_clipboard_until = 0.0
        self.history_dirty = True
        self.last_clipboard_text = self._read_clipboard_text()
        self.keyboard_controller = pynput_keyboard.Controller()

        self.auto_paste = tk.BooleanVar(value=bool(self.config.get("auto_paste", True)))
        self.grammar_correction = tk.BooleanVar(value=bool(self.config.get("grammar_correction", True)))
        self.capture_clipboard_history = tk.BooleanVar(
            value=bool(self.config.get("capture_clipboard_history", False))
        )
        self.mute_playback_while_recording = tk.BooleanVar(
            value=bool(self.config.get("mute_playback_while_recording", True))
        )
        self.transcription_profile = tk.StringVar(
            value=initial_profile["display_name"]
        )
        self.transcription_language = tk.StringVar(
            value=initial_language["display_name"]
        )
        self.profile_description_text = tk.StringVar(
            value=initial_profile["description"]
        )
        self.status = tk.StringVar(value="Preparando transcrição e agente local...")
        self.backend_text = tk.StringVar(value=self.model_backend)
        self.agent_status_text = tk.StringVar(value=self.agent_backend)
        self.rules_status_text = tk.StringVar(value=self._rules_status())
        self.skills_status_text = tk.StringVar(value=self._skills_status())
        self.editing_rule_id = None
        self.editing_skill_id = None

        self.input_devices = self._get_input_devices()
        self._build_interface()
        self.overlay = FloatingOverlay(self.root)
        if self.start_hidden:
            self.root.withdraw()

        self.listener = pynput_keyboard.Listener(
            on_press=self._on_key_press,
            on_release=self._on_key_release,
        )
        self.listener.start()
        self.tray_icon = self._create_tray_icon()
        threading.Thread(target=self.tray_icon.run, daemon=True).start()

        self.root.protocol("WM_DELETE_WINDOW", self._hide_main_window)
        self.root.after(50, self._process_events)
        self.root.after(450, self._poll_clipboard)
        self.root.after(700, self._refresh_lazy_views)
        self.root.after(300, self._start_model_preload)

    def _get_input_devices(self):
        devices = []
        default_input = sd.default.device[0]
        for index, device in enumerate(sd.query_devices()):
            if device["max_input_channels"] > 0:
                devices.append(
                    {
                        "index": index,
                        "name": device["name"],
                        "sample_rate": int(device["default_samplerate"]),
                        "default": index == default_input,
                    }
                )
        devices.sort(key=lambda item: 0 if item["default"] else 1)
        return devices

    def _build_interface(self):
        shell = ctk.CTkFrame(self.root, fg_color="transparent")
        shell.pack(fill="both", expand=True, padx=26, pady=(20, 16))

        header = ctk.CTkFrame(shell, fg_color="transparent")
        header.pack(fill="x")
        ctk.CTkLabel(
            header,
            text="  D  ",
            width=48,
            height=48,
            corner_radius=16,
            fg_color="#7C5CFC",
            text_color="#FFFFFF",
            font=ctk.CTkFont(size=18, weight="bold"),
        ).pack(side="left")
        title_group = ctk.CTkFrame(header, fg_color="transparent")
        title_group.pack(side="left", padx=14)
        ctk.CTkLabel(
            title_group,
            text="Ditado local",
            text_color="#FFFFFF",
            font=ctk.CTkFont(size=22, weight="bold"),
        ).pack(anchor="w")
        ctk.CTkLabel(
            title_group,
            text="Ditado, revisão e ações por voz no Windows",
            text_color="#9898A6",
            font=ctk.CTkFont(size=12),
        ).pack(anchor="w", pady=(2, 0))
        self.ready_badge = ctk.CTkLabel(
            header,
            text="PREPARANDO",
            width=104,
            height=30,
            corner_radius=15,
            fg_color="#25232D",
            text_color="#C9C3FF",
            font=ctk.CTkFont(size=10, weight="bold"),
        )
        self.ready_badge.pack(side="right")

        self.tabs = ctk.CTkTabview(
            shell,
            fg_color="#111116",
            segmented_button_fg_color="#18181F",
            segmented_button_selected_color="#6F52E5",
            segmented_button_selected_hover_color="#7C5CFC",
            segmented_button_unselected_color="#18181F",
            segmented_button_unselected_hover_color="#25252E",
            border_width=1,
            border_color="#24242D",
            corner_radius=20,
        )
        self.tabs.pack(fill="both", expand=True, pady=(20, 14))
        for tab_name in [
            "Ditado",
            "Correções",
            "Histórico",
            "Agente",
            "Regras",
            "Skills",
        ]:
            self.tabs.add(tab_name)

        self._build_dictation_tab(self.tabs.tab("Ditado"))
        self._build_corrections_tab(self.tabs.tab("Correções"))
        self._build_history_tab(self.tabs.tab("Histórico"))
        self._build_agent_tab(self.tabs.tab("Agente"))
        self._build_rules_tab(self.tabs.tab("Regras"))
        self._build_skills_tab(self.tabs.tab("Skills"))

        status_card = ctk.CTkFrame(shell, fg_color="#101014", corner_radius=15)
        status_card.pack(fill="x")
        status_inner = ctk.CTkFrame(status_card, fg_color="transparent")
        status_inner.pack(fill="x", padx=16, pady=12)
        self.status_dot = ctk.CTkLabel(
            status_inner,
            text="●",
            width=18,
            text_color="#9B87F5",
            font=ctk.CTkFont(size=13),
        )
        self.status_dot.pack(side="left")
        status_group = ctk.CTkFrame(status_inner, fg_color="transparent")
        status_group.pack(side="left", fill="x", expand=True, padx=(6, 0))
        ctk.CTkLabel(
            status_group,
            textvariable=self.status,
            text_color="#E8E8EE",
            font=ctk.CTkFont(size=11, weight="bold"),
        ).pack(anchor="w")
        ctk.CTkLabel(
            status_group,
            textvariable=self.backend_text,
            text_color="#81818E",
            font=ctk.CTkFont(size=10),
        ).pack(anchor="w", pady=(2, 0))
        ctk.CTkButton(
            status_inner,
            text="Ocultar",
            width=80,
            height=30,
            corner_radius=10,
            fg_color="#1C1C23",
            hover_color="#292932",
            command=self._hide_main_window,
        ).pack(side="right")

    def _build_dictation_tab(self, tab):
        canvas = ctk.CTkScrollableFrame(
            tab,
            fg_color="#111116",
            corner_radius=0,
        )
        canvas.pack(fill="both", expand=True, padx=4, pady=4)
        tab = canvas

        hero = ctk.CTkFrame(tab, fg_color="#17171D", corner_radius=20, border_width=1, border_color="#2A2A34")
        hero.pack(fill="x", padx=12, pady=(12, 10))
        hero_grid = ctk.CTkFrame(hero, fg_color="transparent")
        hero_grid.pack(fill="x", padx=22, pady=20)

        hotkey_group = ctk.CTkFrame(hero_grid, fg_color="transparent")
        hotkey_group.pack(side="left")
        ctk.CTkLabel(
            hotkey_group,
            text="SEGURE PARA DITAR",
            text_color="#8F8F9D",
            font=ctk.CTkFont(size=10, weight="bold"),
        ).pack(anchor="w")
        hotkey = ctk.CTkFrame(hotkey_group, fg_color="transparent")
        hotkey.pack(anchor="w", pady=(9, 7))
        for label in ["Ctrl", "+", "Espaço"]:
            if label == "+":
                ctk.CTkLabel(hotkey, text=label, text_color="#777785", width=22).pack(side="left")
            else:
                ctk.CTkLabel(
                    hotkey,
                    text=label,
                    height=40,
                    corner_radius=11,
                    fg_color="#7C5CFC",
                    text_color="#FFFFFF",
                    font=ctk.CTkFont(size=14, weight="bold"),
                    padx=14,
                ).pack(side="left")
        ctk.CTkLabel(
            hotkey_group,
            text="Fale e solte Espaço para inserir o texto",
            text_color="#B0B0BC",
            font=ctk.CTkFont(size=11),
        ).pack(anchor="w")

        action_group = ctk.CTkFrame(hero_grid, fg_color="transparent")
        action_group.pack(side="right", anchor="e")
        ctk.CTkButton(
            action_group,
            text="Copiar última transcrição",
            width=190,
            height=40,
            corner_radius=12,
            fg_color="#2B2750",
            hover_color="#373063",
            text_color="#E9E4FF",
            command=self._copy_latest_transcription,
        ).pack()
        ctk.CTkLabel(
            action_group,
            text="Sempre disponível na área de transferência",
            text_color="#72727F",
            font=ctk.CTkFont(size=9),
        ).pack(pady=(7, 0))

        settings = ctk.CTkFrame(tab, fg_color="#17171D", corner_radius=17)
        settings.pack(fill="x", padx=12, pady=8)
        inner = ctk.CTkFrame(settings, fg_color="transparent")
        inner.pack(fill="x", padx=18, pady=16)

        ctk.CTkLabel(
            inner,
            text="Microfone fixo",
            text_color="#DADAE2",
            font=ctk.CTkFont(size=12, weight="bold"),
        ).pack(anchor="w")
        microphone_labels = [self._device_label(device) for device in self.input_devices]
        self.microphone = ctk.CTkComboBox(
            inner,
            values=microphone_labels or ["Nenhum microfone encontrado"],
            command=self._microphone_changed,
            height=38,
            corner_radius=10,
            fg_color="#202027",
            border_color="#30303A",
            button_color="#2B2B35",
            button_hover_color="#383844",
            dropdown_fg_color="#202027",
            text_color="#EFEFF4",
        )
        self.microphone.pack(fill="x", pady=(7, 14))
        self._select_saved_microphone()

        ctk.CTkLabel(
            inner,
            text="Idioma falado",
            text_color="#DADAE2",
            font=ctk.CTkFont(size=12, weight="bold"),
        ).pack(anchor="w")
        self.transcription_language_control = ctk.CTkComboBox(
            inner,
            values=[
                language["display_name"]
                for language in TRANSCRIPTION_LANGUAGES.values()
            ],
            variable=self.transcription_language,
            command=self._transcription_language_changed,
            height=38,
            corner_radius=10,
            fg_color="#202027",
            border_color="#30303A",
            button_color="#2B2B35",
            button_hover_color="#383844",
            dropdown_fg_color="#202027",
            text_color="#EFEFF4",
            state="readonly",
        )
        self.transcription_language_control.pack(fill="x", pady=(7, 14))

        switches = ctk.CTkFrame(inner, fg_color="transparent")
        switches.pack(fill="x")
        self._build_switch_row(
            switches,
            "Corrigir gramática e pontuação com IA local",
            self.grammar_correction,
            self._settings_changed,
        ).pack(fill="x", pady=(0, 8))
        self._build_switch_row(
            switches,
            "Colar automaticamente no aplicativo em foco",
            self.auto_paste,
            self._settings_changed,
        ).pack(fill="x", pady=(0, 8))
        self._build_switch_row(
            switches,
            "Mutar o áudio do computador enquanto falo",
            self.mute_playback_while_recording,
            self._settings_changed,
        ).pack(fill="x", pady=(0, 8))
        self._build_switch_row(
            switches,
            "Guardar também textos copiados em outros aplicativos",
            self.capture_clipboard_history,
            self._settings_changed,
        ).pack(fill="x")

        details = ctk.CTkFrame(tab, fg_color="#121217", corner_radius=15)
        details.pack(fill="x", padx=12, pady=(8, 12))
        ctk.CTkLabel(
            details,
            text="MODO DE TRANSCRIÇÃO",
            text_color="#9B87F5",
            font=ctk.CTkFont(size=10, weight="bold"),
        ).pack(anchor="w", padx=16, pady=(13, 7))
        self.transcription_profile_control = ctk.CTkSegmentedButton(
            details,
            values=[
                TRANSCRIPTION_PROFILES["balanced"]["display_name"],
                TRANSCRIPTION_PROFILES["max_precision"]["display_name"],
            ],
            variable=self.transcription_profile,
            command=self._transcription_profile_changed,
            height=32,
            corner_radius=10,
            fg_color="#202027",
            selected_color="#6F52E5",
            selected_hover_color="#7C5CFC",
            unselected_color="#202027",
            unselected_hover_color="#2B2B35",
            text_color="#EFEFF4",
        )
        self.transcription_profile_control.pack(fill="x", padx=16)
        ctk.CTkLabel(
            details,
            textvariable=self.profile_description_text,
            text_color="#B8B8C3",
            font=ctk.CTkFont(size=11),
        ).pack(anchor="w", padx=16, pady=(7, 13))

    def _build_switch_row(self, parent, label, variable, command):
        row = ctk.CTkFrame(parent, fg_color="transparent")
        ctk.CTkLabel(
            row,
            text=label,
            text_color="#C8C8D2",
            font=ctk.CTkFont(size=11),
        ).pack(side="left")
        ctk.CTkSwitch(
            row,
            text="",
            variable=variable,
            command=command,
            width=42,
            progress_color="#7C5CFC",
            button_color="#FFFFFF",
        ).pack(side="right")
        return row

    def _build_corrections_tab(self, tab):
        ctk.CTkLabel(
            tab,
            text="Ensine as grafias que devem ser usadas",
            text_color="#FFFFFF",
            font=ctk.CTkFont(size=17, weight="bold"),
        ).pack(anchor="w", padx=18, pady=(18, 4))
        ctk.CTkLabel(
            tab,
            text="A versão correta influencia o reconhecimento e substitui a forma errada no texto final.",
            text_color="#9696A3",
            font=ctk.CTkFont(size=11),
        ).pack(anchor="w", padx=18)

        add_card = ctk.CTkFrame(tab, fg_color="#17171D", corner_radius=16)
        add_card.pack(fill="x", padx=18, pady=16)
        form = ctk.CTkFrame(add_card, fg_color="transparent")
        form.pack(fill="x", padx=14, pady=14)
        self.wrong_entry = ctk.CTkEntry(
            form,
            placeholder_text="Como o ditado costuma escrever",
            height=38,
            fg_color="#222229",
            border_color="#33333D",
        )
        self.wrong_entry.pack(side="left", fill="x", expand=True)
        ctk.CTkLabel(form, text="→", width=34, text_color="#777785").pack(side="left")
        self.correct_entry = ctk.CTkEntry(
            form,
            placeholder_text="Grafia correta",
            height=38,
            fg_color="#222229",
            border_color="#33333D",
        )
        self.correct_entry.pack(side="left", fill="x", expand=True)
        ctk.CTkButton(
            form,
            text="Adicionar",
            width=96,
            height=38,
            corner_radius=10,
            fg_color="#7C5CFC",
            hover_color="#8D73FF",
            command=self._add_correction,
        ).pack(side="left", padx=(10, 0))

        self.corrections_frame = ctk.CTkScrollableFrame(
            tab,
            fg_color="#121217",
            corner_radius=15,
            label_text="Dicionário personalizado",
            label_text_color="#D9D9E2",
        )
        self.corrections_frame.pack(fill="both", expand=True, padx=18, pady=(0, 16))
        self._rebuild_corrections()

    def _build_history_tab(self, tab):
        header = ctk.CTkFrame(tab, fg_color="transparent")
        header.pack(fill="x", padx=18, pady=(18, 10))
        title_group = ctk.CTkFrame(header, fg_color="transparent")
        title_group.pack(side="left")
        ctk.CTkLabel(
            title_group,
            text="Histórico local",
            text_color="#FFFFFF",
            font=ctk.CTkFont(size=17, weight="bold"),
        ).pack(anchor="w")
        ctk.CTkLabel(
            title_group,
            text="Transcrições, ações e cópias opcionais protegidas pelo Windows",
            text_color="#858592",
            font=ctk.CTkFont(size=10),
        ).pack(anchor="w", pady=(3, 0))
        ctk.CTkButton(
            header,
            text="Limpar histórico",
            width=112,
            height=34,
            corner_radius=10,
            fg_color="#312026",
            hover_color="#482B35",
            text_color="#FFB3C0",
            command=self._clear_history,
        ).pack(side="right")
        ctk.CTkButton(
            header,
            text="Copiar última",
            width=108,
            height=34,
            corner_radius=10,
            fg_color="#2B2750",
            hover_color="#373063",
            command=self._copy_latest_transcription,
        ).pack(side="right", padx=(0, 8))

        self.history_frame = ctk.CTkScrollableFrame(
            tab,
            fg_color="#121217",
            corner_radius=15,
        )
        self.history_frame.pack(fill="both", expand=True, padx=18, pady=(0, 16))

    def _build_agent_tab(self, tab):
        hero = ctk.CTkFrame(tab, fg_color="#19151C", corner_radius=20, border_width=1, border_color="#34273A")
        hero.pack(fill="x", padx=16, pady=(16, 12))
        ctk.CTkLabel(
            hero,
            text="MODO AGENTE LOCAL",
            text_color="#F0A6FF",
            font=ctk.CTkFont(size=10, weight="bold"),
        ).pack(anchor="w", padx=20, pady=(18, 4))
        ctk.CTkLabel(
            hero,
            text="Selecione um texto e segure Ctrl esquerdo + Alt esquerdo",
            text_color="#FFFFFF",
            font=ctk.CTkFont(size=16, weight="bold"),
        ).pack(anchor="w", padx=20)
        ctk.CTkLabel(
            hero,
            text="Fale o que deseja fazer e solte qualquer uma das teclas. O resultado substitui a seleção e também fica copiado.",
            text_color="#B6ABB9",
            wraplength=650,
            justify="left",
            font=ctk.CTkFont(size=11),
        ).pack(anchor="w", padx=20, pady=(7, 18))

        examples = ctk.CTkFrame(tab, fg_color="#15151B", corner_radius=17)
        examples.pack(fill="x", padx=16, pady=8)
        ctk.CTkLabel(
            examples,
            text="EXEMPLOS DE COMANDO",
            text_color="#8C8C99",
            font=ctk.CTkFont(size=10, weight="bold"),
        ).pack(anchor="w", padx=18, pady=(16, 9))
        for example in [
            "Formate este texto como uma lista clara",
            "Deixe mais profissional e conciso",
            "Traduza para inglês sem alterar os dados",
            "Corrija a gramática mantendo meu tom",
            "Use a skill Resposta profissional",
        ]:
            ctk.CTkLabel(
                examples,
                text=f"•  {example}",
                text_color="#C6C6D0",
                font=ctk.CTkFont(size=11),
            ).pack(anchor="w", padx=18, pady=3)
        ctk.CTkLabel(examples, text="", height=8).pack()

        agent_status = ctk.CTkFrame(tab, fg_color="#111116", corner_radius=15)
        agent_status.pack(fill="x", padx=16, pady=(8, 16))
        ctk.CTkLabel(
            agent_status,
            text="AGENTE",
            text_color="#E0A6EB",
            font=ctk.CTkFont(size=10, weight="bold"),
        ).pack(anchor="w", padx=16, pady=(13, 2))
        ctk.CTkLabel(
            agent_status,
            textvariable=self.agent_status_text,
            text_color="#A5A5B0",
            font=ctk.CTkFont(size=11),
        ).pack(anchor="w", padx=16, pady=(0, 13))
        ctk.CTkLabel(
            agent_status,
            textvariable=self.rules_status_text,
            text_color="#F0ABFC",
            font=ctk.CTkFont(size=10),
        ).pack(anchor="w", padx=16, pady=(0, 6))
        ctk.CTkLabel(
            agent_status,
            textvariable=self.skills_status_text,
            text_color="#C4B5FD",
            font=ctk.CTkFont(size=10),
        ).pack(anchor="w", padx=16, pady=(0, 13))

    def _build_rules_tab(self, tab):
        canvas = ctk.CTkScrollableFrame(
            tab,
            fg_color="#111116",
            corner_radius=0,
        )
        canvas.pack(fill="both", expand=True, padx=8, pady=8)

        ctk.CTkLabel(
            canvas,
            text="Regras permanentes",
            text_color="#FFFFFF",
            font=ctk.CTkFont(size=18, weight="bold"),
        ).pack(anchor="w", padx=10, pady=(8, 3))
        ctk.CTkLabel(
            canvas,
            text=(
                "Defina preferências que devem valer em toda ação do agente. "
                "O resultado passa por uma revisão local para cumprir as regras ativas."
            ),
            text_color="#9696A3",
            wraplength=650,
            justify="left",
            font=ctk.CTkFont(size=11),
        ).pack(anchor="w", padx=10)

        form = ctk.CTkFrame(canvas, fg_color="#17171D", corner_radius=16)
        form.pack(fill="x", padx=10, pady=14)
        self.rule_form_title = ctk.CTkLabel(
            form,
            text="NOVA REGRA",
            text_color="#F0ABFC",
            font=ctk.CTkFont(size=10, weight="bold"),
        )
        self.rule_form_title.pack(anchor="w", padx=14, pady=(13, 7))

        self.rule_name_entry = ctk.CTkEntry(
            form,
            placeholder_text="Nome, por exemplo: Preservar o idioma",
            height=36,
            fg_color="#222229",
            border_color="#33333D",
        )
        self.rule_name_entry.pack(fill="x", padx=14)

        ctk.CTkLabel(
            form,
            text="INSTRUÇÕES DA REGRA",
            text_color="#8C8C99",
            font=ctk.CTkFont(size=9, weight="bold"),
        ).pack(anchor="w", padx=14, pady=(10, 3))
        self.rule_instructions_text = ctk.CTkTextbox(
            form,
            height=100,
            fg_color="#222229",
            border_width=1,
            border_color="#33333D",
            wrap="word",
            font=ctk.CTkFont(size=11),
        )
        self.rule_instructions_text.pack(fill="x", padx=14)

        actions = ctk.CTkFrame(form, fg_color="transparent")
        actions.pack(fill="x", padx=14, pady=12)
        self.cancel_rule_button = ctk.CTkButton(
            actions,
            text="Cancelar edição",
            width=112,
            height=34,
            corner_radius=9,
            fg_color="#282830",
            hover_color="#353540",
            command=self._reset_rule_form,
        )
        self.cancel_rule_button.pack(side="right")
        self.cancel_rule_button.pack_forget()
        ctk.CTkButton(
            actions,
            text="Salvar regra",
            width=106,
            height=34,
            corner_radius=9,
            fg_color="#A855F7",
            hover_color="#C084FC",
            command=self._save_rule,
        ).pack(side="right", padx=(0, 8))

        self.rules_frame = ctk.CTkFrame(canvas, fg_color="transparent")
        self.rules_frame.pack(fill="x", padx=10, pady=(0, 12))
        self._rebuild_rules()

    def _build_skills_tab(self, tab):
        canvas = ctk.CTkScrollableFrame(
            tab,
            fg_color="#111116",
            corner_radius=0,
        )
        canvas.pack(fill="both", expand=True, padx=8, pady=8)

        ctk.CTkLabel(
            canvas,
            text="Skills do agente",
            text_color="#FFFFFF",
            font=ctk.CTkFont(size=18, weight="bold"),
        ).pack(anchor="w", padx=10, pady=(8, 3))
        ctk.CTkLabel(
            canvas,
            text=(
                "Crie comportamentos reutilizáveis. Ative uma skill pelo nome "
                "ou por uma frase cadastrada."
            ),
            text_color="#9696A3",
            wraplength=650,
            justify="left",
            font=ctk.CTkFont(size=11),
        ).pack(anchor="w", padx=10)

        form = ctk.CTkFrame(canvas, fg_color="#17171D", corner_radius=16)
        form.pack(fill="x", padx=10, pady=14)
        self.skill_form_title = ctk.CTkLabel(
            form,
            text="NOVA SKILL",
            text_color="#C4B5FD",
            font=ctk.CTkFont(size=10, weight="bold"),
        )
        self.skill_form_title.pack(anchor="w", padx=14, pady=(13, 7))

        first_row = ctk.CTkFrame(form, fg_color="transparent")
        first_row.pack(fill="x", padx=14)
        self.skill_name_entry = ctk.CTkEntry(
            first_row,
            placeholder_text="Nome, por exemplo: Resposta profissional",
            height=36,
            fg_color="#222229",
            border_color="#33333D",
        )
        self.skill_name_entry.pack(side="left", fill="x", expand=True)
        self.skill_triggers_entry = ctk.CTkEntry(
            first_row,
            placeholder_text="Ativações separadas por vírgula",
            width=250,
            height=36,
            fg_color="#222229",
            border_color="#33333D",
        )
        self.skill_triggers_entry.pack(side="left", padx=(8, 0))

        self.skill_description_entry = ctk.CTkEntry(
            form,
            placeholder_text="Quando esta skill deve ser usada",
            height=36,
            fg_color="#222229",
            border_color="#33333D",
        )
        self.skill_description_entry.pack(fill="x", padx=14, pady=(8, 0))

        ctk.CTkLabel(
            form,
            text="INSTRUÇÕES",
            text_color="#8C8C99",
            font=ctk.CTkFont(size=9, weight="bold"),
        ).pack(anchor="w", padx=14, pady=(10, 3))
        self.skill_instructions_text = ctk.CTkTextbox(
            form,
            height=82,
            fg_color="#222229",
            border_width=1,
            border_color="#33333D",
            wrap="word",
            font=ctk.CTkFont(size=11),
        )
        self.skill_instructions_text.pack(fill="x", padx=14)

        ctk.CTkLabel(
            form,
            text="EXEMPLOS OPCIONAIS, UM POR LINHA",
            text_color="#8C8C99",
            font=ctk.CTkFont(size=9, weight="bold"),
        ).pack(anchor="w", padx=14, pady=(10, 3))
        self.skill_examples_text = ctk.CTkTextbox(
            form,
            height=58,
            fg_color="#222229",
            border_width=1,
            border_color="#33333D",
            wrap="word",
            font=ctk.CTkFont(size=10),
        )
        self.skill_examples_text.pack(fill="x", padx=14)

        actions = ctk.CTkFrame(form, fg_color="transparent")
        actions.pack(fill="x", padx=14, pady=12)
        self.cancel_skill_button = ctk.CTkButton(
            actions,
            text="Cancelar edição",
            width=112,
            height=34,
            corner_radius=9,
            fg_color="#282830",
            hover_color="#353540",
            command=self._reset_skill_form,
        )
        self.cancel_skill_button.pack(side="right")
        self.cancel_skill_button.pack_forget()
        ctk.CTkButton(
            actions,
            text="Salvar skill",
            width=106,
            height=34,
            corner_radius=9,
            fg_color="#7C5CFC",
            hover_color="#8D73FF",
            command=self._save_skill,
        ).pack(side="right", padx=(0, 8))

        self.skills_frame = ctk.CTkFrame(canvas, fg_color="transparent")
        self.skills_frame.pack(fill="x", padx=10, pady=(0, 12))
        self._rebuild_skills()

    def _device_label(self, device):
        return f"{device['index']}: {device['name']}"

    def _select_saved_microphone(self):
        saved_name = self.config.get("microphone_name", "")
        selected = None
        if saved_name:
            selected = next(
                (device for device in self.input_devices if device["name"] == saved_name),
                None,
            )
        if selected is None and self.input_devices:
            selected = self.input_devices[0]
        if selected:
            self.microphone.set(self._device_label(selected))
            if saved_name != selected["name"]:
                self.config.set("microphone_name", selected["name"])

    def _selected_device(self):
        selection = self.microphone.get()
        if not selection or not self.input_devices:
            raise RuntimeError("Selecione um microfone antes de gravar.")
        device_index = int(selection.split(":", 1)[0])
        for device in self.input_devices:
            if device["index"] == device_index:
                return device
        raise RuntimeError("O microfone selecionado não está disponível.")

    def _refresh_audio_devices(self):
        sd.stop()
        terminate = getattr(sd, "_terminate", None)
        initialize = getattr(sd, "_initialize", None)
        if callable(terminate) and callable(initialize):
            terminate()
            initialize()

        self.input_devices = self._get_input_devices()
        microphone_labels = [self._device_label(device) for device in self.input_devices]
        self.microphone.configure(
            values=microphone_labels or ["Nenhum microfone encontrado"]
        )
        self._select_saved_microphone()

    def _start_input_stream(self, device):
        stream = sd.InputStream(
            samplerate=device["sample_rate"],
            device=device["index"],
            channels=1,
            dtype="float32",
            callback=self._capture_audio,
        )
        try:
            stream.start()
        except Exception:
            try:
                stream.close()
            except Exception:
                pass
            raise
        return stream

    def _open_input_stream_with_recovery(self):
        device = self._selected_device()
        try:
            return device, self._start_input_stream(device)
        except sd.PortAudioError:
            self._refresh_audio_devices()
            device = self._selected_device()
            return device, self._start_input_stream(device)

    def _microphone_changed(self, selected_label):
        try:
            device_index = int(selected_label.split(":", 1)[0])
            device = next(item for item in self.input_devices if item["index"] == device_index)
            self.config.set("microphone_name", device["name"])
            self.status.set(f"Microfone fixado: {device['name']}")
        except (ValueError, StopIteration):
            self.status.set("Não foi possível salvar o microfone selecionado.")

    def _settings_changed(self):
        self.config.set("auto_paste", bool(self.auto_paste.get()))
        self.config.set("grammar_correction", bool(self.grammar_correction.get()))
        self.config.set(
            "capture_clipboard_history",
            bool(self.capture_clipboard_history.get()),
        )
        self.config.set(
            "mute_playback_while_recording",
            bool(self.mute_playback_while_recording.get()),
        )

    def _transcription_language_changed(self, selected_label):
        language_id = TRANSCRIPTION_LANGUAGE_IDS_BY_LABEL.get(selected_label)
        if language_id is None:
            language_id = "auto"
            self.transcription_language.set(
                TRANSCRIPTION_LANGUAGES[language_id]["display_name"]
            )
        self.config.set("transcription_language", language_id)
        if language_id == "auto":
            self.status.set("O idioma será detectado automaticamente.")
        else:
            self.status.set(f"Idioma fixado: {selected_label}.")

    def _transcription_profile_changed(self, selected_label):
        current_profile_id = self.config.get("transcription_profile", "balanced")
        current_profile = resolve_transcription_profile(current_profile_id)
        selected_profile_id = TRANSCRIPTION_PROFILE_IDS_BY_LABEL.get(selected_label)
        if selected_profile_id is None:
            self.transcription_profile.set(current_profile["display_name"])
            return
        if self.recording or self.processing:
            self.transcription_profile.set(current_profile["display_name"])
            self.status.set("Aguarde a transcrição atual antes de trocar o modo.")
            return
        if selected_profile_id == current_profile_id:
            return

        selected_profile = resolve_transcription_profile(selected_profile_id)
        self.config.set("transcription_profile", selected_profile_id)
        self.profile_description_text.set(selected_profile["description"])
        self.model_backend = (
            f"Preparando {selected_profile['backend_name']}..."
        )
        self.backend_text.set(self.model_backend)
        self.status.set("Preparando o modo de transcrição selecionado...")
        self.status_dot.configure(text_color="#9B87F5")
        self.ready_badge.configure(text="PREPARANDO", fg_color="#25232D")
        threading.Thread(
            target=self._reload_transcription_model,
            daemon=True,
        ).start()

    def _add_correction(self):
        wrong = self.wrong_entry.get()
        correct = self.correct_entry.get()
        if self.config.add_correction(wrong, correct):
            self.wrong_entry.delete(0, tk.END)
            self.correct_entry.delete(0, tk.END)
            self._rebuild_corrections()
            self.status.set("Correção adicionada ao dicionário.")
        else:
            self.status.set("Preencha a forma errada e a forma correta.")

    def _remove_correction(self, wrong):
        self.config.remove_correction(wrong)
        self._rebuild_corrections()
        self.status.set("Correção removida do dicionário.")

    def _rebuild_corrections(self):
        for child in self.corrections_frame.winfo_children():
            child.destroy()
        corrections = self.config.get("corrections", [])
        if not corrections:
            ctk.CTkLabel(
                self.corrections_frame,
                text="Nenhuma correção personalizada ainda.",
                text_color="#777784",
            ).pack(pady=20)
            return
        for item in corrections:
            row = ctk.CTkFrame(self.corrections_frame, fg_color="#1A1A21", corner_radius=11)
            row.pack(fill="x", pady=4, padx=4)
            ctk.CTkLabel(
                row,
                text=item.get("wrong", ""),
                text_color="#FFB5C1",
                anchor="w",
                width=190,
            ).pack(side="left", padx=(12, 4), pady=9)
            ctk.CTkLabel(row, text="→", text_color="#777784", width=26).pack(side="left")
            ctk.CTkLabel(
                row,
                text=item.get("correct", ""),
                text_color="#B9F6C9",
                anchor="w",
            ).pack(side="left", fill="x", expand=True, pady=9)
            ctk.CTkButton(
                row,
                text="Remover",
                width=72,
                height=28,
                corner_radius=8,
                fg_color="#302127",
                hover_color="#472B34",
                command=lambda wrong=item.get("wrong", ""): self._remove_correction(wrong),
            ).pack(side="right", padx=9)

    def _rules_status(self):
        total = len(self.config.get_rules())
        active = len(self.config.get_rules(enabled_only=True))
        if total == 0:
            return "Nenhuma regra permanente. Adicione preferências na aba Regras."
        if active == 1:
            return "1 regra permanente ativa."
        return f"{active} regras permanentes ativas de {total} cadastradas."

    def _save_rule(self):
        rule_id = self.config.save_rule(
            self.editing_rule_id,
            self.rule_name_entry.get(),
            self.rule_instructions_text.get("1.0", "end-1c"),
        )
        if not rule_id:
            self.status.set("Preencha o nome e as instruções da regra.")
            return
        was_editing = self.editing_rule_id is not None
        self._reset_rule_form()
        self._rebuild_rules()
        self.status.set("Regra atualizada." if was_editing else "Regra criada e ativada.")

    def _reset_rule_form(self):
        self.editing_rule_id = None
        self.rule_form_title.configure(text="NOVA REGRA")
        self.rule_name_entry.delete(0, tk.END)
        self.rule_instructions_text.delete("1.0", tk.END)
        self.cancel_rule_button.pack_forget()

    def _edit_rule(self, rule_id):
        rule = next(
            (
                item
                for item in self.config.get_rules()
                if item.get("id") == rule_id
            ),
            None,
        )
        if not rule:
            return
        self._reset_rule_form()
        self.editing_rule_id = rule_id
        self.rule_form_title.configure(text="EDITANDO REGRA")
        self.rule_name_entry.insert(0, rule.get("name", ""))
        self.rule_instructions_text.insert("1.0", rule.get("instructions", ""))
        self.cancel_rule_button.pack(side="right", padx=(0, 8))
        self.status.set(f"Editando a regra {rule.get('name', '')}.")

    def _remove_rule(self, rule_id):
        self.config.remove_rule(rule_id)
        if self.editing_rule_id == rule_id:
            self._reset_rule_form()
        self._rebuild_rules()
        self.status.set("Regra removida.")

    def _toggle_rule(self, rule_id, enabled):
        self.config.set_rule_enabled(rule_id, bool(enabled))
        self.rules_status_text.set(self._rules_status())
        self.status.set("Regra ativada." if enabled else "Regra pausada.")

    def _rebuild_rules(self):
        for child in self.rules_frame.winfo_children():
            child.destroy()
        rules = self.config.get_rules()
        self.rules_status_text.set(self._rules_status())
        if not rules:
            empty = ctk.CTkFrame(
                self.rules_frame,
                fg_color="#15151B",
                corner_radius=14,
            )
            empty.pack(fill="x")
            ctk.CTkLabel(
                empty,
                text=(
                    "Exemplo: preserve o idioma do texto, exceto quando eu pedir "
                    "explicitamente uma tradução."
                ),
                text_color="#858592",
                wraplength=610,
                justify="left",
                font=ctk.CTkFont(size=11),
            ).pack(anchor="w", padx=14, pady=16)
            return

        for rule in rules:
            card = ctk.CTkFrame(
                self.rules_frame,
                fg_color="#17171D",
                corner_radius=14,
                border_width=1,
                border_color="#292933",
            )
            card.pack(fill="x", pady=5)
            header = ctk.CTkFrame(card, fg_color="transparent")
            header.pack(fill="x", padx=13, pady=(11, 4))
            ctk.CTkLabel(
                header,
                text=rule.get("name", "Regra"),
                text_color="#FFFFFF",
                font=ctk.CTkFont(size=13, weight="bold"),
            ).pack(side="left")
            enabled_variable = tk.BooleanVar(value=rule.get("enabled", True))
            ctk.CTkSwitch(
                header,
                text="Ativa",
                variable=enabled_variable,
                width=62,
                command=lambda rule_id=rule.get("id"), variable=enabled_variable: self._toggle_rule(
                    rule_id,
                    variable.get(),
                ),
            ).pack(side="right")
            ctk.CTkLabel(
                card,
                text=rule.get("instructions", ""),
                text_color="#B7B7C2",
                wraplength=610,
                justify="left",
                font=ctk.CTkFont(size=10),
            ).pack(anchor="w", padx=13)
            actions = ctk.CTkFrame(card, fg_color="transparent")
            actions.pack(fill="x", padx=10, pady=(7, 10))
            ctk.CTkButton(
                actions,
                text="Editar",
                width=66,
                height=28,
                corner_radius=8,
                fg_color="#2B2750",
                hover_color="#3A3464",
                command=lambda rule_id=rule.get("id"): self._edit_rule(rule_id),
            ).pack(side="right")
            ctk.CTkButton(
                actions,
                text="Remover",
                width=74,
                height=28,
                corner_radius=8,
                fg_color="#302127",
                hover_color="#472B34",
                command=lambda rule_id=rule.get("id"): self._remove_rule(rule_id),
            ).pack(side="right", padx=(0, 7))

    def _skills_status(self):
        total = len(self.config.get_skills())
        active = len(self.config.get_skills(enabled_only=True))
        if total == 0:
            return "Nenhuma skill criada. Adicione comportamentos na aba Skills."
        if active == 1:
            return "1 skill ativa para ativação por nome ou frase."
        return f"{active} skills ativas de {total} cadastradas."

    def _save_skill(self):
        triggers = [
            item.strip()
            for item in self.skill_triggers_entry.get().replace(";", ",").split(",")
            if item.strip()
        ]
        examples = [
            item.strip()
            for item in self.skill_examples_text.get("1.0", "end-1c").splitlines()
            if item.strip()
        ]
        skill_id = self.config.save_skill(
            self.editing_skill_id,
            self.skill_name_entry.get(),
            self.skill_description_entry.get(),
            triggers,
            self.skill_instructions_text.get("1.0", "end-1c"),
            examples,
        )
        if not skill_id:
            self.status.set("Preencha nome, quando usar e instruções da skill.")
            return
        was_editing = self.editing_skill_id is not None
        self._reset_skill_form()
        self._rebuild_skills()
        self.status.set("Skill atualizada." if was_editing else "Skill criada e ativada.")

    def _reset_skill_form(self):
        self.editing_skill_id = None
        self.skill_form_title.configure(text="NOVA SKILL")
        self.skill_name_entry.delete(0, tk.END)
        self.skill_triggers_entry.delete(0, tk.END)
        self.skill_description_entry.delete(0, tk.END)
        self.skill_instructions_text.delete("1.0", tk.END)
        self.skill_examples_text.delete("1.0", tk.END)
        self.cancel_skill_button.pack_forget()

    def _edit_skill(self, skill_id):
        skill = next(
            (
                item
                for item in self.config.get_skills()
                if item.get("id") == skill_id
            ),
            None,
        )
        if not skill:
            return
        self._reset_skill_form()
        self.editing_skill_id = skill_id
        self.skill_form_title.configure(text="EDITANDO SKILL")
        self.skill_name_entry.insert(0, skill.get("name", ""))
        self.skill_triggers_entry.insert(0, ", ".join(skill.get("triggers", [])))
        self.skill_description_entry.insert(0, skill.get("description", ""))
        self.skill_instructions_text.insert("1.0", skill.get("instructions", ""))
        self.skill_examples_text.insert("1.0", "\n".join(skill.get("examples", [])))
        self.cancel_skill_button.pack(side="right", padx=(0, 8))
        self.status.set(f"Editando a skill {skill.get('name', '')}.")

    def _remove_skill(self, skill_id):
        self.config.remove_skill(skill_id)
        if self.editing_skill_id == skill_id:
            self._reset_skill_form()
        self._rebuild_skills()
        self.status.set("Skill removida.")

    def _toggle_skill(self, skill_id, enabled):
        self.config.set_skill_enabled(skill_id, bool(enabled))
        self.skills_status_text.set(self._skills_status())
        self.status.set("Skill ativada." if enabled else "Skill pausada.")

    def _rebuild_skills(self):
        for child in self.skills_frame.winfo_children():
            child.destroy()
        skills = self.config.get_skills()
        self.skills_status_text.set(self._skills_status())
        if not skills:
            empty = ctk.CTkFrame(
                self.skills_frame,
                fg_color="#15151B",
                corner_radius=14,
            )
            empty.pack(fill="x")
            ctk.CTkLabel(
                empty,
                text="Sua primeira skill pode ensinar tom, formato ou um fluxo recorrente.",
                text_color="#858592",
                wraplength=610,
                justify="left",
                font=ctk.CTkFont(size=11),
            ).pack(anchor="w", padx=14, pady=16)
            return

        for skill in skills:
            card = ctk.CTkFrame(
                self.skills_frame,
                fg_color="#17171D",
                corner_radius=14,
                border_width=1,
                border_color="#292933",
            )
            card.pack(fill="x", pady=5)
            header = ctk.CTkFrame(card, fg_color="transparent")
            header.pack(fill="x", padx=13, pady=(11, 4))
            ctk.CTkLabel(
                header,
                text=skill.get("name", "Skill"),
                text_color="#FFFFFF",
                font=ctk.CTkFont(size=13, weight="bold"),
            ).pack(side="left")
            enabled_variable = tk.BooleanVar(value=skill.get("enabled", True))
            ctk.CTkSwitch(
                header,
                text="Ativa",
                variable=enabled_variable,
                width=62,
                command=lambda skill_id=skill.get("id"), variable=enabled_variable: self._toggle_skill(
                    skill_id,
                    variable.get(),
                ),
            ).pack(side="right")
            ctk.CTkLabel(
                card,
                text=skill.get("description", ""),
                text_color="#B7B7C2",
                wraplength=610,
                justify="left",
                font=ctk.CTkFont(size=10),
            ).pack(anchor="w", padx=13)
            triggers = ", ".join(skill.get("triggers", []))
            if triggers:
                ctk.CTkLabel(
                    card,
                    text=f"Ativação: {triggers}",
                    text_color="#A78BFA",
                    wraplength=610,
                    justify="left",
                    font=ctk.CTkFont(size=9),
                ).pack(anchor="w", padx=13, pady=(5, 0))
            actions = ctk.CTkFrame(card, fg_color="transparent")
            actions.pack(fill="x", padx=10, pady=(7, 10))
            ctk.CTkButton(
                actions,
                text="Editar",
                width=66,
                height=28,
                corner_radius=8,
                fg_color="#2B2750",
                hover_color="#3A3464",
                command=lambda skill_id=skill.get("id"): self._edit_skill(skill_id),
            ).pack(side="right")
            ctk.CTkButton(
                actions,
                text="Remover",
                width=74,
                height=28,
                corner_radius=8,
                fg_color="#302127",
                hover_color="#472B34",
                command=lambda skill_id=skill.get("id"): self._remove_skill(skill_id),
            ).pack(side="right", padx=(0, 7))

    def _on_key_press(self, key):
        if time.monotonic() < self.suppress_hotkeys_until:
            return
        self.keys_down.add(key)
        ctrl_down = bool(self.keys_down & self.CTRL_KEYS)
        left_agent_down = self.LEFT_AGENT_KEYS.issubset(self.keys_down)

        if key == pynput_keyboard.Key.space and ctrl_down and not left_agent_down:
            if not self.dictation_chord_active and not self.agent_chord_active:
                self.dictation_chord_active = True
                self.events.put(("start", "dictation"))
        elif left_agent_down and not self.agent_chord_active and not self.dictation_chord_active:
            self.agent_chord_active = True
            self.events.put(("start", "agent"))

    def _on_key_release(self, key):
        if time.monotonic() < self.suppress_hotkeys_until:
            return
        if self.dictation_chord_active and key == pynput_keyboard.Key.space:
            self.dictation_chord_active = False
            self.events.put(("stop", None))
        if self.agent_chord_active and key in self.LEFT_AGENT_KEYS:
            self.agent_chord_active = False
            self.events.put(("stop", None))
        self.keys_down.discard(key)

    def start_recording(self, mode):
        if self.recording or self.processing:
            return
        try:
            self.target_window = ctypes.windll.user32.GetForegroundWindow()
            self.recording_mode = mode
            self.audio_chunks = []
            self.current_level = 0.0
            device, self.stream = self._open_input_stream_with_recovery()
            self.input_sample_rate = device["sample_rate"]
            self.agent_selected_text = ""
            self.selection_ready = threading.Event()
            self.recording = True
            if bool(self.mute_playback_while_recording.get()):
                self.playback_mute.mute_for_recording()

            if mode == "agent":
                self.status.set("Modo agente: ouvindo sua instrução...")
                self.status_dot.configure(text_color="#E879F9")
                self.ready_badge.configure(text="AGENTE", fg_color="#3C2045")
                self.overlay.show(
                    "agent_recording",
                    "Agente ouvindo...",
                    "Solte Ctrl ou Alt para executar",
                    "#E879F9",
                )
                threading.Thread(target=self._capture_selected_text, daemon=True).start()
            else:
                self.status.set("Ouvindo sua voz...")
                self.status_dot.configure(text_color="#A78BFA")
                self.ready_badge.configure(text="OUVINDO", fg_color="#2D2347")
                self.overlay.show(
                    "recording",
                    "Ouvindo...",
                    "Solte Espaço para transcrever",
                    "#A78BFA",
                )
            self._restore_target_window()
        except Exception as error:
            self.recording = False
            self.playback_mute.restore()
            self.stream = None
            self._show_error(f"Não foi possível acessar o microfone: {error}")

    def _capture_audio(self, indata, _frames, _time_info, _status):
        channel = indata[:, 0].copy()
        self.audio_chunks.append(channel)
        rms = float(np.sqrt(np.mean(np.square(channel)))) if channel.size else 0.0
        self.current_level = min(1.0, rms * 12.0)

    def _capture_selected_text(self):
        previous_clipboard = self._read_clipboard_text()
        sentinel = f"__DITADO_SELECTION_{uuid.uuid4()}__"
        try:
            self.ignore_clipboard_until = time.monotonic() + 1.2
            pyperclip.copy(sentinel)
            self.suppress_hotkeys_until = time.monotonic() + 0.45
            self._restore_target_window()
            self.keyboard_controller.release(pynput_keyboard.Key.alt_l)
            time.sleep(0.04)
            self.keyboard_controller.press("c")
            self.keyboard_controller.release("c")
            time.sleep(0.16)
            copied = self._read_clipboard_text()
            if self.agent_chord_active:
                self.keyboard_controller.press(pynput_keyboard.Key.alt_l)

            if copied and copied != sentinel:
                self.agent_selected_text = copied
                self.history.add(copied, "selection")
                self.last_clipboard_text = copied
                self.history_dirty = True
            else:
                self.agent_selected_text = ""
                pyperclip.copy(previous_clipboard)
                self.last_clipboard_text = previous_clipboard
        except Exception:
            self.agent_selected_text = ""
            if previous_clipboard:
                pyperclip.copy(previous_clipboard)
                self.last_clipboard_text = previous_clipboard
        finally:
            self.selection_ready.set()

    def stop_recording(self):
        if not self.recording:
            return
        self.recording = False
        self.playback_mute.restore()
        self.processing = True
        stream = self.stream
        self.stream = None
        try:
            stream.stop()
            stream.close()
            audio = np.concatenate(self.audio_chunks) if self.audio_chunks else np.array([], dtype=np.float32)
        except Exception as error:
            self.processing = False
            self._show_error(f"Não foi possível concluir a gravação: {error}")
            return

        if len(audio) < self.input_sample_rate // 4:
            self.processing = False
            self._show_error("Gravação muito curta. Segure o atalho por mais tempo.")
            return

        if self.recording_mode == "agent":
            self.status.set("Entendendo sua instrução...")
            self.status_dot.configure(text_color="#F0A6FF")
            self.ready_badge.configure(text="AGINDO", fg_color="#3C2045")
            self.overlay.show(
                "agent_processing",
                "Entendendo o pedido...",
                "Mantenha o texto selecionado",
                "#E879F9",
            )
        else:
            profile = resolve_transcription_profile(
                self.config.get("transcription_profile", "balanced")
            )
            self.status.set("Transformando sua fala em texto...")
            self.status_dot.configure(text_color="#60A5FA")
            self.ready_badge.configure(text="PROCESSANDO", fg_color="#172B46")
            self.overlay.show(
                "processing",
                "Transcrevendo...",
                f"Usando {profile['backend_name']}",
                "#60A5FA",
            )
        threading.Thread(
            target=self._transcribe_and_process,
            args=(audio, self.recording_mode),
            daemon=True,
        ).start()

    def _resample_to_16khz(self, audio):
        if self.input_sample_rate == SAMPLE_RATE:
            return audio.astype(np.float32, copy=False)
        return soxr.resample(
            audio,
            self.input_sample_rate,
            SAMPLE_RATE,
            quality="HQ",
        ).astype(np.float32, copy=False)

    def _start_model_preload(self):
        if self.closing:
            return
        threading.Thread(target=self._preload_models, daemon=True).start()

    def _preload_models(self):
        try:
            self._ensure_whisper_model()
            silence = np.zeros(SAMPLE_RATE // 2, dtype=np.float32)
            warmup_language = (
                resolve_transcription_language(
                    self.config.get("transcription_language", "auto")
                )
                or "en"
            )
            segments, _ = self.model.transcribe(
                silence,
                language=warmup_language,
                beam_size=1,
            )
            list(segments)
            self.events.put(("status", "Whisper pronto. Preparando revisão local..."))
        except Exception as error:
            self.events.put(("error", f"Não foi possível preparar o Whisper: {error}"))
            return

        last_agent_error = None
        for _attempt in range(15):
            try:
                self.ollama.warm_up()
                self.agent_backend = f"{self.ollama.model}  •  Ollama local  •  pronto"
                self.events.put(("model_ready", None))
                return
            except Exception as error:
                last_agent_error = error
                time.sleep(2)

        self.agent_backend = f"Agente aguardando Ollama: {last_agent_error}"
        self.events.put(("model_ready", None))

    def _reload_transcription_model(self):
        try:
            self._ensure_whisper_model()
            silence = np.zeros(SAMPLE_RATE // 2, dtype=np.float32)
            warmup_language = (
                resolve_transcription_language(
                    self.config.get("transcription_language", "auto")
                )
                or "en"
            )
            segments, _ = self.model.transcribe(
                silence,
                language=warmup_language,
                beam_size=1,
            )
            list(segments)
            self.events.put(("model_ready", None))
        except Exception as error:
            self.events.put(("error", f"Não foi possível trocar o modo: {error}"))

    def _ensure_whisper_model(self):
        profile_id = self.config.get("transcription_profile", "balanced")
        profile = resolve_transcription_profile(profile_id)
        if self.model is not None and self.model_profile_id == profile_id:
            return
        with self.model_lock:
            profile_id = self.config.get("transcription_profile", "balanced")
            profile = resolve_transcription_profile(profile_id)
            if self.model is not None and self.model_profile_id == profile_id:
                return
            previous_model = self.model
            self.model = None
            self.model_profile_id = None
            if previous_model is not None:
                del previous_model
                gc.collect()
            gpu_error = "bibliotecas CUDA 12 e cuDNN 8 não encontradas"
            if is_cuda_runtime_available():
                try:
                    self.model = WhisperModel(
                        profile["model"],
                        device="cuda",
                        compute_type="float16",
                        num_workers=1,
                    )
                    self.model_profile_id = profile_id
                    self.model_backend = (
                        f"{profile['backend_name']}  •  CUDA  •  GPU"
                    )
                    return
                except Exception as error:
                    gpu_error = str(error)

            self.model = WhisperModel(
                "small",
                device="cpu",
                compute_type="int8",
                cpu_threads=min(8, os.cpu_count() or 4),
                num_workers=1,
            )
            self.model_profile_id = profile_id
            self.model_backend = (
                f"Whisper Small  •  CPU  •  GPU indisponível: {gpu_error}"
            )

    def _transcribe_and_process(self, audio, mode):
        started_at = time.perf_counter()
        try:
            self._ensure_whisper_model()
            corrections = self.config.get("corrections", [])
            resampled_audio = self._resample_to_16khz(audio)
            profile = resolve_transcription_profile(
                self.config.get("transcription_profile", "balanced")
            )
            transcription_language = resolve_transcription_language(
                self.config.get("transcription_language", "auto")
            )
            segments, _info = self.model.transcribe(
                resampled_audio,
                language=transcription_language,
                beam_size=profile["beam_size"],
                temperature=0,
                vad_filter=True,
                vad_parameters={"min_silence_duration_ms": 250},
                condition_on_previous_text=False,
                initial_prompt=correction_prompt(corrections),
            )
            spoken_text = " ".join(segment.text.strip() for segment in segments).strip()
            spoken_text = apply_custom_corrections(spoken_text, corrections)
            if not spoken_text:
                raise RuntimeError("Não detectei fala. Verifique o microfone e tente novamente.")

            if mode == "agent":
                self.selection_ready.wait(timeout=2.0)
                if not self.agent_selected_text:
                    raise RuntimeError("Selecione um texto antes de usar Ctrl + Alt.")
                rules = self.config.get_rules(enabled_only=True)
                skills = self.config.get_skills(enabled_only=True)
                selected_skill = select_voice_skill(spoken_text, skills)
                if selected_skill:
                    pipeline_subtitle = f"Skill: {selected_skill.get('name', 'ativa')}"
                elif skills:
                    pipeline_subtitle = "Nenhuma skill ativada; aplicando sua instrução"
                else:
                    pipeline_subtitle = "Aplicando sua instrução localmente"
                self.events.put(
                    (
                        "pipeline_status",
                        ("Agindo sobre a seleção...", pipeline_subtitle, "#E879F9"),
                    )
                )
                final_text = self.ollama.transform_selected_text(
                    self.agent_selected_text,
                    spoken_text,
                    skills=skills,
                    selected_skill=selected_skill,
                    rules=rules,
                )
                final_text = apply_custom_corrections(final_text, corrections)
            else:
                final_text = spoken_text
                if bool(self.config.get("grammar_correction", True)):
                    self.events.put(
                        (
                            "pipeline_status",
                            ("Revisando o texto...", "Corrigindo gramática sem mudar o sentido", "#60A5FA"),
                        )
                    )
                    try:
                        final_text = self.ollama.correct_grammar(final_text)
                        final_text = apply_custom_corrections(final_text, corrections)
                    except Exception:
                        final_text = spoken_text

            elapsed = time.perf_counter() - started_at
            self.events.put(("finish", (final_text.strip(), elapsed, mode)))
        except Exception as error:
            self.events.put(("error", str(error)))

    def _finish_result(self, payload):
        text, elapsed, mode = payload
        self.processing = False
        source = "agent" if mode == "agent" else "transcription"
        self._copy_text(text, source)
        self.status.set(f"Pronto em {elapsed:.1f} s. O resultado está copiado.")
        self.backend_text.set(self.model_backend)
        self.status_dot.configure(text_color="#4ADE80")
        self.ready_badge.configure(text="PRONTO", fg_color="#173727")
        self.overlay.show(
            "success",
            "Resultado pronto",
            f"Copiado em {elapsed:.1f} s",
            "#4ADE80",
        )

        should_paste = mode == "agent" or bool(self.config.get("auto_paste", True))
        if should_paste:
            self._restore_target_window()
            self.root.after(150, self._paste_into_active_app)
        self.root.after(1200, self.overlay.hide)

    def _copy_text(self, text, source):
        pyperclip.copy(text)
        self.last_clipboard_text = text
        self.ignore_clipboard_until = time.monotonic() + 0.8
        self.history.add(text, source)
        self.history_dirty = True

    def _copy_latest_transcription(self):
        latest = self.history.latest_transcription()
        if not latest:
            self.status.set("Ainda não existe uma transcrição no histórico.")
            return
        self._copy_text(latest, "transcription")
        self.status.set("Última transcrição copiada novamente.")

    def _copy_history_item(self, text):
        self._copy_text(text, "clipboard")
        self.status.set("Item do histórico copiado.")

    def _clear_history(self):
        self.history.clear()
        self.history_dirty = True
        self.status.set("Histórico local apagado.")
        self._rebuild_history()

    def _read_clipboard_text(self):
        try:
            value = pyperclip.paste()
            return value if isinstance(value, str) else ""
        except Exception:
            return ""

    def _poll_clipboard(self):
        if self.closing:
            return
        current = self._read_clipboard_text()
        if not bool(self.capture_clipboard_history.get()):
            self.last_clipboard_text = current
            self.root.after(450, self._poll_clipboard)
            return
        if (
            current
            and current != self.last_clipboard_text
            and time.monotonic() >= self.ignore_clipboard_until
        ):
            self.last_clipboard_text = current
            self.history.add(current, "clipboard")
            self.history_dirty = True
        self.root.after(450, self._poll_clipboard)

    def _refresh_lazy_views(self):
        if self.closing:
            return
        if self.history_dirty and self.tabs.get() == "Histórico":
            self._rebuild_history()
        self.root.after(700, self._refresh_lazy_views)

    def _rebuild_history(self):
        self.history_dirty = False
        for child in self.history_frame.winfo_children():
            child.destroy()
        entries = self.history.all()
        if not entries:
            ctk.CTkLabel(
                self.history_frame,
                text="O histórico está vazio. Tudo que você copiar aparecerá aqui.",
                text_color="#777784",
            ).pack(pady=24)
            return
        source_names = {
            "transcription": "Transcrição",
            "agent": "Agente",
            "clipboard": "Copiado",
            "selection": "Seleção",
        }
        for entry in entries:
            card = ctk.CTkFrame(self.history_frame, fg_color="#1A1A21", corner_radius=12)
            card.pack(fill="x", pady=5, padx=4)
            body = ctk.CTkFrame(card, fg_color="transparent")
            body.pack(side="left", fill="both", expand=True, padx=12, pady=9)
            metadata = f"{source_names.get(entry.get('source'), 'Copiado')}  •  {entry.get('timestamp', '').replace('T', ' ')}"
            ctk.CTkLabel(
                body,
                text=metadata,
                text_color="#898996",
                font=ctk.CTkFont(size=9),
            ).pack(anchor="w")
            text = entry.get("text", "")
            preview = text if len(text) <= 280 else text[:277] + "..."
            ctk.CTkLabel(
                body,
                text=preview,
                text_color="#E0E0E7",
                wraplength=520,
                justify="left",
                anchor="w",
                font=ctk.CTkFont(size=10),
            ).pack(anchor="w", pady=(4, 0))
            ctk.CTkButton(
                card,
                text="Copiar",
                width=70,
                height=30,
                corner_radius=9,
                fg_color="#2B2750",
                hover_color="#3A3464",
                command=lambda text=text: self._copy_history_item(text),
            ).pack(side="right", padx=10)

    def _restore_target_window(self):
        if self.target_window and ctypes.windll.user32.IsWindow(self.target_window):
            try:
                ctypes.windll.user32.SetForegroundWindow(self.target_window)
            except Exception:
                pass

    def _paste_into_active_app(self):
        try:
            self._restore_target_window()
            self.suppress_hotkeys_until = time.monotonic() + 0.3
            self.keyboard_controller.press(pynput_keyboard.Key.ctrl)
            self.keyboard_controller.press("v")
            self.keyboard_controller.release("v")
            self.keyboard_controller.release(pynput_keyboard.Key.ctrl)
        except Exception as error:
            self._show_error(f"O resultado foi copiado, mas não consegui colar: {error}")

    def _show_error(self, message):
        self.recording = False
        self.processing = False
        self.status.set(message)
        self.status_dot.configure(text_color="#FB7185")
        self.ready_badge.configure(text="ATENÇÃO", fg_color="#47202A")
        self.overlay.show("error", "Algo não funcionou", message[:48], "#FB7185")
        self.root.after(2800, self.overlay.hide)

    def _process_events(self):
        if ctypes.windll.kernel32.WaitForSingleObject(SHOW_EVENT_HANDLE, 0) == WAIT_OBJECT_0:
            ctypes.windll.kernel32.ResetEvent(SHOW_EVENT_HANDLE)
            self._show_main_window()
        while True:
            try:
                event, payload = self.events.get_nowait()
            except queue.Empty:
                break
            if event == "start":
                self.start_recording(payload)
            elif event == "stop":
                self.stop_recording()
            elif event == "status":
                self.status.set(payload)
            elif event == "pipeline_status":
                title, subtitle, color = payload
                self.status.set(title)
                self.overlay.show("processing", title, subtitle, color)
            elif event == "model_ready":
                self.backend_text.set(self.model_backend)
                self.agent_status_text.set(self.agent_backend)
                self.status.set("Pronto. Use Ctrl + Espaço para ditar.")
                self.status_dot.configure(text_color="#4ADE80")
                self.ready_badge.configure(text="PRONTO", fg_color="#173727")
            elif event == "finish":
                self._finish_result(payload)
            elif event == "error":
                self._show_error(payload)
            elif event == "show_window":
                self._show_main_window()
            elif event == "copy_latest":
                self._copy_latest_transcription()
            elif event == "exit":
                self._exit_app()
        if self.recording:
            self.overlay.set_level(self.current_level)
        if not self.closing:
            self.root.after(50, self._process_events)

    def _create_tray_icon(self):
        icon_image = Image.new("RGBA", (64, 64), "#111116")
        draw = ImageDraw.Draw(icon_image)
        draw.rounded_rectangle((6, 6, 58, 58), radius=18, fill="#7C5CFC")
        draw.rounded_rectangle((27, 16, 37, 39), radius=5, fill="#FFFFFF")
        draw.arc((19, 24, 45, 48), 0, 180, fill="#FFFFFF", width=4)
        draw.line((32, 47, 32, 54), fill="#FFFFFF", width=4)
        menu = pystray.Menu(
            pystray.MenuItem(
                "Abrir Ditado local",
                lambda _icon, _item: self.events.put(("show_window", None)),
            ),
            pystray.MenuItem(
                "Copiar última transcrição",
                lambda _icon, _item: self.events.put(("copy_latest", None)),
            ),
            pystray.MenuItem(
                "Encerrar",
                lambda _icon, _item: self.events.put(("exit", None)),
            ),
        )
        return pystray.Icon(
            "ditado_local",
            icon_image,
            "Ditado local: Ctrl + Espaço para falar",
            menu,
        )

    def _hide_main_window(self):
        self.root.withdraw()

    def _show_main_window(self):
        self.root.deiconify()
        self.root.lift()
        self.root.attributes("-topmost", True)
        self.root.focus_force()
        self.root.after(300, lambda: self.root.attributes("-topmost", False))

    def _exit_app(self):
        self.closing = True
        self.playback_mute.restore()
        if self.stream is not None:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception:
                pass
        try:
            self.listener.stop()
        except Exception:
            pass
        try:
            self.tray_icon.stop()
        except Exception:
            pass
        self.root.destroy()

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    INSTANCE_MUTEX, SHOW_EVENT_HANDLE = ensure_single_instance()
    DitadoLocalApp().run()
