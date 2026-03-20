# AudioAssist

Local audio/video transcription with speaker diarization, powered by Qwen3-ASR or Whisper.

## Requirements

- Python 3.12.2 (see `.python-version`)
- ffmpeg + ffprobe installed and in PATH

### Install ffmpeg

| Platform | Command |
|----------|---------|
| macOS    | `brew install ffmpeg` |
| Linux    | `sudo apt install ffmpeg` |
| Windows  | Download from https://ffmpeg.org/download.html and add to PATH |

## Setup

```bash
# 1. Create virtualenv
python -m venv .venv
source .venv/bin/activate        # macOS/Linux
.venv\Scripts\activate           # Windows

# 2. Install core dependencies
pip install -r requirements.txt

# 3. Install ASR backend (choose one)
#   Apple Silicon (macOS arm64):
pip install mlx-whisper
#   Other platforms:
pip install faster-whisper
```

## Run

```bash
python run.py
# Debug mode (opens DevTools):
python run.py --debug
```

## Platform notes

### Data directory

App data (models, config, transcripts) is stored in the platform-standard location via `platformdirs`:

| Platform | Path |
|----------|------|
| macOS    | `~/Library/Application Support/TranscribeApp/` |
| Linux    | `~/.local/share/TranscribeApp/` |
| Windows  | `%APPDATA%\TranscribeApp\` |

### ASR engine

- **Qwen3-ASR** (`engine="qwen"`): best accuracy for Chinese + 30 languages; requires CPU (MPS causes SIGBUS); runs on macOS/Linux/Windows.
- **Whisper** (`engine="whisper"`): Apple Silicon — uses `mlx-whisper` (Neural Engine); other platforms — uses `faster-whisper` (CPU/CUDA).

### Speaker diarization

Requires a HuggingFace token with access to `pyannote/speaker-diarization-3.1`. Set `HF_TOKEN` environment variable or pass via `transcribe()` options.

## Running tests

```bash
pip install pytest
python -m pytest tests/ -v
```

