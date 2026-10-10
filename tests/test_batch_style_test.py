"""Batch orchestration against a fake public command; never connects to LR or a model."""
import contextlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import batch_style_test as batch


class BatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='batch 空格 ')
        self.root = Path(self.tmp.name).resolve()
        self.dataset = self.root / 'data_set'
        self.out = self.root / 'experiment'
        self.root_patch = patch.object(batch, 'ROOT', self.root)
        self.root_patch.start()
        for _, name in batch.MATERIALS:
            p = self.dataset / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(('raw fixture ' + name).encode())
        for name, folder in batch.STYLES:
            p = self.dataset / folder / ('prompts' if name == '下午城市' else '') / '参考.jpg'
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(name.encode())
        for name in ['main.py', 'harness.py', 'lr_link.py', 'batch_style_test.py']:
            (self.root / name).write_text('# fixture')
        self.calls = []

    def tearDown(self):
        self.root_patch.stop()
        self.tmp.cleanup()

    def invoke(self, argv, log):
        self.calls.append(argv)
        if argv == ['doctor']:
            return 0
        run = Path(argv[argv.index('--run-dir') + 1])
        if argv[0] == 'plan':
            run.mkdir(parents=True)
            (run / 'initial').mkdir()
            target = Path(argv[argv.index('--target') + 1])
            self.assertTrue(target.is_relative_to(self.out))
            self.assertNotIn('--approve', argv)
            batch.save(run / 'state.json', {'status': 'goal_ready'})
            batch.save(run / 'initial/baseline.json', {'settings': {'develop': {'Exposure2012': 0}}})
            (run / 'initial/preview.jpg').write_bytes(b'preview')
        else:
            self.assertEqual(argv[0], 'continue')
            self.assertNotIn('--approve', argv)
            p = run / 'final.jpg'
            p.write_bytes(b'export fixture')
            batch.save(run / 'export.json', {'path': str(p), 'sha256': batch.sha(p)})
            batch.save(run / 'state.json', {'status': 'complete'})
        return 0

    def main(self, *args, invoke=None):
        with patch.object(batch, 'invoke', side_effect=invoke or self.invoke), contextlib.redirect_stdout(io.StringIO()):
            return batch.main(['--dataset', str(self.dataset), '--batch-dir', str(self.out), *args])

    def test_dry_run_has_only_selected_combinations_and_no_side_effects(self):
        items = batch.experiments(self.dataset)
        self.assertEqual([i["id"] for i in items], ["E03", "E06", "E10", "E16", "E20", "E21", "E25"])
        self.assertEqual(len({(i['source'], i['style']) for i in items}), 7)
        self.assertEqual(Path(items[0]['source']).name, 'DSC_0217.NEF')
        self.assertEqual(self.main('--dry-run'), 0)
        self.assertFalse(self.out.exists())
        self.assertEqual(self.calls, [])

    def test_independent_copies_and_completed_runs_are_not_replayed(self):
        source = self.dataset / batch.MATERIALS[0][1]
        before = batch.sha(source)
        self.assertEqual(self.main('--run', '--only', 'E03,E06'), 0)
        a = self.out / 'E03/input' / source.name
        b = self.out / 'E06/input' / Path(batch.MATERIALS[1][1]).name
        self.assertNotEqual(a.stat().st_ino, b.stat().st_ino)
        self.assertNotEqual(a.stat().st_ino, source.stat().st_ino)
        self.assertEqual(batch.sha(a), before)
        self.assertEqual(batch.sha(source), before)
        self.assertTrue((self.out / 'comparison.html').exists())
        self.assertTrue((self.out / 'results.csv').exists())
        self.calls.clear()
        self.assertEqual(self.main('--run', '--only', 'E03,E06'), 0)
        self.assertEqual(self.calls, [['doctor']])

    def test_default_run_dispatches_exactly_seven_selected_experiments(self):
        self.assertEqual(self.main('--run'), 0)
        plans = [c for c in self.calls if c[0] == 'plan']
        ids = [Path(c[c.index('--run-dir') + 1]).parent.name for c in plans]
        self.assertEqual(ids, ['E03', 'E06', 'E10', 'E16', 'E20', 'E21', 'E25'])
        self.assertEqual(sum(c[0] == 'continue' for c in self.calls), 7)
        self.assertEqual(len(batch.read(self.out / 'batch.json')['items']), 7)
        self.assertIn('7 组精选风格实验', (self.out / 'comparison.html').read_text())
        with self.assertRaisesRegex(ValueError, '本脚本只运行'):
            self.main('--run', '--only', 'E01')

    def test_different_baseline_blocks_comparison(self):
        items = [{'id': 'E03', 'source': 'same.raw'}, {'id': 'E06', 'source': 'same.raw'}]
        for exposure, item in enumerate(items):
            folder = self.out / item['id'] / 'run/initial'
            folder.mkdir(parents=True)
            batch.save(folder / 'baseline.json', {'settings': {'develop': {'Exposure2012': exposure}}})
        with self.assertRaisesRegex(ValueError, '初始显影设置不同'):
            batch.baseline_check(self.out, items, items[1])

    def test_failure_stops_and_uncertain_write_cannot_be_skipped(self):
        def invoke(argv, log):
            if argv[0] == 'continue':
                self.calls.append(argv)
                run = Path(argv[argv.index('--run-dir') + 1])
                batch.save(run / 'state.json', {'status': 'write_uncertain'})
                return 2
            return self.invoke(argv, log)
        with self.assertRaisesRegex(ValueError, '未完成'):
            self.main('--run', '--only', 'E03,E06', invoke=invoke)
        self.assertFalse((self.out / 'E06').exists())
        self.calls.clear()
        with self.assertRaisesRegex(ValueError, '禁止自动重放'):
            self.main('--run', '--only', 'E03,E06', '--skip-failed')
        self.assertEqual(self.calls, [['doctor']])

    def test_limit_reached_finishes_normally_continues_batch_and_is_not_replayed(self):
        def invoke(argv, log):
            if argv[0] == 'continue' and Path(argv[argv.index('--run-dir') + 1]).parent.name == 'E03':
                self.calls.append(argv)
                run = Path(argv[argv.index('--run-dir') + 1])
                batch.save(run / 'state.json', {'status': 'limit_reached', 'error': None,
                                                'stop_reason': 'Reached correction limit'})
                return 0
            return self.invoke(argv, log)
        self.assertEqual(self.main('--run', '--only', 'E03,E06', invoke=invoke), 0)
        self.assertFalse((self.out / 'E03/run/export.json').exists())
        self.assertEqual(batch.status(self.out, {'id': 'E06'}), 'complete')
        self.assertIn('Reached correction limit', (self.out / 'comparison.html').read_text())
        self.calls.clear()
        self.assertEqual(self.main('--run', '--only', 'E03,E06', invoke=invoke), 0)
        self.assertEqual(self.calls, [['doctor']])

    def test_nonzero_exit_with_limit_status_still_stops_batch(self):
        def invoke(argv, log):
            if argv[0] == 'continue':
                run = Path(argv[argv.index('--run-dir') + 1])
                batch.save(run / 'state.json', {'status': 'limit_reached'})
                return 2
            return self.invoke(argv, log)
        with self.assertRaisesRegex(ValueError, '未完成'):
            self.main('--run', '--only', 'E03,E06', invoke=invoke)
        self.assertFalse((self.out / 'E06').exists())

    def test_changed_reference_and_missing_export_block_resume(self):
        self.main('--run', '--only', 'E03')
        manifest = batch.read(self.out / 'batch.json')
        (self.out / 'references' / manifest['items'][1]['reference_name']).write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, '参考图已变化'):
            self.main('--run', '--only', 'E06')
        (self.out / 'E03/run/final.jpg').unlink()
        with self.assertRaisesRegex(ValueError, '成片缺失'):
            self.main('--run', '--only', 'E03')

    def test_changed_original_is_not_silently_used(self):
        self.main('--run', '--only', 'E03')
        source = self.dataset / batch.MATERIALS[1][1]
        source.write_bytes(b'changed original')
        with self.assertRaisesRegex(ValueError, '原素材已变化'):
            self.main('--run', '--only', 'E06')


if __name__ == '__main__':
    unittest.main()
