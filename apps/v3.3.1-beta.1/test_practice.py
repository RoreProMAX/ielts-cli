#!/usr/bin/env python3
import copy
import tempfile
import unittest

from practice import PracticeRound
from scheduler import StudyPlanner


class Clock:
    def __init__(self, value=1000.0):
        self.value = value

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


class CatalogFixture:
    def __init__(self, count=6):
        self.by_id = {"ielts": {"id": "ielts"}}
        self.words = [{"name": "word-%d" % index, "trans": ["meaning-%d" % index]} for index in range(count)]

    def words_for(self, identifier):
        if identifier not in self.by_id:
            raise ValueError(identifier)
        return self.words


def provider(dictionary_id, word, mode):
    return {"mode": "recall", "word": word, "answer": word, "prompt": "meaning for " + word, "options": [], "correct_indices": []}


class PracticeTests(unittest.TestCase):
    def make_round(self, count=6):
        clock = Clock()
        catalog = CatalogFixture(count)
        temporary = tempfile.TemporaryDirectory()
        if not hasattr(self, "_tempdirs"):
            self._tempdirs = []
        self._tempdirs.append(temporary)
        planner = StudyPlanner(temporary.name, clock)
        planner.seed_from_library({"ielts": {"records": {word["name"]: {} for word in catalog.words}}})
        cards = planner.due_cards("ielts", now=clock())
        return clock, catalog, planner, PracticeRound(catalog, planner, cards, provider=provider)

    def tearDown(self):
        for planner in getattr(self, "_planners", []):
            planner.close()
        for temporary in getattr(self, "_tempdirs", []):
            temporary.cleanup()

    def register(self, planner):
        if not hasattr(self, "_planners"):
            self._planners = []
        self._planners.append(planner)

    def test_due_queue_correct_and_skip_preserves_due(self):
        clock, catalog, planner, round_ = self.make_round(3)
        self.register(planner)
        original_due = planner.due_cards("ielts", now=clock())[0]["due"]
        round_.set_draft("word-0")
        self.assertEqual(round_.submit(), "correct")
        self.assertEqual(round_.correct, 1)
        self.assertEqual(round_.submit(), "next")
        due_before = planner.db.execute("SELECT due FROM cards WHERE word='word-1'").fetchone()[0]
        round_.skip()
        due_after = planner.db.execute("SELECT due FROM cards WHERE word='word-1'").fetchone()[0]
        self.assertEqual(due_before, due_after)
        self.assertEqual(original_due, 1000.0)

    def test_wrong_repair_then_retry_after_three_questions(self):
        clock, catalog, planner, round_ = self.make_round(6)
        self.register(planner)
        round_.set_draft("wrong")
        self.assertEqual(round_.submit(), "wrong")
        self.assertEqual(round_.phase, "repair")
        round_.set_draft("word-0")
        self.assertEqual(round_.submit(), "repaired")
        self.assertEqual(round_.phase, "done")
        self.assertEqual(round_.submit(), "next")
        for expected in ("word-1", "word-2", "word-3"):
            self.assertEqual(round_.word["name"], expected)
            round_.set_draft(expected)
            self.assertEqual(round_.submit(), "correct")
            round_.submit()
        self.assertEqual(round_.word["name"], "word-0")
        self.assertEqual(round_.current["retry"], 1)

    def test_last_card_repair_is_deferred_and_retry_cap_is_two(self):
        clock, catalog, planner, round_ = self.make_round(1)
        self.register(planner)
        round_.set_draft("wrong")
        round_.submit()
        round_.set_draft("word-0")
        self.assertEqual(round_.submit(), "repaired")
        self.assertEqual(round_.submit(), "next")
        self.assertTrue(round_.finished)
        clock.advance(600)
        self.assertGreaterEqual(planner.due_cards("ielts", now=clock())[0]["due"], 1600)
        round_.queue = [{"dictionary_id": "ielts", "word": "word-0", "retry": 2}]
        round_.current = {"dictionary_id": "ielts", "word": "word-1", "retry": 2}
        round_.retry_queued = False
        round_._queue_retry()
        self.assertEqual(round_.queue, [{"dictionary_id": "ielts", "word": "word-0", "retry": 2}])

    def test_reveal_and_audio_hint_are_assisted_not_independent_success(self):
        clock, catalog, planner, round_ = self.make_round(3)
        self.register(planner)
        round_.reveal()
        self.assertEqual(round_.answered, 1)
        self.assertEqual(round_.correct, 0)
        round_.set_draft("word-0")
        self.assertEqual(round_.submit(), "repaired")
        self.assertEqual(round_.correct, 0)
        round_.submit()
        round_.mark_audio_hint()
        round_.set_draft("word-1")
        self.assertEqual(round_.submit(), "assisted")
        self.assertEqual(round_.correct, 0)
        card = planner.db.execute("SELECT stage,due FROM cards WHERE word='word-1'").fetchone()
        self.assertEqual(card[0], 0)
        self.assertGreaterEqual(card[1], clock() + 599)

    def test_snapshot_restores_draft_and_inflight_event_is_not_graded_twice(self):
        clock, catalog, planner, round_ = self.make_round(3)
        self.register(planner)
        round_.set_draft("word-0")
        snapshot_before = round_.snapshot()
        self.assertEqual(round_.submit(), "correct")
        restored = PracticeRound.restore(catalog, planner, snapshot_before, provider=provider)
        self.assertEqual(restored.phase, "done")
        self.assertEqual(restored.draft, "")
        count = planner.db.execute("SELECT COUNT(*) FROM events WHERE event_id=?", (snapshot_before["event_id"] + "-grade",)).fetchone()[0]
        self.assertEqual(count, 1)
        self.assertEqual(planner.mode_stats("ielts", "word-0")["recall"]["correct"], 1)

    def test_repair_snapshot_restores_phase_and_draft(self):
        clock, catalog, planner, round_ = self.make_round(3)
        self.register(planner)
        round_.set_draft("bad")
        round_.submit()
        round_.set_draft("word-0")
        snapshot = round_.snapshot()
        restored = PracticeRound.restore(catalog, planner, snapshot, provider=provider)
        self.assertEqual(restored.phase, "repair")
        self.assertEqual(restored.draft, "word-0")
        self.assertEqual(restored.current, round_.current)


if __name__ == "__main__":
    unittest.main()
