#!/usr/bin/env python3
"""Local Lightroom client: inspect, preview, and guarded single-photo edits."""
import argparse
import json
import pathlib
import time
import uuid
import math
import re

ROOT = pathlib.Path(__file__).resolve().parent / 'work' / 'lr-link'
CURVES = {'ToneCurvePV2012', 'ToneCurvePV2012Red', 'ToneCurvePV2012Green', 'ToneCurvePV2012Blue'}

def finite(value):
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)

def encode_plan(plan, photo_id):
    if plan.get('photo_id') != photo_id:
        raise ValueError('Plan photo_id must match --photo-id')
    token = plan.get('baseline_token', '')
    if not isinstance(token, str) or not re.fullmatch(r'[a-f0-9]{32}', token):
        raise ValueError('Plan requires baseline_token from settings response')
    settings = plan.get('settings', {})
    if not isinstance(settings, dict) or len(settings) > 128:
        raise ValueError('Plan may contain up to 128 numeric settings')
    rows = []
    for key, value in sorted(settings.items()):
        if not isinstance(key, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9]*', key):
            raise ValueError('Invalid setting name')
        if not finite(value):
            raise ValueError(f'{key} must be a finite number')
        rows.append(f'{key}={value:.17g}')
    curves = plan.get('curves', {})
    if not isinstance(curves, dict):
        raise ValueError('curves must be a field-to-point-list object')
    for key, points in sorted(curves.items()):
        if key not in CURVES or not isinstance(points, list) or not 2 <= len(points) <= 16:
            raise ValueError('Unsupported curve or point count')
        last_x, last_y = -1, -1
        for point in points:
            if not isinstance(point, list) or len(point) != 2 or not all(finite(n) and 0 <= n <= 255 for n in point):
                raise ValueError('Curve points must be [x,y] in 0..255')
            x, y = point
            if x <= last_x or y < last_y:
                raise ValueError('Curves must have increasing x and nondecreasing y')
            last_x, last_y = x, y
        rows.append('curve:' + key + '=' + ','.join(f'{x:.17g}:{y:.17g}' for x,y in points))
    masks = plan.get('masks', [])
    if not isinstance(masks, list) or len(masks) > 8:
        raise ValueError('masks must contain at most eight masks')
    seen = set()
    for mask in masks:
        uid, name, kind = mask.get('id',''), mask.get('name',''), mask.get('type','')
        if not re.fullmatch(r'[a-f0-9]{32}', uid) or uid in seen or not re.fullmatch(r'[a-z][a-z0-9-]{0,63}',name):
            raise ValueError('Invalid or duplicate mask ID/name')
        seen.add(uid)
        geometry=mask.get('geometry')
        expected_size={'radial':6,'linear':4,'subject':0,'sky':0,'background':0,'existing':0}.get(kind)
        if not isinstance(geometry,list) or len(geometry)!=expected_size or not all(finite(v) for v in geometry):
            raise ValueError('Invalid mask geometry')
        local=mask.get('settings',{})
        if not isinstance(local,dict) or not local:
            raise ValueError('Mask requires local settings')
        rows.append(f'mask:{uid}:{name}:{kind}='+','.join(f'{v:.17g}' for v in geometry))
        for key,value in sorted(local.items()):
            if not re.fullmatch(r'[A-Za-z][A-Za-z0-9]*',key) or not finite(value):
                raise ValueError('Invalid local setting')
            rows.append(f'local:{uid}:{key}={value:.17g}')
    if not rows:
        raise ValueError('Plan contains no executable adjustments')
    return token + '\n' + '\n'.join(rows) + '\n'

