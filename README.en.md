# IELTS CLI

English | [简体中文](README.md)

An independent terminal vocabulary practice tool inspired by the idea of Qwerty typing vocabulary practice. It is useful for IELTS, CET, and general English review. The default application is `apps/v3.2.0`; `apps/v1.0.0` and `apps/v2.0.0` are retained.

## Features

- Five dictionaries with 19,086 valid entries, organized in 20-word chapters; the final chapter uses the actual remaining count.
- Daily practice automatically combines typing (`copy`), English-to-Chinese (`en_to_zh`), Chinese-to-English (`zh_to_en`), and recall (`recall`).
- Chinese-to-English and recall show the complete meanings stored in the dictionary. Long meanings can be paged with arrow keys or PageUp/PageDown.
- V3 uses mixed practice groups by default, records results per mode, and supports due reviews.
- Incremental example and collocation practice covers 124 words. It starts by default after the daily task; if fewer items are due, the actual due count is used.
- Incremental practice does not inflate normal new-word or review goals, and correct answers do not repeatedly extend the original review schedule.
- Examples during the learning flow are off by default and can be enabled from the Esc learning-plan menu.
- Text practice works offline. Pronunciation uses `ffplay`; the first request for a word needs network access, then the audio is cached locally.

## Quick start

Download a ZIP, or copy the repository's actual URL and run:

```sh
git clone https://github.com/RoreProMAX/ielts-cli.git
cd ielts-cli
```

Python 3.10 or newer is required. Windows also needs `windows-curses==2.4.2`:

```powershell
py -3 -m pip install -r requirements-windows.txt
```

Start the program with `start.bat` on Windows, `start.command` on macOS, or `sh start.sh` on Linux. The launcher defaults to V3.2:

```sh
python3 launcher.py --app-version 3
```

Use `--app-version 1` or `--app-version 2` to select an older version. The launcher interface is kept compatible across all three versions.

## Checks

Use `--doctor` to inspect Python, curses, SQLite, application files, and resolved data paths. It works in a source checkout:

```sh
python3 launcher.py --doctor
```

Use `--verify` to check SHA-256 values from a release manifest. A source checkout does not include `BUNDLE_MANIFEST.json`; run this check on a published release bundle:

```sh
python3 launcher.py --verify
```

The options can be combined. On Windows, use `py -3` or `start.bat` as appropriate.

## Data and portable mode

Progress and settings use the platform's user data directory by default. With `--portable`, progress and audio cache are stored under `user-data/` beside the launcher:

```sh
python3 launcher.py --app-version 3 --portable
```

The launcher does not implicitly import other records from the receiving computer. Do not commit personal `user-data/`, caches, logs, accounts, or API keys.

## Documentation and status

See [the deployment guide](docs/部署指南.md), [the user manual](docs/使用手册.md), [CONTRIBUTING.md](CONTRIBUTING.md), and [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

Linux has passed 120 tests, relocation checks, and offline container validation. Windows adaptations are included, but Windows and macOS interactive behavior has not yet been tested on physical machines. Cross-platform smoke CI is configured; its first remote run will be available in Actions.

## Source and licensing

This is an independent Python implementation, not an official fork and not endorsed by the upstream projects. The five dictionaries were taken from a fixed `qwerty-learner-vscode` commit. Each version keeps the original URLs, SHA-256 values, and provenance metadata in `data/source.json`, together with the MIT notice in `data/QWERTY-VSCODE-LICENSE.txt`. The main `qwerty-learner` project is currently GPL-3.0; that is distinct from the dictionary source notice. See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

The project license is [GPL-3.0-only](LICENSE).

The application code is licensed under [GPL-3.0-only](LICENSE). Third-party content retains its own license notices. Repository: [RoreProMAX/ielts-cli](https://github.com/RoreProMAX/ielts-cli).
