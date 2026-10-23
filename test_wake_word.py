import hashlib
import importlib.machinery
import importlib.util
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

APP_PATH = Path(__file__).with_name("ditado_local.pyw")
sys.path.insert(0, str(APP_PATH.parent))
LOADER = importlib.machinery.SourceFileLoader("ditado_local", str(APP_PATH))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
DITADO_LOCAL = importlib.util.module_from_spec(SPEC)
LOADER.exec_module(DITADO_LOCAL)

import ditado_wakeword
from ditado_storage import AppConfig
from ditado_wakeword import (
    CHUNK_SAMPLES,
    FEATURE_WINDOW,
    WARMUP_PREDICTIONS,
    PhraseSpotter,
    RetryClock,
    SpeechBursts,
    SpeechEndpoint,
    WakeListeners,
    WakeWordDetector,
    WakeWordEngine,
    builtin_wake_words,
    custom_wake_phrases,
    ensure_models,
    phrase_score,
    required_model_files,
    resolve_wake_assignment,
    resolve_wake_sensitivity,
    resolve_wake_word,
    validate_wake_phrase,
    wake_word_actions,
)


class FakeSession:
    def __init__(self, name, run):
        self.name = name
        self._run = run
        self.inputs = []

    def get_inputs(self):
        return [SimpleNamespace(name=self.name)]

    def run(self, _outputs, feeds):
        value = feeds[self.name]
        self.inputs.append(value)
        return [self._run(value)]


def fake_detector(score=0.9, other_score=0.2):
    melspec = FakeSession("audio", lambda x: np.zeros((1, 1, 8, 32), dtype=np.float32))
    embedding = FakeSession("mel", lambda x: np.ones((1, 1, 1, 96), dtype=np.float32))
    wakeword = FakeSession("features", lambda x: np.array([[score]], dtype=np.float32))
    other = FakeSession("x", lambda x: np.array([[other_score]], dtype=np.float32))
    detector = WakeWordDetector(melspec, embedding, {"hey_jarvis": wakeword, "alexa": other})
    return detector, (melspec, embedding, wakeword)


class ModelDownloadTests(unittest.TestCase):
    def test_downloads_missing_models_and_verifies_signature(self):
        payloads = {name: name.encode() for name in required_model_files("hey_jarvis")}
        expected = {name: hashlib.sha256(data).hexdigest() for name, data in payloads.items()}
        original_features = dict(ditado_wakeword.FEATURE_MODELS)
        original_word = dict(ditado_wakeword.WAKE_WORDS["hey_jarvis"])
        try:
            for name in original_features:
                ditado_wakeword.FEATURE_MODELS[name] = expected[name]
            ditado_wakeword.WAKE_WORDS["hey_jarvis"]["sha256"] = expected[original_word["file"]]
            fetched = []

            def fetch(url, destination):
                name = url.rsplit("/", 1)[1]
                fetched.append(name)
                Path(destination).write_bytes(payloads[name])

            with tempfile.TemporaryDirectory() as directory:
                paths = ensure_models(directory, "hey_jarvis", fetch=fetch)
                self.assertEqual(set(payloads), set(paths))
                self.assertEqual(3, len(fetched))
                ensure_models(directory, "hey_jarvis", fetch=fetch)
                self.assertEqual(3, len(fetched), "verified files are not downloaded again")
        finally:
            ditado_wakeword.FEATURE_MODELS.update(original_features)
            ditado_wakeword.WAKE_WORDS["hey_jarvis"].update(original_word)

    def test_rejects_tampered_download_without_leaving_files(self):
        def fetch(_url, destination):
            Path(destination).write_bytes(b"tampered")

        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "assinatura"):
                ensure_models(directory, "alexa", fetch=fetch)
            self.assertEqual([], list(Path(directory).iterdir()))

    def test_two_words_share_one_set_of_feature_models(self):
        files = required_model_files(["hey_jarvis", "alexa"])
        self.assertEqual(4, len(files))
        self.assertEqual(files, required_model_files(("alexa", "hey_jarvis")))

    def test_unknown_choices_fall_back_to_defaults(self):
        self.assertEqual("hey_jarvis", resolve_wake_word("ok_google"))
        self.assertEqual("medium", resolve_wake_sensitivity(None))
        self.assertEqual(
            {"dictation": "hey_jarvis", "agent": "alexa"},
            resolve_wake_assignment("ok_google", None),
        )


