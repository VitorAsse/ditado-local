import json
import unittest
from unittest.mock import Mock, patch
from ditado_ai import OllamaClient, normalize_agent_conversation
from ditado_harness import CONTEXT_TOKENS, OUTPUT_TOKENS, check_budget


def response(payload):
    result = Mock()
    result.__enter__ = Mock(return_value=result)
    result.__exit__ = Mock(return_value=False)
    result.read.return_value = json.dumps(payload).encode()
    return result


class LongContextTests(unittest.TestCase):
    def test_large_transport_preserves_tail_and_reserves_output(self):
        text = ('Texto para revisao. ' * 1400) + 'FIM_UNICO'
        messages = [{'role': 'user', 'content': text}]
        client = OllamaClient()
        with patch('ditado_ai.urllib.request.urlopen', side_effect=[
            response({'model_info': {'general.architecture': 'qwen3', 'qwen3.context_length': 262144}}),
            response({'message': {'content': 'Pronto.'}}),
            response({'message': {'content': 'Pronto.'}}),
        ]) as send:
            client.chat_messages(messages)
            client.chat_messages(messages)
        self.assertEqual(3, send.call_count)  # Capability is cached.
        body = json.loads(send.call_args_list[1].args[0].data)
        self.assertEqual(messages, body['messages'])
        self.assertGreater(body['options']['num_ctx'], CONTEXT_TOKENS)
        self.assertGreaterEqual(body['options']['num_ctx'], check_budget(messages) + OUTPUT_TOKENS)
        self.assertEqual(300, send.call_args_list[1].kwargs['timeout'])

    def test_small_requests_keep_small_context_and_need_no_metadata(self):
        with patch('ditado_ai.urllib.request.urlopen', return_value=response({'message': {'content': 'OK'}})) as send:
            OllamaClient().chat('System', 'Hello')
        self.assertEqual(1, send.call_count)
        self.assertEqual(CONTEXT_TOKENS, json.loads(send.call_args.args[0].data)['options']['num_ctx'])

    def test_smaller_model_rejects_without_sending_source(self):
        with patch('ditado_ai.urllib.request.urlopen', return_value=response({
            'model_info': {'general.architecture': 'small', 'small.context_length': 8192}})) as send:
            with self.assertRaises(ValueError):
                OllamaClient(model='small').chat('System', 'a' * 18000)
        self.assertEqual(1, send.call_count)
        self.assertNotIn('messages', json.loads(send.call_args.args[0].data))

    def test_unknown_capacity_does_not_silently_truncate(self):
        with patch('ditado_ai.urllib.request.urlopen', return_value=response({})) as send:
            with self.assertRaises(RuntimeError):
                OllamaClient().chat('System', 'a' * 18000)
        self.assertEqual(1, send.call_count)

    def test_large_prefill_survives_save_and_followup(self):
        text = ('Texto selecionado. ' * 1400) + 'FIM_UNICO'
        client = OllamaClient()
        client.chat = Mock(return_value='Pronto.')
        client.chat_messages = Mock(return_value='Ajustado.')
        _, conversation = client.start_free_conversation(text)
        restored = normalize_agent_conversation(json.loads(json.dumps(conversation)))
        self.assertEqual(text, restored['messages'][0]['content'])
        _, updated = client.continue_selected_text_conversation(restored, 'Deixe mais curto.')
        self.assertEqual(text, updated['messages'][0]['content'])
        self.assertIn('FIM_UNICO', client.chat_messages.call_args.args[0][1]['content'])

    def test_unicode_overflow_is_rejected_before_transport(self):
        with patch('ditado_ai.urllib.request.urlopen') as send:
            with self.assertRaises(ValueError):
                OllamaClient().chat('System', '\U0001f600' * 20000)
        send.assert_not_called()


if __name__ == '__main__':
    unittest.main()
