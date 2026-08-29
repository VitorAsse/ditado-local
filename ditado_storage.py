import base64
import copy
import ctypes
import json
import os
import threading
import uuid
from ctypes import wintypes
from datetime import datetime, timezone
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
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
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

    SYNCED_PREFERENCE_KEYS = {
        "auto_paste",
        "grammar_correction",
        "capture_clipboard_history",
        "transcription_language",
        "history_limit",
    }

    def __init__(self, path=None):
        self.lock = threading.RLock()
        self.path = Path(path) if path is not None else CONFIG_PATH
        self.data = copy.deepcopy(self.DEFAULTS)
        self.load()

    @staticmethod
    def _now():
        return datetime.now(timezone.utc).isoformat(timespec="microseconds")

    def _normalize_sync_metadata(self):
        changed = False
        preference_timestamps = self.data.get("_cloud_preference_timestamps")
        if not isinstance(preference_timestamps, dict):
            preference_timestamps = {}
            self.data["_cloud_preference_timestamps"] = preference_timestamps
            changed = True
        now = self._now()
        for key in self.SYNCED_PREFERENCE_KEYS:
            if not isinstance(preference_timestamps.get(key), str):
                preference_timestamps[key] = now
                changed = True

        for collection_name in ("corrections", "rules", "skills"):
            normalized = []
            for raw_item in self.data.get(collection_name, []):
                if not isinstance(raw_item, dict):
                    changed = True
                    continue
                item = dict(raw_item)
                if not isinstance(item.get("id"), str) or not item["id"]:
                    item["id"] = str(uuid.uuid4())
                    changed = True
                if not isinstance(item.get("updated_at"), str):
                    item["updated_at"] = now
                    changed = True
                normalized.append(item)
            if normalized != self.data.get(collection_name, []):
                self.data[collection_name] = normalized
                changed = True
        return changed

    def load(self):
        with self.lock:
            if not self.path.exists():
                self._normalize_sync_metadata()
                self.save()
                return
            try:
                loaded = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    self.data.update(loaded)
                if self._normalize_sync_metadata():
                    self.save()
            except (OSError, json.JSONDecodeError):
                self.data = copy.deepcopy(self.DEFAULTS)
                self.save()

    def save(self):
        with self.lock:
            atomic_write_text(
                self.path,
                json.dumps(self.data, ensure_ascii=False, indent=2),
            )

    def rebind(self, path, initial_data=None):
        with self.lock:
            self.path = Path(path)
            self.data = copy.deepcopy(self.DEFAULTS)
            if self.path.exists():
                self.load()
            elif isinstance(initial_data, dict):
                self.data.update(copy.deepcopy(initial_data))
                self._normalize_sync_metadata()
                self.save()
            else:
                self.save()

    def get(self, key, default=None):
        with self.lock:
            return self.data.get(key, default)

    def set(self, key, value):
        with self.lock:
            self.data[key] = value
            if key in self.SYNCED_PREFERENCE_KEYS:
                timestamps = self.data.setdefault(
                    "_cloud_preference_timestamps",
                    {},
                )
                timestamps[key] = self._now()
            self.save()

    def add_correction(self, wrong, correct):
        normalized_wrong = wrong.strip()
        normalized_correct = correct.strip()
        if not normalized_wrong or not normalized_correct:
            return False
        with self.lock:
            existing = next(
                (
                    item
                    for item in self.data.get("corrections", [])
                    if item.get("wrong", "").casefold()
                    == normalized_wrong.casefold()
                ),
                None,
            )
            corrections = [
                item
                for item in self.data.get("corrections", [])
                if item.get("wrong", "").casefold() != normalized_wrong.casefold()
            ]
            corrections.insert(
                0,
                {
                    "id": (
                        existing.get("id")
                        if isinstance(existing, dict) and existing.get("id")
                        else str(uuid.uuid4())
                    ),
                    "wrong": normalized_wrong,
                    "correct": normalized_correct,
                    "updated_at": self._now(),
                },
            )
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
                "updated_at": self._now(),
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
                    item["updated_at"] = self._now()
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
                "updated_at": self._now(),
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
                    item["updated_at"] = self._now()
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

    def cloud_snapshot(self):
        with self.lock:
            if self._normalize_sync_metadata():
                self.save()
            timestamps = self.data.get("_cloud_preference_timestamps", {})
            return {
                "preferences": [
                    {
                        "id": key,
                        "key": key,
                        "value": copy.deepcopy(self.data.get(key)),
                        "updated_at": timestamps.get(key, self._now()),
                    }
                    for key in sorted(self.SYNCED_PREFERENCE_KEYS)
                ],
                "corrections": copy.deepcopy(self.data.get("corrections", [])),
                "rules": copy.deepcopy(self.data.get("rules", [])),
                "skills": copy.deepcopy(self.data.get("skills", [])),
            }

    def replace_cloud_snapshot(self, snapshot):
        if not isinstance(snapshot, dict):
            return
        with self.lock:
            preference_timestamps = self.data.setdefault(
                "_cloud_preference_timestamps",
                {},
            )
            for item in snapshot.get("preferences", []):
                key = item.get("key") if isinstance(item, dict) else None
                if key in self.SYNCED_PREFERENCE_KEYS:
                    self.data[key] = copy.deepcopy(item.get("value"))
                    preference_timestamps[key] = item.get(
                        "updated_at",
                        self._now(),
                    )
            for collection_name in ("corrections", "rules", "skills"):
                collection = snapshot.get(collection_name)
                if isinstance(collection, list):
                    self.data[collection_name] = copy.deepcopy(collection)
            self._normalize_sync_metadata()
            self.save()


