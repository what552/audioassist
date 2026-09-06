# AudioAssist Development Backlog

Updated: 2026-09-06

This is the authoritative development task list. `agentops/TODO.md` and the
older roadmap/report files are historical inputs; an unchecked box in those
files does not by itself mean the work is still open.

Priority meanings:

- **P0**: current data-loss, security, or release-blocking defect.
- **P1**: next planned work or a prerequisite for that work.
- **P2**: valuable follow-up that does not block the next milestone.
- **P3**: polish or bounded technical debt.

## Ordered execution queue

| Order | ID | Priority | Status | Task |
|---:|---|---|---|---|
| 1 | RUNTIME-01 | P0 | Complete | Enforce local-model-only loading in both realtime capture implementations |
| 2 | ENV-01 | P1 | Complete | Unify development, CLI, test, and package builds on the repository `.venv` |
| 3 | QWEN-01 | P1 | Complete | Add the internal Qwen backend protocol/factory and deterministic model cleanup |
| 4 | QWEN-02 | P1 | Complete | Implement and package the full-precision MLX Qwen adapter |
| 5 | QWEN-03 | P1 | Complete: hold | Add a reproducible benchmark gate and keep the Mac automatic backend unchanged |
| 6 | REL-01 | P1 | Implemented: external gate | Produce and verify a reproducible signed macOS release candidate |
| 7 | ASR-LANG-01 | P1 | Complete | Canonicalize detected languages, correct strong metadata/text conflicts, and support mixed Chinese/English |
| 8 | QWEN-04A | P1 | Complete: no-go | Human-reference short fixtures plus existing 5/10-minute evidence reject MPS and do not yet clear MLX |
| 9 | QWEN-04B | P1 | Hold: gate not met | Promote a Mac backend only after the remaining MLX quality and release gates pass |
| 10 | TRANS-01 | P2 | Complete | Make cancel/refine/rerun transactional and serialize conflicting same-session mutations |
| 11 | TEST-UI-01 | P2 | Complete | Cover setup, cancel/retry, model modal, drag/drop, and Summary Agent browser flows |
| 12 | ASR-QUALITY-01 | P2 | Complete | Use confidence-gated overlap and same-speaker gap alignment without hiding uncertainty |
| 13 | TEXT-01 | P2 | Complete | Add conservative, language-aware punctuation without semantic rewriting |
| 14 | R03-B1 | P2 | Complete: go | Foreground/read-only and visible-app server modes have explicit lifecycle, locking, recovery, cancellation, schema, and security boundaries |
| 15 | R03-B2 | P2 | Complete | Authenticated desktop/read-only HTTP service and eight-tool stdio MCP adapter, without a daemon |
| 16 | REALTIME-DIAG-01 | P1 | Complete | Prove the 44-byte WAV failure is a TCC code-requirement mismatch after replacing an ad-hoc signed build |
| 17 | REALTIME-START-01 | P1 | Complete | Request microphone access before PortAudio/model/session startup, with cancel and timeout recovery; installed 2.0.1 gate passed |
| 18 | PLAYER-02 | P2 | Complete | Prevent playback position and active playback from leaking across session switches |
| 19 | UI-SESSION-REFRESH-01 | P2 | Complete | Keep renamed/interrupted/resumed session labels and visibility current without an app restart |
| 20 | LOCAL-API-CLEANUP-01 | P3 | Complete | Make stale desktop discovery cleanup robust after abnormal app termination |
| 21 | CAPTURE-PERM-01 | P1 | Complete | Enforce and explain the distinct Mic/System/Mix macOS permission matrix before capture startup; installed 2.0.1 gate passed |
| 22 | REL-TCC-01 | P1 | Blocked: stable signing identity unavailable | Prove that a signed DMG upgrade preserves recording permissions without a TCC reset, and separately verify clean-install prompts |

### REALTIME-DIAG-01 — Intermittent packaged microphone startup

The installed 2.0.0 app produced one microphone session that remained in
`starting` with a header-only 44-byte WAV, followed by a successful recording in
the same app process. Re-signed isolated app copies produced similar symptoms but
are not valid permission evidence because changing their signature invalidated
their TCC identity.

- Persist a bounded desktop runtime log because the Finder-launched PyInstaller
  app has no console.
- Correlate each attempt across model loading, WAV creation, sounddevice import,
  PortAudio stream open/start, the first audio callback, and UI confirmation.
- Reproduce with the formally installed app and compare cold/warm Qwen and Whisper
  attempts without changing production capture parameters first.
