import ctypes
import tkinter as tk
from ctypes import wintypes

from ditado_theme import APP_COLORS, app_font


class MonitorInfo(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]


class ResultNotification:
    """Non-activating bottom-right notice, above the active monitor's taskbar."""
    def __init__(self, root):
        self.root = root
        self.timer = None
        self.window = tk.Toplevel(root)
        self.window.withdraw()
        self.window.overrideredirect(True)
        self.window.attributes("-topmost", True)
        self.window.configure(bg=APP_COLORS["surface_deep"])
        self.title = tk.Label(self.window, bg=APP_COLORS["surface_deep"], fg=APP_COLORS["success"],
                              anchor="w", font=app_font(12, "bold"))
        self.title.pack(fill="x", padx=16, pady=(14, 4))
        self.body = tk.Label(self.window, bg=APP_COLORS["surface_deep"], fg=APP_COLORS["text"],
                             anchor="w", justify="left", wraplength=300, font=app_font(11))
        self.body.pack(fill="x", padx=16, pady=(0, 14))
        for widget in (self.window, self.title, self.body):
            widget.bind("<Button-1>", lambda _event: self.hide())
        self.window.update_idletasks()
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.user32.GetForegroundWindow.restype = wintypes.HWND
        self.user32.GetParent.argtypes = [wintypes.HWND]
        self.user32.GetParent.restype = wintypes.HWND
        self.user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
        self.user32.MonitorFromWindow.restype = wintypes.HANDLE
        self.user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MonitorInfo)]
        self.user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
        self.user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.LONG]
        self.user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int,
                                          ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
        self.hwnd = self.user32.GetParent(self.window.winfo_id())
        style = self.user32.GetWindowLongW(self.hwnd, -20)
        self.user32.SetWindowLongW(self.hwnd, -20, style | 0x08000000 | 0x00000080)

    def show(self, mode, clipboard_ready=True):
        if self.timer:
            self.root.after_cancel(self.timer)
        self.title.configure(text="Agente: resultado pronto" if mode == "agent" else "Transcrição pronta")
        self.body.configure(text=("Está na área de transferência.\nUse Ctrl + V quando quiser."
                                  if clipboard_ready else "Disponível no Histórico do Ditado Local.\nA área de transferência foi alterada."))
        self.window.update_idletasks()
        width, height = 340, max(108, self.window.winfo_reqheight())
        monitor = self.user32.MonitorFromWindow(self.user32.GetForegroundWindow(), 2)
        info = MonitorInfo(cbSize=ctypes.sizeof(MonitorInfo))
        if self.user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            x, y = info.rcWork.right - width - 16, info.rcWork.bottom - height - 16
        else:
            x, y = self.window.winfo_screenwidth() - width - 16, self.window.winfo_screenheight() - height - 64
        self.window.geometry(f"{width}x{height}")
        # SWP_NOACTIVATE | SWP_SHOWWINDOW: do not steal the user's typing focus.
        self.user32.SetWindowPos(self.hwnd, wintypes.HWND(-1), x, y, width, height, 0x0010 | 0x0040)
        self.timer = self.root.after(8000, self.hide)

    def hide(self):
        if self.timer:
            self.root.after_cancel(self.timer)
            self.timer = None
        self.window.withdraw()
