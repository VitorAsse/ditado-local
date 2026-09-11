"""Explicit mouse gesture and small correction dialog; no background text capture."""
import ctypes
from ctypes import wintypes
import customtkinter as ctk
from pynput import mouse
from ditado_notification import MonitorInfo


DEFAULT_CORRECTION_GESTURE = 'Ctrl + clique direito'
CORRECTION_GESTURES = {
    DEFAULT_CORRECTION_GESTURE: frozenset({'Ctrl'}),
    'Shift + clique direito': frozenset({'Shift'}),
    'Alt + clique direito': frozenset({'Alt'}),
    'Ctrl + Shift + clique direito': frozenset({'Ctrl', 'Shift'}),
    'Desativado': frozenset(),
}


class CorrectionGesture:
    def __init__(self, preference, on_request):
        self.preference, self.on_request = preference, on_request
        self.user32 = ctypes.WinDLL('user32', use_last_error=True)
        self.armed = False
        self.listener = mouse.Listener(win32_event_filter=self._filter)

    def _filter(self, message, data):
        if message == 0x0205 and self.armed:
            self.armed = False
            self.listener.suppress_event()
        if message != 0x0204:
            return True
        expected = CORRECTION_GESTURES.get(self.preference(), frozenset())
        pressed = frozenset(name for name, keys in {
            'Ctrl': (0xA2, 0xA3), 'Shift': (0xA0, 0xA1),
            'Alt': (0xA4, 0xA5), 'Windows': (0x5B, 0x5C),
        }.items() if any(self.user32.GetAsyncKeyState(key) & 0x8000 for key in keys))
        if expected and pressed == expected:
            try:
                accepted = self.on_request(data.pt.x, data.pt.y)
            except Exception:
                return True
            if accepted:
                self.armed = True
                self.listener.suppress_event()
        return True

    def start(self):
        self.listener.start()

    def stop(self):
        self.listener.stop()


class QuickCorrectionDialog:
    def __init__(self, root, text, position, on_save, hint=''):
        self.on_save = on_save
        self.saved = False
        self._focus_timer = None
        self._dismiss_timer = None
        self.window = ctk.CTkToplevel(root)
        self.window.withdraw()
        self.window.title('Adicionar correção')
        self.window.overrideredirect(True)
        self.window.attributes('-topmost', True)
        self.window.configure(fg_color='#17171D')
        self.window.bind('<Escape>', self._cancel)
        self.window.bind('<Return>', lambda _event: self.submit())
        self.window.bind('<FocusOut>', self._focus_out, add='+')
        self.window.protocol('WM_DELETE_WINDOW', self.close)
        header = ctk.CTkFrame(self.window, fg_color='transparent')
        header.pack(fill='x', padx=14, pady=(8, 0))
        ctk.CTkLabel(header, text='Correção rápida', anchor='w').pack(side='left')
        self.close_button = ctk.CTkButton(header, text='×', width=30, height=28,
                                         fg_color='transparent', command=self.close)
        self.close_button.pack(side='right')
        self.card = ctk.CTkFrame(self.window, fg_color='transparent')
        self.card.pack(fill='both', expand=True, padx=14, pady=12)
        self.position = position
        self.text, self.hint = text, hint
        ctk.CTkButton(self.card, text='Adicionar correção', command=self.open_form,
                      height=36).pack(fill='x')
        self._place(280, 100)
        self.window.deiconify()
        self._focus_timer = self.window.after_idle(self.window.focus_force)

    def _cancel(self, _event=None):
        self.close()
        return 'break'

    def _focus_out(self, _event=None):
        if self._dismiss_timer:
            self.window.after_cancel(self._dismiss_timer)
        self._dismiss_timer = self.window.after(75, self._dismiss_if_outside)

    def _dismiss_if_outside(self):
        self._dismiss_timer = None
        if not self.is_open():
            return
        try:
            focused = self.window.focus_get()
            inside = focused is not None and focused.winfo_toplevel() == self.window
        except Exception:
            inside = False
        if not inside:
            self.close()

    def _place(self, width, height):
        x, y = self.position
        api = ctypes.WinDLL('user32')
        api.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
        api.MonitorFromPoint.restype = wintypes.HANDLE
        api.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MonitorInfo)]
        info = MonitorInfo(cbSize=ctypes.sizeof(MonitorInfo))
        if api.GetMonitorInfoW(api.MonitorFromPoint(wintypes.POINT(x, y), 2), ctypes.byref(info)):
            x = max(info.rcWork.left, min(x + 12, info.rcWork.right - width))
            y = max(info.rcWork.top, min(y + 12, info.rcWork.bottom - height))
        self.window.geometry(f'{width}x{height}{x:+d}{y:+d}')

    def open_form(self):
        if hasattr(self, 'wrong'):
            return
        for child in self.card.winfo_children():
            child.destroy()
        ctk.CTkLabel(self.card, text='Adicionar correção', anchor='w').pack(fill='x')
        ctk.CTkLabel(self.card, text='Como foi escrito', anchor='w', height=18).pack(fill='x', pady=(6, 0))
        self.wrong = ctk.CTkEntry(self.card, placeholder_text='Como foi escrito', height=34)
        self.wrong.pack(fill='x', pady=(2, 6))
        self.wrong.insert(0, self.text)
        ctk.CTkLabel(self.card, text='Grafia correta', anchor='w', height=18).pack(fill='x')
        self.correct = ctk.CTkEntry(self.card, placeholder_text='Digite e pressione Enter', height=34)
        self.correct.pack(fill='x')
        self.message = ctk.CTkLabel(self.card, text=self.hint or 'Enter para salvar · Esc para fechar',
                                    wraplength=330, justify='left', anchor='w')
        self.message.pack(fill='x', pady=6)
        actions = ctk.CTkFrame(self.card, fg_color='transparent')
        actions.pack(fill='x')
        self.cancel_button = ctk.CTkButton(actions, text='Cancelar', width=100,
                                          command=self.close, height=32, fg_color='#40404A')
        self.cancel_button.pack(side='left')
        self.save_button = ctk.CTkButton(actions, text='Salvar correção', command=self.submit, height=32)
        self.save_button.pack(side='right', fill='x', expand=True, padx=(8, 0))
        self._place(370, 310)
        if self._focus_timer:
            self.window.after_cancel(self._focus_timer)
        self._focus_timer = self.window.after_idle(lambda: (self.correct if self.text else self.wrong).focus_set())

    def submit(self):
        if self.saved:
            self.close()
            return 'break'
        if not hasattr(self, 'wrong'):
            self.open_form()
            return 'break'
        try:
            message = self.on_save(self.wrong.get(), self.correct.get())
        except ValueError as error:
            self.message.configure(text=str(error))
            return 'break'
        self.saved = True
        self.wrong.configure(state='disabled')
        self.correct.configure(state='disabled')
        self.message.configure(text=message)
        self.save_button.configure(text='Fechar', command=self.close)
        return 'break'

    def is_open(self):
        try:
            return bool(self.window.winfo_exists())
        except Exception:
            return False

    def close(self):
        if self.is_open():
            for timer in (self._focus_timer, self._dismiss_timer):
                if timer:
                    self.window.after_cancel(timer)
            self._focus_timer = self._dismiss_timer = None
            self.window.destroy()