- Do not select a sample-rate, timeout, retry, or permission fix until the last
  completed stage of a failed attempt is captured.

Acceptance:

- Runtime logs rotate and never contain transcript or audio content.
- Unit tests prove one handler is installed and startup stage markers remain
  correlated and ordered.
- At least one installed-app failure or a bounded repeated-pass result is recorded
  before REALTIME-START-01 changes behavior.

Result: the diagnostic build reproduced the failure on its first attempt. The
last application marker was `stream_open.begin`; the thread was blocked inside
PortAudio `Pa_OpenStream` while CoreAudio requested microphone access. macOS TCC
then reported that the prior permission's code requirement did not match the new
ad-hoc build CDHash and remained in `AUTHREQ_PROMPTING`. The default device was
the 48 kHz built-in MacBook Pro microphone, but the block occurred while assigning
the AUHAL device/IOProc, before stream activation; sample rate is not the proven
cause. See `docs/realtime-start-diagnosis.md`.

### REALTIME-START-01 — Bounded microphone authorization

- Query TCC before recording and request first-use access through AVFoundation,
  before loading a model, creating a Session/WAV, or opening PortAudio.
- Enforce the same authorization boundary inside `API.start_realtime`; HTTP/MCP
  also preflights Mic and returns `capture_unavailable` rather than prompting.
- Keep the permission wait cancelable in the UI and bound the native request to
  60 seconds. Denial, timeout, cancellation, or bridge failure restores the idle
  controls and never calls the realtime backend.
- Persist only explicitly marked startup diagnostics. Rotation is serialized
  across overlapping app processes, and the audio callback only signals an
  event; ordinary logs, transcript text, paths, and file I/O stay out of it.
- Treat stable Developer ID signing/notarization as the production upgrade fix;
  an edited ad-hoc build still has to be authorized as a new code identity.

Acceptance passed on installed 2.0.1: the first-use native prompt was restored,
the owner confirmed Mic recording, repeated System capture passed after resetting
the stale 2.0.0 ad-hoc TCC row, and Mix passed start/pause/resume/stop with a
non-empty signal. Stable Developer ID upgrade continuity remains REL-TCC-01.

2026-09-06 follow-up: after resetting the stale ad-hoc TCC grant, first-use Mic
still failed without a prompt. The dynamic AVFoundation loader lacked PyObjC
completion-block metadata; the real call raised `TypeError: Argument 3 is a block,
but no signature available` before reaching TCC. Mock-only tests missed this.
The 2.0.1 candidate registers the native `void (^)(BOOL)` signature explicitly,
adds a fresh-process macOS bridge regression, and records request exceptions in
startup diagnostics. Undetermined permission now invites the native request,
not a visit to a Settings list where the app does not yet exist. This is a
separate defect from ad-hoc signing identity continuity above.

### CAPTURE-PERM-01 — Mode-specific macOS permission UX

- Mic requires only Microphone; System requires only Screen & System Audio Recording;
  Mix requires both.
- Preflight reports both statuses and an ordered `missing_permissions` list. The GUI
  requests each missing permission separately and exposes separate settings actions.
- The synchronous CoreGraphics system-audio request is process-wide single-flight, so
  cancel, timeout, and immediate retry cannot create concurrent native requests.
- HTTP/MCP never trigger privacy prompts and reject any missing permission before
  model, Session/WAV, helper, or stream startup.
- Package both `NSScreenCaptureUsageDescription` and the newer
  `NSAudioCaptureUsageDescription`, in addition to the microphone description.
- Preserve runtime Mix degradation for device/converter failure only after both
  required permissions have passed preflight.

Installed 2.0.1 acceptance: System session `acda4f17` captured 8.80 seconds
with peak volume -20.9 dB; Mix session `66212e94` passed pause/resume and captured
6.88 seconds with peak volume -30.1 dB. The permission reset removed the prior
ad-hoc CDHash mismatch; no later `Failed to match existing code requirement`
event was present.

### REL-TCC-01 — Recording permission continuity across upgrades

- Ad-hoc candidates must be labeled local-only. Reset and re-authorize their Microphone
  and Screen & System Audio Recording decisions before fresh-build capture testing, since
  a rebuilt ad-hoc binary has a different code identity.
- A distributable release must use a stable signing identity and pass an upgrade test from
  the previous released DMG without resetting TCC. Mic, System, and Mix must all keep
  working under the existing grants.
- Run the clean-install permission flow independently after an intentional reset or in a
  clean macOS account. Do not use that reset flow as evidence of upgrade compatibility.
- Capture both releases' signing authority, TeamIdentifier, bundle ID, designated
  requirement, permission state, and successful non-empty recordings in release evidence.
