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
Useful commands:
- `ls docs/` — list current specs/contracts.
- `git diff` — review changes before committing.
- `git log --oneline -n 20` — see recent commit message patterns.

### Android (CLI)
This project has two Android product flavors:
- `nosdk` — builds/runs without Viture SDK; IMU/head tracking disabled.
- `viture` — builds/runs with Viture SDK AAR; IMU/head tracking enabled.

Prereqs:
- JDK 17 (recommended: `brew install openjdk@17`)
  - Note: JDK 25 currently fails the Gradle/Kotlin build with `IllegalArgumentException: 25.0.1`.
- Android SDK with:
  - Platform 34
  - Build-Tools 34.x
  - Platform-Tools
- For `viture` flavor: `android/app/libs/VITURE-SDK-1.0.7.aar` present (not committed).

Commands:
- `export JAVA_HOME="/opt/homebrew/opt/openjdk@17/libexec/openjdk.jdk/Contents/Home"`
- `export ANDROID_SDK_ROOT="$HOME/Library/Android/sdk"`
- `cd android`
- `cat > local.properties <<EOF
sdk.dir=$ANDROID_SDK_ROOT
EOF`
- Build APKs:
  - `./gradlew :app:assembleNosdkDebug`
  - `./gradlew :app:assembleVitureDebug`
- Install to a USB-connected device:
  - `adb devices -l`
  - `./gradlew :app:installNosdkDebug`
  - `./gradlew :app:installVitureDebug`

Notes:
- `:app:installDebug` is ambiguous because there are multiple flavors; use the full task name above.
- `android/local.properties` is intentionally gitignored (local-only SDK path).

### Android Debug Logs (Logcat)
When the app crashes on startup, grab logs via:
- `adb devices -l`
- `adb logcat -c`
- Start the app: `adb shell am start -n dev.iancdev.hudglasses/.RemoteActivity`
- Tail crash output: `adb logcat | rg -n \"FATAL EXCEPTION|AndroidRuntime|dev\\.iancdev\\.hudglasses|VitureImu|Wristband\"`

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
