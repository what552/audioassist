# QWEN-REALTIME-MLX-01 Development Plan

Date: 2026-09-08  
Status: Complete; 2.0.5 installed owner acceptance passed
Target release: 2.0.5

## Problem

AudioAssist 2.0.4 can complete file transcription with both PyTorch MPS and
MLX, but realtime Mic, System, and Mix sessions fail when MLX is selected.
CPU and MPS realtime sessions remain functional.

The current capture adapters load the ASR backend in the asynchronous recording
startup thread and then use it from a separate serialized transcription worker.
That arrangement is accepted by the current PyTorch CPU/MPS runtime, but MLX
binds its Metal stream to the thread that created it.

The local reproduction is deterministic:

- load MLX in one thread and transcribe in another: fails with
  `There is no Stream(gpu, 1) in current thread.`;
- load and transcribe a 4-second local sample in one clean worker thread: passes.

This is separate from QWEN-BOUNDARY-01 and from macOS recording permissions.

## Current and target ownership

Current:

```text
recording startup thread: create + load ASR
capture callback/PCM thread: collect audio + enqueue utterances
ASR worker thread: transcribe + close ASR
```

Target:

```text
recording startup thread: start ASR worker, wait for ready, then open capture
capture callback/PCM thread: collect audio + enqueue utterances only
ASR worker thread: create + load + transcribe serially + close ASR
```

The desktop API also holds one cancelable session lease from backend
construction until the owner worker has fully exited. A stop during model load
marks that lease cancelled, and a new session cannot start while the old lease
is starting or stopping.

CPU, MPS, MLX, and MLX Whisper will use the same ownership contract. There is
no fallback to another backend and no change to the saved user preference.

## Scope

1. Make the existing Mic and NativeCapture ASR workers own backend creation,
   model loading, all inference, and cleanup.
2. Add an explicit ready/error handshake so capture never starts before the ASR
   backend is usable, and model-load failures return through the existing GUI
   error path instead of leaving Loading state behind.
3. Preserve serialized inference, queue draining, pause/resume, and the existing
   native helper/FIFO ordering.
4. Log the snapshotted Qwen backend and the worker lifecycle so packaged failures
   can be attributed to CPU, MPS, or MLX.
5. Keep reusable model-resolution and construction logic presentation-agnostic
   under `src/`.

## Non-goals

- No changes to microphone or Screen & System Audio permission logic.
- No changes to the Swift capture helper, file transcription, Forced Aligner,
  diarization, session layout, or transcript schema.
- No automatic CPU/MPS fallback when MLX fails.
- No daemon, cross-process job manager, or UI redesign.

## Implementation sequence

1. Add failing thread-affinity tests for `RealtimeTranscriber` and
   `NativeCaptureHelper`. The fake backend records thread IDs for load,
   transcription, and close; all must match.
2. Add worker ready/error state and move backend construction/loading into each
   serialized ASR worker without moving inference into an audio callback.
3. Make startup failure and post-capture cleanup join the worker without closing
   a live backend from a foreign thread. A native 30-second drain timeout keeps
   the API in `stopping` and retains the lease until late cleanup finishes.
4. Add restart, load-failure, capture-open-failure, pause/resume, and stop-during-
   startup regression coverage.
5. Add backend identity to recording startup diagnostics.
6. Run focused tests, frontend tests if callbacks change, then the complete suite.
7. Run a real local 4-second MLX worker probe plus source Mic/System/Mix checks.
8. Commit the exact implementation and obtain an independent concurrency review.
9. After the review gate passes, bump 2.0.4 to 2.0.5, build a new App/DMG, run
   mechanical verification, and perform installed CPU/MPS/MLX × Mic/System/Mix
   acceptance. Privacy-permission checks remain a separate release gate.

## Acceptance criteria

- MLX backend load, every realtime inference call, and close occur on the same
  worker thread for Mic, System, and Mix.
- CPU and MPS retain their current recording behavior under the same lifecycle.
- Capture starts only after ASR ready; a load error restores the UI to a retryable
  non-recording state.
- Stop drains accepted utterances, releases the backend once, and leaves no live
  ASR worker. Repeated start/stop does not reuse a dead queue or stale backend.
- No ASR work occurs in the sounddevice callback or native PCM reader.
- The selected backend remains explicit and never silently falls back.
- `python -m pytest -q` and the applicable frontend suite pass.
- The installed 2.0.5 App passes all nine backend/capture combinations, with
  non-empty WAV frames and at least one expected realtime result for voiced tests.

## Release policy

