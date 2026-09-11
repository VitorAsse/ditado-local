import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from ditado_hotkey import parse_chat_shortcut
from ditado_storage import AppConfig
from test_ditado_local import DITADO_LOCAL as ui


class ShortcutSettingsTests(unittest.TestCase):
    def app(self, directory):
        app = object.__new__(ui.DitadoLocalApp)
        app.config = AppConfig(path=Path(directory) / 'config.json')
        app.config.set('agent_chat_hotkey', 'F8')
        app.chat_hotkey = Mock(registered=True)
        app.chat_hotkey.spec = parse_chat_shortcut('F8')
        app.agent_shortcut_entry = Mock()
        app.agent_shortcut_status = Mock()
        app._sync_after_local_change = Mock()
        app.events = ui.queue.SimpleQueue()
        return app

    def test_save_applies_new_shortcut_before_stopping_old_and_syncs_preference(self):
        with tempfile.TemporaryDirectory() as directory:
            app = self.app(directory)
            app.agent_shortcut_entry.get.return_value = 'windows + ctrl'
            old = app.chat_hotkey
            actions = []
            old.stop.side_effect = lambda: actions.append('old-stopped')
            candidate = Mock(registered=True)
            candidate.spec = parse_chat_shortcut('Ctrl + Windows')
            candidate.start.side_effect = lambda: actions.append('new-started')
            candidate.ready.wait.return_value = True
            with patch.object(ui, 'AgentChatHotkey', return_value=candidate):
                app._save_agent_shortcut()
            self.assertEqual(['new-started', 'old-stopped'], actions)
            self.assertIs(app.chat_hotkey, candidate)
            self.assertEqual('Ctrl + Windows', AppConfig(path=app.config.path).get('agent_chat_hotkey'))
            app._sync_after_local_change.assert_called_once_with('Atalho salvo.', success_message='Atalho salvo na nuvem.')

    def test_failed_registration_keeps_working_shortcut_and_saved_preference(self):
        with tempfile.TemporaryDirectory() as directory:
            app = self.app(directory)
            app.agent_shortcut_entry.get.return_value = 'Ctrl + Windows'
            old = app.chat_hotkey
            candidate = Mock(registered=False, error='Atalho ocupado.')
            candidate.ready.wait.return_value = True
            with patch.object(ui, 'AgentChatHotkey', return_value=candidate):
                app._save_agent_shortcut()
            self.assertIs(old, app.chat_hotkey)
            old.stop.assert_not_called()
            candidate.stop.assert_called_once()
            self.assertEqual('F8', app.config.get('agent_chat_hotkey'))
            app._sync_after_local_change.assert_not_called()
            self.assertIn('F8 continua ativo', app.agent_shortcut_status.set.call_args.args[0])

    def test_invalid_shortcut_is_rejected_without_registration_or_profile_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            app = self.app(directory)
            app.agent_shortcut_entry.get.return_value = 'Ctrl + Espaço'
            with patch.object(ui, 'AgentChatHotkey') as registration:
                app._save_agent_shortcut()
            registration.assert_not_called()
            self.assertEqual('F8', app.config.get('agent_chat_hotkey'))

    def test_cloud_refresh_reconfigures_runtime_shortcut(self):
        with tempfile.TemporaryDirectory() as directory:
            app = self.app(directory)
            app.config.set('agent_chat_hotkey', 'Ctrl + Shift + Enter')
            app._configure_agent_shortcut = Mock(return_value=True)
            app._refresh_agent_shortcut()
            app.agent_shortcut_entry.set.assert_called_once_with('Ctrl + Shift + Enter')
            app._configure_agent_shortcut.assert_called_once_with('Ctrl + Shift + Enter')

    def test_identity_save_includes_name_aliases_and_invokes_cloud_sync(self):
        with tempfile.TemporaryDirectory() as directory:
            app = self.app(directory)
            app.agent_identity_name = Mock()
            app.agent_identity_name.get.return_value = '  Morgan Lee  '
            app.agent_identity_aliases = Mock()
            app.agent_identity_aliases.get.return_value = 'Morgan, M. Lee'
            app._save_agent_identity()
            identity = AppConfig(path=app.config.path).get('user_identity')
            self.assertEqual({'display_name':'Morgan Lee', 'aliases':['Morgan', 'M. Lee']}, identity)
            item = next(x for x in app.config.cloud_snapshot()['preferences'] if x['key'] == 'user_identity')
            self.assertEqual(identity, item['value'])
            app._sync_after_local_change.assert_called_once_with('Identificação salva.', success_message='Nome e aliases salvos na nuvem.')


if __name__ == '__main__':
    unittest.main()