class HistoryStore:
    def __init__(self, limit=150, path=None):
        self.limit = limit
        self.lock = threading.RLock()
        self.path = Path(path) if path is not None else HISTORY_PATH
        self.entries = []
        self.load()

    def load(self):
        with self.lock:
            if not self.path.exists():
                return
            try:
                encrypted = base64.b64decode(self.path.read_text(encoding="ascii"))
                payload = unprotect_for_current_user(encrypted)
                loaded = json.loads(payload.decode("utf-8"))
                if isinstance(loaded, list):
                    self.entries = loaded[: self.limit]
                    changed = False
                    now = datetime.now(timezone.utc).isoformat(timespec="microseconds")
                    for entry in self.entries:
                        if not isinstance(entry.get("id"), str) or not entry["id"]:
                            entry["id"] = str(uuid.uuid4())
                            changed = True
                        if not isinstance(entry.get("updated_at"), str):
                            entry["updated_at"] = entry.get("timestamp") or now
                            changed = True
                    if changed:
                        self.save()
            except Exception:
                self.entries = []

    def save(self):
        with self.lock:
            payload = json.dumps(self.entries, ensure_ascii=False).encode("utf-8")
            encrypted = protect_for_current_user(payload)
            atomic_write_text(self.path, base64.b64encode(encrypted).decode("ascii"))

    def rebind(self, path, initial_entries=None):
        with self.lock:
            self.path = Path(path)
            self.entries = []
            if self.path.exists():
                self.load()
            elif isinstance(initial_entries, list):
                self.entries = copy.deepcopy(initial_entries)[: self.limit]
                self.save()

    def add(self, text, source, conversation=None):
        if not isinstance(text, str):
            return None
        normalized = text.strip()
        if not normalized:
            return None
        with self.lock:
            if self.entries and self.entries[0].get("text") == normalized:
                entry = self.entries[0]
                entry["timestamp"] = datetime.now().isoformat(timespec="seconds")
                entry["updated_at"] = datetime.now(timezone.utc).isoformat(
                    timespec="microseconds"
                )
                entry["source"] = source
            else:
                entry = {
                    "id": str(uuid.uuid4()),
                    "text": normalized,
                    "source": source,
                    "timestamp": datetime.now().isoformat(timespec="seconds"),
                    "updated_at": datetime.now(timezone.utc).isoformat(
                        timespec="microseconds"
                    ),
                }
                self.entries.insert(0, entry)
            if isinstance(conversation, dict):
                entry["conversation"] = json.loads(
                    json.dumps(conversation, ensure_ascii=False)
                )
            else:
                entry.pop("conversation", None)
            self.entries = self.entries[: self.limit]
            self.save()
        return entry["id"]

    def update_conversation(self, entry_id, text, conversation):
        if (
            not isinstance(entry_id, str)
            or not entry_id
            or not isinstance(text, str)
            or not text.strip()
            or not isinstance(conversation, dict)
        ):
            return False
        with self.lock:
            entry = next(
                (
                    item
                    for item in self.entries
                    if isinstance(item, dict) and item.get("id") == entry_id
                ),
                None,
            )
            if entry is None or entry.get("source") != "agent":
                return False
            entry["text"] = text.strip()
            entry["conversation"] = json.loads(
                json.dumps(conversation, ensure_ascii=False)
            )
            entry["timestamp"] = datetime.now().isoformat(timespec="seconds")
            entry["updated_at"] = datetime.now(timezone.utc).isoformat(
                timespec="microseconds"
            )
            self.entries.remove(entry)
            self.entries.insert(0, entry)
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

    def replace_entries(self, entries):
        if not isinstance(entries, list):
            return
        with self.lock:
            self.entries = copy.deepcopy(entries)[: self.limit]
            self.save()

    def latest_transcription(self):
        with self.lock:
            for entry in self.entries:
                if entry.get("source") in {"transcription", "agent"}:
                    return entry.get("text", "")
        return ""

    def latest_agent_conversation(self):
        with self.lock:
            for entry in self.entries:
                if (
                    entry.get("source") == "agent"
                    and isinstance(entry.get("conversation"), dict)
                ):
                    return json.loads(
                        json.dumps(entry, ensure_ascii=False)
                    )
        return None
