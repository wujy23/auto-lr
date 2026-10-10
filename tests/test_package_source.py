"""Release boundary checks, using synthetic private files and isolated folders."""
import hashlib
import json
from pathlib import Path
import shutil
import sys
import subprocess
import tempfile
import unittest
import uuid
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.package_source import ROOT, package, source_files


class SourcePackageTests(unittest.TestCase):
    def fixture(self, root):
        for source in source_files(ROOT):
            dest = root / source.relative_to(ROOT)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, dest)

    def test_portable_bundle_excludes_private_files_and_matches_manifest(self):
        with tempfile.TemporaryDirectory(prefix='release 空格 ') as tmp:
            root = Path(tmp) / 'source'
            self.fixture(root)
            (root / 'LICENSE').unlink(missing_ok=True)
            sentinel = uuid.uuid4().hex.encode('ascii')
            private = [
                'data_set/private.raw', 'social-media/private.png', 'runs/state.json',
                'exports/photo.jpg', 'work/auth.json', '.env',
                'LightroomLink.lrplugin/InstalledConfig.lua',
                'LightroomLink.lrplugin/harness-install.json',
            ]
            for name in private:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(sentinel)
            output = package(root)
            with zipfile.ZipFile(output) as archive:
                self.assertIsNone(archive.testzip())
                files = {name.removeprefix('Codex-Auto-LR/'): archive.read(name)
                         for name in archive.namelist()}
                manifest = json.loads(files.pop('SOURCE_MANIFEST.json'))
                self.assertEqual(set(files), set(manifest['files']))
                for name, data in files.items():
                    self.assertEqual(hashlib.sha256(data).hexdigest(), manifest['files'][name])
                    self.assertNotIn(sentinel, data)
                self.assertTrue(set(private).isdisjoint(files))
                self.assertIn('.agents/skills/lightroom-edit/SKILL.md', files)
                self.assertIn('.agents/skills/lightroom-setup/SKILL.md', files)
                self.assertIn('.github/workflows/ci.yml', files)
                self.assertNotIn('LICENSE', files)
            checksum = output.with_suffix('.zip.sha256').read_text().split()[0]
            self.assertEqual(checksum, hashlib.sha256(output.read_bytes()).hexdigest())
            self.assertEqual(output.read_bytes(), (output.parent / 'lightroom-harness-source.zip').read_bytes())
            first = output.read_bytes()
            self.assertEqual(package(root).read_bytes(), first)

    @unittest.skipUnless(shutil.which('git'), 'Git unavailable')
    def test_git_upload_keeps_internal_references_but_excludes_user_materials(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.fixture(root)
            for name in ['references/private.jpg', 'raw/private.raw', 'social-media/private.png']:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b'private test material')
            subprocess.run(['git', 'init', '-q', str(root)], check=True, capture_output=True)
            subprocess.run(['git', '-C', str(root), 'add', '--', '.'], check=True, capture_output=True)
            result = subprocess.run(['git', '-C', str(root), 'ls-files', '-z'],
                                    check=True, capture_output=True, text=True)
            tracked = set(result.stdout.rstrip('\0').split('\0'))
            expected = {p.relative_to(root).as_posix() for p in source_files(root)}
            self.assertEqual(tracked, expected)

    def test_optional_license_is_included_when_selected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.fixture(root)
            (root / 'LICENSE').write_text('A deliberately selected test license')
            with zipfile.ZipFile(package(root)) as archive:
                self.assertEqual(archive.read('Codex-Auto-LR/LICENSE'), b'A deliberately selected test license')

    def test_missing_skill_external_file_and_invalid_version_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'source'
            self.fixture(root)
            skill = root / '.agents/skills/lightroom-edit/SKILL.md'
            original = skill.read_bytes()
            skill.unlink()
            with self.assertRaisesRegex(ValueError, 'Missing public Skill'):
                source_files(root)
            skill.write_bytes(original)
            outside = Path(tmp) / 'outside.md'
            outside.write_text('private external text')
            readme = root / 'README.md'
            readme.unlink()
            readme.symlink_to(outside)
            with self.assertRaisesRegex(ValueError, 'external source file'):
                source_files(root)
            readme.unlink()
            readme.write_text('Public README')
            (root / 'VERSION').write_text('../escape')
            with self.assertRaisesRegex(ValueError, 'Invalid release VERSION'):
                package(root)


if __name__ == '__main__':
    unittest.main()
