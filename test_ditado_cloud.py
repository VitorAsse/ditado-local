import copy
import base64
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

import ditado_cloud
import ditado_storage
from ditado_cloud import (
    CloudError,
    CloudStateStore,
    CloudSyncManager,
    InvalidRecoveryKey,
    RecoveryKeyRequired,
    SupabaseCloudClient,
    decrypt_record,
    encrypt_record,
    generate_recovery_code,
    unwrap_master_key,
    wrap_master_key,
)
from ditado_storage import AppConfig, HistoryStore


class FakeSupabaseClient:
    def __init__(self):
        self.user_keys = {}
        self.sync_items = {}
        self.devices = {}

    def close(self):
        return None

    @staticmethod
    def _filter_value(params, key):
        value = (params or {}).get(key, "")
        return value[3:] if isinstance(value, str) and value.startswith("eq.") else value

    def select(self, table, _access_token, params=None):
        user_id = self._filter_value(params, "user_id")
        if table == "ditado_user_keys":
            row = self.user_keys.get(user_id)
            return [copy.deepcopy(row)] if row else []
        if table == "ditado_sync_items":
            rows = [
                copy.deepcopy(row)
                for key, row in self.sync_items.items()
                if key[0] == user_id
            ]
            rows.sort(key=lambda row: row["updated_at"])
            return rows
        if table == "ditado_devices":
            device_id = self._filter_value(params, "device_id")
            rows = [
                copy.deepcopy(row)
                for key, row in self.devices.items()
                if key[0] == user_id and (not device_id or key[1] == device_id)
            ]
            return rows
        raise AssertionError(f"Unexpected table: {table}")

    def upsert(self, table, rows, _access_token, _on_conflict):
        results = []
        for row in rows:
            row = copy.deepcopy(row)
            if table == "ditado_user_keys":
                self.user_keys[row["user_id"]] = row
            elif table == "ditado_sync_items":
                key = (row["user_id"], row["item_type"], row["item_id"])
                old = self.sync_items.get(key)
                new_order = (row["updated_at"], row["device_id"])
                old_order = (
                    (old["updated_at"], old["device_id"])
                    if old
                    else ("", "")
                )
                if new_order >= old_order:
                    self.sync_items[key] = row
                row = self.sync_items[key]
            elif table == "ditado_devices":
                key = (row["user_id"], row["device_id"])
                self.devices[key] = row
            else:
                raise AssertionError(f"Unexpected table: {table}")
            results.append(copy.deepcopy(row))
        return results

    def update(self, table, values, _access_token, params):
        if table != "ditado_devices":
            raise AssertionError(f"Unexpected table: {table}")
        key = (
            self._filter_value(params, "user_id"),
            self._filter_value(params, "device_id"),
        )
        self.devices[key].update(copy.deepcopy(values))
        return [copy.deepcopy(self.devices[key])]

    def rpc(self, function_name, values, _access_token):
        if function_name != "ditado_revoke_device":
            raise AssertionError(f"Unexpected function: {function_name}")
        device_id = values["p_device_id"]
        for row in self.devices.values():
            if row["device_id"] == device_id:
                row["revoked_at"] = ditado_cloud.utc_now()
                return True
        return False


def auth_payload(user_id, email):
    session_id = (
        "11111111-1111-1111-1111-111111111111"
        if user_id == "user-a"
        else "22222222-2222-2222-2222-222222222222"
    )
    payload = base64.urlsafe_b64encode(
        json.dumps({"sub": user_id, "session_id": session_id}).encode("utf-8")
    ).decode("ascii").rstrip("=")
    return {
        "access_token": f"header.{payload}.signature",
        "refresh_token": f"refresh-{user_id}",
        "expires_at": int(time.time()) + 3600,
        "user": {"id": user_id, "email": email},
    }


