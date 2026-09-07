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
    BenchmarkSample,
    _audio_duration,
    _redact,
    _result_payload,
    _validated_duration,
    assess_backend_quality,
    assess_mlx_gate,
    compare_runs,
    edit_distance,
    error_rates,
    load_manifest,
    run_benchmark,
    run_worker,
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


def test_measured_duration_rejects_false_declaration_and_duration_tag(tmp_path):
    audio = tmp_path / "sample.wav"
    _wav(audio)
    declared = BenchmarkSample("declared", str(audio), (), duration_seconds=300)
    tagged = BenchmarkSample("tagged", str(audio), ("five-minute",))

    with pytest.raises(ValueError, match="differs from audio"):
        _validated_duration(declared)
    with pytest.raises(ValueError, match="does not match tag"):
        _validated_duration(tagged)


def test_wav_with_unbounded_stream_header_uses_ffprobe_duration(tmp_path):
    audio = tmp_path / "streaming.wav"
    _wav(audio)
    payload = bytearray(audio.read_bytes())
    payload[40:44] = (0xFFFFFFFF).to_bytes(4, "little")
    audio.write_bytes(payload)
    ffprobe = SimpleNamespace(stdout="1.000000\n")

    with patch("src.qwen_benchmark.subprocess.run", return_value=ffprobe) as run:
        assert _audio_duration(str(audio)) == 1.0

    assert run.call_args.args[0][0] == "ffprobe"


def test_worker_rejects_mixed_realtime_and_offline_manifest(tmp_path):
    _wav(tmp_path / "realtime.wav")
    _wav(tmp_path / "offline.wav")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "samples": [
                    {"id": "realtime", "path": "realtime.wav", "tags": ["realtime"]},
                    {"id": "offline", "path": "offline.wav", "tags": ["offline"]},
                ]
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="cannot mix"):
        run_worker(
            backend="mlx",
            manifest_path=str(manifest),
            model_path="/model",
            aligner_path="/aligner",
            warm_runs=0,
            lifecycle_cycles=1,
        )


def test_realtime_worker_disables_aligner_and_timestamps(tmp_path):
    audio = tmp_path / "sample.wav"
    _wav(audio, seconds=5)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {"samples": [{"id": "short", "path": "sample.wav", "tags": ["realtime"]}]}
        ),
        encoding="utf-8",
    )
    engine = SimpleNamespace(
        load=lambda: None,
        close=lambda: None,
        transcribe=lambda _path: SimpleNamespace(text="hello", language="English", words=[]),
        last_truncated=False,
        inference_config={"batch_size": 1, "max_chunk_seconds": 30},
        last_chunk_diagnostics=[{"start": 0, "end": 5, "possible_truncation": True,
                                 "generated_tokens": 3, "finish_reason": "eos"}],
    )

    with (
        patch("src.qwen_benchmark.create_qwen_engine", return_value=engine) as create,
        patch("src.qwen_benchmark._rss_mb", return_value=100.0),
        patch("src.qwen_benchmark._peak_rss_mb", return_value=200.0),
        patch("src.qwen_benchmark._physical_memory_mb", return_value=1024.0),
    ):
        result = run_worker(
            backend="mlx",
            manifest_path=str(manifest),
            model_path="/model",
            aligner_path="/aligner",
            warm_runs=1,
            lifecycle_cycles=1,
        )

    assert create.call_args.kwargs["aligner_path"] is None
    assert create.call_args.kwargs["with_timestamps"] is False
    assert result["mode"] == "realtime-asr"
    assert len(result["cycles"][0]["samples"][0]["passes"]) == 2
    recorded = result["cycles"][0]["samples"][0]["passes"][0]
    assert recorded["possible_truncation"]
    assert recorded["chunks"] == engine.last_chunk_diagnostics
    assert recorded["chunks"] is not engine.last_chunk_diagnostics
    assert recorded["inference_config"] == engine.inference_config


def test_edit_distance_and_language_aware_error_rates():
    assert edit_distance(list("kitten"), list("sitting")) == 3
    rates = error_rates("你好，World!", "你好 world")
    assert rates["cer"] == 0
    assert rates["wer"] == 0


