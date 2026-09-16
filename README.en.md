# IELTS CLI

A lightweight, general-purpose terminal vocabulary practice tool: use it in a regular terminal, an Agent Desktop CLI window, or a spare moment during development or study. It is an independent Python implementation inspired by Qwerty typing vocabulary practice, not an official upstream fork.

The default source/Beta package runs `apps/v3.3.1-beta.1`; `apps/v1.0.0`, `apps/v2.0.0`, `apps/v3.2.0`, and `apps/v3.3.0` remain available. Stable downloads remain at the [stable Release](https://github.com/RoreProMAX/ielts-cli/releases/latest); the Beta uses the separate [v3.3.1-beta.1 Release](https://github.com/RoreProMAX/ielts-cli/releases/tag/v3.3.1-beta.1). Five dictionaries contain 19,086 valid entries, organized in 20-word chapters.

## Project note

I made this small project in spare moments while waiting for an Agent to respond during vibe coding. It is still a work in progress, with unfinished features and room to improve the experience and compatibility. Feedback, suggestions, and corrections from all users are welcome.

Thank you to Qwerty and all upstream open-source projects, and to the authors and maintainers who share their code, dictionaries, and tools. See [Third-party notices](THIRD_PARTY_NOTICES.md) for sources and license details.

## Quick start

Download a [Release ZIP](https://github.com/RoreProMAX/ielts-cli/releases/latest), or clone the repository:

```sh
git clone https://github.com/RoreProMAX/ielts-cli.git
cd ielts-cli
```

Python 3.10+ is required. V3.3.1-beta.1 uses a standard-library VT output and native console input backend on Windows; `windows-curses==2.4.2` remains for older versions and comparison diagnostics:

```powershell
py -3 -m pip install -r requirements-windows.txt
```

Start `start.bat` on Windows, double-click `start.command` on macOS, or run `sh start.sh` on Linux. The default is V3.3.1-beta.1:

```sh
python3 launcher.py --app-version 3
```

Use a regular terminal or an Agent Desktop terminal pane with at least 40 columns and 6 rows. If the host captures F10, use Esc or `?` with an empty input to open the menu.

## Features

- Typing (`copy`), English-to-Chinese (`en_to_zh`), Chinese-to-English (`zh_to_en`), and recall (`recall`); V3 uses mixed learning by default and records each mode separately.
- Complete stored Chinese meanings in Chinese-to-English and recall, with paging for long meanings.
- F7/Ctrl+D selects a dictionary, F8/Ctrl+K selects a chapter, F9/Ctrl+B starts or pauses due review, and F11/Ctrl+Y opens today's task.
- Incremental examples and collocations start by default after the daily task. Current material covers 124 words; examples during learning are off by default and configurable from the Esc learning-plan menu. V3.3 can return from incremental examples to word learning.
- V3.3.1-beta.1 checks public stable Releases in the background at startup (on by default). The update channel is stable by default; Esc “Version and updates” can opt into beta. Beta accepts only newer stable or beta releases and never downgrades; confirmation is required and study sessions are not interrupted or restarted automatically. Stable users do not receive Beta releases.
- V3.3.0 and earlier have no Beta channel. Download the Beta package manually once, then explicitly enable its Beta channel to receive later Beta fixes; no intermediate version is required. `--no-update` uses the original entry version and skips checking for this run only; it does not change the setting.
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

V3.3.1-beta.1 has 174 local tests and a completed 10-step real-terminal scenario on each of four CI platforms; see [Actions run 35058831180](https://github.com/RoreProMAX/ielts-cli/actions/runs/35058831180). The Beta Windows path uses VT/native console. The V3.3.0 Windows PDCurses CJK redraw failure remains documented as a historical baseline in [STATUS](docs/STATUS.md). Fonts, DPI, IME, and real Desktop hosts still require manual acceptance.

The project code is GPL-3.0 under [LICENSE](LICENSE). The dictionaries have separate MIT notices, fixed-commit provenance, source URLs, and SHA-256 values documented in [THIRD_PARTY_NOTICES](THIRD_PARTY_NOTICES.md) and each version's `data/source.json`.

For coding agents: [deployment and optimization guide](docs/FOR_AGENTS.md). This is a maintenance guide, not a restriction on who can use the application.