2.0.4 remains valid for file transcription, including MLX. Until 2.0.5 passes
the installed gate, realtime recording should use CPU or MPS. The 2.0.5 App and
DMG are local ad-hoc test candidates, not distributable signed upgrades.

## Source implementation result

The Mic and NativeCapture serialized workers now establish a ready/error
handshake, load the selected backend inside the worker, process every queued
utterance there, and close the backend in that same worker. Capture opens only
after model readiness. Packaged runtime diagnostics now include the snapshotted
Qwen backend and the resolved backend after load.

Verification completed before review:

- the new ownership tests failed twice on 2.0.4, then passed after the change;
- focused realtime/native tests: 161 passed;
- complete suite outside the restricted browser/socket sandbox: 1078 passed,
  957 dependency warnings;
- explicit frontend suite: 38 passed;
- real 4-second `RealtimeTranscriber` probes passed on CPU, MPS, and MLX with a
  fake audio device and clean worker shutdown;
- a real-MLX `NativeCaptureHelper` probe passed with a fake helper process,
  exercising the System/Mix ASR path without capturing user audio.

No version bump or installable artifact has been produced. Independent review,
source capture checks, 2.0.5 packaging, and installed nine-combination acceptance
remain open.

## First independent review and remediation

The first exact-commit concurrency review rejected `2feeac3` with two P1
findings: startup used an un-cancelable placeholder that allowed a new MLX
session to overlap a slow old load, and NativeCapture reported success after a
30-second worker timeout while the old backend was still alive.

The remediation adds:

- a cancelable startup lease published before backend construction;
- bounded, cancellation-aware model readiness waits;
- serialized capture activation and teardown;
- exactly-once worker stop signalling;
- retention of the app lease and `stopping` state until Native PCM/ASR workers
  have actually exited;
- regression tests for stop-during-load, blocked restart, late Native shutdown,
  model-load timeout, and factory/create/load/infer/close thread identity.

Post-remediation verification:

- focused realtime/native/app lifecycle tests: 207 passed;
- complete suite outside the restricted browser/socket sandbox: 1082 passed;
- explicit frontend suite: 38 passed;
- real 4-second Mic-path probes passed on `pytorch-cpu`, `pytorch-mps`, and
  `mlx`, with a non-empty result and no surviving worker;
- the real MLX NativeCapture ASR path passed with a fake helper process.

No version bump or installable artifact has been produced. Exact-commit
re-review and the 2.0.5 package/install matrix remain open.

## Second independent review and remediation

The second review confirmed both original P1 findings were fixed, then found a
remaining P1 in application shutdown: `shutdown()` still understood only a
fully-started backend, not the new startup lease. Closing AudioAssist during
model load could therefore clear app state without cancelling the late capture
and owner worker.

The shutdown path now cancels either a startup lease or an active backend,
waits synchronously for a late Native worker when required, and releases the
lease only after cleanup succeeds. Two additional tests cover application exit
during blocked startup and exit during a delayed Native worker shutdown.

Post-fix focused realtime/native lifecycle tests: 209 passed. The final full
suite passed 1084 tests with one dependency warning.

The third independent review examined exact commit `23dcf88` and passed it with
no P0, P1, or P2 findings. It confirmed shutdown-during-load, delayed Native
shutdown, normal stop, owner-thread identity, single-sentinel cleanup, and
CPU/MPS/Whisper routing. Source implementation is therefore accepted; 2.0.5
packaging and installed recording checks are the remaining release gates.

## 2.0.5 package result

`bash scripts/build_dmg.sh` refreshed the editable installation to 2.0.5 and
produced `dist/AudioAssist.app`, `dist/AudioAssist-2.0.5.dmg`, and
`dist/release-verification-2.0.5.json`. Mechanical verification passed with 491
Mach-O files, 584 symlinks, aligned source/App/DMG metadata, required Qwen/MLX
assets, and a matching read-only mounted payload. The DMG SHA-256 is
`3794c591df2a66062affd62548e0f4c39050055d33cbb0e19199e63bef09008e`.

The package is ad-hoc signed. The owner installed 2.0.5 and reported no issues.
Runtime logs confirm successful MPS Mic, MLX Mic, and MLX Mix startup, and the
three saved 16 kHz mono WAV files contain 68.26, 45.86, and 82.88 seconds of
audio with persisted transcripts of 18, 12, and 26 segments. Because Mix uses
the shared NativeCapture system-audio path, this closes the reported MLX
realtime failure. Stable Developer ID upgrade continuity remains outside this
local candidate and is tracked separately by REL-TCC-01.
