"""独立保存读音开关，不改变原有单词学习进度格式。"""

import json
import os
from portable_compat import private_file
from pathlib import Path
import tempfile


class AudioSettings:
    def __init__(self, data_dir):
        self.path = Path(data_dir) / 'settings.json'
        self.auto_pronounce = False
        if self.path.exists():
            if self.path.stat().st_size > 4096:
                raise ValueError('发音设置文件过大')
            value = json.loads(self.path.read_text(encoding='utf-8'))
            if not isinstance(value, dict) or type(value.get('version')) is not int or value['version'] != 1 or type(value.get('auto_pronounce')) is not bool:
                raise ValueError('发音设置格式无效；原文件未改动')
            self.auto_pronounce = value['auto_pronounce']

    def set_auto(self, enabled):
        if type(enabled) is not bool:
            raise ValueError('自动发音开关必须是布尔值')
        value = {'version': 1, 'auto_pronounce': enabled}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix='.audio-settings-', dir=str(self.path.parent))
        try:
            private_file(fd)
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                json.dump(value, stream, ensure_ascii=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            self.auto_pronounce = enabled
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
