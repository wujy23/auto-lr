"""Behavior tests: no real Codex calls or Lightroom writes."""
import copy
import contextlib
import io
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main as h


class ProcessErrorTests(unittest.TestCase):
    def test_codex_terminal_failure_overrides_stderr_and_item_warning(self):
        events = [
            {'type': 'item.completed', 'item': {'type': 'error', 'message': 'Code Mode unavailable'}},
            {'type': 'turn.failed', 'error': {'message': 'Selected model is at capacity. Please try a different model.'}},
            {'type': 'error', 'message': 'Cleanup warning'},
        ]
        stdout = '\n'.join(json.dumps(event) for event in events)
        script = f'import sys; print({stdout!r}); sys.stderr.write("Reading prompt from stdin...\\n"); sys.exit(1)'
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(h.WorkflowError) as caught:
                h.process([sys.executable, '-c', script], Path(tmp))
            error = str(caught.exception)
            self.assertIn('Selected model is at capacity', error)
            self.assertNotIn('Code Mode unavailable', error)
            self.assertNotIn('Cleanup warning', error)
            self.assertNotIn('Reading prompt', error)
            self.assertEqual((Path(tmp) / 'stdout.log').read_text().strip(), stdout)

    def test_top_level_error_fallback_ignores_malformed_lines_and_items(self):
        stdout = 'progress\nnull\n' + json.dumps({'type': 'error', 'message': 'Connection failed'})
        self.assertEqual(h.process_error(stdout, 'Reading prompt'), 'Connection failed')
        item = json.dumps({'type': 'item.completed', 'item': {'type': 'error', 'message': 'Warning'}})
        self.assertEqual(h.process_error(item, 'Actual process failure'), 'Actual process failure')

    def test_pretty_bridge_error_overrides_stderr_progress(self):
        stdout = json.dumps({'ok': False, 'error': 'Auto-selection failed'}, indent=2)
        self.assertEqual(h.process_error(stdout, 'Starting bridge'), 'Auto-selection failed')

    def test_successful_process_with_warning_is_not_rejected(self):
        script = 'print(\'{"type":"error","message":"Warning"}\')'
        with tempfile.TemporaryDirectory() as tmp:
            h.process([sys.executable, '-c', script], Path(tmp))

    def test_bridge_json_error_is_shown_when_stderr_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = 'import sys; print(\'{"ok": false, "error": "Auto-selection failed"}\'); sys.exit(1)'
            with self.assertRaisesRegex(h.WorkflowError, 'Auto-selection failed'):
                h.process([sys.executable, '-c', script], Path(tmp))

    def test_permission_failure_surfaces_cause_without_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            marker = root / 'attempts'
            script = ("import pathlib, sys; "
                      "p = pathlib.Path(sys.argv[1]); "
                      "p.write_text(p.read_text() + 'x' if p.exists() else 'x'); "
                      "sys.stderr.write(\"can't open file '/test/lr_link.py': Operation not permitted\\n\"); "
                      "sys.exit(2)")
            with self.assertRaises(h.WorkflowError) as caught:
                h.process([sys.executable, '-c', script, str(marker)], root / 'logs')
            self.assertIn('Operation not permitted', str(caught.exception))
            self.assertIn('文稿文件夹', str(caught.exception))
            self.assertEqual(marker.read_text(), 'x')


class PortablePathTests(unittest.TestCase):
    def test_system_proxy_is_used_without_overriding_explicit_env(self):
        with patch.dict(h.os.environ, {'https_proxy': 'http://explicit:9000'}, clear=True), patch.object(
                h.urllib.request, 'getproxies', return_value={'https': 'http://system:8000', 'http': 'http://system:8000'}):
            env = h.agent_environment()
        self.assertEqual(env['https_proxy'], 'http://explicit:9000')
        self.assertNotIn('HTTPS_PROXY', env)
        self.assertEqual(env['HTTP_PROXY'], 'http://system:8000')

    def test_codex_fallback_without_path_and_explicit_override_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            cli = Path(tmp) / 'app/codex'
            cli.parent.mkdir()
            cli.write_text('#!/bin/sh\nexit 0\n')
            cli.chmod(0o755)
            with patch.object(h.shutil, 'which', return_value=None), patch.object(h, 'codex_candidates', return_value=[cli]):
                self.assertEqual(h.resolve_codex(), str(cli.resolve()))
                with self.assertRaisesRegex(h.WorkflowError, '找不到'):
                    h.resolve_codex('missing-explicit-codex')
                cli.chmod(0o644)
                with self.assertRaisesRegex(h.WorkflowError, '找不到'):
                    h.resolve_codex()

    def test_relative_options_and_materials_ignore_working_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / 'moved project'
            (project / 'raw').mkdir(parents=True)
            (project / 'raw' / 'test.DNG').touch()
            (project / 'reference.jpg').touch()
            with patch.object(h, 'HERE', project):
                args = h.parser().parse_args(['run', '--raw-dir', 'raw', '--reference', 'reference.jpg',
                                             '--bridge', 'lr_link.py', '--run-dir', 'runs/new'])
                targets, references = h.resolve_inputs(args)
                self.assertEqual(targets, [(project / 'raw/test.DNG').resolve()])
                self.assertEqual(references, [(project / 'reference.jpg').resolve()])
                self.assertEqual(args.bridge, (project / 'lr_link.py').resolve())
                self.assertEqual(args.run_dir, (project / 'runs/new').resolve())

    def test_bundled_bridge_queue_follows_relocated_project(self):
        import shutil
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / 'moved project'
            queue = project / 'work/lr-link'
            queue.mkdir(parents=True)
            shutil.copy2(h.HERE / 'lr_link.py', project / 'lr_link.py')
            h.save(queue / 'status.json', {'state': 'ready', 'updatedAt': h.time.time(),
                                          'test_marker': 'relocated queue'})
            h.process([sys.executable, str(project / 'lr_link.py'), 'status'], Path(tmp) / 'logs', cwd='/')
            self.assertEqual(h.load(Path(tmp) / 'logs/stdout.log')['test_marker'], 'relocated queue')


