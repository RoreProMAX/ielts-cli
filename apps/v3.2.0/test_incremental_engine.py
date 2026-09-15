import json
import tempfile
import unittest
from pathlib import Path

from practice import PracticeRound
from routine import Routine
from scheduler import StudyPlanner


class Catalog:
    by_id = {'d': {}}
    words = [{'name': 'alpha', 'trans': ['阿尔法']}]

    def words_for(self, identifier):
        return self.words


def provider(dictionary_id, word, mode):
    if mode in ('cloze', 'collocation'):
        return {'mode': mode, 'word': word, 'answer': word, 'prompt': '',
                'options': [], 'correct_indices': [], 'skip_example': True}
    return {'mode': mode, 'word': word, 'answer': word, 'prompt': word,
            'options': [], 'correct_indices': []}


def content_provider(dictionary_id, word, mode):
    result = provider(dictionary_id, word, mode)
    result.pop('skip_example', None)
    return result


class IncrementalEngineTests(unittest.TestCase):
    def test_routine_switches_are_persisted_and_strict(self):
        with tempfile.TemporaryDirectory() as directory:
            routine = Routine(directory)
            self.assertTrue(routine.config['extra_after_daily'])
            self.assertFalse(routine.config['examples_during_learning'])
            routine.update(extra_after_daily=False, examples_during_learning=True)
            self.assertTrue(Routine(directory).config['examples_during_learning'])
            with self.assertRaises(ValueError):
                routine.update(extra_after_daily=1)

    def test_extra_keeps_existing_schedule_but_wrong_resets(self):
        with tempfile.TemporaryDirectory() as directory:
            planner = StudyPlanner(directory, lambda: 1000.0)
            planner.seed_from_library({'d': {'records': {'alpha': {}}}})
            before = planner.note('d', 'alpha', 'recall', True, event_id='seed-event')
            after = planner.note('d', 'alpha', 'recall', True, event_id='seed-event-2')
            result = planner.note('d', 'alpha', 'recall', True, context='extra', event_id='extra-ok')
            self.assertEqual((result['due'], result['stage']), (after['due'], after['stage']))
            planner.note('d', 'alpha', 'recall', False, context='extra', event_id='extra-bad')
            card = planner.due_cards('d', now=1000.0 + 600)[0]
            self.assertEqual(card['stage'], 0)
            self.assertEqual(planner.stats()['extra_attempts'], 2)

    def test_examples_add_two_stages_and_skip_without_recall(self):
        catalog = Catalog()
        with tempfile.TemporaryDirectory() as directory:
            planner = StudyPlanner(directory, lambda: 1000.0)
            planner.seed_from_library({'d': {'records': {'alpha': {}}}})
            round_ = PracticeRound(catalog, planner,
                                   [{'dictionary_id': 'd', 'word': 'alpha'}],
                                   mode='mixed', context='learn', examples=True,
                                   provider=content_provider)
            modes = []
            while not round_.finished:
                modes.append(round_.question['mode'])
                round_.set_draft('alpha')
                self.assertEqual(round_.submit(), 'correct')
                round_.submit()
            self.assertEqual(modes, ['copy', 'en_to_zh', 'zh_to_en', 'recall', 'cloze', 'collocation'])
            self.assertEqual(round_.snapshot()['version'], 3)

            skipped = PracticeRound(catalog, planner,
                                    [{'dictionary_id': 'd', 'word': 'alpha'}],
                                    mode='mixed', context='learn', examples=True,
                                    provider=provider)
            seen = []
            while not skipped.finished:
                seen.append(skipped.question['mode'])
                skipped.set_draft('alpha')
                skipped.submit()
                skipped.submit()
            self.assertEqual(seen, ['copy', 'en_to_zh', 'zh_to_en', 'recall'])


if __name__ == '__main__':
    unittest.main()
