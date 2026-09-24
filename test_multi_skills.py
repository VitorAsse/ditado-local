import unittest
from unittest.mock import Mock

from ditado_ai import OllamaClient, select_voice_skills
from ditado_harness import active_skills, make_system, route_skills


def skill(name, kind="primary", trigger=None):
    return dict(id=name, name=name, kind=kind, triggers=[trigger or name],
                instructions="Apply " + name + ".", output_mode="auto", enabled=True)


class MultiSkillTests(unittest.TestCase):
    def setUp(self):
        self.skills = [skill("n8n"), skill("JavaScript"),
                       skill("PurpleFire", "modifier"), skill("English", "modifier")]
        self.client = OllamaClient()
        self.client._generate_checked = Mock(return_value="Resultado completo.")

    def test_four_distinct_skills_reach_model_and_saved_conversation(self):
        _, conversation = self.client.start_selected_text_conversation(
            "Conteúdo.", "n8n JavaScript PurpleFire English", skills=self.skills)
        selected = conversation["harness"]["skills"]
        self.assertEqual(4, len(selected))
        system = self.client._generate_checked.call_args.args[0]
        for entry in self.skills:
            self.assertIn(entry["instructions"], system)
        self.assertEqual(selected, select_voice_skills(
            "n8n JavaScript PurpleFire English", self.skills))

    def test_free_chat_also_composes_four(self):
        _, conversation = self.client.start_free_conversation(
            "n8n JavaScript PurpleFire English", skills=self.skills)
        self.assertEqual(4, len(conversation["harness"]["skills"]))

    def test_fifth_skill_fails_before_generation(self):
        with self.assertRaisesRegex(ValueError, "mais de 4"):
            self.client.start_free_conversation(
                "n8n JavaScript PurpleFire English SQL", skills=self.skills + [skill("SQL")])
        self.client._generate_checked.assert_not_called()

    def test_duplicate_ids_and_multiple_triggers_do_not_consume_slots(self):
        self.skills[0]["triggers"].append("fluxo")
        selected = active_skills(route_skills(
            "n8n fluxo JavaScript PurpleFire English", self.skills + [dict(self.skills[0])]))
        self.assertEqual(4, len(selected))

    def test_disabled_skill_does_not_consume_slot(self):
        extra = skill("SQL")
        extra["enabled"] = False
        self.assertEqual(4, len(select_voice_skills(
            "n8n JavaScript PurpleFire English SQL", self.skills + [extra])))

    def test_overlap_abstains_but_explicit_composition_is_allowed(self):
        skills = [skill("A", trigger="mensagem para"),
                  skill("B", trigger="mensagem para equipe")]
        route = route_skills("mensagem para equipe", skills)
        self.assertTrue(route["ambiguous"])
        self.assertEqual([], active_skills(route))
        self.assertEqual(2, len(select_voice_skills("skill A e skill B", skills)))

    def test_instruction_budget_is_enforced_without_truncation(self):
        for entry in self.skills:
            entry["instructions"] = "x" * 2000
        self.assertEqual(8000, sum(len(s["instructions"]) for s in
                                  select_voice_skills("n8n JavaScript PurpleFire English", self.skills)))
        self.skills[0]["instructions"] += "x"
        with self.assertRaisesRegex(ValueError, "8000"):
            make_system([], self.skills)
        self.assertEqual(2001, len(self.skills[0]["instructions"]))

    def test_direct_system_construction_cannot_bypass_count_limit(self):
        with self.assertRaisesRegex(ValueError, "mais de 4"):
            make_system([], self.skills + [skill("SQL")])

    def test_follow_up_adds_style_without_losing_context_and_deduplicates(self):
        _, conversation = self.client.start_free_conversation(
            "n8n JavaScript PurpleFire", skills=self.skills)
        _, updated = self.client.continue_selected_text_conversation(
            conversation, "English", skills=self.skills)
        self.assertEqual(4, len(updated["harness"]["skills"]))
        _, repeated = self.client.continue_selected_text_conversation(
            updated, "English", skills=self.skills)
        self.assertEqual(4, len(repeated["harness"]["skills"]))

    def test_follow_up_cannot_accumulate_fifth_skill(self):
        _, conversation = self.client.start_free_conversation(
            "n8n JavaScript PurpleFire English", skills=self.skills)
        self.client._generate_checked.reset_mock()
        with self.assertRaisesRegex(ValueError, "mais de 4"):
            self.client.continue_selected_text_conversation(
                conversation, "Brief", skills=self.skills + [skill("Brief", "modifier")])
        self.client._generate_checked.assert_not_called()
        self.assertEqual(4, len(conversation["harness"]["skills"]))

    def test_disabled_saved_skill_is_removed_before_enforcing_follow_up_limit(self):
        _, conversation = self.client.start_free_conversation(
            "n8n JavaScript PurpleFire English", skills=self.skills)
        self.skills[-1]["enabled"] = False
        _, updated = self.client.continue_selected_text_conversation(
            conversation, "Brief", skills=self.skills + [skill("Brief", "modifier")])
        self.assertEqual(4, len(updated["harness"]["skills"]))
        self.assertNotIn("English", [s["name"] for s in updated["harness"]["skills"]])


if __name__ == "__main__":
    unittest.main()
