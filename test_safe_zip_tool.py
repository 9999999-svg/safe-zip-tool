"""Actual archive and output checks; --exe runs the same checks against a frozen EXE."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import threading
import zipfile

def checks(executable=None):
    command = [str(executable)] if executable else [sys.executable, str(Path(__file__).with_name('safe_zip_tool.py'))]
    def run(*args, code=0):
        result = subprocess.run(command + list(map(str, args)), capture_output=True, timeout=60)
        assert result.returncode == code, (args, result.returncode, result.stdout, result.stderr)
        return json.loads(result.stdout if code in {0, 1} else result.stderr)
    with tempfile.TemporaryDirectory(prefix='ZIP-中文-') as folder:
        root = Path(folder)
        archive = root / '正常.zip'
        expected = {'资料/中文.txt': '你好\r\n第二行'.encode('utf-8'), '空.txt': b'', 'nested/b.txt': b'example'}
        with zipfile.ZipFile(archive, 'w') as output:
            output.writestr('资料/', b'')
            for (name, data), method in zip(expected.items(), [8, 12, 14]):
                output.writestr(name, data, compress_type=method)
        initial = hashlib.sha256(archive.read_bytes()).digest()
        listing = run('list', archive)
        assert listing['extractable'] and listing['entries'] == 4
        destination = root / '结果'
        report = run('extract', archive, destination)
        assert report['files'] == 3 and report['bytes'] == sum(map(len, expected.values()))
        for name, content in expected.items():
            assert (destination / name).read_bytes() == content
        run('extract', archive, destination, code=2)
        assert (destination / '资料/中文.txt').read_bytes() == expected['资料/中文.txt']
        assert hashlib.sha256(archive.read_bytes()).digest() == initial
        bad_sets = [['../越界.txt'], ['/absolute.txt'], ['C:/drive.txt'], ['a\\b.txt'],
            ['file:stream'], ['CON.txt'], ['com¹.txt'], ['a./b'], ['a /b'], ['a//b'],
            ['a.txt', 'A.TXT'], ['file', 'file/child'], ['file/child', 'file'], ['dir/', 'DIR/']]
        for index, names in enumerate(bad_sets):
            path = root / f'bad-{index}.zip'
            with zipfile.ZipFile(path, 'w') as output:
                for name in names:
                    output.writestr(name, b'' if name.endswith('/') else b'blocked')
            # On Windows ZipInfo normalizes backslashes when writing. Put the
            # hostile name into both headers to test a real incoming archive.
            if any('\\' in name for name in names):
                raw = path.read_bytes()
                for name in names:
                    raw = raw.replace(name.replace('\\', '/').encode(), name.encode())
                path.write_bytes(raw)
            assert not run('list', path, code=1)['extractable']
            output_dir = root / f'refused-{index}'
            run('extract', path, output_dir, code=2)
            assert not output_dir.exists()
        link = root / 'link.zip'
        member = zipfile.ZipInfo('link')
        member.create_system = 3
        member.external_attr = (stat.S_IFLNK | 0o777) << 16
        with zipfile.ZipFile(link, 'w') as output:
            output.writestr(member, b'../elsewhere')
        assert not run('list', link, code=1)['extractable']
        run('extract', link, root / 'link-out', code=2)
        assert not (root / 'link-out').exists()
        balloon = root / '膨胀.zip'
        with zipfile.ZipFile(balloon, 'w', zipfile.ZIP_DEFLATED) as output:
            output.writestr('zeros', b'0' * (2 * 1024 * 1024))
        assert not run('list', balloon, code=1)['extractable']
        corrupt = root / 'CRC损坏.zip'
        with zipfile.ZipFile(corrupt, 'w') as output:
            output.writestr('broken.txt', b'UNIQUE-CONTENT-FOR-CRC')
        data = corrupt.read_bytes().replace(b'UNIQUE-CONTENT-FOR-CRC', b'UNIQUE-CONTENT-FOR-CRX', 1)
        corrupt.write_bytes(data)
        run('extract', corrupt, root / 'crc-out', code=2)
        invalid = root / '不是ZIP.zip'
        invalid.write_bytes(b'not a zip')
        run('list', invalid, code=2)
        if not executable:
            import safe_zip_tool as tool
            event = threading.Event()
            event.set()
            try:
                tool.extract(archive, root / 'cancelled', event)
            except InterruptedError:
                pass
            else:
                raise AssertionError('Cancellation ignored')
            assert not (root / 'cancelled').exists()
            linked_path = root / 'source-link.zip'
            try:
                linked_path.symlink_to(archive)
            except OSError:
                print('SKIP: OS does not permit symlink fixture')
            else:
                run('list', linked_path, code=2)
            old = tool.MAX_TOTAL
            tool.MAX_TOTAL = 1
            try:
                try:
                    tool.inspect(archive)
                except ValueError:
                    pass
                else:
                    raise AssertionError('Total limit ignored')
            finally:
                tool.MAX_TOTAL = old
        print('PASS: actual Unicode ZIP listing/extraction, content/CRC, path/collision/link/ratio refusals, no overwrite/source changes', flush=True)

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--exe', type=Path)
    checks(parser.parse_args().exe)
