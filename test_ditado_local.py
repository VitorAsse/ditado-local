import importlib.machinery
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


APP_PATH = Path(__file__).with_name("ditado_local.pyw")
sys.path.insert(0, str(APP_PATH.parent))
LOADER = importlib.machinery.SourceFileLoader("ditado_local", str(APP_PATH))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
DITADO_LOCAL = importlib.util.module_from_spec(SPEC)
LOADER.exec_module(DITADO_LOCAL)
from ditado_ai import OllamaClient, correction_prompt, select_voice_skill
import ditado_storage
from ditado_storage import AppConfig


class TranscriptionProfileTests(unittest.TestCase):
    def test_balanced_profile_uses_turbo_with_quality_preserving_search(self):
        profile = DITADO_LOCAL.resolve_transcription_profile("balanced")

        self.assertEqual(
            "dropbox-dash/faster-whisper-large-v3-turbo",
            profile["model"],
        )
        self.assertEqual(3, profile["beam_size"])

    def test_max_precision_profile_keeps_large_v3_and_full_search(self):
        profile = DITADO_LOCAL.resolve_transcription_profile("max_precision")

        self.assertEqual("large-v3", profile["model"])
        self.assertEqual(5, profile["beam_size"])

    def test_new_config_defaults_to_balanced_profile(self):
        self.assertEqual("balanced", AppConfig.DEFAULTS["transcription_profile"])

    def test_new_config_defaults_to_muting_playback_while_recording(self):
        self.assertTrue(AppConfig.DEFAULTS["mute_playback_while_recording"])

    def test_new_config_starts_without_personal_rules(self):
        self.assertEqual([], AppConfig.DEFAULTS["rules"])

    def test_new_config_does_not_monitor_unrelated_clipboard_content(self):
        self.assertFalse(AppConfig.DEFAULTS["capture_clipboard_history"])

    def test_new_config_detects_transcription_language_automatically(self):
        self.assertEqual("auto", AppConfig.DEFAULTS["transcription_language"])

    def test_transcription_language_can_be_automatic_or_fixed(self):
        self.assertIsNone(DITADO_LOCAL.resolve_transcription_language("auto"))
        self.assertEqual("pt", DITADO_LOCAL.resolve_transcription_language("pt"))

    def test_transcription_prompt_contains_only_user_vocabulary(self):
        prompt = correction_prompt(
            [{"wrong": "acme", "correct": "Acme"}]
        )

        self.assertIn("Acme", prompt)
        self.assertEqual(
            "Grafias preferidas / Preferred spellings: Acme.",
            prompt,
        )


