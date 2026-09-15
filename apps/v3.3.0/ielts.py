#!/usr/bin/env python3
"""在底部终端练习雅思拼写；使用 curses 重绘并保存本机学习进度。"""

import argparse
import copy
import datetime
import curses
import json
import locale
import os
from pathlib import Path
import signal
from portable_compat import shutdown_signals
import sqlite3
import subprocess
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
from quiz import QuestionFactory, is_correct, choose_mode, display_meanings
from routine import Routine
from updater import UpdateManager
import reminders

MODE_LABELS = {'copy': '跟打', 'recall': '中文默写', 'en_to_zh': '英选中', 'zh_to_en': '中选英', 'listening': '听音拼写', 'mixed': '混合练习', 'cloze': '例句填空', 'collocation': '常用搭配'}


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
    def __init__(self, session, store, audio_settings=None, pronouncer=None, library=None, planner=None, routine=None, update_manager=None):
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
        self.routine = routine or Routine(store.path.parent)
        self.updater = update_manager
        self.update_status = update_manager.poll() if update_manager else {'state': 'disabled'}
        self.update_banner = ''
        self.update_activated = False
        self.update_activated_version = None
        self.update_dismissed_version = None
        self.update_restart_path = None
        self.update_restart_args = []
        self.skip_update_check = False
        self.daily_panel = False
        self.reminder_pending = False
        self.force_daily = False
        self.force_incremental = False
        self.editor_field = None
        self.editor_draft = ''
        self.round = None
        self.prompt_offset = 0
        self.prompt_page_size = 1
        self.prompt_total_lines = 0
        self.prompt_view_key = None
        self.round_path = store.path.parent / 'active_round.json'
        self.extra_round_path = store.path.parent / 'extra_round.json'
        self.extra_skipped = set()
        self.extra_skip_day = None
        self.learn_round_path = store.path.parent / ('learn_round-' + (library.active_id if library else 'ielts') + '.json')
        self.practice_settings_path = store.path.parent / 'practice_settings.json'
        self.practice_settings = {'version': 2, 'learn_mode': 'mixed', 'review_mode': 'recall'}
        if self.practice_settings_path.exists():
            value = json.loads(self.practice_settings_path.read_text(encoding='utf-8'))
            if not isinstance(value, dict) or type(value.get('version')) is not int or value['version'] not in (1, 2) or value.get('learn_mode') not in MODE_LABELS or value.get('review_mode') not in MODE_LABELS:
                raise ValueError('题型设置无效')
            if value['version'] == 1:
                value = dict(value, version=2, learn_mode='mixed')
                self.store._atomic_write(self.practice_settings_path, value)
            self.practice_settings = value
        else:
            self.store._atomic_write(self.practice_settings_path, self.practice_settings)
        self.factories = {}
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
            path = {'review': self.round_path, 'learn': self.learn_round_path, 'extra': self.extra_round_path}[self.round.context]
            self.store._atomic_write(path, self.round.snapshot())
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

    def factory(self, identifier):
        if identifier not in self.factories:
            self.factories[identifier] = QuestionFactory(self.library.catalog.words_for(identifier), content_path=ROOT / 'content.json')
        return self.factories[identifier]

    def make_question(self, identifier, word, mode):
        factory = self.factory(identifier)
        content_key = ' '.join(word.casefold().split())
        if mode == 'mixed':
            mode = choose_mode(self.planner.mode_stats(identifier, word), has_content=content_key in factory.content, allow_listening=self.audio_settings.auto_pronounce) if self.planner.has_seen(identifier, word) else 'copy'
        if mode == 'copy':
            return {'mode': 'copy', 'word': word, 'answer': word, 'prompt': word, 'options': [], 'correct_indices': [], 'reason': '先对照拼写；跟打不计为已经记住。'}
        question = factory.make(word, mode)
        if mode in ('cloze', 'collocation'):
            if question['mode'] != mode:
                question['skip_example'] = True
            elif mode == 'cloze':
                question['translation'] = factory.content[content_key].get('translation', '')
        if question['mode'] == 'copy':
            question['prompt'] = word
        return question

    def load_round(self, context='review'):
        if context == 'learn':
            self.learn_round_path = self.store.path.parent / ('learn_round-' + self.library.active_id + '.json')
        path = {'review': self.round_path, 'learn': self.learn_round_path, 'extra': self.extra_round_path}[context]
        if not path.exists():
            return None
        if path.stat().st_size > 2 * 1024 * 1024:
            raise ValueError('复习轮次文件过大')
        restored = PracticeRound.restore(self.library.catalog, self.planner, json.loads(path.read_text(encoding='utf-8')), provider=self.make_question, checker=is_correct)
        if restored and context == 'extra' and restored.mode not in ('cloze', 'collocation'):
            # 3.2.0 的返回菜单可能误改增量轮次的 mode；保留当前题、输入和评分事件。
            restored.mode = restored.question['mode'] if restored.question['mode'] in ('cloze', 'collocation') else 'cloze'
            restored.sequential = False
        if restored and restored.question.get('mode') in ('zh_to_en', 'recall'):
            # 承接旧题时只刷新中文提示；选项、答案、输入与评分事件保持原值。
            prompt = display_meanings(restored.word)
            if prompt:
                restored.question['prompt'] = prompt
        return restored

    def start_quiz(self, mode=None, limit=None, resume=True, cards=None):
        if not self.planner or not self.library:
            return
        self.persist()
        self.pronouncer.cancel()
        mode = mode or self.practice_settings['learn_mode']
        limit = limit or (self.routine.config['batch_size'] if mode == 'mixed' else 20)
        self.learn_round_path = self.store.path.parent / ('learn_round-' + self.library.active_id + '.json')
        pending = self.load_round('learn') if resume else None
        if pending and (pending.mode == mode or mode == 'mixed') and pending.current['dictionary_id'] == self.library.active_id:
            self.round = pending
            self.round.mode = mode
        else:
            identifier = self.library.active_id
            if cards is None:
                words = self.session.words
                if mode in ('cloze', 'collocation'):
                    content = self.factory(identifier).content
                    words = [w for w in words if w['name'].casefold() in content]
                    unused = [w for w in words if not sum(self.planner.mode_stats(identifier, w['name'])[mode][k] for k in ('correct', 'wrong'))]
                    words = unused or words
                else:
                    start = max(0, self.session.position - 1)
                    words = words[start:]
                cards = [{'dictionary_id': identifier, 'word': w['name']} for w in words[:limit]]
            self.round = PracticeRound(self.library.catalog, self.planner, cards, mode=mode, context='learn', provider=self.make_question, checker=is_correct, examples=self.routine.config['examples_during_learning'])
            # 旧版尚未完成的输入先沿用原题，下一题起进入自动题型。
            state = self.session.state
            if resume and self.round.current and self.round.current['word'] == state['current'] and (state['draft'] or state['phase'] == 'success' or state['revealed']):
                self.round.question = self.make_question(identifier, state['current'], state['mode'])
                self.round.draft = state['draft']
                self.round.audio_hint = bool(state['revealed'] or state['hadError'])
                if state['phase'] == 'success':
                    self.round.phase = 'done'
                    self.round.message = '上次已答对；Enter 继续自动练习。'
                elif state['revealed']:
                    self.round.phase = 'repair'
                    self.round.message = '已保留上次答案和输入；完成补练后继续。'
        if self.round.finished:
            self.round = None
            self.notice = '当前词库没有适用的题目；可选择雅思通用或其它题型。'
            return
        self.cursor = len(self.round.draft)
        self.dirty = True
        self.persist()
        self.notice = ''
        self.round_audio()

    def open_mode_picker(self):
        self.persist()
        self.pronouncer.cancel()
        self.picker = 'mode'
        self.picker_scope = 'review_mode' if self.round and self.round.context == 'review' else 'learn_mode'
        self.picker_number = self.picker_error = ''
        values = [m for m in MODE_LABELS if not (self.picker_scope == 'review_mode' and m == 'copy')]
        self.picker_rows = [{'value': mode, 'label': f'{i + 1}. {MODE_LABELS[mode]}'} for i, mode in enumerate(values)]
        chosen = self.round.mode if self.round else self.practice_settings[self.picker_scope]
        self.picker_index = values.index(chosen) if chosen in values else 0

    def open_menu(self):
        self.persist()
        self.pronouncer.cancel()
        self.help_open = False
        self.picker = 'menu'
        self.picker_number = self.picker_error = ''
        self.picker_index = 0
        self.picker_rows = [
            {'value': 'continue', 'label': '1. 继续当前题'},
            {'value': 'automatic', 'label': '2. 返回单词学习（自动题型）'},
            {'value': 'mode', 'label': '3. 手动指定题型'},
            {'value': 'dictionary', 'label': '4. 选择词库'},
            {'value': 'chapter', 'label': '5. 选择章节'},
            {'value': 'review', 'label': '6. 到期复习'},
            {'value': 'daily', 'label': '7. 今日任务'},
            {'value': 'routine', 'label': '8. 学习计划'},
            {'value': 'incremental', 'label': '9. 增量练习（例句与搭配）'},
        ]
        if self.updater:
            self.picker_rows.append({'value': 'updates', 'label': '10. 版本与更新'})

    def update_rows(self):
        status = self.update_status
        rows = [
            ('auto_check', '启动时检查稳定版本：' + ('开' if self.updater.auto_check else '关')),
            ('check_update', '立即检查更新'),
        ]
        if status.get('state') == 'available':
            rows.append(('install_update', '下载并校验 v' + status['version']))
        if status.get('state') == 'ready':
            rows.append(('restart_update', '保存进度并重启到 v' + status['version']))
            if not self.update_activated:
                rows.append(('activate_update', '下次启动使用 v' + status['version']))
        rows.append(('continue', '继续当前练习' if self.update_activated else '暂不更新，继续当前练习'))
        return [{'value': value, 'label': f'{index + 1}. {label}'} for index, (value, label) in enumerate(rows)]

    def open_update_picker(self):
        if not self.updater:
            return
        self.persist()
        self.pronouncer.cancel()
        self.daily_panel = False
        self.update_status = self.updater.poll()
        self.picker = 'updates'
        self.picker_rows = self.update_rows()
        self.picker_index = 0
        self.picker_number = self.picker_error = ''

    def start_update_check(self):
        if self.updater and self.updater.auto_check and not self.skip_update_check and os.environ.get('IELTS_DISABLE_UPDATE_CHECK') != '1':
            return self.updater.check_async()
        return False

    def poll_updates(self):
        if not self.updater:
            return
        status = self.updater.poll()
        changed = status != self.update_status
        self.update_status = status
        self.update_activated = status.get('version') == self.update_activated_version
        state = status.get('state')
        if state == 'available':
            self.update_banner = '新版 v' + status['version'] + ' 可用；Esc 菜单 10 可选择更新。'
        elif state == 'downloading':
            self.update_banner = '更新正在后台下载；可继续当前练习。'
        elif state == 'ready':
            self.update_banner = ('新版将在下次启动启用；' if self.update_activated else '新版已下载并校验；') + 'Esc 菜单 10 可保存并重启。'
        else:
            # 自动检查失败只在更新页说明，不打断题目或覆盖答题反馈。
            self.update_banner = ''
        if status.get('version') == self.update_dismissed_version:
            self.update_banner = ''
        if changed and self.picker == 'updates':
            chosen = self.picker_rows[self.picker_index]['value'] if self.picker_rows else None
            self.picker_rows = self.update_rows()
            self.picker_index = next((i for i, row in enumerate(self.picker_rows) if row['value'] == chosen), 0)

    def handle_update_choice(self, value):
        if value == 'auto_check':
            self.updater.set_auto_check(not self.updater.auto_check)
            self.open_update_picker()
        elif value == 'check_update':
            self.update_dismissed_version = None
            self.updater.check_async()
            self.open_update_picker()
        elif value == 'install_update':
            self.update_dismissed_version = None
            self.updater.install_async()
            self.picker = None
            self.notice = '正在后台下载更新；当前练习与输入保持不变。'
        elif value in ('restart_update', 'activate_update'):
            self.persist()
            launcher = self.updater.active_launcher() if self.update_activated else self.updater.activate_ready()
            if launcher is None:
                raise ValueError(self.updater.poll().get('message') or '新版尚未准备好，请重新检查更新。')
            self.update_activated = True
            self.update_activated_version = self.updater.poll().get('version')
            self.picker = None
            self.notice = '新版已就绪，下次启动时使用；当前练习可继续。'
            if value == 'restart_update':
                self.update_restart_path = Path(launcher)
                context = self.round.context if self.round else 'learn'
                self.update_restart_args = [{'extra': '--incremental', 'review': '--review', 'learn': '--learn'}[context]]
                self.running = False
        else:
            self.update_dismissed_version = self.update_status.get('version')
            self.picker = None
        self.poll_updates()

    def apply_question_mode(self, mode):
        candidate = dict(self.practice_settings)
        candidate[self.picker_scope] = mode
        self.store._atomic_write(self.practice_settings_path, candidate)
        self.practice_settings = candidate
        returning_to_learning = self.picker_scope == 'learn_mode' and self.round and self.round.context != 'learn'
        if self.picker_scope == 'learn_mode':
            self.force_learning = True
            self.daily_panel = False
            if returning_to_learning:
                self.pause_review()
            elif self.round:
                self.round.daily = False
        if returning_to_learning:
            self.start_quiz(mode)
            self.notice = '已返回单词学习；之前的练习和输入已保留。'
        elif self.round:
            self.round.mode = mode
            self.dirty = True
            self.persist()
            self.notice = '已设置下一题的题型；当前题与输入保留。'
        elif mode in ('copy', 'recall'):
            if self.session.state['mode'] != mode:
                self.session.toggle_mode()
                self.dirty = True
                self.persist()
            self.cursor = len(self.session.state['draft'])
        else:
            self.start_quiz(mode)

    def start_review(self, limit=None, cards=None):
        if not self.planner or not self.library:
            self.notice = '请重新启动程序以使用复习。'
            return
        self.persist()
        self.force_learning = False
        self.pronouncer.cancel()
        self.reminder_pending = False
        self.routine.mark_review_started()
        self.round = self.load_round()
        if not self.round:
            if cards is None:
                cards = self.planner.due_cards(limit=limit or self.routine.config['batch_size'])
            self.round = PracticeRound(self.library.catalog, self.planner, cards, mode=self.practice_settings['review_mode'], provider=self.make_question, checker=is_correct)
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
            self.notice = '本轮已暂停，输入已保留；Esc 菜单可继续学习或复习。'

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
            daily = self.round.daily
            was_extra = self.round.context == 'extra'
            continue_learning = not daily and ((self.round.context == 'learn' and self.round.mode == 'mixed') or (self.round.context == 'review' and self.practice_settings['learn_mode'] == 'mixed'))
            self.round = None
            self.cursor = len(self.session.state['draft'])
            self.notice = message
            self.pronouncer.cancel()
            if was_extra:
                self.start_incremental(resume=False)
            elif self.advance_daily_work():
                pass
            elif daily:
                self.daily_panel = True
            elif continue_learning:
                self.start_quiz('mixed', resume=False)
                self.notice = '一组完成，已接上下一组；随时可保存退出。'
        else:
            self.cursor = len(self.round.draft)

    def start_daily_group(self):
        self.force_learning = False
        self.daily_panel = False
        self.persist()
        if self.round:
            self.notice = '先继续当前未完成的小组。'
            return
        if self.maybe_start_incremental():
            return
        stats = self.planner_stats(refresh=True)
        summary = self.routine.summary(stats)
        size = summary['batch_size']
        review_cards = self.planner.due_cards(limit=min(size, summary['remaining_review']), exclude_reviewed_today=True) if summary['remaining_review'] else []
        if summary['remaining_review'] and (review_cards or self.load_round()):
            self.start_review(cards=review_cards)
        elif summary['remaining_new']:
            pending = self.load_round('learn')
            if pending:
                self.round = pending
                self.round_audio()
            else:
                start = max(0, self.session.position - 1)
                ordered = self.session.words[start:] + self.session.words[:start]
                candidates = []
                preferred = self.practice_settings['learn_mode']
                available_content = self.factory(self.library.active_id).content if preferred in ('cloze', 'collocation') else None
                for word in ordered:
                    if available_content is not None and word['name'].casefold() not in available_content:
                        continue
                    if not self.planner.has_seen(self.library.active_id, word['name']):
                        candidates.append({'dictionary_id': self.library.active_id, 'word': word['name']})
                        if len(candidates) >= min(size, summary['remaining_new']):
                            break
                if not candidates:
                    self.daily_panel = True
                    self.notice = '当前词库没有符合题型的未学词；可换词库或题型。'
                    return
                self.start_quiz(preferred, limit=len(candidates), resume=False, cards=candidates)
        else:
            self.daily_panel = True
            self.notice = '今日目标已完成。仍有到期词时，可用 F9 继续复习。' if not summary['remaining_review'] else '当前没有可加入今日小组的到期词；F9 可继续重测。'
            return
        if self.round:
            self.round.daily = True
            self.cursor = len(self.round.draft)
            self.dirty = True
            self.persist()

    def daily_work_complete(self):
        stats = self.planner_stats(refresh=True)
        summary = self.routine.summary(stats)
        if summary['remaining_new'] or self.load_round('review'):
            return False
        return not summary['remaining_review'] or not self.planner.due_cards(limit=1, exclude_reviewed_today=True)

    def maybe_start_incremental(self):
        if self.force_learning or not self.planner or not self.routine.config['extra_after_daily'] or not self.daily_work_complete():
            return False
        self.start_incremental()
        return True

    def advance_daily_work(self):
        """新词目标到达后接上剩余到期复习，再进入增量练习。"""
        if self.force_learning or not self.routine.config['extra_after_daily']:
            return False
        summary = self.routine.summary(self.planner_stats(refresh=True))
        if summary['remaining_new']:
            return False
        if summary['remaining_review']:
            cards = self.planner.due_cards(limit=min(summary['batch_size'], summary['remaining_review']), exclude_reviewed_today=True)
            if cards or self.load_round('review'):
                self.start_review(cards=cards)
                return True
        return self.maybe_start_incremental()

    def incremental_candidates(self):
        """只练已学且有内容的词；例句与搭配每个词当天各安排一次。"""
        moment = self.planner._now()
        day = datetime.date.fromtimestamp(moment)
        if self.extra_skip_day != day:
            self.extra_skip_day = day
            self.extra_skipped.clear()
        start = datetime.datetime.combine(day, datetime.time.min).timestamp()
        end = datetime.datetime.combine(day + datetime.timedelta(days=1), datetime.time.min).timestamp()
        completed = set(self.planner.db.execute("SELECT DISTINCT dictionary_id,word,mode FROM events WHERE context='extra' AND happened_at>=? AND happened_at<?", (start, end)))
        totals = {mode: sum(1 for item in completed if item[2] == mode) for mode in ('cloze', 'collocation')}
        seen = {(row[0], row[1]): row[2] or 0 for row in self.planner.db.execute('SELECT dictionary_id,word,last_seen FROM cards')}
        pools = {'cloze': [], 'collocation': []}
        for entry in self.library.catalog.entries:
            identifier = entry['id']
            factory = self.factory(identifier)
            for word in factory.words:
                name = word['name']
                key = (identifier, name)
                if key not in seen or ' '.join(name.casefold().split()) not in factory.content:
                    continue
                for mode in pools:
                    event_key = (identifier, name, mode)
                    if event_key in completed or event_key in self.extra_skipped:
                        continue
                    if factory.make(name, mode)['mode'] != mode:
                        continue
                    pools[mode].append((identifier != self.library.active_id, -seen[key], name, identifier))
        preferred = 'cloze' if totals['cloze'] <= totals['collocation'] else 'collocation'
        for mode in (preferred, 'collocation' if preferred == 'cloze' else 'cloze'):
            if pools[mode]:
                chosen = sorted(pools[mode])[:self.routine.config['batch_size']]
                return mode, [{'dictionary_id': row[3], 'word': row[2]} for row in chosen]
        return 'cloze', []

    def start_incremental(self, resume=True):
        if not self.planner or not self.library:
            return
        self.persist()
        self.force_learning = False
        self.pronouncer.cancel()
        self.daily_panel = False
        day = datetime.date.fromtimestamp(self.planner._now())
        if self.extra_skip_day != day:
            self.extra_skip_day = day
            self.extra_skipped.clear()
        self.round = self.load_round('extra') if resume else None
        if not self.round:
            mode, cards = self.incremental_candidates()
            self.round = PracticeRound(self.library.catalog, self.planner, cards, mode=mode, context='extra', provider=self.make_question, checker=is_correct)
        if self.round.finished:
            self.round = None
            self.daily_panel = True
            stats = self.planner_stats(refresh=True)
            self.notice = '本次可用的增量练习已练完或跳过。' if stats.get('extra_attempts', 0) or self.extra_skipped else '当前已学词还没有可用例句；学到配套词后即可开始增量练习。'
            return
        self.cursor = len(self.round.draft)
        self.notice = '增量练习：复用已学词，不增加每日新词或复习计数。'
        self.dirty = True
        self.persist()
        self.round_audio()

    def poll_routine(self):
        if self.planner and self.routine.tick(has_due=self.planner_stats()['due'] > 0 and not (self.round and self.round.context == 'review')):
            self.reminder_pending = True

    def snooze_reminder(self):
        self.routine.snooze(15)
        self.reminder_pending = False
        self.notice = '提醒已推迟 15 分钟；当前题与输入保留。'

    def draw_daily(self, screen):
        rows, cols = screen.getmaxyx()
        stats = self.planner_stats()
        summary = self.routine.summary(stats)
        self.put(screen, 0, 0, '雅思随手练 / 今日任务', curses.A_BOLD)
        self.put(screen, 1, 0, f"新词 {summary['new_done']}/{summary['new_goal']}  复习 {summary['review_done']}/{summary['review_goal']}  到期 {stats['due']}")
        self.put(screen, 2, 0, self.notice or f"每组 {summary['batch_size']} 个词；优先到期复习，完成后回来选下一组。")
        self.put(screen, 3, 0, '1 开始下一组   2 稍后15分钟   3 调整目标')
        if rows >= 8:
            by_mode = stats.get('by_mode', {})
            spelling = sum(by_mode.get(m, {}).get('correct', 0) for m in ('recall', 'listening', 'cloze', 'collocation'))
            choices = sum(by_mode.get(m, {}).get('correct', 0) for m in ('en_to_zh', 'zh_to_en'))
            self.put(screen, 4, 0, f"今日题型成绩：拼写答对 {spelling} 次，选择答对 {choices} 次，分开统计。")
            self.put(screen, 5, 0, f"增量练习：{stats.get('extra_words', 0)} 词 / {stats.get('extra_attempts', 0)} 题；Esc 菜单第9项可随时进入。")
        self.put(screen, rows - 1, 0, self.update_banner or 'Esc 菜单  F9 复习  ^Q 保存退出', curses.A_DIM)
        screen.refresh()

    def open_routine_picker(self):
        self.persist()
        self.pronouncer.cancel()
        self.daily_panel = False
        names = {'new_goal': '每日新词目标', 'review_goal': '每日复习目标', 'batch_size': '每组词数', 'reminder_minutes': '程序内提醒间隔（0为关闭）'}
        self.picker_rows = [{'value': key, 'label': f'{index + 1}. {label}：{self.routine.config[key]}'} for index, (key, label) in enumerate(names.items())]
        self.picker_rows += [{'value': 'snooze', 'label': '5. 稍后15分钟再提醒'}, {'value': 'background', 'label': '6. 退出后后台提醒：查看开启命令'}]
        self.picker_rows += [
            {'value': 'extra_after_daily', 'label': '7. 每日任务完成后增量练习：' + ('开' if self.routine.config['extra_after_daily'] else '关')},
            {'value': 'examples_during_learning', 'label': '8. 学习过程中加入例句：' + ('开' if self.routine.config['examples_during_learning'] else '关')},
        ]
        if self.updater:
            self.picker_rows.append({'value': 'auto_update_check', 'label': '9. 启动时检查稳定版本：' + ('开' if self.updater.auto_check else '关')})
        self.picker, self.picker_index = 'routine', 0
        self.picker_number = self.picker_error = ''

    def handle_routine_editor(self, key):
        if key == '\x1b':
            self.open_routine_picker()
            return
        if key in ('\n', '\r', curses.KEY_ENTER):
            try:
                if not self.editor_draft or not self.editor_draft.isascii() or not self.editor_draft.isdigit():
                    raise ValueError('请输入有效整数')
                self.routine.update(**{self.editor_field: int(self.editor_draft)})
                self.notice = '设置已保存。当前题与输入保持不变。'
                self.open_routine_picker()
            except (OSError, ValueError) as error:
                self.picker_error = str(error)
            return
        if key in (curses.KEY_BACKSPACE, '\x7f', '\b'):
            self.editor_draft = self.editor_draft[:-1]
        elif key == '\x15':
            self.editor_draft = ''
        elif isinstance(key, str) and key in '0123456789' and len(self.editor_draft) < 6:
            self.editor_draft += key

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

    def sync_typed_success(self, ref, mode, unassisted):
        if mode not in ('copy', 'recall', 'listening', 'cloze', 'collocation') or not ref:
            return
        identifier, name = ref['dictionary_id'], ref['word']
        profiles = dict(self.library.profiles)
        base = self.session.state if identifier == self.library.active_id else profiles.get(identifier)
        if base is None:
            return
        state = copy.deepcopy(base)
        record = state['records'].setdefault(name, {'correct': 0, 'mistakes': 0, 'recall': 0, 'needsReview': False})
        record['correct'] += 1
        if mode == 'recall' and unassisted:
            record['recall'] += 1
        day = self.session._today()
        state['days'][day] = state['days'].get(day, 0) + 1
        profiles[identifier] = state
        self.library._write(profiles, self.library.active_id)
        self.library.profiles = profiles
        if identifier == self.library.active_id:
            self.session.state = state

    def advance_learning_bookmark(self, ref):
        if not ref or ref.get('retry') or ref.get('lesson_step', 0):
            return
        identifier, name = ref['dictionary_id'], ref['word']
        profiles = dict(self.library.profiles)
        base = self.session.state if identifier == self.library.active_id else profiles.get(identifier)
        if base is None:
            return
        state = copy.deepcopy(base)
        words = self.library.catalog.words_for(identifier)
        names = [w['name'] for w in words]
        index = names.index(name)
        old_index = names.index(state['current']) if state['current'] in names else 0
        if index >= old_index:
            state.update(current=names[(index + 1) % len(names)], draft='', phase='typing', revealed=False, hadError=False)
        profiles[identifier] = state
        self.library._write(profiles, self.library.active_id)
        self.library.profiles = profiles
        if identifier == self.library.active_id:
            self.session.state = state

    def draw_prompt(self, screen, text, start_row, capacity):
        """按显示宽度换行，保存翻阅位置，让小终端也能看到完整题干。"""
        _, cols = screen.getmaxyx()
        lines = wrapped(text, cols - 1)
        key = (self.round.event_id, self.round.phase, text)
        if self.prompt_view_key != key:
            self.prompt_view_key = key
            self.prompt_offset = 0
        self.prompt_page_size = max(1, capacity)
        self.prompt_total_lines = len(lines)
        self.prompt_offset = min(self.prompt_offset, max(0, len(lines) - self.prompt_page_size))
        visible = lines[self.prompt_offset:self.prompt_offset + self.prompt_page_size]
        for index, line in enumerate(visible):
            self.put(screen, start_row + index, 0, line)
        return len(visible)

    def example_text(self, round_, show_answer=False):
        question = round_.question
        text = question['prompt']
        if show_answer:
            text = text.replace('____', question['answer'])
        translation = question.get('translation')
        if translation:
            text += '  句意：' + translation
        text += '  目标词义：' + display_meanings(round_.word)
        return text

    def draw_round(self, screen):
        rows, cols = screen.getmaxyx()
        round_ = self.round
        book = self.library.catalog.by_id[round_.current['dictionary_id']]['name']
        activity = '增量练习' if round_.context == 'extra' else ('复习' if round_.context == 'review' else '学习')
        label = MODE_LABELS.get(round_.question.get('mode'), '默写')
        flow = '自动' if round_.mode == 'mixed' else ''
        audio = '开' if self.audio_settings.auto_pronounce else '关'
        self.put(screen, 0, 0, f'雅思随手练 v{VERSION} {activity}{flow} {label} {book} 已答{round_.answered} 待答{round_.remaining} 音:{audio}', curses.A_BOLD)
        choice = round_.phase == 'answer' and bool(round_.question.get('options'))
        input_row = rows - 3
        self.prompt_total_lines = 0
        try:
            curses.curs_set(0 if choice or round_.phase == 'done' else 1)
        except curses.error:
            pass
        if round_.phase != 'answer':
            self.put(screen, 1, 0, round_.word['name'], self.colors.get('word', curses.A_BOLD))
            explanation = self.example_text(round_, show_answer=True) if round_.question.get('mode') in ('cloze', 'collocation') else display_meanings(round_.word)
            self.draw_prompt(screen, explanation, 2, input_row - 2)
        elif round_.question.get('mode') == 'copy':
            phone = round_.word.get('ukphone', '')
            label = round_.word['name'] + (' /' + phone + '/' if phone else '')
            self.put(screen, 1, 0, label, self.colors.get('word', curses.A_BOLD))
            self.draw_prompt(screen, display_meanings(round_.word), 2, input_row - 2)
        elif choice:
            options = round_.question['options']
            cell = (cols - 2) // 2
            vertical = rows >= 10 or (rows >= 8 and any(width(f'{i + 1}. {option}') > cell for i, option in enumerate(options)))
            option_rows = len(options) if vertical else (len(options) + 1) // 2
            used = self.draw_prompt(screen, round_.question['prompt'], 1, rows - 3 - option_rows)
            option_start = 1 + used
            for index, option in enumerate(options):
                if vertical:
                    self.put(screen, option_start + index, 0, f'{index + 1}. {option}')
                else:
                    self.put(screen, option_start + index // 2, (index % 2) * (cell + 1), clipped(f'{index + 1}. {option}', cell))
        else:
            prompt = self.example_text(round_) if round_.question.get('mode') in ('cloze', 'collocation') else round_.question['prompt']
            self.draw_prompt(screen, prompt, 1, input_row - 1)
        prefix = '补练 > ' if round_.phase == 'repair' else '答案 > '
        if choice:
            pass
        elif round_.phase == 'done':
            self.put(screen, input_row, 0, 'Enter 下一题；Esc → 2 返回单词学习。')
        else:
            self.put(screen, input_row, 0, prefix + round_.draft)
        self.put(screen, rows - 2, 0, self.notice or round_.message, self.colors.get('bad', 0) if round_.phase == 'repair' else 0)
        footer = '1–4选择 Esc菜单' if choice and cols < 65 else ('1–4选择 F3答案 F4跳过 F5读音 Esc菜单 ^Q退出' if choice else 'Enter检查 F3答案 F4跳过 F5读音 Esc菜单 ^Q退出')
        if self.prompt_total_lines > self.prompt_page_size:
            last = min(self.prompt_offset + self.prompt_page_size, self.prompt_total_lines)
            action = '1–4选择' if choice else ('Enter继续' if round_.phase == 'done' else 'Enter检查')
            kind = '例句' if round_.question.get('mode') in ('cloze', 'collocation') else '释义'
            footer = f'{kind} {self.prompt_offset + 1}–{last}/{self.prompt_total_lines} ↑↓翻阅 ' + action
        if self.reminder_pending:
            footer = '有到期复习：F9 开始，Ctrl+S 稍后；当前题保留。'
        if self.update_banner:
            footer = self.update_banner
        self.put(screen, rows - 1, 0, footer, curses.A_DIM)
        try:
            screen.move(input_row, min(cols - 2, width(prefix) + width(round_.draft[:self.cursor])))
        except curses.error:
            pass
        screen.refresh()

    def handle_round(self, key):
        round_ = self.round
        if key in (curses.KEY_UP, curses.KEY_DOWN, curses.KEY_PPAGE, curses.KEY_NPAGE):
            direction = -1 if key in (curses.KEY_UP, curses.KEY_PPAGE) else 1
            step = self.prompt_page_size if key in (curses.KEY_PPAGE, curses.KEY_NPAGE) else 1
            self.prompt_offset = max(0, min(self.prompt_offset + direction * step, self.prompt_total_lines - self.prompt_page_size))
            return
        if round_.phase == 'answer' and round_.question.get('options') and isinstance(key, str) and key in '1234':
            round_.set_draft(key)
            key = '\n'
        if key in ('\n', '\r', curses.KEY_ENTER):
            ref = copy.deepcopy(round_.current)
            question_mode = round_.question.get('mode')
            result = round_.submit()
            if result in ('correct', 'assisted', 'repaired'):
                self.sync_typed_success(ref, 'copy' if result == 'repaired' else question_mode, result == 'correct')
            if round_.context == 'learn' and round_.sequential and result in ('correct', 'assisted', 'repaired'):
                self.advance_learning_bookmark(ref)
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
            if round_.context == 'extra':
                self.extra_skipped.add((round_.current['dictionary_id'], round_.current['word'], round_.question['mode']))
            if round_.context == 'learn' and round_.sequential:
                self.advance_learning_bookmark(round_.current)
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
        if self.picker == 'routine_edit':
            self.put(screen, 0, 0, '修改学习计划', curses.A_BOLD)
            self.put(screen, 1, 0, self.editor_label)
            self.put(screen, 2, 0, '数值 > ' + self.editor_draft)
            self.put(screen, 3, 0, self.picker_error)
            self.put(screen, rows - 1, 0, 'Ctrl+U 清空  Enter 保存  Esc 返回', curses.A_DIM)
            screen.refresh()
            return
        title = '词库选择' if self.picker == 'dictionary' else ('学习计划与提醒' if self.picker == 'routine' else ('题型选择 / ' + ('复习' if self.picker_scope == 'review_mode' else '学习') if self.picker == 'mode' else '章节选择 / ' + self.library.entry['name']))
        if self.picker == 'menu':
            title = '学习菜单 / 默认自动穿插题型'
        elif self.picker == 'updates':
            title = '版本与更新 / 当前 v' + VERSION
        self.put(screen, 0, 0, title, curses.A_BOLD)
        number = self.picker_number or str(self.picker_index + 1)
        description = self.update_status.get('message', '只检查稳定版本，由你选择是否更新。') if self.picker == 'updates' else f'编号: {number} / {len(self.picker_rows)}（可直接输入编号）'
        self.put(screen, 1, 0, self.picker_error or description, self.colors.get('bad', 0) if self.picker_error else 0)
        visible = max(1, rows - 3)
        start = max(0, min(self.picker_index - visible // 2, len(self.picker_rows) - visible))
        for offset, item in enumerate(self.picker_rows[start:start + visible]):
            selected = start + offset == self.picker_index
            self.put(screen, 2 + offset, 0, item['label'], curses.A_REVERSE if selected else 0)
        self.put(screen, rows - 1, 0, '↑↓/PgUp/PgDn 选择  Enter 确定  Esc 返回', curses.A_DIM)
        screen.refresh()

    def handle_picker(self, key):
        if self.picker == 'routine_edit':
            self.handle_routine_editor(key)
            return
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
        picker_kind = self.picker
        old_session = self.session
        try:
            if self.picker == 'menu':
                self.picker = None
                self.daily_panel = False
                if value == 'mode':
                    self.open_mode_picker()
                elif value in ('dictionary', 'chapter'):
                    self.open_picker(value)
                elif value == 'automatic':
                    self.picker_scope = 'learn_mode'
                    self.apply_question_mode('mixed')
                elif value == 'review':
                    if self.round and self.round.context == 'learn':
                        self.pause_review()
                    self.start_review()
                elif value == 'daily':
                    self.daily_panel = True
                    self.planner_stats(refresh=True)
                elif value == 'routine':
                    self.open_routine_picker()
                elif value == 'incremental':
                    self.start_incremental()
                elif value == 'updates':
                    self.open_update_picker()
                return
            elif self.picker == 'updates':
                self.handle_update_choice(value)
                return
            elif self.picker == 'routine':
                if value == 'auto_update_check':
                    self.updater.set_auto_check(not self.updater.auto_check)
                    self.open_routine_picker()
                elif value in ('extra_after_daily', 'examples_during_learning'):
                    self.routine.update(**{value: not self.routine.config[value]})
                    self.notice = '设置已保存；学习中加入例句的开关从下一组起生效。'
                    self.open_routine_picker()
                elif value == 'snooze':
                    self.snooze_reminder()
                    self.picker = None
                elif value == 'background':
                    self.picker_error = 'Linux：退出后用启动脚本加 --reminders enable --reminder-minutes 30'
                else:
                    self.editor_field = value
                    self.editor_label = self.picker_rows[self.picker_index]['label']
                    self.editor_draft = str(self.routine.config[value])
                    self.picker_error = ''
                    self.picker = 'routine_edit'
                return
            elif self.picker == 'mode':
                self.apply_question_mode(value)
                self.picker = None
                return
            elif self.picker == 'dictionary':
                self.session = self.library.switch_dictionary(value)
            elif value != self.session.chapter_index or self.session.state['filter'] != 'all':
                self.session = self.library.switch_chapter(value)
            self.picker = None
            self.cursor = len(self.session.state['draft'])
            self.dirty, self.saved = False, True
            self.normal_audio_hint = False
            if self.session is not old_session and self.planner and self.practice_settings['learn_mode'] not in ('copy', 'recall'):
                self.start_quiz(resume=picker_kind == 'dictionary')
            elif self.session is not old_session and self.audio_settings.auto_pronounce and self.auto_audio_allowed():
                self.play_current()
        except (OSError, ValueError, RuntimeError, sqlite3.Error) as error:
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
        if self.daily_panel:
            self.draw_daily(screen)
            return
        if self.help_open:
            lines = [
                '雅思随手练 / 快捷键（任意键返回）',
                'Enter 检查/下一词；Tab 跟打/默写',
                'F2/Ctrl+R 错词；F3/Ctrl+G 答案；F4/Ctrl+N 跳过',
                'F5/Ctrl+P 发音；F6/Ctrl+T 自动发音开关',
                'F7/Ctrl+D 词库；F8/Ctrl+K 章节',
                'F9/Ctrl+B 到期复习 / 暂停复习',
                '题型自动穿插；选择题直接按 1–4',
                'Esc 菜单；空输入时 ? 也可打开菜单',
                'F11/Ctrl+Y 今日任务；F12/Ctrl+L 学习计划',
                'Ctrl+S 将复习提醒推迟15分钟',
                'Esc菜单第9项：增量例句与搭配；学习计划可开启学习中例句',
                'Esc菜单第10项：稳定版本检查、更新开关与确认安装',
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
        footer = '有到期复习：F9 开始，Ctrl+S 稍后；当前输入不会被替换。' if self.reminder_pending else 'Esc菜单 F7词库 F8章节 F9复习 F11任务 F1帮助 ^Q退出'
        if self.update_banner:
            footer = self.update_banner
        self.put(screen, input_row + 2, 0, footer, curses.A_DIM)
        try:
            screen.move(input_row, min(cols - 2, width(prefix) + width(draft[offset:self.cursor])))
        except curses.error:
            pass
        screen.refresh()

    def handle(self, key):
        if key in ('\x03', '\x11'):
            self.running = False
            return
        if key == curses.KEY_RESIZE:
            return
        if self.picker:
            self.handle_picker(key)
            return
        draft = self.round.draft if self.round else self.session.state['draft']
        if self.library and (key == '\x1b' or (key == '?' and not draft)):
            self.open_menu()
            return
        if key in (curses.KEY_F11, '\x19') and self.planner:
            self.persist()
            self.daily_panel = not self.daily_panel
            self.planner_stats(refresh=True)
            return
        if key in (curses.KEY_F12, '\x0c') and self.planner:
            self.open_routine_picker()
            return
        if key == '\x13':
            self.snooze_reminder()
            return
        if self.daily_panel:
            if key == '\x1b':
                self.daily_panel = False
            elif key == '1':
                self.start_daily_group()
            elif key == '2':
                self.snooze_reminder()
            elif key == '3':
                self.open_routine_picker()
            elif key in (curses.KEY_F9, '\x02'):
                self.daily_panel = False
                if not self.round or self.round.context != 'review':
                    self.start_review()
            return
        if self.help_open:
            self.help_open = False
            return
        if key == curses.KEY_F1:
            self.help_open = True
            return
        self.notice = ''
        if key in (curses.KEY_F9, '\x02'):
            if self.round and self.round.context == 'review':
                self.pause_review()
                if self.practice_settings['learn_mode'] == 'mixed':
                    self.start_quiz('mixed')
            else:
                if self.round:
                    self.pause_review()
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
            self.start_update_check()
            if self.force_incremental:
                self.start_incremental()
            elif self.force_daily:
                self.start_daily_group()
            elif self.planner and (self.force_review or (not self.force_learning and self.load_round())):
                self.start_review()
            elif self.planner and self.practice_settings['learn_mode'] == 'mixed' and self.load_round('learn'):
                self.start_quiz('mixed')
            elif not self.force_learning and self.maybe_start_incremental():
                pass
            elif self.planner and not self.force_learning and self.planner.due_cards(limit=1):
                self.start_review()
            elif self.planner and self.practice_settings['learn_mode'] not in ('copy', 'recall'):
                self.start_quiz()
            if not self.round and not self.daily_panel and self.audio_settings.auto_pronounce and self.auto_audio_allowed():
                self.play_current()
            while self.running:
                self.poll_updates()
                self.poll_routine()
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


def restart_with_update(launcher, data_dir, arguments):
    """在旧会话和文件锁关闭后启动已校验的新包，沿用原学习目录。"""
    if os.environ.get('XDG_CACHE_HOME'):
        os.environ['IELTS_UPDATE_CACHE_HOME'] = os.environ['XDG_CACHE_HOME']
    command = [sys.executable, '-B', str(launcher)] + list(arguments)
    command += ['--data-dir', str(Path(data_dir).expanduser().resolve()), '--no-import']
    os.execv(sys.executable, command)


def main(argv=None):
    original_arguments = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(description='在终端跟打、默写和复习雅思单词；无需网页或模型。')
    data_home = Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local' / 'share'))
    parser.add_argument('--data-dir', type=Path, default=default_data_dir(), help='当前版本独立的进度目录')
    parser.add_argument('--no-import', action='store_true', help='首次运行时不复制旧版本或 MCP 进度')
    parser.add_argument('--mode', choices=('copy', 'recall'), help='启动时使用跟打或默写模式')
    parser.add_argument('--dictionary', help='词库 ID；用 --list-dictionaries 查看')
    parser.add_argument('--chapter', type=int, help='从第几章开始（编号从 1 起）')
    parser.add_argument('--list-dictionaries', action='store_true', help='列出英语与雅思词库后退出')
    parser.add_argument('--version', action='store_true', help='显示当前运行版本')
    parser.add_argument('--no-update', action='store_true', help='本次使用原版本，不跟随已启用的更新，也不自动检查')
    parser.add_argument('--update-status', action='store_true', help='只读显示更新设置和已安装版本，不联网')
    parser.add_argument('--auto-update-check', choices=('on', 'off'), help='启动时后台检查稳定版本；可配合 --configure 保存')
    parser.add_argument('--review', action='store_true', help='启动时进入到期复习')
    parser.add_argument('--learn', action='store_true', help='直接学习，暂不自动进入复习')
    parser.add_argument('--stats', action='store_true', help='显示本版本学习统计后退出')
    parser.add_argument('--review-intervals', help='复习间隔天数，例如 1,3,7,14,30')
    parser.add_argument('--question-mode', choices=list(MODE_LABELS), help='学习题型；与 --review 同用时设置复习题型')
    parser.add_argument('--daily', action='store_true', help='开始今日的一小组任务，优先到期复习')
    parser.add_argument('--incremental', action='store_true', help='进入增量练习：已学词的例句与搭配')
    parser.add_argument('--extra-after-daily', choices=('on', 'off'), help='每日任务完成后自动增量练习，默认on')
    parser.add_argument('--examples-during-learning', choices=('on', 'off'), help='在混合学习中加入例句与搭配，默认off')
    parser.add_argument('--configure', action='store_true', help='保存目标/提醒设置后退出')
    parser.add_argument('--new-goal', type=int, help='每日新词目标')
    parser.add_argument('--review-goal', type=int, help='每日复习词目标')
    parser.add_argument('--batch-size', type=int, help='每组词数，1–200')
    parser.add_argument('--reminder-minutes', type=int, help='程序内提醒间隔分钟，0关闭；后台开启时须大于0')
    parser.add_argument('--reminders', choices=('enable', 'disable', 'status'), help='明确开启、关闭或查询退出后的后台提醒')
    parser.add_argument('--notify-review', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args(original_arguments)
    update_warning = ''
    bootstrap_updater = UpdateManager(args.data_dir, VERSION)
    try:
        activated = None
        try:
            if not args.no_update:
                activated = bootstrap_updater.active_launcher()
        except (OSError, ValueError, RuntimeError) as error:
            update_warning = '已安装更新暂不可用，继续使用当前版本；可在版本与更新中重新检查。'
        if args.update_status:
            status = dict(bootstrap_updater.poll(), current_version=VERSION, auto_check=bootstrap_updater.auto_check,
                          activated_launcher=str(activated) if activated else None, warning=update_warning)
            print(json.dumps(status, ensure_ascii=False, indent=2))
            return 0
        if activated is not None:
            bootstrap_updater.close()
            restart_with_update(activated, args.data_dir, original_arguments)
        if args.version:
            print('ielts ' + VERSION)
            return 0
    finally:
        bootstrap_updater.close()
    if args.notify_review or args.reminders:
        try:
            if args.notify_review:
                result = reminders.notify_review(args.data_dir)
            elif args.reminders == 'enable':
                result = reminders.enable(args.data_dir, ROOT / 'ielts.py', args.reminder_minutes if args.reminder_minutes is not None else 30)
            elif args.reminders == 'disable':
                result = reminders.disable()
            else:
                result = reminders.status()
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        except (OSError, ValueError, sqlite3.Error, subprocess.SubprocessError) as error:
            print(f'提醒操作失败：{error}', file=sys.stderr)
            return 1
    try:
        catalog = Catalog(ROOT / 'data' / 'catalog.json')
        if args.list_dictionaries:
            for entry in catalog.entries:
                chapters = (entry['word_count'] + catalog.chapter_size - 1) // catalog.chapter_size
                print(f"{entry['id']:<18} {entry['name']}  {entry['word_count']} 词 / {chapters} 章")
            return 0
    except (OSError, ValueError) as error:
        parser.exit(2, f'词库目录读取失败：{error}\n')
    if not (args.stats or args.configure) and (not sys.stdin.isatty() or not sys.stdout.isatty()):
        parser.exit(2, '请在交互终端运行 ielts（不能通过管道启动）。\n')
    if not (args.stats or args.configure) and os.environ.get('TERM', '') in ('', 'dumb'):
        parser.exit(2, '终端未声明可用的 TERM；请在 Codex 底部终端或普通终端运行。\n')
    locale.setlocale(locale.LC_ALL, '')
    old_progress = None if args.no_import else data_home / 'ielts-strip' / 'progress.json'
    try:
        with StateStore(args.data_dir, import_from=old_progress) as store:
            seed_version_profile(store, disabled=args.no_import)
            update_manager = UpdateManager(args.data_dir, VERSION)
            if args.auto_update_check is not None:
                update_manager.set_auto_check(args.auto_update_check == 'on')
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
            routine = Routine(args.data_dir)
            updates = {key: getattr(args, key) for key in ('new_goal', 'review_goal', 'batch_size', 'reminder_minutes') if getattr(args, key) is not None}
            updates.update({key: getattr(args, key) == 'on' for key in ('extra_after_daily', 'examples_during_learning') if getattr(args, key) is not None})
            if updates:
                routine.update(**updates)
            if args.configure:
                print(json.dumps(dict(routine.config, auto_update_check=update_manager.auto_check), ensure_ascii=False, indent=2))
                update_manager.close()
                planner.close()
                return 0
            if args.stats:
                stats = planner.stats()
                print(json.dumps(dict(stats, goals=routine.summary(stats)), ensure_ascii=False, indent=2))
                planner.close()
                update_manager.close()
                return 0
            app = TerminalStudy(session, store, library=library, planner=planner, routine=routine, update_manager=update_manager)
            app.skip_update_check = args.no_update
            if update_warning:
                app.notice = update_warning
            if args.question_mode:
                app.picker_scope = 'review_mode' if args.review else 'learn_mode'
                candidate = dict(app.practice_settings)
                candidate[app.picker_scope] = args.question_mode
                store._atomic_write(app.practice_settings_path, candidate)
                app.practice_settings = candidate
                if args.question_mode in ('copy', 'recall') and not args.review and session.state['mode'] != args.question_mode:
                    session.toggle_mode()
                    library.save()
            elif args.mode:
                candidate = dict(app.practice_settings, learn_mode=args.mode)
                store._atomic_write(app.practice_settings_path, candidate)
                app.practice_settings = candidate
            app.force_learning, app.force_review = args.learn, args.review
            app.force_daily = args.daily
            app.force_incremental = args.incremental
            try:
                curses.wrapper(app.run)
            finally:
                app.pronouncer.close()
                planner.close()
                update_manager.close()
        if app.update_restart_path is not None:
            restart_with_update(app.update_restart_path, args.data_dir, app.update_restart_args)
        print('进度已保存。下次运行 ielts 可继续。')
        return 0
    except (OSError, ValueError, RuntimeError, curses.error, sqlite3.Error, subprocess.SubprocessError) as error:
        print(f'启动或保存失败：{error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