def jpeg_header(width=6000, height=4000):
    # SOF-only fixture for the header reader; never used as a real rendered image.
    return b'\xff\xd8\xff\xc0\x00\x0b\x08' + height.to_bytes(2, 'big') + width.to_bytes(2, 'big') + b'\x01\x01\x11\x00\xff\xd9'


def fixed_goal():
    return {'style_summary': '自然明亮，保留天空层次',
            'criteria': [{'id': 'subject', 'dimension': 'exposure', 'region': '主体',
                          'target': '主体可辨且天空不过曝', 'reference_evidence': '测试目标',
                          'pass_when': '主体结构清楚且天空有层次', 'fail_when': '主体仍暗或天空失去层次'}],
            'hard_constraints': ['保持裁切']}


def planned(app):
    return app.plan_from_goal(app.prepare())


def proposal(exposure=0.2):
    return {
        'status': 'ready', 'goal_digest': h.digest(fixed_goal()), 'visual_analysis': '合成测试输入，不是真实图像分析',
        'edit_hypotheses': ['整体轻微提亮'], 'blockers': [],
        'parameter_review': [{'control': key, 'current': '见本轮基线',
                              'decision': 'adjust' if key == 'Exposure2012' else 'keep',
                              'reason': '合成测试'} for key in h.AUDIT],
        'mask_plan': '无需蒙版', 'settings': [{'name': 'Exposure2012', 'value': exposure}],
        'curves': [], 'masks': [],
    }


def review(verdict='pass'):
    return {'verdict': verdict, 'summary': '合成测试结论',
            'criteria': [{'id': 'subject', 'result': 'met' if verdict == 'pass' else 'unmet',
                          'change': 'improved', 'remaining_gap': '' if verdict == 'pass' else '主体仍暗',
                          'evidence': '仅测试状态控制，不代表真实画面'}],
            'protection_issues': [], 'corrections': ['主体仍偏暗'] if verdict == 'revise' else [],
            'blockers': ['无法判断'] if verdict == 'blocked' else []}


class FakeLR:
    def __init__(self, root, target):
        self.root = root
        self.info = {'photoId': '42', 'catalogPath': '/test/catalog.lrcat',
                     'filePath': str(target), 'originalAvailable': True}
        self.develop = {'Exposure2012': 0.0, 'orientation': 'AB', 'CropAngle': 0,
                        'ToneCurvePV2012': [0, 0, 255, 255], 'MaskGroupBasedCorrections': []}
        self.ranges = {'Exposure2012': [-5, 5]}
        self.capabilities = {'applyNumeric': True, 'applyStructured': True,
                             'preparePhoto': True, 'exportFullJpeg': True, 'jsonArrays': True}
        self.tokens = {}
        self.calls = []
        self.applied = []
        self.fail_after_write = False
        self.unverified = False

    def call(self, action, photo_id=None, plan=None, *, source=None, catalog=None, baseline_token=None):
        self.calls.append(action)
        if photo_id:
            h.check(photo_id == self.info['photoId'], 'Selected photo changed')
        if action == 'status':
            return {'capabilities': self.capabilities}
        if action in ('inspect', 'prepare'):
            if catalog:
                h.check(catalog == self.info['catalogPath'], 'Catalog changed')
            return copy.deepcopy(self.info)
        if action == 'settings':
            token = h.uuid.uuid4().hex
            self.tokens[token] = copy.deepcopy(self.develop)
            return {**{k: self.info[k] for k in ['photoId', 'catalogPath']},
                    'develop': copy.deepcopy(self.develop), 'numericRanges': self.ranges, 'baselineToken': token}
        if action == 'preview':
            p = self.root / 'fake-preview.jpg'
            p.write_bytes(b'fake image for protocol tests only')
            return {'previewPath': str(p), 'colorSpace': 'sRGB', 'photoId': self.info['photoId']}
        if action == 'apply':
            wire = h.load(plan)
            h.check(self.tokens[wire['baseline_token']] == self.develop, 'Stale baseline')
            self.applied.append(wire)
            self.develop.update(wire['settings'])
            for name, points in wire['curves'].items():
                self.develop[name] = [v for p in points for v in p]
            if self.fail_after_write:
                raise TimeoutError('Response lost after applying; do not resend')
            return {'photoId': self.info['photoId'], 'valuesMatch': not self.unverified,
                    'maskResults': {}, 'snapshot': 'test snapshot'}
        if action == 'export':
            h.check(self.tokens[baseline_token] == self.develop, 'Stale export baseline')
            p = self.root / 'exported.jpg'
            p.write_bytes(jpeg_header())
            return {'photoId': self.info['photoId'], 'catalogPath': self.info['catalogPath'],
                    'exportPath': str(p), 'baselineToken': baseline_token, 'format': 'JPEG',
                    'fullSize': True, 'quality': 100, 'colorSpace': 'sRGB'}
        raise AssertionError(action)


