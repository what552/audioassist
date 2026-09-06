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

- Verify microphone, system, and mix modes independently.
- Verify pause, playback while paused, resume, finish, and short-recording discard.
- Confirm Screen Recording permission guidance and missing-helper errors are actionable.
- Confirm a denied/unavailable microphone degrades mix mode to system-only without ending the session.
- Confirm the final WAV and diarized transcript stay aligned.

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