class CloudAuthenticationTests(unittest.TestCase):
    def test_resend_confirmation_uses_the_supabase_signup_endpoint(self):
        observed = {}

        def handler(request):
            observed["path"] = request.url.path
            observed["payload"] = json.loads(request.content)
            return httpx.Response(200, json={})

        client = SupabaseCloudClient(
            "https://test.supabase.co",
            "sb_publishable_test",
            transport=httpx.MockTransport(handler),
        )
        try:
            client.resend_signup_confirmation("user@example.com")
        finally:
            client.close()

        self.assertEqual("/auth/v1/resend", observed["path"])
        self.assertEqual(
            {"type": "signup", "email": "user@example.com"},
            observed["payload"],
        )

    def test_manager_normalizes_email_before_resending_confirmation(self):
        temporary_directory = tempfile.TemporaryDirectory()
        root = Path(temporary_directory.name)
        config = AppConfig(path=root / "config.json")
        history = HistoryStore(path=root / "history.dat")
        state = CloudStateStore(path=root / "cloud_state.dat")
        state.configure_backend("https://test.supabase.co", "sb_publishable_test")
        manager = CloudSyncManager(state, config, history)

        class FakeAuthClient:
            def __init__(self):
                self.email = ""

            def resend_signup_confirmation(self, email):
                self.email = email

            def close(self):
                return None

        backend = FakeAuthClient()
        manager._client = lambda: backend
        try:
            result = manager.resend_signup_confirmation("  User@Example.COM ")
        finally:
            temporary_directory.cleanup()

        self.assertEqual("user@example.com", backend.email)
        self.assertEqual({"email": "user@example.com"}, result)


class CloudCryptoTests(unittest.TestCase):
    def test_record_round_trip_is_authenticated_and_does_not_expose_plaintext(self):
        key = bytes(range(32))
        payload = {"id": "one", "text": "conteúdo privado"}

        ciphertext = encrypt_record(key, "user-a", "history", "one", payload)
        restored = decrypt_record(
            key,
            "user-a",
            "history",
            "one",
            ciphertext,
        )

        self.assertNotIn("conteúdo privado", ciphertext)
        self.assertEqual(payload, restored)
        with self.assertRaises(CloudError):
            decrypt_record(key, "user-b", "history", "one", ciphertext)

    def test_recovery_code_wraps_master_key_and_rejects_a_different_code(self):
        master_key = bytes(reversed(range(32)))
        recovery_code = generate_recovery_code()
        wrapped = wrap_master_key(master_key, recovery_code, "user-a")

        self.assertEqual(
            master_key,
            unwrap_master_key(wrapped, recovery_code, "user-a"),
        )
        with self.assertRaises(InvalidRecoveryKey):
            unwrap_master_key(wrapped, generate_recovery_code(), "user-a")


class CloudSyncTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.patches = [
            patch.object(ditado_cloud, "PROFILES_ROOT", self.root / "profiles"),
            patch.object(
                ditado_storage,
                "protect_for_current_user",
                side_effect=lambda value: value,
            ),
            patch.object(
                ditado_storage,
                "unprotect_for_current_user",
                side_effect=lambda value: value,
            ),
            patch.object(
                ditado_cloud,
                "protect_for_current_user",
                side_effect=lambda value: value,
            ),
            patch.object(
                ditado_cloud,
                "unprotect_for_current_user",
                side_effect=lambda value: value,
            ),
        ]
        for active_patch in self.patches:
            active_patch.start()
        self.backend = FakeSupabaseClient()

    def tearDown(self):
        for active_patch in reversed(self.patches):
            active_patch.stop()
        self.temporary_directory.cleanup()

    def _manager(self, name, user_id="user-a", email="a@example.com"):
        local_root = self.root / name
        config = AppConfig(path=local_root / "config.json")
        history = HistoryStore(path=local_root / "history.dat")
        state = CloudStateStore(path=local_root / "cloud_state.dat")
        state.configure_backend("https://test.supabase.co", "sb_publishable_test")
        state.put_session(auth_payload(user_id, email), email=email)
        manager = CloudSyncManager(state, config, history)
        manager._client = lambda: self.backend
        return manager, config, history, state

    def test_second_device_requires_recovery_key_then_restores_private_data(self):
        first, first_config, first_history, _first_state = self._manager("first")
        first_config.add_correction("open ai", "OpenAI")
        first_config.set("auto_paste", False)
        first_history.add("transcrição secreta", "transcription")

        first_result = first.sync_once()
        recovery_code = first_result["recovery_code"]

        cloud_blob = json.dumps(list(self.backend.sync_items.values()))
        self.assertNotIn("transcrição secreta", cloud_blob)
        self.assertNotIn("OpenAI", cloud_blob)

        second, second_config, second_history, _second_state = self._manager("second")
        with self.assertRaises(RecoveryKeyRequired):
            second.sync_once()

        second.sync_once(recovery_code=recovery_code)

        self.assertEqual(
            "OpenAI",
            second_config.get("corrections")[0]["correct"],
        )
        self.assertFalse(second_config.get("auto_paste"))
        self.assertEqual("transcrição secreta", second_history.all()[0]["text"])

    def test_accounts_are_isolated_by_user_id(self):
        first, first_config, _first_history, _first_state = self._manager("user-a")
        first_config.add_correction("acme", "Acme A")
        first.sync_once()

        second, second_config, _second_history, _second_state = self._manager(
            "user-b",
            user_id="user-b",
            email="b@example.com",
        )
        second_config.add_correction("acme", "Acme B")
        second.sync_once()

        first_rows = [key for key in self.backend.sync_items if key[0] == "user-a"]
        second_rows = [key for key in self.backend.sync_items if key[0] == "user-b"]
        self.assertTrue(first_rows)
        self.assertTrue(second_rows)
        self.assertFalse(set(first_rows) & set(second_rows))

    def test_deleted_local_record_becomes_a_cloud_tombstone(self):
        manager, config, _history, _state = self._manager("deletion")
        config.add_correction("errado", "certo")
        manager.sync_once()
        correction_id = config.get("corrections")[0]["id"]

        config.remove_correction("errado")
        manager.sync_once()

        row = self.backend.sync_items[("user-a", "correction", correction_id)]
        self.assertIsNotNone(row["deleted_at"])
        self.assertEqual("", row["ciphertext"])

    def test_device_registration_tracks_and_revokes_its_auth_session(self):
        manager, _config, _history, state = self._manager("devices")
        manager.sync_once()
        first_device_id = state.data["device_id"]
        registered = self.backend.devices[("user-a", first_device_id)]

        self.assertEqual(
            "11111111-1111-1111-1111-111111111111",
            registered["session_id"],
        )

        second_device_id = "99999999-9999-9999-9999-999999999999"
        self.backend.devices[("user-a", second_device_id)] = {
            "user_id": "user-a",
            "device_id": second_device_id,
            "name": "Outro PC",
            "platform": "Windows",
            "session_id": "33333333-3333-3333-3333-333333333333",
            "last_seen": ditado_cloud.utc_now(),
            "revoked_at": None,
        }
        manager.revoke_device(second_device_id)

        self.assertIsNotNone(
            self.backend.devices[("user-a", second_device_id)]["revoked_at"]
        )

    def test_profile_paths_are_stable_and_distinct_for_each_account(self):
        state = CloudStateStore(path=self.root / "profiles-state.dat")

        first = state.profile_paths("11111111-1111-1111-1111-111111111111")
        second = state.profile_paths("22222222-2222-2222-2222-222222222222")

        self.assertNotEqual(first, second)
        self.assertEqual(first, state.profile_paths("11111111-1111-1111-1111-111111111111"))


