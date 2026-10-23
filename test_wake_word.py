import hashlib
import importlib.machinery
import importlib.util
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

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
    RetryClock,
    SpeechEndpoint,
    WakeWordDetector,
    WakeWordEngine,
    ensure_models,
    required_model_files,
    resolve_wake_action,
    resolve_wake_sensitivity,
    resolve_wake_word,
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


def fake_detector(score=0.9):
    melspec = FakeSession("audio", lambda x: np.zeros((1, 1, 8, 32), dtype=np.float32))
    embedding = FakeSession("mel", lambda x: np.ones((1, 1, 1, 96), dtype=np.float32))
    wakeword = FakeSession("features", lambda x: np.array([[score]], dtype=np.float32))
    return WakeWordDetector(melspec, embedding, wakeword), (melspec, embedding, wakeword)


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

    def test_unknown_choices_fall_back_to_defaults(self):
        self.assertEqual("hey_jarvis", resolve_wake_word("ok_google"))
        self.assertEqual("medium", resolve_wake_sensitivity(None))
        self.assertEqual("dictation", resolve_wake_action("delete_files"))


class DetectorTests(unittest.TestCase):
    def test_scores_every_80ms_after_feature_window_and_warmup(self):
        detector, (melspec, embedding, wakeword) = fake_detector(0.9)
        scores = []
        for _ in range(FEATURE_WINDOW + WARMUP_PREDICTIONS + 3):
            scores += detector.process(np.zeros(CHUNK_SAMPLES // 2, dtype=np.float32))
            scores += detector.process(np.zeros(CHUNK_SAMPLES // 2, dtype=np.float32))
        self.assertEqual(FEATURE_WINDOW + WARMUP_PREDICTIONS + 3, len(scores))
        self.assertAlmostEqual(0.9, scores[-1], places=5)
        self.assertEqual(0.0, scores[FEATURE_WINDOW + WARMUP_PREDICTIONS - 2])
        self.assertEqual((1, CHUNK_SAMPLES + 480), melspec.inputs[-1].shape)
        self.assertEqual((1, 76, 32, 1), embedding.inputs[-1].shape)
        self.assertEqual((1, FEATURE_WINDOW, 96), wakeword.inputs[-1].shape)

    def test_reset_requires_a_new_warmup(self):
        detector, _sessions = fake_detector(0.9)
        audio = np.zeros(CHUNK_SAMPLES * (FEATURE_WINDOW + WARMUP_PREDICTIONS + 2), dtype=np.float32)
        self.assertAlmostEqual(0.9, max(detector.process(audio)), places=5)
        detector.reset()
        self.assertEqual(0.0, max(detector.process(audio[: CHUNK_SAMPLES * 4])))


class EngineTests(unittest.TestCase):
    def test_reports_detection_once_and_ignores_audio_from_before_reset(self):
        detected = threading.Event()
        scores = []
        detector = Mock()
        detector.process.side_effect = lambda audio: [0.8] if audio.max() > 1000 else [0.1]
        engine = WakeWordEngine(
            detector,
            0.5,
            lambda score: (scores.append(score), detected.set()),
            resampler_factory=lambda rate: (lambda samples: samples),
        )
        try:
            engine.feed(np.full(160, 0.01, dtype=np.float32), 16_000)
            engine.feed(np.full(160, 0.5, dtype=np.float32), 48_000)
            self.assertTrue(detected.wait(2))
            self.assertEqual([0.8], scores)
            detector.reset.assert_called()
        finally:
            engine.close()
            engine.thread.join(2)

    def test_threshold_change_applies_to_following_audio(self):
        detected = threading.Event()
        detector = Mock()
        detector.process.return_value = [0.4]
        engine = WakeWordEngine(detector, 0.5, lambda score: detected.set())
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
    def test_wake_preferences_default_off_and_stay_on_this_device(self):
        self.assertFalse(AppConfig.DEFAULTS["wake_word_enabled"])
        for key in ("wake_word_enabled", "wake_word", "wake_word_sensitivity", "wake_word_action"):
            self.assertIn(key, AppConfig.DEVICE_LOCAL_PREFERENCE_KEYS)
            self.assertNotIn(key, AppConfig.SYNCED_PREFERENCE_KEYS)
        self.assertEqual(16, len(AppConfig.SYNCED_PREFERENCE_KEYS))


def wake_app(action="dictation"):
    app = object.__new__(DITADO_LOCAL.DitadoLocalApp)
    app.config = Mock()
    app.config.get.side_effect = lambda key, default=None: {
        "wake_word_action": action,
        "wake_word": "hey_jarvis",
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

        app._start_wake_recording()

        listener.close.assert_called_once_with()
        self.assertIsNone(app.wake_stream)
        app.start_recording.assert_called_once_with("dictation")
        self.assertEqual(("wake", 1), app.active_hotkey_session)
        self.assertEqual(1, app.wake_endpoint[0])
        self.assertEqual("Cancelar", app.overlay.show.call_args.kwargs["action_label"])

    def test_detection_is_ignored_while_busy(self):
        app = wake_app()
        app.processing = True
        app._start_wake_recording()
        app.start_recording.assert_not_called()

    def test_pause_after_speech_transcribes(self):
        app = wake_app()
        app._start_wake_recording()
        app._finish_wake_recording((1, "end"))
        app.stop_recording.assert_called_once_with()
        self.assertIsNone(app.wake_endpoint)

    def test_silence_after_wake_word_discards_recording(self):
        app = wake_app()
        app._start_wake_recording()
        app._finish_wake_recording((1, "no_speech"))
        app._cancel_recording.assert_called_once_with()
        app.stop_recording.assert_not_called()

    def test_stale_endpoint_is_ignored_after_hotkey_takes_over(self):
        app = wake_app()
        app._start_wake_recording()
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

    def test_agent_action_opens_chat_and_records_question(self):
        app = wake_app("agent")
        chat = Mock()
        chat.is_open.return_value = True

        def open_chat():
            app.agent_chat_window = chat

        app._open_new_agent_chat = Mock(side_effect=open_chat)
        app._toggle_chat_recording = Mock(side_effect=lambda _chat: app.start_recording("agent_chat"))

        app._start_wake_recording()
        app._toggle_chat_recording.assert_called_once_with(chat)
        self.assertEqual(("wake", 1), app.active_hotkey_session)

        app._finish_wake_recording((1, "end"))
        self.assertEqual(2, app._toggle_chat_recording.call_count)


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
