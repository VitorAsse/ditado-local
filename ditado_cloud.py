import base64
import copy
import hashlib
import json
import os
import platform
import re
import secrets
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from ditado_storage import (
    APP_ROOT,
    CONFIG_PATH,
    HISTORY_PATH,
    atomic_write_text,
    protect_for_current_user,
    unprotect_for_current_user,
)


CLOUD_STATE_PATH = APP_ROOT / "cloud_state.dat"
PROFILES_ROOT = APP_ROOT / "profiles"
SYNC_ITEM_TYPES = {"preference", "correction", "rule", "skill", "history"}
COLLECTION_BY_TYPE = {
    "correction": "corrections",
    "rule": "rules",
    "skill": "skills",
}
RECOVERY_ITERATIONS = 600_000
RECOVERY_AAD_PREFIX = b"ditado-local-keyring-v1:"
RECORD_AAD_PREFIX = b"ditado-local-record-v1:"


class CloudError(RuntimeError):
    pass


class CloudNotConfiguredError(CloudError):
    pass


class CloudAuthenticationError(CloudError):
    pass


class RecoveryKeyRequired(CloudError):
    pass


class InvalidRecoveryKey(CloudError):
    pass


class DeviceRevokedError(CloudError):
    pass


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def parse_timestamp(value):
    if not isinstance(value, str) or not value:
        return datetime.min.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _b64encode(value):
    return base64.b64encode(value).decode("ascii")


def _b64decode(value):
    return base64.b64decode(value.encode("ascii"), validate=True)