class CloudSchemaTests(unittest.TestCase):
    def test_schema_enables_rls_and_checks_auth_uid_on_all_tables(self):
        sql = (
            Path(__file__).with_name("supabase") / "ditado_cloud_schema.sql"
        ).read_text(encoding="utf-8")

        for table in ("ditado_user_keys", "ditado_sync_items", "ditado_devices"):
            self.assertIn(f"alter table public.{table} enable row level security", sql)
        self.assertGreaterEqual(sql.count("(select auth.uid()) = user_id"), 11)
        self.assertNotIn("service_role", sql)
        self.assertIn("grant select, insert, update", sql)
        self.assertIn("private.ditado_session_is_active()", sql)
        self.assertIn("delete from auth.sessions", sql)
        self.assertIn("security invoker", sql)
        self.assertIn(
            "revoke execute on function public.rls_auto_enable() from anon",
            sql,
        )
        self.assertIn("procedure.prorettype = 'event_trigger'::regtype", sql)
        self.assertIn("event_trigger.evtfoid = auto_rls_function_oid", sql)

        revoke_function = sql.split(
            "create or replace function private.ditado_revoke_owned_device", 1
        )[1].split("$$;", 1)[0]
        self.assertIn(
            "if not private.ditado_session_is_active()", revoke_function
        )


class SecureBootstrapTests(unittest.TestCase):
    def test_bootstrap_protects_the_key_without_using_the_app_python(self):
        script = (
            Path(__file__).with_name("scripts")
            / "configure-supabase-secure.ps1"
        ).read_text(encoding="utf-8")
        launcher = (
            Path(__file__).with_name("scripts")
            / "configure-supabase-secure.cmd"
        ).read_text(encoding="utf-8")

        self.assertIn("Read-Host", script)
        self.assertIn("-AsSecureString", script)
        self.assertIn("ZeroFreeBSTR", script)
        self.assertIn("ProtectedData]::Unprotect", script)
        self.assertIn("ProtectedData]::Protect", script)
        self.assertIn('Join-Path $installRoot "cloud_state.dat"', script)
        self.assertNotIn(".venv\\Scripts\\python.exe", script)
        self.assertIn("powershell.exe -NoLogo -NoProfile", launcher)


if __name__ == "__main__":
    unittest.main()
