# 词库维护

V3 的目录文件是 `apps/v3.2.0/data/catalog.json`，词库 JSON 位于同一 `data/` 目录或其子目录。F7/Ctrl+D 可在 CLI 内选择词库，F8/Ctrl+K 可选择章节；命令行也可用 `--list-dictionaries`、`--dictionary ID` 和 `--chapter N`。

## 当前五本词库

| ID | 名称 | 有效词条 |
|---|---|---:|
| `ielts` | 雅思通用 | 3,575 |
| `cet4` | 英语四级 | 2,607 |
| `cet6` | 英语六级 | 2,345 |
| `ielts-listening` | 雅思听力场景 | 1,171 |
| `ielts-expanded` | 雅思扩展（顺序） | 9,388 |

合计 19,086；默认 `chapter_size` 为 20。

## 文件约束

`Catalog` 要求目录 JSON 的 `version` 为整数 `1`，`chapter_size` 为 1–500 的整数，`dictionaries` 为非空数组，`default_dictionary` 必须存在。每个条目需要唯一的 `id`（仅小写字母、数字和连字符，且以字母或数字开头）、非空 `name`、`path`、正整数 `word_count` 和 `language: "en"`。`path` 解析后必须位于 `catalog.json` 所在 `data/` 目录内。

词库文件必须是 JSON 数组，单文件上限为 5 MiB。每个有效元素至少需要非空字符串 `name` 和字符串数组 `trans`；重复或不合格元素会被跳过。实际加载时检查 SHA-256（若目录提供 `sha256`）和有效词条数是否等于 `word_count`。

## 添加自定义词库

建议使用新 ID 和新文件，不直接改写现有 ID 的原始字库。这样可以保留原 profile、来源 provenance 和原始数据的可追溯性；个人词库的进度也会与内置词库隔离。不要填写假 SHA-256、假来源 URL 或与文件不符的 `word_count`。

例如，在 `apps/v3.2.0/data/custom/` 创建自编的 `travel-words.json`：

```json
[
  {"name": "journey", "trans": ["n. 旅行；旅程"]},
  {"name": "reserve", "trans": ["v. 预订；保留", "n. 储备"]}
]
```

计算实际文件摘要后，在 `catalog.json` 的 `dictionaries` 中加入条目，并按需把 `default_dictionary` 改为新 ID：

```json
{
  "id": "travel-words",
  "name": "旅行词汇",
  "description": "自编的旅行词汇与释义",
  "path": "custom/travel-words.json",
  "word_count": 2,
  "language": "en",
  "sha256": "<实际文件的 SHA-256>"
}
```

自编内容不需要编造来源 URL；引用外部数据时附上真实来源与许可证。以下脚本只读取新文件并打印准确目录条目，不写入 `catalog.json`。在仓库根目录保存并运行脚本，再将输出加入 `dictionaries` 数组：

```python
import hashlib
import json
import sys
from pathlib import Path
app = Path('apps/v3.2.0')
sys.path.insert(0, str(app))
from study import load_words
path = app / 'data/custom/travel-words.json'
print(json.dumps({
    'id': 'travel-words', 'name': '旅行词汇',
    'description': '自编的旅行词汇与释义',
    'path': 'custom/travel-words.json', 'language': 'en',
    'word_count': len(load_words(path)),
    'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
}, ensure_ascii=False, indent=2))
```

更新后实际加载新词库，才能验证摘要和有效词数。`--doctor` 与 `--list-dictionaries` 不替代这一步：

```sh
python3 -c "import sys; sys.path.insert(0, 'apps/v3.2.0'); from library import Catalog; c=Catalog('apps/v3.2.0/data/catalog.json'); print(len(c.words_for('travel-words')))"
python3 launcher.py --dictionary travel-words --learn
```

上例结果应为 `2`。Windows 使用 `py -3` 替换 `python3`。向项目提交新词库时同步更新目录相关测试与来源记录；私人词库不要混入公共发布包。

自定义词库的每个 profile 会写入当前数据目录的多词库进度文件。更换同一 ID 的内容会触发摘要或词数校验失败，也可能使已有进度语义不再对应；需要替换时使用新 ID，并保留旧文件和来源记录。