class RuleConfigTests(unittest.TestCase):
    def test_user_can_create_and_reload_a_permanent_rule(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            app_root = Path(temporary_directory)
            config_path = app_root / "config.json"
            with (
                patch.object(ditado_storage, "APP_ROOT", app_root),
                patch.object(ditado_storage, "CONFIG_PATH", config_path),
            ):
                config = AppConfig()
                rule_id = config.save_rule(
                    None,
                    "Preservar o tom",
                    "Mantenha o nível de formalidade do texto selecionado.",
                )

                reloaded = AppConfig()
                rules = reloaded.get_rules(enabled_only=True)

        self.assertIsNotNone(rule_id)
        self.assertEqual(1, len(rules))
        self.assertEqual("Preservar o tom", rules[0]["name"])
        self.assertIn("formalidade", rules[0]["instructions"])


class RecordingRecoveryTests(unittest.TestCase):
    def test_refreshes_audio_devices_and_retries_after_open_error(self):
        app = object.__new__(DITADO_LOCAL.DitadoLocalApp)
        stale_device = {"index": 2, "name": "Viva voz", "sample_rate": 44_100}
        refreshed_device = {"index": 14, "name": "Viva voz", "sample_rate": 48_000}
        recovered_stream = object()

        app._selected_device = Mock(side_effect=[stale_device, refreshed_device])
        app._start_input_stream = Mock(
            side_effect=[
                DITADO_LOCAL.sd.PortAudioError("Error opening InputStream"),
                recovered_stream,
            ]
        )
        app._refresh_audio_devices = Mock()

        device, stream = app._open_input_stream_with_recovery()

        self.assertEqual(refreshed_device, device)
        self.assertIs(recovered_stream, stream)
        app._refresh_audio_devices.assert_called_once_with()
        self.assertEqual(2, app._start_input_stream.call_count)


class ModelRuntimeTests(unittest.TestCase):
    def test_missing_cuda_runtime_uses_cpu_without_attempting_gpu(self):
        app = object.__new__(DITADO_LOCAL.DitadoLocalApp)
        app.config = Mock()
        app.config.get.return_value = "balanced"
        app.model = None
        app.model_profile_id = None
        app.model_lock = DITADO_LOCAL.threading.Lock()

        with (
            patch.object(
                DITADO_LOCAL,
                "is_cuda_runtime_available",
                return_value=False,
            ),
            patch.object(DITADO_LOCAL, "WhisperModel") as whisper_model,
        ):
            app._ensure_whisper_model()

        whisper_model.assert_called_once()
        _model_name, = whisper_model.call_args.args
        self.assertEqual("small", _model_name)
        self.assertEqual("cpu", whisper_model.call_args.kwargs["device"])


class PlaybackMuteIntegrationTests(unittest.TestCase):
    def test_starting_dictation_mutes_playback_when_enabled(self):
        app = object.__new__(DITADO_LOCAL.DitadoLocalApp)
        app.recording = False
        app.processing = False
        app.target_window = None
        app.audio_chunks = []
        app.current_level = 0.0
        app.agent_selected_text = ""
        app.selection_ready = Mock()
        app._open_input_stream_with_recovery = Mock(
            return_value=({"sample_rate": 48_000}, object())
        )
        app.mute_playback_while_recording = Mock()
        app.mute_playback_while_recording.get.return_value = True
        app.playback_mute = Mock()
        app.status = Mock()
        app.status_dot = Mock()
        app.ready_badge = Mock()
        app.overlay = Mock()
        app._restore_target_window = Mock()
        app._show_error = Mock()

        app.start_recording("dictation")

        app.playback_mute.mute_for_recording.assert_called_once_with()

    def test_starting_dictation_keeps_playback_when_disabled(self):
        app = object.__new__(DITADO_LOCAL.DitadoLocalApp)
        app.recording = False
        app.processing = False
        app.target_window = None
        app.audio_chunks = []
        app.current_level = 0.0
        app.agent_selected_text = ""
        app._open_input_stream_with_recovery = Mock(
            return_value=({"sample_rate": 48_000}, object())
        )
        app.mute_playback_while_recording = Mock()
        app.mute_playback_while_recording.get.return_value = False
        app.playback_mute = Mock()
        app.status = Mock()
        app.status_dot = Mock()
        app.ready_badge = Mock()
        app.overlay = Mock()
        app._restore_target_window = Mock()
        app._show_error = Mock()

        app.start_recording("dictation")

        app.playback_mute.mute_for_recording.assert_not_called()

    def test_start_failure_after_muting_restores_playback(self):
        app = object.__new__(DITADO_LOCAL.DitadoLocalApp)
        app.recording = False
        app.processing = False
        app.target_window = None
        app.audio_chunks = []
        app.current_level = 0.0
        app.agent_selected_text = ""
        app._open_input_stream_with_recovery = Mock(
            return_value=({"sample_rate": 48_000}, Mock())
        )
        app.mute_playback_while_recording = Mock()
        app.mute_playback_while_recording.get.return_value = True
        app.playback_mute = Mock()
        app.status = Mock()
        app.status_dot = Mock()
        app.ready_badge = Mock()
        app.overlay = Mock()
        app.overlay.show.side_effect = RuntimeError("Window unavailable")
        app._restore_target_window = Mock()
        app._show_error = Mock()

        app.start_recording("dictation")

        app.playback_mute.restore.assert_called_once_with()

    def test_stopping_recording_restores_playback_before_processing(self):
        app = object.__new__(DITADO_LOCAL.DitadoLocalApp)
        app.recording = True
        app.processing = False
        app.stream = Mock()
        app.audio_chunks = [DITADO_LOCAL.np.zeros(10, dtype=DITADO_LOCAL.np.float32)]
        app.input_sample_rate = 16_000
        app.playback_mute = Mock()
        app._show_error = Mock()

        app.stop_recording()

        app.playback_mute.restore.assert_called_once_with()

    def test_exiting_the_app_restores_playback(self):
        app = object.__new__(DITADO_LOCAL.DitadoLocalApp)
        app.closing = False
        app.stream = None
        app.playback_mute = Mock()
        app.listener = Mock()
        app.tray_icon = Mock()
        app.root = Mock()

        app._exit_app()

        app.playback_mute.restore.assert_called_once_with()


class VoiceSkillRoutingTests(unittest.TestCase):
    def setUp(self):
        self.weekly_skill = {
            "id": "weekly-update",
            "name": "Weekly Update Formatter",
            "description": "Formata um resumo semanal de trabalho.",
            "triggers": ["weekly update", "resumo semanal"],
            "instructions": "Organize o texto como uma atualização semanal.",
            "examples": [],
            "enabled": True,
        }

    def test_unmatched_instruction_does_not_attach_active_skill_to_prompt(self):
        client = OllamaClient()
        client.chat = Mock(return_value="Texto mais curto.")

        client.transform_selected_text(
            "Texto selecionado.",
            "Deixe mais curto.",
            skills=[self.weekly_skill],
            selected_skill=None,
        )

        system_prompt = client.chat.call_args.args[0]
        self.assertNotIn("Weekly Update Formatter", system_prompt)
        self.assertNotIn("SKILLS DISPONÍVEIS", system_prompt)

    def test_grammar_review_preserves_the_text_language(self):
        client = OllamaClient()
        client.chat = Mock(return_value="This text is correct.")

        result = client.correct_grammar("This text are correct.")

        system_prompt = client.chat.call_args.args[0]
        self.assertEqual("This text is correct.", result)
        self.assertIn("idioma original", system_prompt)
        self.assertNotIn("revisor de português brasileiro", system_prompt)

    def test_matching_trigger_attaches_selected_skill_to_prompt(self):
        client = OllamaClient()
        client.chat = Mock(return_value="Weekly update pronto.")
        selected_skill = select_voice_skill(
            "Criar weekly update.",
            [self.weekly_skill],
        )

        client.transform_selected_text(
            "Texto selecionado.",
            "Criar weekly update.",
            skills=[self.weekly_skill],
            selected_skill=selected_skill,
        )

        system_prompt = client.chat.call_args.args[0]
        self.assertIn("Weekly Update Formatter", system_prompt)

    def test_english_selection_has_no_hidden_personal_rule(self):
        client = OllamaClient()
        client.chat = Mock(return_value="Você poderia compartilhar a atualização?")

        result = client.transform_selected_text(
            "Can you share the latest update?",
            "Deixe mais profissional.",
            skills=[],
            selected_skill=None,
            rules=[],
        )

        system_prompt = client.chat.call_args.args[0]
        self.assertEqual("Você poderia compartilhar a atualização?", result)
        self.assertEqual(1, client.chat.call_count)
        self.assertNotIn("REGRA OBRIGATORIA DE IDIOMA", system_prompt)

    def test_enabled_rule_is_applied_and_reviewed(self):
        client = OllamaClient()
        client.chat = Mock(
            side_effect=[
                "Você poderia compartilhar a atualização mais recente?",
                "Could you share the latest update?",
            ]
        )
        rules = [
            {
                "id": "preserve-english",
                "name": "Manter respostas em inglês",
                "instructions": (
                    "Quando o texto selecionado estiver em inglês, mantenha a resposta "
                    "em inglês, exceto quando a instrução pedir outro idioma."
                ),
                "enabled": True,
            }
        ]

        result = client.transform_selected_text(
            "Hey team, could you share the latest project update before our meeting?",
            "Deixe mais profissional e conciso.",
            skills=[],
            selected_skill=None,
            rules=rules,
        )

        self.assertEqual("Could you share the latest update?", result)
        self.assertEqual(2, client.chat.call_count)
        first_system_prompt = client.chat.call_args_list[0].args[0]
        review_system_prompt = client.chat.call_args_list[1].args[0]
        self.assertIn("Manter respostas em inglês", first_system_prompt)
        self.assertIn("REGRAS PERMANENTES DO USUÁRIO", first_system_prompt)
        self.assertIn("Manter respostas em inglês", review_system_prompt)

    def test_spoken_instruction_can_request_translation(self):
        client = OllamaClient()
        client.chat = Mock(
            return_value="Você poderia compartilhar a atualização mais recente?"
        )

        result = client.transform_selected_text(
            "Could you share the latest update?",
            "Traduza para português.",
            skills=[],
            selected_skill=None,
        )

        self.assertEqual(
            "Você poderia compartilhar a atualização mais recente?",
            result,
        )
        self.assertEqual(1, client.chat.call_count)

    def test_agent_retries_when_model_echoes_spoken_instruction(self):
        client = OllamaClient()
        client.chat = Mock(
            side_effect=[
                "Deixe mais curto.",
                "Texto mais curto.",
            ]
        )

        result = client.transform_selected_text(
            "Este é um texto selecionado que precisa ser reduzido.",
            "Deixe mais curto.",
            skills=[],
            selected_skill=None,
        )

        self.assertEqual("Texto mais curto.", result)
        self.assertEqual(2, client.chat.call_count)

    def test_agent_rejects_instruction_echo_after_retry(self):
        client = OllamaClient()
        client.chat = Mock(
            side_effect=[
                "Deixe mais curto.",
                "deixe mais curto",
            ]
        )

        with self.assertRaisesRegex(
            RuntimeError,
            "repetiu a instrução falada",
        ):
            client.transform_selected_text(
                "Este é um texto selecionado que precisa ser reduzido.",
                "Deixe mais curto.",
                skills=[],
                selected_skill=None,
            )


if __name__ == "__main__":
    unittest.main()
