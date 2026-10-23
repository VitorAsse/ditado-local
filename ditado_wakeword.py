"""Local wake word activation and automatic end of speech.

Runs the openWakeWord ONNX pipeline (melspectrogram -> embedding -> wake word
classifier) with onnxruntime, without the openwakeword package and its
training dependencies. Audio never leaves the computer and is not stored while
listening; only a few seconds of features stay in memory.
https://github.com/dscripka/openWakeWord

Custom phrases have no trained model: short utterances followed by a pause are
transcribed locally and compared with the phrase the user typed.
"""
import ctypes
import hashlib
import os
import queue
import re
import threading
import time
import unicodedata
from collections import deque
from difflib import SequenceMatcher
from pathlib import Path

import numpy as np

MODEL_RELEASE_URL = "https://github.com/dscripka/openWakeWord/releases/download/v0.5.1"
WAKE_SAMPLE_RATE = 16_000
CHUNK_SAMPLES = 1280  # 80 ms, the native step of the openWakeWord models
MEL_CONTEXT_SAMPLES = 160 * 3
MEL_WINDOW = 76
MEL_STEP = 8
FEATURE_WINDOW = 16
WARMUP_PREDICTIONS = 5

FEATURE_MODELS = {
    "melspectrogram.onnx": "ba2b0e0f8b7b875369a2c89cb13360ff53bac436f2895cced9f479fa65eb176f",
    "embedding_model.onnx": "70d164290c1d095d1d4ee149bc5e00543250a7316b59f31d056cff7bd3075c1f",
}
WAKE_WORDS = {
    "hey_jarvis": {
        "display_name": "Hey Jarvis",
        "file": "hey_jarvis_v0.1.onnx",
        "sha256": "94a13cfe60075b132f6a472e7e462e8123ee70861bc3fb58434a73712ee0d2cb",
    },
    "alexa": {
        "display_name": "Alexa",
        "file": "alexa_v0.1.onnx",
        "sha256": "6ff566a01d12670e8d9e3c59da32651db1575d17272a601b7f8a39283dfbae3e",
    },
}
DEFAULT_WAKE_WORD = "hey_jarvis"
WAKE_SENSITIVITIES = {
    "low": {"display_name": "Baixa", "threshold": 0.7},
    "medium": {"display_name": "Média", "threshold": 0.5},
    "high": {"display_name": "Alta", "threshold": 0.35},
}
DEFAULT_WAKE_SENSITIVITY = "medium"
WAKE_WORD_OFF = "off"
WAKE_WORD_OFF_LABEL = "Desligado"
WAKE_WORD_CUSTOM = "custom"
WAKE_WORD_CUSTOM_LABEL = "Personalizado"
CUSTOM_TRIGGER_PREFIX = "custom_"
DEFAULT_WAKE_ASSIGNMENT = {"dictation": "hey_jarvis", "agent": "alexa"}
# Minimum similarity between what was heard and the typed phrase.
PHRASE_SIMILARITY = {"low": 0.9, "medium": 0.8, "high": 0.7}
MAX_PHRASE_CHARS = 40


def resolve_wake_word(value):
    return value if value in WAKE_WORDS else DEFAULT_WAKE_WORD


def resolve_wake_sensitivity(value):
    return value if value in WAKE_SENSITIVITIES else DEFAULT_WAKE_SENSITIVITY


def normalize_phrase(text):
    """Lowercase words without accents or punctuation, for comparing speech."""
    text = unicodedata.normalize("NFKD", str(text or "").casefold())
    text = "".join(char for char in text if not unicodedata.combining(char))
    return " ".join(re.findall(r"[a-z0-9]+", text))


def validate_wake_phrase(text):
    """Return (clean phrase, error message or None)."""
    clean = " ".join(str(text or "").split())
    if len(clean) > MAX_PHRASE_CHARS:
        return clean, f"Use uma frase de até {MAX_PHRASE_CHARS} caracteres."
    if len(normalize_phrase(clean).replace(" ", "")) < 5:
        return clean, "Use uma frase com duas ou mais palavras ou sílabas fortes, como “Ok computador”."
    return clean, None


