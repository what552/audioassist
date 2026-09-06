"""Shared Qwen decode/chunk/quality contract, without model or device access."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pytest
import soundfile as sf

from src.qwen_audio import (load_local_audio, split_audio, merged_language,
                            validate_words, chunk_diagnostic)
from src.types import WordSegment


@pytest.mark.parametrize("length", [1, 8000, 480000, 480001, 16000 * 600])
def test_chunking_is_bounded_lossless_and_sample_exact(length):
    audio = np.random.default_rng(7).normal(0, .1, length).astype(np.float32)
    parts = split_audio(audio)
    np.testing.assert_array_equal(np.concatenate([p for p, _ in parts]), audio)
    consumed = 0
    for part, offset in parts:
        assert offset == consumed / 16000
        assert 0 < len(part) <= 480000
        assert np.shares_memory(part, audio)
        consumed += len(part)


def test_quiet_audio_makes_progress_and_finds_low_energy_boundary():
    audio = np.ones(640000, dtype=np.float32)
    audio[300000:308000] = 0
    parts = split_audio(audio)
    assert len(parts) == 2
    assert parts[1][1] == 19.0
    assert sum(map(len, (p for p, _ in split_audio(np.zeros(16000 * 600))))) == 16000 * 600


@pytest.mark.parametrize("audio", [np.array([]), np.zeros((2, 2)), np.array([np.nan]), np.array([np.inf])])
def test_chunker_rejects_invalid_audio(audio):
    with pytest.raises(ValueError):
        split_audio(audio)


@pytest.mark.parametrize("seconds", [0, -1, float("nan"), float("inf")])
def test_chunker_rejects_invalid_duration(seconds):
    with pytest.raises(ValueError):
        split_audio(np.zeros(100), max_seconds=seconds)


def test_decode_mixes_channels_without_quantizing_float_wav(tmp_path):
    audio = np.array([[.123456, .234567], [-.4, .2]], dtype=np.float32)
    path = tmp_path / "sample.wav"
    sf.write(path, audio, 16000, subtype="FLOAT")
    with patch("src.qwen_audio.subprocess.run") as run:
        result = load_local_audio(str(path))
    np.testing.assert_array_equal(result, audio.mean(axis=1))
    run.assert_not_called()


def test_decode_fallback_uses_local_bundled_ffmpeg_float_output(tmp_path):
    path = tmp_path / "sample.unknown"
    path.write_bytes(b"test")
    output = np.array([.123456, -.7], dtype="<f4")
    with patch("src.qwen_audio._find_bin", return_value="/bundle/ffmpeg"), \
            patch("src.qwen_audio.subprocess.run", return_value=SimpleNamespace(returncode=0, stdout=output.tobytes())) as run:
        result = load_local_audio(str(path))
    np.testing.assert_array_equal(result, output)
    command = run.call_args.args[0]
    assert command[0] == "/bundle/ffmpeg"
    assert "f32le" in command and "-nostdin" in command


@pytest.mark.parametrize("name", ["https://example.com/audio.wav", "missing.wav"])
def test_decode_never_downloads_audio(name):
    with patch("src.qwen_audio.subprocess.run") as run, pytest.raises(ValueError, match="local"):
        load_local_audio(name)
    run.assert_not_called()


@pytest.mark.parametrize("audio", [np.zeros(0), np.array([np.nan]), np.array([np.inf])])
def test_decoder_rejects_empty_or_nonfinite_samples(tmp_path, audio):
    path = tmp_path / "sample.wav"
    sf.write(path, audio, 16000, subtype="FLOAT")
    with pytest.raises(ValueError, match="finite"):
        load_local_audio(str(path))


def test_language_merge_ignores_empty_and_unknown():
    assert merged_language(["", "unknown", "English", "Chinese", "Chinese"]) == "Chinese"
    assert merged_language(["", None, "Unknown"]) == ""


@pytest.mark.parametrize("start,end", [(-.1, .2), (.5, .4), (0, 1.1), (0, float("inf")), (float("nan"), .4)])
def test_invalid_timestamps_are_rejected_not_clamped(start, end):
    word = WordSegment("x", start, end)
    with pytest.raises(RuntimeError, match="timestamp"):
        validate_words([word], 1)
    assert word.end == end


def test_silence_does_not_trigger_suspicious_tail_but_nonempty_text_does():
    assert not chunk_diagnostic(0, 30, [], with_timestamps=True, text_present=False)["possible_truncation"]
    assert chunk_diagnostic(0, 30, [], with_timestamps=True, text_present=True)["possible_truncation"]
    assert not chunk_diagnostic(0, 30, [], with_timestamps=False, text_present=True)["possible_truncation"]


def test_packaging_preserves_shim_version_metadata():
    spec = (Path(__file__).parents[1] / "AudioAssist.spec").read_text()
    assert '*copy_metadata("qwen-asr")' in spec
