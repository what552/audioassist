"""
Runtime environment detection and CUDA torch installation helpers.
"""
from __future__ import annotations

from functools import lru_cache
import json
import platform
import subprocess
import sys


_TORCH_CUDA_CHANNEL = "cu128"
_TORCH_VERSION = "2.10.0"
_TORCHAUDIO_VERSION = "2.10.0"
_TORCH_INDEX_URL = f"https://download.pytorch.org/whl/{_TORCH_CUDA_CHANNEL}"


def _run_nvidia_smi() -> tuple[bool, str | None, str | None]:
    """
    Return (has_nvidia_gpu, gpu_name, driver_version).

    The command is intentionally small and machine-readable so it can be called
    during app startup without depending on locale-specific output.
    """
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,driver_version",
                "--format=csv,noheader",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception:
        return False, None, None

    if result.returncode != 0 or not result.stdout.strip():
        return False, None, None

    first_line = result.stdout.strip().splitlines()[0]
    parts = [part.strip() for part in first_line.split(",", maxsplit=1)]
    gpu_name = parts[0] if parts else None
    driver_version = parts[1] if len(parts) > 1 else None
    return True, gpu_name, driver_version


def _probe_torch_runtime() -> dict:
    """Inspect torch in a subprocess so the current app process does not lock it."""
    script = (
        "import json\n"
        "try:\n"
        " import torch\n"
        " data = {\n"
        "  'torch_version': str(torch.__version__),\n"
        "  'torch_cuda_build': torch.version.cuda,\n"
        "  'torch_cuda_available': bool(torch.cuda.is_available()),\n"
        "  'device_name': torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'CPU',\n"
        "  'torch_error': None,\n"
        " }\n"
        "except Exception as exc:\n"
        " data = {\n"
        "  'torch_version': None,\n"
        "  'torch_cuda_build': None,\n"
        "  'torch_cuda_available': False,\n"
        "  'device_name': 'CPU',\n"
        "  'torch_error': str(exc),\n"
        " }\n"
        "print(json.dumps(data))\n"
    )

    try:
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            timeout=20,
        )
    except Exception as exc:
        return {
            "torch_version": None,
            "torch_cuda_build": None,
            "torch_cuda_available": False,
            "device_name": "CPU",
            "torch_error": str(exc),
        }

    if result.returncode != 0:
        error = result.stderr.strip() or result.stdout.strip() or "torch probe failed"
        return {
            "torch_version": None,
            "torch_cuda_build": None,
            "torch_cuda_available": False,
            "device_name": "CPU",
            "torch_error": error,
        }

    try:
        return json.loads(result.stdout.strip())
    except json.JSONDecodeError:
        return {
            "torch_version": None,
            "torch_cuda_build": None,
            "torch_cuda_available": False,
            "device_name": "CPU",
            "torch_error": result.stdout.strip() or "torch probe returned invalid JSON",
        }


def get_cuda_torch_install_plan() -> dict:
    return {
        "channel": _TORCH_CUDA_CHANNEL,
        "torch_version": _TORCH_VERSION,
        "torchaudio_version": _TORCHAUDIO_VERSION,
        "index_url": _TORCH_INDEX_URL,
        "command": (
            f'"{sys.executable}" -m pip install --upgrade --force-reinstall '
            f"torch=={_TORCH_VERSION} torchaudio=={_TORCHAUDIO_VERSION} "
            f"--index-url {_TORCH_INDEX_URL}"
        ),
    }


@lru_cache(maxsize=1)
def _detect_runtime_status() -> dict:
    has_nvidia_gpu, gpu_name, driver_version = _run_nvidia_smi()
    torch_info = _probe_torch_runtime()
    install_plan = get_cuda_torch_install_plan()

    status = {
        "platform": platform.system(),
        "python_version": platform.python_version(),
        "has_nvidia_gpu": has_nvidia_gpu,
        "gpu_name": gpu_name,
        "gpu_driver_version": driver_version,
        "torch_version": torch_info["torch_version"],
        "torch_cuda_build": torch_info["torch_cuda_build"],
        "torch_cuda_available": torch_info["torch_cuda_available"],
        "device_name": torch_info["device_name"],
        "torch_error": torch_info["torch_error"],
        "preferred_device": "cuda" if torch_info["torch_cuda_available"] else "cpu",
        "severity": "info",
        "message": "",
        "needs_cuda_torch": False,
        "install_plan": install_plan,
    }

    if torch_info["torch_error"]:
        status["severity"] = "warning"
        status["message"] = (
            "PyTorch failed to import in this Python environment. "
            "AudioAssist will not be able to use Qwen until PyTorch is repaired."
        )
        return status

    if torch_info["torch_cuda_available"]:
        status["severity"] = "ok"
        status["message"] = (
            f"CUDA ready: {torch_info['device_name']}. "
            f"PyTorch {torch_info['torch_version']} can use GPU acceleration for Qwen."
        )
        return status

    if has_nvidia_gpu and not torch_info["torch_cuda_build"]:
        status["severity"] = "warning"
        status["needs_cuda_torch"] = True
        status["message"] = (
            f"Detected NVIDIA GPU ({gpu_name}), but this Python environment uses "
            f"CPU-only PyTorch ({torch_info['torch_version']}). Qwen will run on CPU "
            "until a CUDA-enabled PyTorch build is installed for this interpreter."
        )
        return status

    if has_nvidia_gpu and torch_info["torch_cuda_build"] and not torch_info["torch_cuda_available"]:
        status["severity"] = "warning"
        status["needs_cuda_torch"] = True
        status["message"] = (
            f"Detected NVIDIA GPU ({gpu_name}) and a CUDA PyTorch build "
            f"({torch_info['torch_version']}, CUDA {torch_info['torch_cuda_build']}), "
            "but torch.cuda.is_available() is false. Check the NVIDIA driver/runtime "
            "for this Python environment."
        )
        return status

    status["message"] = (
        "No NVIDIA CUDA device detected. AudioAssist will use CPU for Qwen on this machine."
    )
    return status


def get_runtime_status(refresh: bool = False) -> dict:
    if refresh:
        _detect_runtime_status.cache_clear()
    return dict(_detect_runtime_status())


def install_cuda_torch() -> dict:
    """Install an official CUDA-enabled torch/torchaudio pair for this interpreter."""
    plan = get_cuda_torch_install_plan()
    command = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--upgrade",
        "--force-reinstall",
        f"torch=={plan['torch_version']}",
        f"torchaudio=={plan['torchaudio_version']}",
        "--index-url",
        plan["index_url"],
    ]
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=60 * 30,
    )
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        stdout = (result.stdout or "").strip()
        message = stderr or stdout or "pip install failed"
        raise RuntimeError(message)

    return get_runtime_status(refresh=True)
