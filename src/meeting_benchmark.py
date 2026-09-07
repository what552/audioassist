"""Speaker-aware scoring for public meeting benchmark reports."""
from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from itertools import permutations
import json
from pathlib import Path
import time
import unicodedata
from typing import Any, Sequence

from .diarize import DiarizationEngine, SpeakerSegment
from .merge import speaker_for_interval
from .model_manager import ModelManager
from .qwen_benchmark import edit_distance


@dataclass(frozen=True)
class ReferenceTurn:
    speaker: str
    start: float
    end: float
    text: str


def load_reference_turns(path: str | Path) -> list[ReferenceTurn]:
    turns = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        turn = ReferenceTurn(
            speaker=str(row["speaker_id"]),
            start=float(row["start_s"]),
            end=float(row["end_s"]),
            text=str(row.get("text", "")),
        )
        if not 0 <= turn.start <= turn.end:
            raise ValueError(f"invalid reference turn in {path}: {row!r}")
        turns.append(turn)
    if not turns:
        raise ValueError(f"reference turns are empty: {path}")
    return sorted(turns, key=lambda turn: (turn.start, turn.end, turn.speaker))


def _character_units(text: str) -> list[str]:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    return [
        char
        for char in normalized
        if not char.isspace() and unicodedata.category(char)[0] not in {"P", "S"}
    ]


def _word_units(text: str) -> list[str]:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    cleaned = "".join(
        " " if unicodedata.category(char)[0] in {"P", "S"} else char
        for char in normalized
    )
    return cleaned.split()


def speaker_texts_from_reference(turns: Sequence[ReferenceTurn]) -> dict[str, str]:
    grouped: dict[str, list[str]] = defaultdict(list)
    for turn in turns:
        if turn.text.strip():
            grouped[turn.speaker].append(turn.text.strip())
    return {speaker: " ".join(parts) for speaker, parts in grouped.items()}


def speaker_texts_from_words(
    words: Sequence[dict[str, Any]],
    diarization: Sequence[SpeakerSegment],
) -> dict[str, str]:
    grouped: dict[str, list[str]] = defaultdict(list)
    segments = list(diarization)
    for word in words:
        speaker = speaker_for_interval(
            float(word["start"]), float(word["end"]), segments
        )
        grouped[speaker].append(str(word.get("text", "")))
    return {speaker: " ".join(parts) for speaker, parts in grouped.items()}


def concatenated_permutation_error_rate(
    reference: dict[str, str],
    hypothesis: dict[str, str],
    *,
    metric: str,
) -> dict[str, float | int]:
    if metric not in {"cpcer", "cpwer"}:
        raise ValueError("speaker metric must be cpcer or cpwer")
    units = _character_units if metric == "cpcer" else _word_units
    references = [units(reference[key]) for key in sorted(reference)]
    hypotheses = [units(hypothesis[key]) for key in sorted(hypothesis)]
    size = max(len(references), len(hypotheses), 1)
    references += [[] for _ in range(size - len(references))]
    hypotheses += [[] for _ in range(size - len(hypotheses))]
    errors = min(
        sum(edit_distance(references[index], candidate) for index, candidate in enumerate(order))
        for order in permutations(hypotheses)
    )
    denominator = sum(len(item) for item in references)
    return {
        "errors": errors,
        "reference_units": denominator,
        "rate": errors / denominator if denominator else 0.0,
    }


def diarization_error(
    reference: Sequence[ReferenceTurn], hypothesis: Sequence[SpeakerSegment]
) -> dict[str, float]:
    from pyannote.core import Annotation, Segment
    from pyannote.metrics.diarization import DiarizationErrorRate

    reference_annotation = Annotation()
    hypothesis_annotation = Annotation()
    for index, turn in enumerate(reference):
        reference_annotation[Segment(turn.start, turn.end), index] = turn.speaker
    for index, turn in enumerate(hypothesis):
        hypothesis_annotation[Segment(turn.start, turn.end), index] = turn.speaker
    detailed = DiarizationErrorRate(collar=0.0, skip_overlap=False)(
        reference_annotation, hypothesis_annotation, detailed=True
    )
    return {str(key): float(value) for key, value in detailed.items()}


def _asr_samples(report: dict[str, Any]) -> dict[str, dict[str, dict[str, Any]]]:
    indexed = {}
    for run in report.get("runs", []):
        if not run.get("ok"):
            continue
        cycles = run["result"].get("cycles", [])
        if cycles:
            indexed[run["backend"]] = {
                sample["id"]: sample for sample in cycles[0].get("samples", [])
            }
    return indexed


def evaluate(
    *, manifest_path: str, asr_report_path: str, diarizer_model_id: str
) -> dict[str, Any]:
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    asr_report = json.loads(Path(asr_report_path).read_text(encoding="utf-8"))
    asr_by_backend = _asr_samples(asr_report)
    manager = ModelManager()
    if not manager.is_downloaded(diarizer_model_id):
        raise ValueError(f"local diarizer model is required: {diarizer_model_id}")

    results = []
    engine = DiarizationEngine(model_id=diarizer_model_id)
    for item in manifest["samples"]:
        sample_id = str(item["id"])
        turns_path = item.get("reference_turns_path")
        metric = item.get("speaker_metric")
        if not turns_path or metric not in {"cpcer", "cpwer"}:
            raise ValueError(f"speaker-aware metadata missing for {sample_id}")
        turns = load_reference_turns(turns_path)
        engine.num_speakers = int(item["num_speakers"])
        started = time.perf_counter()
        diarization = engine.diarize(str(item["path"]))
        elapsed = time.perf_counter() - started
        reference_speakers = speaker_texts_from_reference(turns)
        backend_scores = {}
        for backend, samples in asr_by_backend.items():
            sample = samples.get(sample_id)
            if not sample or not sample.get("ok", True) or not sample.get("passes"):
                backend_scores[backend] = {
                    "ok": False,
                    "error": (sample or {}).get("error", "ASR sample unavailable"),
                }
                continue
            words = sample["passes"][0].get("words", [])
            if words and "text" not in words[0]:
                raise ValueError(
                    "ASR report is redacted; rerun with --include-transcripts"
                )
            hypothesis_speakers = speaker_texts_from_words(words, diarization)
            backend_scores[backend] = {
                "ok": True,
                "hypothesis_speakers": len(hypothesis_speakers),
                metric: concatenated_permutation_error_rate(
                    reference_speakers, hypothesis_speakers, metric=metric
                ),
            }
        results.append(
            {
                "id": sample_id,
                "duration_seconds": float(item["duration_seconds"]),
                "reference_speakers": len(reference_speakers),
                "detected_speakers": len({turn.speaker for turn in diarization}),
                "diarization_seconds": elapsed,
                "der": diarization_error(turns, diarization),
                "backend_scores": backend_scores,
            }
        )
    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "asr_manifest_sha256": asr_report.get("manifest_sha256"),
        "diarizer_model_id": diarizer_model_id,
        "overlap_scored": True,
        "collar_seconds": 0.0,
        "samples": results,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Score Qwen meeting benchmark output")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--asr-report", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument(
        "--diarizer-model-id", default="pyannote-diarization-community-1"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = evaluate(
        manifest_path=args.manifest,
        asr_report_path=args.asr_report,
        diarizer_model_id=args.diarizer_model_id,
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"ok": True, "output": str(output)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
