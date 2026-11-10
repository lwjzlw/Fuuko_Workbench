"""Portable distribution checks; all generated packages and service data are disposable."""
from __future__ import annotations

import importlib.util
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from shared import wbkit

spec = importlib.util.spec_from_file_location('fuuko_package_release', ROOT / 'scripts' / 'package_release.py')
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


class DistributionTests(unittest.TestCase):
    def test_source_zip_is_isolated_and_starts_without_sibling_workbenches(self):
        with tempfile.TemporaryDirectory(prefix='fuuko-distribution-') as temporary:
            base = Path(temporary)
            output = base / 'source.zip'
            release.build_zip(ROOT, output)
            with zipfile.ZipFile(output) as archive:
                names = archive.namelist()
                self.assertIn('shared/wbkit.py', names)
                self.assertIn('examples/hello/workbench.json', names)
                self.assertFalse(any(n.startswith(('apps/', 'work/', 'agent_records/', 'shared/host/bin/')) for n in names))
                self.assertFalse(any(n.endswith(('.pyc', '.tmp', '.bak')) or '__pycache__' in n for n in names))
                self.assertNotIn('STATUS.md', names)
                self.assertNotIn('ui/fuuko.ico', names)
                self.assertIn('SOURCE-MANIFEST.json', names)
                self.assertIn(b'* -text', archive.read('.gitattributes'))
                self.assertEqual(archive.read('shared/assets/fuuko-avatar.png'), release.generic_png(256))
                extracted = base / 'isolated'
                archive.extractall(extracted)
            code = '''
import json, pathlib, sys, urllib.request
sys.path.insert(0, sys.argv[1])
import fuuko_app
from shared import wbkit
assert wbkit.SHARED_DIR.is_relative_to(pathlib.Path(sys.argv[1]))
app = fuuko_app.build_app(port=0, data_dir=pathlib.Path(sys.argv[2]), legacy=False)
try:
    url = app.start()
    req = urllib.request.Request(url + 'api/apps', headers={'X-WB-Token': app.token})
    with urllib.request.urlopen(req, timeout=5) as response:
        data = json.load(response)
    assert data['apps'] == [], data
    with urllib.request.urlopen(url + '_shared/assets/fuuko-avatar.png', timeout=5) as response:
        assert response.read().startswith(b'\\x89PNG')
    print('isolated source package: empty hub started')
finally:
    app.stop()
'''
            done = subprocess.run([sys.executable, '-I', '-B', '-c', code, str(extracted), str(base / 'data')],
                                  capture_output=True, text=True, timeout=30,
                                  creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            self.assertEqual(done.returncode, 0, done.stdout + done.stderr)

    def test_release_never_overwrites_existing_output(self):
        with tempfile.TemporaryDirectory(prefix='fuuko-distribution-') as temporary:
            output = Path(temporary) / 'source.zip'
            output.write_bytes(b'keep')
            with self.assertRaises(FileExistsError):
                release.build_zip(ROOT, output)
            self.assertEqual(output.read_bytes(), b'keep')

    def test_release_removes_only_its_partial_output_on_failure(self):
        with tempfile.TemporaryDirectory(prefix='fuuko-distribution-') as temporary:
            output = Path(temporary) / 'source.zip'
            with patch.object(release, 'release_files', return_value={'shared/__init__.py': b''}), \
                    patch.object(zipfile.ZipFile, 'writestr', side_effect=OSError('disk error')) as write:
                with self.assertRaises(OSError):
                    release.build_zip(ROOT, output)
                write.assert_called_once()
            self.assertFalse(output.exists())

    def test_source_tree_refresh_rejects_user_edits_and_extra_files(self):
        with tempfile.TemporaryDirectory(prefix='fuuko-source-tree-') as temporary:
            output = Path(temporary) / 'source'
            release.build_tree(ROOT, output)
            release.build_tree(ROOT, output, refresh=True)
            manifest = json.loads((output / 'SOURCE-MANIFEST.json').read_text(encoding='utf-8'))
            for row in manifest['files']:
                self.assertEqual(hashlib.sha256((output / row['path']).read_bytes()).hexdigest(), row['sha256'])
            (output / 'README.md').write_text('user edit', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'user edits'):
                release.build_tree(ROOT, output, refresh=True)
            self.assertEqual((output / 'README.md').read_text(), 'user edit')
            (output / 'private.txt').write_text('keep', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'extra/missing'):
                release.build_tree(ROOT, output, refresh=True)

    def test_public_release_refuses_unreviewed_host_binary(self):
        with self.assertRaisesRegex(ValueError, 'Prebuilt hosts'):
            release.release_files(ROOT, include_host=True)

    def test_atomic_json_failure_preserves_original_and_cleans_temp(self):
        with tempfile.TemporaryDirectory(prefix='fuuko-json-') as temporary:
            target = Path(temporary) / 'settings.json'
            target.write_text('{"saved": true}', encoding='utf-8')
            with patch.object(wbkit.os, 'replace', side_effect=OSError('replace refused')):
                with self.assertRaises(OSError):
                    wbkit.write_json_atomic(target, {'saved': False})
            self.assertEqual(json.loads(target.read_text()), {'saved': True})
            self.assertEqual(list(Path(temporary).iterdir()), [target])

    def test_malformed_runtime_is_ignored_without_modifying_file(self):
        with tempfile.TemporaryDirectory(prefix='fuuko-runtime-') as temporary:
            target = Path(temporary) / 'runtime.json'
            for raw in [[], None, 'text', 1, {}, {'pid': True}, {'pid': -1},
                        {'pid': 0}, {'pid': '123'}, {'pid': 2 ** 40}]:
                with self.subTest(raw=raw):
                    source = json.dumps(raw).encode('utf-8')
                    target.write_bytes(source)
                    with patch.object(wbkit, 'pid_alive') as alive:
                        self.assertIsNone(wbkit.read_runtime(target))
                        alive.assert_not_called()
                    self.assertEqual(target.read_bytes(), source)
            for pid in [True, None, -1, 0, '123', 2 ** 40]:
                self.assertFalse(wbkit.pid_alive(pid))
            target.write_text(json.dumps({'pid': os.getpid()}), encoding='utf-8')
            self.assertEqual(wbkit.read_runtime(target), {'pid': os.getpid()})

    def test_standalone_example_health_and_authenticated_shutdown(self):
        with tempfile.TemporaryDirectory(prefix='fuuko-example-') as temporary:
            data = Path(temporary)
            child = subprocess.Popen([sys.executable, '-I', '-B', str(ROOT / 'examples/hello/hello_app.py'),
                                      '--serve', '--data-dir', str(data)],
                                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                     creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            try:
                runtime_path = data / 'runtime.json'
                deadline = time.monotonic() + 10
                while not runtime_path.is_file() and child.poll() is None and time.monotonic() < deadline:
                    time.sleep(0.05)
                self.assertTrue(runtime_path.is_file())
                runtime = json.loads(runtime_path.read_text(encoding='utf-8'))
                with urllib.request.urlopen(runtime['url'] + 'api/health', timeout=3) as response:
                    health = json.load(response)
                self.assertEqual(health['pid'], child.pid)
                self.assertEqual(health['app'], 'example-workbench')
                denied = urllib.request.Request(runtime['url'] + 'api/shutdown', data=b'{}',
                                                headers={'X-WB-Control': runtime['control'], 'Origin': 'http://evil.invalid'})
                with self.assertRaises(urllib.error.HTTPError) as error:
                    urllib.request.urlopen(denied, timeout=3)
                self.assertEqual(error.exception.code, 403)
                error.exception.close()
                self.assertIsNone(child.poll())
                allowed = urllib.request.Request(runtime['url'] + 'api/shutdown', data=b'{}',
                                                 headers={'X-WB-Control': runtime['control']})
                with urllib.request.urlopen(allowed, timeout=3) as response:
                    self.assertTrue(json.load(response)['ok'])
                child.wait(timeout=5)
                self.assertEqual(child.returncode, 0)
                self.assertFalse(runtime_path.exists())
                self.assertEqual(list(data.iterdir()), [])
            finally:
                if child.poll() is None:
                    child.terminate()
                    child.wait(timeout=5)
                child.stderr.close()


if __name__ == '__main__':
    unittest.main()
