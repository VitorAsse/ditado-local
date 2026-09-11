"""Dedicated Windows registration for the agent chat shortcut.

Uses RegisterHotKey/WM_HOTKEY independently of the dictation keyboard hook.
https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-registerhotkey
"""
import ctypes
import threading
from dataclasses import dataclass
from ctypes import wintypes


DEFAULT_CHAT_SHORTCUT = "Ctrl + Windows"
CHAT_SHORTCUT_CHOICES = [DEFAULT_CHAT_SHORTCUT, "Ctrl + Shift + Enter", "Ctrl + Alt + Enter", "F8", "F9"]


@dataclass(frozen=True)
class HotkeySpec:
    label: str
    bindings: tuple


def parse_chat_shortcut(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Informe um atalho, como Ctrl + Windows ou Ctrl + Shift + Enter.")
    aliases = {"ctrl": "Ctrl", "control": "Ctrl", "alt": "Alt", "shift": "Shift",
               "win": "Windows", "windows": "Windows"}
    masks = {"Ctrl": 2, "Alt": 1, "Shift": 4, "Windows": 8}
    modifiers, key = set(), None
    names = {"enter": ("Enter", 0x0D), "return": ("Enter", 0x0D),
             "space": ("Espaço", 0x20), "espaço": ("Espaço", 0x20),
             "tab": ("Tab", 0x09), "home": ("Home", 0x24), "end": ("End", 0x23),
             "insert": ("Insert", 0x2D), "delete": ("Delete", 0x2E)}
    for part in value.split("+"):
        token = part.strip().casefold()
        if token in aliases:
            modifier = aliases[token]
            if modifier in modifiers:
                raise ValueError("Não repita a mesma tecla no atalho.")
            modifiers.add(modifier)
            continue
        if key is not None:
            raise ValueError("Use os modificadores e uma única tecla, como Ctrl + Shift + Enter.")
        if token in names:
            key = names[token]
        elif len(token) == 1 and token.isascii() and token.isalnum():
            key = (token.upper(), ord(token.upper()))
        elif token.startswith("f") and token[1:].isdigit() and 1 <= int(token[1:]) <= 24:
            number = int(token[1:])
            if number == 12:
                raise ValueError("F12 é reservado pelo Windows. Escolha outra tecla.")
            key = (f"F{number}", 0x6F + number)
        else:
            raise ValueError("Atalho não reconhecido. Use Ctrl, Alt, Shift, Windows e uma letra, número, Enter ou tecla F.")
    if key is None:
        if modifiers == {"Ctrl", "Windows"}:
            # Windows reports either Win key, but reports either Ctrl as VK_CONTROL.
            # Include both modifiers even when one of them is also the virtual key.
            return HotkeySpec(DEFAULT_CHAT_SHORTCUT, ((10, 0x5B), (10, 0x5C), (10, 0x11)))
        raise ValueError("Use Ctrl + Windows ou acrescente uma tecla. Ctrl + Alt já é usado pelo agente por voz.")
    mask = sum(masks[m] for m in modifiers)
    if (key[1] == 0x20 and "Ctrl" in modifiers and "Shift" not in modifiers):
        raise ValueError("Ctrl + Espaço já é usado pelo ditado. Escolha outro atalho.")
    if (modifiers == {"Ctrl", "Alt"} and key[1] == 0x2E) or ("Windows" in modifiers and key[0] == "L"):
        raise ValueError("Essa combinação é reservada pelo Windows. Escolha outra.")
    if not modifiers and not 0x70 <= key[1] <= 0x87:
        raise ValueError("Para letras e teclas de edição, inclua Ctrl, Alt, Shift ou Windows.")
    label = " + ".join([m for m in masks if m in modifiers] + [key[0]])
    return HotkeySpec(label, ((mask, key[1]),))


class AgentChatHotkey:
    HOTKEY_ID = 0x444C
    WM_HOTKEY = 0x0312
    WM_QUIT = 0x0012

    def __init__(self, on_press, on_status, shortcut=DEFAULT_CHAT_SHORTCUT):
        self.spec = parse_chat_shortcut(shortcut)
        self.error = ""
        self.on_press = on_press
        self.on_status = on_status
        self.ready = threading.Event()
        self.stopping = threading.Event()
        self.registered = False
        self.registered_ids = []
        self.thread_id = None
        self.thread = None
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel32.GetCurrentThreadId.restype = wintypes.DWORD
        self.user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
        self.user32.RegisterHotKey.restype = wintypes.BOOL
        self.user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
        self.user32.UnregisterHotKey.restype = wintypes.BOOL
        self.user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
        self.user32.GetMessageW.restype = ctypes.c_int
        self.user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        self.user32.PostThreadMessageW.restype = wintypes.BOOL

    def start(self):
        if self.thread is None:
            self.thread = threading.Thread(target=self._run, daemon=True, name="agent-chat-hotkey")
            self.thread.start()

    def _run(self):
        try:
            self.thread_id = self.kernel32.GetCurrentThreadId()
            if self.stopping.is_set():
                return
            for offset, (modifiers, virtual_key) in enumerate(self.spec.bindings):
                identifier = self.HOTKEY_ID + offset
                if not self.user32.RegisterHotKey(None, identifier, modifiers | 0x4000, virtual_key):
                    code = ctypes.get_last_error()
                    detail = "já está em uso por outro aplicativo" if code == 1409 else f"não pôde ser registrado no Windows (erro {code})"
                    self.error = f"{self.spec.label} {detail}. Escolha outro atalho na aba Agente."
                    self.on_status(False, self.error)
                    return
                self.registered_ids.append(identifier)
            self.registered = True
            self.on_status(True, f"{self.spec.label} abre o Agente, com ou sem texto selecionado.")
            self.ready.set()
            message = wintypes.MSG()
            while not self.stopping.is_set():
                result = self.user32.GetMessageW(ctypes.byref(message), None, 0, 0)
                if result == 0:
                    break
                if result == -1:
                    raise ctypes.WinError(ctypes.get_last_error())
                if message.message == self.WM_HOTKEY and message.wParam in self.registered_ids:
                    if not self.stopping.is_set():
                        self.on_press()
        except Exception:
            if not self.stopping.is_set():
                self.error = f"O atalho {self.spec.label} parou de responder. Abra o Agente pelo menu na bandeja e reinicie o Ditado Local."
                self.on_status(False, self.error)
        finally:
            for identifier in self.registered_ids:
                self.user32.UnregisterHotKey(None, identifier)
            self.registered_ids.clear()
            self.registered = False
            self.ready.set()

    def stop(self):
        self.stopping.set()
        self.ready.wait(timeout=1)
        if self.thread_id:
            self.user32.PostThreadMessageW(self.thread_id, self.WM_QUIT, 0, 0)
        if self.thread and self.thread is not threading.current_thread():
            self.thread.join(timeout=1)
