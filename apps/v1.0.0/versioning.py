"""按发布版本隔离学习数据，首次运行复制旧数据而不改动来源。"""

import datetime
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile

ROOT = Path(__file__).resolve().parent
VERSION = (ROOT / 'VERSION').read_text().strip()
MAJOR = int(VERSION.split('.')[0])


def data_base():
    return Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local/share')) / 'ielts-cli'


def default_data_dir():
    return data_base() / 'versions' / VERSION


def seed_version_profile(store, disabled=False, base=None):
    destination = store.path.parent
    marker = destination / 'version_origin.json'
    if disabled or (destination / 'library.json').exists() or marker.exists():
        return
    base = Path(base) if base is not None else data_base()
    candidates = [base / 'versions' / f'{major}.0.0' for major in range(MAJOR - 1, 0, -1)] + [base]
    source = next((p for p in candidates if p.resolve() != destination.resolve() and (p / 'library.json').is_file()), None)
    if source is None:
        return
    for name in ('library.json', 'progress.json', 'settings.json', 'migration.json', 'routine.json', 'practice_settings.json', 'active_round.json'):
        old = source / name
        target = destination / name
        if old.is_file() and not target.exists():
            value = json.loads(old.read_text(encoding='utf-8'))
            store._atomic_write(target, value)
    old_db = source / 'learning.sqlite3'
    new_db = destination / 'learning.sqlite3'
    if old_db.is_file() and not new_db.exists():
        fd, name = tempfile.mkstemp(prefix='.learning-import-', dir=str(destination))
        os.close(fd)
        try:
            source_connection = sqlite3.connect(old_db.as_uri() + '?mode=ro', uri=True)
            target_connection = sqlite3.connect(name)
            try:
                source_connection.backup(target_connection)
            finally:
                source_connection.close()
                target_connection.close()
            os.chmod(name, 0o600)
            os.replace(name, new_db)
        finally:
            if os.path.exists(name):
                os.unlink(name)
    store._atomic_write(marker, {'version': VERSION, 'source': str(source), 'copied_at': datetime.datetime.now(datetime.timezone.utc).isoformat()})
