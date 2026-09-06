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

The current baseline is 820 automated tests, including 8 browser tests. It
canonicalizes backend language names and ISO variants, corrects strongly conflicting
language metadata from transcript script evidence, reports substantial Chinese/English
code-switching as `mixed`, and normalizes legacy sessions on read without rewriting
them. It also includes the pinned `mlx-qwen3-asr` 0.3.5 adapter,
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

QWEN-04A added three synthetic voice-clone fixtures with exact source text. CPU
and MLX completed Chinese, English, and 104-second code-switching audio; MLX was
about 29% faster than CPU on the warm code-switching pass with equal 0.65% CER.
PyTorch MPS was much faster on the two monolingual clips but repeatedly truncated
the code-switching clip, with 79-103 seconds missing across repeated runs. Combined
with the existing 5/5/10-minute evidence, neither accelerated backend is approved
for automatic selection: MPS fails the short code-switching quality gate and MLX
still fails the existing 10-minute cross-backend text/timestamp thresholds.

TRANS-01 now registers cancellation before worker startup, rejects concurrent
operations for the same job, and writes transcription/refine results through a
same-session staging directory before committing the canonical JSON. Cancellation or
pipeline failure therefore leaves an existing transcript intact. Re-transcription and
refinement hold the existing per-session mutation lock, so a concurrent rename/save is
applied after the new transcript instead of being silently overwritten. Refine
cancellation is exposed through the same API and keeps the option available for retry.

TEST-UI-01 expands the browser harness from three startup/history checks to eight
end-to-end UI scenarios. The real frontend scripts now run against a configurable,
call-recording PyWebView API double for first-run setup, model download failures,
upload retry/cancel, model library state, serial drag/drop queueing, and Summary Agent
history/stream/tool/error/clear behavior.

ASR-QUALITY-01 replaces midpoint-only speaker assignment with a shared interval
overlap policy for file and realtime results. A speaker must cover at least 20% of
the ASR interval and, when multiple speakers overlap it, own at least 60% of the
diarized overlap. Gaps up to 350 ms are repaired only when both adjacent turns have
the same speaker; near-even cross-speaker boundaries and weakly covered intervals
remain explicitly `UNKNOWN`. Transcript-level results without word timestamps also
remain `UNKNOWN` when diarization contains multiple identities.

TEXT-01 removes unconditional punctuation at every generated block boundary. Existing
commas, colons, ellipses, sentence marks, and closing quotes are preserved without
doubling. Internal generated terminals now require at least a 450 ms pause; splits
caused only by display-length limits do not invent a sentence boundary. The final
transcript still receives a conservative terminal when the whole utterance is long
enough. Script/language fixtures cover English, Chinese, Japanese, mixed Chinese-English,
Russian, Arabic, and Korean, and the transformation never changes lexical tokens.

REL-01 now derives the app and versioned DMG version from `pyproject.toml`,
builds from the locked repository environment, signs nested code before the
outer app, creates the DMG without Finder automation, and emits a JSON
verification record. A real 2.0.0 ad-hoc candidate passed strict signatures,
focused asset/entitlement checks, 491 Mach-O load-path checks, 582 symlink
checks, DMG image verification, and a read-only mounted payload comparison by
bundle identity and CDHash. Developer ID mode also audits every nested Mach-O
for team identity, Hardened Runtime, timestamp, and entitlement drift. The
implementation is complete, while a
distributable build remains externally gated by the unavailable Developer ID
identity/notary credentials and clean-machine release checks.

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
full-precision MLX adapter, conservative benchmark decision, and reproducible
release implementation are complete. QWEN-04 retains the deferred promotion
matrix, and the distribution release gate is recorded in
`docs/release-candidate-results.md`. HTTP/MCP work remains a separate milestone
and must not imply durable background work until lifecycle, locking, recovery,
and cancellation are designed. With AGENT-01 paused by the owner, the next ordered
task is the R03-B1 HTTP lifecycle/security design gate, followed by R03-B2 only if
that design provides an implementable local boundary.
