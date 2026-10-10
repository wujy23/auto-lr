import tempfile
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main as h


class SkillBindingTests(unittest.TestCase):
    def test_real_skills_resolve_all_references_and_have_separate_workflows(self):
        planner_name, planner_files = h.skill_files(h.SKILL_DEFAULTS['planner'])
        reviewer_name, reviewer_files = h.skill_files(h.SKILL_DEFAULTS['reviewer'])
        self.assertEqual(planner_name, 'lightroom-style-planner')
        self.assertEqual(reviewer_name, 'lightroom-result-reviewer')
        self.assertIn('references/parameter-generation.md', planner_files)
        self.assertIn('references/tone-color-analysis.md', planner_files)
        self.assertEqual(set(reviewer_files), {'SKILL.md'})

    def test_snapshot_survives_source_change_but_rejects_snapshot_tampering(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'source'
            source.mkdir()
            entry = source / 'SKILL.md'
            entry.write_text('---\nname: test-skill\ndescription: Test\n---\nOriginal workflow.\n')
            run = root / 'run'
            h.snapshot_skills({'planner_skill': str(source), 'reviewer_skill': str(source)}, run)
            entry.write_text('Changed future workflow.')
            binding, text = h.load_agent_skill(run, 'planner')
            self.assertIn('Original workflow.', text)
            snapshot = run / binding['directory'] / 'SKILL.md'
            snapshot.write_text('Tampered snapshot')
            with self.assertRaisesRegex(h.WorkflowError, 'Skill snapshot changed'):
                h.load_agent_skill(run, 'planner')

    def test_missing_linked_reference_and_escaping_reference_are_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for link in ['references/missing.md', '../outside.md']:
                with self.subTest(link=link):
                    (root / 'SKILL.md').write_text(f'---\nname: test-skill\ndescription: Test\n---\n[read]({link})')
                    with self.assertRaises(h.WorkflowError):
                        h.skill_files(root)

    def test_unknown_agent_role_is_not_silently_treated_as_reviewer(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(h.WorkflowError, 'Unknown agent role'):
                h.load_agent_skill(Path(tmp), 'writer')


if __name__ == '__main__':
    unittest.main()
