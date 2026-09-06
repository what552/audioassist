# REL-01 Release Candidate Record

Updated: 2026-09-06

## Outcome

The reproducible release implementation is complete and a local ad-hoc
AudioAssist 0.1.0 release candidate passed all mechanical checks. It is not a
distribution release: this machine has no Developer ID Application identity,
so Developer ID signing, Apple notarization, stapling, Gatekeeper validation on
a quarantined download, and clean-machine checks remain external release gates.

The build script does not downgrade those missing gates to a pass. With no
signing environment variables it labels the result `ad-hoc (local verification
only; not distributable)`. A distribution run requires both the Developer ID
identity and, for notarization, an explicitly configured `notarytool` keychain
profile.

## Verified local candidate

- Version: `0.1.0`, sourced from `pyproject.toml` into both bundle version keys
- Bundle identifier: `com.audioassist.app`
- App: `dist/AudioAssist.app` (1.1 GiB)
- DMG: `dist/AudioAssist-0.1.0.dmg` (463 MiB)
- Final local DMG SHA-256:
  `452a104553ad7b8af9cc861ada5b8b678971c778f3ebcab0c5c4aea703c2924b`
- Signature: strict ad-hoc app and DMG signatures verified
- Entitlements: only `com.apple.security.device.audio-input` on the app and
  capture helper; the obsolete sandbox microphone key and undocumented screen
  recording entitlement were removed
- Package inspection: 491 Mach-O files and 582 symlinks checked; no developer
  checkout, `.venv`, `/Users`, or `/private/tmp` load path was present
- Required payload: main executable, capture helper, ffmpeg, ffprobe,
  `mlx_qwen3_asr` mel/Korean assets, and distribution metadata present
- DMG image checksum: valid

The canonical builder uses headless `hdiutil`; it does not use Finder automation.
This was made explicit after `create-dmg` timed out while the desktop session was
locked.

## Commands and evidence

```bash
uv sync --frozen --extra mac --extra dev --extra build
.venv/bin/python -m pytest -q
bash scripts/build_dmg.sh
.venv/bin/python scripts/verify_release.py \
  --app dist/AudioAssist.app \
  --dmg dist/AudioAssist-0.1.0.dmg \
  --expected-version 0.1.0 \
  --output dist/release-verification-0.1.0.json
security find-identity -v -p codesigning
```

The checked-in verifier emits machine-readable JSON and fails on version or
bundle-id drift, invalid app/DMG signatures, unexpected app/helper entitlements,
missing focused runtime assets, external/broken symlinks, developer-only Mach-O
load paths, or an invalid DMG checksum. Distribution runs additionally require
a Developer ID authority and, when requested, a valid stapled notarization
ticket.

The final complete run (with no `--skip-*` flags) passed all five stages. The
automated repository suite passed 750 tests. An earlier full-suite attempt hit
two existing asynchronous callback timing flakes; both passed immediately in
isolation and the final full-suite rerun passed without exclusions.

## External distribution gate

`security find-identity -v -p codesigning` returned `0 valid identities found`.
Once a Developer ID Application certificate and `notarytool` profile are
available, run the documented distribution command in
`docs/release-checklist.md`, then perform the quarantined DMG, clean-machine,
first-run model setup, offline transcription, and capture-permission checks.
