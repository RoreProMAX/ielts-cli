#!/usr/bin/env python3
"""V2 题型逻辑：只处理题目生成与判分，不保存学习进度。"""

import json
import random
import re
from pathlib import Path


_SPACE_RE = re.compile(r"\s+")
_BLANK_RE = re.compile(r"____")
_PUNCT_RE = re.compile(r"[^\w\u4e00-\u9fff]+", re.UNICODE)
_MEANING_SPLIT_RE = re.compile(r"[,，、;；]+")
_POS_PREFIX_RE = re.compile(r"^(?:aux|prep|pron|conj|adj|adv|num|vt|vi|n|v)(?:\.\s*|\s+|(?=[\u4e00-\u9fff]))", re.I)
_CHOICE_MODES = {"en_to_zh", "zh_to_en"}
_ALL_MODES = _CHOICE_MODES | {"recall", "listening", "cloze", "collocation"}


def _normal(value):
    return _SPACE_RE.sub(" ", str(value).strip().casefold())


def _meaning_key(value):
    return _PUNCT_RE.sub("", _normal(value))


def _meaning_parts(values):
    parts = []
    for value in values:
        for part in _MEANING_SPLIT_RE.split(value):
            part = _POS_PREFIX_RE.sub("", part).strip()
            if part:
                parts.append(part)
    return _unique_text(parts)


def _unique_text(values):
    result = []
    seen = set()
    for value in values:
        if not isinstance(value, str):
            continue
        key = _meaning_key(value)
        if key and key not in seen:
            seen.add(key)
            result.append(value.strip())
    return result


def display_meanings(word):
    """显示词库中全部释义，保留词性和义项标点；拆义仅用于干扰项比较。"""
    values = word.get('trans', [])
    if not isinstance(values, list):
        return ''
    return '；'.join(_unique_text(_SPACE_RE.sub(' ', value).strip() for value in values if isinstance(value, str)))


class QuestionFactory:
    def __init__(self, words, content_path=None, rng=None):
        if not isinstance(words, list) or not words:
            raise ValueError("词库不能为空")
        self.words = []
        seen_names = set()
        for word in words:
            if not isinstance(word, dict) or not isinstance(word.get("name"), str) or not word["name"]:
                continue
            name_key = _normal(word["name"])
            if name_key in seen_names:
                continue
            seen_names.add(name_key)
            self.words.append(word)
        if not self.words:
            raise ValueError("词库没有有效单词")
        self.by_name = {_normal(word["name"]): word for word in self.words}
        self.rng = rng or random.Random()
        self.content = {}
        if content_path is not None:
            payload = json.loads(Path(content_path).read_text(encoding="utf-8"))
            if not isinstance(payload, dict) or payload.get("version") != 1 or not isinstance(payload.get("items"), list):
                raise ValueError("题型内容格式无效")
            for item in payload["items"]:
                if not isinstance(item, dict) or not isinstance(item.get("word"), str):
                    continue
                if _normal(item["word"]) not in self.by_name:
                    continue
                self.content[_normal(item["word"])] = item

    def _word(self, word_name):
        word = self.by_name.get(_normal(word_name))
        if word is None:
            raise ValueError("词库中不存在该单词: %s" % word_name)
        return word

    def _meanings(self, word):
        return _meaning_parts(word.get("trans", []))

    def _fallback(self, word, requested, prompt=None, reason=None, mode="recall"):
        return {
            "mode": mode,
            "prompt": prompt or display_meanings(word) or "请跟打当前单词。",
            "options": [],
            "correct_indices": [],
            "answer": word["name"],
            "word": word["name"],
            "reason": reason or ("无法为 %s 找到至少三个不冲突的干扰项，回退为默写。" % requested),
        }

    def _shuffle_options(self, answer, distractors):
        options = [answer] + distractors[:3]
        self.rng.shuffle(options)
        return options, [options.index(answer)]

    def _choice(self, word, mode):
        target_name = _normal(word["name"])
        target_meanings = {_meaning_key(value) for value in self._meanings(word)}
        if not target_meanings:
            return self._fallback(word, mode, reason="该词没有中文释义，改为跟打练习，不计入默写熟练度。", mode="copy")
        candidates = []
        seen = set(target_meanings if mode == "en_to_zh" else {target_name})
        for other in self.words:
            other_name = _normal(other["name"])
            if other_name == target_name:
                continue
            if mode == "en_to_zh":
                for meaning in self._meanings(other):
                    key = _meaning_key(meaning)
                    if key not in seen and key not in target_meanings:
                        seen.add(key)
                        candidates.append(meaning)
                        break
            else:
                other_meanings = {_meaning_key(value) for value in self._meanings(other)}
                if other_meanings & target_meanings or other_name in seen:
                    continue
                seen.add(other_name)
                candidates.append(other["name"])
        if len(candidates) < 3:
            prompt = display_meanings(word) or None
            reason = None if prompt else "该词没有中文释义，改为跟打练习，不计入默写熟练度。"
            return self._fallback(word, mode, prompt, reason)
        answer = self._meanings(word)[0] if mode == "en_to_zh" else word["name"]
        self.rng.shuffle(candidates)
        options, correct = self._shuffle_options(answer, candidates)
        return {
            "mode": mode,
            "prompt": word["name"] if mode == "en_to_zh" else display_meanings(word),
            "options": options,
            "correct_indices": correct,
            "answer": answer,
            "word": word["name"],
        }

    def make(self, word_name, mode):
        if mode not in _ALL_MODES:
            raise ValueError("未知题型: %s" % mode)
        word = self._word(word_name)
        if mode in _CHOICE_MODES:
            return self._choice(word, mode)
        item = self.content.get(_normal(word["name"]))
        if mode in {"cloze", "collocation"}:
            if item is None:
                return self._fallback(word, mode, reason="缺少可用的 %s 内容，回退为默写。" % mode)
            field = "sentence" if mode == "cloze" else "collocation_prompt"
            prompt = item.get(field)
            if not isinstance(prompt, str) or len(_BLANK_RE.findall(prompt)) != 1:
                return self._fallback(word, mode, reason="缺少可用的 %s 内容，回退为默写。" % field)
            answer = word["name"]
            return {"mode": mode, "prompt": prompt, "options": [], "correct_indices": [], "answer": answer, "word": word["name"]}
        if mode == "listening":
            return {"mode": mode, "prompt": "听写你听到的单词。", "options": [], "correct_indices": [], "answer": word["name"], "word": word["name"]}
        meanings = self._meanings(word)
        if not meanings:
            return self._fallback(word, mode, reason="该词没有中文释义，改为跟打练习，不计入默写熟练度。", mode="copy")
        return {"mode": "recall", "prompt": display_meanings(word), "options": [], "correct_indices": [], "answer": word["name"], "word": word["name"]}