class AssignmentTests(unittest.TestCase):
    def test_each_action_has_its_own_word_or_none(self):
        self.assertEqual(
            {"dictation": "alexa", "agent": None}, resolve_wake_assignment("alexa", "off"),
        )
        self.assertEqual(
            {"alexa": "dictation", "hey_jarvis": "agent"},
            wake_word_actions(resolve_wake_assignment("alexa", "hey_jarvis")),
        )

    def test_same_word_never_triggers_both_actions(self):
        self.assertEqual(
            {"dictation": "alexa", "agent": None}, resolve_wake_assignment("alexa", "alexa"),
        )

    def test_custom_phrase_needs_a_valid_typed_phrase(self):
        self.assertEqual(
            {"dictation": "hey_jarvis", "agent": None},
            resolve_wake_assignment("hey_jarvis", "custom", {"agent": "oi"}),
        )
        assignment = resolve_wake_assignment(
            "alexa", "custom", {"agent": "  Ok   computador "},
        )
        self.assertEqual({"dictation": "alexa", "agent": "custom_agent"}, assignment)
        self.assertEqual(("alexa",), builtin_wake_words(assignment))
        self.assertEqual(
            {"custom_agent": "Ok computador"},
            custom_wake_phrases(assignment, {"agent": "  Ok   computador "}),
        )

    def test_identical_custom_phrases_keep_only_dictation(self):
        assignment = resolve_wake_assignment(
            "custom", "custom", {"dictation": "Ok computador", "agent": "ok, computador!"},
        )
        self.assertEqual({"dictation": "custom_dictation", "agent": None}, assignment)


class PhraseMatchTests(unittest.TestCase):
    def test_accents_case_and_punctuation_are_ignored(self):
        self.assertEqual(1.0, phrase_score(" Olá, Assistente! ", "ola assistente"))

    def test_close_transcriptions_score_high_and_other_speech_low(self):
        self.assertGreater(phrase_score("Ok, computadô.", "Ok computador"), 0.8)
        self.assertGreater(phrase_score("Ei, ok computador", "Ok computador"), 0.9)
        self.assertLess(phrase_score("Bom dia, tudo bem?", "Ok computador"), 0.5)

    def test_sentence_containing_the_phrase_is_not_a_command(self):
        self.assertEqual(
            0.0, phrase_score("eu falei ok computador para ele ontem", "Ok computador"),
        )

    def test_phrase_validation(self):
        self.assertIsNone(validate_wake_phrase("Ok computador")[1])
        self.assertIsNotNone(validate_wake_phrase("oi")[1])
        self.assertIsNotNone(validate_wake_phrase("x" * 41)[1])
        self.assertEqual("Ok computador", validate_wake_phrase("  Ok   computador ")[0])


class SpeechBurstTests(unittest.TestCase):
    RATE = 16_000

    def block(self, level, seconds):
        return np.full(int(self.RATE * seconds), level, dtype=np.float32)

    def test_short_utterance_followed_by_pause_is_returned_once(self):
        bursts = SpeechBursts()
        out = bursts.feed(self.block(0.001, 0.5))
        out += bursts.feed(self.block(0.1, 0.8))
        self.assertEqual([], out)
        out += bursts.feed(self.block(0.001, 0.6))
        self.assertEqual(1, len(out))
        self.assertGreaterEqual(out[0].size, int(self.RATE * 0.8))
        self.assertEqual([], bursts.feed(self.block(0.001, 1.0)))

    def test_long_speech_and_clicks_are_ignored(self):
        bursts = SpeechBursts()
        out = bursts.feed(self.block(0.1, 4.0)) + bursts.feed(self.block(0.001, 0.6))
        out += bursts.feed(self.block(0.1, 0.1)) + bursts.feed(self.block(0.001, 0.6))
        self.assertEqual([], out)


