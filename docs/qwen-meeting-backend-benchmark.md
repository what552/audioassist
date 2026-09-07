# Qwen CPU / MPS / MLX meeting benchmark

Date: 2026-09-07
Status: source runtime and meeting-corpus diagnostic complete; automatic backend unchanged

## Outcome

The corrected CPU, MPS and MLX adapters completed all ten public meeting and
controlled-overlap fixtures (60 minutes of audio per backend). CPU and MPS
produced identical text and word timestamps on every sample. MLX stayed close:
its text differed from CPU by at most 1.78% normalized characters, at least
97.73% of word tokens aligned, and matched-token timestamp median and p95
differences were 0 ms.

MPS was fastest overall on this M5 Pro. Its aggregate RTF was 0.185, compared
with 0.372 for CPU and 0.199 for MLX. MLX had the lowest load latency and peak
process RSS. Recognition quality was effectively tied across backends; the
large errors came from natural multi-speaker and overlapping speech, not from
the Apple acceleration adapter.

The run found and fixed two shared long-form defects before the final pass:

- Dense/repetitive generation could reject an entire recording when one
  at-most-30-second chunk ended at the 1024-token limit or with a non-EOS
  repetition stop. Both adapters now bisect only that failed chunk, down to a
  five-second minimum, and reject the job only if the smaller retry still fails.
- The Forced Aligner can quantize a final boundary up to 30 ms beyond the exact
  decoded WAV boundary. Boundaries within 50 ms are now clamped to the WAV;
  larger or otherwise invalid timestamps are still rejected.

Normal EOS chunks with quiet tails retain their tail-gap diagnostic but are no
longer labelled truncated solely because the last aligned word precedes the
chunk boundary. Generation termination remains authoritative under the pinned
Qwen runtimes.

## Corpus and method

The deterministic local corpus was prepared by the existing 宇生伴月 Phase 2
ASR comparison work:

- one 15-minute AliMeeting Mandarin meeting with four reference speakers;
- one 15-minute AMI English meeting with four reference speakers;
- Mandarin and English controlled-overlap fixtures at 0%, 10%, 30% and 50%,
  each 225 seconds with two reference speakers.

All models and audio were local and the run forced Hugging Face and Transformers
offline mode. Each ASR backend ran once in a separate process with the same
decoder, low-energy at-most-30-second initial split, 1024-token budget and
Forced Aligner. The diarizer ran once per audio file with the known reference
speaker count and its result was shared across the three ASR outputs.

Flat Mandarin scores use CER and flat English scores use WER after ordering
reference turns by start time. Speaker-aware scoring concatenates text per
speaker and chooses the least-error speaker permutation: cpCER for Mandarin and
cpWER for English. DER uses a zero-second collar and includes overlapped speech.
The explicit `UNKNOWN` stream is retained and penalized; it is why a two-speaker
sample can contain three hypothesis text streams.

## Runtime results

Inference excludes load and cleanup. RTF is inference seconds divided by the
full 3,600 seconds of audio; lower is faster.

| Backend | Load | Inference | RTF / realtime multiple | Peak RSS | Recovered generation blocks |
|---|---:|---:|---:|---:|---:|
| PyTorch CPU fp32 | 3.87 s | 1,340.48 s | 0.372 / 2.69× | 13.86 GiB | 3 |
| PyTorch MPS fp32 | 4.53 s | 667.38 s | 0.185 / 5.39× | 8.56 GiB | 3 |
| MLX fp16 | 0.47 s | 714.98 s | 0.199 / 5.04× | 5.84 GiB | 4 |

MPS used 50.2% less inference time than CPU; MLX used 46.7% less. MPS was
6.7% faster than MLX for this corpus. These are one cold process run per
backend, not confidence intervals or general hardware rankings.

## Natural meeting results

CPU and MPS have the same values. MLX is shown separately only where its score
differs. Speaker-aware error is higher than flat error because words assigned to
the wrong or `UNKNOWN` speaker are penalized in addition to recognition errors.

| Fixture | Flat CPU/MPS | Flat MLX | Speaker-aware CPU/MPS | Speaker-aware MLX | DER |
|---|---:|---:|---:|---:|---:|
| AliMeeting Mandarin, 15m | CER 48.54% | 48.68% | cpCER 65.90% | 65.57% | 23.92% |
| AMI English, 15m | WER 44.29% | 43.68% | cpWER 64.37% | 64.12% | 32.66% |

