# Qwen3-ASR on macOS: PyTorch MPS vs MLX

Status: QWEN-02 MLX adapter complete; MLX not yet approved as the production default
Research date: 2026-09-05; upstream rechecked 2026-09-06  
Scope: AudioAssist Qwen3-ASR inference on Apple Silicon macOS

Tracked implementation batches: `QWEN-01` through `QWEN-03` in
[`docs/development-backlog.md`](development-backlog.md). Complete `ENV-01`
before changing the inference backend so dependency and package measurements
come from one reproducible environment.

## Executive decision

Use platform-specific Qwen3-ASR backends instead of choosing one backend for
every operating system:

| Platform | Default Qwen backend | Purpose |
| --- | --- | --- |
| Apple Silicon macOS | MLX | Primary optimized Mac implementation |
| NVIDIA CUDA | Official `qwen-asr` PyTorch backend | Supported GPU path outside macOS |
| Intel Mac / CPU-only Windows or Linux | Official `qwen-asr` PyTorch CPU backend | Compatibility fallback |
| Apple Silicon, diagnostic override | Official PyTorch MPS backend | Parity testing and troubleshooting only |

Do not expose PyTorch MPS as the normal Mac default until it passes the same
long-audio, realtime, memory, and packaged-app gates as MLX. Current evidence
shows that PyTorch MPS can execute Qwen3-ASR, but it is not documented as a
supported upstream Qwen platform and did not improve latency on the short local
smoke test.

The recommended implementation is an internal backend factory behind the
existing `engine="qwen"` contract. Do not add a new public CLI engine value or
duplicate pipeline behavior.

## Why this decision is needed

The current Qwen wrapper in `src/asr.py` selects CUDA when available and CPU in
all other cases. It does not test `torch.backends.mps.is_available()`. The code
and `README.dev.md` also state that MPS causes SIGBUS. That statement no longer
matches the current local runtime test, but it may reflect a failure in an older
PyTorch, Transformers, Qwen, or macOS combination.

AudioAssist already uses both framework families:

- `mlx-whisper` is the current Apple Silicon Whisper backend.
- PyTorch remains necessary for Silero VAD and pyannote diarization.
- Realtime ASR is serialized through a single worker, which is compatible with
  Metal execution.
- The PyInstaller specification already bundles MLX and its Metal resources.

Consequently, adopting MLX for Qwen does not remove PyTorch from the packaged
application. The benefit is Qwen inference performance and a better Mac-native
runtime, not elimination of the PyTorch dependency or a large guaranteed
reduction in application size.

## Evidence collected in this repository environment

The following test was run outside the restricted execution sandbox so the
Metal device was visible:

| Item | Value |
| --- | --- |
| Machine architecture | `arm64` Apple Silicon |
| macOS | 26.5.1 |
| Python | arm64 Python 3.12 environment used by the app build |
| PyTorch | 2.10.0 |
| `qwen-asr` | 0.0.6 |
| MLX | 0.31.1 |
| ASR model | Qwen3-ASR 1.7B, existing local weights |
| Aligner | Qwen3-ForcedAligner 0.6B, existing local weights |

Observed results:

- PyTorch reported MPS as built and available.
- A basic MPS tensor operation succeeded.
- Qwen3-ASR 1.7B loaded with `device_map="mps"` and `float16`.
- Qwen3-ForcedAligner 0.6B also loaded on MPS when given its own device and
  dtype arguments.
- Transcription and word timestamps completed without SIGBUS.
- CPU and MPS produced identical text and timestamp output for the same smoke
  sample.
- On the 0.9-second synthetic sample, warmup was not separated from inference:
  MPS inference took about 6.45 seconds and CPU inference took about 4.02
  seconds. This is not a representative long-audio benchmark, but it shows that
  MPS availability alone does not guarantee lower realtime latency.

Important limitation: this smoke test proves that the current operator path can
run. It does not establish production stability, long-audio correctness,
thermal behavior, cancellation behavior, or compatibility inside the signed
PyInstaller application.

## Upstream and community status

