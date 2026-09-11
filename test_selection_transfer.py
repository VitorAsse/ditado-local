import threading
import unittest
from unittest.mock import Mock, patch
from ditado_desktop import DesktopTextAccess, FocusTarget, SelectionTooLongError


class SelectionTransferTests(unittest.TestCase):
    def desktop(self):
        desktop = object.__new__(DesktopTextAccess)
        desktop._clipboard_lock = threading.Lock()
        desktop.user32 = Mock()
        desktop.selected_text = Mock(return_value=('', FocusTarget(1, 2, (3,), True)))
        desktop.same_window = Mock(return_value=True)
        desktop.modifiers_down = Mock(return_value=False)
        desktop.copy_native_selection = Mock(return_value=False)
        desktop.native_edit = Mock(return_value=None)
        desktop._shortcut = Mock()
        desktop.user32.GetClipboardSequenceNumber.side_effect = [4, 5]
        return desktop

    def test_browser_fallback_copies_fresh_selection_after_releasing_modifiers(self):
        desktop = self.desktop()
        desktop.modifiers_down.side_effect = [True, False]
        with patch('ditado_desktop.pyperclip.paste', return_value='Selected\r\nparagraph'):
            text, target = desktop.capture_selection(FocusTarget(1, 2), threading.Event())
        self.assertEqual('Selected\r\nparagraph', text)
        desktop._shortcut.assert_called_once_with('c')
        self.assertFalse(target.editable)

    def test_unchanged_clipboard_never_returns_stale_text(self):
        desktop = self.desktop()
        desktop.user32.GetClipboardSequenceNumber.side_effect = None
        desktop.user32.GetClipboardSequenceNumber.return_value = 4
        with patch('ditado_desktop.pyperclip.paste') as paste, patch('ditado_desktop.time.monotonic', side_effect=[0, 0, 2]):
            text, _ = desktop.capture_selection(FocusTarget(1, 2), threading.Event())
        self.assertEqual('', text)
        paste.assert_not_called()

    def test_cancel_or_focus_change_prevents_copy(self):
        for cancelled, focus in ((True, True), (False, False)):
            desktop = self.desktop()
            desktop.same_window.return_value = focus
            stop = threading.Event()
            if cancelled: stop.set()
            self.assertEqual('', desktop.capture_selection(FocusTarget(1, 2), stop)[0])
            desktop._shortcut.assert_not_called()

    def test_password_and_oversize_never_fall_back_to_keyboard(self):
        desktop = self.desktop()
        desktop.selected_text.return_value = ('', FocusTarget(1, 2, protected=True))
        self.assertEqual('', desktop.capture_selection(FocusTarget(1, 2), threading.Event())[0])
        desktop._shortcut.assert_not_called()
        desktop.selected_text.side_effect = SelectionTooLongError('long')
        with self.assertRaises(SelectionTooLongError):
            desktop.capture_selection(FocusTarget(1, 2), threading.Event())
        desktop._shortcut.assert_not_called()

    def replacement(self):
        desktop = self.desktop()
        target = FocusTarget(1, 2, (3,), True)
        desktop.describe_focus = Mock(return_value=target)
        desktop.capture_selection = Mock(return_value=('exmplo', target))
        desktop.can_paste = Mock(return_value=True)
        return desktop, target

    def test_replacement_requires_exact_original_selection_and_editable_field(self):
        for valid, text, editable, runtime in ((True,'exmplo',True,(3,)), (False,'other',True,(3,)),
                                               (False,'exmplo',False,(3,)), (False,'exmplo',True,(4,))):
            desktop, target = self.replacement()
            desktop.describe_focus.return_value = FocusTarget(1, 2, runtime, editable)
            desktop.capture_selection.return_value = (text, target)
            with patch('ditado_desktop.pyperclip.copy') as copy, patch('ditado_desktop.pyperclip.paste', return_value='exemplo'):
                self.assertEqual(valid, desktop.replace_selected_text(target,'exmplo','exemplo',threading.Event()))
                if valid:
                    copy.assert_called_once_with('exemplo')
                    desktop._shortcut.assert_called_once_with('v')
                else:
                    copy.assert_not_called()
                    desktop._shortcut.assert_not_called()

    def test_cancellation_during_recheck_prevents_replacement(self):
        desktop, target = self.replacement()
        stop = threading.Event()
        def capture(*args, **kwargs):
            stop.set()
            return 'exmplo', target
        desktop.capture_selection.side_effect = capture
        self.assertFalse(desktop.replace_selected_text(target,'exmplo','exemplo',stop))
        desktop._shortcut.assert_not_called()


if __name__ == '__main__':
    unittest.main()
