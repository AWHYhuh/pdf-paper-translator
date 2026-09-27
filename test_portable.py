"""Offline regression tests for the portable subprocess and archive boundary."""
import contextlib
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import fitz
import pdftranslate
import paperflow


class PortableTests(unittest.TestCase):
    def test_token_formats(self):
        self.assertEqual(pdftranslate.token_metrics('Total tokens: 1,200\nPrompt tokens: 1000\nCache hit prompt tokens: 800\nCompletion tokens: 200'), [1200, 1000, 800, 200])
        self.assertEqual(pdftranslate.token_metrics('Total Token Usage:\nTotal 1200, Prompt 1000, Cache Hit Prompt 800, Completion 200'), [1200, 1000, 800, 200])
        self.assertEqual(pdftranslate.token_metrics(''), [None] * 4)

    def test_native_dispatch(self):
        with patch('paperflow.subprocess.Popen') as popen:
            popen.return_value.wait.return_value = 0
            paperflow.run_translation(Path('/tmp/中文 paper'), 'test-model', True, True)
            command = popen.call_args.args[0]
            self.assertEqual(command[0], paperflow.sys.executable)
            self.assertIn('/tmp/中文 paper/paper.pdf', command)
            self.assertIn('--skip-scanned-detection', command)

    def test_success_failure_and_preflight(self):
        with tempfile.TemporaryDirectory(prefix='中文 mac test ') as tmp:
            root = Path(tmp)
            source = root / 'input.pdf'
            with fitz.open() as doc:
                page = doc.new_page()
                page.insert_text((72, 72), 'Offline test paper')
                doc.save(source)
            runtime = root / 'runtime'
            (runtime / 'bin').mkdir(parents=True)
            fake = runtime / 'bin/pdf2zh'
            fake.write_text('''#!/usr/bin/env python3
import os, pathlib, shutil, sys
out = pathlib.Path(sys.argv[sys.argv.index('--output') + 1])
print('Total Token Usage: Total 1200, Prompt 1000, Cache Hit Prompt 800, Completion 200')
print(os.environ['PDF2ZH_DEEPSEEK_API_KEY'])
if os.environ.get('FAKE_FAIL'): sys.exit(7)
for kind in ('mono', 'dual'):
    shutil.copyfile(sys.argv[-1], out / ('paper.no_watermark.zh.' + kind + '.pdf'))
''')
            fake.chmod(0o755)
            argv = ['pdftranslate', str(source), '--runtime-directory', str(runtime),
                    '--library-root', str(root / 'papers'), '--paper-id', 'test', '--pages', '1']
            with patch.dict(os.environ, {'DEEPSEEK_API_KEY': 'offline-test-secret'}):
                with patch('sys.argv', argv + ['--preflight']), contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(pdftranslate.main(), 0)
                self.assertFalse((root / 'papers').exists())
                output = io.StringIO()
                with patch('sys.argv', argv), contextlib.redirect_stdout(output):
                    self.assertEqual(pdftranslate.main(), 0)
                self.assertNotIn('offline-test-secret', output.getvalue())
                trial = root / 'papers/test/trials/pages-1'
                self.assertTrue((trial / 'paper_zh-CN.dual.pdf').exists())
                before = (trial / 'paper_zh-CN.dual.pdf').read_bytes()
                with patch.dict(os.environ, {'FAKE_FAIL': '1'}), patch('sys.argv', argv), contextlib.redirect_stdout(io.StringIO()):
                    with self.assertRaisesRegex(RuntimeError, 'code 7'):
                        pdftranslate.main()
                self.assertEqual(before, (trial / 'paper_zh-CN.dual.pdf').read_bytes())
                report = (trial / 'translation-report.md').read_text()
                self.assertIn('failed', report)
                self.assertIn('1200', report)
                self.assertNotIn('offline-test-secret', report)
                self.assertEqual(source.read_bytes(), (root / 'papers/test/paper.pdf').read_bytes())


if __name__ == '__main__':
    unittest.main()
