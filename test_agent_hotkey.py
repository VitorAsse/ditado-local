import unittest
from unittest.mock import Mock, patch

from ditado_hotkey import AgentChatHotkey, parse_chat_shortcut


class AgentHotkeyTests(unittest.TestCase):
    def hotkey(self):
        hotkey = AgentChatHotkey(Mock(), Mock(), shortcut="F8")
        hotkey.user32 = Mock()
        hotkey.kernel32 = Mock()
        hotkey.kernel32.GetCurrentThreadId.return_value = 123
        hotkey.user32.RegisterHotKey.return_value = True
        return hotkey

    def test_windows_message_opens_chat_once_and_registration_is_released(self):
        hotkey = self.hotkey()
        messages = iter([(hotkey.WM_HOTKEY, hotkey.HOTKEY_ID), None])
        def get_message(pointer, *_):
            message = next(messages)
            if message is None:
                return 0
            pointer._obj.message, pointer._obj.wParam = message
            return 1
        hotkey.user32.GetMessageW.side_effect = get_message
        hotkey._run()
        hotkey.user32.RegisterHotKey.assert_called_once_with(None, hotkey.HOTKEY_ID, 0x4000, 0x77)
        hotkey.on_press.assert_called_once_with()
        hotkey.user32.UnregisterHotKey.assert_called_once_with(None, hotkey.HOTKEY_ID)
        self.assertTrue(hotkey.ready.is_set())
        self.assertFalse(hotkey.registered)

    def test_occupied_shortcut_reports_failure_without_silently_falling_back(self):
        hotkey = self.hotkey()
        hotkey.user32.RegisterHotKey.return_value = False
        with patch('ditado_hotkey.ctypes.get_last_error', return_value=1409):
            hotkey._run()
        self.assertFalse(hotkey.on_status.call_args.args[0])
        self.assertIn('outro aplicativo', hotkey.on_status.call_args.args[1])
        hotkey.on_press.assert_not_called()
        hotkey.user32.GetMessageW.assert_not_called()
        hotkey.user32.UnregisterHotKey.assert_not_called()
        self.assertTrue(hotkey.ready.is_set())

    def test_stopping_before_start_does_not_reserve_key(self):
        hotkey = self.hotkey()
        hotkey.stopping.set()
        hotkey._run()
        hotkey.user32.RegisterHotKey.assert_not_called()
        self.assertTrue(hotkey.ready.is_set())

    def test_shutdown_signals_only_the_registered_thread(self):
        hotkey = self.hotkey()
        hotkey.thread_id = 123
        hotkey.thread = Mock()
        hotkey.ready.set()
        hotkey.stop()
        hotkey.user32.PostThreadMessageW.assert_called_once_with(123, hotkey.WM_QUIT, 0, 0)
        hotkey.thread.join.assert_called_once_with(timeout=1)

    def test_callback_failure_releases_shortcut_and_reports_error(self):
        hotkey = self.hotkey()
        def get_message(pointer, *_):
            pointer._obj.message = hotkey.WM_HOTKEY
            pointer._obj.wParam = hotkey.HOTKEY_ID
            return 1
        hotkey.user32.GetMessageW.side_effect = get_message
        hotkey.on_press.side_effect = RuntimeError('Failed callback')
        hotkey._run()
        hotkey.user32.UnregisterHotKey.assert_called_once()
        self.assertFalse(hotkey.on_status.call_args.args[0])

    def test_control_windows_covers_either_order_and_both_windows_keys(self):
        spec = parse_chat_shortcut('win + control')
        self.assertEqual('Ctrl + Windows', spec.label)
        self.assertEqual(((10, 0x5B), (10, 0x5C), (10, 0x11)), spec.bindings)

    def test_custom_shortcut_parses_and_normalizes(self):
        spec = parse_chat_shortcut('shift + control + a')
        self.assertEqual('Ctrl + Shift + A', spec.label)
        self.assertEqual(((6, 0x41),), spec.bindings)

    def test_invalid_and_conflicting_shortcuts_are_rejected(self):
        for text in ('F', 'a', 'Enter', 'Ctrl + Espaço', 'Ctrl + Alt', 'F12', 'Win + L',
                     'Ctrl + Alt + Delete', 'Ctrl + Ctrl + A', 'Ctrl + A + B', ''):
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_chat_shortcut(text)

    def test_partial_registration_failure_releases_only_new_bindings(self):
        hotkey = self.hotkey()
        hotkey.spec = parse_chat_shortcut('Ctrl + Windows')
        hotkey.user32.RegisterHotKey.side_effect = [True, False]
        with patch('ditado_hotkey.ctypes.get_last_error', return_value=1409):
            hotkey._run()
        hotkey.user32.UnregisterHotKey.assert_called_once_with(None, hotkey.HOTKEY_ID)
        self.assertFalse(hotkey.registered)


if __name__ == '__main__':
    unittest.main()
