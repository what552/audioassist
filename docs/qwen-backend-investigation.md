# Qwen MPS / MLX failure investigation

Date: 2026-09-07. Source-tree diagnostics only; no installed application,
model weights, dependency installation, session, or automatic backend changed.

Implementation follow-up: the diagnostic workarounds below are now implemented
in the source adapters on the QWEN-04A branch. Shared decoding/chunking, serial
1024-token inference, MPS float32 ASR/alignment, removable window-mask hooks and
generation observers, explicit cap/non-EOS/timestamp failures, and redacted
per-chunk benchmark diagnostics are covered by tests. Short-suite real inference
passes on CPU/MPS/MLX with equal normalized text and timestamps. Full regression
on the host: **996 passed** (53 added tests); the execution sandbox cannot run
browser/local-socket tests, so the host run is the acceptance result. Full
10-minute adapter verification and independent commit review are pending below.
No `.app`/DMG has been built and version 2.0.1 is unchanged.

## Scope and environment

Revisit the two blockers in QWEN-04A: severe MPS truncation on the 103.941125 s
synthetic Mandarin/English sample, and the MLX-versus-CPU discrepancy on the
600 s room recording. All inference uses the existing local snapshots with
`HF_HUB_OFFLINE=1` and `TRANSFORMERS_OFFLINE=1`.

The checked software versions, hardware, and local model sources match the
original test: Python 3.12.2,
PyTorch 2.10.0, qwen-asr 0.0.6, transformers 4.57.6, MLX 0.32.2,
mlx-qwen3-asr 0.3.5, on the Apple M5 Pro / 64 GiB machine.
This does not assert identical background load or memory pressure between runs.

## MPS: confirmed precision-path failure

The original failure reproduces with the same input and weights. Generated
token IDs confirm **early EOS**, not exhaustion of the 512-token budget.

| Isolated change | Result on 103.94 s sample |
|---|---|
| None: MPS float16 | 2–6 generated tokens, 8–13 output characters, ~98.9% source-script CER |
| Add missing audio window mask only | Still 3 tokens / early EOS |
| Use eager attention only | Still 3 tokens / early EOS; encoder outputs finite |
| All MPS float32 | Two passes: 340 tokens, 912 characters, 0.654% CER; timestamp tail 103.92 s |
| Audio encoders float32, text decoders float16 | 340 tokens, 912 characters, 0.654% CER; timestamp tail 103.84 s |
| Float32 plus audio window mask | 337 tokens, 917 characters, 0.262% CER; timestamp tail 103.92 s |
| Reshape encoder conv_out linear projection to 2D only | Still early EOS |
| Make convolution inputs contiguous only | Still early EOS |
| Reduce convolution chunk batch to 16 only | Still early EOS |

The fp32 passes took 27.49 s cold and 23.44 s warm; the mixed-precision
encoder-only experiment took 19.23 s. These are diagnostic observations, not a
complete promotion performance matrix.

This localizes a sufficient workaround to the MPS audio-encoder precision path.
It does **not** yet prove a particular Metal kernel defect or a universal fix
for every Apple Silicon generation. Do not attribute it solely to SDPA: eager
also failed. Do not attribute it to model corruption: the same local weights
complete the same sample under float32.

## Baseline defects and MLX comparison

Installed qwen-asr 0.0.6 defines
`Qwen3ASRAudioEncoder._prepare_attention_mask()` but does not call it or pass
its result to its encoder layers. CPU/MPS SDPA and eager do not interpret the
Flash-Attention `cu_seqlens` metadata. This is the existing upstream
[Qwen3-ASR PR #103](https://github.com/QwenLM/Qwen3-ASR/pull/103), not an
AudioAssist-only hypothesis. The Forced Aligner reuses the same model class.

The official aligned path also uses approximately 180 s audio chunks with
`max_new_tokens=512`; MLX 0.3.5 uses at-most-30 s chunks with a duration-based
budget per chunk. The previous comparison did not control these conditions.
MLX already implements windowed audio attention.

Controlled extraction of the first 180 s proves the budget problem: with the
audio-encoder fp32 workaround and batch size 1, the 512-token run reaches its
cap without EOS (829 characters). Raising only the cap to 2048 yields a normal
EOS at 538 tokens (868 characters). This does not prove completeness: the
unmasked alignment still ends around 120 s. Adding the mask at the larger cap
produces 553 tokens / 889 characters, but that mixed-precision long-chunk
alignment even extends to 195.28 s, beyond the 180 s input. Therefore increasing
the budget alone is insufficient, and long-chunk MPS alignment is not accepted.

The full-precision 10-minute default-batch MPS experiment was interrupted after
several minutes with substantial system memory compression. It has no completed
quality result. A float32 recommendation must also bound chunk size and batch
size; blindly changing dtype while retaining the default batch of 32 is not an
accepted fix.

A fresh MLX 10-minute run reproduces the old transcript hash exactly:
`3876ed6ee7e6500278a970bb78617399f956bb27b1a60af479659c869ac52579`.
All 31 chunks end normally (EOS), with zero capped/truncated chunks and a maximum
of 114 generated tokens in any chunk. It returns 3339 characters and 3002 word
timestamps. The raw aggregate language is still empty, which the existing
AudioAssist chunk-language fallback already corrects.
EOS excludes reaching the token cap; it does not itself exclude omissions or
premature termination.

## Completed matched-chunk comparison

The same 600 s waveform was split into MLX's exact 31 low-energy chunks (each
at most 30 s), saved losslessly as float32 WAV, and passed sequentially to CPU
and MPS. Both PyTorch runs used float32 for ASR **and** alignment, the window
mask on both encoders, batch size 1, and a sufficient 1024-token budget per chunk.
MLX kept its original duration-based budget, which no chunk reached.