class FakeAgents:
    def __init__(self, proposals=None, reviews=None):
        self.proposals = proposals or [proposal()]
        self.reviews = reviews or [review()]
        self.inputs = []
        self.on_planner = None

    def call(self, role, context, images, folder):
        self.inputs.append((role, copy.deepcopy(context), images))
        if context.get('phase') == 'define_goal':
            return fixed_goal()
        if role == 'planner':
            if self.on_planner:
                self.on_planner()
            return copy.deepcopy(self.proposals.pop(0))
        return copy.deepcopy(self.reviews.pop(0))


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.target = self.root / 'target.raw'
        self.target.write_bytes(b'raw-test-fixture')
        self.config = {'target': str(self.target), 'photo_id': None, 'references': [],
                       'style': '自然', 'max_corrections': 2, 'export_dir': str(self.root / 'exports')}
        self.j = h.Journal(self.root)
        self.lr = FakeLR(self.root, self.target)

    def tearDown(self):
        self.tmp.cleanup()

    def harness(self, agents=None):
        return h.Harness(self.config, self.j, self.lr, agents or FakeAgents())

    def test_initial_preview_recaptures_lazy_mask_metadata(self):
        original = self.lr.call
        renders = []
        self.lr.develop['MaskGroupBasedCorrections'] = [
            {'CorrectionMasks': [{'CorrectionRangeMask': {'Type': 2}}]}]
        def lazy_call(action, *args, **kwargs):
            result = original(action, *args, **kwargs)
            if action == 'preview':
                renders.append(action)
                self.lr.develop['MaskGroupBasedCorrections'][0]['CorrectionMasks'][0][
                    'CorrectionRangeMask']['LuminanceDepthSampleInfo'] = '0 0.599982 0.174461'
                Path(result['previewPath']).write_bytes(f'render-{len(renders)}'.encode())
            return result
        self.lr.call = lazy_call
        agents = FakeAgents()
        pending = self.harness(agents).prepare()
        self.assertEqual(len(renders), 2)
        self.assertEqual(Path(pending['baseline']['preview']).read_bytes(), b'render-2')
        self.assertEqual(pending['baseline']['settings']['develop'], self.lr.develop)
        self.assertEqual(len(agents.inputs), 1)
        self.assertFalse(self.lr.applied)

    def test_initial_capture_stops_if_settings_never_settle(self):
        original = self.lr.call
        def changing_call(action, *args, **kwargs):
            result = original(action, *args, **kwargs)
            if action == 'preview':
                self.lr.develop['Exposure2012'] += .1
            return result
        self.lr.call = changing_call
        agents = FakeAgents()
        with self.assertRaisesRegex(h.WorkflowError, 'baseline is not coherent'):
            self.harness(agents).prepare()
        self.assertEqual(self.lr.calls.count('preview'), 3)
        self.assertFalse(agents.inputs)
        self.assertFalse((self.root / 'initial/baseline.json').exists())

    def test_result_capture_still_rejects_first_setting_change(self):
        original = self.lr.call
        def changing_call(action, *args, **kwargs):
            result = original(action, *args, **kwargs)
            if action == 'preview':
                self.lr.develop['Exposure2012'] += .1
            return result
        self.lr.call = changing_call
        with self.assertRaisesRegex(h.WorkflowError, 'baseline is not coherent'):
            h.capture(self.lr, self.config, self.root / 'result')
        self.assertEqual(self.lr.calls.count('preview'), 1)

    def test_prepare_is_read_only_and_returns_bound_plan(self):
        pending = planned(self.harness())
        self.assertNotIn('apply', self.lr.calls)
        self.assertEqual(pending['wire']['photo_id'], '42')
        self.assertEqual(pending['approval_digest'], h.digest({k: v for k, v in pending.items() if k != 'approval_digest'}))

    def test_happy_path_readback_then_review(self):
        agents = FakeAgents()
        app = self.harness(agents)
        app.execute(app.prepare())
        self.assertEqual(self.j.state['status'], 'complete')
        self.assertEqual(len(self.lr.applied), 1)
        self.assertNotIn('proposal', agents.inputs[-1][1])
        self.assertNotIn('baseline', agents.inputs[-1][1])
        self.assertEqual(self.lr.develop['Exposure2012'], 0.2)
        self.assertEqual(len(agents.inputs[-1][2]), 3)
        self.assertEqual(self.j.state['exported_jpeg']['width'], 6000)
        self.assertEqual(len(list((self.root / 'exports').glob('*.jpg'))), 1)

    def test_corrections_use_current_absolute_values_and_stop_at_two(self):
        agents = FakeAgents([proposal(.2), proposal(.3), proposal(.4)], [review('revise') for _ in range(3)])
        app = self.harness(agents)
        app.execute(app.prepare())
        self.assertEqual(self.j.state['status'], 'limit_reached')
        self.assertIsNone(self.j.state['error'])
        self.assertTrue(self.j.state['stop_reason'])
        self.assertEqual(len(self.lr.applied), 3)
        self.assertNotIn('export', self.lr.calls)
        self.assertEqual(self.lr.develop['Exposure2012'], .4)
        planner_contexts = [c for role, c, _ in agents.inputs if role == 'planner' and c.get('phase') == 'plan']
        self.assertEqual([c['baseline']['settings']['develop']['Exposure2012'] for c in planner_contexts], [0, .2, .3])
        self.assertEqual(len({v['baseline_token'] for v in self.lr.applied}), 3)

    def test_external_edit_blocks_execution(self):
        app = self.harness()
        pending = app.prepare()
        self.lr.develop['Exposure2012'] = 1
        with self.assertRaisesRegex(h.WorkflowError, 'Settings changed'):
            app.execute(pending)
        self.assertFalse(self.lr.applied)

    def test_catalog_switch_blocks_execution(self):
        app = self.harness()
        pending = app.prepare()
        self.lr.info['catalogPath'] = '/another/catalog.lrcat'
        with self.assertRaisesRegex(h.WorkflowError, 'catalog changed'):
            app.execute(pending)
        self.assertFalse(self.lr.applied)

    def test_wrong_target_is_rejected_before_model_call(self):
        agents = FakeAgents()
        self.lr.info['filePath'] = '/wrong.raw'
        with self.assertRaisesRegex(h.WorkflowError, 'does not match'):
            self.harness(agents).prepare()
        self.assertFalse(agents.inputs)

    def test_timeout_after_write_is_not_retried(self):
        self.lr.fail_after_write = True
        agents = FakeAgents()
        app = self.harness(agents)
        pending = app.prepare()
        with self.assertRaises(TimeoutError):
            app.execute(pending)
        self.assertEqual(self.j.state['status'], 'applying')
        self.assertTrue(self.j.state['may_have_applied'])
        self.assertEqual(len(self.lr.applied), 1)
        self.assertEqual([v[0] for v in agents.inputs], ['planner', 'planner'])

    def test_failed_write_verification_never_goes_to_visual_qa(self):
        self.lr.unverified = True
        agents = FakeAgents()
        app = self.harness(agents)
        with self.assertRaisesRegex(h.WorkflowError, 'did not verify'):
            app.execute(app.prepare())
        self.assertEqual([v[0] for v in agents.inputs], ['planner', 'planner'])

    def test_blocked_analysis_never_applies(self):
        p = proposal()
        p.update(status='blocked', blockers=['需要接口不支持的精确天空选区'])
        with self.assertRaisesRegex(h.WorkflowError, 'Analysis blocked'):
            planned(self.harness(FakeAgents([p])))
        self.assertFalse(self.lr.applied)

    def test_correction_cannot_change_goal(self):
        p = proposal(.3)
        p['goal_digest'] = '0' * 64
        app = self.harness(FakeAgents([proposal(), p], [review('revise')]))
        with self.assertRaisesRegex(h.WorkflowError, 'different fixed goal'):
            app.execute(app.prepare())
        self.assertEqual(len(self.lr.applied), 1)

    def test_no_op_still_reviews(self):
        p = proposal()
        p['settings'] = []
        app = self.harness(FakeAgents([p]))
        app.execute(app.prepare())
        self.assertEqual(self.j.state['status'], 'complete')
        self.assertFalse(self.lr.applied)

    def test_pass_requires_all_targets_met(self):
        r = review()
        r['criteria'][0]['result'] = 'unknown'
        with self.assertRaises(h.WorkflowError):
            h.validate_review(r, fixed_goal()['criteria'])

    def test_settings_change_while_reviewer_runs_blocks_completion(self):
        parent = FakeAgents()
        original = parent.call
        def call(*args):
            result = original(*args)
            if args[0] == 'reviewer':
                self.lr.develop['Exposure2012'] = 2
            return result
        parent.call = call
        app = self.harness(parent)
        with self.assertRaisesRegex(h.WorkflowError, 'Settings changed'):
            app.execute(app.prepare())
        self.assertNotEqual(self.j.state['status'], 'complete')

    def test_unsupported_global_or_duplicate_rejected(self):
        baseline = h.capture(self.lr, self.config, self.root / 'capture')
        for settings in [[{'name': 'Temperature', 'value': 6000}],
                         [{'name': 'Exposure2012', 'value': 6}],
                         [{'name': 'Exposure2012', 'value': .2}] * 2,
                         [{'name': 'Exposure2012', 'value': True}]]:
            with self.subTest(settings=settings):
                p = proposal()
                p['settings'] = settings
                with self.assertRaises(h.WorkflowError):
                    h.compile_plan(p, baseline, self.lr.capabilities, {})

    def test_mask_identity_reused_and_invalid_geometry_rejected(self):
        baseline = h.capture(self.lr, self.config, self.root / 'capture')
        p = proposal()
        p['masks'] = [{'id': '', 'name': 'foreground', 'type': 'radial',
                       'geometry': [.1, .2, .8, .9, 80, 1],
                       'settings': [{'name': 'ExposureEV', 'value': .2}]}]
        registry = {}
        first = h.compile_plan(p, baseline, self.lr.capabilities, registry)
        second = h.compile_plan(p, baseline, self.lr.capabilities, registry)
        self.assertEqual(first['masks'][0]['id'], second['masks'][0]['id'])
        p['masks'][0]['geometry'][4] = 101
        with self.assertRaisesRegex(h.WorkflowError, 'Invalid ellipse'):
            h.compile_plan(p, baseline, self.lr.capabilities, registry)

    def test_late_token_refresh_does_not_reuse_planning_token(self):
        app = self.harness()
        pending = app.prepare()
        pending = app.plan_from_goal(pending)
        planned_token = pending['wire']['baseline_token']
        app.execute(pending)
        self.assertNotEqual(self.lr.applied[0]['baseline_token'], planned_token)

    def test_invalid_curve_and_missing_audit_rejected(self):
        baseline = h.capture(self.lr, self.config, self.root / 'capture')
        p = proposal()
        p['curves'] = [{'name': 'ToneCurvePV2012', 'points': [[0, 0], [255, 255], [255, 230]]}]
        with self.assertRaises(h.WorkflowError):
            h.compile_plan(p, baseline, self.lr.capabilities, {})
        p['curves'] = []
        p['parameter_review'].pop()
        with self.assertRaisesRegex(h.WorkflowError, 'Incomplete'):
            h.compile_plan(p, baseline, self.lr.capabilities, {})

    def test_cli_continue_refuses_uncertain_state_without_rewriting_it(self):
        h.save(self.root / 'config.json', {'bridge': str(self.root / 'lr_link.py')})
        self.j.set('write_uncertain')
        rc = h.main(['continue', '--run-dir', str(self.root), '--approve', 'x'])
        self.assertEqual(rc, 2)
        self.assertEqual(h.load(self.root / 'state.json')['status'], 'write_uncertain')

    def test_real_bridge_encoder_accepts_compiled_wire_offline(self):
        client = h.HERE / 'lr_link.py'
        if not client.exists():
            self.skipTest('Bundled bridge not present')
        spec = importlib.util.spec_from_file_location('legacy_lr_link', client)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        pending = planned(self.harness())
        payload = module.encode_plan(pending['wire'], '42')
        self.assertIn('Exposure2012=0.2', payload)

    def test_readback_catches_unrequested_global_changes(self):
        pending = planned(self.harness())
        before = {'Exposure2012': 0., 'Saturation': 0.}
        after = {'Exposure2012': .2, 'Saturation': -20.}
        with self.assertRaisesRegex(h.WorkflowError, 'Unrequested setting'):
            h.verify_readback(pending['wire'], before, after, {'valuesMatch': True}, {'Saturation': [-100, 100]})

    def test_readback_catches_mask_geometry_and_checks_local_units(self):
        mask = {'id': 'a' * 32, 'name': 'foreground', 'type': 'radial',
                'geometry': [.1, .2, .8, .9, 80, 1], 'settings': {'ExposureEV': .4}}
        wire = {'settings': {}, 'curves': {}, 'masks': [mask]}
        shape = dict(Left=.1, Top=.2, Right=.8, Bottom=.9, Feather=80, Flipped=True)
        group = {'CorrectionID': 'a' * 32, 'CorrectionName': 'Codex foreground',
                 'CorrectionMasks': [shape], 'LocalExposure2012': .1}
        after = {'MaskGroupBasedCorrections': [group]}
        response = {'valuesMatch': True, 'maskResults': {'a' * 32: {'verified': True}}}
        h.verify_readback(wire, {}, after, response)
        shape['Left'] = .2
        with self.assertRaisesRegex(h.WorkflowError, 'geometry changed'):
            h.verify_readback(wire, {}, after, response)

    def test_export_failure_does_not_mark_task_complete(self):
        original = self.lr.call
        def call(action, *args, **kwargs):
            if action == 'export':
                raise TimeoutError('Full-size export timed out')
            return original(action, *args, **kwargs)
        self.lr.call = call
        app = self.harness()
        with self.assertRaises(TimeoutError):
            app.execute(app.prepare())
        self.assertEqual(self.j.state['status'], 'exporting')
        self.assertEqual(len(self.lr.applied), 1)
        self.assertFalse((self.root / 'exports').exists())

    def test_old_plugin_stops_before_photo_selection(self):
        self.lr.capabilities.pop('preparePhoto')
        with self.assertRaisesRegex(h.WorkflowError, 'Reload'):
            self.harness().prepare()
        self.assertEqual(self.lr.calls, ['status'])

    def test_export_keeps_existing_files(self):
        directory = self.root / 'exports'
        directory.mkdir()
        old = directory / 'target.jpg'
        old.write_bytes(b'previous finished photo')
        app = self.harness()
        app.execute(app.prepare())
        self.assertEqual(old.read_bytes(), b'previous finished photo')
        self.assertEqual(len(list(directory.glob('*.jpg'))), 2)


