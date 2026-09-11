"""Windows selection and focused-edit checks. No synthetic activation keys or focus changes."""
import ctypes
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from ctypes import wintypes

import comtypes
from comtypes.client import CreateObject, GetModule


class GUIThreadInfo(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("hwndActive", wintypes.HWND), ("hwndFocus", wintypes.HWND),
                ("hwndCapture", wintypes.HWND), ("hwndMenuOwner", wintypes.HWND),
                ("hwndMoveSize", wintypes.HWND), ("hwndCaret", wintypes.HWND),
                ("rcCaret", wintypes.RECT)]


class SelectionTooLongError(ValueError):
    pass


@dataclass(frozen=True)
class FocusTarget:
    window: int
    control: int
    runtime_id: tuple = ()
    editable: bool = False


class DesktopTextAccess:
    def __init__(self):
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.user32.GetForegroundWindow.restype = wintypes.HWND
        self.user32.WindowFromPoint.argtypes = [wintypes.POINT]
        self.user32.WindowFromPoint.restype = wintypes.HWND
        self.user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        self.user32.GetGUIThreadInfo.argtypes = [wintypes.DWORD, ctypes.POINTER(GUIThreadInfo)]
        self.user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
        self.user32.GetAncestor.restype = wintypes.HWND
        self.user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        self.user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
        self.user32.IsWindowEnabled.argtypes = [wintypes.HWND]
        self.user32.SendMessageTimeoutW.argtypes = [wintypes.HWND, wintypes.UINT,
            wintypes.WPARAM, wintypes.LPARAM, wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_size_t)]
        self.user32.SendMessageTimeoutW.restype = wintypes.LPARAM
        self._module = None
        self._module_lock = threading.Lock()

    def basic_focus(self):
        window = self.user32.GetForegroundWindow()
        info = GUIThreadInfo(cbSize=ctypes.sizeof(GUIThreadInfo))
        if not window:
            return FocusTarget(0, 0)
        thread = self.user32.GetWindowThreadProcessId(window, None)
        if not self.user32.GetGUIThreadInfo(thread, ctypes.byref(info)):
            return FocusTarget(int(window), 0)
        return FocusTarget(int(window), int(info.hwndFocus or 0))

    def target_at_point(self, target, x, y):
        window = self.user32.WindowFromPoint(wintypes.POINT(x, y))
        return bool(window and target and target.window
                    and self.user32.GetAncestor(window, 2) == target.window)

    def same_window(self, target):
        now = self.basic_focus()
        return bool(target and target.window and target.control and
                    (target.window, target.control) == (now.window, now.control))

    def native_edit(self, target):
        """Positive native edit classification; unknown controls are not considered writable."""
        name = ctypes.create_unicode_buffer(256)
        self.user32.GetClassNameW(target.control, name, len(name))
        if name.value.casefold() != "edit" and not name.value.casefold().startswith("richedit"):
            return None
        style = self.user32.GetWindowLongW(target.control, -16)
        return bool(self.user32.IsWindowEnabled(target.control) and not style & (0x0800 | 0x0020))

    @contextmanager
    def automation(self):
        # Every call is run on a worker thread; avoid COM/Tk cross-thread interfaces.
        comtypes.CoInitializeEx(0)
        try:
            with self._module_lock:
                if self._module is None:
                    self._module = GetModule("UIAutomationCore.dll")
            module = self._module
            client = CreateObject(module.CUIAutomation, interface=module.IUIAutomation)
            yield client, module
        finally:
            comtypes.CoUninitialize()

    def prepare(self):
        try:
            with self.automation():
                pass
        except Exception:
            pass

    @staticmethod
    def _editable(element, module):
        if not element.CurrentIsEnabled or element.CurrentIsPassword or not element.CurrentHasKeyboardFocus:
            return False
        try:
            value = element.GetCurrentPattern(module.UIA_ValuePatternId).QueryInterface(module.IUIAutomationValuePattern)
            return not bool(value.CurrentIsReadOnly)
        except Exception:
            pass
        # Multiline web editors often expose TextPattern without ValuePattern.
        try:
            text = element.GetCurrentPattern(module.UIA_TextPatternId).QueryInterface(module.IUIAutomationTextPattern)
            readonly = text.DocumentRange.GetAttributeValue(module.UIA_IsReadOnlyAttributeId)
            return isinstance(readonly, (bool, int)) and readonly == 0
        except Exception:
            return False

    def _focused_element(self, client, target):
        if not self.same_window(target):
            return None
        element = client.GetFocusedElement()
        if not element or not self.same_window(target):
            return None
        return element

    def describe_focus(self, target):
        if not self.same_window(target):
            return target
        native = self.native_edit(target)
        if native is not None:
            return FocusTarget(target.window, target.control, editable=native)
        try:
            with self.automation() as (client, module):
                element = self._focused_element(client, target)
                if element:
                    return FocusTarget(target.window, target.control,
                                       tuple(element.GetRuntimeId()), self._editable(element, module))
        except Exception:
            pass
        return target

    def selected_text(self, target, cancelled, max_chars=32000):
        """Read actual selected ranges, including read-only documents. Never read whole documents."""
        if cancelled.is_set() or not self.same_window(target):
            return "", target
        try:
            with self.automation() as (client, module):
                element = self._focused_element(client, target)
                if element is None or element.CurrentIsPassword:
                    return "", target
                native = self.native_edit(target)
                snapshot = FocusTarget(target.window, target.control, tuple(element.GetRuntimeId()),
                                       native if native is not None else self._editable(element, module))
                # The focused node or its owning document may expose the selection.
                node = element
                for _ in range(5):
                    if not node or cancelled.is_set() or not self.same_window(target):
                        break
                    try:
                        pattern = node.GetCurrentPattern(module.UIA_TextPatternId).QueryInterface(module.IUIAutomationTextPattern)
                        ranges = pattern.GetSelection()
                        if ranges and ranges.Length:
                            chunks = [ranges.GetElement(i).GetText(max_chars + 1) for i in range(min(ranges.Length, 32))]
                            text = "\n".join(chunk for chunk in chunks if chunk)
                            if len(text) > max_chars or ranges.Length > 32:
                                raise SelectionTooLongError("A seleção está longa demais. Use um trecho menor; nenhum texto foi cortado.")
                            if text:
                                return text, snapshot
                    except SelectionTooLongError:
                        raise
                    except Exception:
                        pass
                    # Only walk within the originally focused application.
                    node = client.ControlViewWalker.GetParentElement(node)
                    if node and node.CurrentNativeWindowHandle:
                        root = self.user32.GetAncestor(node.CurrentNativeWindowHandle, 2)
                        if root != target.window:
                            break
                return "", snapshot
        except SelectionTooLongError:
            raise
        except Exception:
            return "", target

    def copy_native_selection(self, target):
        """WM_COPY is independent of held Ctrl/Alt. Limit fallback to native text controls."""
        if not self.same_window(target) or self.native_edit(target) is None:
            return False
        style = self.user32.GetWindowLongW(target.control, -16)
        if style & 0x0020:  # password
            return False
        before = self.user32.GetClipboardSequenceNumber()
        result = ctypes.c_size_t()
        self.user32.SendMessageTimeoutW(target.control, 0x0301, 0, 0, 0x0002, 150, ctypes.byref(result))
        return before != self.user32.GetClipboardSequenceNumber()

    def can_paste(self, target):
        if not target or not target.editable or not self.same_window(target):
            return False
        current = self.describe_focus(self.basic_focus())
        if not current.editable:
            return False
        if target.runtime_id and current.runtime_id:
            return target.runtime_id == current.runtime_id
        return self.native_edit(current) is True

    def modifiers_down(self):
        return any(self.user32.GetAsyncKeyState(k) & 0x8000
                   for k in (0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5, 0x5B, 0x5C))
