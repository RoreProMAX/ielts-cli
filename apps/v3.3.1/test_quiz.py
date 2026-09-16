#!/usr/bin/env python3
import json
from pathlib import Path
import tempfile
import unittest

from quiz import QuestionFactory, choose_mode, is_correct


ROOT = Path(__file__).resolve().parent
WORDS = json.loads((Path(__file__).resolve().parent / 'data/ielts.json').read_text(encoding="utf-8"))


class QuizTests(unittest.TestCase):
    def factory(self, words=WORDS, content=True):
        return QuestionFactory(words, ROOT / "content.json" if content else None)

    def test_choice_modes_are_four_options_and_score_by_number_or_text(self):
        factory = self.factory()
        for mode in ("en_to_zh", "zh_to_en"):
            question = factory.make("adapt", mode)
            self.assertEqual(question["mode"], mode)
            self.assertEqual(len(question["options"]), 4)
            self.assertEqual(len(question["correct_indices"]), 1)
            index = question["correct_indices"][0]
            self.assertTrue(is_correct(question, index + 1))
            self.assertTrue(is_correct(question, str(index + 1)))
            self.assertTrue(is_correct(question, "  " + question["options"][index].upper() + "  "))
            self.assertFalse(is_correct(question, 0))

    def test_multimeaning_and_duplicate_options_do_not_make_multianswer(self):
        words = [
            {"name": "target", "trans": ["共同释义", "另一义项", "共同释义"]},
            {"name": "TARGET", "trans": ["重复的大小写词条"]},
            {"name": "same", "trans": ["共同释义。"]},
            {"name": "other", "trans": ["其他"]},
            {"name": "third", "trans": ["第三"]},
            {"name": "fourth", "trans": ["第四"]},
        ]
        factory = self.factory(words, content=False)
        self.assertEqual(len(factory.words), 5)
        question = factory.make("target", "en_to_zh")
        self.assertEqual(len(question["options"]), 4)
        self.assertEqual(question["options"].count("共同释义"), 1)
        reverse = factory.make("target", "zh_to_en")
        self.assertEqual(len(reverse["options"]), 4)
        self.assertNotIn("same", reverse["options"])

    def test_short_pool_explicitly_falls_back_to_recall(self):
        words = [{"name": "one", "trans": ["一"]}, {"name": "two", "trans": ["二"]}, {"name": "three", "trans": ["三"]}]
        question = self.factory(words, content=False).make("one", "en_to_zh")
        self.assertEqual(question["mode"], "recall")
        self.assertIn("干扰项", question["reason"])

    def test_missing_meaning_does_not_leak_word_in_recall_fallback(self):
        words = [{"name": "opaque", "trans": []}, {"name": "one", "trans": ["一"]}, {"name": "two", "trans": ["二"]}, {"name": "three", "trans": ["三"]}]
        question = QuestionFactory(words).make("opaque", "en_to_zh")
        self.assertEqual(question["mode"], "copy")
        self.assertNotIn("opaque", question["prompt"].casefold())
        self.assertIn("跟打", question["reason"])
        self.assertEqual(QuestionFactory(words).make("opaque", "recall")["mode"], "copy")

    def test_choice_draws_distractors_from_the_whole_candidate_pool(self):
        class ReverseRng:
            def shuffle(self, values):
                values.reverse()

        words = [{"name": "target", "trans": ["目标"]}]
        words.extend({"name": "word%d" % i, "trans": ["义项%d" % i]} for i in range(1, 8))
        question = QuestionFactory(words, rng=ReverseRng()).make("target", "en_to_zh")
        self.assertEqual(set(question["options"]) - {"目标"}, {"义项7", "义项6", "义项5"})

    def test_each_mode_and_invalid_response(self):
        factory = self.factory()
        for mode in ("recall", "listening", "cloze", "collocation"):
            question = factory.make("adapt", mode)
            self.assertEqual(question["mode"], mode)
            self.assertTrue(is_correct(question, question["answer"]))
            self.assertFalse(is_correct(question, "wrong answer"))
        self.assertEqual(factory.make("adapt", "cloze")["prompt"].count("____"), 1)
        self.assertEqual(factory.make("adapt", "collocation")["prompt"].count("____"), 1)
        self.assertEqual(factory.make("challenge", "collocation")["answer"], "challenge")
        self.assertEqual(factory.make("finance", "collocation")["answer"], "finance")

    def test_content_has_matching_words_and_unique_blanks(self):
        payload = json.loads((ROOT / "content.json").read_text(encoding="utf-8"))
        self.assertEqual(len(payload["items"]), 124)
        from library import Catalog
        catalog = Catalog(ROOT / 'data/catalog.json')
        words = [word for entry in catalog.entries for word in catalog.words_for(entry['id'])]
        names = {word["name"].casefold() for word in words}
        factory = QuestionFactory(words, ROOT / 'content.json')
        content_names = {item['word'].casefold() for item in payload['items']}
        self.assertTrue({word['name'].casefold() for word in catalog.words_for('ielts')[:40]} <= content_names)
        for item in payload["items"]:
            self.assertIn(item["word"].casefold(), names)
            self.assertEqual(item["sentence"].count("____"), 1)
            self.assertEqual(item["collocation_prompt"].count("____"), 1)
            self.assertEqual(item["collocation"].replace(item["word"], "____", 1), item["collocation_prompt"])

            question = factory.make(item["word"], "collocation")
            self.assertEqual(question["answer"].casefold(), item["word"].casefold())

    def test_missing_content_falls_back_explicitly(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "content.json"
            path.write_text(json.dumps({"version": 1, "items": []}), encoding="utf-8")
            question = QuestionFactory(WORDS, path).make("adapt", "cloze")
            self.assertEqual(question["mode"], "recall")
            self.assertIn("缺少", question["reason"])

    def test_scheduler_order_is_independent_of_question_answers(self):
        empty = {}
        en_familiar = {"en_to_zh": {"correct": 3, "attempts": 3}}
        zh_familiar = {**en_familiar, "zh_to_en": {"correct": 3, "attempts": 3}}
        recall_familiar = {**zh_familiar, "recall": {"correct": 3, "attempts": 3}}
        self.assertEqual(choose_mode(empty), "en_to_zh")
        self.assertEqual(choose_mode(en_familiar), "zh_to_en")
        self.assertEqual(choose_mode(zh_familiar, has_content=True), "recall")
        self.assertEqual(choose_mode(recall_familiar), "recall")
        self.assertEqual(choose_mode(recall_familiar, allow_listening=True), "listening")

    def test_scheduler_uses_correct_plus_wrong_and_rotates_content_modes(self):
        stats = {
            "en_to_zh": {"correct": 3, "wrong": 0},
            "zh_to_en": {"correct": 3, "wrong": 0},
            "recall": {"correct": 3, "wrong": 0},
        }
        self.assertIn(choose_mode(stats, has_content=True), {"listening", "cloze", "collocation"})
        stats["listening"] = {"correct": 3, "wrong": 0}
        self.assertIn(choose_mode(stats, has_content=True), {"cloze", "collocation"})


if __name__ == "__main__":
    unittest.main()
