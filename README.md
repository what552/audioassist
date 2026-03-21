# AudioAssist

Local audio/video transcription with speaker diarization, powered by Qwen3-ASR or Whisper.

## Features (v0.2 — r01-c02)

- **Engine selector** — choose Qwen3-ASR or Whisper before transcribing
- **Open File button** — native file picker for audio/video files
- **Drag-and-drop** — drop a file onto the drop zone to start transcription
- **Transcription progress** — live progress bar + status message while pipeline runs
- **Transcript list** — speaker-labelled blocks with timestamps; click any row to seek the player
- **Inline editing** — double-click a row's text to edit in-place (Enter/Blur saves, Escape cancels); unsaved rows highlighted in orange
- **Save button** — flush all edits back to the JSON transcript; `.md` sidecar regenerated automatically
- **Audio player** — HTML5 playback panel; playhead position synced to transcript highlight in real time
- **Output files** — per-job `.json` (full word-level data) + `.md` (human-readable) saved to the platform data directory

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

# 2. Install core dependencies (includes Qwen3-ASR + torch)
pip install -r requirements.txt

# 3. Install Whisper backend — only needed if you want engine="whisper"
#    Choose ONE depending on your platform:
#    Apple Silicon (macOS arm64):
pip install mlx-whisper
#    Other platforms (Linux / Windows / x86 macOS):
pip install faster-whisper
```

> **Note:** `requirements.txt` already includes `qwen-asr` and `torch`.
> The Whisper backends (`mlx-whisper` / `faster-whisper`) are optional add-ons
> and are **not** included in `requirements.txt` because the correct choice is
> platform-dependent. Skip step 3 entirely if you only use the Qwen3-ASR engine.

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

Requires a HuggingFace token with access to `pyannote/speaker-diarization-3.1`.

> **Current version:** HF token must be supplied via the `HF_TOKEN` environment
> variable. There is no UI configuration entry for the token yet — that will be
> added in c03.

```bash
export HF_TOKEN=hf_...   # macOS / Linux
set HF_TOKEN=hf_...      # Windows cmd
```

## Running tests

```bash
pip install pytest
python -m pytest tests/ -v
```
