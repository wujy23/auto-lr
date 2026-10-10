"""Goal authority, reviewer isolation, and archived replay invariants."""
import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from test_main import h, FakeAgents, FakeLR, fixed_goal, proposal, review


class GoalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        target = self.root / 'photo.raw'; target.touch()
        self.config = {'target': str(target), 'photo_id': None, 'references': [], 'style': 'test',
                       'max_corrections': 2, 'export_dir': str(self.root / 'exports')}
        self.j = h.Journal(self.root)
        self.lr = FakeLR(self.root, target)
        self.agents = FakeAgents()
        self.app = h.Harness(self.config, self.j, self.lr, self.agents)

    def tearDown(self):
        self.tmp.cleanup()

    def test_goal_is_created_before_parameters(self):
        pending = self.app.prepare()
        self.assertEqual(pending['stage'], 'goal')
        self.assertEqual(self.agents.inputs[0][1]['phase'], 'define_goal')
        self.assertEqual(self.agents.inputs[0][1]['available_controls'], ['Exposure2012'])
        self.assertNotIn('baseline', self.agents.inputs[0][1])
        self.assertEqual(len(self.agents.inputs), 1)
        self.assertFalse((self.root / 'round-00').exists())
        self.assertFalse(self.lr.applied)
        self.assertEqual(h.load(self.root / 'goal.json'), fixed_goal())
        planned = self.app.plan_from_goal(pending)
        self.assertEqual(planned['stage'], 'plan')
        self.assertEqual(self.agents.inputs[1][1]['goal'], fixed_goal())
        self.assertNotIn('criteria', planned['proposal'])

    def test_changed_goal_before_planning_blocks_planner(self):
        pending = self.app.prepare()
        changed = fixed_goal(); changed['criteria'][0]['target'] = 'different'
        h.save(self.root / 'goal.json', changed)
        with self.assertRaisesRegex(h.WorkflowError, 'Fixed goal changed'):
            self.app.execute(pending)
        self.assertEqual(len(self.agents.inputs), 1)
        self.assertFalse(self.lr.applied)

    def test_change_during_planner_blocks_write(self):
        pending = self.app.prepare()
        self.agents.on_planner = lambda: h.save(self.root / 'goal.json', {**fixed_goal(), 'style_summary': 'tampered'})
        with self.assertRaisesRegex(h.WorkflowError, 'Fixed goal changed'):
            self.app.execute(pending)
        self.assertFalse(self.lr.applied)

    def test_supplied_goal_skips_goal_model_before_execution(self):
        path = self.root / 'supplied.json'; h.save(path, fixed_goal())
        self.config['goal_file'] = str(path)
        pending = self.app.prepare()
        self.assertFalse(self.agents.inputs)
        self.assertEqual(pending['stage'], 'goal')
        self.assertFalse(self.lr.applied)

    def test_reviewer_has_goal_without_prior_labels_or_planner_story(self):
        agents = FakeAgents([proposal(), proposal(.3)], [review('revise'), review()])
        app = h.Harness(self.config, self.j, self.lr, agents)
        app.execute(app.prepare())
        contexts = [c for role, c, _ in agents.inputs if role == 'reviewer']
        self.assertEqual(contexts[0]['goal'], contexts[1]['goal'])
        self.assertEqual(contexts[0], contexts[1])
        for ctx in contexts:
            self.assertTrue(ctx['execution_verified'])
            for forbidden in ['proposal', 'baseline', 'result', 'previous_review', 'review_feedback',
                              'previous_unresolved_ids', 'cycle_history', 'replanning']:
                self.assertNotIn(forbidden, ctx)

    def test_correction_uses_verified_facts_and_replans_after_two_unmet_reviews(self):
        first = proposal()
        first['visual_analysis'] = 'OLD_PLANNER_STORY'
        first['edit_hypotheses'] = ['OLD_PLANNER_RATIONALE']
        agents = FakeAgents([first, proposal(.3), proposal(.4)],
                            [review('revise'), review('revise'), review()])
        app = h.Harness(self.config, self.j, self.lr, agents)
        app.execute(app.prepare())
        plans = [c for role, c, _ in agents.inputs if c['phase'] == 'plan']
        self.assertEqual(plans[0]['cycle_history'], [])
        self.assertFalse(plans[0]['replanning']['required'])
        self.assertFalse(plans[1]['replanning']['required'])
        self.assertEqual(plans[2]['replanning'], {'required': True, 'criterion_ids': ['subject']})
        self.assertEqual(plans[2]['goal'], fixed_goal())
        self.assertEqual(plans[2]['review_feedback'], review('revise'))
        self.assertEqual(plans[2]['cycle_history'][0]['verified_controls']['settings'],
                         {'Exposure2012': {'before': 0.0, 'after': .2}})
        self.assertEqual(plans[2]['cycle_history'][1]['verified_controls']['settings'],
                         {'Exposure2012': {'before': .2, 'after': .3}})
        for ctx in plans:
            self.assertNotIn('previous_plan', ctx)
            self.assertNotIn('OLD_PLANNER', h.json.dumps(ctx))
        compared = h.load(self.root / 'round-01/review-comparison.json')
        self.assertEqual(compared['criteria'][0]['consecutive_unmet'], 2)
        self.assertEqual(compared['criteria'][0]['change'], 'improved')
        self.assertEqual(h.load(self.root / 'round-02/review-comparison.json')['replan_criteria'], [])
        self.assertEqual(self.j.state['status'], 'complete')

    def test_review_streaks_reset_on_met_or_unknown_and_track_regression(self):
        previous = None
        for result, expected, regressed in [('unmet', 1, False), ('unmet', 2, False),
                                            ('unknown', 0, False), ('unmet', 1, False),
                                            ('met', 0, False), ('unmet', 1, True)]:
            value = review('revise')
            value['criteria'][0]['result'] = result
            previous = h.compare_reviews(value, previous)
            self.assertEqual(previous['criteria'][0]['consecutive_unmet'], expected)
            self.assertEqual(previous['criteria'][0]['regressed'], regressed)
            self.assertEqual(previous['replan_criteria'], ['subject'] if expected >= 2 else [])

    def test_failed_write_never_enters_verified_cycle_history(self):
        self.lr.fail_after_write = True
        with self.assertRaises(TimeoutError):
            self.app.execute(self.app.prepare())
        self.assertNotIn('cycle_history', self.j.state)
        self.assertFalse((self.root / 'round-00/review-comparison.json').exists())

    def test_cycle_record_preserves_curve_and_mask_operations_without_prose(self):
        mask = {'id': 'mask', 'name': 'test', 'type': 'radial', 'geometry': [0, 0, 1, 1, 80, 1],
                'settings': {'ExposureEV': .3}}
        wire = {'settings': {}, 'curves': {'ToneCurvePV2012': [[0, 0], [255, 255]]}, 'masks': [mask]}
        value = review('revise')
        record = h.cycle_record(0, wire, {}, value, h.compare_reviews(value))
        self.assertEqual(record['verified_controls']['masks'], [mask])
        self.assertEqual(record['verified_controls']['curves']['ToneCurvePV2012']['after'],
                         [[0, 0], [255, 255]])
        mask['settings']['ExposureEV'] = 1
        self.assertEqual(record['verified_controls']['masks'][0]['settings']['ExposureEV'], .3)

    def test_improved_does_not_imply_met(self):
        value = review('revise'); value['criteria'][0]['change'] = 'improved'
        h.validate_review(value, fixed_goal()['criteria'])
        value['verdict'] = 'pass'
        with self.assertRaises(h.WorkflowError):
            h.validate_review(value, fixed_goal()['criteria'])

    def test_residual_gap_cannot_be_labelled_met(self):
        value = review(); value['criteria'][0]['remaining_gap'] = 'still green'
        with self.assertRaisesRegex(h.WorkflowError, 'remaining gap'):
            h.validate_review(value, fixed_goal()['criteria'])

    def test_goal_mode_loads_no_parameter_recipes(self):
        h.snapshot_skills({}, self.root)
        _, goal_text = h.load_agent_skill(self.root, 'planner', 'define_goal')
        _, plan_text = h.load_agent_skill(self.root, 'planner', 'plan')
        self.assertNotIn('--- Skill file: references/parameter-generation.md ---', goal_text)
        self.assertIn('--- Skill file: references/parameter-generation.md ---', plan_text)

    def archived_run(self):
        ref = self.root / 'inputs/reference-01.jpg'
        ref.parent.mkdir(); ref.write_bytes(b'archived reference fixture')
        h.save(self.root / 'manifest.json', {'inputs/reference-01.jpg': h.file_hash(ref)})
        self.app.execute(self.app.prepare())
        args = h.parser().parse_args(['review-run', '--source-run', str(self.root),
                                     '--goal-file', str(self.root / 'goal.json'),
                                     '--run-dir', str(self.root / 'replay')])
        return args, ref

    def test_replay_uses_saved_evidence_and_never_constructs_lightroom(self):
        args, _ = self.archived_run()
        agents = FakeAgents(reviews=[review('revise')])
        hashes = {p: h.file_hash(p) for p in self.root.rglob('*') if p.is_file()}
        with patch.object(h, 'CodexAgents', return_value=agents), patch.object(h, 'Lightroom') as lr:
            self.assertEqual(h.review_saved_run(args), 0)
            lr.assert_not_called()
        self.assertEqual([r for r, _, _ in agents.inputs], ['reviewer'])
        self.assertEqual(len(agents.inputs[0][2]), 4)
        self.assertTrue(all(h.file_hash(p) == sha for p, sha in hashes.items()))
        self.assertEqual(h.load(args.run_dir / 'comparison.json')[0]['verdict'], 'revise')

    def test_replay_rejects_tampered_preview_before_model_call(self):
        args, _ = self.archived_run()
        (self.root / 'initial/preview.jpg').write_bytes(b'tampered')
        with patch.object(h, 'CodexAgents') as agents:
            with self.assertRaisesRegex(h.WorkflowError, 'Archived preview changed'):
                h.review_saved_run(args)
            agents.return_value.call.assert_not_called()

    def test_replay_reviews_are_blind_but_comparisons_track_repeated_gaps(self):
        self.agents = FakeAgents([proposal(), proposal(.3), proposal(.4)],
                                 [review('revise'), review('revise'), review()])
        self.app = h.Harness(self.config, self.j, self.lr, self.agents)
        args, _ = self.archived_run()
        agents = FakeAgents(reviews=[review('revise'), review('revise'), review()])
        with patch.object(h, 'CodexAgents', return_value=agents), patch.object(h, 'Lightroom') as lr:
            self.assertEqual(h.review_saved_run(args), 0)
            lr.assert_not_called()
        contexts = [ctx for _, ctx, _ in agents.inputs]
        self.assertTrue(all(ctx == contexts[0] for ctx in contexts))
        comparison = h.load(args.run_dir / 'round-01/review-comparison.json')
        self.assertEqual(comparison['replan_criteria'], ['subject'])

    def test_replay_rejects_tampered_reference_before_model_call(self):
        args, ref = self.archived_run()
        ref.write_bytes(b'tampered')
        with patch.object(h, 'CodexAgents') as agents:
            with self.assertRaisesRegex(h.WorkflowError, 'Saved input changed'):
                h.review_saved_run(args)
            agents.assert_not_called()


if __name__ == '__main__':
    unittest.main()