These two meetings are deliberately difficult and are not a representative
accuracy estimate. AliMeeting contains substantial simultaneous speech; flat
chronological concatenation is especially harsh because a single-stream ASR
cannot emit two simultaneous reference streams in a uniquely defined order.

## Controlled-overlap results

The table reports CPU/MPS because their transcripts are identical. MLX differs
by at most 0.58 percentage points in these speaker-aware cells and does not
change the trend.

| Language | Overlap | Flat CER/WER | cpCER/cpWER | DER |
|---|---:|---:|---:|---:|
| Mandarin | 0% | 11.60% | 33.77% | 26.67% |
| Mandarin | 10% | 21.38% | 36.61% | 13.76% |
| Mandarin | 30% | 46.65% | 74.97% | 29.85% |
| Mandarin | 50% | 58.83% | 84.32% | 22.64% |
| English | 0% | 19.41% | 27.86% | 9.06% |
| English | 10% | 26.97% | 32.95% | 14.73% |
| English | 30% | 52.07% | 59.50% | 40.76% |
| English | 50% | 62.21% | 68.97% | 49.53% |

Recognition and speaker-aware error worsen sharply at 30–50% overlap. DER is
not required to be monotonic on these small constructed sets: missed speech,
false alarm and confusion contribute differently, and the Mandarin mixtures
do not contain identical utterance material at every overlap ratio. The English
series shows the expected strong DER degradation.

## Decision and remaining gates

This run removes the old evidence that MPS or MLX fails merely because it is an
accelerated backend. It also makes MPS the performance leader on the tested M5
Pro meeting corpus. It does **not** change the product-wide `auto` default:

- high-overlap accuracy is a model/pipeline limitation shared by all backends;
- packaged-app and signed-upgrade gates remain separate;
- the meeting run is one cold pass; the subsequent corrected-adapter lifecycle
  result is recorded separately.

The personal Settings choice for CPU/MPS/MLX remains appropriate. The bounded
50-cycle MPS/MLX lifecycle gate subsequently passed; see
[the lifecycle result](qwen-lifecycle-results.md). Packaged 2.0.2 acceptance is
the remaining QWEN-04A gate.

## Reproduction and local evidence

Commands:

```bash
.venv/bin/python build/prepare-qwen-meeting-benchmark.py
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
  .venv/bin/python scripts/benchmark_qwen.py run \
  --manifest build/qwen-meeting-benchmark-manifest.json \
  --output build/qwen-meeting-benchmark-report.json \
  --warm-runs 0 --lifecycle-cycles 1 --include-transcripts
MPLCONFIGDIR=/tmp/audioassist-mpl HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
  .venv/bin/python scripts/evaluate_qwen_meetings.py \
  --manifest build/qwen-meeting-benchmark-manifest.json \
  --asr-report build/qwen-meeting-benchmark-report.json \
  --output build/qwen-meeting-speaker-report.json
```

Git-ignored local artifacts:

- `build/qwen-meeting-benchmark-manifest.json`, SHA-256
  `d640238140b6cffd8de58ce4259dcb94b5d7a207aec0226810fda4cfa63933e5`
- `build/qwen-meeting-benchmark-report.json`, SHA-256
  `cc2e45cdd8906f9c8da8c561624e69425f38c3fc0a1e3a7ac35fa34526f79115`
- `build/qwen-meeting-speaker-report.json`, SHA-256
  `b746d4f7baf691ccd15a50256a946545a13dcdec6c7908fc33e6647b7798ae13`

The ASR report intentionally retains public benchmark transcript text to permit
speaker-aware scoring. No private meeting, AudioAssist session, microphone,
system-audio permission, network download or installed application was used.

Automated verification: `MPLCONFIGDIR=/tmp/audioassist-mpl .venv/bin/python
-m pytest -q` completed with **1053 passed**, 952 dependency warnings, in
48.97 seconds on the host environment. The same run inside the restricted
sandbox could not launch Chromium or bind loopback test sockets; its remaining
1011 tests passed before the required host rerun.
