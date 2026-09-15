#!/usr/bin/env python3
"""在底部终端练习雅思拼写；使用 curses 重绘并保存本机学习进度。"""

import argparse
import copy
import curses
import json
import locale
import os
from pathlib import Path
import signal
from portable_compat import shutdown_signals
import sys
import time
import unicodedata
import uuid

from study import StateStore, StudySession, load_words
from audio_settings import AudioSettings
from pronunciation import Pronouncer
from library import Catalog, StudyLibrary
from scheduler import StudyPlanner
from practice import PracticeRound
from versioning import VERSION, MAJOR, default_data_dir, seed_version_profile


ROOT = Path(__file__).resolve().parent


def clean_text(value):
    """词库文本只作为字符显示，不让控制字符影响终端。"""
    return ''.join(' ' if unicodedata.category(char).startswith('C') else char for char in str(value))


def cell_width(char):
    if unicodedata.combining(char):
        return 0
    return 2 if unicodedata.east_asian_width(char) in ('W', 'F') else 1


def width(text):
    return sum(cell_width(char) for char in text)


def clipped(text, limit):
    result = ''
    used = 0
    for char in clean_text(text):
        size = cell_width(char)
        if used + size > limit:
            break
        result += char
        used += size
    return result


def wrapped(text, limit):
    lines, line, used = [], '', 0
    for char in clean_text(text):
        size = cell_width(char)
        if used + size > limit and line:
            lines.append(line)
            line, used = '', 0
        line += char
        used += size
    if line:
        lines.append(line)
    return lines or ['']


