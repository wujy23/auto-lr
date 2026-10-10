#!/usr/bin/env python3
"""Seven selected style/material experiments. Preview by default; --run executes via harness.py."""
from __future__ import annotations

import argparse
import contextlib
import csv
import fcntl
import hashlib
import html
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent
EXPERIMENT_IDS = ('E03', 'E06', 'E10', 'E16', 'E20', 'E21', 'E25')
STYLES = [
    ('古典明暗', '人物/风格样例/古典明暗'),
    ('黑金', '城市/风格样例/黑金夜景'),
    ('下午城市', '城市/风格样例/下午城市'),
    ('逆光通透', '静物/风格样例/逆光通透'),
]
MATERIALS = [
    ('北外滩第三张', '城市/北外滩/DSC_0217.NEF'),
    ('迪拜', '城市/城市 - 冷暖对比（迪拜）/raw/p01_aadm_5hmZAOBEWaYJxltIgRa33Y3TPmI.nef'),
    ('法兰克福', '城市/城市 - 蓝金夜景（法兰克福）/raw/p01_neuralyzer_viDXHgcEP2IpXXrnZrGyFqEeA8d.RAF'),
    ('玻璃饮品', '静物/静物 - 逆光通透（玻璃饮品）/raw/p01_ilia3101_iTUsp7S7Z7XIDvqizEjQclxrlXl.ARW'),
    ('酒瓶与手表', '静物/产品 - 暗调质感（酒瓶与手表）/raw/p01_knminhaz_raw_1.dng'),
    ('古典明暗人像', '人物/人像 - 古典明暗/raw/Mairi_Troisieme.ORF'),
    ('室内人像', '人物/人像 - 室内电影感/raw/p01_pu.photograph_raw_1.arw'),
]
READY = {'goal_ready', 'awaiting_confirmation'}  # Legacy unexecuted goals are also readable.
FAILED = {'stopped', 'blocked', 'export_failed'}


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def save(path, data):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def resolve(value):
    p = Path(value).expanduser()
    return (p if p.is_absolute() else ROOT / p).resolve()


def experiments(dataset):
    references = []
    for name, folder in STYLES:
        images = sorted(p for p in (dataset / folder).rglob('*')
                        if p.is_file() and not p.name.startswith('.')
                        and p.suffix.lower() in {'.jpg', '.jpeg', '.png', '.webp'})
        require(len(images) == 1, f'{name} 必须恰有一张参考图，实际 {len(images)} 张：{folder}')
        references.append((name, images[0]))
    items = []
    for material, source in MATERIALS:
        source = dataset / source
        require(source.is_file(), f'缺少 RAW：{source}')
        for style, reference in references:
            items.append({'id': f'E{len(items)+1:02d}', 'material': material, 'style': style,
                          'source': str(source), 'reference': str(reference)})
    return [item for item in items if item['id'] in EXPERIMENT_IDS]


def selection(items, only):
    wanted = {value.strip() for value in only.split(',')} if only else set(EXPERIMENT_IDS)
    require(wanted <= set(EXPERIMENT_IDS), '本脚本只运行：' + ','.join(EXPERIMENT_IDS))
    require(wanted <= {x['id'] for x in items}, '未知实验编号：' + ','.join(sorted(wanted - {x['id'] for x in items})))
    return [x for x in items if x['id'] in wanted]


@contextlib.contextmanager
def batch_lock():
    (ROOT / 'work').mkdir(exist_ok=True)
    with (ROOT / 'work/style-batch.lock').open('a') as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError('另一个批量脚本正在运行。') from exc
        yield


def invoke(arguments, log):
    """Use only the public entry point; keep process output and terminate children on Ctrl-C."""
    command = [sys.executable, '-B', str(ROOT / 'harness.py'), *arguments]
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open('a', encoding='utf-8') as stream:
        stream.write(json.dumps({'command': command, 'started': time.time()}, ensure_ascii=False) + '\n')
        stream.flush()
        proc = subprocess.Popen(command, cwd=ROOT, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, start_new_session=True)
        try:
            for line in proc.stdout:
                print(line, end='', flush=True)
                stream.write(line)
                stream.flush()
            return proc.wait()
        except BaseException:
            if proc.poll() is None:
                os.killpg(proc.pid, signal.SIGINT)
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid, signal.SIGKILL)
                    proc.wait()
            raise
        finally:
            proc.stdout.close()