def test_result_keeps_large_timestamp_tail_as_diagnostic_only():
    result = SimpleNamespace(
        text="only the beginning",
        language="English",
        words=[SimpleNamespace(word="only", start=0.0, end=2.0)],
    )

    payload = _result_payload(result, 100.0, 1.0, truncated=False)

    assert payload["timestamp_tail_gap_seconds"] == 98.0
    assert payload["possible_truncation"] is False


def test_worker_compares_expected_language_at_product_boundary(tmp_path):
    audio = tmp_path / "sample.wav"
    _wav(audio, seconds=1)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "samples": [
                    {
                        "id": "chinese",
                        "path": "sample.wav",
                        "expected_language": "zh",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    engine = SimpleNamespace(
        load=lambda: None,
        close=lambda: None,
        transcribe=lambda _path: SimpleNamespace(
            text="这是一段明确的中文转写内容",
            language="English",
            words=[],
        ),
        last_truncated=False,
    )

    with (
        patch("src.qwen_benchmark.create_qwen_engine", return_value=engine),
        patch("src.qwen_benchmark._rss_mb", return_value=100.0),
        patch("src.qwen_benchmark._peak_rss_mb", return_value=200.0),
        patch("src.qwen_benchmark._physical_memory_mb", return_value=1024.0),
    ):
        result = run_worker(
            backend="pytorch-cpu",
            manifest_path=str(manifest),
            model_path="/model",
            aligner_path="/aligner",
            warm_runs=0,
            lifecycle_cycles=1,
        )

    sample = result["cycles"][0]["samples"][0]
    assert sample["passes"][0]["language"] == "English"
    assert sample["passes"][0]["canonical_language"] == "zh"
    assert sample["passes"][0]["expected_language_agrees"] is True


def test_worker_isolates_sample_failure_and_continues(tmp_path):
    first = tmp_path / "first.wav"
    second = tmp_path / "second.wav"
    _wav(first)
    _wav(second)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "samples": [
                    {"id": "fails", "path": "first.wav"},
                    {"id": "passes", "path": "second.wav"},
                ]
            }
        ),
        encoding="utf-8",
    )
    calls = 0

    def transcribe(_path):
        nonlocal calls
        calls += 1
        if calls == 1:
            engine.last_truncated = True
            engine.last_chunk_diagnostics = [
                {"start": 0, "end": 1, "possible_truncation": True}
            ]
            raise RuntimeError("generation limit")
        engine.last_truncated = False
        engine.last_chunk_diagnostics = []
        return SimpleNamespace(text="hello", language="English", words=[])

    engine = SimpleNamespace(
        load=lambda: None,
        close=lambda: None,
        transcribe=transcribe,
        last_truncated=False,
        last_chunk_diagnostics=[],
        inference_config={"max_new_tokens": 1024},
    )
    with (
        patch("src.qwen_benchmark.create_qwen_engine", return_value=engine),
        patch("src.qwen_benchmark._rss_mb", return_value=100.0),
        patch("src.qwen_benchmark._peak_rss_mb", return_value=200.0),
        patch("src.qwen_benchmark._physical_memory_mb", return_value=1024.0),
    ):
        result = run_worker(
            backend="pytorch-cpu",
            manifest_path=str(manifest),
            model_path="/model",
            aligner_path="/aligner",
            warm_runs=0,
            lifecycle_cycles=1,
        )

    failed, passed = result["cycles"][0]["samples"]
    assert failed["ok"] is False
    assert failed["error"] == "RuntimeError: generation limit"
    assert failed["failed_pass"]["possible_truncation"] is True
    assert failed["failed_pass"]["chunks"][0]["possible_truncation"] is True
    assert failed["passes"] == []
    assert passed["ok"] is True
    assert passed["error"] is None
    assert passed["passes"][0]["text"] == "hello"


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


def test_compare_runs_uses_canonical_language_when_available():
    cpu = _run("pytorch-cpu", "你好", 0.0)
    mlx = _run("mlx", "你好", 0.0)
    cpu_pass = cpu["result"]["cycles"][0]["samples"][0]["passes"][0]
    mlx_pass = mlx["result"]["cycles"][0]["samples"][0]["passes"][0]
    cpu_pass.update({"language": "Chinese", "canonical_language": "zh"})
    mlx_pass.update({"language": "zh", "canonical_language": "zh"})

    assert compare_runs([cpu, mlx])["mlx"][0]["language_agrees"] is True


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