class PhraseSpotterTests(unittest.TestCase):
    def test_transcribes_short_utterances_and_reports_the_matching_phrase(self):
        detected, heard = threading.Event(), []
        transcripts = iter(["bom dia", "Ok, computador."])
        spotter = PhraseSpotter(
            lambda audio: next(transcripts),
            {"custom_agent": "Ok computador"},
            0.8,
            lambda trigger, score: (heard.append(trigger), detected.set()),
        )
        try:
            for _ in range(2):
                spotter.feed(np.full(16_000, 0.1, dtype=np.float32), 16_000)
                spotter.feed(np.full(16_000, 0.001, dtype=np.float32), 16_000)
            self.assertTrue(detected.wait(2))
            self.assertEqual(["custom_agent"], heard)
        finally:
            spotter.close()
            spotter.thread.join(2)

    def test_listeners_share_feed_reset_and_sensitivity(self):
        first, second = Mock(), Mock()
        listeners = WakeListeners([first, second])
        listeners.feed("audio", 48_000)
        listeners.reset()
        listeners.set_sensitivity("high")
        for listener in (first, second):
            listener.feed.assert_called_once_with("audio", 48_000)
            listener.reset.assert_called_once_with()
            listener.set_sensitivity.assert_called_once_with("high")


class DetectorTests(unittest.TestCase):
    def test_scores_every_80ms_after_feature_window_and_warmup(self):
        detector, (melspec, embedding, wakeword) = fake_detector(0.9)
        scores = []
        for _ in range(FEATURE_WINDOW + WARMUP_PREDICTIONS + 3):
            scores += detector.process(np.zeros(CHUNK_SAMPLES // 2, dtype=np.float32))
            scores += detector.process(np.zeros(CHUNK_SAMPLES // 2, dtype=np.float32))
        self.assertEqual(FEATURE_WINDOW + WARMUP_PREDICTIONS + 3, len(scores))
        self.assertAlmostEqual(0.9, scores[-1]["hey_jarvis"], places=5)
        self.assertAlmostEqual(0.2, scores[-1]["alexa"], places=5)
        self.assertEqual(
            {"hey_jarvis": 0.0, "alexa": 0.0}, scores[FEATURE_WINDOW + WARMUP_PREDICTIONS - 2],
        )
        self.assertEqual((1, CHUNK_SAMPLES + 480), melspec.inputs[-1].shape)
        self.assertEqual((1, 76, 32, 1), embedding.inputs[-1].shape)
        self.assertEqual((1, FEATURE_WINDOW, 96), wakeword.inputs[-1].shape)

    def test_reset_requires_a_new_warmup(self):
        detector, _sessions = fake_detector(0.9)
        audio = np.zeros(CHUNK_SAMPLES * (FEATURE_WINDOW + WARMUP_PREDICTIONS + 2), dtype=np.float32)
        self.assertAlmostEqual(0.9, detector.process(audio)[-1]["hey_jarvis"], places=5)
        detector.reset()
        steps = detector.process(audio[: CHUNK_SAMPLES * 4])
        self.assertEqual(0.0, max(step["hey_jarvis"] for step in steps))


class EngineTests(unittest.TestCase):
    def test_reports_detection_once_and_ignores_audio_from_before_reset(self):
        detected = threading.Event()
        scores = []
        detector = Mock()
        detector.process.side_effect = lambda audio: (
            [{"hey_jarvis": 0.3, "alexa": 0.8}] if audio.max() > 1000
            else [{"hey_jarvis": 0.1, "alexa": 0.1}]
        )
        engine = WakeWordEngine(
            detector,
            0.5,
            lambda word, score: (scores.append((word, score)), detected.set()),
            resampler_factory=lambda rate: (lambda samples: samples),
        )
        try:
            engine.feed(np.full(160, 0.01, dtype=np.float32), 16_000)
            engine.feed(np.full(160, 0.5, dtype=np.float32), 48_000)
            self.assertTrue(detected.wait(2))
            self.assertEqual([("alexa", 0.8)], scores)
            detector.reset.assert_called()
        finally:
            engine.close()
            engine.thread.join(2)

    def test_threshold_change_applies_to_following_audio(self):
        detected = threading.Event()
        detector = Mock()
        detector.process.return_value = [{"hey_jarvis": 0.4}]
        engine = WakeWordEngine(detector, 0.5, lambda _word, _score: detected.set())
        try:
            engine.feed(np.zeros(160, dtype=np.float32), 16_000)
            self.assertFalse(detected.wait(0.3))
            engine.threshold = 0.35
            engine.feed(np.zeros(160, dtype=np.float32), 16_000)
            self.assertTrue(detected.wait(2))
        finally:
            engine.close()


class EndpointTests(unittest.TestCase):
    RATE = 16_000

    def block(self, level, seconds=0.1):
        return np.full(int(self.RATE * seconds), level, dtype=np.float32)

    def test_ends_after_speech_followed_by_silence(self):
        endpoint = SpeechEndpoint(self.RATE, silence_seconds=1.0)
        results = [endpoint.feed(self.block(0.1)) for _ in range(10)]
        results += [endpoint.feed(self.block(0.001)) for _ in range(10)]
        self.assertEqual("end", results[-1])
        self.assertTrue(all(result is None for result in results[:-1]))
        self.assertIsNone(endpoint.feed(self.block(0.001)))

    def test_pauses_shorter_than_limit_keep_recording(self):
        endpoint = SpeechEndpoint(self.RATE, silence_seconds=1.0)
        for _ in range(5):
            for _ in range(5):
                self.assertIsNone(endpoint.feed(self.block(0.1)))
            for _ in range(6):
                self.assertIsNone(endpoint.feed(self.block(0.001)))

    def test_cancels_when_nobody_speaks(self):
        endpoint = SpeechEndpoint(self.RATE, no_speech_seconds=2.0)
        results = [endpoint.feed(self.block(0.002)) for _ in range(20)]
        self.assertEqual("no_speech", results[-1])

    def test_steady_background_noise_does_not_hold_recording_open(self):
        endpoint = SpeechEndpoint(self.RATE, silence_seconds=1.0, no_speech_seconds=30.0)
        for _ in range(30):
            endpoint.feed(self.block(0.006))
        for _ in range(5):
            endpoint.feed(self.block(0.15))
        results = [endpoint.feed(self.block(0.007)) for _ in range(12)]
        self.assertIn("end", results)

    def test_stops_at_maximum_duration(self):
        endpoint = SpeechEndpoint(self.RATE, max_seconds=1.0)
        results = [endpoint.feed(self.block(0.2)) for _ in range(10)]
        self.assertEqual("max", results[-1])


class RetryClockTests(unittest.TestCase):
    def test_backs_off_and_resets(self):
        now = [100.0]
        retry = RetryClock(delays=(2.0, 5.0), clock=lambda: now[0])
        self.assertTrue(retry.ready())
        retry.failed()
        self.assertFalse(retry.ready())
        now[0] += 2.0
        self.assertTrue(retry.ready())
        retry.failed()
        now[0] += 4.0
        self.assertFalse(retry.ready())
        retry.succeeded()
        self.assertTrue(retry.ready())


class WakeConfigTests(unittest.TestCase):
    def test_wake_preferences_default_off_and_sync_with_the_account(self):
        self.assertFalse(AppConfig.DEFAULTS["wake_word_enabled"])
        for key in ("wake_word_enabled", "wake_word_dictation", "wake_word_agent",
                    "wake_phrase_dictation", "wake_phrase_agent", "wake_word_sensitivity",
                    "output_device_name", "windows_microphone_name"):
            self.assertIn(key, AppConfig.SYNCED_PREFERENCE_KEYS)
        self.assertEqual(set(), AppConfig.DEVICE_LOCAL_PREFERENCE_KEYS)
        self.assertEqual(24, len(AppConfig.SYNCED_PREFERENCE_KEYS))

    def test_single_word_setting_from_0_4_13_becomes_two_words(self):
        for action, expected in (("agent", ("alexa", "hey_jarvis")),
                                 ("dictation", ("hey_jarvis", "alexa"))):
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "config.json"
                path.write_text(
                    '{"wake_word": "hey_jarvis", "wake_word_action": "%s"}' % action,
                    encoding="utf-8",
                )
                config = AppConfig(path)
                self.assertEqual(
                    expected,
                    (config.get("wake_word_dictation"), config.get("wake_word_agent")),
                )
                self.assertNotIn("wake_word_action", AppConfig(path).data)


def wake_app(dictation="hey_jarvis", agent="alexa"):
    app = object.__new__(DITADO_LOCAL.DitadoLocalApp)
    app.config = Mock()
    app.config.get.side_effect = lambda key, default=None: {
        "wake_word_dictation": dictation,
        "wake_word_agent": agent,
    }.get(key, default)
    app.recording = False
    app.processing = False
    app.recording_mode = "dictation"
    app.input_sample_rate = 48_000
    app.wake_stream = Mock()
    app.wake_engine = Mock()
    app.wake_enabled = Mock()
    app.wake_enabled.get.return_value = True
    app.wake_loading = False
    app.wake_locked = False
    app.wake_status_text = Mock()
    app.wake_endpoint = None
    app.active_hotkey_session = None
    app.hotkey_session_counter = 0
    app.closing = False
    app.overlay = Mock()
    app.events = DITADO_LOCAL.queue.SimpleQueue()
    app.agent_chat_window = None
    app.wake_agent_selection = None

    def start(mode):
        app.recording = True
        app.recording_mode = mode

    app.start_recording = Mock(side_effect=start)
    app.stop_recording = Mock(side_effect=lambda: setattr(app, "recording", False))
    app._cancel_recording = Mock(side_effect=lambda: setattr(app, "recording", False))
    app.status = Mock()
    return app


class WakeRecordingTests(unittest.TestCase):
    def test_detection_closes_listener_and_starts_hands_free_dictation(self):
        app = wake_app()
        listener = app.wake_stream

        app._start_wake_recording("hey_jarvis")

        listener.close.assert_called_once_with()
        self.assertIsNone(app.wake_stream)
        app.start_recording.assert_called_once_with("dictation")
        self.assertEqual(("wake", 1), app.active_hotkey_session)
        self.assertEqual(1, app.wake_endpoint[0])
        self.assertEqual("Cancelar", app.overlay.show.call_args.kwargs["action_label"])

    def test_detection_is_ignored_while_busy(self):
        app = wake_app()
        app.processing = True
        app._start_wake_recording("hey_jarvis")
        app.start_recording.assert_not_called()

    def test_word_without_action_is_ignored(self):
        app = wake_app(agent="off")
        listener = app.wake_stream
        app._start_wake_recording("alexa")
        app.start_recording.assert_not_called()
        self.assertIs(listener, app.wake_stream)

    def test_pause_after_speech_transcribes(self):
        app = wake_app()
        app._start_wake_recording("hey_jarvis")
        app._finish_wake_recording((1, "end"))
        app.stop_recording.assert_called_once_with()
        self.assertIsNone(app.wake_endpoint)

    def test_silence_after_wake_word_discards_recording(self):
        app = wake_app()
        app._start_wake_recording("hey_jarvis")
        app._finish_wake_recording((1, "no_speech"))
        app._cancel_recording.assert_called_once_with()
        app.stop_recording.assert_not_called()

    def test_stale_endpoint_is_ignored_after_hotkey_takes_over(self):
        app = wake_app()
        app._start_wake_recording("hey_jarvis")
        app.active_hotkey_session = ("dictation", 7)
        app._finish_wake_recording((1, "end"))
        app.stop_recording.assert_not_called()

    def test_escape_cancels_only_a_wake_recording(self):
        app = wake_app()
        app.keys_down = set()
        app.active_hotkey_session = ("wake", 3)
        app._on_key_press(DITADO_LOCAL.pynput_keyboard.Key.esc)
        self.assertEqual(("cancel", ("wake", 3)), app.events.get_nowait())
        app.active_hotkey_session = ("dictation", 4)
        app.dictation_chord_active = False
        app.agent_chat_active = False
        app._on_key_press(DITADO_LOCAL.pynput_keyboard.Key.esc)
        self.assertTrue(app.events.empty())

    def test_capture_feeds_endpoint_and_requests_stop(self):
        app = wake_app()
        app.audio_chunks = []
        endpoint = Mock()
        endpoint.feed.return_value = "end"
        app.wake_endpoint = (5, endpoint)
        app._capture_audio(np.zeros((480, 1), dtype=np.float32), 480, None, None)
        self.assertEqual(("wake_stop", (5, "end")), app.events.get_nowait())

    def agent_app(self):
        app = wake_app()
        app.history = SimpleNamespace(path="perfil.json")
        app.agent_chat_profile_path = "perfil.json"
        app.agent_chat_entry_id = "entry:1"
        chat = Mock()
        chat.is_open.return_value = True
        chat.loading = False

        def open_chat():
            app.agent_chat_window = chat

        app._open_new_agent_chat = Mock(side_effect=open_chat)
        app._toggle_chat_recording = Mock(side_effect=lambda _chat: app.start_recording("agent_chat"))
        app.desktop = Mock()
        app.desktop.basic_focus.return_value = SimpleNamespace(window=0, control=0)
        return app, chat

    def selection_app(self, selected="Texto selecionado"):
        app, chat = self.agent_app()
        app.desktop.basic_focus.return_value = SimpleNamespace(window=123, control=456)
        app.desktop.capture_selection.return_value = (selected, None)
        app._focus_is_ditado = Mock(return_value=False)
        app.root = Mock()
        new_chat = Mock()
        new_chat.is_open.return_value = True
        new_chat.loading = False

        def create(text):
            app.agent_chat_window = new_chat
            app.agent_chat_entry_id = "draft:1"
            return new_chat

        app._create_agent_chat = Mock(side_effect=create)
        return app, chat, new_chat

    def deliver_selection(self, app):
        event, payload = app.events.get(timeout=2)
        self.assertEqual("wake_agent_selection", event)
        app._finish_wake_agent_selection(payload)

    def test_selected_text_goes_into_a_new_chat_with_the_question(self):
        app, _chat, new_chat = self.selection_app()

        app._start_wake_recording("alexa")
        self.assertEqual("agent_chat", app.recording_mode)
        app._create_agent_chat.assert_not_called()
        self.deliver_selection(app)

        app._create_agent_chat.assert_called_once_with("Texto selecionado")
        new_chat.set_recording.assert_called_once_with(True)
        self.assertEqual(("draft:1", "perfil.json"), app.voice_chat_target)
        app._finish_wake_recording((1, "end"))
        app._toggle_chat_recording.assert_called_once_with(new_chat)

    def test_pause_before_the_selection_is_read_finishes_after_the_chat_opens(self):
        app, _chat, new_chat = self.selection_app()
        app._start_wake_recording("alexa")
        app._finish_wake_recording((1, "end"))
        app._toggle_chat_recording.assert_not_called()

        self.deliver_selection(app)

        app._toggle_chat_recording.assert_called_once_with(new_chat)

    def test_selection_is_added_to_the_open_conversation(self):
        app, chat, _new_chat = self.selection_app("Outro trecho")
        app.agent_chat_window = chat

        app._start_wake_recording("alexa")
        self.deliver_selection(app)

        chat.prefill.assert_called_once_with("Outro trecho")
        app._create_agent_chat.assert_not_called()
        chat.set_recording.assert_called_once_with(True)
        self.assertEqual(("entry:1", "perfil.json"), app.voice_chat_target)

    def test_cancelled_recording_ignores_the_late_selection(self):
        app, _chat, _new_chat = self.selection_app()
        app._start_wake_recording("alexa")
        app.recording = False

        self.deliver_selection(app)

        app._create_agent_chat.assert_not_called()

    def test_agent_word_opens_chat_and_records_question(self):
        app, chat = self.agent_app()

        app._start_wake_recording("alexa")
        app._open_new_agent_chat.assert_called_once_with()
        app._toggle_chat_recording.assert_called_once_with(chat)
        self.assertEqual(("wake", 1), app.active_hotkey_session)

        app._finish_wake_recording((1, "end"))
        self.assertEqual(2, app._toggle_chat_recording.call_count)

    def test_agent_word_continues_the_open_chat(self):
        app, chat = self.agent_app()
        app.agent_chat_window = chat

        app._start_wake_recording("alexa")

        app._open_new_agent_chat.assert_not_called()
        chat._focus_input.assert_called_once_with()
        app._toggle_chat_recording.assert_called_once_with(chat)
        self.assertEqual("agent_chat", app.recording_mode)

    def test_agent_word_waits_while_the_agent_answers(self):
        app, chat = self.agent_app()
        app.agent_chat_window = chat
        chat.loading = True

        app._start_wake_recording("alexa")

        app._open_new_agent_chat.assert_not_called()
        app.start_recording.assert_not_called()
        self.assertIn("Aguarde", app.status.set.call_args.args[0])

    def test_closed_chat_starts_a_new_conversation(self):
        app, chat = self.agent_app()
        closed = Mock()
        closed.is_open.return_value = False
        app.agent_chat_window = closed

        app._start_wake_recording("alexa")

        app._open_new_agent_chat.assert_called_once_with()
        app._toggle_chat_recording.assert_called_once_with(chat)


class AudioDeviceTests(unittest.TestCase):
    def test_portaudio_restarts_only_when_the_microphones_change(self):
        app = wake_app()
        app.microphone = Mock()
        app._get_input_devices = Mock(return_value=[])
        app._select_saved_microphone = Mock()
        app._apply_windows_audio_defaults = Mock()
        signature = [("brio",)]
        with patch.object(DITADO_LOCAL, "sd") as audio, patch.object(
            DITADO_LOCAL, "capture_endpoint_signature", side_effect=lambda: signature[0],
        ):
            app._refresh_audio_devices()
            app._refresh_audio_devices()
            self.assertEqual(1, audio._terminate.call_count)
            app._apply_windows_audio_defaults.assert_not_called()

            signature[0] = ("brio", "bluetooth")
            app._refresh_audio_devices()
            self.assertEqual(2, audio._terminate.call_count)
            app._apply_windows_audio_defaults.assert_called_once_with()

            app._refresh_audio_devices(force=True)
            self.assertEqual(3, audio._terminate.call_count)

    def devices_app(self, output="", microphone=""):
        app = wake_app()
        app.config.get.side_effect = lambda key, default=None: {
            "output_device_name": output, "windows_microphone_name": microphone,
        }.get(key, default)
        app.playback_mute = SimpleNamespace(active=False)
        app._refresh_windows_device_options = Mock()
        return app

    def test_chosen_devices_become_the_windows_defaults(self):
        app = self.devices_app(output="Caixa", microphone="Webcam")
        with patch.object(DITADO_LOCAL, "set_default_endpoint", return_value=True) as setter:
            app._apply_windows_audio_defaults()
        self.assertEqual(
            [(("render", "Caixa"),), (("capture", "Webcam"),)],
            [call.args and (call.args,) for call in setter.call_args_list],
        )
        app.status.set.assert_not_called()

    def test_unchanged_choice_never_touches_windows(self):
        app = self.devices_app()
        with patch.object(DITADO_LOCAL, "set_default_endpoint") as setter:
            app._apply_windows_audio_defaults()
        setter.assert_not_called()

    def test_missing_device_keeps_the_windows_choice_and_warns(self):
        app = self.devices_app(output="Caixa desligada")
        with patch.object(DITADO_LOCAL, "set_default_endpoint", return_value=False):
            app._apply_windows_audio_defaults()
        self.assertIn("Caixa desligada", app.status.set.call_args.args[0])

    def test_defaults_are_not_changed_during_a_recording(self):
        app = self.devices_app(output="Caixa")
        app.recording = True
        with patch.object(DITADO_LOCAL, "set_default_endpoint") as setter:
            app._apply_windows_audio_defaults()
        setter.assert_not_called()


class WakeListenerLifecycleTests(unittest.TestCase):
    def test_listener_runs_only_while_idle_and_unlocked(self):
        app = wake_app()
        app.wake_lock_checked_at = 0.0
        original = DITADO_LOCAL.is_session_locked
        try:
            DITADO_LOCAL.is_session_locked = lambda: False
            self.assertTrue(app._wake_listener_wanted())
            app.recording = True
            self.assertFalse(app._wake_listener_wanted())
            app.recording = False
            app.wake_lock_checked_at = 0.0
            DITADO_LOCAL.is_session_locked = lambda: True
            app._update_wake_status = Mock()
            self.assertFalse(app._wake_listener_wanted())
            app._update_wake_status.assert_called_once_with()
        finally:
            DITADO_LOCAL.is_session_locked = original

    def test_microphone_failure_backs_off(self):
        app = wake_app()
        app.wake_stream = None
        app.wake_retry = RetryClock()
        app._refresh_audio_devices = Mock()
        app._selected_device = Mock(side_effect=RuntimeError("sem microfone"))
        app._start_wake_listener()
        self.assertIsNone(app.wake_stream)
        self.assertFalse(app.wake_retry.ready())
        self.assertIn("sem microfone", app.wake_status_text.set.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
