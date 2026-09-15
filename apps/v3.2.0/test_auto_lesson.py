#!/usr/bin/env python3
import tempfile
import unittest

from practice import PracticeRound
from quiz import choose_mode
from scheduler import StudyPlanner


class Catalog:
    by_id = {"d": {"id": "d"}}

    def __init__(self):
        self.words = [{"name": "w%d" % i, "trans": ["m%d" % i]} for i in range(5)]

    def words_for(self, identifier):
        return self.words


def provider(dictionary_id, word, mode):
    if mode == "copy":
        return {"mode": mode, "word": word, "answer": word, "prompt": word,
                "options": [], "correct_indices": []}
    if mode == "en_to_zh":
        answer, prompt = "m" + word[1:], word
    elif mode == "zh_to_en":
        answer, prompt = word, "m" + word[1:]
    else:
        answer, prompt = word, "m" + word[1:]
    return {"mode": mode, "word": word, "answer": answer, "prompt": prompt,
            "options": [], "correct_indices": []}


class AutoLessonTests(unittest.TestCase):
    def test_mixed_learning_inserts_next_stages_and_exposes_both_choices(self):
        catalog = Catalog()
        with tempfile.TemporaryDirectory() as directory:
            planner = StudyPlanner(directory, lambda: 1000.0)
            planner.seed_from_library({"d": {"records": {w["name"]: {} for w in catalog.words}}})
            round_ = PracticeRound(catalog, planner,
                                   [{"dictionary_id": "d", "word": w["name"]} for w in catalog.words],
                                   mode="mixed", context="learn", provider=provider)
            seen = []
            while not round_.finished:
                seen.append(round_.question["mode"])
                round_.set_draft(round_.question["answer"])
                self.assertEqual(round_.submit(), "correct")
                round_.submit()
            self.assertIn("en_to_zh", seen)
            self.assertIn("zh_to_en", seen)
            self.assertLessEqual(max(seen.index("recall"), 0), len(seen) - 1)
            self.assertEqual(len([m for m in seen if m == "copy"]), 5)

    def test_restore_graded_snapshot_advances_once_without_duplicate_event(self):
        catalog = Catalog()
        with tempfile.TemporaryDirectory() as directory:
            planner = StudyPlanner(directory, lambda: 1000.0)
            planner.seed_from_library({"d": {"records": {w["name"]: {} for w in catalog.words}}})
            round_ = PracticeRound(catalog, planner,
                                   [{"dictionary_id": "d", "word": w["name"]} for w in catalog.words],
                                   mode="mixed", context="learn", provider=provider)
            snapshot = round_.snapshot()
            round_.set_draft("w0")
            self.assertEqual(round_.submit(), "correct")
            restored = PracticeRound.restore(catalog, planner, snapshot, provider=provider)
            self.assertEqual(restored.phase, "done")
            self.assertEqual(sum(r.get("lesson_step") == 1 for r in restored.queue), 1)
            self.assertEqual(planner.db.execute("SELECT COUNT(*) FROM events WHERE event_id=?",
                                                (snapshot["event_id"] + "-grade",)).fetchone()[0], 1)
            restored_again = PracticeRound.restore(catalog, planner, snapshot, provider=provider)
            self.assertEqual(sum(r.get("lesson_step") == 1 for r in restored_again.queue), 1)

    def test_v1_mixed_snapshot_maps_current_question_stage(self):
        catalog = Catalog()
        with tempfile.TemporaryDirectory() as directory:
            planner = StudyPlanner(directory, lambda: 1000.0)
            planner.seed_from_library({"d": {"records": {w["name"]: {} for w in catalog.words}}})
            event_id = "a" * 32
            planner.note("d", "w0", "en_to_zh", True, context="learn", event_id=event_id + "-grade")
            value = PracticeRound(catalog, planner, [], mode="mixed", context="learn", provider=provider).snapshot()
            value.update(version=1, active=True, mode="mixed", context="learn", phase="answer",
                         event_id=event_id, draft="m0")
            value["current"] = {"dictionary_id": "d", "word": "w0", "retry": 0}
            value["queue"] = [{"dictionary_id": "d", "word": "w1", "retry": 1}]
            value["question"] = provider("d", "w0", "en_to_zh")
            restored = PracticeRound.restore(catalog, planner, value, provider=provider)
            self.assertEqual(restored.current["lesson_step"], 1)
            self.assertEqual(restored.draft, "")
            self.assertTrue(any(r["word"] == "w0" and r["lesson_step"] == 2 for r in restored.queue))
            self.assertEqual(restored.queue[0]["lesson_step"], 1)

    def test_choose_mode_advances_after_one_and_revisits_weak_mode(self):
        self.assertEqual(choose_mode({}), "en_to_zh")
        stats = {"en_to_zh": {"correct": 1, "wrong": 0}}
        self.assertEqual(choose_mode(stats), "zh_to_en")
        stats["zh_to_en"] = {"correct": 1, "wrong": 1}
        self.assertEqual(choose_mode(stats), "zh_to_en")
        complete = {m: {"correct": 1, "wrong": 0} for m in ("en_to_zh", "zh_to_en", "recall")}
        self.assertEqual(choose_mode(complete), "recall")
        self.assertEqual(choose_mode(complete, allow_listening=True), "listening")


if __name__ == "__main__":
    unittest.main()
