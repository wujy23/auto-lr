#!/usr/bin/env python3
"""Public chat entry point. All photo work is delegated to main.py."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time
import uuid

import main as workflow

ROOT = Path(__file__).resolve().parent


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    commands = p.add_subparsers(dest='command', required=True)
    for name, help_text in [('run', 'Define goal, edit, review and export without confirmation'),
                            ('plan', 'Only save a goal and baseline; do not edit')]:
        plan = commands.add_parser(name, help=help_text)
        plan.add_argument('--target', required=True)
        references = plan.add_mutually_exclusive_group(required=True)
        references.add_argument('--reference', action='append')
        references.add_argument('--no-references', action='store_true')
        plan.add_argument('--style', required=True)
        plan.add_argument('--run-dir', type=workflow.project_path)
        plan.add_argument('--planner-model')
        plan.add_argument('--reviewer-model')
        plan.add_argument('--photo-id')
        plan.add_argument('--max-corrections', type=int, choices=range(3), default=2)
        plan.add_argument('--agent-timeout', type=int, default=900)
    resume = commands.add_parser('continue', help='Execute a saved, unexecuted goal without confirmation')
    resume.add_argument('--run-dir', type=workflow.project_path, required=True)
    resume.add_argument('--approve', help='Optional legacy digest; verified if supplied')
    status = commands.add_parser('status', help='Read a saved run; never resume or retry')
    status.add_argument('--run-dir', type=workflow.project_path, required=True)
    commands.add_parser('library', help='List local inputs')
    doctor = commands.add_parser('doctor', help='Read-only environment and connection checks')
    doctor.add_argument('--smoke-model', action='store_true', help='Run a tiny model request; uses quota')
    setup = commands.add_parser('setup', help='Install/update the bundled Lightroom plug-in')
    setup.add_argument('--dry-run', action='store_true')
    return p


def audit(event):
    folder = ROOT / 'work'
    folder.mkdir(exist_ok=True)
    with (folder / 'harness-invocations.jsonl').open('a', encoding='utf-8') as stream:
        stream.write(json.dumps(event, ensure_ascii=False) + '\n')
        stream.flush()


def dispatch(args):
    if args.command in ('setup', 'doctor'):
        from scripts import setup, doctor
        result = (setup.install(ROOT, dry_run=args.dry_run) if args.command == 'setup'
                  else doctor.check(ROOT, smoke_model=args.smoke_model))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result['ok'] else 2
    argv = [args.command]
    run = getattr(args, 'run_dir', None)
    if args.command in ('plan', 'run'):
        run = run or ROOT / 'runs' / (time.strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:8])
        argv += ['--target', args.target, '--style', args.style,
                 '--python', sys.executable, '--max-corrections', str(args.max_corrections),
                 '--agent-timeout', str(args.agent_timeout)]
        if args.no_references:
            argv += ['--no-references']
        for reference in args.reference or []:
            argv += ['--reference', reference]
        for name in ('planner_model', 'reviewer_model', 'photo_id'):
            if getattr(args, name):
                argv += ['--' + name.replace('_', '-'), getattr(args, name)]
    if run is not None:
        argv += ['--run-dir', str(run)]
    if args.command == 'continue' and args.approve:
        argv += ['--approve', args.approve]
    invocation = uuid.uuid4().hex
    record = {'invocation_id': invocation, 'command': args.command,
              'run_dir': str(run) if run else None,
              'engine': str(ROOT / 'main.py'), 'engine_sha256': workflow.file_hash(ROOT / 'main.py')}
    audit({**record, 'event': 'started', 'time': time.time()})
    print(json.dumps({'harness_invocation': record}, ensure_ascii=False), flush=True)
    try:
        code = workflow.main(argv)
    except BaseException as error:
        audit({**record, 'event': 'interrupted', 'time': time.time(), 'error_type': type(error).__name__})
        raise
    audit({**record, 'event': 'finished', 'time': time.time(), 'exit_code': code})
    return code


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        return dispatch(args)
    except (OSError, ValueError, RuntimeError, workflow.WorkflowError) as error:
        print(json.dumps({'ok': False, 'error': str(error)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
