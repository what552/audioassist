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
| 12 | ASR-QUALITY-01 | P2 | Next | Improve speaker ownership and realtime alignment without hiding uncertainty |
| 13 | TEXT-01 | P2 | Planned | Add conservative, language-aware punctuation improvement |
| 14 | R03-B1 | P2 | Needs design gate | Specify the HTTP process lifecycle, locking, recovery, cancellation, and local security boundary |
| 15 | R03-B2 | P2 | Depends on R03-B1 | Implement HTTP recording control and the separate MCP adapter |

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

| ID | Task | Acceptance direction |
|---|---|---|
| QWEN-04A | Accelerated-backend evidence | Human Mandarin/Cantonese/English/code-switch references plus short and 5/10-minute fixtures compare CPU, MPS, and MLX before longer promotion runs |
| QWEN-04B | Conditional Mac default promotion | The selected backend must pass lifecycle, offline, checkpoint, packaged-app, and approved long-audio gates before changing `auto` |
| R03-B1 | HTTP service lifecycle/security design | Explicit ownership, localhost boundary, port collision, lock, recovery, cancellation, shutdown, and versioned schema before implementation |
| R03-B2 | HTTP recording control plus MCP adapter | Reuse `src/service.py`; data reads work with the app closed; recording mutations require the visible app and return stable errors |
| AGENT-01 | Summary Agent reliability (paused by owner) | Request/tool timeouts, transcript paging instead of a fixed 6000-character truncation, and summary writes through the shared service |
| TEST-UI-01 | Complete: close frontend coverage gaps | Eight browser scenarios cover startup/history, setup, cancel/retry, model modal, drag/drop queueing, and Summary Agent flows |
| TRANS-01 | Complete: cancellation and concurrency hardening | Immediate cancel registration, duplicate-job rejection, transactional output/refine, retryable refine cancellation, and serialized rename/rerun mutations |
| ASR-QUALITY-01 | Speaker/timestamp quality | Evaluate UNKNOWN-speaker reassignment and realtime diarization alignment without hiding uncertainty |
| TEXT-01 | Conservative punctuation improvement | Language-aware evaluation; no semantic rewriting |
| MAC-AUDIO-01 | Mix-mode echo cancellation | Define measurable echo/reference-signal acceptance before implementation |
| WIN-AUDIO-01 | Windows system-audio capture and EXE | Select a Windows capture backend, add lifecycle tests, then package and smoke-test on Windows |
| CLOUD-ASR-01 | Optional cloud ASR fallback | Privacy/consent, provider abstraction, cancellation, cost visibility, and local-default behavior required |
| OBSIDIAN-01 | Deeper Obsidian integration | Define concrete bidirectional or workflow value beyond the existing sync before scheduling |

## P3 maintenance backlog

| ID | Task |
|---|---|
| CORE-LOCK-01 | Bound or evict the per-job `_transcript_locks` registry in long-running processes |
| UI-ERROR-01 | Replace remaining blocking `alert()` paths with the app error/toast pattern |
| PLAYER-01 | Surface actionable `seekTo()` media failures instead of only logging them |
| UI-STATE-01 | Recheck the historical player/empty-hint state transitions with browser regression tests |
| REALTIME-01 | Harden pause/resume when the worker has already failed, and avoid reporting a UI transition before backend confirmation |
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
| UNKNOWN speaker, punctuation, aligner logging | Still open as ASR-QUALITY-01, TEXT-01, and LOG-01 |
| Windows system audio and mix echo cancellation | Still open as WIN-AUDIO-01 and MAC-AUDIO-01 |

## Deferred ideas, not committed work

- Qwen 0.6B or quantized realtime profiles, only after full-precision MLX gates.
- Cross-session semantic search/embeddings and app-wide agent memory.
- Mobile/recorder ingestion and local/cloud collaborative workflows.
- Changing capture mode during an active recording.