def status(batch, item):
    path = batch / item['id'] / 'run/state.json'
    if path.is_file():
        return read(path)['status']
    return 'interrupted' if (batch / item['id'] / 'started.json').exists() else 'not_started'


def copy_checked(source, destination, expected):
    if destination.exists():
        require(sha(destination) == expected, f'输入副本已变化：{destination}')
        return
    require(sha(source) == expected, f'原素材已变化：{source}')
    destination.parent.mkdir(parents=True, exist_ok=True)
    # A real copy, not a hard link: Lightroom may write metadata to the imported copy.
    with Path(source).open('rb') as src, destination.open('xb') as dst:
        shutil.copyfileobj(src, dst)
    require(sha(destination) == expected, f'复制校验失败：{destination}')


def baseline_check(batch, items, current):
    baseline = read(batch / current['id'] / 'run/initial/baseline.json')
    for other in items:
        path = batch / other['id'] / 'run/initial/baseline.json'
        if other['id'] != current['id'] and other['source'] == current['source'] and path.exists():
            require(read(path)['settings']['develop'] == baseline['settings']['develop'],
                    f'{current["id"]} 与 {other["id"]} 初始显影设置不同；停止以避免无效对照。')


def complete_export(run):
    info = read(run / 'export.json')
    path = Path(info['path'])
    require(path.is_file() and sha(path) == info['sha256'], f'成片缺失或摘要不符：{path}')
    return path


def report(batch, manifest):
    rows, cards = [], []
    def url(p):
        return quote(os.path.relpath(p, batch), safe='/')
    def picture(path, label):
        return (f'<figure><a href="{url(path)}"><img loading="lazy" src="{url(path)}"></a>'
                f'<figcaption>{html.escape(label)}</figcaption></figure>') if path.is_file() else ''
    report_items = selection(manifest['items'], None)
    for item in report_items:
        run = batch / item['id'] / 'run'
        state = read(run / 'state.json') if (run / 'state.json').exists() else {}
        summary = read(run / 'summary.json') if (run / 'summary.json').exists() else {}
        st = status(batch, item)
        exported, error = '', state.get('error') or ''
        if st == 'complete':
            try:
                exported = str(complete_export(run))
            except (OSError, ValueError, KeyError) as exc:
                st, error = 'export_invalid', str(exc)
        row = {'id': item['id'], 'material': item['material'], 'style': item['style'],
               'status': st, 'rounds': len(list(run.glob('round-*/review.json'))),
               'seconds': round(sum(summary.get('seconds_by_stage', {}).values()), 2),
               'export': exported, 'error': error, 'stop_reason': state.get('stop_reason') or ''}
        rows.append(row)
        images = picture(batch / 'references' / item['reference_name'], '风格参考')
        images += picture(run / 'initial/preview.jpg', '初始基线')
        first = run / 'round-00/result/preview.jpg'
        images += picture(first, '初次结果（预览）')
        final = Path(state['result_preview']) if state.get('result_preview') else None
        if final is not None and final != first:
            images += picture(final, '最终预览（以状态判断是否通过）')
        if exported:
            images += f'<p><a href="{url(Path(exported))}">下载正式成片</a></p>'
        cards.append(f'<article data-material="{html.escape(item["material"])}" data-style="{html.escape(item["style"])}">'
                     f'<h2>{item["id"]} · {html.escape(item["material"])} × {html.escape(item["style"])}</h2>'
                     f'<p>{st} · {row["rounds"]} 轮 · {row["seconds"]} 秒</p><div class="images">{images}</div>'
                     f'<p>{html.escape(error or row["stop_reason"])}</p><a href="{url(run)}/">运行记录与 prompt</a></article>')
    with (batch / 'results.csv').open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    selectors = ''
    for key in ('material', 'style'):
        selectors += f'<select id="{key}"><option value="">全部{ "素材" if key == "material" else "风格" }</option>'
        selectors += ''.join(f'<option>{html.escape(v)}</option>' for v in dict.fromkeys(i[key] for i in report_items)) + '</select>'
    page = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>风格交叉实验</title>