def is_correct(question, response):
    if not isinstance(question, dict):
        return False
    if isinstance(response, int) and not isinstance(response, bool):
        return 1 <= response <= 4 and response - 1 in question.get("correct_indices", [])
    text = _normal(response)
    if not text:
        return False
    if text in {"1", "2", "3", "4"} and question.get("correct_indices"):
        return int(text) - 1 in question["correct_indices"]
    answer = _normal(question.get("answer", ""))
    if text == answer:
        return True
    return any(text == _normal(question.get("options", [])[index]) for index in question.get("correct_indices", []) if 0 <= index < len(question.get("options", [])))


def _familiar(stats, mode):
    value = stats.get(mode, {}) if isinstance(stats, dict) else {}
    if not isinstance(value, dict):
        return False
    raw_correct = value.get('correct', 0)
    correct = value.get("unassisted_correct", raw_correct)
    wrong = value.get("wrong", 0)
    attempts = value.get("attempts", value.get("total", value.get("seen", 0)))
    if not isinstance(correct, int) or not isinstance(wrong, int):
        return False
    if not isinstance(attempts, int):
        return False
    if attempts == 0:
        attempts = raw_correct + wrong
    return attempts >= 3 and correct >= 3 and correct / attempts >= 0.8


def choose_mode(stats, has_content=False, allow_listening=False):
    """根据题型表现选下一题；每层一次无提示答对即可前进。

    ``wrong``/``recent_wrong`` 让弱项重新出现，但不会把词永久锁在某一层。
    听写是有副作用的自动音频题，必须由调用方显式允许。
    """
    def weak(mode):
        value = stats.get(mode, {}) if isinstance(stats, dict) else {}
        if not isinstance(value, dict):
            return True
        correct = value.get("unassisted_correct", value.get("correct", 0))
        wrong = value.get("wrong", 0)
        attempts = value.get("attempts", value.get("total", value.get("seen", 0)))
        if not isinstance(correct, int) or not isinstance(wrong, int):
            return True
        if isinstance(value.get("recent_wrong"), bool) and value["recent_wrong"]:
            return True
        if not isinstance(attempts, int) or attempts == 0:
            attempts = correct + wrong
        if correct < 1:
            return True
        return wrong > correct or (attempts >= 2 and correct / float(attempts) < 0.8)

    if weak("en_to_zh"):
        return "en_to_zh"
    if weak("zh_to_en"):
        return "zh_to_en"
    if weak("recall"):
        return "recall"
    candidates = []
    if allow_listening:
        candidates.append("listening")
    def attempts(mode):
        value = stats.get(mode, {}) if isinstance(stats, dict) else {}
        if not isinstance(value, dict):
            return 0
        raw = value.get("attempts", value.get("total", value.get("seen")))
        if isinstance(raw, int):
            return raw
        correct = value.get("correct", 0)
        wrong = value.get("wrong", 0)
        return correct + wrong if isinstance(correct, int) and isinstance(wrong, int) else 0
    if has_content:
        candidates.extend(["cloze", "collocation"])
    return min(candidates, key=attempts) if candidates else "recall"
