import ast
import pathlib
import queue
import threading
import time
import types
import unittest
from unittest.mock import Mock

SOURCE = pathlib.Path(__file__).with_name('ditado_local.pyw')
tree = ast.parse(SOURCE.read_text(encoding='utf-8'))
app_class = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'DitadoLocalApp')


def method(name, **env):
    node = next(n for n in app_class.body if isinstance(n, ast.FunctionDef) and n.name == name)
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(SOURCE), 'exec'), env)
    return env[name]


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.cancel, self.ready = threading.Event(), threading.Event()
        self.target = object()
        self.copy = Mock()
        self.app = types.SimpleNamespace(
            closing=False, agent_selected_text='', history_dirty=False,
            _read_clipboard_text=Mock(return_value='native selection'),
            _restore_target_window=Mock(), keyboard_controller=Mock(),
            history=Mock(), events=queue.SimpleQueue(), desktop=Mock(),
        )
        self.app.desktop.selected_text.return_value = ('selected text', self.target)
        self.app.desktop.copy_native_selection.return_value = False
        self.capture = method('_capture_selected_text', time=time,
                              pyperclip=types.SimpleNamespace(copy=self.copy))

    def run_capture(self):
        self.capture(self.app, self.cancel, self.ready, self.target)

    def test_copies_immediately_while_modifiers_held_without_injecting_keys(self):
        self.app.desktop.modifiers_down.return_value = True
        self.run_capture()
        self.copy.assert_called_once_with('selected text')
        self.assertEqual(self.app.agent_selected_text, 'selected text')
        self.app.keyboard_controller.press.assert_not_called()
        self.app._restore_target_window.assert_not_called()
        self.app.desktop.modifiers_down.assert_not_called()
        self.assertTrue(self.ready.is_set())

    def test_snapshot_survives_later_selection_change(self):
        self.run_capture()
        self.app.desktop.selected_text.return_value = ('other text', object())
        self.assertEqual(self.app.agent_selected_text, 'selected text')
        self.assertEqual(self.app.paste_target, self.target)

    def test_cancelled_read_cannot_publish_selection(self):
        def read(*_):
            self.cancel.set()
            return 'selected text', self.target
        self.app.desktop.selected_text.side_effect = read
        self.run_capture()
        self.copy.assert_not_called()
        self.app.history.add.assert_not_called()
        self.assertTrue(self.ready.is_set())

    def test_closing_does_not_publish(self):
        self.app.closing = True
        self.run_capture()
        self.copy.assert_not_called()

    def test_no_selection_preserves_existing_clipboard(self):
        self.app.desktop.selected_text.return_value = ('', self.target)
        self.run_capture()
        self.copy.assert_not_called()
        self.assertEqual(self.app.agent_selected_text, '')
        self.assertIn('seleção', self.app.capture_error)

    def test_native_copy_fallback_uses_only_actual_selection(self):
        self.app.desktop.selected_text.return_value = ('', self.target)
        self.app.desktop.copy_native_selection.return_value = True
        self.run_capture()
        self.copy.assert_called_once_with('native selection')

    def test_oversized_selection_rejected_without_truncation(self):
        self.app.desktop.selected_text.return_value = ('x' * 32001, self.target)
        self.run_capture()
        self.copy.assert_not_called()
        self.assertIn('longa demais', self.app.capture_error)

    def test_old_worker_signals_only_its_own_event(self):
        self.app.selection_ready = threading.Event()
        self.cancel.set()
        self.run_capture()
        self.assertTrue(self.ready.is_set())
        self.assertFalse(self.app.selection_ready.is_set())


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.app = types.SimpleNamespace(
            desktop=Mock(), _read_clipboard_text=Mock(return_value='result'),
            _paste_into_active_app=Mock(return_value=True), events=queue.SimpleQueue(),
            _restore_target_window=Mock())
        self.app.desktop.modifiers_down.return_value = False
        self.app.desktop.can_paste.return_value = True
        self.app.desktop.same_window.return_value = True
        self.deliver = method('_deliver_result')

    def test_pastes_in_unchanged_writable_field_without_refocusing(self):
        self.deliver(self.app, 'result', 'dictation', object())
        self.app._paste_into_active_app.assert_called_once_with()
        self.app._restore_target_window.assert_not_called()
        self.assertTrue(self.app.events.empty())

    def test_both_modes_notify_when_no_writable_field(self):
        self.app.desktop.can_paste.return_value = False
        for mode in ('agent', 'dictation'):
            self.deliver(self.app, 'result', mode, None)
            self.assertEqual(('clipboard_result_ready', (mode, 'result')), self.app.events.get())
        self.app._paste_into_active_app.assert_not_called()

    def test_focus_change_during_check_does_not_paste(self):
        self.app.desktop.same_window.return_value = False
        self.deliver(self.app, 'result', 'agent', object())
        self.app._paste_into_active_app.assert_not_called()
        self.assertFalse(self.app.events.empty())

    def test_clipboard_change_during_check_does_not_paste_unrelated_text(self):
        self.app._read_clipboard_text.side_effect = ['result', 'another copy']
        self.deliver(self.app, 'result', 'dictation', object())
        self.app._paste_into_active_app.assert_not_called()

    def test_held_modifiers_do_not_inject_a_shortcut(self):
        self.app.desktop.modifiers_down.return_value = True
        self.deliver(self.app, 'result', 'agent', object())
        self.app._paste_into_active_app.assert_not_called()

    def test_failed_injection_notifies(self):
        self.app._paste_into_active_app.return_value = False
        self.deliver(self.app, 'result', 'agent', object())
        self.assertEqual(('clipboard_result_ready', ('agent', 'result')), self.app.events.get())


if __name__ == '__main__':
    unittest.main()
