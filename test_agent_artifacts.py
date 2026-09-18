import json
import unittest
from unittest.mock import Mock, patch

from ditado_ai import OllamaClient
from ditado_harness import check_budget, output_budget
from test_long_context import response


class AgentArtifactTests(unittest.TestCase):
    def test_informal_edit_preserves_raw_artifact_and_history(self):
        artifacts = [
            '  const key = "{{source - field}}";\n  const total = left - right;\n',
            '<section data-label="A - B">Ready</section>\n',
            '  def subtract(left, right):\n      return left - right\n',
            '{"label": "A - B", "enabled": true}\n',
        ]
        for artifact in artifacts:
            with self.subTest(artifact=artifact):
                client = OllamaClient()
                client.chat = Mock(return_value=artifact)
                client.chat_messages = Mock(return_value=artifact)
                result, conversation = client.start_selected_text_conversation(artifact, 'ajusta isso')
                self.assertEqual(artifact, result)
                self.assertEqual(artifact, conversation['messages'][-1]['content'])
                result, updated = client.continue_selected_text_conversation(conversation, 'mantém assim')
                self.assertEqual(artifact, result)
                self.assertEqual(artifact, updated['messages'][-1]['content'])

    def test_transport_reserves_the_same_expanded_budget_it_generates(self):
        messages = [{'role': 'system', 'content': 'Edit the supplied material.'},
                    {'role': 'user', 'content': 'const value = 1;\n' * 700}]
        with patch('ditado_ai.urllib.request.urlopen', side_effect=[
            response({'model_info': {'general.architecture': 'qwen', 'qwen.context_length': 65536}}),
            response({'message': {'content': 'Complete result'}, 'done_reason': 'stop'}),
        ]) as send:
            OllamaClient().chat_messages(messages)
        payload = json.loads(send.call_args.args[0].data)
        budget = payload['options']['num_predict']
        self.assertGreater(budget, 1400)
        self.assertEqual(output_budget(messages), budget)
        self.assertGreaterEqual(payload['options']['num_ctx'], check_budget(messages) + budget)
        self.assertEqual(messages, payload['messages'])

    def test_expanded_output_cannot_overflow_model_capacity(self):
        messages = [{'role': 'user', 'content': 'x' * 5000}]
        with patch('ditado_ai.urllib.request.urlopen', return_value=response({
            'model_info': {'general.architecture': 'small', 'small.context_length': 8192}
        })) as send:
            with self.assertRaisesRegex(ValueError, 'nenhum trecho foi cortado'):
                OllamaClient().chat_messages(messages)
        self.assertEqual(1, send.call_count)
        self.assertNotIn('messages', json.loads(send.call_args.args[0].data))


if __name__ == '__main__':
    unittest.main()
