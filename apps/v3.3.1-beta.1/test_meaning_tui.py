#!/usr/bin/env python3
"""完整释义在小终端中可翻阅，旧题提示更新不改评分与选项。"""

from contextlib import contextmanager
import copy
import curses
import json
import os
from pathlib import Path
import tempfile
import unittest

import pexpect

from ielts import TerminalStudy, wrapped
from library import Catalog, StudyLibrary
from quiz import QuestionFactory, display_meanings
from scheduler import StudyPlanner
from study import StateStore

ROOT = Path(__file__).resolve().parent
CATALOG = Catalog(ROOT / 'data/catalog.json')


class Silent:
    def speak(self, word):
        raise AssertionError('Unexpected audio')

    def cancel(self):
        pass

    def close(self):
        pass


class Screen:
    def __init__(self, rows=6, cols=40):
        self.size = rows, cols
        self.items = {}

    def getmaxyx(self):
        return self.size

    def addstr(self, row, col, text, *args):
        self.items[row, col] = text

    def move(self, *args):
        pass

    def refresh(self):
        pass


@contextmanager
def application(directory):
    with StateStore(directory) as store:
        library = StudyLibrary(CATALOG, store)
        planner = StudyPlanner(directory)
        try:
            app = TerminalStudy(library.session, store, library=library, planner=planner, pronouncer=Silent())
            words = [word for word in CATALOG.words_for('ielts') if word['name'] in ('cast', 'cancel', 'adapt', 'issue')]
            app.factories['ielts'] = QuestionFactory(words)
            app.start_quiz('zh_to_en', cards=[{'dictionary_id': 'ielts', 'word': 'cast'}], resume=False)
            yield app
        finally:
            planner.close()


class MeaningTuiTests(unittest.TestCase):
    def test_six_rows_scroll_all_meanings_with_all_four_options_retained(self):
        with tempfile.TemporaryDirectory() as directory, application(directory) as app:
            screen = Screen()
            original = copy.deepcopy(app.round.snapshot())
            lines = wrapped(app.round.question['prompt'], 39)
            self.assertGreater(len(lines), 1)
            visible = []
            for index in range(len(lines)):
                screen.items.clear()
                app.draw_round(screen)
                visible.append(screen.items[1, 0])
                options = [value for (row, col), value in screen.items.items() if row in (2, 3)]
                for number in range(1, 5):
                    self.assertTrue(any(value.startswith(str(number) + '. ') for value in options))
                if index + 1 < len(lines):
                    app.handle_round(curses.KEY_DOWN)
            self.assertEqual(visible, lines)
            self.assertIn('特征', ''.join(visible))
            self.assertEqual(app.round.snapshot(), original)
            app.handle_round(curses.KEY_PPAGE)
            self.assertLess(app.prompt_offset, len(lines) - 1)

    def test_restored_old_question_refreshes_only_chinese_prompt(self):
        with tempfile.TemporaryDirectory() as directory, application(directory) as app:
            old = app.round.snapshot()
            old['question']['prompt'] = '投射'
            old['draft'] = '2'
            app.store._atomic_write(app.learn_round_path, old)
            resumed = app.load_round('learn')
            self.assertEqual(resumed.question['prompt'], display_meanings(resumed.word))
            self.assertIn('石膏绷带', resumed.question['prompt'])
            self.assertEqual(resumed.event_id, old['event_id'])
            self.assertEqual(resumed.draft, '2')
            for key in ('options', 'correct_indices', 'answer'):
                self.assertEqual(resumed.question[key], old['question'][key])
            self.assertEqual(app.planner.stats()['attempts'], 0)

    def test_real_six_row_terminal_reaches_last_definition(self):
        with tempfile.TemporaryDirectory() as directory:
            with StateStore(directory) as store:
                library = StudyLibrary(CATALOG, store)
                library.session.state['current'] = 'cast'
                library.save()
            env = dict(os.environ, TERM='xterm-256color', LANG='C.UTF-8', LC_ALL='C.UTF-8')
            child = pexpect.spawn(str(ROOT / 'ielts.py'), ['--data-dir', directory, '--no-import', '--learn', '--question-mode', 'zh_to_en'], env=env, encoding='utf-8', dimensions=(6, 40), timeout=5)
            try:
                child.expect('投射')
                child.expect('翻阅')
                for _ in range(8):
                    child.send('\x1bOB')
                child.expect('特征')
                current = json.loads((Path(directory) / 'learn_round-ielts.json').read_text())
                self.assertEqual(current['answered'], 0)
                self.assertEqual(current['draft'], '')
            finally:
                if child.isalive():
                    child.sendcontrol('q')
                    child.expect(pexpect.EOF)
                child.close(force=True)


if __name__ == '__main__':
    unittest.main()
