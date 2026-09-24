import json
import queue
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from ditado_ai import OllamaClient, normalize_agent_conversation
from ditado_storage import HistoryStore
from test_ditado_local import DITADO_LOCAL as ui, destroy_test_root


class FreeAgentTests(unittest.TestCase):
    def test_message_selection_copies_only_selected_text_and_cannot_edit(self):
        import tkinter as tk
        root = ui.ctk.CTk()
        copied = Mock()
        _, _, conversation = self.conversation()
        chat = ui.AgentChatWindow(root, conversation, Mock(), copied)
        try:
            root.update()
            def descendants(widget):
                for child in widget.winfo_children():
                    yield child
                    yield from descendants(child)
            texts = [w for w in descendants(chat.messages_frame) if isinstance(w, tk.Text)]
            self.assertEqual(2, len(texts))
            for text in texts:
                original = text.get('1.0', 'end-1c')
                text.tag_add('sel', '1.0', '1.4')
                text.event_generate('<<Copy>>')
                copied.assert_called_with(original[:4])
                text.insert('1.0', 'INVALID')
                text.delete('1.0', 'end')
                self.assertEqual(original, text.get('1.0', 'end-1c'))
                self.assertGreater(text.winfo_height(), 1)
        finally:
            chat.close()
            destroy_test_root(root)

    def test_greeting_and_general_knowledge_are_not_rejected_as_transformations(self):
        client = OllamaClient()
        client.chat = Mock(return_value='Olá!')
        self.assertEqual('Olá!', client.start_free_conversation('Olá!')[0])
        client.chat = Mock(return_value='O evento aconteceu em 1989.')
        self.assertEqual('O evento aconteceu em 1989.', client.start_free_conversation('Resuma quando ocorreu o evento.')[0])

    def conversation(self):
        client = OllamaClient()
        client.chat = Mock(return_value='Você pode começar organizando as prioridades.')
        result, conversation = client.start_free_conversation('Como organizo meu dia?')
        return client, result, conversation

    def test_no_selection_is_required_only_for_explicit_free_chat(self):
        client, result, conversation = self.conversation()
        self.assertEqual('', conversation['original_text'])
        self.assertEqual('free', conversation['kind'])
        self.assertEqual(result, conversation['messages'][-1]['content'])
        self.assertIn('direct conversation', client.chat.call_args.args[0])
        with self.assertRaisesRegex(ValueError, 'Selecione'):
            client.start_selected_text_conversation('', 'Como organizo meu dia?')
        legacy = dict(conversation)
        legacy.pop('kind')
        self.assertIsNone(normalize_agent_conversation(legacy))

    def test_free_follow_up_answers_questions_and_survives_history_reload(self):
        client, result, conversation = self.conversation()
        client.chat_messages = Mock(return_value='16')
        _, updated = client.continue_selected_text_conversation(conversation, 'Quanto é 8 mais 8?')
        self.assertEqual('answer', updated['harness']['context']['task'])
        self.assertEqual('free', updated['kind'])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'history'
            store = HistoryStore(path=path)
            store.add('16', 'agent', updated)
            restored = HistoryStore(path=path).all()[0]['conversation']
            self.assertEqual(updated, normalize_agent_conversation(restored))

    def test_old_chat_chord_does_not_start_chat_or_dictation(self):
        app = object.__new__(ui.DitadoLocalApp)
        app.keys_down = set()
        app.dictation_chord_active = app.agent_chord_active = False
        app.events = queue.SimpleQueue()
        app._is_left_agent_chord_physically_down = Mock(return_value=False)
        key = ui.pynput_keyboard.Key
        for pressed in [key.ctrl_l, key.shift_l, key.space, key.space]:
            app._on_key_press(pressed)
        self.assertTrue(app.events.empty())
        app._on_key_release(key.space)
        app._on_key_release(key.shift_l)
        app._on_key_press(key.space)
        self.assertEqual('dictation', app.events.get()[1][0])

    def test_empty_chat_accepts_voice_then_shows_reply_and_continues(self):
        root = ui.ctk.CTk()
        root.withdraw()
        sent, voice = Mock(), Mock()
        chat = ui.AgentChatWindow(root, None, sent, Mock(), on_voice=voice)
        try:
            root.update()
            self.assertTrue(chat.window.winfo_viewable())
            self.assertIsNone(chat.conversation)
            chat.voice_button.invoke()
            voice.assert_called_once()
            chat.set_recording(True)
            chat.input.insert('1.0', 'Contexto: hoje tenho pouco tempo.')
            chat.submit_voice('Como organizo meu dia?')
            sent.assert_called_once_with('Contexto: hoje tenho pouco tempo.\nComo organizo meu dia?')
            self.assertTrue(chat.loading)
            _, _, conversation = self.conversation()
            chat.show_reply(conversation)
            chat.input.insert('1.0', 'Explique melhor.')
            chat.submit()
            self.assertEqual('Explique melhor.', sent.call_args.args[0])
        finally:
            chat.close()
            destroy_test_root(root)

    def test_free_voice_routes_transcription_to_chat_without_clipboard_or_selection(self):
        app = object.__new__(ui.DitadoLocalApp)
        app._ensure_whisper_model = Mock()
        app.config = Mock()
        app.config.get.side_effect = lambda k, default=None: default
        app._resample_to_16khz = Mock(return_value=ui.np.zeros(16000))
        app.model = Mock()
        app.model.transcribe.return_value = ([SimpleNamespace(text='Explique o que é uma API.')], {})
        app.voice_chat_target = ('draft:test', 'profile-a')
        app.events = queue.SimpleQueue()
        app.selection_ready = Mock()
        app._copy_to_clipboard = Mock()
        app._transcribe_and_process(ui.np.zeros(16000), 'agent_chat')
        event, payload = app.events.get()
        self.assertEqual('agent_chat_voice', event)
        self.assertEqual((app.voice_chat_target, 'Explique o que é uma API.'), payload)
        app.selection_ready.wait.assert_not_called()
        app._copy_to_clipboard.assert_not_called()

    def test_late_transcription_cannot_go_to_another_chat(self):
        app = object.__new__(ui.DitadoLocalApp)
        app.history = SimpleNamespace(path='profile-a')
        app.agent_chat_entry_id = 'draft:new'
        app.agent_chat_window = Mock()
        app.overlay = Mock()
        app._finish_chat_voice((('draft:old', 'profile-a'), 'old request'))
        app.agent_chat_window.submit_voice.assert_not_called()

    def test_first_reply_is_saved_without_pasting_and_then_updates_same_history(self):
        _, result, conversation = self.conversation()
        with tempfile.TemporaryDirectory() as directory:
            app = object.__new__(ui.DitadoLocalApp)
            app.history = HistoryStore(path=Path(directory) / 'history')
            app.agent_chat_entry_id = 'draft:new'
            app.agent_chat_window = Mock()
            app.status = Mock()
            app.tabs = Mock()
            app._copy_to_clipboard = Mock()
            app._finish_agent_chat_reply(('draft:new', result, conversation, str(app.history.path)))
            first_id = app.agent_chat_entry_id
            self.assertFalse(first_id.startswith('draft:'))
            app._finish_agent_chat_reply((first_id, result, conversation, str(app.history.path)))
            self.assertEqual(1, len(app.history.all()))
            app._copy_to_clipboard.assert_not_called()
            app._finish_agent_chat_reply(('draft:wrong-account', result, conversation, 'another-profile'))
            self.assertEqual(1, len(app.history.all()))

    def test_closing_voice_chat_cancels_microphone(self):
        app = object.__new__(ui.DitadoLocalApp)
        chat = object()
        app.agent_chat_window = chat
        app.recording = True
        app.recording_mode = 'agent_chat'
        app._cancel_recording = Mock()
        app._close_chat_recording(chat)
        app._cancel_recording.assert_called_once()


if __name__ == '__main__':
    unittest.main()