def test_explicit_transcript_report_keeps_word_text_for_speaker_scoring():
    run = _run("mlx", "public benchmark", 0.0)

    _redact(run, include_transcripts=True)

    item = run["result"]["cycles"][0]["samples"][0]["passes"][0]
    assert item["text"] == "public benchmark"
    assert item["words"][0]["text"] == "public benchmark"
    assert len(item["text_sha256"]) == 64


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
    assert "independently verified signed-app release checklist" in gate["missing_evidence"]


def test_gate_counts_realtime_utterances_across_lifecycle_cycles():
    mlx = _run("mlx", "hello", 0.0)
    sample = mlx["result"]["cycles"][0]["samples"][0]
    sample.update({"tags": ["realtime"], "duration_seconds": 5})
    sample["passes"] = [
        {**sample["passes"][0], "seconds": 1.0, "rtf": 0.2, "possible_truncation": False}
        for _ in range(3)
    ]
    mlx["result"]["cycles"] = [
        {"samples": [sample]}
        for _ in range(50)
    ]
    mlx["result"]["lifecycle_summary"] = {
        "completed_cycles": 50,
        "rss_after_cleanup_samples": 50,
        "rss_after_cleanup_growth_mb": 10,
        "rss_after_cleanup_min_mb": 500,
        "rss_after_cleanup_max_mb": 510,
        "physical_memory_mb": 65536,
        "peak_rss_mb_max": 6000,
    }
    report = {
        "source": "source-tree",
        "lifecycle_cycles": 50,
        "runs": [mlx],
        "comparisons_against_pytorch_cpu": {},
    }

    gate = assess_mlx_gate(report)

    assert "100 serialized realtime utterances" not in gate["missing_evidence"]
    assert not any("cleanup RSS" in failure for failure in gate["failures"])


def test_gate_ignores_cold_realtime_passes_and_requires_measured_lifecycle():
    mlx = _run("mlx", "hello", 0.0)
    sample = mlx["result"]["cycles"][0]["samples"][0]
    sample.update({"tags": ["realtime"], "duration_seconds": 5})
    sample["passes"] = [
        {**sample["passes"][0], "seconds": 1.0, "possible_truncation": False}
        for _ in range(2)
    ]
    mlx["result"]["cycles"] = [{"samples": [sample]} for _ in range(50)]
    mlx["result"]["lifecycle_summary"] = {
        "completed_cycles": 49,
        "rss_after_cleanup_samples": 0,
    }

    gate = assess_mlx_gate(
        {"runs": [mlx], "comparisons_against_pytorch_cpu": {}, "warm_runs": 1}
    )

    assert "100 serialized realtime utterances" in gate["missing_evidence"]
    assert "50 completed lifecycle start/stop cycles" in gate["missing_evidence"]
    assert "complete lifecycle RSS samples" in gate["missing_evidence"]


def test_gate_uses_realtime_factor_for_mixed_utterance_durations():
    mlx = _run("mlx", "hello", 0.0)
    short = mlx["result"]["cycles"][0]["samples"][0]
    short.update({"id": "short", "tags": ["realtime"], "duration_seconds": 1})
    short["passes"] = [
        {**short["passes"][0], "seconds": 2.0, "possible_truncation": False}
        for _ in range(101)
    ]
    long = {
        **short,
        "id": "long",
        "duration_seconds": 10,
        "passes": [
            {**short["passes"][0], "seconds": 2.0, "possible_truncation": False}
            for _ in range(101)
        ],
    }
    mlx["result"]["cycles"] = [{"samples": [short, long]}]

    gate = assess_mlx_gate(
        {"runs": [mlx], "comparisons_against_pytorch_cpu": {}, "warm_runs": 100}
    )

    assert "MLX warm realtime p95 is not below utterance duration" in gate["failures"]