- If an ordinary upgrade needs `tccutil`, permission-row deletion, or repeated Settings
  toggles, it is release-blocking rather than a user workaround.

R03-a, the synchronous local CLI and shared service layer, is complete. R03-b
is no longer ahead of Mac inference work: the CLI already supplies local agent
access, while Qwen CPU latency is the more immediate product constraint on
Apple Silicon.

### ASR-LANG-01 — Canonical transcript language metadata

- Normalize backend language names and common ISO variants to stable short codes.
- Correct a reported language only when transcript script evidence is strong; do not
  treat isolated English names in Chinese text as code-switching.
- Report substantial Chinese/English code-switching as `mixed` and preserve both
  languages in summary instructions.
- Apply the same logic to file, realtime, history, detail, and CLI paths. Legacy files
  are normalized when read without an automatic on-disk migration.
- Real regression fixtures: the 12-second Chinese, 8-second English, and 104-second
  bilingual clips recorded in the AudioAssist CLI 2.0.0 smoke run.

## Immediate task details

### RUNTIME-01 — Realtime local-model-only gate (P0)

`RealtimeTranscriber` and `NativeCaptureHelper` currently construct Qwen and
Whisper adapters without the local model path selected by `ModelManager`. That
can ignore app-managed weights and fall back to a Hugging Face repository ID.

- Resolve and validate the selected ASR model before starting either capture
  implementation.
- Pass its explicit local path through mic, system, and mix modes.
- Qwen realtime must set `with_timestamps=False` and must not load the Forced
  Aligner. Whisper must receive its validated local directory.
- Missing/incomplete models fail with the existing model-not-ready behavior;
  they must never start a download.

Acceptance:

- Unit tests cover Qwen and Whisper in both capture implementations, selected
  model propagation, missing/incomplete models, and no repository-ID fallback.
- Mic and system/mix short recordings pass with Hugging Face/Transformers
  offline mode enabled.

### ENV-01 — One repository-owned Python environment

Scope:

- Make `.venv` under the repository the canonical interpreter.
- Put runtime, platform-specific inference, build, and test dependencies behind
  documented install groups and generate a reviewed lock file.
- Add an idempotent bootstrap command.
- Make the installed `audioassist` command, source application, tests, and
  `scripts/build_dmg.sh` use that interpreter.
- Remove the personal `/Users/feifei/programing/local asr/.venv` path from
  `AudioAssist.spec`; resolve package data from the active build interpreter.
- Do not move or redownload application-managed model weights.

Acceptance:

- A clean checkout can bootstrap without depending on another checkout or the
  global pyenv site-packages.
- CLI and build diagnostics report the same Python prefix.
- The 669-test baseline passes.
- Short Qwen and Whisper jobs pass with Hugging Face/Transformers offline mode.
- The packaged app contains no developer-only absolute runtime path.

### QWEN-01 — Stable backend seam and lifecycle

Follow `docs/qwen-asr-mac-backend-decision.md`, phase 1:

- Keep the public CLI/UI/checkpoint value `engine="qwen"` unchanged.
- Introduce an application-owned Qwen protocol and internal backend factory.
- Preserve the existing PyTorch CPU/CUDA adapter.
- Add PyTorch MPS only as a development diagnostic override; configure the ASR
  model and Forced Aligner device/dtype independently.
- Pass validated local paths in offline and realtime flows. No transcription
  job may download a model or rely on a coincidental global cache.
- When timestamps are requested, require a validated local Forced Aligner or
  return an explicit visible readiness error instead of silently degrading.
- Add `close()` and release Qwen/aligner memory before loading pyannote, including
  success, cancellation, and exception paths.
- Realtime loads only the ASR model, reuses one instance, and keeps one serial
  inference worker.

Acceptance:

- Unit tests cover platform selection, override validation, lazy imports,
  local-path-only construction, independent aligner configuration, cleanup,
  realtime reuse, visible fallback, and checkpoint compatibility.
- Existing CLI JSON, exit code, transcript, Markdown, and session contracts do
  not change.
- CPU real-audio behavior remains the baseline for later comparisons.

### QWEN-02 — MLX full-precision adapter

- Pin and review one focused MLX Qwen implementation; do not depend on two
  competing Qwen MLX frameworks.
- Verify whether the existing official 1.7B and Forced Aligner directories can
  be consumed directly. Any converted artifact must be a separate managed model
  entry and must not silently mutate official weights.