def read_status(status_file):
    # LR's supported move API cannot replace an existing file atomically.
    # Allow for the very short heartbeat replacement interval.
    for _ in range(5):
        try:
            return json.loads(status_file.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            time.sleep(0.1)
    raise SystemExit('Plugin has not connected. Use Library > Plug-in Extras > Start connection.')

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['status', 'inspect', 'preview', 'stop', 'setexposure', 'masks', 'addradial', 'maskprobe', 'tuneradial', 'settings', 'apply', 'prepare', 'export', 'peopleskinall', 'peopleskinleft', 'peopleskinright', 'peoplebuiltin', 'peopledirectleft', 'peopledirectright', 'peoplegrouprefs', 'peoplepointarray', 'peoplepointmap', 'peopletwogroups', 'peoplecandidates'])
    parser.add_argument('--source', type=pathlib.Path)
    parser.add_argument('--catalog', default='')
    parser.add_argument('--baseline-token', default='')
    parser.add_argument('--plan', type=pathlib.Path)
    parser.add_argument('--photo-id', default='')
    parser.add_argument('--value', type=float)
    parser.add_argument('--expected-before', type=float)
    parser.add_argument('--expected-mask-count', type=int)
    parser.add_argument('--timeout', type=float, default=45)
    args = parser.parse_args()
    if args.timeout <= 0 or args.timeout > 120:
        parser.error('--timeout must be between 0 and 120 seconds')
    if args.photo_id and not args.photo_id.isascii():
        parser.error('--photo-id must contain ASCII digits')
    if args.photo_id and not args.photo_id.isdigit():
        parser.error('--photo-id must contain digits')
    if args.action in ('preview', 'setexposure', 'masks', 'addradial', 'maskprobe', 'tuneradial', 'settings', 'apply', 'export', 'peopleskinall', 'peopleskinleft', 'peopleskinright', 'peoplebuiltin', 'peopledirectleft', 'peopledirectright', 'peoplegrouprefs', 'peoplepointarray', 'peoplepointmap', 'peopletwogroups', 'peoplecandidates') and not args.photo_id:
        parser.error('This action requires --photo-id from an inspect response')
    if args.action == 'setexposure':
        if args.value is None or not -5 <= args.value <= 5:
            parser.error('--value must be between -5 and 5')
        if args.expected_before is None or not -5 <= args.expected_before <= 5:
            parser.error('--expected-before must be the current exposure between -5 and 5')
    if args.action in ('addradial', 'tuneradial'):
        if args.value is None or not -5 <= args.value <= 5:
            parser.error('--value must be between -5 and 5')
        if args.expected_mask_count is None or args.expected_mask_count < 0:
            parser.error('--expected-mask-count must be nonnegative')
    plan_payload = None
    if args.action == 'prepare' and (not args.source or not args.source.is_file()):
        parser.error('prepare requires --source pointing to a readable original file')
    if args.action == 'export' and not re.fullmatch(r'[a-f0-9]{32}', args.baseline_token):
        parser.error('export requires a fresh --baseline-token from settings')
    if args.action == 'apply':
        if not args.plan:
            parser.error('apply requires --plan')
        try:
            plan_payload = encode_plan(json.loads(args.plan.read_text()), args.photo_id)
        except (OSError, ValueError, TypeError) as error:
            parser.error(str(error))
    status_file = ROOT / 'status.json'
    status = read_status(status_file)
    status['heartbeatAgeSeconds'] = round(time.time() - status['updatedAt'], 1)
    if args.action == 'status':
        print(json.dumps(status, ensure_ascii=False, indent=2))
        return
    if status['state'] != 'ready' or status['heartbeatAgeSeconds'] > 15:
        raise SystemExit('Plugin is offline. Use Library > Plug-in Extras > Start connection.')
    if args.action == 'setexposure' and not status.get('capabilities', {}).get('setExposure'):
        raise SystemExit('Reload the updated plug-in before setting exposure.')
    if args.action in ('addradial', 'tuneradial') and not status.get('capabilities', {}).get('addRadial'):
        raise SystemExit('Reload the updated plug-in before adding a radial mask.')
    if args.action in ('settings', 'apply') and not status.get('capabilities', {}).get('applyNumeric'):
        raise SystemExit('Reload the updated plug-in before numeric batch editing.')
    if args.action == 'apply' and ('curve:' in plan_payload or 'mask:' in plan_payload) and not status.get('capabilities',{}).get('applyStructured'):
        raise SystemExit('Reload plug-in for curves and geometric masks.')
    capability = {'prepare': 'preparePhoto', 'export': 'exportFullJpeg', 'peopleskinall': 'peopleProbe', 'peopleskinleft': 'peopleProbe', 'peopleskinright': 'peopleProbe', 'peoplebuiltin': 'peopleProbe', 'peopledirectleft': 'peopleProbe', 'peopledirectright': 'peopleProbe', 'peoplegrouprefs': 'peopleProbe', 'peoplepointarray': 'peopleProbe', 'peoplepointmap': 'peopleProbe', 'peopletwogroups': 'peopleProbe', 'peoplecandidates': 'peopleProbe'}.get(args.action)
    if capability and not status.get('capabilities', {}).get(capability):
        raise SystemExit('Reload Lightroom Link 0.6 or later for import/selection and full-size JPEG export.')
    request_id = uuid.uuid4().hex
    body = f'{args.action}\n{args.photo_id}\n{int(time.time() + args.timeout)}\n'
    if plan_payload:
        body += plan_payload
    if args.action == 'prepare':
        body += str(args.source.resolve()).encode('utf-8').hex() + '\n'
        body += (args.catalog.encode('utf-8').hex() if args.catalog else '-') + '\n'
    if args.action == 'export':
        body += args.baseline_token + '\n'
    if args.action == 'setexposure':
        body += f'{args.value:.8f}\n{args.expected_before:.8f}\n'
    if args.action in ('addradial', 'tuneradial'):
        body += f'{args.value:.8f}\n{args.expected_mask_count}\n'
    temporary = ROOT / 'requests' / f'{request_id}.tmp'
    temporary.write_text(body)
    temporary.rename(temporary.with_suffix('.request'))
    response_file = ROOT / 'responses' / f'{request_id}.json'
    deadline = time.monotonic() + args.timeout
    while time.monotonic() < deadline:
        if response_file.exists():
            response = json.loads(response_file.read_text())
            print(json.dumps(response, ensure_ascii=False, indent=2))
            raise SystemExit(0 if response['ok'] else 1)
        time.sleep(0.2)
    raise SystemExit(f'Timed out. Request {request_id}; response may still arrive in {response_file}.')

if __name__ == '__main__':
    main()
