# IELTS CLI

A lightweight, general-purpose terminal vocabulary practice tool: use it in a regular terminal, an Agent Desktop CLI window, or a spare moment during development or study. It is an independent Python implementation inspired by Qwerty typing vocabulary practice, not an official upstream fork.

The default application is `apps/v3.2.0`; `apps/v1.0.0` and `apps/v2.0.0` remain available. Five dictionaries contain 19,086 valid entries, organized in 20-word chapters.

## Quick start

Download a ZIP, or copy the repository's actual URL:

```sh
git clone <repository-url>
cd ielts-cli-public
```

Python 3.10+ is required. On Windows install `windows-curses==2.4.2`:

```powershell
py -3 -m pip install -r requirements-windows.txt
```

Start `start.bat` on Windows, double-click `start.command` on macOS, or run `sh start.sh` on Linux. The default is V3.2:

```sh
python3 launcher.py --app-version 3
```

It is designed to run in an Agent Desktop bottom terminal. The terminal needs at least 40 columns and 6 rows. If the host captures F10, use Esc or `?` with an empty input to open the menu.

## Features

- Typing (`copy`), English-to-Chinese (`en_to_zh`), Chinese-to-English (`zh_to_en`), and recall (`recall`); V3 uses mixed learning by default and records each mode separately.
- Complete stored Chinese meanings in Chinese-to-English and recall, with paging for long meanings.
- F7/Ctrl+D selects a dictionary, F8/Ctrl+K selects a chapter, F9/Ctrl+B starts or pauses due review, and F11/Ctrl+Y opens today's task.
- Incremental examples and collocations start by default after the daily task. Current material covers 124 words; examples during learning are off by default and configurable from the Esc learning-plan menu.
- Incremental practice does not inflate normal new-word or review goals; when fewer items are due, the actual due count is used.
- Text practice works offline. Pronunciation requires `ffplay`; the first audio request for a word needs network access and later uses the local cache.

## Checks and data

Use `--doctor` in a source checkout to inspect Python, curses, SQLite, application files, and resolved data paths:

```sh
python3 launcher.py --doctor
```

Use `--verify` only with a Release bundle containing its manifest. A source checkout does not contain `BUNDLE_MANIFEST.json`:

```sh
python3 launcher.py --verify
```

`--portable` stores progress and audio cache under `user-data/` beside the launcher. Do not commit personal progress, caches, logs, accounts, or API keys.

## Documentation and status

See [FEATURES](docs/FEATURES.md), [INTERFACES](docs/INTERFACES.md), [DICTIONARIES](docs/DICTIONARIES.md), [STATUS](docs/STATUS.md), the [deployment guide](docs/部署指南.md), and the [user manual](docs/使用手册.md).

Linux has passed 120 V3 tests, 6-row terminal and relocation checks, and offline container validation. The GitHub remote has not completed its push yet. Windows and macOS adaptations exist, but real Desktop interaction has not been accepted and cross-platform smoke CI is pending. Known limitations are documented in [STATUS](docs/STATUS.md).

The project code is GPL-3.0 under [LICENSE](LICENSE). The dictionaries have separate MIT notices, fixed-commit provenance, source URLs, and SHA-256 values documented in [THIRD_PARTY_NOTICES](THIRD_PARTY_NOTICES.md) and each version's `data/source.json`.

For coding agents: [deployment and optimization guide](docs/FOR_AGENTS.md). This is a maintenance guide, not a restriction on who can use the application.
