import ctypes
import queue
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from ditado_quick_correction import CorrectionGesture, QuickCorrectionDialog
from ditado_storage import AppConfig
from test_ditado_local import DITADO_LOCAL as ui, destroy_test_root


class Suppressed(Exception):
    pass


class QuickCorrectionTests(unittest.TestCase):
    def test_only_selected_gesture_suppresses_press_and_release(self):
        gesture = object.__new__(CorrectionGesture)
        gesture.preference = Mock(return_value='Ctrl + clique direito')
        gesture.on_request = Mock(return_value=True)
        gesture.user32 = Mock()
        pressed = {0xA2}
        gesture.user32.GetAsyncKeyState.side_effect = lambda key: 0x8000 if key in pressed else 0
        gesture.listener = Mock()
        gesture.listener.suppress_event.side_effect = Suppressed
        gesture.armed = False
        data = SimpleNamespace(pt=SimpleNamespace(x=60, y=90))
        with self.assertRaises(Suppressed):
            gesture._filter(0x204, data)
        pressed.clear()
        with self.assertRaises(Suppressed):
            gesture._filter(0x205, data)
        self.assertFalse(gesture.armed)
        gesture.on_request.assert_called_once_with(60, 90)
        self.assertTrue(gesture._filter(0x204, data))
        pressed.add(0xA2)
        pressed.add(0xA4)
        self.assertTrue(gesture._filter(0x204, data))
        gesture.preference.return_value = 'Alt + clique direito'
        pressed.remove(0xA2)
        with self.assertRaises(Suppressed):
            gesture._filter(0x204, data)
        gesture.armed = False
        gesture.preference.return_value = 'Desativado'
        self.assertTrue(gesture._filter(0x204, data))

    def test_busy_application_does_not_intercept_mouse(self):
        app = object.__new__(ui.DitadoLocalApp)
        app.closing = app.processing = False
        app.recording = True
        self.assertFalse(app._on_quick_correction_gesture(1, 2))

    def test_click_on_another_window_does_not_capture_old_selection(self):
        app = object.__new__(ui.DitadoLocalApp)
        app.closing = app.processing = app.recording = app.quick_correction_active = False
        app.quick_correction_request = None
        app.desktop = Mock()
        app.desktop.target_at_point.return_value = False
        app.events = queue.SimpleQueue()
        self.assertFalse(app._on_quick_correction_gesture(30, 40))
        self.assertTrue(app.events.empty())

    def app(self, path):
        app = object.__new__(ui.DitadoLocalApp)
        app.config = AppConfig(path=path / 'profile.json')
        app.history = SimpleNamespace(path=path / 'history')
        app.status = Mock()
        app.status.get.return_value = 'Correção salva.'
        app._rebuild_corrections = Mock()
        app._sync_corrections_after_change = Mock()
        return app

    def test_save_uses_profile_dictionary_and_requests_cloud_sync(self):
        with tempfile.TemporaryDirectory() as directory:
            app = self.app(Path(directory))
            profile = str(app.history.path)
            app._save_quick_correction(profile, ' exmplo ', ' exemplo ')
            correction = app.config.get('corrections')[0]
            self.assertEqual(('exmplo', 'exemplo'), (correction['wrong'], correction['correct']))
            app._sync_corrections_after_change.assert_called_once()
            self.assertEqual(app.config.get('corrections'), AppConfig(app.config.path).get('corrections'))
            app._save_quick_correction(profile, 'exmplo', 'Exemplo')
            self.assertEqual(correction['id'], app.config.get('corrections')[0]['id'])
            self.assertEqual(1, len(app.config.get('corrections')))

    def test_account_switch_and_invalid_fields_do_not_save(self):
        with tempfile.TemporaryDirectory() as directory:
            app = self.app(Path(directory))
            for profile, wrong, correct in [('old-account', 'x', 'y'),
                    (str(app.history.path), '', 'y'), (str(app.history.path), 'same', 'same'),
                    (str(app.history.path), 'x' * 2049, 'y')]:
                with self.assertRaises(ValueError):
                    app._save_quick_correction(profile, wrong, correct)
            self.assertEqual([], app.config.get('corrections'))
            app._sync_corrections_after_change.assert_not_called()

    def test_dictionary_capacity_never_evicts_existing_rules(self):
        with tempfile.TemporaryDirectory() as directory:
            app = self.app(Path(directory))
            existing = [{'wrong': str(i), 'correct': 'ok'} for i in range(200)]
            app.config.data['corrections'] = existing.copy()
            with self.assertRaises(ValueError):
                app._save_quick_correction(str(app.history.path), 'new', 'correct')
            self.assertEqual(existing, app.config.get('corrections'))

    def test_late_capture_and_other_account_do_not_open_dialog(self):
        app = object.__new__(ui.DitadoLocalApp)
        app.closing = False
        app.history = SimpleNamespace(path=Path('new-profile'))
        request = (threading.Event(), 'old-profile', (1, 2), None)
        app.quick_correction_request = request
        with patch.object(ui, 'QuickCorrectionDialog') as dialog:
            app._finish_quick_correction((request, 'word', ''))
            app._finish_quick_correction((request, 'late', ''))
            dialog.assert_not_called()
        self.assertTrue(request[0].is_set())

    def test_gesture_save_updates_cloud_preference(self):
        with tempfile.TemporaryDirectory() as directory:
            app = self.app(Path(directory))
            app._sync_after_local_change = Mock()
            app._save_correction_gesture('Shift + clique direito')
            self.assertEqual('Shift + clique direito', app.config.get('quick_correction_gesture'))
            self.assertIn('quick_correction_gesture', app.config.SYNCED_PREFERENCE_KEYS)
            app._sync_after_local_change.assert_called_once()

    def test_unsaved_dialog_can_close_from_menu_or_form(self):
        root = ui.ctk.CTk()
        root.withdraw()
        save = Mock()
        try:
            for action in ('menu_close', 'form_close', 'cancel', 'escape', 'outside'):
                with self.subTest(action=action):
                    dialog = QuickCorrectionDialog(root, 'exmplo', (50, 50), save)
                    root.update()
                    if action != 'menu_close':
                        dialog.open_form()
                        dialog.correct.insert(0, 'unsaved draft')
                        root.update()
                    if action in ('menu_close', 'form_close'):
                        dialog.close_button.invoke()
                    elif action == 'cancel':
                        dialog.cancel_button.invoke()
                    elif action == 'escape':
                        dialog.correct._entry.event_generate('<Escape>')
                        root.update()
                    else:
                        with patch.object(dialog.window, 'focus_get', return_value=None):
                            dialog._dismiss_if_outside()
                    self.assertFalse(dialog.is_open())
            save.assert_not_called()
        finally:
            destroy_test_root(root)

    def test_moving_focus_between_form_fields_does_not_close(self):
        root = ui.ctk.CTk()
        root.withdraw()
        try:
            dialog = QuickCorrectionDialog(root, 'exmplo', (50, 50), Mock())
            dialog.open_form()
            with patch.object(dialog.window, 'focus_get', return_value=dialog.correct._entry):
                dialog._dismiss_if_outside()
            self.assertTrue(dialog.is_open())
            dialog.close()
        finally:
            destroy_test_root(root)

    def test_successful_save_closes_before_applying_to_source(self):
        root = ui.ctk.CTk()
        root.withdraw()
        try:
            save, apply = Mock(return_value='saved'), Mock()
            dialog = QuickCorrectionDialog(root, 'exmplo', (50, 50), save, on_saved=apply)
            dialog.open_form()
            dialog.correct.insert(0, 'exemplo')
            apply.side_effect = lambda correct: self.assertFalse(dialog.is_open())
            dialog.submit()
            save.assert_called_once_with('exmplo', 'exemplo')
            apply.assert_called_once_with('exemplo')
            dialog.close()
            apply.assert_called_once()
        finally:
            destroy_test_root(root)

    def test_invalid_save_does_not_apply_or_close(self):
        root = ui.ctk.CTk()
        root.withdraw()
        try:
            apply = Mock()
            dialog = QuickCorrectionDialog(root, 'exmplo', (50, 50),
                Mock(side_effect=ValueError('invalid')), on_saved=apply)
            dialog.open_form()
            dialog.submit()
            self.assertTrue(dialog.is_open())
            apply.assert_not_called()
            dialog.close()
        finally:
            destroy_test_root(root)

    def test_late_replacement_after_profile_switch_does_not_touch_clipboard(self):
        app = object.__new__(ui.DitadoLocalApp)
        request = (threading.Event(), 'old-profile')
        app.quick_replacement = request
        app.history = SimpleNamespace(path='new-profile')
        app.closing = False
        app._copy_to_clipboard = Mock()
        app.result_notification = Mock()
        app._finish_quick_correction_applied((request, False, 'correction'))
        app._copy_to_clipboard.assert_not_called()
        app.result_notification.show_correction.assert_not_called()

    def test_dialog_submit_saves_once_and_close_does_not_save(self):
        root = ui.ctk.CTk()
        root.withdraw()
        save = Mock(return_value='Correção salva neste PC.')
        try:
            dialog = QuickCorrectionDialog(root, 'exmplo', (50, 50), save)
            dialog.submit()
            self.assertEqual('exmplo', dialog.wrong.get())
            dialog.correct.insert(0, 'exemplo')
            # Exercise the submission handler without requiring desktop focus;
            # focus changes intentionally dismiss this non-modal window now.
            dialog.submit()
            self.assertTrue(dialog.saved)
            save.assert_called_once_with('exmplo', 'exemplo')
            dialog.submit()
            save.assert_called_once()
            other = QuickCorrectionDialog(root, 'cancelled', (50, 50), save)
            other.close()
            save.assert_called_once()
        finally:
            destroy_test_root(root)


if __name__ == '__main__':
    unittest.main()
