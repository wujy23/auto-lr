#!/usr/bin/env python3
"""Build a versioned, source-only handoff bundle with per-file SHA-256 hashes."""
from pathlib import Path
import argparse
import hashlib
import json
import re
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def source_files(root):
    root = Path(root)
    files = [root / name for name in (
        'README.md', 'AGENTS.md', '.gitignore', 'VERSION', 'CHANGELOG.md',
        'main.py', 'harness.py', 'lr_link.py', 'batch_style_test.py',
        '.github/workflows/ci.yml',
    )]
    for directory, pattern in (('scripts', '*.py'), ('tests', '*.py'),
                               ('LightroomLink.lrplugin', '*.lua'), ('skills', '*.md'),
                               ('.agents/skills', '*.md'), ('goals', '*.json'), ('docs', '*.md')):
        files.extend(p for p in (root / directory).rglob(pattern)
                     if p.name != 'InstalledConfig.lua')
    # Only include a license if the maintainer has actually selected one.
    if (root / 'LICENSE').exists():
        files.append(root / 'LICENSE')
    for file in files:
        if (not file.is_file() or file.is_symlink()
                or not file.resolve().is_relative_to(root.resolve())):
            raise ValueError('Missing, symbolic, or external source file: ' + str(file))
    for name in ('lightroom-edit', 'lightroom-setup'):
        if root / '.agents/skills' / name / 'SKILL.md' not in files:
            raise ValueError('Missing public Skill: ' + name)
    return sorted(set(files))


def package(root=ROOT, output_dir=None):
    root = Path(root)
    files = source_files(root)
    version = (root / 'VERSION').read_text(encoding='utf-8').strip()
    if not re.fullmatch(r'\d+\.\d+\.\d+(?:-[a-zA-Z0-9.-]+)?', version):
        raise ValueError('Invalid release VERSION')
    # Snapshot once: hashes and archived contents must describe exactly the same bytes.
    payload = {file.relative_to(root).as_posix(): file.read_bytes() for file in files}
    manifest = {'version': version, 'files': {
        name: hashlib.sha256(data).hexdigest() for name, data in payload.items()}}
    payload['SOURCE_MANIFEST.json'] = (json.dumps(manifest, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
    folder = Path(output_dir) if output_dir is not None else root / 'dist'
    folder.mkdir(parents=True, exist_ok=True)
    output = folder / f'Codex-Auto-LR-{version}-source.zip'
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(payload.items()):
            entry = zipfile.ZipInfo('Codex-Auto-LR/' + name, date_time=(1980, 1, 1, 0, 0, 0))
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.create_system = 3
            entry.external_attr = 0o100644 << 16
            archive.writestr(entry, data)
    archive_bytes = output.read_bytes()
    checksum = hashlib.sha256(archive_bytes).hexdigest()
    output.with_suffix('.zip.sha256').write_text(f'{checksum}  {output.name}\n', encoding='utf-8')
    # Refresh the previous public filename too, so an old link cannot deliver stale code.
    (folder / 'lightroom-harness-source.zip').write_bytes(archive_bytes)
    return output


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path)
    args = parser.parse_args()
    print(package(output_dir=args.output_dir))
