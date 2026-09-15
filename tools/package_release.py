#!/usr/bin/env python3
"""Build a clean ZIP from tracked Git files, adding per-file integrity hashes."""

import argparse
import datetime
import hashlib
import json
from pathlib import Path
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def build(output):
    output = output.resolve()
    if output.exists():
        raise ValueError('Refusing to overwrite an existing release file')
    tracked = subprocess.check_output(['git', 'ls-files', '-z'], cwd=ROOT).decode('utf-8').split('\0')
    payloads = {}
    prohibited = {'user-data', '__pycache__', '.git', '.codex', '.venv', 'updates'}
    private_names = {'learning.sqlite3', 'progress.json', 'library.json', 'settings.json', 'routine.json', 'practice_settings.json', 'active_round.json', 'extra_round.json', 'update_settings.json'}
    for name in filter(None, tracked):
        relative = Path(name)
        item = ROOT / relative
        if relative.is_absolute() or '..' in relative.parts or set(relative.parts) & prohibited or relative.name in private_names:
            raise ValueError('Private or invalid path in tracked files: ' + name)
        if item.is_symlink() or not item.is_file():
            raise ValueError('Expected a regular tracked file: ' + name)
        payloads[name] = item.read_bytes()
    if not payloads:
        raise ValueError('There are no tracked files to package')
    version = (ROOT / 'VERSION').read_text(encoding='utf-8').strip()
    if (ROOT / 'apps' / ('v' + version) / 'VERSION').read_text(encoding='utf-8').strip() != version:
        raise ValueError('Root VERSION and default application VERSION differ')
    manifest = {
        'bundle_version': version + '-public.1',
        'created_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'default_app': version,
        'personal_learning_data_included': False,
        'files': {name: hashlib.sha256(data).hexdigest() for name, data in sorted(payloads.items())},
    }
    payloads['BUNDLE_MANIFEST.json'] = (json.dumps(manifest, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
    payloads['SHA256SUMS'] = ''.join(hashlib.sha256(data).hexdigest() + '  ' + name + '\n' for name, data in sorted(payloads.items())).encode('utf-8')
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, data in sorted(payloads.items()):
            info = zipfile.ZipInfo(name, datetime.datetime.now().timetuple()[:6])
            info.compress_type = zipfile.ZIP_DEFLATED
            mode = 0o755 if name.endswith(('.sh', '.command')) or name.endswith('/ielts.py') else 0o644
            info.external_attr = (0o100000 | mode) << 16
            archive.writestr(info, data)
    with zipfile.ZipFile(output) as archive:
        if archive.testzip() is not None:
            raise ValueError('ZIP CRC verification failed')
    print(json.dumps({'file': str(output), 'files': len(payloads), 'bytes': output.stat().st_size, 'sha256': hashlib.sha256(output.read_bytes()).hexdigest()}, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('output', type=Path)
    build(parser.parse_args().output)
