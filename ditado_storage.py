import base64
import copy
import ctypes
import json
import os
import threading
import uuid
from ctypes import wintypes
from datetime import datetime
from pathlib import Path


APP_ROOT = Path(os.environ["LOCALAPPDATA"]) / "faster-whisper"
CONFIG_PATH = APP_ROOT / "config.json"
HISTORY_PATH = APP_ROOT / "clipboard_history.dat"


class DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_byte)),
    ]


CRYPT32 = ctypes.WinDLL("crypt32", use_last_error=True)
KERNEL32 = ctypes.WinDLL("kernel32", use_last_error=True)
CRYPT32.CryptProtectData.argtypes = [
    ctypes.POINTER(DataBlob),
    wintypes.LPCWSTR,
    ctypes.POINTER(DataBlob),
    ctypes.c_void_p,
    ctypes.c_void_p,
    wintypes.DWORD,
    ctypes.POINTER(DataBlob),
]
CRYPT32.CryptProtectData.restype = wintypes.BOOL
CRYPT32.CryptUnprotectData.argtypes = [
    ctypes.POINTER(DataBlob),
    ctypes.POINTER(wintypes.LPWSTR),
    ctypes.POINTER(DataBlob),
    ctypes.c_void_p,
    ctypes.c_void_p,
    wintypes.DWORD,
    ctypes.POINTER(DataBlob),
]
CRYPT32.CryptUnprotectData.restype = wintypes.BOOL
KERNEL32.LocalFree.argtypes = [wintypes.HLOCAL]
KERNEL32.LocalFree.restype = wintypes.HLOCAL


def _blob_from_bytes(data):
    buffer = ctypes.create_string_buffer(data)
    blob = DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))
    return blob, buffer


def protect_for_current_user(data):
    input_blob, input_buffer = _blob_from_bytes(data)
    output_blob = DataBlob()
    success = CRYPT32.CryptProtectData(
        ctypes.byref(input_blob),
        "Histórico do Ditado local",
        None,
        None,
        None,
        0x1,
        ctypes.byref(output_blob),
    )
    if not success:
        raise ctypes.WinError()
    try:
        return ctypes.string_at(output_blob.pbData, output_blob.cbData)
    finally:
        KERNEL32.LocalFree(ctypes.cast(output_blob.pbData, wintypes.HLOCAL))
        del input_buffer


def unprotect_for_current_user(data):
    input_blob, input_buffer = _blob_from_bytes(data)
    output_blob = DataBlob()
    success = CRYPT32.CryptUnprotectData(
        ctypes.byref(input_blob),
        None,
        None,
        None,
        None,
        0x1,
        ctypes.byref(output_blob),
    )
    if not success:
        raise ctypes.WinError()
    try:
        return ctypes.string_at(output_blob.pbData, output_blob.cbData)
    finally:
        KERNEL32.LocalFree(ctypes.cast(output_blob.pbData, wintypes.HLOCAL))
        del input_buffer


def atomic_write_text(path, content):
    APP_ROOT.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    temporary_path.write_text(content, encoding="utf-8")
    os.replace(temporary_path, path)


