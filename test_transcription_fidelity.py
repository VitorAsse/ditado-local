import json
import unittest
from unittest.mock import Mock, patch

from ditado_ai import OllamaClient, _is_safe_grammar_revision, correction_prompt, transcription_vocabulary
from ditado_harness import output_issues, request_context


class TranscriptionFidelityTests(unittest.TestCase):
    def test_grammar_cannot_change_negation_environment_or_tense(self):
        pairs = [
            ('Eu não quero publicar o deploy hoje.', 'Eu quero publicar o deploy hoje.'),
            ('Execute o teste no staging.', 'Execute o teste na produção.'),
            ('O teste foi concluído.', 'O teste será concluído.'),
            ('O valor é -15.', 'O valor é 15.'),
            ('Use apenas a versão 2.', 'Use a versão 2.'),
            ('Envie sem publicar.', 'Envie e publique.'),
        ]
        for original, candidate in pairs:
            with self.subTest(original=original):
                self.assertFalse(_is_safe_grammar_revision(original, candidate))

    def test_grammar_preserves_technical_phrases_urls_and_identifiers(self):
        pairs = [
            ('O refresh token continua válido.', 'O token de refresh continua válido.'),
            ('Verifique https://example.test/api.', 'Verifique https://example.test/new.'),
            ('O user_id deve ser preservado.', 'O user_name deve ser preservado.'),
            ('Use o método getUser hoje.', 'Use o método getName hoje.'),
            ('O servidor usa AcmeDB.', 'O servidor usa outro banco.'),
        ]
        for original, candidate in pairs:
            with self.subTest(original=original):
                self.assertFalse(_is_safe_grammar_revision(original, candidate, ['AcmeDB']))

    def test_valid_grammar_and_accents_still_work(self):
        self.assertTrue(_is_safe_grammar_revision('This text are correct.', 'This text is correct.'))
        self.assertTrue(_is_safe_grammar_revision('Qual e a capital da França?', 'Qual é a capital da França?'))
        self.assertTrue(_is_safe_grammar_revision('O refresh token esta valido.', 'O refresh token está válido.'))

    def test_rejected_revision_returns_original(self):
        client = OllamaClient()
        client.chat = Mock(return_value=json.dumps({'corrected_text': 'Publique em produção.'}))
        self.assertEqual('Não publique em produção.', client.correct_grammar('Não publique em produção.'))

    def test_three_natural_portuguese_requests_resolve_same_recipient(self):
        requests = [
            'Escreva uma mensagem minha para o Bruno, em português.',
            'Escreva uma mensagem para Bruno em português pedindo a revisão.',
            'Escreva uma mensagem para Bruno, em português, pedindo a revisão.',
        ]
        for instruction in requests:
            with self.subTest(instruction=instruction):
                context = request_context(instruction, '', {'display_name': 'Vitor'})
                self.assertEqual('Bruno', context['target_recipient'])
                self.assertEqual('pt', context['explicit_language'])

    def test_unrecognized_request_does_not_invent_recipient(self):
        context = request_context('Reescreva uma mensagem mais natural.', '')
        self.assertIsNone(context['target_recipient'])

    def test_english_technical_terms_do_not_translate_portuguese_message(self):
        context = request_context('Deixe mais natural, preservando os termos técnicos em inglês.', 'O teste ainda não foi concluído.')
        self.assertIsNone(context['explicit_language'])
        context = request_context('Escreva em português e preserve os nomes em inglês.', '')
        self.assertEqual('pt', context['explicit_language'])
        context = request_context('Traduza para inglês.', '')
        self.assertEqual('en', context['explicit_language'])

    def test_glossary_is_a_hint_not_an_automatic_replacement(self):
        corrections = [{'wrong': 'acme', 'correct': 'Acme'}]
        self.assertEqual(['Acme', 'webhook'], transcription_vocabulary(corrections, ['Acme', 'webhook']))
        prompt = correction_prompt(corrections, ['webhook'], 'pt')
        self.assertIn('Português brasileiro', prompt)
        self.assertIn('Acme, webhook', prompt)
        self.assertEqual(corrections, [{'wrong': 'acme', 'correct': 'Acme'}])

    def test_rewrite_rejects_new_causation_between_independent_facts(self):
        source = 'Não publique. O teste no staging falhou.'
        request = 'Reescreva em português.'
        context = request_context(request, source)
        self.assertIn('unsupported_causal_link', output_issues(
            'Não publique porque o teste no staging falhou.', context, source, request))
        self.assertNotIn('unsupported_causal_link', output_issues(
            'Não publique. O teste no staging falhou.', context, source, request))

    def test_stated_reasons_and_conversation_referrals_are_allowed(self):
        source = 'Não publique porque o teste falhou.'
        context = request_context('Reescreva.', source)
        self.assertNotIn('unsupported_causal_link', output_issues(source, context, source, 'Reescreva.'))
        source = 'Morgan: I need the export.\nSam: Ask Alex; my access is unavailable.'
        context = request_context('Write to Alex.', source, {'display_name': 'Morgan'})
        self.assertNotIn('unsupported_causal_link', output_issues(
            'Sam referred me because their access is unavailable.', context, source, 'Write to Alex.'))

    def test_unrepairable_causal_edit_preserves_original_facts(self):
        client = OllamaClient()
        client.chat = Mock(return_value='Não publique porque o teste falhou.')
        client.chat_messages = Mock(return_value='Não publique porque o teste falhou.')
        source = 'Não publique. O teste falhou.'
        result, _ = client.start_selected_text_conversation(source, 'Reescreva de forma natural.')
        self.assertEqual(source, result)
        self.assertTrue(client.last_diagnostics['fallback_original'])

    def test_new_model_uses_non_thinking_and_configured_residency(self):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = b'{"message":{"content":"OK"},"load_duration":100}'
        with patch('ditado_ai.urllib.request.urlopen', return_value=response) as send:
            client = OllamaClient(model='qwen3.5:4b', keep_alive='2h')
            client.chat('System', 'Hello')
        body = json.loads(send.call_args.args[0].data)
        self.assertFalse(body['think'])
        self.assertEqual('2h', body['keep_alive'])
        self.assertEqual(100, client.last_call_metrics['load_duration'])


if __name__ == '__main__':
    unittest.main()
