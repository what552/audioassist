"""QWEN-03 manifest, metric, comparison, and privacy tests."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import wave

import pytest

from src.qwen_benchmark import (
    _redact,
    assess_mlx_gate,
    compare_runs,
    edit_distance,
    error_rates,
    load_manifest,
    run_benchmark,
)


def _wav(path: Path, seconds: int = 1) -> None:
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16_000)
        output.writeframes(b"\0\0" * 16_000 * seconds)


def test_manifest_resolves_relative_paths_and_reference(tmp_path):
    _wav(tmp_path / "sample.wav")
    (tmp_path / "reference.txt").write_text("你好 world", encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "samples": [
                    {
                        "id": "mixed",
                        "path": "sample.wav",
                        "tags": ["code-switching"],
                        "reference_path": "reference.txt",
                        "reference_kind": "human",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    sample = load_manifest(str(manifest))[0]

    assert sample.path == str((tmp_path / "sample.wav").resolve())
    assert sample.reference_text == "你好 world"
    assert sample.tags == ("code-switching",)


def test_manifest_can_use_a_bounded_existing_transcript_as_pseudo_reference(tmp_path):
    _wav(tmp_path / "sample.wav")
    (tmp_path / "transcript.json").write_text(
        json.dumps(
            {
                "segments": [
                    {"start": 0, "text": "first"},
                    {"start": 3, "text": " second"},
                    {"start": 8, "text": " excluded"},
                ]
            }
        ),
        encoding="utf-8",
    )
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "samples": [
                    {
                        "id": "bounded",
                        "path": "sample.wav",
                        "reference_transcript_path": "transcript.json",
                        "reference_end_seconds": 5,
                        "reference_kind": "pseudo",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    assert load_manifest(str(manifest))[0].reference_text == "first second"


@pytest.mark.parametrize(
    "payload, message",
    [
        ({"samples": []}, "non-empty"),
        (
            {
                "samples": [
                    {
                        "id": "one",
                        "path": "missing.wav",
                    }
                ]
            },
            "does not exist",
        ),
    ],
)
def test_manifest_rejects_invalid_input(tmp_path, payload, message):
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        load_manifest(str(path))


def test_manifest_rejects_nonpositive_declared_duration(tmp_path):
    _wav(tmp_path / "sample.wav")
    path = tmp_path / "manifest.json"
    path.write_text(
        json.dumps(
            {
                "samples": [
                    {"id": "bad-duration", "path": "sample.wav", "duration_seconds": 0}
                ]
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="positive"):
        load_manifest(str(path))


def test_edit_distance_and_language_aware_error_rates():
    assert edit_distance(list("kitten"), list("sitting")) == 3
    rates = error_rates("你好，World!", "你好 world")
    assert rates["cer"] == 0
    assert rates["wer"] == 0


def _run(backend: str, text: str, start: float) -> dict:
    sample = {
        "id": "sample",
        "passes": [
            {
                "text": text,
                "language": "Chinese",
                "words": [{"text": text, "start": start, "end": 1.0 + start}],
            }
        ],
    }
    return {
        "backend": backend,
        "ok": True,
        "result": {"cycles": [{"samples": [sample]}]},
    }


def test_compare_runs_reports_text_language_and_timestamp_differences():
    cpu = _run("pytorch-cpu", "你好", 0.0)
    mlx = _run("mlx", "你号", 0.1)
    cpu["result"]["cycles"][0]["samples"][0]["passes"][0]["words"][0]["text"] = "共"
    mlx["result"]["cycles"][0]["samples"][0]["passes"][0]["words"][0]["text"] = "共"
    comparison = compare_runs([cpu, mlx])["mlx"][0]
    assert comparison["language_agrees"] is True
    assert comparison["text_vs_cpu_cer"] == 0.5
    assert comparison["timestamp_median_abs_seconds"] == pytest.approx(0.1)
    assert comparison["timestamp_alignment_ratio"] == 1.0


def test_timestamp_comparison_aligns_matching_tokens_not_array_indexes():
    cpu = _run("pytorch-cpu", "AB", 0.0)
    cpu_pass = cpu["result"]["cycles"][0]["samples"][0]["passes"][0]
    cpu_pass["words"] = [
        {"text": "A", "start": 0.0, "end": 0.5},
        {"text": "B", "start": 0.5, "end": 1.0},
    ]
    mlx = _run("mlx", "XAB", 0.0)
    mlx_pass = mlx["result"]["cycles"][0]["samples"][0]["passes"][0]
    mlx_pass["words"] = [
        {"text": "X", "start": 0.0, "end": 0.1},
        {"text": "A", "start": 0.1, "end": 0.6},
        {"text": "B", "start": 0.6, "end": 1.1},
    ]

    comparison = compare_runs([cpu, mlx])["mlx"][0]

    assert comparison["timestamp_matched_words"] == 2
    assert comparison["timestamp_alignment_ratio"] == pytest.approx(2 / 3)
    assert comparison["timestamp_median_abs_seconds"] == pytest.approx(0.1)


def test_redaction_removes_meeting_text_and_keeps_hashes():
    run = _run("mlx", "private meeting", 0.0)
    _redact(run, include_transcripts=False)
    item = run["result"]["cycles"][0]["samples"][0]["passes"][0]
    assert "text" not in item
    assert item["text_length"] == 15
    assert len(item["text_sha256"]) == 64
    assert "text" not in item["words"][0]


def test_gate_never_promotes_incomplete_source_tree_evidence():
    cpu = _run("pytorch-cpu", "你好", 0.0)
    mlx = _run("mlx", "你好", 0.0)
    for run in (cpu, mlx):
        sample = run["result"]["cycles"][0]["samples"][0]
        sample.update(
            {
                "tags": ["mandarin", "five-minute"],
                "duration_seconds": 300,
                "reference_kind": "pseudo",
                "metrics": {"cer": 0.0, "wer": 0.0},
            }
        )
        sample["passes"][0].update({"seconds": 1.0, "rtf": 0.01, "possible_truncation": False})
    mlx["result"]["cycles"][0]["samples"][0]["passes"][0].update(
        {"seconds": 0.5, "rtf": 0.005}
    )
    report = {
        "source": "source-tree",
        "lifecycle_cycles": 1,
        "runs": [cpu, mlx],
        "comparisons_against_pytorch_cpu": compare_runs([cpu, mlx]),
    }
    gate = assess_mlx_gate(report)
    assert gate["status"] == "insufficient-evidence"
    assert gate["recommendation"] == "keep-current-auto"
    assert "signed-app benchmark report" in gate["missing_evidence"]


def test_gate_counts_realtime_utterances_across_lifecycle_cycles():
    mlx = _run("mlx", "hello", 0.0)
    sample = mlx["result"]["cycles"][0]["samples"][0]
    sample.update({"tags": ["realtime"], "duration_seconds": 5})
    sample["passes"] = [
        {**sample["passes"][0], "seconds": 1.0, "rtf": 0.2, "possible_truncation": False}
        for _ in range(2)
    ]
    mlx["result"]["cycles"] = [
        {"samples": [sample]}
        for _ in range(50)
    ]
    mlx["result"]["lifecycle_summary"] = {"rss_after_cleanup_growth_mb": 10}
    report = {
        "source": "source-tree",
        "lifecycle_cycles": 50,
        "runs": [mlx],
        "comparisons_against_pytorch_cpu": {},
    }

    gate = assess_mlx_gate(report)

    assert "100 serialized realtime utterances" not in gate["missing_evidence"]
    assert not any("cleanup RSS" in failure for failure in gate["failures"])


def test_parent_forces_offline_subprocess_and_redacts_output(tmp_path):
    audio = tmp_path / "sample.wav"
    _wav(audio)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps({"samples": [{"id": "one", "path": str(audio)}]}),
        encoding="utf-8",
    )
    model = tmp_path / "model"
    aligner = tmp_path / "aligner"
    model.mkdir()
    aligner.mkdir()
    worker_result = _run("mlx", "secret", 0.0)["result"]
    completed = SimpleNamespace(returncode=0, stdout=json.dumps(worker_result), stderr="")
    args = argparse.Namespace(
        manifest=str(manifest),
        output=str(tmp_path / "report.json"),
        backends="mlx",
        model_path=str(model),
        aligner_path=str(aligner),
        warm_runs=1,
        lifecycle_cycles=1,
        timeout=None,
        include_transcripts=False,
        source="source-tree",
    )

    with patch("src.qwen_benchmark.subprocess.run", return_value=completed) as run:
        report = run_benchmark(args)

    environment = run.call_args.kwargs["env"]
    assert environment["HF_HUB_OFFLINE"] == "1"
    assert environment["TRANSFORMERS_OFFLINE"] == "1"
    saved_pass = report["runs"][0]["result"]["cycles"][0]["samples"][0]["passes"][0]
    assert "text" not in saved_pass


def test_parent_rejects_invalid_cycle_counts(tmp_path):
    audio = tmp_path / "sample.wav"
    _wav(audio)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps({"samples": [{"id": "one", "path": str(audio)}]}),
        encoding="utf-8",
    )
    args = argparse.Namespace(
        manifest=str(manifest),
        output=str(tmp_path / "report.json"),
        backends="mlx",
        model_path=None,
        aligner_path=None,
        warm_runs=-1,
        lifecycle_cycles=1,
        timeout=None,
        include_transcripts=False,
        source="source-tree",
    )
    with pytest.raises(ValueError, match="warm_runs"):
        run_benchmark(args)
