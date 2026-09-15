"""英语词库目录及分词库进度；旧版 progress.json 保留为迁移来源。"""

import copy
import hashlib
import json
from pathlib import Path
import re

from study import StudySession, fresh_state, load_words, _require_state


ID_PATTERN = re.compile(r'^[a-z0-9][a-z0-9-]*$')
MAX_LIBRARY_BYTES = 30 * 1024 * 1024


class Catalog:
    def __init__(self, path):
        self.path = Path(path).resolve()
        self.base = self.path.parent
        payload = json.loads(self.path.read_text(encoding='utf-8'))
        if not isinstance(payload, dict) or type(payload.get('version')) is not int or payload['version'] != 1:
            raise ValueError('词库目录版本无效')
        self.chapter_size = payload.get('chapter_size', 20)
        if type(self.chapter_size) is not int or not 1 <= self.chapter_size <= 500:
            raise ValueError('每章词数无效')
        self.entries = payload.get('dictionaries')
        if not isinstance(self.entries, list) or not self.entries:
            raise ValueError('没有可选择的英语词库')
        self.by_id, self._paths, self._words = {}, {}, {}
        for entry in self.entries:
            if not isinstance(entry, dict) or not isinstance(entry.get('id'), str) or not ID_PATTERN.fullmatch(entry['id']):
                raise ValueError('词库标识无效')
            identifier = entry['id']
            if identifier in self.by_id or entry.get('language') != 'en':
                raise ValueError('词库目录只允许不重复的英语词库')
            if not isinstance(entry.get('name'), str) or not entry['name'] or not isinstance(entry.get('path'), str):
                raise ValueError('词库名称或路径无效')
            if type(entry.get('word_count')) is not int or entry['word_count'] < 1:
                raise ValueError('词库词数无效')
            file_path = (self.base / entry['path']).resolve()
            try:
                file_path.relative_to(self.base)
            except ValueError:
                raise ValueError('词库路径超出数据目录')
            self.by_id[identifier] = entry
            self._paths[identifier] = file_path
        self.default_dictionary = payload.get('default_dictionary')
        if self.default_dictionary not in self.by_id:
            raise ValueError('默认词库不存在')

    def words_for(self, identifier):
        if identifier not in self.by_id:
            raise ValueError('所选词库不存在')
        if identifier not in self._words:
            path = self._paths[identifier]
            entry = self.by_id[identifier]
            expected_hash = entry.get('sha256')
            if expected_hash and hashlib.sha256(path.read_bytes()).hexdigest() != expected_hash:
                raise ValueError('词库文件校验失败：' + entry['name'])
            words = load_words(path)
            if len(words) != entry['word_count']:
                raise ValueError('词库实际词数与目录不一致：' + entry['name'])
            self._words[identifier] = words
        return self._words[identifier]


class StudyLibrary:
    def __init__(self, catalog, store):
        self.catalog = catalog
        self.store = store
        self.path = store.path.parent / 'library.json'
        self.migration = None
        if self.path.exists():
            if self.path.stat().st_size > MAX_LIBRARY_BYTES:
                raise ValueError('多词库进度文件超过大小限制')
            payload = json.loads(self.path.read_text(encoding='utf-8'))
            self._validate_payload(payload)
            self.profiles = payload['dictionaries']
            self.active_id = payload['active_dictionary']
            self.migration = payload.get('migration')
        else:
            # 仅复制旧版进度；以后的新学习记录只写 library.json。
            legacy = copy.deepcopy(store.load())
            self.active_id = catalog.default_dictionary
            self.profiles = {self.active_id: legacy}
            if store.path.exists():
                self.migration = {'source': str(store.path), 'sha256': hashlib.sha256(store.path.read_bytes()).hexdigest()}
        self.session = StudySession(catalog.words_for(self.active_id), copy.deepcopy(self.profiles.get(self.active_id, fresh_state())), chapter_size=catalog.chapter_size)

    def _validate_payload(self, payload):
        if not isinstance(payload, dict) or type(payload.get('version')) is not int or payload['version'] != 2:
            raise ValueError('多词库进度格式无效；原文件未改动')
        if payload.get('active_dictionary') not in self.catalog.by_id:
            raise ValueError('上次使用的词库不在当前目录中')
        if not isinstance(payload.get('dictionaries'), dict):
            raise ValueError('分词库进度无效')
        for identifier, state in payload['dictionaries'].items():
            if not isinstance(identifier, str) or not ID_PATTERN.fullmatch(identifier):
                raise ValueError('进度中的词库标识无效')
            _require_state(state)
        if payload['active_dictionary'] not in payload['dictionaries']:
            raise ValueError('当前词库缺少学习记录')

    def _write(self, profiles, active_id):
        payload = {'version': 2, 'active_dictionary': active_id, 'chapter_size': self.catalog.chapter_size, 'dictionaries': profiles}
        if self.migration:
            payload['migration'] = self.migration
        if len(json.dumps(payload, ensure_ascii=False).encode('utf-8')) > MAX_LIBRARY_BYTES:
            raise ValueError('多词库进度超过大小限制')
        self.store._atomic_write(self.path, payload)

    @property
    def entry(self):
        return self.catalog.by_id[self.active_id]

    def save(self, state=None):
        state = self.session.state if state is None else state
        _require_state(state)
        profiles = dict(self.profiles)
        profiles[self.active_id] = copy.deepcopy(state)
        self._write(profiles, self.active_id)
        self.profiles = profiles

    def switch_dictionary(self, identifier):
        if identifier == self.active_id:
            return self.session
        words = self.catalog.words_for(identifier)
        new_session = StudySession(words, copy.deepcopy(self.profiles.get(identifier, fresh_state())), chapter_size=self.catalog.chapter_size)
        profiles = dict(self.profiles)
        profiles[self.active_id] = copy.deepcopy(self.session.state)
        profiles[identifier] = copy.deepcopy(new_session.state)
        # 先完成原子保存，再切换内存中的活动词库；失败时保持原界面可用。
        self._write(profiles, identifier)
        self.profiles, self.active_id, self.session = profiles, identifier, new_session
        return new_session

    def switch_chapter(self, index):
        new_session = StudySession(self.session.words, copy.deepcopy(self.session.state), chapter_size=self.catalog.chapter_size)
        new_session.select_chapter(index)
        profiles = dict(self.profiles)
        profiles[self.active_id] = copy.deepcopy(new_session.state)
        self._write(profiles, self.active_id)
        self.profiles, self.session = profiles, new_session
        return new_session

    def practiced_count(self, identifier):
        if identifier != self.active_id and identifier not in self.profiles:
            return 0
        profile = self.session.state if identifier == self.active_id else self.profiles.get(identifier, fresh_state())
        records = profile['records']
        return sum(1 for word in self.catalog.words_for(identifier) if records.get(word['name'], {}).get('correct', 0) > 0)