- Convert output into the existing `TranscriptResult` and `WordSegment` types.
- Preserve detected language and per-chunk timestamps.
- Package module/runtime data in `AudioAssist.spec` without duplicating MLX.
- Keep 4-bit/8-bit and the 0.6B realtime profile outside this first change.

Acceptance:

- Focused adapter, conversion, frozen-import, local-model, and packaging tests
  pass.
- Source and packaged-app smoke jobs run with networking disabled.

### QWEN-03 — Benchmark and promotion gate

- Compare pinned PyTorch CPU, diagnostic PyTorch MPS, and MLX full precision on
  Mandarin, Cantonese, English, code-switching, noisy/quiet audio, realtime
  utterances, five-minute chunks, and 30/60/120-minute meetings.
- Record cold/warm latency, realtime p95, real-time factor, peak memory,
  CER/WER, truncation, language agreement, timestamp differences, cleanup time,
  and repeated start/stop stability.
- Include source-tree and signed-app results.

MLX becomes the Apple Silicon default only after the gates in the Qwen decision
document pass. The current evidence and hold decision are recorded in
`docs/qwen-benchmark-results.md`: MLX remains explicit and `auto` is unchanged.
Promotion-level gaps move to QWEN-04; they do not silently disappear.

### REL-01 — Reproducible macOS release

- Build the Swift helper and DMG from the canonical environment.
- Sign/notarize as applicable and run `docs/release-checklist.md` on a machine
  without the development checkout or shared virtual environment.
- Verify first-run model setup, offline jobs, capture permissions, and package
  resource discovery.
- Correct product text that describes MLX as Neural Engine acceleration; the
  current implementation uses the Apple GPU through Metal.

## P2 product and reliability backlog

### PLAYER-02 — Session-safe playback state

- Route every selected-session transition through one helper that stops the prior
  audio only when the selected session actually changes.
- Make loading the same source idempotent so rename/status rerenders preserve its
  current position and playback state.
- Pause and reset immediately before a different source is loaded, and publish a
  zero-time update so the previous session's position cannot remain visible while
  the new media metadata loads.
- Browser regression covers both same-source preservation and different-source
  pause/reset behavior.

### UI-SESSION-REFRESH-01 — Durable session labels and transitions

- Apply valid renames optimistically so a status rerender cannot restore the old
  title while persistence waits behind the session mutation lock. Serialize
  PyWebView rename calls per session so rapid edits remain last-write-wins, and
  restore the last confirmed title if one or more writes fail.
- Make new-job renames wait on the same session lock as transcription and recheck
  for the completed transcript before persisting, preventing a restart from
  revealing a stale pre-transcription title.
- Preserve the renamed title in `meta.json` for interrupted sessions and read it
  back in history instead of falling back to the source basename.
- Treat immediate and background resume errors or cancellation as interrupted and
  retryable rather than leaving the session indefinitely marked as transcribing or
  hiding its Resume action.
- Backend and browser regressions cover renamed interruption, a rename during a
  fresh transcription, rapid ordered renames, failed-write rollback, pending rename
  plus completion, and immediate/background resume failures without restarting.

| ID | Task | Acceptance direction |
|---|---|---|
| QWEN-04A | Accelerated-backend evidence | Human Mandarin/Cantonese/English/code-switch references plus short and 5/10-minute fixtures compare CPU, MPS, and MLX before longer promotion runs |
| QWEN-04B | Conditional Mac default promotion | The selected backend must pass lifecycle, offline, checkpoint, packaged-app, and approved long-audio gates before changing `auto` |
| R03-B1 | Complete: HTTP service lifecycle/security design | Approved bounded v1 contract in `docs/http-mcp-contract.md`; no daemon or background inference queue |
| R03-B2 | Complete: HTTP recording control plus MCP adapter | Shared-service reads work with the app closed; recording mutations require a live authenticated visible app and return stable errors |
| AGENT-01 | Summary Agent reliability (paused by owner) | Request/tool timeouts, transcript paging instead of a fixed 6000-character truncation, and summary writes through the shared service |
| TEST-UI-01 | Complete: close frontend coverage gaps | Eight browser scenarios cover startup/history, setup, cancel/retry, model modal, drag/drop queueing, and Summary Agent flows |
| TRANS-01 | Complete: cancellation and concurrency hardening | Immediate cancel registration, duplicate-job rejection, transactional output/refine, retryable refine cancellation, and serialized rename/rerun mutations |
| ASR-QUALITY-01 | Complete: speaker/timestamp quality | Interval-overlap assignment, conservative dominance/coverage gates, and bounded same-speaker gap repair shared by file and realtime paths |
| TEXT-01 | Complete: conservative punctuation | Pause-backed internal terminals, transcript-final punctuation, punctuation de-duplication, and Chinese/Japanese/mixed/non-ASCII language fixtures; no semantic rewriting |
| MAC-AUDIO-01 | Mix-mode echo cancellation | Define measurable echo/reference-signal acceptance before implementation |
| WIN-AUDIO-01 | Windows system-audio capture and EXE | Select a Windows capture backend, add lifecycle tests, then package and smoke-test on Windows |
| CLOUD-ASR-01 | Optional cloud ASR fallback | Privacy/consent, provider abstraction, cancellation, cost visibility, and local-default behavior required |
| OBSIDIAN-01 | Deeper Obsidian integration | Define concrete bidirectional or workflow value beyond the existing sync before scheduling |

