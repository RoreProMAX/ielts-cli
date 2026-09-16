# Repository guidance for agents

- This is a general-purpose terminal vocabulary practice tool. Agent Desktop terminals are one supported usage scenario, not the only audience.
- Read `docs/FOR_AGENTS.md`, `docs/STATUS.md`, and `docs/INTERFACES.md` before changing deployment or behavior.
- `launcher.py` selects the application version. The current development/default version is `apps/v3.3.1-beta.1`; `apps/v1.0.0`, `apps/v2.0.0`, `apps/v3.2.0`, and `apps/v3.3.0` are retained for version isolation.
- Keep personal study profiles, audio caches, credentials, and machine paths outside Git. Tests must use temporary data directories.
- Do not answer questions in a user's live study session, enable background reminders, change audio devices, or publish a repository unless the user requested that action.
- Preserve original dictionary payloads and their source/license records. Add a new dictionary ID for a different dataset.
- New examples must be original, use the exact target word as the blank answer, and pass content tests. Do not describe generated examples as official IELTS material.
- Validate at the changed module and run the appropriate application suite. Audio/network/systemd operations must be mocked in tests.
- Distinguish automated startup checks from manual terminal/UI compatibility. Do not claim an untested host is fully supported.
- V3.3 may check public stable releases in a background thread when enabled (default on). It must never auto-download, install, restart, or interrupt a study session; update checks may be disabled with `IELTS_DISABLE_UPDATE_CHECK=1` for tests.
- Update channel is `stable` by default; `beta` is opt-in and accepts newer stable or beta releases without downgrade. A stable release requires explicit user direction. For this project, fixes of this kind default to `X.Y.(Z+1)-beta.N`, incrementing `beta.N` for iterations and never promoting automatically.
- Do not put `Co-Authored-By` or other assistant signatures in commit messages.
