#!/usr/bin/env python3
"""V1 版本目录 seed 的隔离与只复制一次测试。"""

import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from study import StateStore
from versioning import seed_version_profile


class VersioningTests(unittest.TestCase):
    def test_seed_copies_sources_without_overwriting_or_crossing_versions(self):
        with tempfile.TemporaryDirectory() as base_name, tempfile.TemporaryDirectory() as target_name:
            base = Path(base_name)
            source = base
            library = {"version": 2, "active_dictionary": "ielts", "dictionaries": {"ielts": {"version": 1}}}
            (source / "library.json").write_text(json.dumps(library), encoding="utf-8")
            (source / "settings.json").write_text('{"version":1,"auto_pronounce":false}', encoding="utf-8")
            (source / "routine.json").write_text('{"version":1,"new_goal":20}', encoding="utf-8")
            database = sqlite3.connect(source / "learning.sqlite3")
            database.execute("CREATE TABLE cards (word TEXT PRIMARY KEY, due REAL)")
            database.execute("INSERT INTO cards VALUES ('cancel', 1)")
            database.commit()
            database.close()
            before = {path.name: path.read_bytes() for path in source.iterdir()}

            store = StateStore(target_name)
            with store:
                seed_version_profile(store, base=base)
                target = Path(target_name)
                self.assertEqual(json.loads((target / "library.json").read_text(encoding="utf-8")), json.loads(before["library.json"]))
                self.assertEqual((target / "settings.json").read_bytes(), before["settings.json"])
                copied = sqlite3.connect(target / "learning.sqlite3")
                self.assertEqual(copied.execute("SELECT * FROM cards").fetchall(), [("cancel", 1.0)])
                copied.close()
                (target / "library.json").write_text('{"learned":true}', encoding="utf-8")
                seed_version_profile(store, base=base)
                self.assertEqual((target / "library.json").read_text(encoding="utf-8"), '{"learned":true}')
            self.assertEqual({path.name: path.read_bytes() for path in source.iterdir()}, before)

    def test_versioned_default_paths_are_isolated(self):
        with tempfile.TemporaryDirectory() as home:
            with patch.dict(os.environ, {"XDG_DATA_HOME": home}):
                from versioning import default_data_dir, VERSION
                first = default_data_dir()
                second = Path(home) / "ielts-cli" / "versions" / "0.0.0"
                self.assertNotEqual(first, second)
                self.assertEqual(first.name, VERSION)


if __name__ == "__main__":
    unittest.main()
