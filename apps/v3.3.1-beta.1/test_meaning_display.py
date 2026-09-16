#!/usr/bin/env python3
"""覆盖多义词展示与中文到英文题目的释义冲突处理。"""

import unittest

from quiz import QuestionFactory, display_meanings


class MeaningDisplayTests(unittest.TestCase):
    def test_display_keeps_pos_punctuation_and_all_trans_entries(self):
        word = {
            "name": "record",
            "trans": [
                "  n. 记录， 记载； v. 记录  ",
                "n. 记录，记载； v. 记录",
                "adj. 可靠的；可信赖的",
                "prep. 关于",
            ],
        }

        displayed = display_meanings(word)

        self.assertEqual(
            displayed,
            "n. 记录， 记载； v. 记录；adj. 可靠的；可信赖的；prep. 关于",
        )
        self.assertIn("n.", displayed)
        self.assertIn("v.", displayed)
        self.assertIn("记录， 记载； v. 记录", displayed)
        self.assertIn("adj. 可靠的；可信赖的", displayed)
        self.assertIn("prep. 关于", displayed)
        self.assertEqual(displayed.count("记录，"), 1)

    def test_display_does_not_truncate_multiple_trans_entries(self):
        entries = ["第一义项，补充说明；另一部分", "第二义项", "第三义项"]

        displayed = display_meanings({"name": "long", "trans": entries})

        for entry in entries:
            self.assertIn(entry, displayed)

    def test_zh_to_en_excludes_distractors_overlapping_second_or_last_meaning(self):
        words = [
            {"name": "target", "trans": ["n. 目标；目的", "v. 旨在"]},
            {"name": "second_overlap", "trans": ["其他；目的"]},
            {"name": "last_overlap", "trans": ["别的；旨在"]},
            {"name": "clean_one", "trans": ["清楚一"]},
            {"name": "clean_two", "trans": ["清楚二"]},
            {"name": "clean_three", "trans": ["清楚三"]},
        ]

        question = QuestionFactory(words).make("target", "zh_to_en")

        self.assertEqual(len(question["options"]), 4)
        self.assertEqual(len(question["correct_indices"]), 1)
        self.assertIn("target", question["options"])
        self.assertNotIn("second_overlap", question["options"])
        self.assertNotIn("last_overlap", question["options"])
        self.assertEqual(
            set(question["options"]),
            {"target", "clean_one", "clean_two", "clean_three"},
        )

    def test_short_zh_to_en_pool_fallback_shows_all_meanings(self):
        words = [
            {"name": "target", "trans": ["n. 目标；目的", "v. 旨在"]},
            {"name": "second_overlap", "trans": ["其他；目的"]},
            {"name": "last_overlap", "trans": ["别的；旨在"]},
        ]

        question = QuestionFactory(words).make("target", "zh_to_en")

        self.assertEqual(question["mode"], "recall")
        self.assertEqual(question["prompt"], "n. 目标；目的；v. 旨在")
        self.assertIn("干扰项", question["reason"])

    def test_recall_uses_all_meanings_but_single_meaning_stays_unchanged(self):
        words = [
            {"name": "multi", "trans": ["n. 形式；外观", "v. 形成"]},
            {"name": "single", "trans": ["单一释义"]},
        ]
        factory = QuestionFactory(words)

        self.assertEqual(
            factory.make("multi", "recall")["prompt"],
            "n. 形式；外观；v. 形成",
        )
        self.assertEqual(factory.make("single", "recall")["prompt"], "单一释义")

    def test_transitive_pos_prefix_does_not_hide_a_shared_meaning(self):
        words = [
            {'name': 'target', 'trans': ['vt. 引导；vi. 指导']},
            {'name': 'same', 'trans': ['n. 指导']},
            {'name': 'one', 'trans': ['第一']},
            {'name': 'two', 'trans': ['第二']},
            {'name': 'three', 'trans': ['第三']},
        ]
        question = QuestionFactory(words).make('target', 'zh_to_en')
        self.assertNotIn('same', question['options'])
        self.assertEqual(len(question['options']), 4)


if __name__ == "__main__":
    unittest.main()
