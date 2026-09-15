#!/usr/bin/env python3
"""词库目录与分词库进度的隔离、迁移和完整性集成测试。"""

import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from library import Catalog, StudyLibrary
from study import StateStore, fresh_state, load_words


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"


class LibraryTests(unittest.TestCase):
    def test_legacy_progress_is_copied_without_changing_old_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            store = StateStore(directory)
            with store:
                legacy = fresh_state()
                legacy["current"] = "cancel"
                store.save(legacy)
                old_bytes = store.path.read_bytes()
                library = StudyLibrary(Catalog(DATA / "catalog.json"), store)
                library.save()
                self.assertEqual(store.path.read_bytes(), old_bytes)
                self.assertEqual(library.active_id, "ielts")
                self.assertEqual(library.session.state["current"], "cancel")
                self.assertTrue(library.path.is_file())

    def test_each_dictionary_has_independent_state_even_for_same_word_name(self):
        with tempfile.TemporaryDirectory() as directory:
            with StateStore(directory) as store:
                library = StudyLibrary(Catalog(DATA / "catalog.json"), store)
                library.session.set_draft("ielts-prefix")
                library.session.state["records"]["cancel"] = {"correct": 1, "mistakes": 0, "recall": 0, "needsReview": False}
                library.save()

                library.switch_dictionary("cet4")
                self.assertEqual(library.session.state["current"], "cancel")
                self.assertEqual(library.session.state["draft"], "")
                self.assertEqual(library.practiced_count("cet4"), 0)
                library.session.set_draft("cet4-prefix")
                library.session.state["records"]["cancel"] = {"correct": 2, "mistakes": 1, "recall": 0, "needsReview": True}
                library.save()

                library.switch_dictionary("ielts")
                self.assertEqual(library.session.state["draft"], "ielts-prefix")
                self.assertEqual(library.practiced_count("ielts"), 1)
                self.assertEqual(library.profiles["cet4"]["draft"], "cet4-prefix")
                self.assertEqual(library.practiced_count("cet4"), 1)
                self.assertNotEqual(library.profiles["ielts"]["records"]["cancel"], library.profiles["cet4"]["records"]["cancel"])

    def test_switch_and_reopen_restore_active_dictionary_and_prefix(self):
        with tempfile.TemporaryDirectory() as directory:
            catalog = Catalog(DATA / "catalog.json")
            with StateStore(directory) as store:
                library = StudyLibrary(catalog, store)
                library.switch_dictionary("cet6")
                library.session.set_draft("ca")
                library.save()

            with StateStore(directory) as store:
                reopened = StudyLibrary(catalog, store)
                self.assertEqual(reopened.active_id, "cet6")
                self.assertEqual(reopened.session.state["current"], "cancel")
                self.assertEqual(reopened.session.state["draft"], "ca")

    def _valid_library_payload(self, active="ielts"):
        state = fresh_state()
        state["current"] = "cancel"
        return {"version": 2, "active_dictionary": active, "chapter_size": 20, "dictionaries": {active: state}}

    def test_bad_library_and_unknown_active_leave_original_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            catalog = Catalog(DATA / "catalog.json")
            with StateStore(directory) as store:
                library = StudyLibrary(catalog, store)
                library.save()
                path = library.path

                path.write_text("{bad", encoding="utf-8")
                bad_bytes = path.read_bytes()
                with self.assertRaises((ValueError, json.JSONDecodeError)):
                    StudyLibrary(catalog, store)
                self.assertEqual(path.read_bytes(), bad_bytes)

                unknown = self._valid_library_payload("unknown")
                path.write_text(json.dumps(unknown), encoding="utf-8")
                unknown_bytes = path.read_bytes()
                with self.assertRaises(ValueError):
                    StudyLibrary(catalog, store)
                self.assertEqual(path.read_bytes(), unknown_bytes)

    def test_dictionary_hash_and_count_mismatch_are_rejected_without_library_write(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            word_path = base / "book.json"
            word_path.write_bytes((DATA / "ielts.json").read_bytes())
            state = fresh_state()
            state["current"] = "cancel"
            payload = {"version": 2, "active_dictionary": "book", "chapter_size": 20, "dictionaries": {"book": state}}

            for field, value in (("sha256", "0" * 64), ("word_count", 1)):
                with self.subTest(field=field), tempfile.TemporaryDirectory() as store_dir:
                    catalog_payload = {
                        "version": 1,
                        "default_dictionary": "book",
                        "chapter_size": 20,
                        "dictionaries": [{
                            "id": "book", "name": "Book", "description": "test",
                            "path": "book.json", "word_count": 3575,
                            "language": "en", "source_url": "test", "sha256": hashlib.sha256(word_path.read_bytes()).hexdigest()
                        }]
                    }
                    catalog_payload["dictionaries"][0][field] = value
                    catalog_path = base / (field + ".json")
                    catalog_path.write_text(json.dumps(catalog_payload), encoding="utf-8")
                    catalog = Catalog(catalog_path)
                    with StateStore(store_dir) as store:
                        library_path = store.path.parent / "library.json"
                        library_path.write_text(json.dumps(payload), encoding="utf-8")
                        before = library_path.read_bytes()
                        with self.assertRaises(ValueError):
                            StudyLibrary(catalog, store)
                        self.assertEqual(library_path.read_bytes(), before)

    def test_switch_save_failure_does_not_change_active_memory(self):
        with tempfile.TemporaryDirectory() as directory:
            with StateStore(directory) as store:
                library = StudyLibrary(Catalog(DATA / "catalog.json"), store)
                before_active = library.active_id
                before_session = library.session
                before_profiles = copy.deepcopy(library.profiles)

                def fail_write(*_args):
                    raise OSError("simulated write failure")

                library._write = fail_write
                with self.assertRaises(OSError):
                    library.switch_dictionary("cet4")
                self.assertEqual(library.active_id, before_active)
                self.assertIs(library.session, before_session)
                self.assertEqual(library.profiles, before_profiles)

    def test_real_catalog_contains_only_five_english_books_with_load_words_counts(self):
        catalog = Catalog(DATA / "catalog.json")
        self.assertEqual(set(catalog.by_id), {"ielts", "cet4", "cet6", "ielts-listening", "ielts-expanded"})
        self.assertEqual(catalog.chapter_size, 20)
        for identifier, entry in catalog.by_id.items():
            self.assertEqual(entry["language"], "en")
            self.assertEqual(len(catalog.words_for(identifier)), entry["word_count"])
            self.assertEqual(len(load_words(catalog._paths[identifier])), entry["word_count"])


if __name__ == "__main__":
    unittest.main()
