# REL-01 Release Candidate Record

Updated: 2026-09-07

## 2.0.3 runtime-identity candidate

Status: **superseded — do not distribute**. An installed long-file check subsequently
found QWEN-BOUNDARY-01; the corrected source must be packaged as 2.0.4.

- Source baseline: runtime identity merge `fa2e5a3`, version merge `777c60c`, and
  packaged-metadata verifier fix `8fc20a4`; the full host suite passed **1070 tests**.
- Build: `bash scripts/build_dmg.sh` refreshed `audioassist-local` metadata from
  2.0.2 to 2.0.3 before PyInstaller. Its first verification correctly stopped on
  the editable `egg-info` metadata shape; the corrected verifier then passed the
  unchanged candidate without regenerating a same-version artifact.
- Version: `2.0.3`; bundle ID: `com.audioassist.app`; local ad-hoc signature.
- App: `dist/AudioAssist.app`; DMG: `dist/AudioAssist-2.0.3.dmg`.
- DMG SHA-256:
  `84f9f7fa6336ce05c466f6f6089a50c4977ff9fd771a1579211e14fc371f52d6`.
- App CDHash: `a1e75e32c8b490696ccb21b355bdb1a6d92fb3fa`.
- `dist/release-verification-2.0.3.json`: PASS, including 491 Mach-O files,
  584 symlinks, strict signatures, required Qwen/MLX assets, a matching read-only
  mounted DMG payload, and `audioassist_local.egg-info/PKG-INFO` version 2.0.3.
- CLI source identity reports version 2.0.3 and the canonical repository path.
  The packaged Settings identity has automated browser coverage; launch and
  recording-mode acceptance remain pending and are not claimed here.
- At the time of initial mechanical verification this candidate had not been installed
  over `/Applications/AudioAssist.app`. It remains local/ad-hoc rather than a
  distributable signed upgrade and cannot satisfy REL-TCC-01 without a stable
  Developer ID Application identity.
- A subsequent 2.0.3 owner test uploaded a 593.195-second Mandarin file and failed in
  the first outer chunk. Its persisted
  task recorded `pytorch-mps` (not MLX), and an explicit MLX run reproduced the shared
  validator failure. The offending final word had `start=end=29.920s` against a
  29.916s internal chunk. After the bounded start/end clamp, full-file pure-ASR probes
  passed on MPS (1840 words, 31 chunks) and MLX (1838 words, 31 chunks), with no retry.

## 2.0.2 Qwen backend-selection candidate

- Source baseline: merge `9e8b87f` plus version bump `60d3a60`; full host suite
  passed **1061 tests** with 958 dependency warnings.
- Build: `bash scripts/build_dmg.sh` completed all stages successfully.
- Version: `2.0.2`; bundle ID: `com.audioassist.app`; local ad-hoc signature.
- App: `dist/AudioAssist.app`; DMG: `dist/AudioAssist-2.0.2.dmg`.
- DMG SHA-256:
  `99d643b0c9fc3df3a5c0f2a020ca1bda45f2456f6d020cb19a3f63e7ed5aa2a1`.
- App CDHash: `65e1cd87e763a5b6281a0fcfc11fc8ad9b6f2f75`.
- `dist/release-verification-2.0.2.json`: PASS, including 491 Mach-O files,
  583 symlinks, strict signatures, required Qwen/MLX assets, and a matching
  read-only mounted DMG payload.
- Existing session directories loaded without migration or rewrite in the 2.0.2
  app, providing an in-place legacy-read compatibility check against the owner's
  current data directory.
- Packaged file-transcription gate: PASS for Auto/CPU, PyTorch MPS and MLX on the
  same 4.74-second Mandarin FLEURS clip. All three produced one `zh` segment with
  the same text. Persisted `qwen_backend` values were `pytorch-cpu`,
  `pytorch-mps`, and `mlx`, respectively. The personal setting was restored to
  Auto after the test.
- Packaged microphone gate: PASS for start, valid 16 kHz mono PCM frames, pause,
  resume, and finish. The resulting WAV was 40.32 seconds / 1,290,284 bytes.
- Packaged System/Mix gate: pending local re-authorization. The new ad-hoc CDHash
  correctly reports only the missing Screen & System Audio Recording permission
  and disables start; it does not incorrectly require Microphone for System mode.
  No TCC reset or System Settings mutation was performed during this run.
- This remains a local test candidate. It is neither installed over 2.0.1 nor a
  distributable signed upgrade, and it cannot prove TCC continuity without a
  stable Developer ID Application identity.

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

The reproducible release implementation is complete and the latest local ad-hoc
AudioAssist 2.0.1 release candidate passed mechanical and installed capture checks.
It is not a
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
