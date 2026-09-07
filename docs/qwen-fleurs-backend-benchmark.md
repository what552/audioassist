# Qwen CPU / MPS / MLX public-corpus benchmark

Date: 2026-09-07  
Status: source-adapter diagnostic complete; not a product-default or packaged-app gate

## Outcome

On the fixed 36-file FLEURS-derived corpus, PyTorch CPU, PyTorch MPS and MLX
produced identical transcript text on every file. MPS and MLX word timestamps
also matched CPU on every aligned token with 0 ms median and p95 boundary
difference. No backend returned a capped, non-EOS, out-of-bounds or incomplete
chunk.

This is stronger evidence that the corrected MPS and MLX adapters do not change
recognition quality on these inputs. It is not enough to make either backend the
product-wide automatic default: only four source utterances per language were
used, and the test does not cover Cantonese, natural meetings, long recordings,
repeated lifecycle load or a packaged application.

## Corpus and method

The source is the existing public FLEURS test subset prepared by the 宇生伴月
ASR comparison work. FLEURS is an N-way parallel speech benchmark covering 102
languages and is licensed CC BY 4.0. The local source revision is
`70bb2e84b976b7e960aa89f1c648e09c59f894dd`.

References:

- [Google FLEURS dataset card](https://huggingface.co/datasets/google/fleurs/blob/main/README.md)
- [FLEURS paper](https://arxiv.org/abs/2205.12446)
- [Qwen3-ASR official evaluation](https://github.com/QwenLM/Qwen3-ASR#evaluation)

The selection is deterministic: the first four sorted Phase 2 base-audio IDs
for Mandarin Chinese, US English and French. Each utterance is tested in three
conditions: original clean PCM, MUSAN noise at 10 dB SNR, and G.711 μ-law 8 kHz
round-trip. There are 36 files totaling 358.32 seconds. All three adapters use
the same AudioAssist decoder, at-most-30-second split policy, serial inference,
1024-token generation limit and forced alignment path. Models are local and the
run is offline.

This is not a reproduction of Qwen's published FLEURS leaderboard. Qwen reports
larger multilingual sets under a different vLLM/bfloat16 evaluation setup. This
run evaluates parity and local Mac behavior with AudioAssist's production
adapters.

## Recognition results

Scores pool edit errors and reference units across the four utterances in each
cell. Mandarin uses CER; English and French use WER. Lower is better.

| Language / metric | Clean | 10 dB noise | G.711 8 kHz |
|---|---:|---:|---:|
| Mandarin CER | 11.11% (14/126) | 12.70% (16/126) | 11.11% (14/126) |
| English WER | 0.00% (0/108) | 0.00% (0/108) | 0.00% (0/108) |
| French WER | 0.84% (1/119) | 0.84% (1/119) | 4.20% (5/119) |

These scores are identical for CPU, MPS and MLX because their text outputs are
identical. The Mandarin subset is too small and intentionally includes hard
proper-name/formatting cases. Manual inspection found both genuine recognition
differences (`基希讷乌` → `新基纳乌`, `跟恐龙` → `和恐龙`) and reference-style
differences (`Del Potro` omitted beside its Chinese transliteration; `6-6`
rendered as `6比6`). Therefore 11.11% is a result for this four-utterance subset,
not an estimate of general Mandarin accuracy.

One Mandarin G.711 file had raw language metadata `English` on CPU and MPS but
`Chinese` on MLX. AudioAssist's existing script-aware canonicalization corrected
all three final product language values to `zh`; every expected-language check
passed. The raw metadata discrepancy should remain visible as a diagnostic.

## Performance results

Inference time excludes model load and cleanup. RTF is inference seconds divided
by audio seconds; lower is faster. Peak RSS is process resident memory, not total
GPU or unified-memory allocation.

| Backend | Load | Inference | RTF / realtime multiple | Peak RSS |
|---|---:|---:|---:|---:|
| PyTorch CPU fp32 | 3.70 s | 134.76 s | 0.376 / 2.66× | 13.49 GiB |
| PyTorch MPS fp32 | 3.89 s | 60.34 s | 0.168 / 5.94× | 5.86 GiB |
| MLX fp16 | 0.33 s | 91.68 s | 0.256 / 3.91× | 5.71 GiB |

On this corpus MPS used 55.2% less inference time than CPU and MLX used 32.0%
less. MPS was also 34.2% faster than MLX by elapsed inference time. MLX retained
the much lower model-load latency. These are one cold process run per backend,
not confidence intervals or universal hardware rankings.

## Evidence and limitations

Local redacted artifacts (ignored by Git):

- `build/fleurs-backend-benchmark-manifest.json`, SHA-256
  `249b66ad1fd9d4209470f4de558ca881a39a5c46bd5e4be15587f71efcf6ca70`
- `build/fleurs-backend-benchmark-report.json`, SHA-256
  `2067dca82ec20848978f07efc9d9ba2ee26346aad55fca0a18b77740d459c506`
- `build/fleurs-zh-clean-diagnostic-report.json`, SHA-256
  `dec88456c2d870fae02229882abe7137e7631c2b5bb96b64e54dd7ba4eba2e28`

The main report redacts transcript content. The four-file CPU diagnostic retains
public FLEURS text solely to classify the Mandarin errors above. No private
meeting audio, microphone, system-audio permission, remote API or model download
was used.

The generic benchmark runner marks all three backends `fail` because its strict
per-file 5% CER threshold was designed as a release alarm and several hard
FLEURS files exceed it identically. That status is an absolute-corpus alert, not
a backend-parity failure. Promotion remains `insufficient-evidence`.

The separate [AliMeeting/AMI and controlled-overlap evaluation](qwen-meeting-backend-benchmark.md)
now covers the meeting cpCER/cpWER and DER item. Remaining meaningful additions
are a larger speaker-balanced FLEURS sample with bootstrap confidence intervals,
Cantonese, 30/60/120-minute jobs, manual listening, and packaged-app plus
50-cycle lifecycle gates. Natural multi-speaker meetings remain separate from
this single-speaker CER/WER table.