<style>body{font:16px/1.6 system-ui;background:#f3f3ef;margin:24px;color:#26332d}article{background:white;padding:20px;margin:20px 0;border-radius:12px}h2{font-size:20px}.images{display:flex;flex-wrap:wrap;gap:16px}figure{margin:0;width:240px}img{width:240px;height:190px;object-fit:contain;background:#eee}a{color:#276749}select{padding:10px;margin-right:12px}article[hidden]{display:none}</style>
<h1>7 组精选风格实验</h1><p>预览不代表质检通过；只有 complete 且导出校验通过才提供正式成片。耗时为引擎记录的各阶段耗时总和。</p>'''
    page += selectors + ''.join(cards) + '''<script>for(const s of document.querySelectorAll('select'))s.onchange=()=>{for(const a of document.querySelectorAll('article'))a.hidden=[...document.querySelectorAll('select')].some(s=>s.value&&a.dataset[s.id]!==s.value);};</script></html>'''
    (batch / 'comparison.html').write_text(page, encoding='utf-8')
    return rows


def initialize(batch, items, args):
    options = {k: getattr(args, k) for k in ('planner_model', 'reviewer_model', 'max_corrections', 'agent_timeout')}
    if (batch / 'batch.json').exists():
        manifest = read(batch / 'batch.json')
        require(manifest['root'] == str(ROOT) and manifest['batch_dir'] == str(batch), '批次目录已搬迁，请使用新批次。')
        for key, value in options.items():
            require(value is None or value == manifest['options'][key], f'已有批次的 {key} 已固定；换参数请用新 --batch-dir。')
        for name, expected in manifest['engine_hashes'].items():
            require(sha(ROOT / name) == expected, f'运行代码或方法已变化：{name}；请使用新批次。')
        return manifest
    require(not batch.exists() or not any(batch.iterdir()), '目录已存在且不是有效批次，请换一个 --batch-dir。')
    options['max_corrections'] = options['max_corrections'] if options['max_corrections'] is not None else 2
    options['agent_timeout'] = options['agent_timeout'] or 900
    hashes = {p: sha(p) for p in {x['source'] for x in items} | {x['reference'] for x in items}}
    for item in items:
        item['source_sha256'] = hashes[item['source']]
        item['reference_sha256'] = hashes[item['reference']]
        item['reference_name'] = item['style'] + Path(item['reference']).suffix.lower()
    files = [ROOT / n for n in ('main.py', 'harness.py', 'lr_link.py', 'batch_style_test.py')]
    files += sorted((ROOT / 'skills').rglob('*.md')) + sorted((ROOT / 'LightroomLink.lrplugin').glob('*.lua'))
    manifest = {'version': 1, 'root': str(ROOT), 'batch_dir': str(batch), 'items': items, 'options': options,
                'engine_hashes': {str(p.relative_to(ROOT)): sha(p) for p in files}, 'created': time.time()}
    batch.mkdir(parents=True, exist_ok=True)
    for item in items:
        copy_checked(item['reference'], batch / 'references' / item['reference_name'], item['reference_sha256'])
    save(batch / 'batch.json', manifest)
    return manifest


def execute_batch(batch, manifest, selected, skip_failed=False):
    options = manifest['options']
    for position, item in enumerate(selected, 1):
        st = status(batch, item)
        folder, run = batch / item['id'], batch / item['id'] / 'run'
        if st == 'complete':
            complete_export(run)
            continue
        if st == 'limit_reached':
            print(f'{item["id"]} 已达到修正上限，保留结果并跳过（不重试）。', flush=True)
            continue
        if skip_failed and st in FAILED:
            print(f'跳过失败实验 {item["id"]}：{st}（不重试）', flush=True)
            continue
        require(st == 'not_started' or st in READY, f'{item["id"]} 状态为 {st}，禁止自动重放。详见 {run}')
        print(f'\n[{position}/{len(selected)}] {item["id"]}：{item["material"]} × {item["style"]}', flush=True)
        reference = batch / 'references' / item['reference_name']
        require(sha(reference) == item['reference_sha256'], f'批次参考图已变化：{reference}')
        if st == 'not_started':
            target = folder / 'input' / Path(item['source']).name
            copy_checked(item['source'], target, item['source_sha256'])
            request = (f'将参考图所体现的「{item["style"]}」风格迁移到当前「{item["material"]}」素材。'
                       '结合原片内容迁移色彩、影调和光影关系，保留主体身份、场景内容与构图；'
                       '不要求复制参考图中的物体或拍摄场景。')
            command = ['plan', '--target', str(target), '--reference', str(reference), '--style', request,
                       '--run-dir', str(run), '--max-corrections', str(options['max_corrections']),
                       '--agent-timeout', str(options['agent_timeout'])]
            for role in ('planner', 'reviewer'):
                if options[role + '_model']:
                    command += ['--' + role + '-model', options[role + '_model']]
            save(folder / 'started.json', {'command': command, 'time': time.time()})
            code = invoke(command, folder / 'console.log')
            require(code == 0 and status(batch, item) in READY, f'{item["id"]} 目标生成失败；查看 console.log。')
        # Separate plan/continue allows comparing baselines before any edits, without a human gate.
        baseline_check(batch, manifest['items'], item)
        code = invoke(['continue', '--run-dir', str(run)], folder / 'console.log')
        report(batch, manifest)
        final_status = status(batch, item)
        require(code == 0 and final_status in ('complete', 'limit_reached'),
                f'{item["id"]} 未完成，已停止后续实验。')
        if final_status == 'complete':
            complete_export(run)
        else:
            print(f'{item["id"]} 已达到修正上限，正常结束；保留当前预览，继续下一组。', flush=True)
    print(f'\n本次选择已处理；结果：{batch / "comparison.html"}', flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--run', action='store_true', help='实际运行；不弹出目标确认')
    mode.add_argument('--report', action='store_true', help='仅刷新已有批次的汇总')
    mode.add_argument('--dry-run', action='store_true', help='仅列出实验（默认），不连接 LR')
    parser.add_argument('--batch-dir', type=resolve, default=ROOT / 'experiments/style-selected-7')
    parser.add_argument('--dataset', type=resolve, default=ROOT / 'data_set')
    parser.add_argument('--only', help='从这 7 组中再筛选，例如 E03 或 E03,E06')
    parser.add_argument('--skip-failed', action='store_true', help='跳过已结束的失败实验；不重试、不跳过写入不确定状态')
    parser.add_argument('--planner-model')
    parser.add_argument('--reviewer-model')
    parser.add_argument('--max-corrections', type=int, choices=range(3))
    parser.add_argument('--agent-timeout', type=int)
    args = parser.parse_args(argv)
    require(args.agent_timeout is None or args.agent_timeout > 0, '--agent-timeout 必须为正数')
    existing = args.batch_dir / 'batch.json'
    items = read(existing)['items'] if existing.exists() else experiments(args.dataset)
    selected = selection(items, args.only)
    for item in selected:
        print(f'{item["id"]}  {item["material"]} × {item["style"]}')
    print(f'本次选择 {len(selected)} / {len(items)} 组；北外滩第三张 = DSC_0217.NEF。')
    if not args.run and not args.report:
        print('仅预览；加 --run 执行。每组使用独立 RAW 副本，串行处理。')
        return 0
    with batch_lock():
        if args.report:
            require(existing.exists(), '尚未创建批次。')
            report(args.batch_dir, read(existing))
            return 0
        manifest = initialize(args.batch_dir, items, args)
        try:
            require(invoke(['doctor'], args.batch_dir / 'doctor.log') == 0, '环境诊断失败；查看 doctor.log。')
            execute_batch(args.batch_dir, manifest, selection(manifest['items'], args.only), args.skip_failed)
        finally:
            report(args.batch_dir, manifest)
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError) as error:
        print(f'停止：{error}', file=sys.stderr)
        raise SystemExit(2)
    except KeyboardInterrupt:
        print('已中断；下次不会自动重放不确定或已执行的写入。', file=sys.stderr)
        raise SystemExit(130)
