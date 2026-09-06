# Qwen Backend Benchmark Record

Updated: 2026-09-06

## Decision

Do not promote MLX or PyTorch MPS to the automatic Apple Silicon backend in
this release. Keep `auto` on the official PyTorch CPU policy and retain both
accelerated paths as explicit developer overrides.

This is a hold, not a rejection of MLX. MLX passed the measured performance,
short-sample timestamp, and realtime lifecycle checks, but one 10-minute sample
failed the cross-backend text/timestamp thresholds and the promotion-level
corpus and signed-app evidence are incomplete. The remaining evidence is
tracked as QWEN-04 rather than silently disappearing.

## Test environment

- Hardware: Apple M5 Pro, 64 GiB unified memory
- OS: macOS 26.5.1 (25F80), arm64
- Python: 3.12.2, repository `.venv`
- Qwen model: application-managed official Qwen3-ASR 1.7B
- Forced Aligner: application-managed official Qwen3 Forced Aligner 0.6B
- PyTorch: 2.10.0; `qwen-asr`: 0.0.6
- MLX: 0.32.2; `mlx-qwen3-asr`: 0.3.5
- Execution: source tree, with `HF_HUB_OFFLINE=1` and
  `TRANSFORMERS_OFFLINE=1`

Reports are written outside the repository and redact transcript text by
default. They retain transcript hashes, lengths, metrics, timing, memory, and
timestamp boundaries. Existing transcripts in this run are pseudo references,
not human ground truth; their CER/WER cannot authorize a production promotion.

## Results

One five-minute Mandarin/code-switching room clip was run cold and warm on all
three backends. The corrected token-aligned comparison produced:

| Backend | Load | Cold RTF | Warm RTF | Peak RSS | RSS after cleanup |
|---|---:|---:|---:|---:|---:|
| PyTorch CPU | 4.10 s | 0.553 | 0.484 | 20.84 GiB | 3.71 GiB |
| PyTorch MPS | 6.42 s | 0.200 | 0.184 | 5.31 GiB | 2.57 GiB |
| MLX | 0.56 s | 0.261 | 0.265 | 5.88 GiB | 0.55 GiB |

CPU and MPS produced the same transcript hash. MLX differed from CPU by 18.4%
normalized character distance on this sample, while scoring better against the
existing pseudo reference; neither observation is an accuracy conclusion. Of
the word tokens, 87.1% aligned between MLX and CPU. On those matched tokens,
the median absolute boundary difference was 34 ms and p95 was 80 ms, within the
50 ms / 150 ms engineering gate.

A separate one-pass run covered three clips from different existing sessions:
5 minutes, 5 minutes, and 10 minutes (20 minutes total per backend).

| Sample | CPU RTF | MPS RTF | MLX RTF | MLX vs CPU speed | Truncated |
|---|---:|---:|---:|---:|---|
| Mandarin/code-switching, 5m | 0.550 | 0.200 | 0.257 | 53% faster | No |
| English/code-switch candidate, 5m | 0.628 | 0.193 | 0.262 | 58% faster | No |
| Mandarin room recording, 10m | 0.393 | 0.200 | 0.258 | 34% faster | No |

The candidate English clip was detected as Chinese by CPU and MPS and is not
counted as a clean English gate fixture. On the 10-minute clip, upstream MLX
returned an empty aggregate language even though later chunks reported
Chinese. The AudioAssist adapter now asks for chunk metadata and, only when the
aggregate language is empty, uses the most common non-empty chunk language. A
real 10-minute rerun returned Chinese with an unchanged transcript hash and
unchanged 3,002 timestamp items.

That language fallback does not erase the other 10-minute differences: MLX
matched only 70.7% of CPU tokens for timestamp comparison, boundary p95 was
425 ms, and normalized text distance from CPU was 34.8%. The automated gate
now reports each of those per-file failures instead of dropping a low-alignment
row or allowing a stronger sample to mask it.

The corrected MLX realtime lifecycle run disabled the Forced Aligner and word
timestamps, then completed 50 model load/close cycles. Each cycle ran one cold
and two warm five-second utterances, giving 100 measured warm utterances:

- 150/150 total transcript hashes identical; zero explicit truncations
- warm utterance latency p95: 2.67 seconds, below the 5-second input duration
- cleanup median: 20 ms
- maximum peak RSS: 4.73 GiB on a 64 GiB machine
- all 50 post-cleanup RSS samples captured; range 128-364 MiB
- first-to-last RSS after cleanup growth: 237 MiB, below the 256 MiB alert line

## Reproducible runner

The committed runner validates an explicit manifest, runs every backend in an
isolated child process, forces offline mode, records cold/warm and lifecycle
measurements, compares matching token boundaries, and emits a machine-readable
promotion assessment. Audio duration is measured from the file and checked
against declared duration classes. Realtime and offline/aligned samples cannot
be mixed in one run. Cold realtime passes are excluded from the 100-utterance
latency gate, and lifecycle evidence comes from the completed cycles and full
RSS sample sequence rather than a requested count. It never treats missing
evidence as a pass or self-labels a source-tree run as signed-app evidence.

```bash
.venv/bin/python -m src.qwen_benchmark run \
  --manifest /tmp/audioassist-qwen03-corpus/manifest-smoke.json \
  --output /tmp/audioassist-qwen03-corpus/report-smoke-v2.json \
  --backends pytorch-cpu,pytorch-mps,mlx \
  --warm-runs 1 --lifecycle-cycles 1 --timeout 1800

.venv/bin/python -m src.qwen_benchmark run \
  --manifest /tmp/audioassist-qwen03-corpus/manifest.json \
  --output /tmp/audioassist-qwen03-corpus/report-three-samples.json \
  --backends pytorch-cpu,pytorch-mps,mlx \
  --warm-runs 0 --lifecycle-cycles 1 --timeout 2400

.venv/bin/python -m src.qwen_benchmark run \
  --manifest /tmp/audioassist-qwen03-corpus/manifest-realtime.json \
  --output /tmp/audioassist-qwen03-corpus/report-mlx-realtime-stress-v2.json \
  --backends mlx --warm-runs 2 --lifecycle-cycles 50 --timeout 1800
```

## Evidence still required before promotion

- Human-reviewed Mandarin, Cantonese, English, and code-switch references
- Deliberately clean and noisy fixtures, plus Japanese/Korean aligner coverage
- 30-, 60-, and 120-minute meeting runs
- Full capture pause/resume checks and listening review of large text/boundary
  differences
- The same measurements from a signed/notarized app on a clean machine

Until those items pass, the machine-readable recommendation remains
`keep-current-auto`.
