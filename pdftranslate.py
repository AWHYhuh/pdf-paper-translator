#!/usr/bin/env python3
"""Portable translation entry point; secrets are passed only through the environment."""
from __future__ import annotations

import argparse
import importlib.metadata
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time

from paperflow import (archive_pdf, now_iso, update_yaml_field, validate_archive,
                       update_validation_report)


def token_metrics(text):
    labels = ['Total tokens', 'Prompt tokens', 'Cache hit prompt tokens', 'Completion tokens']
    values = []
    for label in labels:
        matches = re.findall(r'(?<!hit )' + re.escape(label) + r':\s*([\d,]+)', text, re.I)
        values.append(int(matches[-1].replace(',', '')) if matches else None)
    matches = re.findall(r'Total Token Usage:[\s\S]{0,500}?Total\s+([\d,]+),\s*Prompt\s+([\d,]+),\s*Cache Hit Prompt\s+([\d,]+),\s*Completion\s+([\d,]+)', text)
    if matches:
        values = [int(x.replace(',', '')) for x in matches[-1]]
    return values


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('pdf', type=Path)
    for name in ('paper-id', 'library-root', 'runtime-directory', 'pages', 'output-directory'):
        p.add_argument('--' + name)
    p.add_argument('--model', default='deepseek-v4-flash')
    for name in ('qps', 'workers'):
        p.add_argument('--' + name, type=int, choices=range(1, 21), default=2)
    for name in ('translate-table-text', 'enable-auto-glossary', 'skip-scanned-detection', 'preflight'):
        p.add_argument('--' + name, action='store_true')
    return p


