from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).parents[1]))

from skill_runtime import discover_skills, get_skill, skill_index, system_prompt


SKILLS_DIR = Path(__file__).parents[1] / "skills"


class SkillRuntimeTests(unittest.TestCase):
    def test_discovers_skill_metadata_without_loading_other_files(self):
        skills = discover_skills(SKILLS_DIR)

        self.assertEqual([skill.name for skill in skills], ["incident-triage", "release-notes"])
        self.assertIn("production incident", skills[0].description)


    def test_full_skill_body_is_loaded_by_exact_name(self):
        skill = get_skill(discover_skills(SKILLS_DIR), "incident-triage")

        self.assertIn("Confirmed facts", skill.body)
        self.assertEqual(skill.path.name, "SKILL.md")


    def test_unknown_skill_has_available_names(self):
        with self.assertRaisesRegex(ValueError, "incident-triage"):
            get_skill(discover_skills(SKILLS_DIR), "does-not-exist")


    def test_prompt_contains_metadata_but_not_full_procedure(self):
        skills = discover_skills(SKILLS_DIR)
        prompt = system_prompt(skills)

        self.assertIn("incident-triage", prompt)
        self.assertNotIn("Confirmed facts", prompt)
        self.assertIn(skill_index(skills), prompt)


if __name__ == "__main__":
    unittest.main()
