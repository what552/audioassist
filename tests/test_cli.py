"""Tests for stable CLI output, commands, and exit codes."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

from src import cli
from src.service import ConfigurationError


def _write_transcript(output_dir, job_id: str = "job-1") -> None:
    session_dir = output_dir / "meetings" / job_id
    session_dir.mkdir(parents=True)
    (session_dir / "transcript.json").write_text(
        json.dumps({
            "job_id": job_id,
            "filename": "meeting.wav",
            "audio": "/audio/meeting.wav",
            "language": "zh",
            "created_at": "2026-09-05T12:00:00",
            "segments": [
                {
                    "speaker": "SPEAKER_00",
                    "start": 0,
                    "end": 3,
                    "text": "你好",
                    "words": [],
                }
            ],
        }, ensure_ascii=False),
        encoding="utf-8",
    )


def test_sessions_list_json_envelope(tmp_path, capsys):
    _write_transcript(tmp_path)

    exit_code = cli.main(["--json", "--output-dir", str(tmp_path), "sessions", "list"])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == cli.EXIT_OK
    assert payload["ok"] is True
    assert payload["error"] is None
    assert payload["data"][0]["job_id"] == "job-1"


def test_sessions_show_missing_has_stable_error_and_exit_code(tmp_path, capsys):
    exit_code = cli.main([
        "--json", "--output-dir", str(tmp_path), "sessions", "show", "missing"
    ])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == cli.EXIT_NOT_FOUND
    assert payload["ok"] is False
    assert payload["error"]["code"] == "session_not_found"


def test_status_human_output(tmp_path, capsys):
    _write_transcript(tmp_path)

    exit_code = cli.main(["--output-dir", str(tmp_path), "status", "job-1"])

    output = capsys.readouterr().out
    assert exit_code == cli.EXIT_OK
    assert "job-1\tcompleted\t100%" in output


def test_transcribe_json_keeps_progress_on_stderr(tmp_path, capsys, monkeypatch):
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"RIFF")

    def fake_transcribe(self, path, **kwargs):
        kwargs["progress_callback"](0.5, "Working")
        return {
            "job_id": "job-new",
            "json_path": str(tmp_path / "transcript.json"),
            "markdown_path": str(tmp_path / "transcript.md"),
            "audio_path": path,
            "engine": "qwen",
            "model_id": None,
        }

    monkeypatch.setattr(cli.AudioAssistService, "transcribe", fake_transcribe)
    exit_code = cli.main([
        "--json", "--output-dir", str(tmp_path), "transcribe", str(audio)
    ])

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert exit_code == cli.EXIT_OK
    assert payload["data"]["job_id"] == "job-new"
    assert "Working" in captured.err


def test_summarize_saves_version(tmp_path, capsys, monkeypatch):
    _write_transcript(tmp_path)
    monkeypatch.setattr(
        cli.AudioAssistService,
        "summarize",
        lambda self, job_id, prompt, stream=False: "结构化纪要",
    )

    exit_code = cli.main([
        "--json", "--output-dir", str(tmp_path), "summarize", "job-1"
    ])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == cli.EXIT_OK
    assert payload["data"]["summary"] == "结构化纪要"
    versions = json.loads(
        (tmp_path / "meetings" / "job-1" / "summary.json").read_text(encoding="utf-8")
    )
    assert versions[-1]["text"] == "结构化纪要"


def test_json_success_is_exactly_one_line(tmp_path, capsys):
    exit_code = cli.main(["--json", "--output-dir", str(tmp_path), "sessions", "list"])

    output = capsys.readouterr().out
    assert exit_code == cli.EXIT_OK
    assert output.count("\n") == 1
    assert json.loads(output)["ok"] is True


def test_json_redirects_dependency_stdout_to_stderr(tmp_path, capsys, monkeypatch):
    def noisy_list(_self):
        print("third-party diagnostic")
        return []

    monkeypatch.setattr(cli.AudioAssistService, "list_sessions", noisy_list)
    exit_code = cli.main(["--json", "--output-dir", str(tmp_path), "sessions", "list"])

    captured = capsys.readouterr()
    assert exit_code == cli.EXIT_OK
    assert json.loads(captured.out)["ok"] is True
    assert "third-party diagnostic" not in captured.out
    assert "third-party diagnostic" in captured.err


@pytest.mark.parametrize(
    "arguments",
    [
        ["--json", "unknown"],
        ["--json", "sessions"],
        ["--json", "status"],
        ["--json", "transcribe", "file.wav", "--num-speakers", "0"],
        ["--json", "transcribe", "file.wav", "--engine", "invalid"],
    ],
)
def test_invalid_arguments_use_json_envelope(arguments, capsys):
    exit_code = cli.main(arguments)

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert exit_code == cli.EXIT_USAGE
    assert payload["ok"] is False
    assert payload["error"]["code"] == "invalid_arguments"


def test_human_error_is_written_to_stderr(tmp_path, capsys):
    exit_code = cli.main(["--output-dir", str(tmp_path), "status", "missing"])

    captured = capsys.readouterr()
    assert exit_code == cli.EXIT_NOT_FOUND
    assert captured.out == ""
    assert "Session not found" in captured.err


def test_missing_input_file_exit_code(tmp_path, capsys):
    exit_code = cli.main([
        "--json",
        "--output-dir",
        str(tmp_path),
        "transcribe",
        str(tmp_path / "missing.wav"),
    ])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == cli.EXIT_NOT_FOUND
    assert payload["error"]["code"] == "file_not_found"


@pytest.mark.parametrize(
    ("error", "expected_code", "expected_exit"),
    [
        (ConfigurationError("missing API config"), "configuration_error", cli.EXIT_NOT_READY),
        (ImportError("No module named qwen_asr"), "dependency_not_ready", cli.EXIT_NOT_READY),
        (RuntimeError("pipeline exploded"), "processing_failed", cli.EXIT_PROCESSING),
    ],
)
def test_runtime_error_matrix(
    tmp_path, capsys, monkeypatch, error, expected_code, expected_exit
):
    monkeypatch.setattr(
        cli.AudioAssistService,
        "list_sessions",
        lambda _self: (_ for _ in ()).throw(error),
    )

    exit_code = cli.main(["--json", "--output-dir", str(tmp_path), "sessions", "list"])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == expected_exit
    assert payload["error"]["code"] == expected_code


def test_model_not_ready_error_maps_to_exit_four(tmp_path, capsys, monkeypatch):
    model_error_type = type("ModelNotReadyError", (RuntimeError,), {})
    monkeypatch.setattr(
        cli.AudioAssistService,
        "list_sessions",
        lambda _self: (_ for _ in ()).throw(model_error_type("model missing")),
    )

    exit_code = cli.main(["--json", "--output-dir", str(tmp_path), "sessions", "list"])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == cli.EXIT_NOT_READY
    assert payload["error"]["code"] == "model_not_ready"


def test_configured_storage_directory_is_used(tmp_path, capsys):
    output_dir = tmp_path / "configured-output"
    output_dir.mkdir()
    _write_transcript(output_dir, "configured-job")
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps({"storage": {"output_dir": str(output_dir)}}), encoding="utf-8"
    )

    exit_code = cli.main(["--json", "--config", str(config), "sessions", "list"])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == cli.EXIT_OK
    assert payload["data"][0]["job_id"] == "configured-job"


def test_output_override_wins_over_config(tmp_path, capsys):
    configured = tmp_path / "configured"
    override = tmp_path / "override"
    configured.mkdir()
    override.mkdir()
    _write_transcript(configured, "configured-job")
    _write_transcript(override, "override-job")
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps({"storage": {"output_dir": str(configured)}}), encoding="utf-8"
    )

    cli.main([
        "--json", "--config", str(config), "--output-dir", str(override),
        "sessions", "list",
    ])

    payload = json.loads(capsys.readouterr().out)
    assert [item["job_id"] for item in payload["data"]] == ["override-job"]


def test_sessions_show_success_shape(tmp_path, capsys):
    _write_transcript(tmp_path)

    exit_code = cli.main([
        "--json", "--output-dir", str(tmp_path), "sessions", "show", "job-1"
    ])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == cli.EXIT_OK
    assert set(payload["data"]) == {"session", "state", "transcript", "summary_versions"}
    assert payload["data"]["state"]["status"] == "completed"


def test_transcribe_forwards_all_options_and_env_token(
    tmp_path, capsys, monkeypatch
):
    audio = tmp_path / "audio with spaces.m4a"
    audio.write_bytes(b"audio")
    captured = {}

    def fake_transcribe(_self, path, **kwargs):
        captured.update({"path": path, **kwargs})
        return {
            "job_id": kwargs["job_id"],
            "json_path": "/tmp/transcript.json",
            "markdown_path": "/tmp/transcript.md",
            "audio_path": path,
            "audio_copy": path,
            "engine": kwargs["engine"],
            "model_id": kwargs["model_id"],
        }

    monkeypatch.setenv("HF_TOKEN", "test-token")
    monkeypatch.setattr(cli.AudioAssistService, "transcribe", fake_transcribe)

    exit_code = cli.main([
        "--json", "--output-dir", str(tmp_path), "transcribe", str(audio),
        "--engine", "whisper", "--model-id", "whisper-medium",
        "--num-speakers", "3", "--job-id", "chosen-job", "--quiet",
    ])

    assert exit_code == cli.EXIT_OK
    assert json.loads(capsys.readouterr().out)["data"]["job_id"] == "chosen-job"
    assert captured["path"] == str(audio)
    assert captured["engine"] == "whisper"
    assert captured["model_id"] == "whisper-medium"
    assert captured["num_speakers"] == 3
    assert captured["hf_token"] == "test-token"


def test_quiet_suppresses_cli_progress(tmp_path, capsys, monkeypatch):
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"audio")

    def fake_transcribe(_self, path, **kwargs):
        kwargs["progress_callback"](0.5, "hidden progress")
        return {
            "job_id": "job",
            "json_path": "/tmp/transcript.json",
            "markdown_path": "/tmp/transcript.md",
            "audio_path": path,
            "audio_copy": path,
            "engine": "qwen",
            "model_id": None,
        }

    monkeypatch.setattr(cli.AudioAssistService, "transcribe", fake_transcribe)
    cli.main([
        "--json", "--output-dir", str(tmp_path), "transcribe", str(audio), "--quiet"
    ])

    captured = capsys.readouterr()
    assert "hidden progress" not in captured.err
    assert json.loads(captured.out)["ok"] is True


def test_module_entrypoint_subprocess_json(tmp_path):
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "src.cli",
            "--json",
            "--output-dir",
            str(tmp_path),
            "sessions",
            "list",
        ],
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == cli.EXIT_OK
    assert json.loads(completed.stdout) == {"ok": True, "data": [], "error": None}
    assert completed.stderr == ""


def test_module_entrypoint_subprocess_usage_error_is_json():
    completed = subprocess.run(
        [sys.executable, "-m", "src.cli", "--json", "unknown"],
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == cli.EXIT_USAGE
    assert json.loads(completed.stdout)["error"]["code"] == "invalid_arguments"


def test_console_entrypoint_metadata_targets_cli_main():
    import tomllib

    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    with pyproject.open("rb") as file:
        metadata = tomllib.load(file)
    assert metadata["project"]["scripts"]["audioassist"] == "src.cli:main"
