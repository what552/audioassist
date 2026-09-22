# Development Status

Updated: 2026-09-22

## Current baseline

AudioAssist is a functional MVP. R01 and R02 are integrated into `main`; R03-a is complete.

| Milestone | Status | Delivered |
|---|---|---|
| R01 | Complete | File transcription, diarization, timestamp playback, editing, summary generation, realtime transcription foundation |
| R02-a to R02-b5 | Complete | Three-column UI, session lifecycle, model setup and management, Summary Agent, Obsidian sync, checkpoint/resume, batch queue, re-transcription |
| R02-b6 | Complete | macOS microphone/system/mix capture, ScreenCaptureKit helper, manual high-accuracy refinement, DMG packaging |
| R03-a | Complete | Local session resources, synchronous transcription and summary CLI with stable JSON output |
| R03-b | Complete | Authenticated loopback session/recording API, foreground read-only CLI service, and eight-tool stdio MCP adapter |

The current packaged baseline is AudioAssist 2.1.0. The 2.1.1 P0 source work adds a
session-owned recovery path for valid realtime WAV files that outlive an interrupted
or exited recording session. Raw recordings now expose a full-transcription action;
the backend resolves their path by validated session id, invalid drag/drop paths are
rejected before worker creation, and an interrupted full-transcription checkpoint is
shown as resumable instead of being hidden by the raw WAV entry. Packaging remains
pending until full verification and independent review complete.
The automated source gate currently passes **1117 tests** (including **52 browser
tests**); real recovered-recording and packaged-app acceptance remain release gates.

The prior source baseline was 1104 automated tests. AudioAssist 2.1.0 refreshes the
desktop transcription workspace while preserving the established feature contract:
Summary starts collapsed, transcript rows present timestamp–speaker–text, speaker and
text corrections are editable in place, a speaker can be renamed globally, themes are
persisted from Settings, and the manual high-accuracy action survives reload/restart.
The 2.1.0 App and DMG pass mechanical release verification; installed Mic/System/Mix
recording acceptance remains the release gate.

The baseline also
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
for automatic selection under that historical configuration. The 2026-09-07
[runtime corrections](qwen-backend-investigation.md) now pass short/10-minute
comparisons; the earlier failures are no longer the current blockers. Existing
FLEURS, exact-script/code-switching and meeting references close the requested
quality scope; packaged release gates remain.

The subsequent [public meeting benchmark](qwen-meeting-backend-benchmark.md)
covered 60 minutes per backend across AliMeeting, AMI and Mandarin/English
0/10/30/50% controlled overlap. All 30 backend/sample combinations completed.
CPU and MPS remained text/timestamp-identical; MLX stayed within 1.78%
normalized character distance and 97.73% timestamp-token alignment. MPS had the
lowest aggregate inference time (RTF 0.185), while MLX had the lowest load time
and peak process RSS. The run also added bounded failed-chunk bisection for
length/repetition stops and a 50 ms aligner-boundary clamp. Speaker-aware scores
show that 30–50% overlap, rather than backend selection, is the dominant quality
limitation.

The subsequent [50-cycle lifecycle gate](qwen-lifecycle-results.md) completed
100 warm serialized utterances on each corrected accelerated backend with zero
failures, truncations or language mismatches. MLX cleanup RSS grew 225 MiB.
MPS retained a larger early PyTorch/Metal cache, then narrowed to a 41 MiB range
over the final ten cycles. The 2.0.2 packaged app then completed the same Mandarin
file through CPU, MPS, and MLX with identical text and persisted the actual backend.
Packaged Mic start/pause/resume/finish also passed with a valid 40.32-second WAV.
System/Mix remain pending only because the new ad-hoc CDHash needs a separate local
Screen & System Audio Recording authorization. Automatic backend policy remains
unchanged.

QWEN-UI-01 adds a persisted personal backend default in Settings, independently
of product-wide automatic promotion. New Qwen jobs use the saved CPU/MPS/MLX
choice (unless explicitly overridden by the launch environment); active jobs
keep their snapshot and checkpoint resumes retain their backend. No automatic
fallback is implemented. File and realtime routing share the same preference.

TRANS-01 now registers cancellation before worker startup, rejects concurrent
operations for the same job, and writes transcription/refine results through a
same-session staging directory before committing the canonical JSON. Cancellation or
pipeline failure therefore leaves an existing transcript intact. Re-transcription and
refinement hold the existing per-session mutation lock, so a concurrent rename/save is
applied after the new transcript instead of being silently overwritten. Refine
cancellation is exposed through the same API and keeps the option available for retry.
Per-session mutation locks now use weak-value retention, so active owners and waiters
share one lock while inactive job entries are reclaimed. Session deletion takes that
same lock and cannot race a transcribe, refine, save, or rename operation.

AUDIO-LINK-01 makes completed realtime sessions resilient to a missing duplicated
`source_audio` file. When the recording session still links to the transcript through
`transcribed_job_id`, history, transcript reads, retry, playback, and eligible manual
high-accuracy refinement use that verified original WAV as a read-time fallback. An
existing canonical copy always wins, unrelated missing paths still fail normally, and
legacy transcript JSON is not silently rewritten.

TEST-UI-01 established a browser harness that has since grown to 37 tests. The real
frontend scripts run against a configurable,
call-recording PyWebView API double for first-run setup, model download failures,
upload retry/cancel, model library state, serial drag/drop queueing, and Summary Agent
history/stream/tool/error/clear behavior. Later regressions cover capture permission
flows, session-safe playback, durable rapid renames, resume failures, non-blocking
errors, and mutually exclusive idle/player/error views.

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

