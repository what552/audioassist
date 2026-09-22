# Release Checklist

Use this checklist for a packaged release or after changing audio capture,
persistence, model loading, or the Python/JavaScript bridge. Routine changes do
not require every manual scenario.

The build follows Apple's inside-out rule: PyInstaller signs collected code,
then the release script signs the capture helper with its own entitlements and
the outer app last. The app is packaged into the DMG only after strict signature
verification. DMG creation uses headless `hdiutil`, so it does not depend on a
logged-in Finder session. Developer ID builds enable Hardened Runtime and a secure timestamp;
notarization uses `notarytool`, then staples and validates the ticket. See
[Apple's distribution-signing guidance](https://developer.apple.com/documentation/xcode/creating-distribution-signed-code-for-the-mac),
[Hardened Runtime requirements](https://developer.apple.com/documentation/xcode/configuring-the-hardened-runtime),
and [notarization workflow](https://developer.apple.com/documentation/security/notarizing-macos-software-before-distribution).

## Build modes

Local ad-hoc release candidate (mechanically verifiable, not distributable):

```bash
bash scripts/build_dmg.sh
```

Developer ID build without submitting to Apple:

```bash
AUDIOASSIST_SIGN_IDENTITY="Developer ID Application: Company (TEAMID)" \
  bash scripts/build_dmg.sh
```

Developer ID build with a `notarytool` keychain profile:

```bash
AUDIOASSIST_SIGN_IDENTITY="Developer ID Application: Company (TEAMID)" \
AUDIOASSIST_NOTARY_PROFILE="audioassist-notary" \
  bash scripts/build_dmg.sh
```

Do not put passwords or API keys in the script or repository. Configure the
named keychain profile separately with `xcrun notarytool store-credentials`.

## Automated checks

- Bootstrap or sync the locked repository environment with `bash scripts/bootstrap.sh`.
- Run `.venv/bin/python -m pytest -q`; the full suite includes the browser tests.
- Run `bash scripts/build_dmg.sh` from the repository root.
- Inspect `dist/release-verification-<version>.json`; it must report the same
  version as `pyproject.toml`, a valid strict signature, required helper/runtime
  assets, no developer-only Mach-O load paths, the DMG SHA-256, and a read-only
  mounted DMG payload whose app identity and CDHash match the source app.
- For distribution, the evidence must say `signature: developer-id`, and
  every nested Mach-O must have the same signing team, Hardened Runtime, a
  secure timestamp, and only the explicitly allowed entitlements. When
  notarization is requested, `xcrun stapler validate
  dist/AudioAssist-<version>.dmg` must pass.

## File transcription

- Import one short file and a multi-file batch.
- Confirm sequential queue behavior, progress, cancellation, retry, and checkpoint resume.
- Confirm timestamps seek the player and edits persist to JSON and Markdown.
- Confirm re-transcription and manual high-accuracy transcription replace the intended session output.

## Realtime capture

- For an ad-hoc local candidate, expect every changed build to have a new CDHash and
  re-authorize microphone/screen capture before judging device initialization. A
  Developer ID signed upgrade must retain the stable designated requirement instead.
- On a fresh microphone decision, confirm the system prompt appears before model loading
  and Session/WAV creation; cancel and timeout must restore idle controls without a
  header-only recording.
- Confirm the packaged Python-to-AVFoundation callback bridge actually reaches the
  native prompt; a mocked authorization result or a Settings link is not sufficient.
  Before the first request, the UI must not instruct users to enable a missing app row.
- Verify microphone, system, and mix modes independently.
- Verify the permission matrix explicitly: Mic requests only Microphone, System requests
  only Screen & System Audio Recording, and Mix reports/requests both independently.
- Verify pause, playback while paused, resume, finish, and short-recording discard.
- Exit with a non-empty realtime WAV before full transcription completes, relaunch,
  start **高精度转写** from that raw history entry, and verify it reuses the same
  session id. Interrupt it once and verify the next launch offers checkpoint resume.
- On macOS, drag a file from a surface that does not expose an absolute path. Verify
  no background job/history duplicate is created and the UI directs the user to
  **Upload File**; then confirm the native picker starts the same file normally.
- Confirm Screen Recording permission guidance and missing-helper errors are actionable.
- Confirm a missing Microphone permission blocks Mix before startup, while a microphone
  device/converter failure after both permissions pass degrades Mix to system-only.
- Confirm the final WAV and diarized transcript stay aligned.

## Privacy permission installation and upgrade gates

- Before testing an ad-hoc local candidate, quit every AudioAssist process, ensure the
  intended packaged app is the only copy being exercised, reset AudioAssist's Microphone
  and Screen & System Audio Recording decisions, and authorize the candidate again. This
  validates a fresh local build only; it is not evidence that upgrades preserve access.
- Test a distributable upgrade without resetting TCC: install the previous released app,
  grant both recording permissions, successfully record in Mic/System/Mix, install the
  new signed DMG over it, and confirm all three modes still record without another prompt,
  Settings toggle, or permission reset.
- Test first installation independently on a clean macOS account or after an intentional
  TCC reset. Confirm Mic asks only for Microphone, System asks only for Screen & System
  Audio Recording, and Mix requests whichever of the two is still missing.
- Record the old and new signing authorities, TeamIdentifier, bundle identifier, and
  designated requirements in the release evidence. CDHash may change across a properly
  signed upgrade, but the new app must satisfy the previously recorded designated
  requirement.
- Block distribution if an upgrade loses recording permission or if recovery instructions
  would require ordinary users to run `tccutil`, remove privacy rows, or repeatedly toggle
  permissions in System Settings.

## Session outputs

- Rename and delete sessions, then restart and verify persistence.
- Export transcript and summary as TXT and Markdown.
- Generate multiple summary versions and continue a Summary Agent conversation.
- If Obsidian sync is enabled, verify filename changes, frontmatter, summary, and transcript updates.

## Packaging

- Launch on a machine without the development checkout or shared virtual environment.
- Complete first-run model setup from an empty application data directory.
- Confirm no runtime dependency is loaded from a developer-only path.
- Confirm Gatekeeper accepts the downloaded/quarantined DMG and app.
- Re-run one short offline Qwen job and one short realtime microphone/system
  capture from the packaged app with networking disabled after model setup.
- Check the release notes against `README.md` and `README.dev.md`.
