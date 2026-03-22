# AudioAssist

Local audio/video transcription with speaker diarization, powered by Qwen3-ASR or Whisper.

## Features (v0.10 — r02-b4)

- **3-column layout** — left history sidebar, center transcript + player, collapsible right summary panel
- **Session state machine** — all UI is driven by a single `_render()` from the selected session's `type + status`; file and realtime sessions coexist safely in the same history list
- **History sidebar** — lists all sessions (active first, then newest-first); click any entry to switch the center panel; live sessions show a 🔴 indicator
- **Engine selector** — dynamically lists all downloaded ASR model variants; each option is a specific model (e.g. `Qwen3-ASR 1.7B`, `Whisper Large v3 Turbo`); refreshes after download or delete
- **Upload File button** — native file picker for audio/video files (sidebar footer); blocked while a recording is active
- **Start Recording button** — launch live microphone transcription from the sidebar footer; blocked while a file transcription is in progress
- **Drag-and-drop** — drop a file onto the center panel to start transcription; blocked while a recording is active
- **Transcription progress** — live progress bar + status message while pipeline runs
- **Transcription cancel** — Cancel button in the progress panel aborts an in-flight transcription and returns the UI to idle
- **Transcription retry** — if a transcription fails the error panel shows a Retry button that re-launches the same file with one click
- **Transcript list** — speaker-labelled blocks with timestamps; click any row to seek the player
- **Inline editing** — double-click a row's text to edit in-place (Enter/Blur saves, Escape cancels); unsaved rows highlighted in orange
- **Save button** — flush all edits back to the JSON transcript; `.md` sidecar regenerated automatically
- **Audio player** — HTML5 playback panel; playhead position synced to transcript highlight in real time
- **Output files** — per-job `.json` (full word-level data) + `.md` (human-readable) saved to the platform data directory
- **Summary panel** — collapsible right panel; LLM-powered streaming summarization; up to 3 versions saved per job with a version switcher (see [Summary panel](#summary-panel))
- **Realtime transcription** — live microphone transcription with Silero VAD; pause/resume mid-session; full session `.wav` auto-saved; on Finish the pipeline runs speaker diarization only (ASR already done live) to produce the final transcript (see [Realtime transcription](#realtime-transcription))
- **Realtime timestamps** — each live utterance records absolute `start`/`end` times (seconds from session start); displayed as `[MM:SS]` prefix in the live panel and passed to the diarizer for accurate speaker labelling
- **Finish → diarize only** — when a realtime session ends, the already-transcribed segments are retained in memory; the post-session pipeline skips ASR entirely and runs only speaker diarization, producing the final speaker-labelled transcript faster
- **Session rename** — hover over any history item and click ✏ to rename inline (Enter to save, Esc to cancel)
- **Session delete** — hover and click 🗑 to delete; removes transcript JSON and summary file after confirmation
- **Model library modal** — toolbar "Models" button opens a modal listing all available models (ASR + Diarizer) with download status, Download button with animated indeterminate progress bar, and Delete button to free disk space; badge reflects actual post-delete state (remains "✓ Downloaded" if HF cache still intact); shows "⚠ Incomplete" when a partial download is detected
- **Settings modal** — toolbar ⚙ button opens a modal for API config (base URL, key, model) and template management; no longer embedded inside the summary panel
- **Summary toggle** — toolbar "Summary" button shows/hides the summary panel
- **First-run setup panel** — on launch the app checks whether the ASR and diarizer models are present; if either is missing a guided setup panel is shown with individual Download buttons and progress bars; the main UI becomes accessible once both models are ready (see [First-run model setup](#first-run-model-setup))

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
| `output/<session_id>.wav` | Full microphone recording from a realtime session |
| `models/` | Downloaded ASR and diarization model weights |

### ASR engine

- **Qwen3-ASR** (`engine="qwen"`): best accuracy for Chinese + 30 languages; requires CPU (MPS causes SIGBUS); runs on macOS/Linux/Windows.
- **Whisper** (`engine="whisper"`): Apple Silicon — uses `mlx-whisper` (Neural Engine); other platforms — uses `faster-whisper` (CPU/CUDA).

> **First run:** On launch, the app checks whether the required ASR model is present. If not, the setup panel is shown (see [First-run model setup](#first-run-model-setup)). Once downloaded, subsequent runs use the cached weights with no network access.

### Speaker diarization

The default diarizer is **`pyannote-diarization-community-1`** — no HuggingFace token required.

> **First run:** On launch, the app checks whether the diarizer model is present. If not, the setup panel prompts you to download it before starting any transcription (see [First-run model setup](#first-run-model-setup)).

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

## First-run model setup

On first launch, AudioAssist checks whether the two required model families are present:

| Model family | Default model | Size |
|---|---|---|
| ASR | `qwen3-asr-1.7b` | ~3.5 GB |
| Speaker diarizer | `pyannote-diarization-community-1` | ~34 MB |

If either model is missing, a **setup panel** is shown in the center of the main window instead of the normal transcript view. The panel displays:

- A status badge per model (Not downloaded / Downloading… / ✓ Ready)
- A progress bar that fills as the download proceeds
- A **Download** button for each model

Click **Download** for each missing model. Downloads run in the background; you can start both simultaneously. Once both show ✓ Ready the setup panel closes automatically and the main UI becomes accessible.

If both models are already present (subsequent launches), the setup panel is skipped entirely.

## Realtime transcription

Click **🎙 Start Recording** in the history sidebar footer to start live microphone transcription.

### How it works

1. **Silero VAD** monitors the microphone stream in real time, detecting speech vs. silence at 16 kHz / 32 ms chunks.
2. Each utterance (speech segment followed by ~480 ms of silence) is extracted and passed to the selected ASR engine. The absolute `start`/`end` timestamps (seconds from session start) are recorded alongside the text.
3. Transcribed text appears in the realtime panel sentence by sentence as you speak.
4. Use the **control bar** (bottom of the center panel) to manage the session:
   - **⏸ / ▶ (Pause / Resume)** — suspend and resume microphone capture mid-session; the timer pauses accordingly; the partial WAV is kept open and continues filling on resume.
   - **▶ (Play)** — available in paused state; plays back the audio recorded so far.
   - **Finish** — stops recording, closes the WAV file, and triggers a **diarize-only** pipeline on the saved WAV (ASR is skipped — text is already captured live). The result is a speaker-labelled JSON + MD transcript.

### Notes

- The first click loads the VAD model and the ASR engine; this may take a few seconds.
- The ASR engine is the same one selected in the toolbar dropdown (Qwen3-ASR or Whisper).
- Utterances shorter than ~160 ms are discarded as noise.
- Each utterance carries `{text, start, end}` with timestamps relative to the session start (seconds). These are used directly for speaker assignment during the post-session diarization pass.
- The complete microphone recording is saved as `output/<session_id>.wav`; the path is passed to the frontend via `onRealtimeStarted(sessionId, wavPath)` and stored in `session.audioPath` so the player is wired automatically when the session finishes.
- **Concurrency rules:** Uploading a file is blocked while a recording is active; starting a new recording is blocked while a file transcription is running.
- **MLX serial execution (Apple Silicon):** Each detected utterance is placed in a `queue.Queue` and processed by a single long-running worker thread. This guarantees that `mlx-whisper` (and any other Metal/GPU backend) is never called from two threads at the same time, preventing the `A command encoder is already encoding to this command buffer` Metal assertion failure that would otherwise crash the process when several short speech segments are flushed in quick succession (e.g. during pause/resume).

### Dependencies

`sounddevice`, `silero-vad`, and `numpy` are included in `requirements.txt` and installed automatically with `pip install -r requirements.txt`. No manual installation is needed.

`sounddevice` wraps PortAudio. On most platforms PortAudio ships as a binary wheel, but on some Linux systems the system library must be present first:

| Platform | System prerequisite |
|----------|---------------------|
| macOS | `brew install portaudio` (if pip install fails with a build error) |
| Linux | `sudo apt install portaudio19-dev` (Debian/Ubuntu) |
| Windows | No additional steps required — binary wheel includes PortAudio |

## Summary panel

After transcription completes, the **Summary** panel is available on the right. Use the **Summary** button in the toolbar (top-right) to show or hide it. Click **⚙** in the toolbar to open the API config and template settings modal.

### Layout

```
┌─ toolbar ─────────────────────────────────────────────┐
│  AudioAssist   [Engine ▼]   [⚙]   [Summary]           │
└───────────────────────────────────────────────────────┘
┌─ summary panel (right column) ────────────────────────┐
│  [Template ▼]  [Generate]                             │  ← controls
│  [v1 · date]  [v2 · date]                             │  ← version switcher
│  ┌─────────────────────────────────────────────────┐  │
│  │  (streaming output / recalled version text)     │  │  ← #summary-output
│  └─────────────────────────────────────────────────┘  │
└───────────────────────────────────────────────────────┘
```

### Usage

1. **Select a template** from the drop-down list.
2. Click **Generate** — the transcript is sent to the configured LLM and the response streams in.
3. Each completed summary is saved automatically as a version (up to 3 per job). Click a version button to recall it.
4. Click **⚙** in the toolbar to open the Settings modal and configure:
   - **Base URL** — OpenAI-compatible endpoint (e.g. `https://api.openai.com/v1`, DeepSeek, Qwen, local Ollama).
   - **API Key** — authentication key for the endpoint.
   - **Model** — model identifier (e.g. `gpt-4o-mini`, `deepseek-chat`).
   - Click **Save** to persist the config to `config.json`.
5. Use **+ Add template** to create a named prompt; **Edit** / **✕** to update or delete existing ones. Templates are saved to `templates.json`.

### OpenAI-compatible endpoints

Any endpoint that follows the OpenAI Chat Completions API can be used:

| Provider | Base URL example |
|----------|-----------------|
| OpenAI | `https://api.openai.com/v1` |
| DeepSeek | `https://api.deepseek.com/v1` |
| Qwen (Alibaba Cloud) | `https://dashscope.aliyuncs.com/compatible-mode/v1` |
| Ollama (local) | `http://localhost:11434/v1` |

## Known fixes

### Open File dialog broken with `dialog_type=0` (pywebview ≥ 5.0)

**Symptom:** Clicking **Open File** or **choose a file** threw a JavaScript-swallowed exception; `_startTranscription` was never reached.

**Root cause:** `create_file_dialog` requires `dialog_type=webview.OPEN_DIALOG` (the symbolic constant). Passing the raw integer `0` is not a valid value in pywebview ≥ 5.0 and raises an internal error. Additionally, the `file_types` description string must not contain a forward slash — `"Audio/Video (*.mp3;…)"` caused a parse failure on some platforms; the description is now `"Audio Video (*.mp3;…)"`.

**Fix (r01-c05, `32df34c`):**
- `dialog_type=0` → `dialog_type=webview.OPEN_DIALOG` (imported lazily inside `select_file()` to keep the test environment, which has no pywebview installed, importable)
- `"Audio/Video (…)"` → `"Audio Video (…)"`

## Running tests

```bash
pip install pytest
python -m pytest tests/ -v
```