class TerminalStudy:
    def __init__(self, session, store, audio_settings=None, pronouncer=None, library=None, planner=None):
        self.session = session
        self.store = store
        self.cursor = len(session.state['draft'])
        self.dirty = False
        self.last_edit = 0.0
        self.running = True
        self.help_open = False
        self.notice = ''
        self.saved = True
        self.colors = {}
        self.library = library
        self.planner = planner
        self.round = None
        self.round_path = store.path.parent / 'active_round.json'
        self.force_learning = False
        self.force_review = False
        self.stats_cache = None
        self.stats_at = 0.0
        self.normal_audio_hint = False
        self.picker = None
        self.picker_rows = []
        self.picker_index = 0
        self.picker_number = ''
        self.picker_error = ''
        self.audio_settings = audio_settings or AudioSettings(store.path.parent)
        cache_home = Path(os.environ.get('XDG_CACHE_HOME', Path.home() / '.cache'))
        self.pronouncer = pronouncer or Pronouncer(cache_home / 'ielts-cli' / 'audio')

    def play_current(self):
        entry = self.round.word if self.round else self.session.current_word
        if entry is None:
            self.notice = '当前没有可播放的单词。'
            return
        if not self.round and self.session.state['mode'] == 'recall' and self.session.state['phase'] != 'success':
            self.normal_audio_hint = True
        self.pronouncer.speak(entry['name'])
        self.notice = '正在准备英式发音…'

    def auto_audio_allowed(self):
        if self.round:
            return self.round.allows_auto_audio
        state = self.session.state
        return state['mode'] == 'copy' or state['phase'] == 'success' or state['revealed']

    def toggle_auto_pronunciation(self):
        self.audio_settings.set_auto(not self.audio_settings.auto_pronounce)
        if self.audio_settings.auto_pronounce:
            self.notice = '自动发音已开启。'
            if self.auto_audio_allowed():
                self.play_current()
        else:
            self.pronouncer.cancel()
            self.notice = '自动发音已关闭；按 F5 可随时播放。'

    def stop(self, *_):
        self.running = False

    def persist(self):
        if not self.dirty:
            return
        if self.round:
            self.store._atomic_write(self.round_path, self.round.snapshot())
        elif self.library:
            self.library.save(self.session.state)
        else:
            self.store.save(self.session.state)
        self.dirty = False
        self.saved = True

    def planner_stats(self, refresh=False):
        if not self.planner:
            return {'due': 0, 'new_words': 0, 'review_words': 0, 'attempts': 0, 'correct': 0, 'by_mode': {}}
        if refresh or self.stats_cache is None or time.monotonic() - self.stats_at > 5:
            self.stats_cache = self.planner.stats()
            self.stats_at = time.monotonic()
        return self.stats_cache

    def load_round(self):
        if not self.round_path.exists():
            return None
        if self.round_path.stat().st_size > 2 * 1024 * 1024:
            raise ValueError('复习轮次文件过大')
        return PracticeRound.restore(self.library.catalog, self.planner, json.loads(self.round_path.read_text(encoding='utf-8')))

    def start_review(self):
        if not self.planner or not self.library:
            self.notice = '请重新启动程序以使用复习。'
            return
        self.persist()
        self.pronouncer.cancel()
        self.round = self.load_round()
        if not self.round:
            cards = self.planner.due_cards()
            self.round = PracticeRound(self.library.catalog, self.planner, cards)
        if self.round.finished:
            self.round = None
            self.notice = '目前没有到期词；可以继续学习新词。'
            return
        self.cursor = len(self.round.draft)
        self.dirty = True
        self.persist()
        self.notice = ''
        self.round_audio()

    def pause_review(self):
        if self.round:
            self.dirty = True
            self.persist()
            self.round = None
            self.pronouncer.cancel()
            self.cursor = len(self.session.state['draft'])
            self.notice = '复习已暂停，F9 可继续；学习位置保持原样。'

    def round_audio(self):
        if not self.round or self.round.finished:
            return
        if self.round.question.get('mode') == 'listening' and self.round.phase == 'answer':
            self.play_current()
        elif self.audio_settings.auto_pronounce and self.round.allows_auto_audio:
            self.play_current()

    def finish_round_if_needed(self):
        self.dirty = True
        self.persist()
        self.planner_stats(refresh=True)
        if self.round.finished:
            message = self.round.message
            self.round = None
            self.cursor = len(self.session.state['draft'])
            self.notice = message
            self.pronouncer.cancel()
        else:
            self.cursor = len(self.round.draft)

    def sync_review_error(self, ref, needs_review):
        if not self.library or ref is None:
            return
        identifier, name = ref['dictionary_id'], ref['word']
        profiles = dict(self.library.profiles)
        base = self.session.state if identifier == self.library.active_id else profiles.get(identifier)
        if base is None:
            return
        state = copy.deepcopy(base)
        record = state['records'].setdefault(name, {'correct': 0, 'mistakes': 0, 'recall': 0, 'needsReview': False})
        record['needsReview'] = needs_review
        profiles[identifier] = state
        self.library._write(profiles, self.library.active_id)
        self.library.profiles = profiles
        if identifier == self.library.active_id:
            self.session.state = state

    def draw_round(self, screen):
        rows, cols = screen.getmaxyx()
        round_ = self.round
        book = self.library.catalog.by_id[round_.current['dictionary_id']]['name']
        self.put(screen, 0, 0, f'雅思随手练 v{VERSION} 复习 {book} 已答{round_.answered} 待答{round_.remaining}', curses.A_BOLD)
        if round_.phase != 'answer':
            self.put(screen, 1, 0, round_.word['name'], self.colors.get('word', curses.A_BOLD))
            self.put(screen, 2, 0, '；'.join(round_.word['trans']))
        else:
            for index, line in enumerate(wrapped(round_.question['prompt'], cols - 1)[:2]):
                self.put(screen, 1 + index, 0, line)
        input_row = rows - 3
        prefix = '补练 > ' if round_.phase == 'repair' else '答案 > '
        if round_.phase == 'done':
            self.put(screen, input_row, 0, 'Enter 下一题；F9 暂停返回学习。')
        else:
            self.put(screen, input_row, 0, prefix + round_.draft)
        self.put(screen, rows - 2, 0, self.notice or round_.message, self.colors.get('bad', 0) if round_.phase == 'repair' else 0)
        self.put(screen, rows - 1, 0, 'Enter检查 F3答案 F4跳过 F5读音 F9暂停 ^Q退出', curses.A_DIM)
        try:
            screen.move(input_row, min(cols - 2, width(prefix) + width(round_.draft[:self.cursor])))
        except curses.error:
            pass
        screen.refresh()

    def handle_round(self, key):
        round_ = self.round
        if key in ('\n', '\r', curses.KEY_ENTER):
            ref = copy.deepcopy(round_.current)
            question_mode = round_.question.get('mode')
            result = round_.submit()
            if result in ('wrong', 'assisted'):
                self.sync_review_error(ref, True)
            elif result == 'correct' and question_mode in ('recall', 'listening', 'cloze', 'collocation'):
                self.sync_review_error(ref, False)
            self.finish_round_if_needed()
            if self.round and result in ('next', 'correct', 'wrong', 'assisted', 'repaired'):
                self.pronouncer.cancel()
                self.round_audio()
            return
        if key in (curses.KEY_F3, '\x07'):
            was_answer = round_.phase == 'answer'
            round_.reveal()
            if was_answer:
                self.sync_review_error(round_.current, True)
            self.finish_round_if_needed()
            self.round_audio()
            return
        if key in (curses.KEY_F4, '\x0e'):
            self.pronouncer.cancel()
            round_.skip()
            self.finish_round_if_needed()
            self.round_audio()
            return
        if round_.phase == 'done':
            return
        text = round_.draft
        if key == curses.KEY_LEFT:
            self.cursor = max(0, self.cursor - 1)
        elif key == curses.KEY_RIGHT:
            self.cursor = min(len(text), self.cursor + 1)
        elif key in (curses.KEY_HOME, '\x01'):
            self.cursor = 0
        elif key in (curses.KEY_END, '\x05'):
            self.cursor = len(text)
        elif key in (curses.KEY_BACKSPACE, '\x7f', '\b') and self.cursor:
            round_.set_draft(text[:self.cursor - 1] + text[self.cursor:])
            self.cursor -= 1
            self.dirty = True
        elif key == curses.KEY_DC:
            round_.set_draft(text[:self.cursor] + text[self.cursor + 1:])
            self.dirty = True
        elif key == '\x15':
            round_.set_draft('')
            self.cursor = 0
            self.dirty = True
        elif isinstance(key, str) and key.isprintable() and len(text) < 300:
            round_.set_draft(text[:self.cursor] + key + text[self.cursor:])
            self.cursor += 1
            self.dirty = True
        self.last_edit = time.monotonic()

    def edit(self, action):
        action()
        self.dirty = True
        self.saved = False
        self.last_edit = time.monotonic()

    def put(self, screen, row, col, text, style=0):
        rows, cols = screen.getmaxyx()
        if row < 0 or row >= rows or col >= cols - 1:
            return
        value = clipped(text, cols - col - 1)
        if not value:
            return
        try:
            screen.addstr(row, col, value, style)
        except curses.error:
            pass

    def open_picker(self, kind):
        if not self.library:
            self.notice = '请重新运行 ielts 使用词库与章节选择。'
            return
        if self.round:
            self.pause_review()
        self.persist()
        self.pronouncer.cancel()
        self.picker_number = self.picker_error = ''
        if kind == 'dictionary':
            self.picker_rows = []
            for index, entry in enumerate(self.library.catalog.entries):
                count = entry['word_count']
                chapters = (count + self.library.catalog.chapter_size - 1) // self.library.catalog.chapter_size
                practiced = self.library.practiced_count(entry['id'])
                marker = '*' if entry['id'] == self.library.active_id else ' '
                label = f"{index + 1}. {marker}{entry['name']}  {count}词 / {chapters}章  已练{practiced}词"
                self.picker_rows.append({'value': entry['id'], 'label': label})
            self.picker_index = next(i for i, row in enumerate(self.picker_rows) if row['value'] == self.library.active_id)
        else:
            self.picker_rows = []
            for index in range(self.session.chapter_count):
                info = self.session.chapter_info(index)
                marker = '*' if index == self.session.chapter_index else ' '
                label = f"{marker}第 {index + 1} 章  {info['start']}-{info['end']}  已练{info['practiced']}/{info['total']}  {info['first']}"
                self.picker_rows.append({'value': index, 'label': label})
            self.picker_index = self.session.chapter_index
        self.picker = kind

    def draw_picker(self, screen):
        rows, cols = screen.getmaxyx()
        title = '词库选择' if self.picker == 'dictionary' else '章节选择 / ' + self.library.entry['name']
        self.put(screen, 0, 0, title, curses.A_BOLD)
        number = self.picker_number or str(self.picker_index + 1)
        self.put(screen, 1, 0, self.picker_error or f'编号: {number} / {len(self.picker_rows)}（可直接输入编号）', self.colors.get('bad', 0) if self.picker_error else 0)
        visible = max(1, rows - 3)
        start = max(0, min(self.picker_index - visible // 2, len(self.picker_rows) - visible))
        for offset, item in enumerate(self.picker_rows[start:start + visible]):
            selected = start + offset == self.picker_index
            self.put(screen, 2 + offset, 0, item['label'], curses.A_REVERSE if selected else 0)
        self.put(screen, rows - 1, 0, '↑↓/PgUp/PgDn 选择  Enter 确定  Esc 返回', curses.A_DIM)
        screen.refresh()

    def handle_picker(self, key):
        if key == '\x1b':
            self.picker = None
            return
        if key in (curses.KEY_UP, curses.KEY_DOWN, curses.KEY_PPAGE, curses.KEY_NPAGE, curses.KEY_HOME, curses.KEY_END):
            self.picker_number = self.picker_error = ''
            step = getattr(self, 'picker_page_size', 5)
            if key == curses.KEY_UP:
                self.picker_index = max(0, self.picker_index - 1)
            elif key == curses.KEY_DOWN:
                self.picker_index = min(len(self.picker_rows) - 1, self.picker_index + 1)
            elif key == curses.KEY_PPAGE:
                self.picker_index = max(0, self.picker_index - step)
            elif key == curses.KEY_NPAGE:
                self.picker_index = min(len(self.picker_rows) - 1, self.picker_index + step)
            elif key == curses.KEY_HOME:
                self.picker_index = 0
            else:
                self.picker_index = len(self.picker_rows) - 1
            return
        if key in (curses.KEY_BACKSPACE, '\x7f', '\b', '\x15') or (isinstance(key, str) and key in '0123456789'):
            if key == '\x15':
                self.picker_number = ''
            elif key in (curses.KEY_BACKSPACE, '\x7f', '\b'):
                self.picker_number = self.picker_number[:-1]
            elif len(self.picker_number) < len(str(len(self.picker_rows))) + 1:
                self.picker_number += key
            self.picker_error = ''
            if self.picker_number:
                number = int(self.picker_number)
                if 1 <= number <= len(self.picker_rows):
                    self.picker_index = number - 1
                else:
                    self.picker_error = f'请输入 1–{len(self.picker_rows)} 范围内的编号。'
            return
        if key not in ('\n', '\r', curses.KEY_ENTER) or self.picker_error:
            return
        value = self.picker_rows[self.picker_index]['value']
        old_session = self.session
        try:
            if self.picker == 'dictionary':
                self.session = self.library.switch_dictionary(value)
            elif value != self.session.chapter_index or self.session.state['filter'] != 'all':
                self.session = self.library.switch_chapter(value)
            self.picker = None
            self.cursor = len(self.session.state['draft'])
            self.dirty, self.saved = False, True
            self.normal_audio_hint = False
            if self.session is not old_session and self.audio_settings.auto_pronounce and self.auto_audio_allowed():
                self.play_current()
        except (OSError, ValueError) as error:
            self.picker_error = str(error)

    def draw(self, screen):
        screen.erase()
        rows, cols = screen.getmaxyx()
        if rows < 6 or cols < 40:
            self.put(screen, 0, 0, '终端至少需要 40 列、6 行。')
            self.put(screen, 1, 0, '拉高/拉宽面板；Ctrl+Q 退出。')
            screen.refresh()
            return
        try:
            curses.curs_set(0 if self.picker or self.help_open else 1)
        except curses.error:
            pass
        if self.picker:
            self.picker_page_size = max(1, rows - 3)
            self.draw_picker(screen)
            return
        if self.help_open:
            lines = [
                '雅思随手练 / 快捷键（任意键返回）',
                'Enter 检查/下一词；Tab 跟打/默写',
                'F2/Ctrl+R 错词；F3/Ctrl+G 答案；F4/Ctrl+N 跳过',
                'F5/Ctrl+P 发音；F6/Ctrl+T 自动发音开关',
                'F7/Ctrl+D 词库；F8/Ctrl+K 章节',
                'F9/Ctrl+B 到期复习 / 暂停复习',
                'Ctrl+Q / Ctrl+C 保存退出',
                '左右/Home/End 定位；退格/Delete 删除；Ctrl+U 清空',
                '无提示、无错误的默写正确，才会移出错词。',
            ]
            for row, line in enumerate(lines[:rows]):
                self.put(screen, row, 0, line)
            screen.refresh()
            return
        if self.round:
            self.draw_round(screen)
            return
        state = self.session.state
        mode = '默写' if state['mode'] == 'recall' else '跟打'
        group = '错词复习' if state['filter'] == 'errors' else 'IELTS'
        auto_label = '开' if self.audio_settings.auto_pronounce else '关'
        if self.library:
            book = self.library.entry['name']
            error_label = '错词复习 ' if state['filter'] == 'errors' else ''
            header = f"雅思随手练 {book} {error_label}章{self.session.chapter_index + 1}/{self.session.chapter_count} 词{self.session.chapter_position}/{self.session.chapter_word_count} {mode} 今日{self.session.today_count} 错{self.session.error_count} 复习{self.planner_stats()['due']} 音:{auto_label}"
        else:
            header = f"雅思随手练  {group} {self.session.position}/{len(self.session.words)}  {mode}  今日 {self.session.today_count} 次  错词 {self.session.error_count}  自动读音:{auto_label}"
        self.put(screen, 0, 0, header, curses.A_BOLD)
        entry = self.session.current_word
        if entry:
            masked = state['mode'] == 'recall' and not state['revealed'] and state['phase'] != 'success'
            name = '·' * len(entry['name']) if masked else entry['name']
            phone = '' if masked else entry.get('ukphone') or entry.get('usphone') or ''
            self.put(screen, 1, 0, name, self.colors.get('word', curses.A_BOLD))
            if phone and width(name) + 5 < cols:
                self.put(screen, 1, width(name) + 3, f'/{phone}/', curses.A_DIM)
            meaning = '；'.join(entry['trans'])
        else:
            self.put(screen, 1, 0, '暂时没有错词', curses.A_BOLD)
            meaning = '按 F2 回到全部词库。'
        chunks = wrapped(meaning, cols - 1)
        meaning_rows = min(2, max(1, rows - 5), len(chunks))
        for index, line in enumerate(chunks[:meaning_rows]):
            if index == meaning_rows - 1 and len(chunks) > meaning_rows:
                line = clipped(line, cols - 4) + '...'
            self.put(screen, 2 + index, 0, line)
        input_row = 2 + meaning_rows
        prefix = '输入 > '
        self.put(screen, input_row, 0, prefix)
        draft = clean_text(state['draft'])
        self.cursor = min(self.cursor, len(draft))
        space = cols - width(prefix) - 2
        offset = 0
        while width(draft[offset:self.cursor]) >= space and offset < self.cursor:
            offset += 1
        answer_style = self.colors.get('good', curses.A_BOLD) if state['phase'] == 'success' else 0
        self.put(screen, input_row, width(prefix), draft[offset:], answer_style)
        message = self.notice or self.session.message or 'Enter 检查；按 F1 查看帮助。'
        suffix = '  [已保存]' if self.saved else '  [保存中]'
        tone = self.session.tone
        self.put(screen, input_row + 1, 0, clipped(message, max(1, cols - width(suffix) - 1)) + suffix, self.colors.get(tone, 0))
        self.put(screen, input_row + 2, 0, 'Tab模式 F5读音 F6自动 F7词库 F8章节 F9复习 F1更多 ^Q退出', curses.A_DIM)
        try:
            screen.move(input_row, min(cols - 2, width(prefix) + width(draft[offset:self.cursor])))
        except curses.error:
            pass
        screen.refresh()

    def handle(self, key):
        if key in ('\x03', '\x11', curses.KEY_F10):
            self.running = False
            return
        if key == curses.KEY_RESIZE:
            return
        if self.picker:
            self.handle_picker(key)
            return
        if self.help_open:
            self.help_open = False
            return
        if key == curses.KEY_F1:
            self.help_open = True
            return
        self.notice = ''
        if key in (curses.KEY_F9, '\x02'):
            if self.round:
                self.pause_review()
            else:
                self.start_review()
            return
        if key in (curses.KEY_F7, '\x04', curses.KEY_F8, '\x0b'):
            try:
                self.open_picker('dictionary' if key in (curses.KEY_F7, '\x04') else 'chapter')
            except (OSError, ValueError) as error:
                self.notice = str(error)
            return
        if key in (curses.KEY_F5, '\x10'):
            if self.round:
                self.round.mark_audio_hint()
                self.dirty = True
                self.persist()
            self.play_current()
            return
        if key in (curses.KEY_F6, '\x14'):
            self.toggle_auto_pronunciation()
            return
        if self.round:
            self.handle_round(key)
            return
        state = self.session.state
        actions = {
            '\t': self.session.toggle_mode,
            curses.KEY_F2: self.session.toggle_errors, '\x12': self.session.toggle_errors,
            curses.KEY_F3: self.session.reveal, '\x07': self.session.reveal,
            curses.KEY_F4: self.session.next_word, '\x0e': self.session.next_word,
            '\n': self.session.submit, '\r': self.session.submit, curses.KEY_ENTER: self.session.submit,
        }
        if key in actions:
            before_word = self.session.current_word
            before_mode = state['mode']
            was_hinted = state['revealed'] or state['hadError'] or self.normal_audio_hint
            grading = key in ('\n', '\r', curses.KEY_ENTER) and state['phase'] != 'success' and bool(state['draft'].strip())
            next_visit = key in (curses.KEY_F4, '\x0e', curses.KEY_F2, '\x12') or (key in ('\n', '\r', curses.KEY_ENTER) and state['phase'] == 'success')
            self.edit(actions[key])
            self.cursor = len(self.session.state['draft'])
            self.persist()
            if self.planner and before_word and grading:
                self.planner.note(self.library.active_id, before_word['name'], before_mode, state['phase'] == 'success', hinted=was_hinted, event_id=uuid.uuid4().hex)
                self.planner_stats(refresh=True)
            if grading and before_mode == 'recall' and state['phase'] == 'success' and self.audio_settings.auto_pronounce:
                self.play_current()
            if before_mode != state['mode']:
                self.normal_audio_hint = False
            if next_visit or before_word != self.session.current_word:
                self.normal_audio_hint = False
                self.pronouncer.cancel()
                if self.audio_settings.auto_pronounce and self.auto_audio_allowed():
                    self.play_current()
            return
        if self.session.current_word is None or state['phase'] == 'success':
            return
        draft = state['draft']
        if key == curses.KEY_LEFT:
            self.cursor = max(0, self.cursor - 1)
        elif key == curses.KEY_RIGHT:
            self.cursor = min(len(draft), self.cursor + 1)
        elif key in (curses.KEY_HOME, '\x01'):
            self.cursor = 0
        elif key in (curses.KEY_END, '\x05'):
            self.cursor = len(draft)
        elif key in (curses.KEY_BACKSPACE, '\x7f', '\b') and self.cursor:
            self.edit(lambda: self.session.set_draft(draft[:self.cursor - 1] + draft[self.cursor:]))
            self.cursor -= 1
        elif key == curses.KEY_DC:
            self.edit(lambda: self.session.set_draft(draft[:self.cursor] + draft[self.cursor + 1:]))
        elif key == '\x15':
            self.edit(lambda: self.session.set_draft(''))
            self.cursor = 0
        elif isinstance(key, str) and key.isprintable() and len(draft) < 300:
            self.edit(lambda: self.session.set_draft(draft[:self.cursor] + key + draft[self.cursor:]))
            self.cursor += 1

    def run(self, screen):
        curses.raw()
        curses.noecho()
        screen.keypad(True)
        screen.timeout(100)
        try:
            curses.set_escdelay(30)
            curses.curs_set(1)
        except (AttributeError, curses.error):
            pass
        if curses.has_colors():
            curses.start_color()
            try:
                curses.use_default_colors()
                for index, (tone, color) in enumerate([('word', curses.COLOR_CYAN), ('good', curses.COLOR_GREEN), ('bad', curses.COLOR_RED)], 1):
                    curses.init_pair(index, color, -1)
                    self.colors[tone] = curses.color_pair(index) | (curses.A_BOLD if tone == 'word' else 0)
            except curses.error:
                pass
        old_handlers = {}
        for signum in shutdown_signals():
            old_handlers[signum] = signal.signal(signum, self.stop)
        try:
            if self.planner and not self.force_learning and (self.force_review or self.load_round() or self.planner.due_cards(limit=1)):
                self.start_review()
            if not self.round and self.audio_settings.auto_pronounce and self.auto_audio_allowed():
                self.play_current()
            while self.running:
                audio_message = self.pronouncer.poll_message()
                if audio_message:
                    self.notice = audio_message
                if self.dirty and time.monotonic() - self.last_edit >= 0.25:
                    self.persist()
                self.draw(screen)
                try:
                    key = screen.get_wch()
                except curses.error:
                    continue
                self.handle(key)
        finally:
            self.persist()
            for signum, handler in old_handlers.items():
                signal.signal(signum, handler)


def main(argv=None):
    parser = argparse.ArgumentParser(description='在终端跟打、默写和复习雅思单词；无需网页或模型。')
    data_home = Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local' / 'share'))
    parser.add_argument('--data-dir', type=Path, default=default_data_dir(), help='当前版本独立的进度目录')
    parser.add_argument('--no-import', action='store_true', help='首次运行时不复制旧 MCP 进度')
    parser.add_argument('--mode', choices=('copy', 'recall'), help='启动时使用跟打或默写模式')
    parser.add_argument('--dictionary', help='词库 ID；用 --list-dictionaries 查看')
    parser.add_argument('--chapter', type=int, help='从第几章开始（编号从 1 起）')
    parser.add_argument('--list-dictionaries', action='store_true', help='列出英语与雅思词库后退出')
    parser.add_argument('--version', action='version', version='ielts ' + VERSION)
    parser.add_argument('--review', action='store_true', help='启动时进入到期复习')
    parser.add_argument('--learn', action='store_true', help='直接学习，暂不自动进入复习')
    parser.add_argument('--stats', action='store_true', help='显示本版本学习统计后退出')
    parser.add_argument('--review-intervals', help='复习间隔天数，例如 1,3,7,14,30')
    args = parser.parse_args(argv)
    try:
        catalog = Catalog(ROOT / 'data' / 'catalog.json')
        if args.list_dictionaries:
            for entry in catalog.entries:
                chapters = (entry['word_count'] + catalog.chapter_size - 1) // catalog.chapter_size
                print(f"{entry['id']:<18} {entry['name']}  {entry['word_count']} 词 / {chapters} 章")
            return 0
    except (OSError, ValueError) as error:
        parser.exit(2, f'词库目录读取失败：{error}\n')
    if not args.stats and (not sys.stdin.isatty() or not sys.stdout.isatty()):
        parser.exit(2, '请在交互终端运行 ielts（不能通过管道启动）。\n')
    if not args.stats and os.environ.get('TERM', '') in ('', 'dumb'):
        parser.exit(2, '终端未声明可用的 TERM；请在 Codex 底部终端或普通终端运行。\n')
    locale.setlocale(locale.LC_ALL, '')
    old_progress = None if args.no_import else data_home / 'ielts-strip' / 'progress.json'
    try:
        with StateStore(args.data_dir, import_from=old_progress) as store:
            seed_version_profile(store, disabled=args.no_import)
            library = StudyLibrary(catalog, store)
            selected_id = args.dictionary or library.active_id
            if selected_id not in catalog.by_id:
                raise ValueError('词库 ID 无效；运行 ielts --list-dictionaries 查看')
            if args.chapter is not None:
                count = (catalog.by_id[selected_id]['word_count'] + catalog.chapter_size - 1) // catalog.chapter_size
                if not 1 <= args.chapter <= count:
                    raise ValueError(f'章节编号应在 1–{count} 之间')
            session = library.switch_dictionary(selected_id)
            if args.chapter is not None and (args.chapter - 1 != session.chapter_index or session.state['filter'] != 'all'):
                session = library.switch_chapter(args.chapter - 1)
            if args.mode and session.state['mode'] != args.mode:
                session.toggle_mode()
            library.save()
            planner = StudyPlanner(args.data_dir)
            planner.seed_from_library(library.profiles)
            if args.review_intervals:
                planner.set_intervals([int(value) for value in args.review_intervals.split(',')])
            if args.stats:
                print(json.dumps(planner.stats(), ensure_ascii=False, indent=2))
                planner.close()
                return 0
            app = TerminalStudy(session, store, library=library, planner=planner)
            app.force_learning, app.force_review = args.learn, args.review
            try:
                curses.wrapper(app.run)
            finally:
                app.pronouncer.close()
                planner.close()
        print('进度已保存。下次运行 ielts 可继续。')
        return 0
    except (OSError, ValueError, RuntimeError, curses.error) as error:
        print(f'启动或保存失败：{error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
