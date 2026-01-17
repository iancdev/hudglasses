# Repository Guidelines

## Project Structure & Module Organization
This repo is currently **docs-first** (hackathon planning + protocols). Key paths:
- `docs/`: project specs and integration contracts (start here).
  - `docs/implementation_plan.md`: end-to-end hackathon plan (server → Android HUD).
  - `docs/ESP32_Protocol.md`: ESP32 → server audio streaming protocol.
  - `docs/PRD.MD`, `docs/PRD_Enhanced.MD`, `docs/Draft.md`: requirements (Draft takes precedence).
- `VitureSDK/`: placeholder for Viture SDK assets/notes (currently empty).

When code lands, keep modules separated (recommended): `android/` (HUD + phone remote), `server/` (laptop processing), `esp32/` (firmware).

## Build, Test, and Development Commands
No build/test scripts are checked in yet (documentation-only repo today). Useful commands:
- `ls docs/` — list current specs/contracts.
- `git diff` — review doc changes before committing.
- `git log --oneline -n 20` — see recent commit message patterns.

When adding Android/server code, include module-specific commands in that module’s README (e.g., `android/README.md`) and update this file.

## Coding Style & Naming Conventions
- Markdown: use clear headings, short paragraphs, and fenced code blocks for JSON/examples.
- Prefer consistent filenames for new docs: `docs/<topic>.md` (lowercase `.md`).
- Protocol docs: include versioning fields (e.g., `v: 1`) and “required vs optional” sections.

## Testing Guidelines
No automated tests exist yet. If you introduce code:
- Add at least one “smoke test” path (unit or integration) per module.
- Document how to run it in the module README.

## Commit & Pull Request Guidelines
Commit messages in history are **imperative and concise** (e.g., “Add …”, “Switch …”, “Plan: …”).
PRs should include:
- What changed + why (link to the relevant `docs/*` file).
- Any protocol changes (and version bumps if applicable).
- Screenshots/video for HUD changes (Viture display) when available.

## Security & Configuration Tips
Do **not** commit secrets (e.g., `ELEVENLABS_API_KEY`), tokens, or Wi‑Fi credentials. Keep local config in ignored files (add to `.gitignore` as new modules are introduced).

