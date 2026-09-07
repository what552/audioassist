# Qwen MPS / MLX lifecycle gate

Date: 2026-09-07  
Status: corrected source adapters pass the bounded 50-cycle operational gate

## Scope

The lifecycle runner used two existing human-reference FLEURS clips: 4.74 seconds
of Mandarin and 8.76 seconds of English. Each backend ran in its own offline
process. Every one of 50 cycles created and loaded a fresh Qwen3-ASR 1.7B engine,
transcribed both clips once cold and once warm, then closed the engine and sampled
process RSS. This provides 100 serialized warm utterances per backend without
repeating the already completed FLEURS or meeting-quality benchmarks.

## Results

| Backend | Cycles | Warm utterances | Failures / truncations / language errors | Peak RSS | RSS after cleanup, first → last | Last 10-cycle range |
|---|---:|---:|---:|---:|---:|---:|
| PyTorch MPS fp32 | 50 | 100 | 0 / 0 / 0 | 6,988 MiB | 1,054 → 2,962 MiB | 41 MiB |
| MLX fp16 | 50 | 100 | 0 / 0 / 0 | 4,837 MiB | 129 → 354 MiB | 35 MiB |

MLX remained below the existing 256 MiB first-to-last cleanup-growth gate at
225 MiB. MPS retained a substantially larger PyTorch/Metal cache, with most of
the increase occurring during the first three cycles. Its cleanup RSS then
approached a platform: growth was 177 MiB over cycles 11–50 and 41 MiB over
cycles 41–50. The run therefore does not show linear 1.9 GiB-per-50-cycle leakage,
but the higher steady-state footprint remains a trade-off when choosing the Mac
automatic backend.

## CLI smoke

The new per-job CLI option also completed the full shared pipeline for both
backends, including local ASR, alignment, one-speaker diarization, session commit,
source-audio copy and JSON/Markdown output. The machine-readable responses reported
`qwen_backend` as `pytorch-mps` and `mlx` respectively. Both jobs ran with Hugging
Face and Transformers offline mode enabled; the desktop UI and capture permissions
were not involved.

## Reproduction

```bash
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 .venv/bin/python \
  scripts/benchmark_qwen.py run \
  --manifest build/qwen-realtime-lifecycle-manifest.json \
  --output build/qwen-realtime-lifecycle-mps.json \
  --backends pytorch-mps --warm-runs 1 --lifecycle-cycles 50 --timeout 1800

HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 .venv/bin/python \
  scripts/benchmark_qwen.py run \
  --manifest build/qwen-realtime-lifecycle-manifest.json \
  --output build/qwen-realtime-lifecycle-mlx.json \
  --backends mlx --warm-runs 1 --lifecycle-cycles 50 --timeout 1800
```

The redacted JSON reports are retained outside the repository under
`Documents/AudioAssist Benchmark Reports/QWEN-04A-20260907/`.

- manifest SHA-256: `95d8b8a4e96a9e19890247a9ef3274fa0504a400e8c498e5205a0a294e55c45d`
- MPS report SHA-256: `361587567c78138018665b6d6ed0116480f3e08fbef804334b73436021c01db5`
- MLX report SHA-256: `0a06544f8868d41b3b3b7d8d89e42d0ee83c50ad33851ffed4acb017ea62927c`

## Decision

The corrected source adapters pass the requested lifecycle stability scope.
Existing FLEURS, exact-script code-switching and public meeting evidence remains
the quality basis; Cantonese and a duplicate manual-listening batch are not product
requirements. Packaged 2.0.2 offline/file/realtime acceptance remains before any
change to the product-wide `auto` backend.