def phrase_score(heard, phrase):
    """Similarity from 0 to 1 between a short utterance and the custom phrase.

    A sentence that merely contains the phrase is ordinary speech, so the
    utterance may have at most two extra words.
    """
    heard, phrase = normalize_phrase(heard), normalize_phrase(phrase)
    if not heard or not phrase:
        return 0.0
    words, size = heard.split(), len(phrase.split())
    if len(words) > size + 2:
        return 0.0
    best = SequenceMatcher(None, heard, phrase).ratio()
    for start in range(len(words) - size + 1):
        window = " ".join(words[start:start + size])
        best = max(best, SequenceMatcher(None, window, phrase).ratio())
    return best


def resolve_wake_assignment(dictation, agent, phrases=None):
    """Return {action: trigger or None}; one trigger never serves both actions.

    Triggers are built-in word keys or custom_<action> for a typed phrase.
    """
    phrases = phrases or {}
    assignment = {}
    for action, value in (("dictation", dictation), ("agent", agent)):
        if value == WAKE_WORD_OFF:
            assignment[action] = None
        elif value == WAKE_WORD_CUSTOM:
            valid = validate_wake_phrase(phrases.get(action))[1] is None
            assignment[action] = CUSTOM_TRIGGER_PREFIX + action if valid else None
        elif value in WAKE_WORDS:
            assignment[action] = value
        else:
            assignment[action] = DEFAULT_WAKE_ASSIGNMENT[action]
    if assignment["agent"] is not None and assignment["agent"] == assignment["dictation"]:
        assignment["agent"] = None
    if (assignment["agent"] and assignment["dictation"]
            and assignment["agent"].startswith(CUSTOM_TRIGGER_PREFIX)
            and assignment["dictation"].startswith(CUSTOM_TRIGGER_PREFIX)
            and normalize_phrase(phrases.get("agent")) == normalize_phrase(phrases.get("dictation"))):
        assignment["agent"] = None
    return assignment


def wake_word_actions(assignment):
    """Map each listened trigger to its action."""
    return {word: action for action, word in assignment.items() if word is not None}


def builtin_wake_words(assignment):
    return tuple(sorted(word for word in assignment.values() if word in WAKE_WORDS))


def custom_wake_phrases(assignment, phrases):
    """{trigger: phrase} for the actions that use a typed phrase."""
    return {
        trigger: " ".join(str(phrases.get(action) or "").split())
        for action, trigger in assignment.items()
        if trigger and trigger.startswith(CUSTOM_TRIGGER_PREFIX)
    }


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _default_fetch(url, destination):
    import httpx

    with httpx.stream("GET", url, follow_redirects=True, timeout=60.0) as response:
        response.raise_for_status()
        with open(destination, "wb") as handle:
            for block in response.iter_bytes():
                handle.write(block)


def required_model_files(wake_words):
    if isinstance(wake_words, str):
        wake_words = [wake_words]
    files = dict(FEATURE_MODELS)
    for word in wake_words:
        entry = WAKE_WORDS[resolve_wake_word(word)]
        files[entry["file"]] = entry["sha256"]
    return files