class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.raw = self.root / 'raw'
        self.refs = self.root / 'references'
        self.raw.mkdir()
        self.refs.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def args(self, *extra):
        return h.parser().parse_args(['run', '--raw-dir', str(self.raw), '--reference-dir', str(self.refs), *extra])

    def test_one_raw_defaults_and_filters_sidecars(self):
        (self.raw / 'A.NEF').write_bytes(b'raw')
        (self.raw / 'A.xmp').write_bytes(b'sidecar')
        (self.raw / '.hidden.NEF').write_bytes(b'ignore')
        (self.refs / 'look.JPG').write_bytes(b'ref')
        targets, refs = h.resolve_inputs(self.args())
        self.assertEqual([p.name for p in targets], ['A.NEF'])
        self.assertEqual([p.name for p in refs], ['look.JPG'])

    def test_multiple_raw_requires_choice_in_noninteractive_mode(self):
        for name in ['A.NEF', 'B.DNG']:
            (self.raw / name).write_bytes(b'raw')
        with patch('sys.stdin.isatty', return_value=False), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(h.WorkflowError, 'Multiple target images'):
                h.resolve_inputs(self.args())
        targets, _ = h.resolve_inputs(self.args('--all'))
        self.assertEqual([p.name for p in targets], ['A.NEF', 'B.DNG'])

    def test_named_target_and_reference_override(self):
        (self.raw / 'A.NEF').write_bytes(b'raw')
        (self.refs / 'look.JPG').write_bytes(b'ref')
        (self.refs / 'other.png').write_bytes(b'ref')
        targets, refs = h.resolve_inputs(self.args('--target', 'A.NEF', '--reference', 'look.JPG'))
        self.assertEqual(targets, [(self.raw / 'A.NEF').resolve()])
        self.assertEqual([p.name for p in refs], ['look.JPG'])
        _, refs = h.resolve_inputs(self.args('--no-references'))
        self.assertEqual(refs, [])

    def test_empty_library_gives_actionable_error(self):
        with self.assertRaisesRegex(h.WorkflowError, 'No supported target images'):
            h.resolve_inputs(self.args())

    def test_jpeg_png_targets_resolve_by_name_and_absolute_path(self):
        for name in ['photo.jpg', 'photo.JPEG', 'photo.PnG']:
            path = self.raw / name
            path.touch()
            for value in [name, str(path)]:
                with self.subTest(target=value):
                    targets, _ = h.resolve_inputs(self.args('--target', value))
                    self.assertEqual(targets, [path.resolve()])

    def test_mixed_library_scan_and_library_command_agree(self):
        for name in ['A.DNG', 'B.JPG', 'C.png', 'D.jpeg', 'E.xmp', 'F.gif', '.hidden.jpg']:
            (self.raw / name).touch()
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(h.main(['library', '--input-dir', str(self.raw),
                                     '--reference-dir', str(self.refs)]), 0)
        listing = json.loads(output.getvalue())
        targets, _ = h.resolve_inputs(self.args('--all'))
        self.assertEqual([p.name for p in targets], ['A.DNG', 'B.JPG', 'C.png', 'D.jpeg'])
        self.assertEqual(listing['targets'], [str(p) for p in targets])
        self.assertEqual(listing['raw'], [str((self.raw / 'A.DNG').resolve())])

    def test_png_auto_selected_and_input_directory_alias(self):
        path = self.raw / 'only.png'; path.touch()
        args = h.parser().parse_args(['run', '--input-dir', str(self.raw), '--no-references'])
        targets, _ = h.resolve_inputs(args)
        self.assertEqual(targets, [path.resolve()])

    def test_unsupported_target_still_rejected(self):
        (self.raw / 'image.gif').touch()
        with self.assertRaisesRegex(h.WorkflowError, 'Target must be RAW, JPG, JPEG, or PNG'):
            h.resolve_inputs(self.args('--target', 'image.gif'))

    def test_batch_stops_before_next_photo_after_failure(self):
        targets = [self.raw / f'{i}.NEF' for i in range(3)]
        for p in targets:
            p.write_bytes(b'raw')
        args = self.args('--all', '--run-dir', str(self.root / 'batch'))
        calls = []
        def run(argv):
            calls.append(argv)
            folder = Path(argv[argv.index('--run-dir') + 1])
            h.save(folder / 'state.json', {'status': 'write_uncertain'})
            return 2
        with patch.object(h, 'main', side_effect=run), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(h.batch_run(args, targets, []), 2)
        self.assertEqual(len(calls), 1)
        self.assertEqual(h.load(self.root / 'batch' / 'batch.json')['status'], 'stopped')

    def test_library_batch_continues_after_normal_correction_limit(self):
        targets = [self.raw / f'{i}.NEF' for i in range(2)]
        args = self.args('--all', '--run-dir', str(self.root / 'batch'))
        args.command = 'run'
        def run(argv):
            folder = Path(argv[argv.index('--run-dir') + 1])
            h.save(folder / 'state.json', {'status': 'limit_reached', 'error': None})
            return 0
        with patch.object(h, 'main', side_effect=run) as calls, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(h.batch_run(args, targets, []), 0)
        self.assertEqual(calls.call_count, 2)
        self.assertEqual([x['status'] for x in h.load(self.root / 'batch/batch.json')['items']],
                         ['limit_reached', 'limit_reached'])


