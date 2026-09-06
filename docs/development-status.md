# Development Status

Updated: 2026-09-06

## Current baseline

AudioAssist is a functional MVP. R01 and R02 are integrated into `main`; R03-a is complete.

| Milestone | Status | Delivered |
|---|---|---|
| R01 | Complete | File transcription, diarization, timestamp playback, editing, summary generation, realtime transcription foundation |
| R02-a to R02-b5 | Complete | Three-column UI, session lifecycle, model setup and management, Summary Agent, Obsidian sync, checkpoint/resume, batch queue, re-transcription |
| R02-b6 | Complete | macOS microphone/system/mix capture, ScreenCaptureKit helper, manual high-accuracy refinement, DMG packaging |
| R03-a | Complete | Local session resources, synchronous transcription and summary CLI with stable JSON output |
| R03-b | Not started | Planned HTTP resource/agent APIs after an explicit lifecycle and security design gate |

The QWEN-03 baseline is 742 automated tests: 739 Python/backend tests and
3 browser tests. It includes the pinned `mlx-qwen3-asr` 0.3.5 adapter,
native Forced Aligner conversion, deterministic holder/cache cleanup, and focused
PyInstaller resources. Forced-offline 30-second Qwen CPU, diagnostic MPS, and MLX CLI
smoke jobs all completed against the application-managed 1.7B and aligner directories.
The frozen app contains the adapter, metadata, and required mel/Korean assets and passes
strict ad-hoc signature verification. QWEN-03 adds a redacted, isolated-process
benchmark runner. On an M5 Pro, MLX
was 34-58% faster than CPU across three 5/5/10-minute samples and passed a
corrected realtime-only run of 50 load/close cycles plus 100 warm serialized
utterances. MPS was faster on this machine, the 10-minute MLX result exceeded
the cross-backend text/timestamp thresholds, and promotion-level
human/long/signed-app evidence is incomplete. The decision is therefore to
keep `auto` unchanged and track the remaining evidence as QWEN-04. Real
audio-device and permission behavior remains
release-level manual validation rather than CI coverage.

## Historical gate record

The former permanent builder/researcher/reviewer worktrees were used to deliver R01 and R02. Their useful conclusions are retained here so the worktrees and role branches are no longer required.

| Scope | Reviewed target | Evidence | Result |
|---|---|---|---|
| R02-b6 c01 system capture | `ca346dd` | Reviewer commits `322d70c`, `a8b2dbf` | Gate fixes accepted |
| R02-b6 c02 mix capture, UI, permissions | `7e16724` | Reviewer commits `363286a`, `318f83a` | Go; no remaining P0/P1/P2 findings |
| R02-b6 c03-c07 and later maintenance | through `8b52cd0` | Current automated baseline | 602 tests passing on 2026-09-05 |

The c02 gate verified the 512-sample mix-buffer drain behavior, Screen Recording guidance, and non-fatal microphone degradation path. Later packaging work removed the bundled torchcodec dependency and loads diarization audio through `soundfile`.

## Working model going forward

- Keep `main` as the only permanent integration checkout.
- Use a short-lived worktree for a bounded feature only when parallel or isolated work is useful.
- Use a separate research task for uncertain platform, hardware, API, or architecture questions.
- Review the exact implementation commit for medium/high-risk changes; routine fixes can be implemented and tested in one task.
- Archive or hand off a task after integration, then remove its worktree and branch.
- Root development rules now live in `AGENTS.md`; a permanent builder role is no longer required.

## Next product work

Use [the authoritative development backlog](development-backlog.md). The realtime
local-model-only gate, repository environment, Qwen backend lifecycle, explicit
full-precision MLX adapter, and conservative benchmark decision are complete; next build
and verify the release candidate. HTTP/MCP
work remains a separate milestone and must not imply durable background work until
lifecycle, locking, recovery, and cancellation are designed.