## P3 maintenance backlog

LOCAL-API-CLEANUP-01 is complete: a dead PID, a reused live PID with an
unreachable listener, or an identity mismatch triggers ownership-checked cleanup.
Cleanup first acquires the same process lock as server startup, so it cannot unlink
a record while a replacement instance owns the service. Tests cover crash residue,
PID reuse, and lock contention.

| ID | Task |
|---|---|
| CORE-LOCK-01 | Complete: weak-value per-job locks remain shared by active owners/waiters and are evicted after use |
| UI-ERROR-01 | Complete: blocking `alert()` paths use non-blocking app toasts with actionable errors |
| PLAYER-01 | Surface actionable `seekTo()` media failures instead of only logging them |
| UI-STATE-01 | Recheck the historical player/empty-hint state transitions with browser regression tests |
| REALTIME-01 | Complete through R03-B2: pause/resume failures produce an error state and never emit false confirmed UI events |
| LOG-01 | Preserve stack traces on remaining aligner fallback/error paths |
| AUDIO-UTIL-01 | Convert nonnumeric ffprobe duration output such as `N/A` into an actionable application error |
| TEST-ASR-01 | Add direct mocked timestamp/result-conversion tests for Qwen and both Whisper adapters |
| DOC-01 | Remove stale historical warnings/checklists and keep feature/status documents consistent |

## Historical backlog audit

The following older unchecked items were audited so they are not accidentally
scheduled twice:

| Historical item | Current disposition |
|---|---|
| Session rename/delete, stop playback on switch, settings/toggle placement | Complete |
| First-run setup, cancel/retry, model download/select/delete, community-1 diarizer | Complete |
| Realtime finish full transcription | Superseded by fast realtime result plus explicit manual high-accuracy refinement |
| ModelManager local path validation | Complete, including HF-cache key-file validation |
| Local-only file transcription | Complete; retain as a regression requirement for every backend |
| Local-only realtime transcription | Complete under RUNTIME-01; retain as a regression requirement |
| Real-audio smoke test | Complete for R03-a and retained as a recurring release gate |
| macOS DMG support | Implemented; reproducible clean-machine release verification remains REL-01 |
| Windows EXE | Still open under WIN-AUDIO-01 |
| Qwen MPS fix | Re-scoped into QWEN-01 through QWEN-03; MPS is diagnostic and MLX is the candidate default |
| Spacebar playback and stale summary reset | Complete |
| Summary spinner completion | Complete |
| Storage path setting and complete session deletion | Complete |
| JS/Jest debt | Closed through browser-level TEST-UI-01 coverage rather than introducing a second frontend test stack without need |
| Setup/cancel/retry/model/drag-drop/Agent UI coverage | Complete under TEST-UI-01 |
| Drag/drop concurrency and refine cancellation | Complete through TEST-UI-01 and TRANS-01 |
| Agent timeouts and fixed transcript truncation | Still open under AGENT-01 |
| Per-job lock growth, seek errors, alerts, pause/resume state | Still open under the corresponding P3 maintenance IDs |
| ffprobe `N/A` and direct ASR timestamp adapter tests | Still open under AUDIO-UTIL-01 and TEST-ASR-01 |
| UNKNOWN speaker, punctuation, aligner logging | Speaker and punctuation work complete as ASR-QUALITY-01/TEXT-01; aligner logging remains LOG-01 |
| Windows system audio and mix echo cancellation | Still open as WIN-AUDIO-01 and MAC-AUDIO-01 |

## Deferred ideas, not committed work

- Qwen 0.6B or quantized realtime profiles, only after full-precision MLX gates.
- Cross-session semantic search/embeddings and app-wide agent memory.
- Mobile/recorder ingestion and local/cloud collaborative workflows.
- Changing capture mode during an active recording.
