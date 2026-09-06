"""Shared adapter behavior: serial chunks, offsets, failures, and reuse."""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
import sys

import numpy as np
import pytest

from src.asr import ASREngine
from src.asr_qwen_mlx import MLXQwenASREngine


@pytest.fixture(params=["pytorch", "mlx"])
def adapter(request, monkeypatch):
    backend = request.param
    module = "src.asr" if backend == "pytorch" else "src.asr_qwen_mlx"
    audio = np.zeros(32000, dtype=np.float32)
    monkeypatch.setattr(module + ".load_local_audio", lambda _: audio)
    monkeypatch.setattr(module + ".split_audio", lambda _: [(audio[:16000], 0), (audio[16000:], 1)])
    engine = (ASREngine(model_path="/models/qwen", aligner_path="/models/aligner") if backend == "pytorch"
              else MLXQwenASREngine(model_path="/models/qwen", aligner_path="/models/aligner", with_timestamps=True))
    infer = MagicMock()
    if backend == "pytorch":
        engine._model = SimpleNamespace(transcribe=infer)
        engine._observer = SimpleNamespace(records=[])
    else:
        engine._session = SimpleNamespace(transcribe=infer)
    control = {"call": 0, "error_at": None, "cap_at": None, "invalid_at": None, "missing_at": None}

    def call(*args, **kwargs):
        index = control["call"]
        control["call"] += 1
        if index == control["error_at"]:
            raise RuntimeError("device failed")
        truncated = index == control["cap_at"]
        generation = {"truncated": truncated, "finish_reason": "length" if truncated else "eos",
                      "max_new_tokens": 1024, "generated_tokens": 1024 if truncated else 3}
        end = 1.5 if index == control["invalid_at"] else .8
        if backend == "pytorch":
            if index != control["missing_at"]:
                engine._observer.records.append(generation)
            return [SimpleNamespace(text=f"chunk{index}", language="Chinese",
                                    time_stamps=SimpleNamespace(items=[SimpleNamespace(text="词", start_time=.1, end_time=end)]))]
        return SimpleNamespace(text=f"chunk{index}", language="Chinese", truncated=truncated,
                               chunks=[] if index == control["missing_at"] else [generation],
                               segments=[{"text": "词", "start": .1, "end": end}])

    infer.side_effect = call
    return engine, control, infer


def test_serial_chunk_merge_offsets_and_private_diagnostics(adapter):
    engine, control, infer = adapter
    result = engine.transcribe("/audio/local.wav")
    assert result.text == "chunk0 chunk1"
    assert result.language == "Chinese"
    assert [(w.start, w.end) for w in result.words] == [(.1, .8), (1.1, 1.8)]
    assert infer.call_count == 2
    assert [(c["start"], c["end"]) for c in engine.last_chunk_diagnostics] == [(0, 1), (1, 2)]
    assert all("text" not in c and "words" not in c for c in engine.last_chunk_diagnostics)
    assert not engine.last_truncated


@pytest.mark.parametrize("kind,message", [("cap_at", "generation limit"), ("invalid_at", "timestamp"),
                                         ("error_at", "device failed"), ("missing_at", "diagnostics missing")])
def test_failed_second_chunk_returns_no_partial_success_and_can_retry(adapter, kind, message):
    engine, control, infer = adapter
    control[kind] = 1
    with pytest.raises(RuntimeError, match=message):
        engine.transcribe("/audio/local.wav")
    assert infer.call_count == 2
    if kind == "cap_at":
        assert engine.last_truncated
    control.update(call=0, **{kind: None})
    assert engine.transcribe("/audio/local.wav").text == "chunk0 chunk1"
    assert len(engine.last_chunk_diagnostics) == 2
    assert not engine.last_truncated


def test_pytorch_failed_second_mask_install_cleans_first_mask_and_models():
    model, aligner = object(), object()
    wrapper = SimpleNamespace(model=model, forced_aligner=SimpleNamespace(model=aligner), processor=object())
    handle = MagicMock()
    qwen = SimpleNamespace(Qwen3ASRModel=SimpleNamespace(from_pretrained=MagicMock(return_value=wrapper)))
    engine = ASREngine(model_path="/models/qwen", aligner_path="/models/aligner")
    with patch.dict(sys.modules, {"qwen_asr": qwen}), \
            patch("src.asr.install_audio_window_masks", side_effect=[[handle], RuntimeError("mask failed")]):
        with pytest.raises(RuntimeError, match="mask failed"):
            engine.load()
    assert engine._model is None and engine._observer is None
    assert wrapper.model is None and wrapper.forced_aligner is None
    handle.remove.assert_called_once()


def test_pytorch_close_unhooks_before_releasing_models_and_is_idempotent():
    events = []
    handle = SimpleNamespace(remove=lambda: events.append("unhook"))
    observer = SimpleNamespace(close=lambda: events.append("restore generation"))
    engine = ASREngine(model_path="/models/qwen", with_timestamps=False)
    engine._observer = observer
    engine._mask_handles = [handle]
    engine._model = SimpleNamespace(model=object(), processor=object(), forced_aligner=None)
    engine.close()
    engine.close()
    assert events == ["restore generation", "unhook"]
    assert engine._mask_handles == []


def test_pytorch_version_change_requires_shim_review_before_loading():
    engine = ASREngine(model_path="/models/qwen", with_timestamps=False)
    factory = MagicMock()
    with patch("importlib.metadata.version", return_value="0.0.7"), \
            patch.dict(sys.modules, {"qwen_asr": SimpleNamespace(Qwen3ASRModel=SimpleNamespace(from_pretrained=factory))}):
        with pytest.raises(RuntimeError, match="Review Qwen compatibility"):
            engine.load()
    factory.assert_not_called()
