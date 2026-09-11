import queue
import threading
import unittest
from unittest.mock import Mock, patch

from test_ditado_local import DITADO_LOCAL as ui, destroy_test_root


class PrefillTests(unittest.TestCase):
    def app(self):
        app = object.__new__(ui.DitadoLocalApp)
        app.recording = app.processing = app.closing = False
        app.agent_chat_window = None
        app.desktop = Mock()
        app.desktop.selected_text.return_value = ('Linha 1\n\nLinha 2', None)
        app.desktop.copy_native_selection.return_value = False
        app.history = Mock(path='profile-a')
        app.root = Mock()
        app.status = Mock()
        app.events = queue.SimpleQueue()
        app._read_clipboard_text = Mock(return_value='stale clipboard')
        app._open_new_agent_chat = Mock()
        app._send_agent_chat_follow_up = Mock()
        app._open_input_stream_with_recovery = Mock()
        return app

    def capture(self, app):
        # Execute the worker deterministically; the actual UI callback stays separate.
        with patch.object(ui.threading, 'Thread') as worker:
            app._request_agent_chat_prefill()
            worker.call_args.kwargs['target']()
        event, payload = app.events.get_nowait()
        self.assertEqual('agent_chat_prefill_ready', event)
        return payload

    def test_selection_is_read_before_opening_and_never_submitted(self):
        app = self.app()
        payload = self.capture(app)
        app._open_new_agent_chat.assert_not_called()
        app._finish_agent_chat_prefill(payload)
        app._open_new_agent_chat.assert_called_once_with(initial_text='Linha 1\n\nLinha 2')
        app._send_agent_chat_follow_up.assert_not_called()
        app._open_input_stream_with_recovery.assert_not_called()
        app.history.add.assert_not_called()

    def test_empty_selection_does_not_insert_old_clipboard(self):
        app = self.app()
        app.desktop.selected_text.return_value = ('', None)
        app._finish_agent_chat_prefill(self.capture(app))
        app._read_clipboard_text.assert_not_called()
        app._open_new_agent_chat.assert_called_once_with(initial_text='')

    def test_native_copy_only_reads_clipboard_after_successful_selection_copy(self):
        app = self.app()
        app.desktop.selected_text.return_value = ('', None)
        app.desktop.copy_native_selection.return_value = True
        app._read_clipboard_text.return_value = 'Native selected text'
        app._finish_agent_chat_prefill(self.capture(app))
        app._open_new_agent_chat.assert_called_once_with(initial_text='Native selected text')

    def test_profile_change_discards_pending_selection(self):
        app = self.app()
        payload = self.capture(app)
        app.history.path = 'profile-b'
        app._finish_agent_chat_prefill(payload)
        app._open_new_agent_chat.assert_not_called()
        self.assertIsNone(app.chat_prefill_request)

    def test_slow_capture_opens_empty_draft_and_late_result_does_not_overwrite(self):
        app = self.app()
        payload = self.capture(app)
        app._expire_agent_chat_prefill(payload[0])
        self.assertTrue(payload[0][0].is_set())
        app._finish_agent_chat_prefill(payload)
        app._open_new_agent_chat.assert_called_once_with(initial_text='')

    def test_inflight_recording_does_not_get_cancelled_or_open_chat(self):
        app = self.app()
        app.recording = True
        with patch.object(ui.threading, 'Thread') as worker:
            app._request_agent_chat_prefill()
        worker.assert_not_called()
        app._open_new_agent_chat.assert_not_called()
        self.assertTrue(app.recording)

    def test_generation_started_during_capture_is_not_unlocked_by_late_error(self):
        app = self.app()
        payload = self.capture(app)
        app.agent_chat_window = Mock(loading=True)
        app.agent_chat_window.is_open.return_value = True
        app._finish_agent_chat_prefill((payload[0], '', 'Read failed'))
        app.agent_chat_window.show_error.assert_not_called()
        app._open_new_agent_chat.assert_not_called()

    def test_editable_draft_preserves_text_and_only_sends_on_submit(self):
        root = ui.ctk.CTk()
        root.withdraw()
        sent = Mock()
        chat = ui.AgentChatWindow(root, None, sent, Mock())
        try:
            root.update()
            chat.prefill('Olá Petra!\r\n\rTexto selecionado.')
            self.assertEqual('Olá Petra!\n\nTexto selecionado.', chat.input.get('1.0', 'end-1c'))
            self.assertFalse(chat.loading)
            self.assertIsNone(chat.conversation)
            sent.assert_not_called()
            chat.input.insert('end', '\n\nReescreva com mais contexto.')
            chat.submit()
            sent.assert_called_once_with('Olá Petra!\n\nTexto selecionado.\n\nReescreva com mais contexto.')
        finally:
            chat.close()
            destroy_test_root(root)

    def test_existing_draft_is_preserved_when_selection_is_added(self):
        root = ui.ctk.CTk()
        root.withdraw()
        sent = Mock()
        chat = ui.AgentChatWindow(root, None, sent, Mock())
        try:
            chat.input.insert('1.0', 'Meu pedido ainda não enviado')
            chat.prefill('Seleção nova')
            self.assertEqual('Meu pedido ainda não enviado\n\nSeleção nova', chat.input.get('1.0', 'end-1c'))
            sent.assert_not_called()
        finally:
            chat.close()
            destroy_test_root(root)


if __name__ == '__main__':
    unittest.main()
