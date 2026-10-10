"""Public entry and installer behavior; no real photos or model requests."""
import contextlib
import io
import json
from pathlib import Path
import shutil
import tempfile
import time
import unittest
from unittest.mock import patch

import harness as entry
from scripts import setup, doctor
from test_main import h, FakeLR, FakeAgents
from test_capabilities import run_lua
from test_lua_bridge import LIB


class EntryTests(unittest.TestCase):
    def test_run_is_noninteractive_and_records_automatic_goal_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'main.py').write_bytes((entry.ROOT / 'main.py').read_bytes())
            (root / 'lr_link.py').touch()
            target = root / 'photo.raw'
            target.touch()
            run = root / 'run'
            lr, agents = FakeLR(root, target), FakeAgents()
            with patch.object(entry, 'ROOT', root), patch.object(h, 'HERE', root), \
                 patch.object(h, 'resolve_codex', return_value='/fixture/codex'), \
                 patch.object(h, 'Lightroom', return_value=lr), patch.object(h, 'CodexAgents', return_value=agents), \
                 patch('builtins.input', side_effect=AssertionError('Must not ask for confirmation')), \
                 contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(entry.main(['run', '--target', str(target), '--no-references',
                                             '--style', '自然', '--run-dir', str(run)]), 0)
            self.assertEqual(h.load(run / 'state.json')['status'], 'complete')
            self.assertEqual([c['phase'] for _, c, _ in agents.inputs], ['define_goal', 'plan', 'review'])
            self.assertEqual(h.load(run / 'goal-lock.json')['mode'], 'automatic')
            self.assertFalse((run / 'goal-approval.json').exists())
            self.assertTrue(Path(h.load(run / 'export.json')['path']).is_file())

    def test_public_entry_runs_python_pipeline_and_rejects_replay(self):
        with tempfile.TemporaryDirectory(prefix='entry 测试 ') as tmp:
            root = Path(tmp)
            (root / 'main.py').write_bytes((entry.ROOT / 'main.py').read_bytes())
            (root / 'lr_link.py').touch()
            target = root / 'photo.raw'
            target.touch()
            run = root / 'runs/first'
            lr, agents = FakeLR(root, target), FakeAgents()
            with patch.object(entry, 'ROOT', root), patch.object(h, 'HERE', root), \
                 patch.object(h, 'resolve_codex', return_value='/fixture/codex'), \
                 patch.object(h, 'Lightroom', return_value=lr), patch.object(h, 'CodexAgents', return_value=agents), \
                 contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(entry.main(['plan', '--target', str(target), '--no-references',
                                             '--style', '自然', '--run-dir', str(run)]), 0)
                self.assertEqual(h.load(run / 'state.json')['status'], 'goal_ready')
                self.assertEqual(len(agents.inputs), 1)
                self.assertFalse(lr.applied)
                self.assertEqual(entry.main(['continue', '--run-dir', str(run), '--approve', 'wrong']), 2)
                self.assertFalse(lr.applied)
                digest = h.load(run / 'pending.json')['approval_digest']
                self.assertEqual(entry.main(['continue', '--run-dir', str(run)]), 0)
                self.assertEqual(h.load(run / 'state.json')['status'], 'complete')
                self.assertTrue(Path(h.load(run / 'export.json')['path']).is_file())
                self.assertEqual([a[0] for a in agents.inputs], ['planner', 'planner', 'reviewer'])
                self.assertEqual(entry.main(['continue', '--run-dir', str(run), '--approve', digest]), 2)
                self.assertEqual(len(lr.applied), 1)
                self.assertEqual(entry.main(['status', '--run-dir', str(run)]), 0)
                self.assertEqual(len(lr.applied), 1)
            events = [json.loads(line) for line in (root / 'work/harness-invocations.jsonl').read_text().splitlines()]
            self.assertEqual(len(events), 10)
            self.assertTrue(all(e['engine_sha256'] == h.file_hash(root / 'main.py') for e in events))

    def test_public_entry_does_not_expose_bypass_flags(self):
        for flag in ['--goal-file', '--bridge', '--accept-style', '--planner-skill']:
            with self.subTest(flag=flag), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    entry.parser().parse_args(['plan', '--target', 'x.raw', '--no-references', '--style', 'test', flag, 'x'])

    def test_smoke_reports_model_failure_without_traceback(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(doctor.workflow, 'resolve_codex', return_value='codex'), \
             patch.object(doctor, 'command', return_value=(True, '--ignore-user-config --output-schema --output-last-message --ephemeral')), \
             patch.object(doctor.workflow, 'process', side_effect=h.WorkflowError('sandbox denied')):
            report = doctor.check(Path(tmp), smoke_model=True)
            smoke = next(c for c in report['checks'] if c['check'] == 'model_smoke')
            self.assertFalse(smoke['ok'])
            self.assertIn('sandbox denied', smoke['detail'])


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='install 空格 ')
        self.root = (Path(self.tmp.name) / 'repo').resolve()
        self.root.mkdir()
        shutil.copytree(entry.ROOT / setup.PLUGIN, self.root / setup.PLUGIN)
        self.destination = Path(self.tmp.name) / 'Lightroom/Modules' / setup.PLUGIN
        self.platform = patch.object(setup.sys, 'platform', 'darwin')
        self.platform.start()

    def tearDown(self):
        self.platform.stop()
        self.tmp.cleanup()

    def install(self, **kwargs):
        return setup.install(self.root, destination=self.destination, **kwargs)

    def test_install_idempotent_update_backup_and_path_binding(self):
        self.assertEqual(self.install(dry_run=True)['action'], 'would_install')
        self.assertFalse(self.destination.exists())
        self.assertEqual(self.install()['action'], 'installed')
        config = (self.destination / 'InstalledConfig.lua').read_text()
        self.assertIn(setup.lua_string(self.root / 'work/lr-link'), config)
        self.assertEqual(self.install()['action'], 'unchanged')
        previous = (self.destination / 'Bridge.lua').read_bytes()
        (self.root / setup.PLUGIN / 'Bridge.lua').write_bytes(previous + b'\n-- new version\n')
        result = self.install()
        self.assertEqual(result['action'], 'updated')
        self.assertEqual((Path(result['backup']) / 'Bridge.lua').read_bytes(), previous)
        self.assertFalse(Path(result['backup']).is_relative_to(self.destination.parent))

    def test_active_connection_blocks_mutation(self):
        queue = self.root / 'work/lr-link'
        queue.mkdir(parents=True)
        h.save(queue / 'status.json', {'state': 'ready', 'updatedAt': time.time()})
        with self.assertRaisesRegex(RuntimeError, 'active'):
            self.install()
        self.assertFalse(self.destination.exists())

    def test_unmanaged_and_other_repository_are_preserved(self):
        self.destination.mkdir(parents=True)
        sentinel = self.destination / 'custom.txt'
        sentinel.write_text('keep me')
        with self.assertRaisesRegex(ValueError, 'unmanaged'):
            self.install()
        h.save(self.destination / setup.RECEIPT, {'repository': '/other'})
        with self.assertRaisesRegex(ValueError, 'another checkout'):
            self.install()
        self.assertEqual(sentinel.read_text(), 'keep me')

    def test_failed_publication_restores_previous_installation(self):
        self.install()
        before = (self.destination / 'Bridge.lua').read_bytes()
        (self.root / setup.PLUGIN / 'Bridge.lua').write_bytes(before + b'\n-- changed\n')
        original = Path.rename
        def rename(source, target):
            if source.name.startswith('.lightroom-harness-'):
                raise OSError('simulated publication failure')
            return original(source, target)
        with patch.object(Path, 'rename', rename), self.assertRaises(OSError):
            self.install()
        self.assertEqual((self.destination / 'Bridge.lua').read_bytes(), before)

    @unittest.skipUnless(LIB.exists(), 'Adobe Lua runtime absent')
    def test_installed_lua_uses_bound_unicode_path_and_manual_connection(self):
        self.install()
        source = '''
function import(name)
 if name=='LrFileUtils' then return {exists=function() return 'file' end} end
 if name=='LrPathUtils' then return {child=function(a,b) return a..'/'..b end} end
 error(name)
end
_PLUGIN={path='/installed/plugin'}
package.preload.InstalledConfig=function()
'''
        source += (self.destination / 'InstalledConfig.lua').read_text() + '\nend\n'
        source += 'local config=(function()\n' + (self.destination / 'Config.lua').read_text() + '\nend)()\n'
        # Compare Lua's decoded UTF-8 string against its independently encoded bytes.
        expected = str(self.root / 'work/lr-link').encode('utf-8')
        source += 'assert(#config.root==' + str(len(expected)) + ')\n'
        for index, byte in enumerate(expected, 1):
            source += f'assert(config.root:byte({index})=={byte})\n'
        source += 'local info=(function()\n' + (self.destination / 'Info.lua').read_text() + '\nend)()\n'
        source += "assert(info.LrInitPlugin==nil and info.LrLibraryMenuItems[1].file=='Start.lua')\n"
        run_lua(source)


if __name__ == '__main__':
    unittest.main()