The official [Qwen3-ASR repository](https://github.com/QwenLM/Qwen3-ASR)
documents native Transformers and vLLM backends and demonstrates CUDA device
selection. Its 2026-06-26 update added native Transformers support, but it still
does not document Apple MPS as a supported target.
PyTorch MPS therefore remains an uncommitted upstream path even when it works
with the versions above.

The community [`mlx-qwen3-asr`](https://github.com/moona3k/mlx-qwen3-asr)
project (latest rechecked release: v0.3.5) provides a native MLX implementation
that accepts the official weights by repository ID or local path for Qwen3-ASR 0.6B/1.7B, long
audio, streaming, and the Qwen3 Forced Aligner. Its published benchmarks claim:

- about 4.19x the PyTorch speed for 75-90 second long-form samples;
- about 3.27x the PyTorch speed on a mixed meeting/earnings test lane;
- about 2.64x the official aligner speed, with 5.69 ms mean word-boundary
  difference in its aligner parity test;
- near aggregate quality parity, although only 67% of its multilingual samples
  produced exactly identical text.

These numbers are maintained by that community project and must be reproduced
on AudioAssist workloads before being used in product claims.

[`mlx-audio`](https://github.com/Blaizzy/mlx-audio) is another possible MLX
implementation. It supports Qwen3-ASR and Qwen3-ForcedAligner, but it is a broad
audio framework and has had historical long-input issues. Prefer evaluating the
more focused `mlx-qwen3-asr` adapter first. Do not depend on both Qwen MLX
implementations.

MLX in this design uses the Apple GPU through Metal. Do not describe this as
Apple Neural Engine acceleration in the UI, model catalog, README, or release
notes unless a separate ANE implementation is introduced and verified.

## Required architecture

### Keep the public contract stable

The stable external engine name remains `qwen`:

```text
CLI / desktop API / saved checkpoint
                |
                v
          engine="qwen"
                |
                v
       internal backend factory
       /          |          \
  MLX/Metal   PyTorch/CUDA   PyTorch/CPU
```

Backend selection is an implementation detail. Existing session checkpoints,
CLI output, error codes, JSON output, Markdown output, and UI model selection
must remain compatible.

Recommended automatic selection:

```text
if macOS and arm64 and MLX Qwen backend is installed:
    qwen-mlx
elif CUDA is available:
    qwen-pytorch-cuda
else:
    qwen-pytorch-cpu
```

An internal diagnostic override may select `pytorch-mps`, for example through a
documented development-only environment variable. Do not add it to the normal
model picker during the first implementation.

If automatic startup falls back because the preferred backend cannot be
initialized, log and surface the actual selected backend. Never switch backend
silently after a transcription job has begun: retrying mid-job can duplicate
large model allocations and can mix different transcript behavior in one
checkpoint.

### Preserve the service boundary

Both backends must implement the same narrow application-owned protocol:

```python
class QwenASREngineProtocol:
    def load(self) -> None: ...
    def transcribe(self, audio_path: str) -> TranscriptResult: ...
    def close(self) -> None: ...
```

Suggested organization:

```text
src/asr.py                 # compatibility facade or existing PyTorch adapter
src/asr_qwen_mlx.py        # MLX adapter only
src/asr_factory.py         # platform/backend selection
src/types.py               # unchanged application-owned result types
src/pipeline.py            # calls the factory; owns workflow and persistence
```

Do not import PyWebView, frontend code, checkpoint persistence, model-download
logic, diarization, or output writers into either backend adapter. Do not use an
MLX library's server, output writers, diarization pipeline, or job system.
AudioAssist's `src/service.py` and `src/pipeline.py` remain the shared source of
truth.

### PyTorch adapter requirements

The official Qwen wrapper treats the ASR model and Forced Aligner as separate
loads. A PyTorch MPS implementation must configure both explicitly:

```python
Qwen3ASRModel.from_pretrained(
    asr_path,
    forced_aligner=aligner_path,
    device_map="mps",
    dtype=torch.float16,
    forced_aligner_kwargs={
        "device_map": "mps",
        "dtype": torch.float16,
    },
)
```

The final code must use the parameter spelling supported by the pinned
`qwen-asr`/Transformers versions. Do not assume that passing the outer
`device_map` moves the aligner; it does not.

CPU should remain float32 unless a separately verified CPU precision mode is
introduced. CUDA precision should follow the official supported configuration.

### MLX adapter requirements

The first MLX implementation should prioritize behavior preservation:

- Load only from the local model path resolved by `ModelManager`.
- Do not allow Hugging Face auto-download during a transcription job.
- Start with the existing Qwen3-ASR 1.7B and Forced Aligner catalog entries.
- Convert the MLX result into the existing `TranscriptResult` and
  `WordSegment` types.
- Preserve detected-language semantics expected by the merge layer.
- Preserve per-chunk timestamps starting at zero; the existing pipeline adds
  each five-minute chunk offset.
- Keep one loaded session for all chunks in a job and for all utterances in a
  realtime session.
- Serialize inference through the existing worker instead of adding internal
  application-level concurrency.

The candidate library documents loading official Qwen weights from a local
path, but this must be verified against the exact existing model directories.
If conversion creates additional files, they must live in an explicit
app-managed model directory and must not mutate the official weight directory
silently.

Do not introduce 4-bit or 8-bit models in the first backend migration. Quantized
variants change accuracy, storage, catalog semantics, and benchmarking scope;
add them as a separate feature after the full-precision backend passes.

### Realtime mode

Realtime transcription uses VAD-derived utterance boundaries and does not need
the Forced Aligner. The Qwen backend should therefore load only the ASR model in
realtime mode.

Retain these properties:

- exactly one long-running ASR worker per realtime session;
- no inference in the audio callback;
- no concurrent Metal calls against the same session;
- model loaded once on start, retained across utterances, and released on stop;
- pause/resume does not reload the model;
- errors reach the current callback without corrupting the WAV recording.

Evaluate Qwen3-ASR 0.6B or an 8-bit variant for realtime only after the initial
1.7B integration. Using different realtime and high-accuracy models requires
explicit model IDs in checkpoints and UI state; it must not be an invisible
quality change.

### Offline pipeline and memory lifecycle

The current offline pipeline performs ASR and then loads pyannote diarization.
It retains the ASR object while diarization begins. With a 1.7B ASR model, a
0.6B aligner, and pyannote sharing unified memory, this can create unnecessary
peak pressure.

After the final ASR chunk and before loading diarization:

1. preserve the completed `TranscriptResult` in application-owned Python data;
2. call the ASR backend's `close()`;
3. release model references and clear the relevant framework cache;
4. only then initialize pyannote.

Cleanup must also run on cancellation and exceptions. Backend-specific cleanup
belongs in the adapter, not scattered through UI or service code.

## Model management and local-only requirements

AudioAssist prohibits automatic model download during transcription. This
applies equally to PyTorch and MLX.

- `ModelManager` must validate every required model before engine construction.
- The backend receives resolved local paths, never the default Hugging Face repo
  ID during a job.
- Realtime mode must also receive the selected local model path; it must not rely
  on a coincidentally populated global Hugging Face cache.
- Missing or incomplete MLX-compatible assets must produce the existing
  model-not-ready class of error, not initiate a network request.
- If a separate converted/quantized artifact is later required, represent it as
  its own catalog entry with its real disk size and deletion behavior.

## Packaging work

PyTorch MPS requires little new package collection, but the signed app must
still be tested because the earlier SIGBUS report may have been packaging-
specific.

For MLX:

- pin a reviewed `mlx-qwen3-asr` version instead of an unbounded dependency;
- collect its modules and runtime data explicitly in `AudioAssist.spec`;
- verify tokenizer/config resource lookup in a frozen app;
- verify Metal initialization after code signing and from Finder launch;
- verify the Intel/non-macOS build does not eagerly import the MLX-only module;
- verify no model download begins when the network is unavailable;
- record any additional bundle-size cost.

Since the application already bundles `mlx` and `mlx-whisper`, do not duplicate
the MLX framework tree under a second destination.

## Benchmark and acceptance plan

### Corpus

Use user-approved, non-sensitive fixtures or synthetic/public test audio that
covers:

- Mandarin, Cantonese, English, and Chinese/English code switching;
- clean close-mic speech, room recordings, noise, music, and long silence;
- 1-10 second realtime utterances;
- approximately 5-minute offline chunks;
- 30-, 60-, and 120-minute meeting recordings;
- overlapping speakers for end-to-end ASR plus diarization;
- punctuation, numbers, names, and domain vocabulary;
- Japanese and Korean when validating Forced Aligner tokenization dependencies.

Compare at least:

```text
official PyTorch CPU
official PyTorch MPS diagnostic mode
MLX full precision
```

CUDA comparison is useful for reference but is not a Mac release gate.

### Metrics

Record cold and warm measurements separately:

- model load time;
- first-utterance latency;
- warm median and p95 realtime utterance latency;
- offline real-time factor;
- peak resident/unified memory;
- transcript CER/WER and catastrophic truncation count;
- detected-language agreement;
- word timestamp median and p95 absolute boundary difference;
- time to release memory before diarization;
- repeated start/stop and pause/resume stability;
- packaged-app results, not only source-tree Python results.

### Proposed go/no-go gates for the MLX Mac default

- No crash, deadlock, runaway allocation, or silent truncation in a two-hour
  end-to-end run.
- No failure across at least 50 realtime start/stop cycles and 100 serialized
  utterances.
- Aggregate CER/WER does not regress by more than 0.5 absolute percentage points
  from the pinned official PyTorch baseline on the AudioAssist evaluation set.
- No severe per-file regression is hidden by aggregate metrics; every large
  difference is reviewed manually.
- Word-boundary median absolute difference is at most 50 ms and p95 at most
  150 ms against the official Forced Aligner baseline, unless listening review
  demonstrates the MLX boundary is more accurate.
- Warm long-form processing is at least 30% faster than the current CPU default
  on the minimum supported Apple Silicon target.
- Realtime p95 processing latency remains below the duration of the submitted
  utterance after warmup.
- The signed DMG passes the relevant checks in `docs/release-checklist.md`.

If MLX misses correctness or stability gates, keep the official PyTorch CPU
backend as the Mac default and ship MLX only after the issue is resolved. Do not
promote PyTorch MPS solely because it initializes successfully; it must meet the
same product-level gates.

## Tests required with implementation

Add focused unit tests for:

- automatic platform/backend selection;
- development override validation;
- lazy imports on unsupported platforms;
- local-path-only model construction;
- ASR and aligner receiving independent device configuration;
- result and timestamp conversion into application types;
- cleanup on success, exception, and cancellation;
- cleanup before diarization load;
- realtime model reuse and single close;
- checkpoint compatibility and stable `engine="qwen"` semantics;
- explicit, visible startup fallback behavior;
- frozen-import discovery for the MLX adapter.

Mock framework/model execution in routine unit tests. Real model and packaged
checks belong to the release test matrix and must be recorded separately.

Required repository verification after implementation:

```bash
.venv/bin/python -m pytest -q
```

If frontend behavior or labels change, also run:

```bash
.venv/bin/python -m pytest tests/frontend -q
```

The development handoff must report exact commands, results, tested hardware,
backend versions, model variants, and whether measurements were source-tree or
packaged-app runs.

## Rollout plan

### Phase 1: backend seam and PyTorch baseline

Completed in QWEN-01. The implementation keeps `engine="qwen"`, requires
validated local ASR/aligner paths, reports the selected internal backend,
supports `AUDIOASSIST_QWEN_BACKEND=pytorch-mps` as a diagnostic override, and
releases Qwen before diarization and on realtime stop/error paths. Source-tree
forced-offline 30-second CPU and MPS smoke jobs produced identical transcript
and timestamp output. Those runs are compatibility checks, not QWEN-03
performance measurements. Checkpoints pin the selected internal backend for
remaining chunks while legacy checkpoints adopt and then record the current
selection. Realtime/native restart is blocked until any timed-out worker exits
and releases the exact model instance it used. Capture stop/start cleanup is
best-effort across process, stream, FIFO, queue, and device errors, and reports
the first failure only after later lifecycle cleanup has run.

- Introduce the internal protocol/factory without changing public behavior.
- Make local model paths explicit in offline and realtime flows.
- Add deterministic cleanup before diarization.
- Add PyTorch MPS as a development-only override.
- Establish CPU and MPS benchmark baselines.

### Phase 2: MLX full-precision adapter

Completed in QWEN-02 with `mlx-qwen3-asr[aligner]` pinned to 0.3.5 on Apple
Silicon. The adapter consumes the existing application-managed official 1.7B
and Forced Aligner directories directly, maps native result segments into the
existing transcript types, and explicitly clears dependency holders and Metal
cache on close. `AUDIOASSIST_QWEN_BACKEND=mlx` is available for diagnosis and
benchmarking; `auto` deliberately remains on the QWEN-01 PyTorch policy.

The source-tree forced-offline 30-second smoke completed successfully. A
PyInstaller build contains the MLX adapter, distribution metadata, mel filters,
and Korean aligner dictionary without a second physical copy of the MLX binary
tree; strict ad-hoc signature verification passes. Performance, long-audio,
realtime stability, and signed-app comparison remain QWEN-03/REL-01 gates.

- Add and pin one focused MLX Qwen dependency.
- Reuse existing local full-precision weights if validation succeeds.
- Implement ASR and native MLX Forced Aligner conversion.
- Run correctness, latency, memory, long-audio, and realtime tests.
- Complete PyInstaller and signed-DMG verification.

### Phase 3: controlled Mac default

QWEN-03 decision: hold the current automatic backend. The source-tree runner
and measurements are recorded in `docs/qwen-benchmark-results.md`. MLX passed
the measured speed, short-sample boundary, truncation, and corrected realtime
50-cycle lifecycle checks. One 10-minute sample failed the per-file
cross-backend text/timestamp thresholds, MPS was faster on the tested M5 Pro,
and the full human, long-audio, and independently verified signed-app matrix is
incomplete. QWEN-04 owns that remaining evidence; neither accelerated backend
is promoted by this batch.

2026-09-07 follow-up: the old 10-minute disagreement is not a ground-truth
MLX accuracy failure. Correcting the PyTorch precision/masking/budget conditions
and matching short chunks reduced it to 0.166% normalized text distance.
Source adapters now implement shared bounded chunks and generation diagnostics;
MPS uses float32 and PyTorch restores non-FA2 window masks. See
`docs/qwen-backend-investigation.md` for source acceptance versus remaining
human-corpus, lifecycle, and packaged-release gates. `auto` still does not change.

- Select MLX automatically on supported Apple Silicon machines only after the
  acceptance gates pass.
- Retain official PyTorch CPU/CUDA paths.
- Keep a visible diagnostic record of the selected backend in logs and support
  information.
- Update the README and model descriptions; remove the obsolete unconditional
  SIGBUS/CPU claim.

### Phase 4: optional model profiles

- Evaluate 0.6B and 8-bit for realtime latency.
- Treat every additional precision/size as an explicit model profile.
- Measure quality and storage impact before exposing it in the model library.

## Explicit non-goals and cautions

- Do not replace `src/pipeline.py` with a third-party pipeline.
- Do not add a daemon or background cross-process inference service as part of
  this migration.
- Do not change the stable CLI `--engine qwen` contract.
- Do not auto-download or auto-convert a model during transcription.
- Do not run concurrent Qwen Metal inference from multiple threads.
- Do not silently mix backends within one checkpointed job.
- Do not claim Neural Engine use, fixed speed multipliers, or accuracy parity
  without AudioAssist-specific evidence.
- Do not remove PyTorch from packaging; pyannote and current VAD paths still
  require it.

## Final recommendation

Develop MLX as the primary Qwen3-ASR backend for Apple Silicon, while preserving
the official PyTorch implementation for CUDA and CPU compatibility. Keep
PyTorch MPS behind a development override for baseline comparison and incident
diagnosis. Promote MLX to the default only after the exact AudioAssist pipeline,
realtime worker, local model manager, and signed application pass the gates in
this document.
