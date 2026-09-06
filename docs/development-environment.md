# Development Environment

AudioAssist uses one repository-owned Python environment at `.venv`. The CLI,
desktop source run, tests, and PyInstaller build must all use this interpreter.
Do not install the checkout into the global pyenv Python or reuse a virtual
environment from another checkout.

## Bootstrap

Install `uv`, then run from the repository root:

```bash
bash scripts/bootstrap.sh
```

The script selects the Apple Silicon MLX Whisper extra on an arm64 Mac and the
faster-whisper extra elsewhere. It installs runtime, test, and build packages
from `uv.lock`, installs the current checkout, and installs Playwright Chromium.
Use `--skip-browser` when browser tests are not required:

```bash
bash scripts/bootstrap.sh --skip-browser
```

On Windows PowerShell, run the equivalent locked sync explicitly:

```powershell
uv sync --frozen --extra other --extra dev --extra build
.\.venv\Scripts\python.exe -m playwright install chromium
```

Model weights are not part of `.venv`. They remain under the platform-standard
TranscribeApp data directory and are managed by the application.

## Canonical commands

```bash
.venv/bin/python run.py
.venv/bin/audioassist --json sessions list
.venv/bin/python -m pytest -q
bash scripts/build_dmg.sh
.venv/bin/python -m src.qwen_benchmark run --help
```

Activation is optional. If the environment is activated, `python` and
`audioassist` resolve to the same `.venv`:

```bash
source .venv/bin/activate
python -c 'import sys; print(sys.prefix)'
audioassist --json sessions list
```

## Dependency policy

- `pyproject.toml` is the human-edited dependency declaration.
- `uv.lock` is the exact reproducible resolution and must be committed.
- Update both with an intentional `uv lock --upgrade-package PACKAGE` change.
- Critical inference interfaces are constrained in `pyproject.toml`; do not
  upgrade Qwen, PyTorch, pyannote, or MLX Whisper incidentally.
- Do not recreate a second `requirements.txt` dependency source.

## Qwen backend diagnostics

The public engine name remains `qwen`. Automatic selection stays on the
official PyTorch CUDA/CPU paths until the QWEN-03 promotion gate. On Apple
Silicon, developers may exercise either candidate explicitly:

```bash
AUDIOASSIST_QWEN_BACKEND=pytorch-mps .venv/bin/audioassist --json transcribe /path/to/audio.wav
AUDIOASSIST_QWEN_BACKEND=mlx .venv/bin/audioassist --json transcribe /path/to/audio.wav
```

Supported diagnostic values are `auto`, `mlx`, `pytorch-cpu`, `pytorch-cuda`,
and `pytorch-mps`. `mlx` requires Apple Silicon macOS. An unavailable explicit
device is an error; a job never silently switches backend after it starts.
Both MLX and MPS remain developer overrides until benchmark evidence supports a
default change.

## Existing global command cleanup

An old editable install may leave `audioassist` under pyenv shims. Confirm the
canonical command first:

```bash
.venv/bin/audioassist --help
```

After that succeeds, the old global editable install can be removed with the
global interpreter that owns it. This cleanup is optional and is deliberately
not performed by bootstrap.