class AppConfig:
    DEFAULTS = {
        "microphone_name": "",
        "auto_paste": True,
        "grammar_correction": True,
        "capture_clipboard_history": False,
        "mute_playback_while_recording": True,
        "transcription_profile": "balanced",
        "transcription_language": "auto",
        "corrections": [],
        "rules": [],
        "skills": [],
        "history_limit": 150,
        "startup_enabled": True,
    }

    def __init__(self):
        self.lock = threading.RLock()
        self.data = copy.deepcopy(self.DEFAULTS)
        self.load()

    def load(self):
        with self.lock:
            if not CONFIG_PATH.exists():
                self.save()
                return
            try:
                loaded = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    self.data.update(loaded)
            except (OSError, json.JSONDecodeError):
                self.data = copy.deepcopy(self.DEFAULTS)
                self.save()

    def save(self):
        with self.lock:
            atomic_write_text(
                CONFIG_PATH,
                json.dumps(self.data, ensure_ascii=False, indent=2),
            )

    def get(self, key, default=None):
        with self.lock:
            return self.data.get(key, default)

    def set(self, key, value):
        with self.lock:
            self.data[key] = value
            self.save()

    def add_correction(self, wrong, correct):
        normalized_wrong = wrong.strip()
        normalized_correct = correct.strip()
        if not normalized_wrong or not normalized_correct:
            return False
        with self.lock:
            corrections = [
                item
                for item in self.data.get("corrections", [])
                if item.get("wrong", "").casefold() != normalized_wrong.casefold()
            ]
            corrections.insert(0, {"wrong": normalized_wrong, "correct": normalized_correct})
            self.data["corrections"] = corrections[:200]
            self.save()
        return True

    def remove_correction(self, wrong):
        with self.lock:
            corrections = [
                item
                for item in self.data.get("corrections", [])
                if item.get("wrong") != wrong
            ]
            self.data["corrections"] = corrections
            self.save()

    def save_rule(self, rule_id, name, instructions):
        normalized_name = name.strip()
        normalized_instructions = instructions.strip()
        if not normalized_name or not normalized_instructions:
            return None

        with self.lock:
            existing = next(
                (
                    item
                    for item in self.data.get("rules", [])
                    if item.get("id") == rule_id
                ),
                None,
            )
            resolved_id = rule_id or str(uuid.uuid4())
            rule = {
                "id": resolved_id,
                "name": normalized_name[:80],
                "instructions": normalized_instructions[:4000],
                "enabled": bool(existing.get("enabled", True)) if existing else True,
            }
            rules = [
                item
                for item in self.data.get("rules", [])
                if item.get("id") != resolved_id
            ]
            rules.insert(0, rule)
            self.data["rules"] = rules[:30]
            self.save()
        return resolved_id

    def remove_rule(self, rule_id):
        with self.lock:
            self.data["rules"] = [
                item
                for item in self.data.get("rules", [])
                if item.get("id") != rule_id
            ]
            self.save()

    def set_rule_enabled(self, rule_id, enabled):
        with self.lock:
            for item in self.data.get("rules", []):
                if item.get("id") == rule_id:
                    item["enabled"] = bool(enabled)
                    self.save()
                    return True
        return False

    def get_rules(self, enabled_only=False):
        with self.lock:
            rules = [
                dict(item)
                for item in self.data.get("rules", [])
                if isinstance(item, dict)
            ]
        if enabled_only:
            return [item for item in rules if item.get("enabled", True)]
        return rules

    def save_skill(
        self,
        skill_id,
        name,
        description,
        triggers,
        instructions,
        examples,
    ):
        normalized_name = name.strip()
        normalized_description = description.strip()
        normalized_instructions = instructions.strip()
        normalized_triggers = list(
            dict.fromkeys(
                trigger.strip()
                for trigger in triggers
                if isinstance(trigger, str) and trigger.strip()
            )
        )[:12]
        normalized_examples = [
            example.strip()
            for example in examples
            if isinstance(example, str) and example.strip()
        ][:8]
        if not normalized_name or not normalized_description or not normalized_instructions:
            return None

        with self.lock:
            existing = next(
                (
                    item
                    for item in self.data.get("skills", [])
                    if item.get("id") == skill_id
                ),
                None,
            )
            resolved_id = skill_id or str(uuid.uuid4())
            skill = {
                "id": resolved_id,
                "name": normalized_name[:80],
                "description": normalized_description[:500],
                "triggers": normalized_triggers,
                "instructions": normalized_instructions[:4000],
                "examples": normalized_examples,
                "enabled": bool(existing.get("enabled", True)) if existing else True,
            }
            skills = [
                item
                for item in self.data.get("skills", [])
                if item.get("id") != resolved_id
            ]
            skills.insert(0, skill)
            self.data["skills"] = skills[:30]
            self.save()
        return resolved_id

    def remove_skill(self, skill_id):
        with self.lock:
            self.data["skills"] = [
                item
                for item in self.data.get("skills", [])
                if item.get("id") != skill_id
            ]
            self.save()

    def set_skill_enabled(self, skill_id, enabled):
        with self.lock:
            for item in self.data.get("skills", []):
                if item.get("id") == skill_id:
                    item["enabled"] = bool(enabled)
                    self.save()
                    return True
        return False

    def get_skills(self, enabled_only=False):
        with self.lock:
            skills = [
                dict(item)
                for item in self.data.get("skills", [])
                if isinstance(item, dict)
            ]
        if enabled_only:
            return [item for item in skills if item.get("enabled", True)]
        return skills


class HistoryStore:
    def __init__(self, limit=150):
        self.limit = limit
        self.lock = threading.RLock()
        self.entries = []
        self.load()

    def load(self):
        with self.lock:
            if not HISTORY_PATH.exists():
                return
            try:
                encrypted = base64.b64decode(HISTORY_PATH.read_text(encoding="ascii"))
                payload = unprotect_for_current_user(encrypted)
                loaded = json.loads(payload.decode("utf-8"))
                if isinstance(loaded, list):
                    self.entries = loaded[: self.limit]
            except Exception:
                self.entries = []

    def save(self):
        with self.lock:
            payload = json.dumps(self.entries, ensure_ascii=False).encode("utf-8")
            encrypted = protect_for_current_user(payload)
            atomic_write_text(HISTORY_PATH, base64.b64encode(encrypted).decode("ascii"))

    def add(self, text, source):
        if not isinstance(text, str):
            return False
        normalized = text.strip()
        if not normalized:
            return False
        with self.lock:
            if self.entries and self.entries[0].get("text") == normalized:
                self.entries[0]["timestamp"] = datetime.now().isoformat(timespec="seconds")
                self.entries[0]["source"] = source
            else:
                self.entries.insert(
                    0,
                    {
                        "id": str(uuid.uuid4()),
                        "text": normalized,
                        "source": source,
                        "timestamp": datetime.now().isoformat(timespec="seconds"),
                    },
                )
            self.entries = self.entries[: self.limit]
            self.save()
        return True

    def clear(self):
        with self.lock:
            self.entries = []
            self.save()

    def all(self):
        with self.lock:
            return [dict(entry) for entry in self.entries]

    def latest_transcription(self):
        with self.lock:
            for entry in self.entries:
                if entry.get("source") in {"transcription", "agent"}:
                    return entry.get("text", "")
        return ""
