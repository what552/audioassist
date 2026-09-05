"""QWEN-01 backend selection, local construction, and PyTorch lifecycle tests."""
from __future__ import annotations

from types import SimpleNamespace
import subprocess
import sys
from unittest.mock import MagicMock, patch

import pytest

from src.asr_factory import (
    QWEN_BACKEND_ENV,
    QwenBackendConfig,
    create_qwen_engine,
    select_qwen_backend,
)


def test_importing_factory_does_not_import_qwen_runtime():
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import src.asr_factory; "
            "raise SystemExit(1 if 'qwen_asr' in sys.modules else 0)",
        ],
        check=False,
    )
    assert completed.returncode == 0


def _torch(*, cuda: bool = False, mps: bool = False):
    return SimpleNamespace(
        cuda=SimpleNamespace(is_available=lambda: cuda),
        backends=SimpleNamespace(mps=SimpleNamespace(is_available=lambda: mps)),
    )


def test_auto_preserves_cuda_then_cpu_selection():
    assert select_qwen_backend(torch_module=_torch(cuda=True)).name == "pytorch-cuda"
    assert select_qwen_backend(torch_module=_torch(cuda=False)).name == "pytorch-cpu"


def test_mps_is_explicit_diagnostic_override():
    selected = select_qwen_backend(
        "pytorch-mps",
        torch_module=_torch(mps=True),
        sys_platform="darwin",
        machine="arm64",
    )
    assert selected == QwenBackendConfig("pytorch-mps", "mps", "float16")


@pytest.mark.parametrize(
    ("sys_platform", "machine", "mps"),
    [("linux", "arm64", True), ("darwin", "x86_64", True), ("darwin", "arm64", False)],
)
def test_mps_override_rejects_unsupported_runtime(sys_platform, machine, mps):
    with pytest.raises(RuntimeError, match="requires Apple Silicon"):
        select_qwen_backend(
            "pytorch-mps",
            torch_module=_torch(mps=mps),
            sys_platform=sys_platform,
            machine=machine,
        )


def test_environment_override_is_validated(monkeypatch):
    monkeypatch.setenv(QWEN_BACKEND_ENV, "mystery")
    with pytest.raises(ValueError, match=QWEN_BACKEND_ENV):
        select_qwen_backend(torch_module=_torch())


def test_factory_requires_local_model_and_aligner_directories(tmp_path):
    model = tmp_path / "qwen"
    model.mkdir()

    with pytest.raises(ValueError, match="model_path"):
        create_qwen_engine(
            model_path=str(tmp_path / "missing"),
            aligner_path=None,
            with_timestamps=False,
        )
    with pytest.raises(ValueError, match="Forced Aligner"):
        create_qwen_engine(
            model_path=str(model),
            aligner_path=str(tmp_path / "missing-aligner"),
            with_timestamps=True,
        )


def test_factory_lazy_constructs_selected_adapter(tmp_path):
    model = tmp_path / "qwen"
    aligner = tmp_path / "aligner"
    model.mkdir()
    aligner.mkdir()
    adapter = MagicMock()

    with patch(
        "src.asr_factory.select_qwen_backend",
        return_value=QwenBackendConfig("pytorch-cpu", "cpu", "float32"),
    ), patch("src.asr.ASREngine", return_value=adapter) as adapter_class:
        result = create_qwen_engine(
            model_path=str(model),
            aligner_path=str(aligner),
            with_timestamps=True,
        )

    assert result is adapter
    adapter_class.assert_called_once_with(
        with_timestamps=True,
        model_path=str(model),
        aligner_path=str(aligner),
        backend=QwenBackendConfig("pytorch-cpu", "cpu", "float32"),
    )


def test_pytorch_adapter_configures_asr_and_aligner_independently():
    wrapper = SimpleNamespace(model=object(), processor=object(), forced_aligner=object())
    qwen_class = SimpleNamespace(from_pretrained=MagicMock(return_value=wrapper))
    fake_qwen = SimpleNamespace(Qwen3ASRModel=qwen_class)
    fake_torch = SimpleNamespace(
        float16="float16",
        float32="float32",
        set_num_threads=MagicMock(),
        cuda=SimpleNamespace(is_available=lambda: False, empty_cache=MagicMock()),
        mps=SimpleNamespace(empty_cache=MagicMock()),
    )

    with patch.dict(sys.modules, {"qwen_asr": fake_qwen, "torch": fake_torch}):
        from src.asr import ASREngine

        engine = ASREngine(
            with_timestamps=True,
            model_path="/models/qwen",
            aligner_path="/models/aligner",
            backend=QwenBackendConfig("pytorch-mps", "mps", "float16"),
        )
        engine.load()

    qwen_class.from_pretrained.assert_called_once_with(
        "/models/qwen",
        forced_aligner="/models/aligner",
        forced_aligner_kwargs={"device_map": "mps", "dtype": "float16"},
        device_map="mps",
        dtype="float16",
    )


def test_close_clears_model_references_and_mps_cache():
    model = SimpleNamespace(model=object(), processor=object(), forced_aligner=object())
    from src.asr import ASREngine

    engine = ASREngine(
        with_timestamps=False,
        model_path="/models/qwen",
        backend=QwenBackendConfig("pytorch-mps", "mps", "float16"),
    )
    engine._model = model

    with patch("torch.mps.empty_cache") as empty_cache:
        engine.close()

    assert engine._model is None
    assert model.model is None
    assert model.processor is None
    assert model.forced_aligner is None
    empty_cache.assert_called_once()


def test_pytorch_adapter_converts_qwen_timestamp_result():
    from src.asr import ASREngine

    timestamp = SimpleNamespace(text="你好", start_time=0.25, end_time=0.75)
    result = SimpleNamespace(
        text="你好",
        language="Chinese",
        time_stamps=SimpleNamespace(items=[timestamp]),
    )
    model = MagicMock()
    model.transcribe.return_value = [result]
    engine = ASREngine(
        with_timestamps=True,
        model_path="/models/qwen",
        aligner_path="/models/aligner",
    )
    engine._model = model

    converted = engine.transcribe("/audio/sample.wav")

    assert converted.text == "你好"
    assert converted.language == "Chinese"
    assert [(word.word, word.start, word.end) for word in converted.words] == [
        ("你好", 0.25, 0.75)
    ]
    model.transcribe.assert_called_once_with(
        "/audio/sample.wav", return_time_stamps=True
    )
