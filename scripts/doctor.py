"""Read-only checks; optional model smoke request never connects to Lightroom."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time

import main as workflow
from scripts.setup import PLUGIN, RECEIPT, desired_files, modules_dir, sha
import hashlib


def command(argv, timeout=20):
    try:
        result = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                                timeout=timeout, env=workflow.agent_environment())
        return result.returncode == 0, (result.stdout + result.stderr).strip()
    except (OSError, subprocess.TimeoutExpired) as error:
        return False, type(error).__name__ + ': ' + str(error)


def check(repo, *, smoke_model=False, destination=None):
    repo = Path(repo).resolve()
    checks = []

    def add(name, ok, detail):
        checks.append({'check': name, 'ok': bool(ok), 'detail': detail})

    add('platform', sys.platform == 'darwin', 'macOS is required for this trial')
    add('python', sys.version_info >= (3, 10), sys.version.split()[0])
    executable = None
    try:
        executable = workflow.resolve_codex()
        ok, detail = command([executable, '--version'])
        add('codex', ok, detail)
        ok, detail = command([executable, 'login', 'status'])
        # Credential storage is never read or copied by the harness.
        add('codex_login', ok, 'Credentials present (not a network/quota test)' if ok else detail)
        ok, detail = command([executable, 'exec', '--help'])
        required = ['--ignore-user-config', '--output-schema', '--output-last-message', '--ephemeral']
        missing = [flag for flag in required if flag not in detail]
        add('codex_flags', ok and not missing, {'missing': missing})
    except (OSError, RuntimeError, workflow.WorkflowError, subprocess.TimeoutExpired) as error:
        add('codex', False, str(error))
    destination = Path(destination) if destination else modules_dir() / PLUGIN
    try:
        receipt = json.loads((destination / RECEIPT).read_text())
        expected = {n: hashlib.sha256(v).hexdigest() for n, v in desired_files(repo).items()}
        valid = receipt.get('repository') == str(repo) and receipt.get('files') == expected
        valid = valid and all((destination / n).is_file() and sha(destination / n) == digest
                              for n, digest in expected.items())
        add('plugin_install', valid, str(destination) if valid else 'Run harness.py setup while disconnected')
    except (OSError, ValueError) as error:
        add('plugin_install', False, 'Run harness.py setup: ' + str(error))
    try:
        status = json.loads((repo / 'work/lr-link/status.json').read_text())
        age = time.time() - status.get('updatedAt', 0)
        live = status.get('state') == 'ready' and 0 <= age <= 15
        add('connection', live, {'state': status.get('state'), 'heartbeat_age_seconds': round(age, 1)})
        add('loaded_plugin', status.get('pluginVersion') == '0.8.0' and
            status.get('communicationRoot') == str(repo / 'work/lr-link'),
            'Requires Lightroom Link 0.8.0 loaded from this installation; reload then Start connection')
    except (OSError, ValueError) as error:
        add('connection', False, 'Open Lightroom Classic and manually Start connection: ' + str(error))
    if smoke_model and executable:
        # Use exactly the same process/sandbox/structured-output path as photo agents.
        with tempfile.TemporaryDirectory(prefix='lr-harness-smoke-') as tmp:
            root = Path(tmp)
            schema = root / 'schema.json'
            schema.write_text(json.dumps({'type': 'object', 'properties': {'ok': {'type': 'boolean'}},
                                          'required': ['ok'], 'additionalProperties': False}))
            argv = workflow.codex_exec_command(executable, root, schema, root / 'output.json')
            try:
                workflow.process(argv, root / 'logs', prompt='Return the JSON object {"ok": true}. Do not use tools.',
                                 timeout=120, cwd=tmp, env=workflow.agent_environment())
                add('model_smoke', json.loads((root / 'output.json').read_text()) == {'ok': True},
                    'Structured model call via the same harness runner completed; no photos sent')
            except (OSError, ValueError, RuntimeError, workflow.WorkflowError, subprocess.TimeoutExpired) as error:
                add('model_smoke', False, str(error))
    return {'ok': all(item['ok'] for item in checks), 'checks': checks,
            'note': 'Read-only diagnostics do not edit photos. Login presence alone does not prove model access.'}
