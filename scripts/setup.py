"""Idempotent macOS plug-in deployment, with explicit repository binding."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
import time
import uuid

PLUGIN = 'LightroomLink.lrplugin'
RECEIPT = 'harness-install.json'


def modules_dir():
    return Path.home() / 'Library/Application Support/Adobe/Lightroom/Modules'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def lua_string(value):
    # Decimal byte escapes are supported by Lua 5.1, including UTF-8 paths.
    return '"' + ''.join('\\%03d' % byte for byte in str(value).encode('utf-8')) + '"'


def desired_files(repo):
    files = {p.name: p.read_bytes() for p in (repo / PLUGIN).glob('*.lua')
             if p.name != 'InstalledConfig.lua'}
    if not {'Info.lua', 'Bridge.lua', 'Config.lua', 'Start.lua', 'Stop.lua'} <= files.keys():
        raise ValueError('Bundled plug-in is incomplete')
    files['InstalledConfig.lua'] = ('return { root = ' + lua_string(repo / 'work/lr-link') +
                                    ', sessionSeconds = 1800 }\n').encode()
    return files


def install(repo, *, dry_run=False, destination=None):
    if sys.platform != 'darwin':
        raise RuntimeError('This trial supports macOS only; Windows process/locking support is not implemented.')
    repo = Path(repo).resolve()
    destination = Path(destination) if destination else modules_dir() / PLUGIN
    if destination.is_symlink():
        raise ValueError('Existing plug-in is a symlink; refusing to replace it.')
    files = desired_files(repo)
    hashes = {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}
    previous = None
    if destination.exists():
        receipt = destination / RECEIPT
        if not receipt.is_file():
            raise ValueError('An unmanaged plug-in already exists at ' + str(destination) + '; preserve it and resolve the conflict first.')
        previous = json.loads(receipt.read_text())
        if previous.get('repository') != str(repo):
            raise ValueError('Plug-in is bound to another checkout: ' + str(previous.get('repository')))
        if previous.get('files') == hashes and all((destination / n).is_file() and sha(destination / n) == v
                                                  for n, v in hashes.items()):
            return {'ok': True, 'action': 'unchanged', 'plugin': str(destination),
                    'next': 'In Lightroom Classic, reload if needed, then manually choose Start connection.'}
    status_path = repo / 'work/lr-link/status.json'
    if status_path.exists():
        status = json.loads(status_path.read_text())
        if status.get('state') == 'ready' and time.time() - status.get('updatedAt', 0) <= 15 and not dry_run:
            raise RuntimeError('Connection is active. Stop connection in Lightroom Classic before installing/updating.')
    result = {'ok': True, 'action': 'would_update' if previous else 'would_install',
              'plugin': str(destination), 'communication_directory': str(repo / 'work/lr-link'),
              'next': 'Restart Lightroom Classic or add this folder in File > Plug-in Manager, disable any older duplicate, then manually choose Start connection.'}
    if dry_run:
        return result
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix='.lightroom-harness-', dir=destination.parent))
    backup = None
    try:
        for name, data in files.items():
            (staging / name).write_bytes(data)
        (staging / RECEIPT).write_text(json.dumps({'repository': str(repo), 'files': hashes,
                                                 'installed_at': time.time()}, indent=2), encoding='utf-8')
        if destination.exists():
            # Backup is outside Modules, so Lightroom will not discover a second copy.
            backups = destination.parent.parent / 'HarnessPluginBackups'
            backups.mkdir(exist_ok=True)
            backup = backups / (PLUGIN + '-' + uuid.uuid4().hex[:12])
            destination.rename(backup)
        try:
            staging.rename(destination)
        except OSError:
            if backup is not None:
                backup.rename(destination)
            raise
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    result.update(action='updated' if previous else 'installed', backup=str(backup) if backup else None)
    return result