R03-B2 implements the approved bounded local-service design. The visible desktop app
owns an authenticated loopback server and a queryable recording state machine; its close
path stops capture, releases the server, and removes instance-owned discovery. The
foreground `audioassist serve --read-only` command exposes only shared-service session
reads and handles SIGINT/SIGTERM cleanup. The separate `audioassist-mcp` stdio adapter
offers three durable session reads plus five live-recording tools; reads work with the
app closed, while recording calls validate a live authenticated desktop instance. There
is still no daemon, remote listener, background inference queue, or automatic app launch.

Installed-app UI acceptance found an apparent intermittent microphone-start symptom:
one formal 2.0.0 attempt remained in `starting` and left a 44-byte WAV, while a later
attempt recorded valid frames. REALTIME-DIAG-01 reproduced and explained it: replacing
an ad-hoc signed build changed its CDHash, so macOS rejected the prior TCC code
requirement and waited for a new microphone prompt inside PortAudio's AUHAL device-open
call. REALTIME-START-01 now requests first-use permission through AVFoundation before
model loading, Session/WAV creation, or PortAudio; its UI can cancel the wait and the
native request times out after 60 seconds. Diagnostic persistence is marker-only,
cross-process rotation-safe, and performs no file I/O in the realtime callback. The
installed 2.0.1 candidate passed first-use Mic authorization and recording. A stable
Developer ID signature is still required to prove permission continuity across real
upgrades. PLAYER-02 and UI-SESSION-REFRESH-01 have also closed the other confirmed
session/playback findings.

CAPTURE-PERM-01 separates the previously ambiguous capture authorization UX. Mic no
longer references screen access; System checks only Screen & System Audio Recording;
Mix checks both and lists missing permissions independently. Both GUI and local API
enforce this matrix before loading models or creating a realtime Session. The installed
2.0.1 app passed Mic, System, and Mix capture; the remaining permission gate is
Developer ID upgrade continuity under REL-TCC-01.

REL-01 now derives the app and versioned DMG version from `pyproject.toml`,
builds from the locked repository environment, signs nested code before the
outer app, creates the DMG without Finder automation, and emits a JSON
verification record. Real 2.0.0 and 2.0.1 ad-hoc candidates passed strict signatures,
focused asset/entitlement checks, 491 Mach-O load-path checks, 582 symlink
checks, DMG image verification, and a read-only mounted payload comparison by
bundle identity and CDHash. Developer ID mode also audits every nested Mach-O
for team identity, Hardened Runtime, timestamp, and entitlement drift. The
implementation is complete, while a
distributable build remains externally gated by the unavailable Developer ID
identity/notary credentials and clean-machine release checks.

RUNTIME-ID-01 makes source, global-shim, `dist`, and installed-App runs
distinguishable. `audioassist --version` and Settings report the authoritative
version, runtime mode, and actual source or `.app` path. Bootstrap and release builds
refresh the editable package metadata; the packaged verifier accepts the valid wheel
or editable metadata format but rejects missing, duplicate, or mismatched identity.
The 2.0.3 ad-hoc App/DMG passed this mechanical gate, while installed recording and
Developer ID upgrade-continuity checks remain separate manual/external gates.

QWEN-BOUNDARY-01 closes a later real-file regression found in the 2.0.3 candidate.
The earlier 50 ms Forced Aligner tolerance clamped an end-only overshoot, but rejected
a zero-duration final word whose start and end were both quantized 4 ms beyond a
29.916-second chunk. The shared validator now clamps both boundaries only within the
same 50 ms limit and reports numeric context for larger defects. The original
593-second Mandarin file completes across all 31 internal chunks on both MPS and MLX;
failed and interrupted GUI sessions also expose their recorded backend. The 2.0.3
artifact is superseded. The 2.0.4 App and DMG passed mechanical package verification;
owner-installed MPS and MLX file transcription passed on the original 593-second
file through diarization and persistence. Recording-mode acceptance remains pending.

QWEN-REALTIME-MLX-01 addresses the separate realtime thread-affinity defect found
after that installed file gate. In 2.0.4 the capture startup thread loads MLX, but
the serialized ASR worker performs inference; MLX rejects that foreign Metal stream
with `There is no Stream(gpu, 1) in current thread.` The source implementation now
makes the worker own backend load, inference, and close for Mic and NativeCapture,
while capture callbacks remain queue-only. A cancelable startup lease now blocks
overlapping backends during start, stop, delayed Native drain, and application
shutdown. Thread-identity regressions, the full 1084-test suite, 38 frontend tests,
real CPU/MPS/MLX Mic-worker probes, and a real MLX NativeCapture ASR probe pass.
Exact commit `23dcf88` passed independent concurrency review. The 2.0.5 App and
DMG passed mechanical verification. The owner-installed 2.0.5 candidate was
accepted without reported issues; logs and saved outputs confirm successful MPS
Mic, MLX Mic, and MLX Mix sessions with valid audio and transcript segments.
The local realtime defect is closed; Developer ID upgrade continuity remains the
separate REL-TCC-01 external gate.

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
release implementation and bounded R03 HTTP/MCP adapters are complete. QWEN-04 retains the deferred promotion
matrix, and the distribution release gate is recorded in
`docs/release-candidate-results.md`. R03-B1/B2 retain the foreground read-only and
visible-desktop lifecycle boundary and do not imply durable background work. With
AGENT-01 paused by the owner, no item from the owner's ordered 1-3/5-7 batch remains
eligible to implement: QWEN-04B is correctly held by its measured quality gate.
The bounded P3 lock, UI error/player state, ffprobe validation, ASR conversion-test,
and logging debts are complete. Remaining product items are explicitly deferred or
externally gated in the backlog rather than implicitly open.
