import json
import queue
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from ditado_ai import OllamaClient
from ditado_harness import NATURAL_PUNCTUATION_RULE, normalize_prose_punctuation
from test_ditado_local import DITADO_LOCAL as ui


class ProsePunctuationTests(unittest.TestCase):
    def test_parenthetical_separators_in_both_languages(self):
        cases = {
            'O relatório — já revisado — está pronto.': 'O relatório, já revisado, está pronto.',
            'Ready—please review.': 'Ready, please review.',
            'Ready – please review.': 'Ready, please review.',
            'Ready - please review.': 'Ready, please review.',
            'Ready -- please review.': 'Ready, please review.',
            'Ready--please review.': 'Ready, please review.',
            'Pronto —.': 'Pronto.',
            'Pronto —\nPode revisar.': 'Pronto\nPode revisar.',
            '- Primeiro — revisado.\n- Segundo - concluído.': '- Primeiro, revisado.\n- Segundo, concluído.',
            'Use `git status` — depois revise.': 'Use `git status`, depois revise.',
        }
        for source, expected in cases.items():
            with self.subTest(source=source):
                actual = normalize_prose_punctuation(source)
                self.assertEqual(expected, actual)
                self.assertEqual(actual, normalize_prose_punctuation(actual))

    def test_meaningful_hyphens_and_technical_literals_are_preserved(self):
        cases = [
            'segunda-feira, e-mail e follow-up; saldo -5; 10 - 5 = 5; 2025–2026.',
            '- Tarefa\n  - Subtarefa\n---\n— Fala em diálogo.',
            'Veja https://example.com/a—b e `a - b — c`.',
            '[link](https://example.com/a—b)',
            '```python\nx = "a — b"\n```\n~~~text\na - b\n~~~',
            '{"text": "a — b"}',
        ]
        for source in cases:
            with self.subTest(source=source):
                self.assertEqual(source, normalize_prose_punctuation(source))
        for mode in ('code', 'json'):
            self.assertEqual('a — b', normalize_prose_punctuation('a — b', {'output_mode': mode}))

    def test_agent_first_reply_and_followup_store_normalized_output(self):
        client = OllamaClient()
        client.chat = Mock(return_value='Pronto — pode revisar.')
        result, conversation = client.start_free_conversation('Diga se está pronto.')
        self.assertEqual('Pronto, pode revisar.', result)
        self.assertEqual(result, conversation['messages'][-1]['content'])
        self.assertIn(NATURAL_PUNCTUATION_RULE, client.chat.call_args.args[0])
        client.chat_messages = Mock(return_value='Está pronto - pode revisar.')
        result, conversation = client.continue_selected_text_conversation(conversation, 'Mais claro.')
        self.assertEqual('Está pronto, pode revisar.', result)
        self.assertEqual(result, conversation['messages'][-1]['content'])

    def test_grammar_normalizes_valid_and_rejected_revisions(self):
        client = OllamaClient()
        for candidate in ('Pronto — pode revisar.', 'Resposta sem relação nenhuma.'):
            client.chat = Mock(return_value=json.dumps({'corrected_text': candidate}))
            self.assertEqual('Pronto, pode revisar.', client.correct_grammar('Pronto — pode revisar.'))
            self.assertIn(NATURAL_PUNCTUATION_RULE, client.chat.call_args.args[0])

    def test_dictation_normalizes_when_grammar_disabled_failed_or_reintroduces_dash(self):
        for enabled, failure in ((False, False), (True, True), (True, False)):
            with self.subTest(enabled=enabled, failure=failure):
                app = object.__new__(ui.DitadoLocalApp)
                app._ensure_whisper_model = Mock()
                app.config = Mock()
                app.config.get.side_effect = lambda k, default=None: enabled if k == 'grammar_correction' else default
                app._resample_to_16khz = Mock(return_value=ui.np.zeros(16000))
                app.model = Mock()
                app.model.transcribe.return_value = ([SimpleNamespace(text='Pronto — pode revisar.')], {})
                app.ollama = Mock()
                if failure:
                    app.ollama.correct_grammar.side_effect = RuntimeError('offline')
                else:
                    app.ollama.correct_grammar.return_value = 'Pronto - pode revisar.'
                app.events = queue.SimpleQueue()
                app._transcribe_and_process(ui.np.zeros(16000), 'dictation')
                events = []
                while not app.events.empty():
                    events.append(app.events.get())
                finishes = [payload for event, payload in events if event == 'finish']
                self.assertEqual(1, len(finishes), events)
                self.assertEqual('Pronto, pode revisar.', finishes[0][0])


if __name__ == '__main__':
    unittest.main()