def _canonical_json(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def jwt_claims(access_token):
    try:
        encoded = access_token.split(".", 2)[1]
        encoded += "=" * (-len(encoded) % 4)
        claims = json.loads(base64.urlsafe_b64decode(encoded).decode("utf-8"))
    except (AttributeError, IndexError, ValueError, json.JSONDecodeError, UnicodeError):
        return {}
    return claims if isinstance(claims, dict) else {}


def generate_recovery_code():
    raw = base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")
    return "-".join(raw[index : index + 4] for index in range(0, len(raw), 4))


def normalize_recovery_code(value):
    if not isinstance(value, str):
        return ""
    return re.sub(r"[^A-Z2-7]", "", value.upper())


def wrap_master_key(master_key, recovery_code, user_id):
    normalized = normalize_recovery_code(recovery_code)
    if len(master_key) != 32 or len(normalized) < 24:
        raise ValueError("Chave mestre ou chave de recuperação inválida.")
    salt = secrets.token_bytes(16)
    nonce = secrets.token_bytes(12)
    wrapping_key = hashlib.pbkdf2_hmac(
        "sha256",
        normalized.encode("ascii"),
        salt,
        RECOVERY_ITERATIONS,
        dklen=32,
    )
    aad = RECOVERY_AAD_PREFIX + str(user_id).encode("ascii")
    ciphertext = AESGCM(wrapping_key).encrypt(nonce, master_key, aad)
    return {
        "version": 1,
        "algorithm": "AES-256-GCM",
        "kdf": "PBKDF2-HMAC-SHA256",
        "iterations": RECOVERY_ITERATIONS,
        "salt": _b64encode(salt),
        "nonce": _b64encode(nonce),
        "ciphertext": _b64encode(ciphertext),
    }


def unwrap_master_key(wrapped_key, recovery_code, user_id):
    try:
        normalized = normalize_recovery_code(recovery_code)
        iterations = int(wrapped_key["iterations"])
        salt = _b64decode(wrapped_key["salt"])
        nonce = _b64decode(wrapped_key["nonce"])
        ciphertext = _b64decode(wrapped_key["ciphertext"])
        wrapping_key = hashlib.pbkdf2_hmac(
            "sha256",
            normalized.encode("ascii"),
            salt,
            iterations,
            dklen=32,
        )
        aad = RECOVERY_AAD_PREFIX + str(user_id).encode("ascii")
        master_key = AESGCM(wrapping_key).decrypt(nonce, ciphertext, aad)
    except (KeyError, TypeError, ValueError, InvalidTag) as error:
        raise InvalidRecoveryKey(
            "A chave de recuperação não corresponde a esta conta."
        ) from error
    if len(master_key) != 32:
        raise InvalidRecoveryKey(
            "A chave de recuperação não corresponde a esta conta."
        )
    return master_key


def encrypt_record(master_key, user_id, item_type, item_id, payload):
    nonce = secrets.token_bytes(12)
    aad = RECORD_AAD_PREFIX + f"{user_id}:{item_type}:{item_id}".encode("utf-8")
    ciphertext = AESGCM(master_key).encrypt(nonce, _canonical_json(payload), aad)
    return _b64encode(nonce + ciphertext)


def decrypt_record(master_key, user_id, item_type, item_id, ciphertext):
    try:
        packed = _b64decode(ciphertext)
        nonce, encrypted = packed[:12], packed[12:]
        aad = RECORD_AAD_PREFIX + f"{user_id}:{item_type}:{item_id}".encode(
            "utf-8"
        )
        plaintext = AESGCM(master_key).decrypt(nonce, encrypted, aad)
        result = json.loads(plaintext.decode("utf-8"))
    except (ValueError, InvalidTag, json.JSONDecodeError, UnicodeDecodeError) as error:
        raise CloudError(
            "Um item da nuvem falhou na verificação criptográfica."
        ) from error
    if not isinstance(result, dict):
        raise CloudError("Um item da nuvem tem formato inválido.")
    return result


def _safe_profile_component(user_id):
    normalized = re.sub(r"[^0-9a-zA-Z-]", "", str(user_id))[:80]
    if not normalized:
        normalized = hashlib.sha256(str(user_id).encode("utf-8")).hexdigest()[:32]
    return normalized


class CloudStateStore:
    DEFAULTS = {
        "version": 1,
        "backend": {"url": "", "publishable_key": ""},
        "device_id": "",
        "device_name": "",
        "active_user_id": "",
        "sessions": {},
        "master_keys": {},
        "sync": {},
    }

    def __init__(self, path=None):
        self.path = Path(path) if path is not None else CLOUD_STATE_PATH
        self.lock = threading.RLock()
        self.data = copy.deepcopy(self.DEFAULTS)
        self.load()

    def load(self):
        with self.lock:
            if self.path.exists():
                try:
                    encrypted = _b64decode(self.path.read_text(encoding="ascii"))
                    decoded = unprotect_for_current_user(encrypted)
                    loaded = json.loads(decoded.decode("utf-8"))
                    if isinstance(loaded, dict):
                        self.data.update(loaded)
                except Exception:
                    self.data = copy.deepcopy(self.DEFAULTS)
            if not self.data.get("device_id"):
                self.data["device_id"] = str(uuid.uuid4())
            if not self.data.get("device_name"):
                self.data["device_name"] = platform.node() or "PC Windows"
            self.save()

    def save(self):
        with self.lock:
            payload = _canonical_json(self.data)
            encrypted = protect_for_current_user(payload)
            atomic_write_text(self.path, _b64encode(encrypted))

    def configure_backend(self, url, publishable_key):
        normalized_url = str(url or "").strip().rstrip("/")
        normalized_key = str(publishable_key or "").strip()
        if not re.fullmatch(r"https://[a-z0-9-]+\.supabase\.co", normalized_url):
            raise CloudError("Informe uma URL válida do projeto Supabase.")
        if not normalized_key.startswith(("sb_publishable_", "eyJ")):
            raise CloudError("Informe a chave publicável do projeto Supabase.")
        with self.lock:
            self.data["backend"] = {
                "url": normalized_url,
                "publishable_key": normalized_key,
            }
            self.save()

    def backend(self):
        with self.lock:
            return dict(self.data.get("backend", {}))

    def put_session(self, auth_payload, email=None, make_active=True):
        user = auth_payload.get("user") if isinstance(auth_payload, dict) else None
        user_id = user.get("id") if isinstance(user, dict) else None
        access_token = auth_payload.get("access_token")
        refresh_token = auth_payload.get("refresh_token")
        if not user_id or not access_token or not refresh_token:
            raise CloudAuthenticationError("A sessão recebida do Supabase é inválida.")
        expires_at = auth_payload.get("expires_at")
        if not expires_at:
            expires_at = int(time.time()) + int(auth_payload.get("expires_in", 3600))
        resolved_email = email or user.get("email") or "Conta sem e-mail"
        session_id = jwt_claims(access_token).get("session_id", "")
        with self.lock:
            self.data.setdefault("sessions", {})[user_id] = {
                "user_id": user_id,
                "email": resolved_email,
                "access_token": access_token,
                "refresh_token": refresh_token,
                "expires_at": int(expires_at),
                "session_id": session_id,
            }
            if make_active:
                self.data["active_user_id"] = user_id
            self.save()
        return user_id

    def active_session(self):
        with self.lock:
            user_id = self.data.get("active_user_id")
            session = self.data.get("sessions", {}).get(user_id)
            return copy.deepcopy(session) if isinstance(session, dict) else None

    def session(self, user_id):
        with self.lock:
            session = self.data.get("sessions", {}).get(user_id)
            return copy.deepcopy(session) if isinstance(session, dict) else None

    def accounts(self):
        with self.lock:
            active_user_id = self.data.get("active_user_id")
            return [
                {
                    "user_id": user_id,
                    "email": session.get("email", "Conta sem e-mail"),
                    "active": user_id == active_user_id,
                }
                for user_id, session in self.data.get("sessions", {}).items()
                if isinstance(session, dict)
            ]

    def set_active_user(self, user_id):
        with self.lock:
            if user_id and user_id not in self.data.get("sessions", {}):
                raise CloudAuthenticationError("Esta conta não está salva neste PC.")
            self.data["active_user_id"] = user_id or ""
            self.save()

    def remove_session(self, user_id):
        with self.lock:
            self.data.get("sessions", {}).pop(user_id, None)
            self.data.get("master_keys", {}).pop(user_id, None)
            if self.data.get("active_user_id") == user_id:
                self.data["active_user_id"] = ""
            self.save()

    def set_master_key(self, user_id, master_key):
        if len(master_key) != 32:
            raise ValueError("A chave mestre deve ter 32 bytes.")
        with self.lock:
            self.data.setdefault("master_keys", {})[user_id] = _b64encode(master_key)
            self.save()

    def master_key(self, user_id):
        with self.lock:
            encoded = self.data.get("master_keys", {}).get(user_id)
        if not encoded:
            return None
        try:
            value = _b64decode(encoded)
        except (ValueError, TypeError):
            return None
        return value if len(value) == 32 else None

    def profile_paths(self, user_id):
        root = PROFILES_ROOT / _safe_profile_component(user_id)
        return root / "config.json", root / "history.dat"

    def active_profile_paths(self):
        session = self.active_session()
        if not session:
            return CONFIG_PATH, HISTORY_PATH
        return self.profile_paths(session["user_id"])

    def sync_bucket(self, user_id):
        with self.lock:
            bucket = self.data.setdefault("sync", {}).setdefault(
                user_id,
                {"manifest": {}, "outbox": {}, "last_sync": ""},
            )
            return copy.deepcopy(bucket)

    def set_sync_bucket(self, user_id, bucket):
        with self.lock:
            self.data.setdefault("sync", {})[user_id] = copy.deepcopy(bucket)
            self.save()


class SupabaseCloudClient:
    def __init__(self, url, publishable_key, transport=None):
        self.url = str(url).rstrip("/")
        self.publishable_key = publishable_key
        self.http = httpx.Client(
            timeout=25,
            follow_redirects=True,
            transport=transport,
        )

    def close(self):
        self.http.close()

    def _request(
        self,
        method,
        path,
        *,
        access_token=None,
        params=None,
        payload=None,
        headers=None,
    ):
        request_headers = {
            "apikey": self.publishable_key,
            "Accept": "application/json",
        }
        if payload is not None:
            request_headers["Content-Type"] = "application/json"
        if access_token:
            request_headers["Authorization"] = f"Bearer {access_token}"
        if headers:
            request_headers.update(headers)
        try:
            response = self.http.request(
                method,
                f"{self.url}{path}",
                params=params,
                json=payload,
                headers=request_headers,
            )
        except httpx.HTTPError as error:
            raise CloudError(
                "Não foi possível alcançar a nuvem. Os dados continuam salvos neste PC."
            ) from error
        if not response.is_success:
            try:
                details = response.json()
            except ValueError:
                details = {}
            message = (
                details.get("msg")
                or details.get("message")
                or details.get("error_description")
                or details.get("error")
                or f"Falha HTTP {response.status_code}"
            )
            if response.status_code in {400, 401, 403} and path.startswith("/auth/"):
                raise CloudAuthenticationError(str(message))
            raise CloudError(str(message))
        if response.status_code == 204 or not response.content:
            return None
        try:
            return response.json()
        except ValueError as error:
            raise CloudError("A nuvem retornou uma resposta inválida.") from error

    def sign_up(self, email, password):
        return self._request(
            "POST",
            "/auth/v1/signup",
            payload={"email": email, "password": password},
        )

    def resend_signup_confirmation(self, email):
        return self._request(
            "POST",
            "/auth/v1/resend",
            payload={"type": "signup", "email": email},
        )

    def sign_in(self, email, password):
        return self._request(
            "POST",
            "/auth/v1/token",
            params={"grant_type": "password"},
            payload={"email": email, "password": password},
        )

    def refresh(self, refresh_token):
        return self._request(
            "POST",
            "/auth/v1/token",
            params={"grant_type": "refresh_token"},
            payload={"refresh_token": refresh_token},
        )

    def sign_out(self, access_token):
        return self._request(
            "POST",
            "/auth/v1/logout",
            access_token=access_token,
        )

    def get_user(self, access_token):
        return self._request("GET", "/auth/v1/user", access_token=access_token)

    def select(self, table, access_token, params=None):
        return self._request(
            "GET",
            f"/rest/v1/{table}",
            access_token=access_token,
            params=params,
        ) or []

    def upsert(self, table, rows, access_token, on_conflict):
        return self._request(
            "POST",
            f"/rest/v1/{table}",
            access_token=access_token,
            params={"on_conflict": on_conflict},
            payload=rows,
            headers={"Prefer": "resolution=merge-duplicates,return=representation"},
        ) or []

    def update(self, table, values, access_token, params):
        return self._request(
            "PATCH",
            f"/rest/v1/{table}",
            access_token=access_token,
            params=params,
            payload=values,
            headers={"Prefer": "return=representation"},
        ) or []

    def rpc(self, function_name, values, access_token):
        return self._request(
            "POST",
            f"/rest/v1/rpc/{function_name}",
            access_token=access_token,
            payload=values,
        )


class CloudSyncManager:
    def __init__(self, state, app_config, history, transport=None):
        self.state = state
        self.app_config = app_config
        self.history = history
        self.transport = transport
        self.sync_lock = threading.Lock()
        self.last_error = ""
        self.last_recovery_code = ""

    def _client(self):
        backend = self.state.backend()
        if not backend.get("url") or not backend.get("publishable_key"):
            raise CloudNotConfiguredError(
                "Configure a URL e a chave publicável do Supabase na aba Nuvem."
            )
        return SupabaseCloudClient(
            backend["url"],
            backend["publishable_key"],
            transport=self.transport,
        )

    def configure_backend(self, url, publishable_key):
        self.state.configure_backend(url, publishable_key)

    def _activate_profile(self, user_id, previous_config, previous_history):
        config_path, history_path = self.state.profile_paths(user_id)
        self.app_config.rebind(config_path, initial_data=previous_config)
        self.history.rebind(history_path, initial_entries=previous_history)

    def sign_up(self, email, password):
        normalized_email = str(email or "").strip().lower()
        if "@" not in normalized_email or len(password or "") < 8:
            raise CloudAuthenticationError(
                "Use um e-mail válido e uma senha com pelo menos 8 caracteres."
            )
        client = self._client()
        try:
            payload = client.sign_up(normalized_email, password)
        finally:
            client.close()
        if not payload.get("access_token"):
            return {
                "confirmation_required": True,
                "email": normalized_email,
            }
        return self._accept_session(payload, normalized_email)

    def sign_in(self, email, password):
        normalized_email = str(email or "").strip().lower()
        if not normalized_email or not password:
            raise CloudAuthenticationError("Informe o e-mail e a senha.")
        client = self._client()
        try:
            payload = client.sign_in(normalized_email, password)
        finally:
            client.close()
        return self._accept_session(payload, normalized_email)

    def resend_signup_confirmation(self, email):
        normalized_email = str(email or "").strip().lower()
        if "@" not in normalized_email:
            raise CloudAuthenticationError("Informe o e-mail usado para criar a conta.")
        client = self._client()
        try:
            client.resend_signup_confirmation(normalized_email)
        finally:
            client.close()
        return {"email": normalized_email}

    def _accept_session(self, payload, email):
        previous_config = copy.deepcopy(self.app_config.data)
        previous_history = self.history.all()
        user_id = self.state.put_session(payload, email=email, make_active=True)
        self._activate_profile(user_id, previous_config, previous_history)
        return {
            "confirmation_required": False,
            "user_id": user_id,
            "email": email,
        }

    def _valid_session(self):
        session = self.state.active_session()
        if not session:
            raise CloudAuthenticationError("Entre em uma conta para sincronizar.")
        if int(session.get("expires_at", 0)) > int(time.time()) + 90:
            return session
        client = self._client()
        try:
            payload = client.refresh(session.get("refresh_token", ""))
        finally:
            client.close()
        self.state.put_session(payload, email=session.get("email"), make_active=True)
        return self.state.active_session()

    def switch_account(self, user_id):
        previous_config = copy.deepcopy(self.app_config.data)
        previous_history = self.history.all()
        self.state.set_active_user(user_id)
        self._activate_profile(user_id, previous_config, previous_history)
        return self.state.active_session()

    def sign_out(self, forget=False):
        session = self.state.active_session()
        if not session:
            return
        if forget:
            try:
                client = self._client()
                try:
                    client.sign_out(session.get("access_token", ""))
                finally:
                    client.close()
            except CloudError:
                pass
            self.state.remove_session(session["user_id"])
        else:
            self.state.set_active_user("")
        self.app_config.rebind(CONFIG_PATH)
        self.history.rebind(HISTORY_PATH)

    def _keyring(self, client, session):
        rows = client.select(
            "ditado_user_keys",
            session["access_token"],
            params={
                "select": "user_id,wrapped_key,updated_at",
                "user_id": f"eq.{session['user_id']}",
                "limit": "1",
            },
        )
        return rows[0] if rows else None

    def ensure_master_key(self, recovery_code=None):
        session = self._valid_session()
        existing_local = self.state.master_key(session["user_id"])
        if existing_local is not None:
            return existing_local, ""
        client = self._client()
        try:
            keyring = self._keyring(client, session)
            if keyring is None:
                master_key = secrets.token_bytes(32)
                new_recovery_code = generate_recovery_code()
                wrapped = wrap_master_key(
                    master_key,
                    new_recovery_code,
                    session["user_id"],
                )
                client.upsert(
                    "ditado_user_keys",
                    [
                        {
                            "user_id": session["user_id"],
                            "wrapped_key": wrapped,
                            "updated_at": utc_now(),
                        }
                    ],
                    session["access_token"],
                    "user_id",
                )
                self.state.set_master_key(session["user_id"], master_key)
                self.last_recovery_code = new_recovery_code
                return master_key, new_recovery_code
            if not recovery_code:
                raise RecoveryKeyRequired(
                    "Digite a chave de recuperação desta conta para abrir os dados."
                )
            master_key = unwrap_master_key(
                keyring.get("wrapped_key", {}),
                recovery_code,
                session["user_id"],
            )
            self.state.set_master_key(session["user_id"], master_key)
            return master_key, ""
        finally:
            client.close()

    def _local_items(self):
        snapshot = self.app_config.cloud_snapshot()
        items = {}
        for item in snapshot.get("preferences", []):
            items[("preference", item["id"])] = copy.deepcopy(item)
        for item_type, collection_name in COLLECTION_BY_TYPE.items():
            for item in snapshot.get(collection_name, []):
                if isinstance(item, dict) and item.get("id"):
                    items[(item_type, item["id"])] = copy.deepcopy(item)
        for item in self.history.all():
            if isinstance(item, dict) and item.get("id"):
                items[("history", item["id"])] = copy.deepcopy(item)
        return items

    @staticmethod
    def _item_hash(payload):
        return hashlib.sha256(_canonical_json(payload)).hexdigest()

    @staticmethod
    def _manifest_key(item_type, item_id):
        return f"{item_type}:{item_id}"

    def _observe_local(self, user_id, bucket):
        current = self._local_items()
        manifest = bucket.setdefault("manifest", {})
        outbox = bucket.setdefault("outbox", {})
        device_id = self.state.data["device_id"]

        current_keys = set()
        for (item_type, item_id), payload in current.items():
            key = self._manifest_key(item_type, item_id)
            current_keys.add(key)
            digest = self._item_hash(payload)
            prior = manifest.get(key, {})
            if prior.get("hash") != digest:
                updated_at = payload.get("updated_at") or utc_now()
                outbox[key] = {
                    "item_type": item_type,
                    "item_id": item_id,
                    "payload": payload,
                    "updated_at": updated_at,
                    "device_id": device_id,
                    "deleted_at": None,
                }
                manifest[key] = {
                    "hash": digest,
                    "updated_at": updated_at,
                    "device_id": device_id,
                }

        for key, prior in list(manifest.items()):
            if key in current_keys or prior.get("hash") == "__deleted__":
                continue
            item_type, item_id = key.split(":", 1)
            deleted_at = utc_now()
            outbox[key] = {
                "item_type": item_type,
                "item_id": item_id,
                "payload": None,
                "updated_at": deleted_at,
                "device_id": device_id,
                "deleted_at": deleted_at,
            }
            manifest[key] = {
                "hash": "__deleted__",
                "updated_at": deleted_at,
                "device_id": device_id,
            }
        return current

    @staticmethod
    def _remote_wins(remote, local_timestamp, local_device_id):
        remote_timestamp = parse_timestamp(remote.get("updated_at"))
        local_timestamp = parse_timestamp(local_timestamp)
        if remote_timestamp != local_timestamp:
            return remote_timestamp > local_timestamp
        return str(remote.get("device_id", "")) > str(local_device_id or "")

    def _merge_remote(
        self,
        user_id,
        master_key,
        rows,
        bucket,
        *,
        prefer_remote=False,
    ):
        current = self._local_items()
        manifest = bucket.setdefault("manifest", {})
        changed = False
        for row in rows:
            item_type = row.get("item_type")
            item_id = row.get("item_id")
            if item_type not in SYNC_ITEM_TYPES or not isinstance(item_id, str):
                continue
            key_tuple = (item_type, item_id)
            manifest_key = self._manifest_key(item_type, item_id)
            local = current.get(key_tuple)
            local_meta = manifest.get(manifest_key, {})
            local_timestamp = (
                local.get("updated_at") if isinstance(local, dict) else None
            ) or local_meta.get("updated_at")
            local_device_id = local_meta.get("device_id")
            if (
                not prefer_remote
                and (local is not None or local_meta)
                and not self._remote_wins(
                    row,
                    local_timestamp,
                    local_device_id,
                )
            ):
                continue
            if row.get("deleted_at"):
                if key_tuple in current:
                    del current[key_tuple]
                    changed = True
                manifest[manifest_key] = {
                    "hash": "__deleted__",
                    "updated_at": row.get("updated_at"),
                    "device_id": row.get("device_id"),
                }
                bucket.setdefault("outbox", {}).pop(manifest_key, None)
                continue
            payload = decrypt_record(
                master_key,
                user_id,
                item_type,
                item_id,
                row.get("ciphertext", ""),
            )
            payload["id"] = item_id
            payload["updated_at"] = row.get("updated_at")
            current[key_tuple] = payload
            manifest[manifest_key] = {
                "hash": self._item_hash(payload),
                "updated_at": row.get("updated_at"),
                "device_id": row.get("device_id"),
            }
            bucket.setdefault("outbox", {}).pop(manifest_key, None)
            changed = True

        if changed:
            preferences = []
            collections = {name: [] for name in COLLECTION_BY_TYPE.values()}
            history_entries = []
            for (item_type, _item_id), payload in current.items():
                if item_type == "preference":
                    preferences.append(payload)
                elif item_type == "history":
                    history_entries.append(payload)
                else:
                    collections[COLLECTION_BY_TYPE[item_type]].append(payload)
            for collection in collections.values():
                collection.sort(
                    key=lambda item: parse_timestamp(item.get("updated_at")),
                    reverse=True,
                )
            history_entries.sort(
                key=lambda item: parse_timestamp(item.get("updated_at")),
                reverse=True,
            )
            self.app_config.replace_cloud_snapshot(
                {"preferences": preferences, **collections}
            )
            self.history.replace_entries(history_entries)
        return changed

    def _remote_items(self, client, session):
        return client.select(
            "ditado_sync_items",
            session["access_token"],
            params={
                "select": (
                    "user_id,item_type,item_id,ciphertext,updated_at,"
                    "deleted_at,device_id"
                ),
                "user_id": f"eq.{session['user_id']}",
                "order": "updated_at.asc",
            },
        )

    def _push_outbox(self, client, session, master_key, bucket):
        outbox = bucket.setdefault("outbox", {})
        if not outbox:
            return 0
        rows = []
        for pending in outbox.values():
            ciphertext = ""
            if pending.get("payload") is not None:
                ciphertext = encrypt_record(
                    master_key,
                    session["user_id"],
                    pending["item_type"],
                    pending["item_id"],
                    pending["payload"],
                )
            rows.append(
                {
                    "user_id": session["user_id"],
                    "item_type": pending["item_type"],
                    "item_id": pending["item_id"],
                    "ciphertext": ciphertext,
                    "updated_at": pending["updated_at"],
                    "deleted_at": pending.get("deleted_at"),
                    "device_id": pending["device_id"],
                }
            )
        client.upsert(
            "ditado_sync_items",
            rows,
            session["access_token"],
            "user_id,item_type,item_id",
        )
        pushed = len(rows)
        outbox.clear()
        return pushed

    def _register_device(self, client, session):
        device_id = self.state.data["device_id"]
        rows = client.select(
            "ditado_devices",
            session["access_token"],
            params={
                "select": "device_id,revoked_at",
                "user_id": f"eq.{session['user_id']}",
                "device_id": f"eq.{device_id}",
                "limit": "1",
            },
        )
        if rows and rows[0].get("revoked_at"):
            raise DeviceRevokedError(
                "Este dispositivo foi removido da conta. Entre novamente em outro dispositivo."
            )
        client.upsert(
            "ditado_devices",
            [
                {
                    "user_id": session["user_id"],
                    "device_id": device_id,
                    "name": self.state.data.get("device_name") or "PC Windows",
                    "platform": platform.platform()[:200],
                    "session_id": session.get("session_id") or None,
                    "last_seen": utc_now(),
                    "revoked_at": None,
                }
            ],
            session["access_token"],
            "user_id,device_id",
        )

    def sync_once(self, recovery_code=None):
        if not self.sync_lock.acquire(blocking=False):
            raise CloudError("Uma sincronização já está em andamento.")
        try:
            session = self._valid_session()
            master_key, new_recovery_code = self.ensure_master_key(recovery_code)
            bucket = self.state.sync_bucket(session["user_id"])
            first_sync = not bucket.get("manifest") and not bucket.get("last_sync")
            self._observe_local(session["user_id"], bucket)
            client = self._client()
            try:
                self._register_device(client, session)
                remote_before = self._remote_items(client, session)
                self._merge_remote(
                    session["user_id"],
                    master_key,
                    remote_before,
                    bucket,
                    prefer_remote=first_sync,
                )
                self._observe_local(session["user_id"], bucket)
                pushed = self._push_outbox(client, session, master_key, bucket)
                remote_after = self._remote_items(client, session)
                changed = self._merge_remote(
                    session["user_id"],
                    master_key,
                    remote_after,
                    bucket,
                )
            finally:
                client.close()
            bucket["last_sync"] = utc_now()
            self.state.set_sync_bucket(session["user_id"], bucket)
            self.last_error = ""
            return {
                "pushed": pushed,
                "remote_changed": changed,
                "last_sync": bucket["last_sync"],
                "recovery_code": new_recovery_code,
            }
        except CloudError as error:
            self.last_error = str(error)
            raise
        finally:
            self.sync_lock.release()

    def list_devices(self):
        session = self._valid_session()
        client = self._client()
        try:
            return client.select(
                "ditado_devices",
                session["access_token"],
                params={
                    "select": "device_id,name,platform,last_seen,revoked_at",
                    "user_id": f"eq.{session['user_id']}",
                    "order": "last_seen.desc",
                },
            )
        finally:
            client.close()

    def revoke_device(self, device_id):
        session = self._valid_session()
        if device_id == self.state.data.get("device_id"):
            raise CloudError("Use Sair da conta para remover este próprio dispositivo.")
        client = self._client()
        try:
            return client.rpc(
                "ditado_revoke_device",
                {"p_device_id": device_id},
                session["access_token"],
            )
        finally:
            client.close()

    def status(self):
        session = self.state.active_session()
        backend = self.state.backend()
        bucket = self.state.sync_bucket(session["user_id"]) if session else {}
        return {
            "configured": bool(backend.get("url") and backend.get("publishable_key")),
            "signed_in": bool(session),
            "email": session.get("email", "") if session else "",
            "user_id": session.get("user_id", "") if session else "",
            "last_sync": bucket.get("last_sync", ""),
            "pending": len(bucket.get("outbox", {})),
            "has_local_key": bool(
                session and self.state.master_key(session["user_id"])
            ),
            "accounts": self.state.accounts(),
            "last_error": self.last_error,
        }
