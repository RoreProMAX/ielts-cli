# Repository guidance for agents

- This is a general-purpose terminal vocabulary practice tool. Agent Desktop terminals are one supported usage scenario, not the only audience.
- Read `docs/FOR_AGENTS.md`, `docs/STATUS.md`, and `docs/INTERFACES.md` before changing deployment or behavior.
- `launcher.py` selects the application version. The current version is `apps/v3.2.0`; `apps/v1.0.0` and `apps/v2.0.0` are retained for version isolation.
- Keep personal study profiles, audio caches, credentials, and machine paths outside Git. Tests must use temporary data directories.
- Do not answer questions in a user's live study session, enable background reminders, change audio devices, or publish a repository unless the user requested that action.
- Preserve original dictionary payloads and their source/license records. Add a new dictionary ID for a different dataset.
- New examples must be original, use the exact target word as the blank answer, and pass content tests. Do not describe generated examples as official IELTS material.
- Validate at the changed module and run the appropriate application suite. Audio/network/systemd operations must be mocked in tests.
- Distinguish automated startup checks from manual terminal/UI compatibility. Do not claim an untested host is fully supported.
- Do not put `Co-Authored-By` or other assistant signatures in commit messages.
