"""可暂停的复习轮次：先回忆，错词补练，再延后重测。"""

import copy
import uuid
import re


def normal(text):
    return ' '.join(str(text).strip().casefold().split())


class PracticeRound:
    def __init__(self, catalog, planner, cards, mode='recall', context='review', provider=None, checker=None, examples=False):
        self.catalog, self.planner = catalog, planner
        self.mode, self.context = mode, context
        self.examples = bool(examples)
        self.sequential = context == 'learn' and mode not in ('cloze', 'collocation')
        if context == 'extra' or mode == 'incremental':
            self.sequential = False
        self.daily = False
        self.provider, self.checker = provider, checker
        self.queue, self._maps = [], {}
        seen = set()
        for item in cards[:200]:
            ref = (item['dictionary_id'], item['word'])
            if ref in seen or ref[0] not in catalog.by_id:
                continue
            if ref[1] not in self._book(ref[0]):
                continue
            seen.add(ref)
            self.queue.append({'dictionary_id': ref[0], 'word': ref[1], 'retry': 0,
                               'lesson_step': 0, 'lesson_visits': 0})
        self.initial_count = len(self.queue)
        self.answered = self.correct = self.errors = self.skipped = 0
        self.current = None
        self.question = {}
        self.phase, self.draft, self.message = 'answer', '', ''
        self.audio_hint = False
        self.retry_queued = False
        self.event_id = ''
        self._advance()

    def _book(self, identifier):
        if identifier not in self._maps:
            self._maps[identifier] = {w['name']: w for w in self.catalog.words_for(identifier)}
        return self._maps[identifier]

    @property
    def word(self):
        return self._book(self.current['dictionary_id'])[self.current['word']] if self.current else None

    @property
    def finished(self):
        return self.current is None

    @property
    def remaining(self):
        return len(self.queue) + (0 if self.finished else 1)

    @property
    def allows_auto_audio(self):
        return self.phase != 'answer' or self.question.get('mode') in ('copy', 'en_to_zh')

    def _advance(self):
        self.current = self.queue.pop(0) if self.queue else None
        self.phase, self.draft = 'answer', ''
        self.audio_hint, self.retry_queued = False, False
        self.event_id = uuid.uuid4().hex
        if self.current:
            word = self.word
            requested_mode = self._lesson_mode() if self.context == 'learn' and self.mode == 'mixed' else self.mode
            self.current['lesson_visits'] = self.current.get('lesson_visits', 0) + 1
            if self.provider:
                self.question = self.provider(self.current['dictionary_id'], word['name'], requested_mode)
            else:
                self.question = {'mode': 'recall', 'word': word['name'], 'answer': word['name'], 'prompt': '；'.join(word['trans']), 'options': [], 'correct_indices': []}
            if self.context == 'learn' and self.mode == 'mixed' and self.examples and self.question.get('skip_example'):
                self._queue_lesson_stage()
                self._advance()
                return
            self.message = self.question.get('reason') or ('请按 1–4 选择答案。' if self.question.get('options') else '请回忆答案；Enter 检查。')
        else:
            self.question = {}
            self.message = f'本轮结束：已答 {self.answered}，答对 {self.correct}，需再练 {self.errors}。'

    def _lesson_mode(self):
        """混合学习的固定阶段；阶段由轮次状态推进，不依赖全局历史统计。"""
        stages = ('copy', 'en_to_zh', 'zh_to_en', 'recall')
        if self.examples:
            stages += ('cloze', 'collocation')
        return stages[min(self.current.get('lesson_step', 0), len(stages) - 1)]

    def _queue_lesson_stage(self):
        if self.context != 'learn' or self.mode != 'mixed':
            return
        step = self.current.get('lesson_step', 0)
        max_step = 5 if self.examples else 3
        if step >= max_step:
            return
        again = dict(self.current, lesson_step=step + 1, lesson_visits=0)
        if any(ref.get('dictionary_id') == again['dictionary_id'] and
               ref.get('word') == again['word'] and
               ref.get('lesson_step', -1) == again['lesson_step'] and
               ref.get('retry', 0) == again.get('retry', 0) for ref in self.queue):
            return
        self.queue.insert(min(3, len(self.queue)), again)

    def set_draft(self, value):
        if self.phase != 'done':
            self.draft = str(value)[:300]

    def _note(self, mode, correct, hinted, suffix, context=None):
        return self.planner.note(self.current['dictionary_id'], self.current['word'], mode, correct, hinted=hinted, context=context or self.context, event_id=self.event_id + suffix)

    def _grade_context(self, mode):
        if (self.context == 'learn' and self.mode == 'mixed' and self.examples and
                mode in ('cloze', 'collocation')):
            return 'extra'
        return self.context

    def _queue_retry(self):
        if self.retry_queued:
            return
        self.retry_queued = True
        # 错题重测沿用原有的至少隔三题规则；轮末不足时交给 planner 的十分钟排期。
        if self.current['retry'] < 2 and len(self.queue) >= 3:
            again = dict(self.current, retry=self.current['retry'] + 1)
            self.queue.insert(3, again)

    def submit(self):
        if self.finished:
            return 'finished'
        if self.phase == 'done':
            self._advance()
            return 'next'
        if not normal(self.draft):
            self.message = '先输入答案。'
            return 'empty'
        if self.phase == 'repair':
            if normal(self.draft) != normal(self.word['name']):
                self.message = '请照着答案完整输入一次。'
                return 'repair_wrong'
            self._note('copy', True, True, '-repair', context='repair')
            self._queue_retry()
            self.phase = 'done'
            self.message = '补练完成。隔几词重测；本轮不足时留到十分钟后。'
            return 'repaired'
        mode = self.question['mode']
        if self.question.get('options'):
            if self.draft.strip() not in [str(i + 1) for i in range(len(self.question['options']))]:
                self.message = '请选择有效的选项编号。'
                return 'invalid_choice'
        correct = self.checker(self.question, self.draft) if self.checker else normal(self.draft) == normal(self.question['answer'])
        self._note(mode, correct, self.audio_hint, '-grade', context=self._grade_context(mode))
        self.answered += 1
        if correct and not self.audio_hint:
            self.correct += 1
            self.phase = 'done'
            # 以实际生成题型为准：choice 无法生成而回退 recall 时，该词已完成本轮。
            is_example_boundary = (mode == 'recall' and self.examples and
                                   self.current.get('lesson_step', 0) == 3)
            is_collocation_boundary = (mode == 'cloze' and self.examples and
                                       self.current.get('lesson_step', 0) == 4)
            if self.mode == 'mixed' and self.context == 'learn' and (mode in ('copy', 'en_to_zh', 'zh_to_en') or is_example_boundary or is_collocation_boundary):
                self._queue_lesson_stage()
            self.message = '回答正确。Enter 继续。'
            return 'correct'
        if correct:
            self.errors += 1
            self.phase = 'done'
            self._queue_retry()
            self.message = '借助提示完成；这个词会较早再复习。'
            return 'assisted'
        self.errors += 1
        self.phase, self.draft = 'repair', ''
        self.message = '这次没答对。看答案并完整跟打一遍。'
        return 'wrong'

    def reveal(self):
        if self.finished or self.phase != 'answer':
            return
        self._note(self.question['mode'], False, True, '-grade', context=self._grade_context(self.question['mode']))
        self.answered += 1
        self.errors += 1
        self.phase, self.draft = 'repair', ''
        self.message = '答案已显示；完整跟打一遍，稍后重测。'

    def mark_audio_hint(self):
        if self.phase == 'answer' and self.question.get('mode') not in ('listening', 'copy', 'en_to_zh'):
            self.audio_hint = True

    def skip(self):
        if not self.finished:
            self.skipped += 1
            self._advance()

    def snapshot(self):
        return copy.deepcopy({'version': 3, 'active': not self.finished, 'mode': self.mode, 'context': self.context, 'examples': self.examples, 'sequential': self.sequential, 'daily': self.daily, 'queue': self.queue, 'current': self.current, 'question': self.question, 'phase': self.phase, 'draft': self.draft, 'audio_hint': self.audio_hint, 'retry_queued': self.retry_queued, 'event_id': self.event_id, 'initial_count': self.initial_count, 'answered': self.answered, 'correct': self.correct, 'errors': self.errors, 'skipped': self.skipped, 'message': self.message})

    @classmethod
    def restore(cls, catalog, planner, value, provider=None, checker=None):
        if not isinstance(value, dict) or value.get('version') not in (1, 2, 3) or not value.get('active'):
            return None
        if value.get('phase') not in ('answer', 'repair', 'done') or not isinstance(value.get('draft'), str) or len(value['draft']) > 300:
            raise ValueError('复习轮次记录无效')
        modes = {'copy', 'recall', 'en_to_zh', 'zh_to_en', 'listening', 'cloze', 'collocation', 'mixed'}
        if value.get('mode') not in modes | {'incremental'} or value.get('context') not in ('review', 'learn', 'extra'):
            raise ValueError('复习模式记录无效')
        if not isinstance(value.get('event_id'), str) or not re.fullmatch(r'[a-f0-9]{32}', value['event_id']):
            raise ValueError('复习事件标识无效')
        if any(type(value.get(key)) is not int or value[key] < 0 for key in ('initial_count', 'answered', 'correct', 'errors', 'skipped')):
            raise ValueError('复习计数记录无效')
        if any(type(value.get(key)) is not bool for key in ('audio_hint', 'retry_queued')) or not isinstance(value.get('queue'), list):
            raise ValueError('复习状态记录无效')
        question = value.get('question')
        if not isinstance(question, dict) or question.get('mode') not in modes - {'mixed'} or not isinstance(question.get('prompt'), str) or not isinstance(question.get('answer'), str):
            raise ValueError('题目记录无效')
        if not isinstance(question.get('options'), list) or len(question['options']) > 4 or not all(isinstance(x, str) for x in question['options']):
            raise ValueError('题目选项无效')
        if not isinstance(question.get('correct_indices'), list) or not all(type(x) is int and 0 <= x < len(question['options']) for x in question['correct_indices']):
            raise ValueError('题目答案索引无效')
        refs = [value.get('current')] + value.get('queue', [])
        if len(refs) > 600:
            raise ValueError('复习轮次过大')
        for ref in refs:
            if not isinstance(ref, dict) or ref.get('dictionary_id') not in catalog.by_id or type(ref.get('retry', 0)) is not int or not 0 <= ref.get('retry', 0) <= 2:
                raise ValueError('复习词条记录无效')
            if type(ref.get('lesson_step', 0)) is not int or not 0 <= ref.get('lesson_step', 0) <= 5:
                raise ValueError('学习阶段记录无效')
            if type(ref.get('lesson_visits', 0)) is not int or not 0 <= ref.get('lesson_visits', 0) <= 4:
                raise ValueError('学习阶段访问记录无效')
            if ref.get('word') not in {w['name'] for w in catalog.words_for(ref['dictionary_id'])}:
                raise ValueError('复习词条已不在词库中')
        obj = cls(catalog, planner, [], mode=value['mode'], context=value['context'], provider=provider, checker=checker, examples=value.get('examples', False))
        obj.sequential = bool(value.get('sequential', obj.sequential))
        obj.daily = bool(value.get('daily', False))
        obj.examples = bool(value.get('examples', False))
        for name in ('queue', 'current', 'question', 'phase', 'draft', 'audio_hint', 'retry_queued', 'event_id', 'initial_count', 'answered', 'correct', 'errors', 'skipped', 'message'):
            setattr(obj, name, copy.deepcopy(value[name]))
        for ref in obj.queue + ([obj.current] if obj.current else []):
            ref.setdefault('retry', 0)
            if 'lesson_step' not in ref:
                ref['lesson_step'] = 0
                if obj.mode == 'mixed':
                    # v1 mixed 快照没有阶段字段；当前题目可由题型精确恢复，
                    # 队列中的错题至少不应退回到 copy。
                    if ref is obj.current:
                        ref['lesson_step'] = {'copy': 0, 'en_to_zh': 1,
                                              'zh_to_en': 2, 'recall': 3}.get(
                                                  obj.question.get('mode'), 0)
                    elif ref.get('retry', 0) > 0:
                        ref['lesson_step'] = 1
            ref.setdefault('lesson_visits', 0)
        # 若进度写入前进程退出，依据已提交的事件恢复阶段，防止重复评分。
        row = planner.db.execute('SELECT correct,hinted FROM events WHERE event_id=?', (obj.event_id + '-grade',)).fetchone()
        repair = planner.db.execute('SELECT 1 FROM events WHERE event_id=?', (obj.event_id + '-repair',)).fetchone()
        if row and obj.phase == 'answer':
            obj.phase = 'done' if row[0] else 'repair'
            obj.draft = ''
            obj.answered += 1
            if row[0] and not row[1]:
                obj.correct += 1
            else:
                obj.errors += 1
            restore_example_boundary = (obj.question.get('mode') == 'recall' and obj.examples and
                                        obj.current.get('lesson_step', 0) == 3)
            restore_collocation_boundary = (obj.question.get('mode') == 'cloze' and obj.examples and
                                            obj.current.get('lesson_step', 0) == 4)
            if row[0] and not row[1] and obj.mode == 'mixed' and obj.context == 'learn' and (obj.question.get('mode') in ('copy', 'en_to_zh', 'zh_to_en') or restore_example_boundary or restore_collocation_boundary):
                obj._queue_lesson_stage()
            elif row[1]:
                obj._queue_retry()
            obj.message = '已恢复上次评分；Enter 继续。' if obj.phase == 'done' else '已恢复上次错题；请跟打补练。'
        if repair and obj.phase == 'repair':
            obj.phase = 'done'
            obj._queue_retry()
            obj.message = '补练已完成；Enter 继续。'
        return obj
