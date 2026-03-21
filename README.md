# AudioAssist

Local audio/video transcription with speaker diarization, powered by Qwen3-ASR or Whisper.

## Features (v0.3 — r01-c04)

- **Engine selector** — choose Qwen3-ASR or Whisper before transcribing
- **Open File button** — native file picker for audio/video files
- **Drag-and-drop** — drop a file onto the drop zone to start transcription
- **Transcription progress** — live progress bar + status message while pipeline runs
- **Transcript list** — speaker-labelled blocks with timestamps; click any row to seek the player
- **Inline editing** — double-click a row's text to edit in-place (Enter/Blur saves, Escape cancels); unsaved rows highlighted in orange
- **Save button** — flush all edits back to the JSON transcript; `.md` sidecar regenerated automatically
- **Audio player** — HTML5 playback panel; playhead position synced to transcript highlight in real time
- **Output files** — per-job `.json` (full word-level data) + `.md` (human-readable) saved to the platform data directory
- **Summary panel** — LLM-powered transcript summarization with streaming output (see [Summary panel](#summary-panel))

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

> **Note:** `requirements.txt` already includes `qwen-asr`, `torch`, and
> `pyannote.audio>=4.0` (speaker diarization).
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

Files written inside the data directory:

| File | Contents |
|------|----------|
| `config.json` | API configuration — `base_url`, `api_key`, `model` for the LLM summary endpoint |
| `templates.json` | Summary templates — list of `{name, prompt}` objects managed in the ⚙ settings panel |
| `output/<job_id>.json` | Full transcript with word-level timing and speaker labels |
| `output/<job_id>.md` | Human-readable transcript sidecar (regenerated on each save) |
| `models/` | Downloaded ASR and diarization model weights |

### ASR engine

- **Qwen3-ASR** (`engine="qwen"`): best accuracy for Chinese + 30 languages; requires CPU (MPS causes SIGBUS); runs on macOS/Linux/Windows.
- **Whisper** (`engine="whisper"`): Apple Silicon — uses `mlx-whisper` (Neural Engine); other platforms — uses `faster-whisper` (CPU/CUDA).

### Speaker diarization

The default diarizer is **`pyannote-diarization-community-1`** — no HuggingFace
token required.

Two models are available:

| Model ID | Token required | Notes |
|----------|---------------|-------|
| `pyannote-diarization-community-1` | No | Default; community model, works out of the box |
| `pyannote-diarization-3.1` | Yes | Gated model; backward-compatible for existing users |

To use `pyannote-diarization-3.1`, set `HF_TOKEN` before launching:

```bash
export HF_TOKEN=hf_...   # macOS / Linux
set HF_TOKEN=hf_...      # Windows cmd
```

## Summary panel

After transcription completes, the **Summary** section appears in the right-hand player panel.

### Layout

```
┌─────────────────────────────────────────┐
│  [Template ▼]  [Summarize]  [⚙]        │  ← summary controls
│ ┌─────────────────────────────────────┐ │
│ │  (streaming output / placeholder)  │ │  ← #summary-output
│ └─────────────────────────────────────┘ │
│  ── API config (hidden by default) ──   │  ← ⚙ settings panel
└─────────────────────────────────────────┘
```

### Usage

1. **Select a template** from the drop-down list (left of the controls bar).
2. Click **Summarize** — the transcript text is sent to the configured LLM endpoint and the response streams in token by token.
3. Click **⚙** to open the settings panel and configure:
   - **Base URL** — OpenAI-compatible endpoint (e.g. `https://api.openai.com/v1`, DeepSeek, Qwen, local Ollama).
   - **API Key** — authentication key for the endpoint.
   - **Model** — model identifier (e.g. `gpt-4o-mini`, `deepseek-chat`).
   - Click **Save** to persist the config to `config.json`.
4. Use **+ Add template** to create a named prompt; **Edit** / **✕** to update or delete existing ones. Templates are saved to `templates.json`.

### OpenAI-compatible endpoints

Any endpoint that follows the OpenAI Chat Completions API can be used:

| Provider | Base URL example |
|----------|-----------------|
| OpenAI | `https://api.openai.com/v1` |
| DeepSeek | `https://api.deepseek.com/v1` |
| Qwen (Alibaba Cloud) | `https://dashscope.aliyuncs.com/compatible-mode/v1` |
| Ollama (local) | `http://localhost:11434/v1` |

## Running tests

```bash
pip install pytest
python -m pytest tests/ -v
```
