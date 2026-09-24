import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ditado_harness import active_skills, route_skills
from ditado_skill_catalog import load_builtin_skills, validate_catalog
from ditado_storage import AppConfig


class SkillCatalogTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.config = AppConfig(Path(self.folder.name) / "config.json")
        self.builtins = load_builtin_skills()

    def test_library_contains_only_generic_skills_and_each_trigger_routes(self):
        self.assertEqual(18, len(self.builtins))
        serialized = json.dumps(self.builtins).casefold()
        for private in ("agencify", "purplefire", "joinbrands", "vitor", "weekly update"):
            self.assertNotIn(private, serialized)
        for entry in self.builtins:
            for trigger in entry["triggers"]:
                with self.subTest(skill=entry["name"], trigger=trigger):
                    selected = active_skills(route_skills(trigger, self.builtins))
                    self.assertEqual([entry["id"]], [s["id"] for s in selected])

    def test_library_addition_is_idempotent_and_sync_snapshot_contains_copies(self):
        self.assertEqual(18, self.config.add_builtin_skills())
        first = self.config.get_skills()
        self.assertEqual(0, self.config.add_builtin_skills())
        self.assertEqual(first, self.config.get_skills())
        self.assertEqual(first, self.config.cloud_snapshot()["skills"])

    def test_personalized_same_name_is_preserved_even_with_different_id_and_disabled(self):
        entry = self.builtins[0]
        personal_id = self.config.save_skill(
            None, entry["name"], "Minha descrição", ["gatilho pessoal"],
            "Minhas instruções.", [], kind=entry["kind"])
        self.config.set_skill_enabled(personal_id, False)
        before = self.config.get_skills()[0]
        self.assertEqual(17, self.config.add_builtin_skills())
        self.assertEqual(before, next(s for s in self.config.get_skills() if s["id"] == personal_id))

    def test_renamed_builtin_is_not_reinstalled_by_id(self):
        self.config.add_builtin_skills()
        self.config.data["skills"][0]["name"] = "Nome pessoal"
        self.config.data["skills"][0]["instructions"] = "Minha versão."
        self.config.save()
        self.assertEqual(0, self.config.add_builtin_skills())
        self.assertEqual("Minha versão.", self.config.get_skills()[0]["instructions"])

    def test_capacity_failure_does_not_partially_add(self):
        for n in range(13):
            self.config.save_skill(None, f"Pessoal {n}", "Descrição", ["pessoal"],
                                   "Instruções.", [])
        before = self.config.path.read_bytes()
        with self.assertRaisesRegex(ValueError, "30"):
            self.config.add_builtin_skills()
        self.assertEqual(before, self.config.path.read_bytes())
        self.assertEqual(13, len(self.config.get_skills()))

    def test_explicit_import_replaces_only_authorized_skills(self):
        keep = self.config.save_skill(None, "Manter", "Descrição", ["manter"], "Não mudar.", [])
        replace = self.config.save_skill(None, "Antiga", "Descrição", ["antiga"], "Antiga.", [])
        self.config.set("microphone_name", "Microfone preservado")
        self.config.import_skills(self.builtins[:1], replace_ids=[replace])
        self.assertEqual({keep, self.builtins[0]["id"]}, {s["id"] for s in self.config.get_skills()})
        self.assertEqual("Microfone preservado", self.config.get("microphone_name"))

    def test_unexpected_existing_name_or_stale_replacement_is_rejected(self):
        self.config.add_builtin_skills()
        before = self.config.path.read_bytes()
        with self.assertRaises(ValueError):
            self.config.import_skills(self.builtins[:1])
        with self.assertRaises(ValueError):
            self.config.import_skills([], replace_ids=["not-present"])
        self.assertEqual(before, self.config.path.read_bytes())

    def test_failed_write_restores_in_memory_state(self):
        with patch.object(self.config, "save", side_effect=OSError("disk")):
            with self.assertRaises(OSError):
                self.config.add_builtin_skills()
        self.assertEqual([], self.config.get_skills())

    def test_invalid_payload_never_mutates_profile(self):
        invalid = copy.deepcopy(self.builtins)
        invalid[-1]["instructions"] = "x" * 4001
        before = self.config.path.read_bytes()
        with self.assertRaises(ValueError):
            self.config.import_skills(invalid)
        self.assertEqual(before, self.config.path.read_bytes())
        with self.assertRaises(ValueError):
            validate_catalog(self.builtins + [self.builtins[0]])


if __name__ == "__main__":
    unittest.main()