| Measure on the 10-minute sample | Original comparison | Corrected joint configuration |
|---|---:|---:|
| MLX normalized character distance from CPU | 34.769% | **0.1658%** |
| MLX/CPU timestamp token match ratio | 70.653% | **99.833%** (2997 matched) |
| Matched boundary median difference | 36.6 ms | **0 ms** |
| Matched boundary p95 difference | 424.7 ms | **0 ms** |
| CPU versus MPS output | Near agreement in original long test | **Exact text and word-timestamp equality** |

30 of 31 chunks have identical normalized CPU/MLX text (including the initial
empty chunk). One remaining chunk differs. CPU and MPS produce identical raw
text and timestamps on all 31 chunks. Neither reaches the token cap, and every
PyTorch word satisfies `0 <= start <= end <= chunk_duration` without clipping
timestamps. Raw merged PyTorch text hash:
`e1589e11a2b2238d015d489c45b09340d1aa2d620194bdf694aab3789e4c4fbf`.

This removes the previous 10-minute cross-backend disagreement as evidence
that MLX is substantially worse. It does **not** establish human accuracy or
isolate the independent contributions of chunking, masking, and the generation
budget. The clip still lacks a human-verified transcript. The synthetic
source-script tests also do not replace a diverse human-recording corpus.

CPU and MPS matched-chunk runs overlapped in time, so their elapsed durations
are diagnostic logs, not a controlled speed ranking or a promotion benchmark.
No two GPU inference runs overlapped.

## Implementation plan from diagnosis (source implementation now under validation)

1. Add bounded short-chunk processing and serial batch configuration, including
   no-loss/no-overlap reconstruction and word-offset regression tests.
2. Make token budgets, termination reasons, and per-chunk coverage visible to
   the benchmark. Do not use a final timestamp as proof of whole-file coverage.
3. Apply the non-FA2 window-mask fix as an instance-scoped compatibility shim
   for both ASR and Forced Aligner, preserving existing masks and cleanup.
4. Start MPS correctness validation with float32 ASR/alignment and bounded
   chunks; evaluate a lower-memory mixed-precision option separately. Do not
   hide invalid timestamps by clamping them to the input duration.
5. Re-run the full fixture corpus, memory/lifecycle/offline tests and packaged
   application gates before considering QWEN-04B. Keep `auto` unchanged for now.

At the end of the diagnostic-only stage, only documentation was changed. The installed
2.0.1 application and dependency environment are unchanged. Metric helper
verification: `.venv/bin/python -m pytest tests/test_qwen_benchmark.py -q` —
**27 passed**. No claim of a new application regression suite or release is made.

**34.8% distance from the old CPU output is not a measured 34.8% MLX error
rate.** The 10-minute clip has a prior machine transcript, not a human-verified
reference. Different text also changes which timestamp tokens can be matched.
The old cross-backend failure should remain a reason to investigate, not proof
that CPU is correct or that MLX transcription quality is worse.

## Diagnostic artifacts

Private transcripts stay in `/private/tmp`; none are checked into this report.
The experiment runner `/private/tmp/qwen-investigate.py` loads both local
models, records token lengths/EOS, and applies instance-scoped experimental
hooks only. No `site-packages` files are patched.

Representative commands (repository `.venv`, outside the execution sandbox so
Metal is available):

```sh
.venv/bin/python /private/tmp/qwen-investigate.py --out /private/tmp/qwen-mps-baseline.json --repeat 2
.venv/bin/python /private/tmp/qwen-investigate.py --out /private/tmp/qwen-mps-float32.json --dtype float32 --repeat 2
.venv/bin/python /private/tmp/qwen-investigate.py --out /private/tmp/qwen-mps-encoder32.json --encoder-float32
.venv/bin/python /private/tmp/qwen-investigate.py --out /private/tmp/qwen-mps32-mask.json --dtype float32 --mask --check-finite
.venv/bin/python /private/tmp/qwen-investigate-mlx.py --out /private/tmp/qwen-mlx-10m.json --chunk-manifest /private/tmp/qwen-matched-chunks/manifest.json
.venv/bin/python /private/tmp/qwen-investigate.py --out /private/tmp/qwen-cpu-matched-10m.json --device cpu --dtype float32 --mask --tokens 1024 --batch 1 --manifest /private/tmp/qwen-matched-chunks/manifest.json --all-samples
.venv/bin/python /private/tmp/qwen-investigate.py --out /private/tmp/qwen-mps32-matched-10m.json --device mps --dtype float32 --mask --tokens 1024 --batch 1 --manifest /private/tmp/qwen-matched-chunks/manifest.json --all-samples
```

The sandbox itself cannot initialize MPS and reports an OS-support error. That
diagnostic was rerun with host GPU access; it is separate from the actual
early-EOS failure reproduced above.