def test_gate_detects_per_file_wer_regression_hidden_by_aggregate():
    cpu = _run("pytorch-cpu", "one", 0.0)
    mlx = _run("mlx", "one", 0.0)
    cpu_first = cpu["result"]["cycles"][0]["samples"][0]
    mlx_first = mlx["result"]["cycles"][0]["samples"][0]
    cpu_first.update(
        {
            "id": "regressed",
            "tags": ["english"],
            "reference_kind": "human",
            "metrics": {"cer": 0.0, "wer": 0.0},
        }
    )
    mlx_first.update(
        {
            "id": "regressed",
            "tags": ["english"],
            "reference_kind": "human",
            "metrics": {"cer": 0.0, "wer": 0.1},
        }
    )
    cpu_second = {
        **cpu_first,
        "id": "improved",
        "metrics": {"cer": 0.0, "wer": 1.0},
    }
    mlx_second = {
        **mlx_first,
        "id": "improved",
        "metrics": {"cer": 0.0, "wer": 0.9},
    }
    cpu["result"]["cycles"][0]["samples"] = [cpu_first, cpu_second]
    mlx["result"]["cycles"][0]["samples"] = [mlx_first, mlx_second]

    gate = assess_mlx_gate(
        {
            "runs": [cpu, mlx],
            "comparisons_against_pytorch_cpu": compare_runs([cpu, mlx]),
            "warm_runs": 1,
        }
    )

    assert "MLX aggregate WER regresses by more than 0.5 percentage points" not in gate[
        "failures"
    ]
    assert "severe per-file WER regression: regressed" in gate["failures"]


def test_gate_does_not_use_whitespace_wer_for_code_switching_text():
    cpu = _run("pytorch-cpu", "你好 hello", 0.0)
    mlx = _run("mlx", "你好hello", 0.0)
    for run, wer in ((cpu, 0.0), (mlx, 1.0)):
        sample = run["result"]["cycles"][0]["samples"][0]
        sample.update(
            {
                "tags": ["english", "code-switching"],
                "reference_kind": "human",
                "metrics": {"cer": 0.0, "wer": wer},
            }
        )

    gate = assess_mlx_gate(
        {
            "runs": [cpu, mlx],
            "comparisons_against_pytorch_cpu": compare_runs([cpu, mlx]),
            "warm_runs": 1,
        }
    )

    assert not any("WER" in failure for failure in gate["failures"])


def test_backend_quality_reports_candidate_truncation_and_severe_cer():
    mps = _run("pytorch-mps", "short", 0.0)
    sample = mps["result"]["cycles"][0]["samples"][0]
    sample.update({"reference_kind": "human", "metrics": {"cer": 0.9, "wer": 1.0}})
    sample["passes"][0].update(
        {"possible_truncation": True, "expected_language_agrees": False}
    )

    quality = assess_backend_quality({"runs": [mps]})

    assert quality["pytorch-mps"]["status"] == "fail"
    assert any("possible truncation" in item for item in quality["pytorch-mps"]["failures"])
    assert any("CER exceeds 5%" in item for item in quality["pytorch-mps"]["failures"])


def test_mlx_gate_does_not_attribute_mps_truncation_to_mlx():
    cpu = _run("pytorch-cpu", "hello", 0.0)
    mps = _run("pytorch-mps", "hello", 0.0)
    mlx = _run("mlx", "hello", 0.0)
    mps["result"]["cycles"][0]["samples"][0]["passes"][0][
        "possible_truncation"
    ] = True

    gate = assess_mlx_gate(
        {
            "runs": [cpu, mps, mlx],
            "comparisons_against_pytorch_cpu": compare_runs([cpu, mlx]),
            "warm_runs": 0,
        }
    )

    assert not any("pytorch-mps" in failure for failure in gate["failures"])


def test_gate_fails_low_alignment_language_and_severe_text_difference():
    report = {
        "runs": [],
        "comparisons_against_pytorch_cpu": {
            "mlx": [
                {
                    "id": "bad",
                    "language_agrees": False,
                    "text_vs_cpu_cer": 0.4,
                    "timestamp_alignment_ratio": 0.5,
                    "timestamp_median_abs_seconds": 0.01,
                    "timestamp_p95_abs_seconds": 0.02,
                }
            ]
        },
    }

    gate = assess_mlx_gate(report)

    assert gate["status"] == "fail"
    assert "timestamp token alignment below 80%: bad" in gate["failures"]
    assert "detected language differs from CPU: bad" in gate["failures"]
    assert "text differs from CPU by more than 25% CER: bad" in gate["failures"]


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
