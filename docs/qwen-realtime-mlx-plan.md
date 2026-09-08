# QWEN-REALTIME-MLX-01 Development Plan

Date: 2026-09-08  
Status: Source implemented and verified; independent review pending
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
   a live backend from a foreign thread. Preserve the native 30-second bounded
   drain behavior.
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
the installed gate, realtime recording should use CPU or MPS. The version will
not be bumped and no App/DMG will be generated until source implementation,
tests, and independent review pass.

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
