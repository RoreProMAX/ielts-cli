#!/usr/bin/env python3
import tempfile
import datetime
from pathlib import Path
import unittest

from scheduler import StudyPlanner


class Clock:
    def __init__(self): self.value = 1000.0
    def __call__(self): return self.value
    def advance(self, seconds): self.value += seconds


class SchedulerTests(unittest.TestCase):
    def test_seed_due_note_intervals_and_restart(self):
        clock = Clock()
        with tempfile.TemporaryDirectory() as directory:
            planner = StudyPlanner(directory, clock)
            profiles = {"ielts": {"records": {"apple": {}, "brave": {}}}}
            self.assertEqual(planner.seed_from_library(profiles), 2)
            self.assertEqual(planner.seed_from_library(profiles), 0)
            planner.note("ielts", "apple", "recall", True, event_id="e1", now=1000)
            self.assertEqual(planner.stats(datetime.date.fromtimestamp(clock()).isoformat())["new_words"], 0)
            self.assertEqual(planner.due_cards(now=1000), [{"dictionary_id": "ielts", "word": "brave", "due": 1000.0, "stage": 0}])
            clock.advance(86400)
            planner.note("ielts", "apple", "recall", False, event_id="e2")
            self.assertEqual(planner.due_cards(now=clock()), [{"dictionary_id": "ielts", "word": "brave", "due": 1000.0, "stage": 0}])
            planner.close()
            reopened = StudyPlanner(directory, clock)
            self.assertEqual(reopened.get_intervals(), [1, 3, 7, 14, 30])
            self.assertEqual(reopened.mode_stats("ielts", "apple")["recall"], {"correct": 1, "wrong": 1, "unassisted_correct": 1})
            reopened.close()

    def test_choice_isolated_idempotence_and_stats(self):
        clock = Clock()
        with tempfile.TemporaryDirectory() as directory:
            planner = StudyPlanner(directory, clock)
            planner.note("d", "word", "en_to_zh", True, event_id="same")
            planner.set_intervals([1, 3, 7])
            planner.note("d", "word", "recall", True, event_id="advance")
            stage = planner.due_cards(now=1000)[0]["stage"] if planner.due_cards(now=1000) else 1
            self.assertEqual(stage, 1)
            planner.note("d", "word", "copy", True, event_id="copy-after")
            self.assertEqual(planner.due_cards(now=1000), [])
            planner.note("d", "word", "en_to_zh", True, event_id="same")
            self.assertEqual(planner.mode_stats("d", "word")["en_to_zh"]["correct"], 1)
            self.assertEqual(planner.due_cards(now=1000), [])
            planner.note("d", "word", "copy", True, context="repair")
            self.assertEqual(planner.mode_stats("d", "word")["copy"]["correct"], 2)
            self.assertEqual(planner.stats(datetime.date.fromtimestamp(clock()).isoformat())["attempts"], 4)
            planner.close()

    def test_validation_and_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            planner = StudyPlanner(directory)
            with self.assertRaises(ValueError): planner.set_intervals([1, True])
            with self.assertRaises(ValueError): planner.set_intervals([36501])
            with self.assertRaises(ValueError): planner.note("d", "w", "copy", 1)
            with self.assertRaises(ValueError): planner.note("d", "w", "copy", True, now=float("inf"))
            planner.close()
            self.assertEqual(Path(directory, "learning.sqlite3").stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