def ensure_models(directory, wake_words, fetch=None):
    """Download missing models once and verify every file by SHA-256."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    fetch = fetch or _default_fetch
    paths = {}
    for name, expected in required_model_files(wake_words).items():
        path = directory / name
        if not path.exists() or _sha256(path) != expected:
            temporary = path.with_suffix(path.suffix + ".download")
            try:
                fetch(f"{MODEL_RELEASE_URL}/{name}", temporary)
                actual = _sha256(temporary)
                if actual != expected:
                    raise ValueError(f"O modelo {name} baixado não confere com a assinatura esperada.")
                os.replace(temporary, path)
            finally:
                if temporary.exists():
                    temporary.unlink()
        paths[name] = path
    return paths


class WakeWordDetector:
    """Streaming openWakeWord inference over 16 kHz audio in int16 scale.

    Several wake words share the melspectrogram and embedding steps; only the
    small classifier runs once per word.
    """

    def __init__(self, melspec_session, embedding_session, wakeword_sessions):
        self.melspec = melspec_session
        self.embedding = embedding_session
        self.wakewords = dict(wakeword_sessions)
        self.melspec_input = melspec_session.get_inputs()[0].name
        self.embedding_input = embedding_session.get_inputs()[0].name
        self.wakeword_inputs = {
            word: session.get_inputs()[0].name for word, session in self.wakewords.items()
        }
        self.reset()

    @classmethod
    def from_files(cls, paths, wake_words):
        import onnxruntime as ort

        options = ort.SessionOptions()
        options.inter_op_num_threads = 1
        options.intra_op_num_threads = 1

        def session(name):
            return ort.InferenceSession(
                str(paths[name]),
                sess_options=options,
                providers=["CPUExecutionProvider"],
            )

        return cls(
            session("melspectrogram.onnx"),
            session("embedding_model.onnx"),
            {word: session(WAKE_WORDS[word]["file"]) for word in wake_words},
        )

    def reset(self):
        self.raw = np.zeros(0, dtype=np.float32)
        self.pending = np.zeros(0, dtype=np.float32)
        self.mel = np.ones((MEL_WINDOW, 32), dtype=np.float32)
        self.features = np.zeros((0, 96), dtype=np.float32)
        self.predictions = 0

    def process(self, samples):
        """Feed int16-scaled float samples; return {word: score} per 80 ms step."""
        self.pending = np.concatenate((self.pending, np.asarray(samples, dtype=np.float32)))
        scores = []
        while self.pending.size >= CHUNK_SAMPLES:
            chunk, self.pending = self.pending[:CHUNK_SAMPLES], self.pending[CHUNK_SAMPLES:]
            scores.append(self._step(chunk))
        return scores

    def _step(self, chunk):
        silent = {word: 0.0 for word in self.wakewords}
        self.raw = np.concatenate((self.raw, chunk))[-(CHUNK_SAMPLES + MEL_CONTEXT_SAMPLES):]
        if self.raw.size < CHUNK_SAMPLES + MEL_CONTEXT_SAMPLES:
            return silent
        mel = self.melspec.run(None, {self.melspec_input: self.raw[None, :]})[0]
        mel = np.squeeze(mel).reshape(-1, 32) / 10.0 + 2.0
        self.mel = np.vstack((self.mel, mel.astype(np.float32)))[-(MEL_WINDOW + MEL_STEP * 10):]
        window = self.mel[-MEL_WINDOW:][None, :, :, None]
        embedding = self.embedding.run(None, {self.embedding_input: window})[0]
        self.features = np.vstack((self.features, embedding.reshape(1, 96)))[-(FEATURE_WINDOW * 4):]
        if self.features.shape[0] < FEATURE_WINDOW:
            return silent
        window = self.features[-FEATURE_WINDOW:][None, :, :]
        scores = {
            word: float(np.ravel(session.run(None, {self.wakeword_inputs[word]: window})[0])[0])
            for word, session in self.wakewords.items()
        }
        self.predictions += 1
        return scores if self.predictions > WARMUP_PREDICTIONS else silent


class WakeWordEngine:
    """Background worker: resamples microphone blocks and reports detections.

    on_detect receives the detected word and its score.
    """

    def __init__(self, detector, threshold, on_detect, resampler_factory=None):
        self.detector = detector
        self.threshold = float(threshold)
        self.thresholds = {key: item["threshold"] for key, item in WAKE_SENSITIVITIES.items()}
        self.on_detect = on_detect
        self.resampler_factory = resampler_factory or _soxr_stream
        self.queue = queue.SimpleQueue()
        self.generation = 0
        self.closed = False
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def feed(self, samples, sample_rate):
        """Called from the audio callback; never blocks."""
        if not self.closed:
            self.queue.put((self.generation, int(sample_rate), np.array(samples, dtype=np.float32)))

    def reset(self):
        self.generation += 1
        self.queue.put(("reset", self.generation))

    def close(self):
        self.closed = True
        self.queue.put(None)

    def set_sensitivity(self, sensitivity):
        self.threshold = float(self.thresholds[resolve_wake_sensitivity(sensitivity)])

    def _run(self):
        generation, resampler, rate = 0, None, None
        while True:
            item = self.queue.get()
            if item is None:
                return
            if item[0] == "reset":
                generation, resampler, rate = item[1], None, None
                self.detector.reset()
                continue
            item_generation, sample_rate, samples = item
            if item_generation != generation:
                continue
            if sample_rate != rate:
                rate = sample_rate
                resampler = None if rate == WAKE_SAMPLE_RATE else self.resampler_factory(rate)
            audio = samples if resampler is None else resampler(samples)
            try:
                scores = self.detector.process(np.clip(audio, -1.0, 1.0) * 32767.0)
            except Exception:
                continue
            best = max(
                ((score, word) for step in scores for word, score in step.items()),
                default=None,
            )
            if best is not None and best[0] >= self.threshold:
                self.detector.reset()
                resampler, rate = None, None
                self.on_detect(best[1], best[0])


class SpeechBursts:
    """Splits 16 kHz audio into short utterances that end with a pause.

    Utterances longer than max_seconds are ordinary speech and are dropped
    without being kept in memory.
    """

    FRAME = 320  # 20 ms

    def __init__(self, rate=WAKE_SAMPLE_RATE, min_seconds=0.3, max_seconds=2.5,
                 pause_seconds=0.45, preroll_seconds=0.2, min_threshold=0.008):
        self.min_speech = round(min_seconds * rate)
        self.max_samples = round(max_seconds * rate)
        self.pause = round(pause_seconds * rate)
        self.min_threshold = min_threshold
        self.preroll = deque(maxlen=max(1, round(preroll_seconds * rate / self.FRAME)))
        self.noise = min_threshold / 3.0
        self.pending = np.zeros(0, dtype=np.float32)
        self._idle()

    def _idle(self):
        self.active = False
        self.frames = []
        self.length = 0
        self.speech = 0
        self.silence = 0
        self.too_long = False

    def feed(self, samples):
        """Feed float audio in [-1, 1]; return the utterances that just ended."""
        self.pending = np.concatenate((self.pending, np.asarray(samples, dtype=np.float32)))
        bursts = []
        while self.pending.size >= self.FRAME:
            frame, self.pending = self.pending[:self.FRAME], self.pending[self.FRAME:]
            burst = self._frame(frame)
            if burst is not None:
                bursts.append(burst)
        return bursts

    def _frame(self, frame):
        rms = float(np.sqrt(np.mean(np.square(frame))))
        loud = rms >= max(self.min_threshold, self.noise * 3.0)
        if not loud:
            self.noise = self.noise * 0.95 + rms * 0.05
        if not self.active:
            if not loud:
                self.preroll.append(frame)
                return None
            self.active = True
            self.frames = list(self.preroll)
            self.length = sum(item.size for item in self.frames)
            self.preroll.clear()
        if not self.too_long:
            self.frames.append(frame)
        self.length += frame.size
        if loud:
            self.speech += frame.size
            self.silence = 0
        else:
            self.silence += frame.size
        if self.length - self.silence > self.max_samples:
            self.too_long, self.frames = True, []
        if self.silence < self.pause:
            return None
        burst = None
        if not self.too_long and self.speech >= self.min_speech:
            burst = np.concatenate(self.frames)
        self._idle()
        return burst


class PhraseSpotter:
    """Background worker that recognizes typed phrases in short utterances.

    transcribe(audio_16k) returns the text heard; it runs only for utterances
    of up to a few seconds followed by a pause, never for continuous speech.
    on_detect receives the trigger and its similarity.
    """

    def __init__(self, transcribe, phrases, threshold, on_detect, resampler_factory=None):
        self.transcribe = transcribe
        self.phrases = dict(phrases)
        self.threshold = float(threshold)
        self.thresholds = dict(PHRASE_SIMILARITY)
        self.on_detect = on_detect
        self.resampler_factory = resampler_factory or _soxr_stream
        self.queue = queue.SimpleQueue()
        self.generation = 0
        self.closed = False
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def feed(self, samples, sample_rate):
        """Called from the audio callback; never blocks."""
        if not self.closed:
            self.queue.put((self.generation, int(sample_rate), np.array(samples, dtype=np.float32)))

    def reset(self):
        self.generation += 1
        self.queue.put(("reset", self.generation))

    def close(self):
        self.closed = True
        self.queue.put(None)

    def set_sensitivity(self, sensitivity):
        self.threshold = float(self.thresholds[resolve_wake_sensitivity(sensitivity)])

    def match(self, text):
        best = max(
            ((phrase_score(text, phrase), trigger) for trigger, phrase in self.phrases.items()),
            default=(0.0, None),
        )
        return best if best[0] >= self.threshold else (best[0], None)

    def _run(self):
        generation, resampler, rate, bursts = 0, None, None, SpeechBursts()
        while True:
            item = self.queue.get()
            if item is None:
                return
            if item[0] == "reset":
                generation, resampler, rate, bursts = item[1], None, None, SpeechBursts()
                continue
            item_generation, sample_rate, samples = item
            if item_generation != generation:
                continue
            if sample_rate != rate:
                rate = sample_rate
                resampler = None if rate == WAKE_SAMPLE_RATE else self.resampler_factory(rate)
            audio = samples if resampler is None else resampler(samples)
            for burst in bursts.feed(np.clip(audio, -1.0, 1.0)):
                if item_generation != self.generation:
                    break
                try:
                    score, trigger = self.match(self.transcribe(burst) or "")
                except Exception:
                    continue
                if trigger is not None and item_generation == self.generation:
                    bursts = SpeechBursts()
                    self.on_detect(trigger, score)
                    break


class WakeListeners:
    """Feeds one microphone stream to every active wake listener."""

    def __init__(self, listeners):
        self.listeners = list(listeners)

    def feed(self, samples, sample_rate):
        for listener in self.listeners:
            listener.feed(samples, sample_rate)

    def reset(self):
        for listener in self.listeners:
            listener.reset()

    def close(self):
        for listener in self.listeners:
            listener.close()

    def set_sensitivity(self, sensitivity):
        for listener in self.listeners:
            listener.set_sensitivity(sensitivity)


def _soxr_stream(input_rate):
    import soxr

    stream = soxr.ResampleStream(input_rate, WAKE_SAMPLE_RATE, 1, dtype="float32")
    return lambda samples: stream.resample_chunk(samples)


class SpeechEndpoint:
    """Ends a hands-free recording after the speaker goes quiet.

    The noise floor adapts to quiet blocks, so a steady fan or room noise does
    not keep the recording open.
    """

    def __init__(
        self,
        sample_rate,
        silence_seconds=1.2,
        no_speech_seconds=6.0,
        max_seconds=120.0,
        min_threshold=0.008,
        min_speech_seconds=0.25,
    ):
        # Counted in samples so long recordings do not drift with float sums.
        rate = int(sample_rate)
        self.silence_limit = round(silence_seconds * rate)
        self.no_speech_limit = round(no_speech_seconds * rate)
        self.max_samples = round(max_seconds * rate)
        self.min_speech = round(min_speech_seconds * rate)
        self.min_threshold = min_threshold
        self.noise = min_threshold / 3.0
        self.elapsed = 0
        self.speech = 0
        self.silence = 0
        self.finished = None

    def feed(self, samples):
        """Return None while recording continues, or 'end', 'no_speech' or 'max'."""
        if self.finished:
            return None
        samples = np.asarray(samples, dtype=np.float32)
        if not samples.size:
            return None
        self.elapsed += samples.size
        rms = float(np.sqrt(np.mean(np.square(samples))))
        threshold = max(self.min_threshold, self.noise * 3.0)
        if rms >= threshold:
            self.speech += samples.size
            self.silence = 0
        else:
            self.noise = self.noise * 0.95 + rms * 0.05
            self.silence += samples.size
        if self.elapsed >= self.max_samples:
            self.finished = "max"
        elif self.speech >= self.min_speech and self.silence >= self.silence_limit:
            self.finished = "end"
        elif self.speech < self.min_speech and self.elapsed >= self.no_speech_limit:
            self.finished = "no_speech"
        return self.finished


def is_session_locked():
    """True while the Windows lock screen or a secure desktop is active."""
    try:
        user32 = ctypes.windll.user32
        desktop = user32.OpenInputDesktop(0, False, 0x0100)  # DESKTOP_SWITCHDESKTOP
        if not desktop:
            return True
        try:
            return not user32.SwitchDesktop(desktop)
        finally:
            user32.CloseDesktop(desktop)
    except Exception:
        return False


class RetryClock:
    """Spaces out reopen attempts after microphone or model failures."""

    def __init__(self, delays=(2.0, 5.0, 15.0, 60.0), clock=time.monotonic):
        self.delays = delays
        self.clock = clock
        self.failures = 0
        self.not_before = 0.0

    def ready(self):
        return self.clock() >= self.not_before

    def failed(self):
        delay = self.delays[min(self.failures, len(self.delays) - 1)]
        self.failures += 1
        self.not_before = self.clock() + delay

    def succeeded(self):
        self.failures = 0
        self.not_before = 0.0