class SubprocessIntegrationTests(unittest.TestCase):
    """Exercise the actual subprocess adapters and CLI confirmation/resume path."""
    def test_plan_then_continue_through_both_process_adapters(self):
        with tempfile.TemporaryDirectory(prefix='lr integration ') as tmp:
            base = Path(tmp)
            target = base / 'target with spaces.raw'
            target.write_bytes(b'fixture')
            preview = base / 'preview.jpg'
            preview.write_bytes(b'synthetic protocol fixture')
            (base / 'exported.jpg').write_bytes(jpeg_header())
            for role in ['planner', 'reviewer']:
                skill = base / (role + '-skill')
                skill.mkdir()
                (skill / 'SKILL.md').write_text(f'---\nname: test-{role}\ndescription: Integration test skill\n---\n{role.upper()}_ONLY\n', encoding='utf-8')
            h.save(base / 'proposal.json', proposal())
            h.save(base / 'goal.json', fixed_goal())
            h.save(base / 'review.json', review())
            h.save(base / 'bridge-state.json', {
                'target': str(target), 'preview': str(preview), 'develop': {'Exposure2012': 0.0},
                'tokens': {}, 'writes': 0, 'exports': 0,
            })
            bridge = base / 'fake_bridge.py'
            bridge.write_text('''import json, sys, uuid
from pathlib import Path
p=Path(__file__).parent/'bridge-state.json'
s=json.loads(p.read_text())
a=sys.argv[1]
identity={'photoId':'42','catalogPath':'/test/catalog'}
if a=='status':
    print(json.dumps({'state':'ready','heartbeatAgeSeconds':0,'capabilities':{'applyNumeric':True,'applyStructured':True,'preparePhoto':True,'exportFullJpeg':True,'jsonArrays':True}}))
    raise SystemExit(0)
if a in ('inspect','prepare'):
    r={**identity,'filePath':s['target'],'originalAvailable':True}
elif a=='settings':
    token=uuid.uuid4().hex
    s['tokens'][token]=dict(s['develop'])
    r={**identity,'baselineToken':token,'develop':s['develop'],'numericRanges':{'Exposure2012':[-5,5]}}
elif a=='preview':
    r={**identity,'previewPath':s['preview'],'colorSpace':'sRGB'}
elif a=='apply':
    plan=json.loads(Path(sys.argv[sys.argv.index('--plan')+1]).read_text())
    assert s['tokens'][plan['baseline_token']]==s['develop']
    s['develop'].update(plan['settings'])
    s['writes']+=1
    r={**identity,'valuesMatch':True,'maskResults':{},'snapshot':'fixture snapshot'}
elif a=='export':
    token=sys.argv[sys.argv.index('--baseline-token')+1]
    assert s['tokens'][token]==s['develop']
    s['exports']+=1
    r={**identity,'fullSize':True,'format':'JPEG','quality':100,'colorSpace':'sRGB',
       'baselineToken':token,'exportPath':str(Path(__file__).parent/'exported.jpg')}
else:
    raise ValueError(a)
p.write_text(json.dumps(s))
print(json.dumps({'ok':True,'result':r,'requestId':uuid.uuid4().hex}))
''', encoding='utf-8')
            codex = base / 'fake-codex'
            codex.write_text('#!' + sys.executable + '\n' + '''import json, sys
from pathlib import Path
a=sys.argv
assert a[1]=='exec' and '--ignore-user-config' in a and a[a.index('--sandbox')+1]=='read-only'
assert 'features.shell_tool=false' in a and 'features.apps=false' in a
assert '--image' in a
prompt=sys.stdin.read()
assert '附图顺序' in prompt and '本轮输入数据' in prompt
schema=json.loads(Path(a[a.index('--output-schema')+1]).read_text())
role='goal' if 'hard_constraints' in schema['properties'] else ('proposal' if 'status' in schema['properties'] else 'review')
expected='planner' if role in ('proposal','goal') else 'reviewer'
other='reviewer' if expected=='planner' else 'planner'
assert ('$test-'+expected) in prompt
assert expected.upper()+'_ONLY' in prompt and other.upper()+'_ONLY' not in prompt
data=(Path(__file__).parent/(role+'.json')).read_text()
Path(a[a.index('--output-last-message')+1]).write_text(data)
print(json.dumps({'type':'turn.completed','usage':{'input_tokens':10,'output_tokens':10}}))
''', encoding='utf-8')
            codex.chmod(0o755)
            run = base / 'run'
            args = ['plan', '--target', str(target), '--style', '自然', '--bridge', str(bridge),
                    '--planner-skill', str(base / 'planner-skill'), '--reviewer-skill', str(base / 'reviewer-skill'),
                    '--codex', str(codex), '--run-dir', str(run),
                    '--no-references', '--export-dir', str(base / 'exports')]
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(h.main(args), 0)
            self.assertEqual(h.load(run / 'state.json')['status'], 'goal_ready')
            self.assertEqual(h.load(base / 'bridge-state.json')['writes'], 0)
            self.assertEqual(h.load(base / 'bridge-state.json')['exports'], 0)
            approval = h.load(run / 'pending.json')['approval_digest']
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(h.main(['continue', '--run-dir', str(run), '--approve', approval]), 0)
            self.assertEqual(h.load(base / 'bridge-state.json')['writes'], 1)

            self.assertEqual(h.load(base / 'bridge-state.json')['exports'], 1)
            self.assertEqual(h.load(run / 'export.json')['width'], 6000)
            self.assertEqual(h.load(run / 'state.json')['status'], 'complete')
            self.assertIn('agent.reviewer', h.load(run / 'summary.json')['seconds_by_stage'])
            self.assertIn('lightroom.apply', h.load(run / 'summary.json')['seconds_by_stage'])
            self.assertEqual(h.load(run / 'round-00' / 'planner' / 'skill-usage.json')['name'], 'test-planner')
            self.assertEqual(h.load(run / 'round-00' / 'reviewer' / 'skill-usage.json')['name'], 'test-reviewer')
            # Finished tasks cannot replay the same write via continue.
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(h.main(['continue', '--run-dir', str(run), '--approve', approval]), 2)
            self.assertEqual(h.load(base / 'bridge-state.json')['writes'], 1)
            # A fresh run reaches its correction budget without an error exit or export.
            limited = base / 'limited-run'
            limited_args = list(args)
            limited_args[0] = 'run'
            limited_args[limited_args.index('--run-dir') + 1] = str(limited)
            limited_args += ['--max-corrections', '2']
            h.save(base / 'review.json', review('revise'))
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(h.main(limited_args), 0)
            self.assertEqual(h.load(limited / 'state.json')['status'], 'limit_reached')
            self.assertIsNone(h.load(limited / 'summary.json')['error'])
            self.assertTrue(h.load(limited / 'summary.json')['stop_reason'])
            self.assertEqual(h.load(base / 'bridge-state.json')['writes'], 4)
            self.assertEqual(h.load(base / 'bridge-state.json')['exports'], 1)
            self.assertFalse((limited / 'export.json').exists())
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(h.main(['continue', '--run-dir', str(limited)]), 2)
            self.assertEqual(h.load(base / 'bridge-state.json')['writes'], 4)



if __name__ == '__main__':
    unittest.main()