def main():
    args = parser().parse_args()
    source = args.pdf.resolve()
    if not source.is_file() or source.suffix.lower() != '.pdf':
        raise ValueError(f'Not a PDF: {source}')
    candidates = [Path(args.runtime_directory)] if args.runtime_directory else []
    if os.environ.get('PDF_PAPER_TRANSLATOR_RUNTIME'):
        candidates.append(Path(os.environ['PDF_PAPER_TRANSLATOR_RUNTIME']))
    candidates += [Path(sys.prefix), Path(__file__).parent / '.venv']
    relative = Path('Scripts/pdf2zh.exe') if os.name == 'nt' else Path('bin/pdf2zh')
    executable = next((r / relative for r in candidates if (r / relative).is_file()), None)
    if executable is None:
        raise RuntimeError('Translation runtime missing; run install.sh on macOS/Linux.')
    archived = source.name == 'paper.pdf' and (source.parent / 'metadata.yaml').exists()
    library = Path(args.library_root).resolve() if args.library_root else source.parent / 'papers'
    if args.preflight:
        print(f'Preflight: source={source}\nRuntime={executable}\nModel={args.model}')
        print('API key: ' + ('configured (hidden)' if os.environ.get('DEEPSEEK_API_KEY', '').strip() else 'missing; translation unavailable'))
        return 0
    key = os.environ.get('DEEPSEEK_API_KEY', '').strip()
    if not key:
        raise RuntimeError('DEEPSEEK_API_KEY is not set in the current process environment.')
    paper = source.parent if archived else archive_pdf(source, library, args.paper_id)[0]
    output = Path(args.output_directory).resolve() if args.output_directory else paper
    if args.pages and not args.output_directory:
        output = paper / 'trials' / ('pages-' + re.sub(r'[^0-9A-Za-z]+', '-', args.pages).strip('-'))
    output.mkdir(parents=True, exist_ok=True)
    command = [str(executable), '--deepseek', '--deepseek-model', args.model,
               '--deepseek-thinking-mode', 'disabled', '--lang-in', 'en', '--lang-out', 'zh',
               '--qps', str(args.qps), '--pool-max-workers', str(args.workers),
               '--watermark-output-mode', 'no_watermark']
    if not args.enable_auto_glossary:
        command.append('--no-auto-extract-glossary')
    if args.translate_table_text:
        command.append('--translate-table-text')
    if args.skip_scanned_detection:
        command.append('--skip-scanned-detection')
    if args.pages:
        command += ['--pages', args.pages, '--only-include-translated-page']
    env = os.environ.copy()
    env['PDF2ZH_DEEPSEEK_API_KEY'] = key
    started = time.monotonic()
    lines, code, error = [], -1, None
    # Isolate new outputs so stale PDFs cannot turn a failed run into a success.
    with tempfile.TemporaryDirectory(prefix='.translation-', dir=output) as temporary:
        command += ['--output', temporary, str(paper / 'paper.pdf')]
        process = None
        try:
            process = subprocess.Popen(command, env=env, stdout=subprocess.PIPE,
                                       stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace')
            for line in process.stdout:
                safe = line.replace(key, '[REDACTED]')
                lines.append(safe)
                print(safe, end='', flush=True)
            code = process.wait()
            if code:
                raise RuntimeError(f'PDFMathTranslate exited with code {code}.')
            for kind in ('mono', 'dual'):
                raw = Path(temporary) / f'paper.no_watermark.zh.{kind}.pdf'
                if not raw.is_file():
                    raise RuntimeError(f'Missing generated {kind} PDF')
            for kind in ('mono', 'dual'):
                shutil.move(str(Path(temporary) / f'paper.no_watermark.zh.{kind}.pdf'),
                            str(output / f'paper_zh-CN.{kind}.pdf'))
        except BaseException as exc:
            error = exc
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            if isinstance(exc, KeyboardInterrupt):
                code = 130
        finally:
            if process is not None and process.stdout is not None:
                process.stdout.close()
            total, prompt, hit, completion = token_metrics(''.join(lines))
            rates = {'deepseek-v4-flash': (0.003, 0.15, 0.6), 'deepseek-flash': (0.003, 0.15, 0.6), 'deepseek-v4-pro': (0.022, 0.66, 1.98)}
            cost = '未计算（模型或 token 信息不完整）'
            if args.model in rates and all(x is not None for x in (prompt, hit, completion)):
                rate = rates[args.model]
                low = (hit * rate[0] + max(0, prompt - hit) * rate[1] + completion * rate[2]) / 1_000_000
                cost = f'美元 {low:.6f}–{2 * low:.6f}（谷时至峰时范围）'
            metric = lambda x: '未记录' if x is None else str(x)
            versions = ', '.join(f'{p} {importlib.metadata.version(p)}' for p in ('pdf2zh-next', 'babeldoc'))
            entry = (f'\n## {now_iso()}\n\n- 模型：`{args.model}`；思考模式：disabled\n'
                     f'- 工具：{versions}\n- 范围：{args.pages or "全文"}\n'
                     f'- 退出码：{code}；状态：{"failed" if error else "generated"}\n'
                     f'- 耗时：{time.monotonic() - started:.2f} 秒\n'
                     f'- 总 tokens：{metric(total)}\n- 输入 tokens：{metric(prompt)}\n'
                     f'- 缓存命中输入 tokens：{metric(hit)}\n- 输出 tokens：{metric(completion)}\n'
                     f'- 估算费用：{cost}；账户实际扣费：待核对\n'
                     '- 价格来源：[DeepSeek 定价](https://api-docs.deepseek.com/quick_start/pricing/)，快照 2026-09-16。\n'
                     '- 译文为机器翻译，待人工校对。\n')
            with (output / 'translation-report.md').open('a', encoding='utf-8') as report:
                report.write(entry)
    if error:
        update_yaml_field(paper / 'metadata.yaml', 'translation_status', 'failed')
        raise error
    if not args.pages and output == paper:
        result = validate_archive(paper)
        update_validation_report(paper, result)
        if result['status'] != 'passed':
            update_yaml_field(paper / 'metadata.yaml', 'translation_status', 'validation_failed')
            raise RuntimeError('Mechanical validation failed; see translation-report.md')
    update_yaml_field(paper / 'metadata.yaml', 'translation_status',
                      'pilot_completed' if args.pages else ('completed' if output == paper else 'generated_external'))
    print(f'Translation report: {output / "translation-report.md"}')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as exc:
        print(f'Error: {exc}', file=sys.stderr)
        sys.exit(1)
