# REL-01 Release Candidate Record

Updated: 2026-09-06

## 2.0.1 microphone authorization fix candidate

- Source fix: `a373c82d256fa437008be038dbb444e36674e177`, independently reviewed PASS.
- Full suite: 913 passed, 894 warnings; separate frontend suite: 21 passed.
- Build: `bash scripts/build_dmg.sh --skip-swift` completed successfully.
- Version: `2.0.1`; bundle ID: `com.audioassist.app`; local ad-hoc signature.
- DMG: `dist/AudioAssist-2.0.1.dmg`; SHA-256:
  `01f41dd0aea35e8b23a15b2e5beb6ed2f15e4d8e06ff916a7bbbc0dbb3f880a2`.
- App CDHash: `cb50f7d82ca9a3ad42c852cdef2246ff734826e3`.
- `dist/release-verification-2.0.1.json`: PASS, including 491 Mach-O files,
  582 symlinks, strict signatures, required assets, and matching mounted DMG payload.
- Native authorization/recording gate: PASS. The owner confirmed Mic; runtime
  diagnostics recorded stream open/start, recording confirmation, and the first
  512-frame audio callback. System session `acda4f17` captured 8.80 seconds
  (281,644 bytes, peak -20.9 dB). Mix session `66212e94` passed start, pause,
  resume, and stop and captured 6.88 seconds (220,348 bytes, peak -30.1 dB).
- The first 2.0.1 System attempt correctly exposed the stale 2.0.0 ad-hoc TCC
  code requirement. After the owner reset that Screen Capture row, macOS recorded
  no further code-requirement mismatch and repeated System/Mix capture passed.
- Installed at `/Applications/AudioAssist.app` after checking the old app was idle;
  installed version and strict signature verified. Previous app retained at
  `/private/tmp/AudioAssist-2.0.0-before-2.0.1.app`; session/model data untouched.
- This is a local test candidate, not a distributable or upgrade-continuity-verified release.

## Outcome

The reproducible release implementation is complete and a local ad-hoc
AudioAssist 2.0.0 release candidate passed all mechanical checks. It is not a
distribution release: this machine has no Developer ID Application identity,
so Developer ID signing, Apple notarization, stapling, Gatekeeper validation on
a quarantined download, and clean-machine checks remain external release gates.

The build script does not downgrade those missing gates to a pass. With no
signing environment variables it labels the result `ad-hoc (local verification
only; not distributable)`. A distribution run requires both the Developer ID
identity and, for notarization, an explicitly configured `notarytool` keychain
profile.

## Verified local candidate

- Version: `2.0.0`, sourced from `pyproject.toml` into both bundle version keys
- Bundle identifier: `com.audioassist.app`
- App: `dist/AudioAssist.app` (1.1 GiB)
- DMG: `dist/AudioAssist-2.0.0.dmg` (462 MiB)
- Final local DMG SHA-256:
  `48cf73f58a06ce3b58d2d18d51a75facaf2c9fc43d07c677abc591eb949c19c8`
- Signature: strict ad-hoc app and DMG signatures verified
- Entitlements: only `com.apple.security.device.audio-input` on the app and
  capture helper; the obsolete sandbox microphone key and undocumented screen
  recording entitlement were removed
- Package inspection: 491 Mach-O files and 582 symlinks checked; no developer
  checkout, `.venv`, `/Users`, or `/private/tmp` load path was present
- Required payload: main executable, capture helper, ffmpeg, ffprobe,
  `mlx_qwen3_asr` mel/Korean assets, and distribution metadata present
- DMG image checksum: valid
- DMG payload: mounted read-only; exactly one `AudioAssist.app` was present and
  its version, bundle identifier, strict signature, and CDHash matched the
  just-built source app

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
  --dmg dist/AudioAssist-2.0.0.dmg \
  --expected-version 2.0.0 \
  --output dist/release-verification-2.0.0.json
security find-identity -v -p codesigning
```

The checked-in verifier emits machine-readable JSON and fails on version or
bundle-id drift, invalid app/DMG signatures, unexpected app/helper entitlements,
missing focused runtime assets, external/broken symlinks, developer-only Mach-O
load paths, an invalid DMG checksum, or a stale/unrelated app inside the DMG.
Distribution runs additionally audit every nested Mach-O for the same Developer
ID team, Hardened Runtime, a secure timestamp, and no unexpected entitlements;
when requested they also require a valid stapled notarization ticket.

The final complete run (with no `--skip-*` flags) passed all five stages. The
automated repository suite passed 757 tests without exclusions. A sandboxed
attempt could not launch Chromium due to macOS Mach-port permissions; the
required system-permission rerun passed all 754 Python/backend and 3 browser
tests.

## External distribution gate

`security find-identity -v -p codesigning` returned `0 valid identities found`.
Once a Developer ID Application certificate and `notarytool` profile are
available, run the documented distribution command in
`docs/release-checklist.md`, then perform the quarantined DMG, clean-machine,
first-run model setup, offline transcription, and capture-permission checks.
