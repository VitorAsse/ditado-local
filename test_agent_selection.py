import ast
import pathlib
import threading
import time
import types
import unittest
import uuid

SOURCE = pathlib.Path(__file__).with_name('ditado_local.pyw')
tree = ast.parse(SOURCE.read_text(encoding='utf-8'))
app_class = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'DitadoLocalApp')
capture = next(n for n in app_class.body if isinstance(n, ast.FunctionDef) and n.name == '_capture_selected_text')


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.held = {0xA2, 0xA4}
        self.actions = []
        self.clipboard = 'previous clipboard'
        self.selection = 'selected text'
        self.cancel = threading.Event()
        self.ready = threading.Event()
        self.fail_copy = False
        self.app = types.SimpleNamespace(
            closing=False, agent_selected_text='', history_dirty=False,
            _read_clipboard_text=lambda: self.clipboard,
            _restore_target_window=lambda: self.actions.append(('focus',)),
            history=types.SimpleNamespace(add=lambda *args: self.actions.append(('history', *args))),
        )

        def copy(text):
            self.actions.append(('clipboard', text))
            self.clipboard = text

        def press(key):
            self.actions.append(('press', key))
            if key == 'c':
                self.assertFalse(self.held, 'C injected while physical modifiers are down')
                self.assertEqual(self.actions[-2], ('press', 'ctrl_l'))
                if self.fail_copy:
                    raise RuntimeError('injection failed')
                if self.selection is not None:
                    self.clipboard = self.selection

        self.app.keyboard_controller = types.SimpleNamespace(
            press=press, release=lambda key: self.actions.append(('release', key)))
        env = dict(
            ctypes=types.SimpleNamespace(windll=types.SimpleNamespace(user32=types.SimpleNamespace(
                GetAsyncKeyState=lambda key: 0x8000 if key in self.held else 0))),
            time=time, uuid=uuid, pyperclip=types.SimpleNamespace(copy=copy),
            pynput_keyboard=types.SimpleNamespace(Key=types.SimpleNamespace(ctrl_l='ctrl_l')),
        )
        exec(compile(ast.Module(body=[capture], type_ignores=[]), str(SOURCE), 'exec'), env)
        self.run_capture = lambda: env['_capture_selected_text'](self.app, self.cancel, self.ready)

    def start(self):
        self.worker = threading.Thread(target=self.run_capture)
        self.worker.start()
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.cancel.set()
        self.worker.join(2)
        self.assertFalse(self.worker.is_alive())

    def test_hold_then_release_both(self):
        self.start()
        time.sleep(.10)
        self.assertEqual(self.actions, [])
        self.held.remove(0xA2)
        time.sleep(.08)
        self.assertEqual(self.actions, [])
        self.held.clear()
        self.assertTrue(self.ready.wait(1))
        self.assertEqual(self.app.agent_selected_text, 'selected text')
        self.assertEqual([a for a in self.actions if a[0] in ('press', 'release')],
                         [('press', 'ctrl_l'), ('press', 'c'), ('release', 'c'), ('release', 'ctrl_l')])

    def test_each_modifier_blocks_copy(self):
        for key in (0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5, 0x5B, 0x5C):
            with self.subTest(key=key):
                self.held = {key}
                self.cancel.clear()
                self.ready.clear()
                self.start()
                time.sleep(.03)
                self.assertEqual(self.actions, [])
                self.cancel.set()
                self.worker.join(1)
                self.assertTrue(self.ready.is_set())

    def test_cancel_during_hold_does_not_touch_clipboard_or_keys(self):
        self.start()
        self.cancel.set()
        self.assertTrue(self.ready.wait(1))
        self.assertEqual(self.actions, [])
        self.assertEqual(self.clipboard, 'previous clipboard')

    def test_closing_does_not_inject(self):
        self.app.closing = True
        self.run_capture()
        self.assertTrue(self.ready.is_set())
        self.assertEqual(self.actions, [])

    def test_no_selection_restores_clipboard(self):
        self.held.clear()
        self.selection = None
        self.run_capture()
        self.assertEqual(self.clipboard, 'previous clipboard')
        self.assertEqual(self.app.agent_selected_text, '')

    def test_copy_failure_releases_ctrl_and_restores_empty_clipboard(self):
        self.held.clear()
        self.clipboard = ''
        self.fail_copy = True
        self.run_capture()
        self.assertIn(('release', 'ctrl_l'), self.actions)
        self.assertEqual(self.clipboard, '')
        self.assertTrue(self.ready.is_set())

    def test_old_worker_signals_own_event(self):
        new_ready = threading.Event()
        self.app.selection_ready = new_ready
        self.cancel.set()
        self.run_capture()
        self.assertTrue(self.ready.is_set())
        self.assertFalse(new_ready.is_set())


if __name__ == '__main__':
    compile(SOURCE.read_text(encoding='utf-8'), str(SOURCE), 'exec')
    unittest.main(verbosity=2)
