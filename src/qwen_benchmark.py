"""Reproducible, offline Qwen backend benchmark runner.

Each backend runs in its own process so model state, peak RSS, and Metal/Torch
caches do not leak into another backend's measurements. The saved report is
redacted by default: it contains transcript hashes and metrics, not meeting
content.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
from difflib import SequenceMatcher
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import resource
import statistics
import subprocess
import sys
import time
import unicodedata
import wave
from typing import Any, Sequence

from .asr_factory import create_qwen_engine
from .model_manager import ModelManager


SCHEMA_VERSION = 1
BACKENDS = ("pytorch-cpu", "pytorch-mps", "mlx")
_DURATION_TAG_RANGES = {
    "realtime": (1.0, 10.0),
    "five-minute": (240.0, 360.0),
    "meeting-30m": (25 * 60.0, 35 * 60.0),
    "meeting-60m": (55 * 60.0, 65 * 60.0),
    "meeting-120m": (110 * 60.0, 130 * 60.0),
}


@dataclass(frozen=True)
class BenchmarkSample:
    id: str
    path: str
    tags: tuple[str, ...]
    expected_language: str | None = None
    reference_text: str | None = None
    reference_kind: str | None = None
    duration_seconds: float | None = None


def _read_reference(item: dict[str, Any], manifest_dir: Path) -> str | None:
    inline = item.get("reference_text")
    reference_path = item.get("reference_path")
    transcript_path = item.get("reference_transcript_path")
    supplied = sum(value is not None for value in (inline, reference_path, transcript_path))
    if supplied > 1:
        raise ValueError(
            "a sample can contain only one reference_text, reference_path, "
            "or reference_transcript_path"
        )
    if transcript_path is not None:
        path = Path(transcript_path)
        if not path.is_absolute():
            path = manifest_dir / path
        transcript = json.loads(path.read_text(encoding="utf-8"))
        end_seconds = float(item.get("reference_end_seconds", float("inf")))
        return "".join(
            str(segment.get("text", ""))
            for segment in transcript.get("segments", [])
            if float(segment.get("start", 0.0)) < end_seconds
        )
    if reference_path is not None:
        path = Path(reference_path)
        if not path.is_absolute():
            path = manifest_dir / path
        return path.read_text(encoding="utf-8")
    return str(inline) if inline is not None else None


def load_manifest(path: str) -> list[BenchmarkSample]:
    manifest_path = Path(path).resolve()
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    items = payload.get("samples") if isinstance(payload, dict) else None
    if not isinstance(items, list) or not items:
        raise ValueError("benchmark manifest must contain a non-empty samples list")

    samples: list[BenchmarkSample] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("each benchmark sample must be an object")
        sample_id = str(item.get("id", "")).strip()
        if not sample_id or sample_id in seen:
            raise ValueError(f"sample id must be non-empty and unique: {sample_id!r}")
        seen.add(sample_id)

        audio_path = Path(str(item.get("path", "")))
        if not audio_path.is_absolute():
            audio_path = manifest_path.parent / audio_path
        audio_path = audio_path.resolve()
        if not audio_path.is_file():
            raise ValueError(f"audio file does not exist for {sample_id!r}: {audio_path}")

        reference = _read_reference(item, manifest_path.parent)
        reference_kind = item.get("reference_kind")
        if reference_kind not in (None, "human", "pseudo"):
            raise ValueError("reference_kind must be 'human' or 'pseudo'")
        if reference is not None and reference_kind is None:
            raise ValueError("reference_kind is required when a reference is supplied")

        duration = item.get("duration_seconds")
        if duration is not None and float(duration) <= 0:
            raise ValueError("duration_seconds must be positive")
        samples.append(
            BenchmarkSample(
                id=sample_id,
                path=str(audio_path),
                tags=tuple(str(tag) for tag in item.get("tags", [])),
                expected_language=(
                    str(item["expected_language"])
                    if item.get("expected_language") is not None
                    else None
                ),
                reference_text=reference,
                reference_kind=reference_kind,
                duration_seconds=float(duration) if duration is not None else None,
            )
        )
    return samples


def _audio_duration(path: str) -> float:
    if Path(path).suffix.lower() == ".wav":
        with wave.open(path, "rb") as audio:
            return audio.getnframes() / audio.getframerate()
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
        check=True,
        capture_output=True,
        text=True,
    )
    return float(result.stdout.strip())


def _validated_duration(sample: BenchmarkSample) -> float:
    actual = _audio_duration(sample.path)
    if not math.isfinite(actual) or actual <= 0:
        raise ValueError(f"audio duration must be positive and finite: {sample.id!r}")
    if sample.duration_seconds is not None:
        tolerance = max(0.25, actual * 0.001)
        if abs(sample.duration_seconds - actual) > tolerance:
            raise ValueError(
                f"declared duration for {sample.id!r} differs from audio "
                f"({sample.duration_seconds:.3f}s versus {actual:.3f}s)"
            )
    duration_tags = [tag for tag in sample.tags if tag in _DURATION_TAG_RANGES]
    if len(duration_tags) > 1:
        raise ValueError(f"sample {sample.id!r} has conflicting duration tags")
    if duration_tags:
        tag = duration_tags[0]
        lower, upper = _DURATION_TAG_RANGES[tag]
        if not lower <= actual <= upper:
            raise ValueError(
                f"audio duration for {sample.id!r} does not match tag {tag!r}"
            )
    return actual


def _normalize_characters(text: str) -> list[str]:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    return [
        character
        for character in normalized
        if not character.isspace()
        and unicodedata.category(character)[0] not in {"P", "S"}
    ]


def _normalize_words(text: str) -> list[str]:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    cleaned = "".join(
        " " if unicodedata.category(character)[0] in {"P", "S"} else character
        for character in normalized
    )
    return cleaned.split()


def edit_distance(left: Sequence[str], right: Sequence[str]) -> int:
    if len(left) < len(right):
        left, right = right, left
    previous = list(range(len(right) + 1))
    for left_index, left_item in enumerate(left, 1):
        current = [left_index]
        for right_index, right_item in enumerate(right, 1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[right_index] + 1,
                    previous[right_index - 1] + (left_item != right_item),
                )
            )
        previous = current
    return previous[-1]


def error_rates(hypothesis: str, reference: str) -> dict[str, float | None]:
    reference_characters = _normalize_characters(reference)
    hypothesis_characters = _normalize_characters(hypothesis)
    cer = (
        edit_distance(hypothesis_characters, reference_characters)
        / len(reference_characters)
        if reference_characters
        else None
    )
    reference_words = _normalize_words(reference)
    hypothesis_words = _normalize_words(hypothesis)
    wer = (
        edit_distance(hypothesis_words, reference_words) / len(reference_words)
        if reference_words
        else None
    )
    return {"cer": cer, "wer": wer}


def _percentile(values: Sequence[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _rss_mb() -> float | None:
    try:
        output = subprocess.run(
            ["ps", "-o", "rss=", "-p", str(os.getpid())],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        return int(output) / 1024
    except Exception:
        return None


def _peak_rss_mb() -> float:
    value = float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    return value / (1024 * 1024) if sys.platform == "darwin" else value / 1024


def _physical_memory_mb() -> float | None:
    try:
        return (
            float(os.sysconf("SC_PHYS_PAGES"))
            * float(os.sysconf("SC_PAGE_SIZE"))
            / (1024 * 1024)
        )
    except (AttributeError, OSError, ValueError):
        return None


def _result_payload(
    result: Any,
    duration: float,
    elapsed: float,
    *,
    truncated: bool,
) -> dict[str, Any]:
    words = [
        {"text": word.word, "start": float(word.start), "end": float(word.end)}
        for word in result.words
    ]
    final_boundary = max((word["end"] for word in words), default=0.0)
    return {
        "seconds": elapsed,
        "rtf": elapsed / duration if duration else None,
        "text": result.text,
        "language": result.language,
        "words": words,
        "possible_truncation": truncated,
        "timestamp_tail_gap_seconds": max(0.0, duration - final_boundary) if words else None,
    }


def run_worker(
    *,
    backend: str,
    manifest_path: str,
    model_path: str,
    aligner_path: str,
    warm_runs: int,
    lifecycle_cycles: int,
) -> dict[str, Any]:
    samples = load_manifest(manifest_path)
    realtime_flags = ["realtime" in sample.tags for sample in samples]
    if any(realtime_flags) and not all(realtime_flags):
        raise ValueError("a benchmark manifest cannot mix realtime and offline samples")
    realtime_mode = all(realtime_flags)
    cycles: list[dict[str, Any]] = []
    for cycle_index in range(lifecycle_cycles):
        engine = create_qwen_engine(
            model_path=model_path,
            aligner_path=None if realtime_mode else aligner_path,
            with_timestamps=not realtime_mode,
            backend_override=backend,
        )
        rss_before = _rss_mb()
        sample_results: list[dict[str, Any]] = []
        load_seconds = 0.0
        try:
            started = time.perf_counter()
            engine.load()
            load_seconds = time.perf_counter() - started
            for sample in samples:
                duration = _validated_duration(sample)
                passes: list[dict[str, Any]] = []
                for _ in range(warm_runs + 1):
                    started = time.perf_counter()
                    result = engine.transcribe(sample.path)
                    elapsed = time.perf_counter() - started
                    pass_result = _result_payload(
                        result,
                        duration,
                        elapsed,
                        truncated=bool(getattr(engine, "last_truncated", False)),
                    )
                    pass_result["expected_language_agrees"] = (
                        result.language == sample.expected_language
                        if sample.expected_language is not None
                        else None
                    )
                    passes.append(pass_result)
                first = passes[0]
                metrics = (
                    error_rates(first["text"], sample.reference_text)
                    if sample.reference_text is not None
                    else {"cer": None, "wer": None}
                )
                sample_results.append(
                    {
                        "id": sample.id,
                        "tags": list(sample.tags),
                        "duration_seconds": duration,
                        "expected_language": sample.expected_language,
                        "reference_kind": sample.reference_kind,
                        "metrics": metrics,
                        "passes": passes,
                    }
                )
        finally:
            started = time.perf_counter()
            engine.close()
            cleanup_seconds = time.perf_counter() - started
        cycles.append(
            {
                "cycle": cycle_index + 1,
                "load_seconds": load_seconds,
                "cleanup_seconds": cleanup_seconds,
                "rss_before_mb": rss_before,
                "rss_after_cleanup_mb": _rss_mb(),
                "peak_rss_mb": _peak_rss_mb(),
                "samples": sample_results,
            }
        )
    rss_after_values = [
        cycle["rss_after_cleanup_mb"]
        for cycle in cycles
        if cycle["rss_after_cleanup_mb"] is not None
    ]
    return {
        "backend": backend,
        "mode": "realtime-asr" if realtime_mode else "offline-aligned-asr",
        "platform": platform.platform(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "cycles": cycles,
        "lifecycle_summary": {
            "completed_cycles": len(cycles),
            "cleanup_seconds_median": statistics.median(
                cycle["cleanup_seconds"] for cycle in cycles
            ),
            "peak_rss_mb_max": max(cycle["peak_rss_mb"] for cycle in cycles),
            "physical_memory_mb": _physical_memory_mb(),
            "rss_after_cleanup_samples": len(rss_after_values),
            "rss_after_cleanup_min_mb": min(rss_after_values) if rss_after_values else None,
            "rss_after_cleanup_max_mb": max(rss_after_values) if rss_after_values else None,
            "rss_after_cleanup_growth_mb": (
                rss_after_values[-1] - rss_after_values[0]
                if len(rss_after_values) >= 2
                else None
            ),
        },
    }


def _word_key(word: dict[str, Any]) -> str:
    return "".join(_normalize_characters(str(word.get("text", ""))))


def _boundary_differences(
    candidate: dict[str, Any], baseline: dict[str, Any]
) -> tuple[list[float], int, float]:
    candidate_words = candidate.get("words", [])
    baseline_words = baseline.get("words", [])
    differences: list[float] = []
    matched = 0
    matcher = SequenceMatcher(
        None,
        [_word_key(word) for word in baseline_words],
        [_word_key(word) for word in candidate_words],
        autojunk=False,
    )
    for baseline_start, candidate_start, size in matcher.get_matching_blocks():
        for offset in range(size):
            baseline_word = baseline_words[baseline_start + offset]
            candidate_word = candidate_words[candidate_start + offset]
            differences.append(abs(candidate_word["start"] - baseline_word["start"]))
            differences.append(abs(candidate_word["end"] - baseline_word["end"]))
            matched += 1
    denominator = max(len(candidate_words), len(baseline_words), 1)
    return differences, matched, matched / denominator


def compare_runs(runs: list[dict[str, Any]]) -> dict[str, Any]:
    by_backend = {run["backend"]: run for run in runs if run.get("ok")}
    baseline = by_backend.get("pytorch-cpu")
    comparisons: dict[str, Any] = {}
    if not baseline:
        return comparisons
    baseline_samples = {
        sample["id"]: sample
        for sample in baseline["result"]["cycles"][0]["samples"]
    }
    for backend, run in by_backend.items():
        if backend == "pytorch-cpu":
            continue
        sample_comparisons: list[dict[str, Any]] = []
        for sample in run["result"]["cycles"][0]["samples"]:
            base_sample = baseline_samples[sample["id"]]
            candidate_pass = sample["passes"][0]
            baseline_pass = base_sample["passes"][0]
            boundaries, matched_words, alignment_ratio = _boundary_differences(
                candidate_pass, baseline_pass
            )
            cross_error = error_rates(candidate_pass["text"], baseline_pass["text"])
            sample_comparisons.append(
                {
                    "id": sample["id"],
                    "language_agrees": candidate_pass["language"] == baseline_pass["language"],
                    "text_vs_cpu_cer": cross_error["cer"],
                    "timestamp_matched_words": matched_words,
                    "timestamp_alignment_ratio": alignment_ratio,
                    "timestamp_median_abs_seconds": (
                        statistics.median(boundaries) if boundaries else None
                    ),
                    "timestamp_p95_abs_seconds": _percentile(boundaries, 0.95),
                }
            )
        comparisons[backend] = sample_comparisons
    return comparisons


def assess_mlx_gate(report: dict[str, Any]) -> dict[str, Any]:
    """Apply automated gates; final promotion always requires independent review."""
    missing: list[str] = []
    failures: list[str] = []
    successful = {
        run["backend"]: run["result"]
        for run in report["runs"]
        if run.get("ok")
    }
    for backend in BACKENDS:
        if backend not in successful:
            missing.append(f"successful {backend} run")
    missing.append("independently verified signed-app release checklist")
    missing.append("manual listening review of severe per-file differences")

    samples_by_backend: dict[str, dict[str, dict[str, Any]]] = {}
    all_tags: set[str] = set()
    for backend, result in successful.items():
        samples = result["cycles"][0]["samples"] if result.get("cycles") else []
        samples_by_backend[backend] = {sample["id"]: sample for sample in samples}
        for sample in samples:
            all_tags.update(sample.get("tags", []))

    required_tags = {
        "mandarin",
        "cantonese",
        "english",
        "code-switching",
        "noisy",
        "clean",
        "realtime",
        "five-minute",
        "meeting-30m",
        "meeting-60m",
        "meeting-120m",
    }
    for tag in sorted(required_tags - all_tags):
        missing.append(f"corpus tag: {tag}")

    cpu_samples = samples_by_backend.get("pytorch-cpu", {})
    mlx_samples = samples_by_backend.get("mlx", {})
    common = sorted(set(cpu_samples) & set(mlx_samples))
    human = [
        sample_id
        for sample_id in common
        if cpu_samples[sample_id].get("reference_kind") == "human"
        and cpu_samples[sample_id].get("metrics", {}).get("cer") is not None
        and mlx_samples[sample_id].get("metrics", {}).get("cer") is not None
    ]
    if not human:
        missing.append("human reference transcripts for CER/WER")
    else:
        cpu_cer = statistics.mean(cpu_samples[item]["metrics"]["cer"] for item in human)
        mlx_cer = statistics.mean(mlx_samples[item]["metrics"]["cer"] for item in human)
        if mlx_cer - cpu_cer > 0.005:
            failures.append("MLX aggregate CER regresses by more than 0.5 percentage points")
        human_with_wer = [
            item
            for item in human
            if cpu_samples[item]["metrics"].get("wer") is not None
            and mlx_samples[item]["metrics"].get("wer") is not None
        ]
        if human_with_wer:
            cpu_wer = statistics.mean(
                cpu_samples[item]["metrics"]["wer"] for item in human_with_wer
            )
            mlx_wer = statistics.mean(
                mlx_samples[item]["metrics"]["wer"] for item in human_with_wer
            )
            if mlx_wer - cpu_wer > 0.005:
                failures.append(
                    "MLX aggregate WER regresses by more than 0.5 percentage points"
                )
        for item in human:
            if (
                mlx_samples[item]["metrics"]["cer"]
                - cpu_samples[item]["metrics"]["cer"]
                > 0.05
            ):
                failures.append(f"severe per-file CER regression: {item}")
            cpu_wer = cpu_samples[item]["metrics"].get("wer")
            mlx_wer = mlx_samples[item]["metrics"].get("wer")
            if (
                cpu_wer is not None
                and mlx_wer is not None
                and mlx_wer - cpu_wer > 0.05
            ):
                failures.append(f"severe per-file WER regression: {item}")

    long_samples = [
        sample_id
        for sample_id in common
        if set(cpu_samples[sample_id].get("tags", []))
        & {"five-minute", "meeting-30m", "meeting-60m", "meeting-120m"}
    ]
    if not long_samples:
        missing.append("long-form speed samples")
    else:
        if report.get("warm_runs", 0) < 1:
            missing.append("warm long-form speed samples")
        else:
            cpu_rtf = statistics.median(
                cpu_samples[item]["passes"][-1]["rtf"] for item in long_samples
            )
            mlx_rtf = statistics.median(
                mlx_samples[item]["passes"][-1]["rtf"] for item in long_samples
            )
            if mlx_rtf > cpu_rtf * 0.70:
                failures.append(
                    "MLX warm long-form processing is less than 30% faster than CPU"
                )

    realtime_rtfs: list[float] = []
    for cycle in successful.get("mlx", {}).get("cycles", []):
        for sample in cycle.get("samples", []):
            if "realtime" in sample.get("tags", []):
                realtime_rtfs.extend(
                    item["seconds"] / sample["duration_seconds"]
                    for item in sample["passes"][1:]
                )
    if len(realtime_rtfs) < 100:
        missing.append("100 serialized realtime utterances")
    elif _percentile(realtime_rtfs, 0.95) >= 1.0:
        failures.append("MLX warm realtime p95 is not below utterance duration")

    lifecycle = successful.get("mlx", {}).get("lifecycle_summary", {})
    completed_cycles = lifecycle.get("completed_cycles", 0)
    if completed_cycles < 50:
        missing.append("50 completed lifecycle start/stop cycles")
    if lifecycle.get("rss_after_cleanup_samples", 0) != completed_cycles:
        missing.append("complete lifecycle RSS samples")
    growth = lifecycle.get("rss_after_cleanup_growth_mb")
    rss_min = lifecycle.get("rss_after_cleanup_min_mb")
    rss_max = lifecycle.get("rss_after_cleanup_max_mb")
    if completed_cycles >= 50:
        if growth is None or rss_min is None or rss_max is None:
            missing.append("usable lifecycle RSS growth evidence")
        else:
            if growth > 256:
                failures.append(
                    "MLX cleanup RSS grows by more than 256MB across lifecycle cycles"
                )
            if rss_max - rss_min > 1024:
                failures.append("MLX intermediate cleanup RSS range exceeds 1GiB")
        physical_memory = lifecycle.get("physical_memory_mb")
        peak_memory = lifecycle.get("peak_rss_mb_max")
        if physical_memory is None or peak_memory is None:
            missing.append("usable lifecycle peak-memory evidence")
        elif peak_memory > physical_memory * 0.9:
            failures.append("MLX peak RSS exceeds 90% of physical memory")

    mlx_comparison = report.get("comparisons_against_pytorch_cpu", {}).get("mlx", [])
    low_alignment_rows = [
        item
        for item in mlx_comparison
        if item.get("timestamp_alignment_ratio", 0) < 0.8
    ]
    for item in low_alignment_rows:
        failures.append(f"timestamp token alignment below 80%: {item['id']}")
    for item in mlx_comparison:
        if not item.get("language_agrees", False):
            failures.append(f"detected language differs from CPU: {item['id']}")
        if (item.get("text_vs_cpu_cer") or 0) > 0.25:
            failures.append(f"text differs from CPU by more than 25% CER: {item['id']}")

    timestamp_rows = [
        item
        for item in mlx_comparison
        if item.get("timestamp_p95_abs_seconds") is not None
        and item.get("timestamp_alignment_ratio", 0) >= 0.8
    ]
    if not timestamp_rows:
        missing.append("MLX versus CPU timestamp comparison with at least 80% token alignment")
    else:
        for item in timestamp_rows:
            if (
                item["timestamp_median_abs_seconds"] > 0.050
                or item["timestamp_p95_abs_seconds"] > 0.150
            ):
                failures.append(
                    f"MLX word-boundary difference exceeds the 50ms/150ms gate: {item['id']}"
                )

    for sample in mlx_samples.values():
        for item in sample.get("passes", []):
            if item.get("expected_language_agrees") is False:
                failures.append(f"detected language differs from expected: {sample['id']}")

    for backend, result in successful.items():
        for cycle in result.get("cycles", []):
            for sample in cycle.get("samples", []):
                if any(item.get("possible_truncation") for item in sample.get("passes", [])):
                    failures.append(f"possible truncation: {backend}/{sample['id']}")

    status = "fail" if failures else "insufficient-evidence"
    return {
        "status": status,
        "recommendation": "keep-current-auto",
        "missing_evidence": missing,
        "failures": sorted(set(failures)),
    }


def _redact(run: dict[str, Any], include_transcripts: bool) -> None:
    if not run.get("ok"):
        return
    for cycle in run["result"]["cycles"]:
        for sample in cycle["samples"]:
            for item in sample["passes"]:
                text = item["text"]
                item["text_sha256"] = hashlib.sha256(text.encode("utf-8")).hexdigest()
                item["text_length"] = len(text)
                for word in item["words"]:
                    word.pop("text", None)
                if not include_transcripts:
                    item.pop("text", None)


def run_benchmark(args: argparse.Namespace) -> dict[str, Any]:
    if args.warm_runs < 0:
        raise ValueError("warm_runs must be non-negative")
    if args.lifecycle_cycles < 1:
        raise ValueError("lifecycle_cycles must be at least 1")
    samples = load_manifest(args.manifest)
    del samples  # Validation happens before expensive subprocesses start.
    manager = ModelManager()
    model_path = args.model_path or manager.local_path("qwen3-asr-1.7b")
    aligner_path = args.aligner_path or manager.local_path("qwen3-forced-aligner")
    if not Path(model_path).is_dir() or not Path(aligner_path).is_dir():
        raise ValueError("validated local Qwen and Forced Aligner directories are required")

    backends = [value.strip() for value in args.backends.split(",") if value.strip()]
    invalid = sorted(set(backends) - set(BACKENDS))
    if invalid:
        raise ValueError(f"unsupported benchmark backend(s): {', '.join(invalid)}")

    runs: list[dict[str, Any]] = []
    for backend in backends:
        print(f"benchmarking {backend}...", file=sys.stderr, flush=True)
        command = [
            sys.executable,
            "-m",
            "src.qwen_benchmark",
            "_worker",
            "--backend",
            backend,
            "--manifest",
            str(Path(args.manifest).resolve()),
            "--model-path",
            str(Path(model_path).resolve()),
            "--aligner-path",
            str(Path(aligner_path).resolve()),
            "--warm-runs",
            str(args.warm_runs),
            "--lifecycle-cycles",
            str(args.lifecycle_cycles),
        ]
        environment = os.environ.copy()
        environment.update({"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"})
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                env=environment,
                timeout=args.timeout,
            )
        except subprocess.TimeoutExpired as exc:
            runs.append(
                {
                    "backend": backend,
                    "ok": False,
                    "returncode": None,
                    "error": f"benchmark timed out after {exc.timeout} seconds",
                }
            )
            continue
        if completed.returncode:
            runs.append(
                {
                    "backend": backend,
                    "ok": False,
                    "returncode": completed.returncode,
                    "error": completed.stderr[-4000:],
                }
            )
            continue
        try:
            worker_result = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            runs.append(
                {
                    "backend": backend,
                    "ok": False,
                    "returncode": completed.returncode,
                    "error": f"worker stdout was not valid JSON: {exc}",
                }
            )
            continue
        runs.append({"backend": backend, "ok": True, "result": worker_result})

    report = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "source-tree",
        "offline_environment": True,
        "manifest_sha256": hashlib.sha256(Path(args.manifest).read_bytes()).hexdigest(),
        "warm_runs": args.warm_runs,
        "lifecycle_cycles": args.lifecycle_cycles,
        "runs": runs,
        "comparisons_against_pytorch_cpu": compare_runs(runs),
    }
    report["mlx_promotion_gate"] = assess_mlx_gate(report)
    for run in runs:
        _redact(run, args.include_transcripts)
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Offline Qwen backend benchmark")
    subparsers = parser.add_subparsers(dest="command", required=True)
    run = subparsers.add_parser("run", help="run isolated backend comparisons")
    run.add_argument("--manifest", required=True)
    run.add_argument("--output", required=True)
    run.add_argument("--backends", default=",".join(BACKENDS))
    run.add_argument("--model-path")
    run.add_argument("--aligner-path")
    run.add_argument("--warm-runs", type=int, default=1)
    run.add_argument("--lifecycle-cycles", type=int, default=1)
    run.add_argument("--timeout", type=float, default=None)
    run.add_argument("--include-transcripts", action="store_true")

    worker = subparsers.add_parser("_worker")
    worker.add_argument("--backend", required=True, choices=BACKENDS)
    worker.add_argument("--manifest", required=True)
    worker.add_argument("--model-path", required=True)
    worker.add_argument("--aligner-path", required=True)
    worker.add_argument("--warm-runs", type=int, required=True)
    worker.add_argument("--lifecycle-cycles", type=int, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "_worker":
        result = run_worker(
            backend=args.backend,
            manifest_path=args.manifest,
            model_path=args.model_path,
            aligner_path=args.aligner_path,
            warm_runs=args.warm_runs,
            lifecycle_cycles=args.lifecycle_cycles,
        )
        json.dump(result, sys.stdout, ensure_ascii=False)
        return 0
    report = run_benchmark(args)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"ok": all(run["ok"] for run in report["runs"]), "output": str(output)}))
    return 0 if all(run["ok"] for run in report["runs"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
