"""Tests for src/runtime_env.py."""
from __future__ import annotations

from unittest.mock import patch

import src.runtime_env as runtime_env


class TestGetRuntimeStatus:
    def teardown_method(self):
        runtime_env._detect_runtime_status.cache_clear()

    def test_warns_when_nvidia_gpu_present_but_torch_is_cpu_only(self):
        with patch("src.runtime_env._run_nvidia_smi", return_value=(True, "RTX 4070", "591.86")), \
             patch(
                 "src.runtime_env._probe_torch_runtime",
                 return_value={
                     "torch_version": "2.11.0+cpu",
                     "torch_cuda_build": None,
                     "torch_cuda_available": False,
                     "device_name": "CPU",
                     "torch_error": None,
                 },
             ):
            status = runtime_env.get_runtime_status(refresh=True)

        assert status["has_nvidia_gpu"] is True
        assert status["needs_cuda_torch"] is True
        assert status["preferred_device"] == "cpu"
        assert status["severity"] == "warning"
        assert "CPU-only PyTorch" in status["message"]
        assert status["install_plan"]["index_url"].endswith("/cu128")

    def test_reports_cuda_ready_when_torch_can_use_gpu(self):
        with patch("src.runtime_env._run_nvidia_smi", return_value=(True, "RTX 4070", "591.86")), \
             patch(
                 "src.runtime_env._probe_torch_runtime",
                 return_value={
                     "torch_version": "2.11.0+cu128",
                     "torch_cuda_build": "12.8",
                     "torch_cuda_available": True,
                     "device_name": "RTX 4070",
                     "torch_error": None,
                 },
             ):
            status = runtime_env.get_runtime_status(refresh=True)

        assert status["torch_cuda_available"] is True
        assert status["preferred_device"] == "cuda"
        assert status["severity"] == "ok"
        assert "CUDA ready" in status["message"]

    def test_reports_cpu_mode_when_no_nvidia_gpu_detected(self):
        with patch("src.runtime_env._run_nvidia_smi", return_value=(False, None, None)), \
             patch(
                 "src.runtime_env._probe_torch_runtime",
                 return_value={
                     "torch_version": "2.11.0+cpu",
                     "torch_cuda_build": None,
                     "torch_cuda_available": False,
                     "device_name": "CPU",
                     "torch_error": None,
                 },
             ):
            status = runtime_env.get_runtime_status(refresh=True)

        assert status["has_nvidia_gpu"] is False
        assert status["needs_cuda_torch"] is False
        assert status["preferred_device"] == "cpu"
        assert status["severity"] == "info"
        assert "No NVIDIA CUDA device detected" in status["message"]

    def test_install_plan_uses_current_python_executable(self):
        plan = runtime_env.get_cuda_torch_install_plan()
        assert "python.exe" in plan["command"].lower()
        assert plan["torch_version"] == "2.10.0"
        assert plan["torchaudio_version"] == "2.10.0"
