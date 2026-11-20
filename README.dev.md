# AudioAssist

Local audio/video transcription with speaker diarization, powered by Qwen3-ASR or Whisper.

## Features (through R03-b local HTTP/MCP)

- **Session-per-directory storage (F6)** — each job lives in `meetings/{job_id}/` inside the output directory (`transcript.json`, `summary.json`, `agent_chat.json`, `source_audio.*`); legacy flat-file sessions are read transparently for backward compatibility; session delete uses `shutil.rmtree` for new layout
- **Local-only model inference (F1)** — `pipeline.run()` and `run_realtime_segments()` never auto-download models; if a required model is not present, `ModelNotReadyError` is raised immediately with a clear message pointing to the Model Library; no silent network calls during transcription
- **Checkpoint / resume (F2)** — long file transcriptions write a `transcription_task.json` checkpoint per chunk; if the app is closed mid-job, history shows the session as "⚠ interrupted"; the ▶ button in the history sidebar resumes from the last completed chunk instead of restarting from scratch; `discard_checkpoint()` API available to abandon a partial job
- **Batch import + sequential queue (F3)** — the Upload button and drag-and-drop both accept multiple files at once; files are queued internally and processed one at a time; the next file starts automatically when the current one finishes or errors
- **One-click re-transcribe (F4)** — the 🔄 button in the history sidebar re-transcribes any completed or errored file session from scratch with the currently selected engine; interrupted sessions show a ▶ Resume button instead (see F2)
- **Recording interrupt confirmation (F5)** — clicking Start Recording while a file transcription is running shows a confirmation dialog ("Stop and Start Recording?"); confirming cancels the transcription (session kept as interrupted), clears the queue, and starts the recording; declining leaves the transcription running untouched
- **3-column layout** — left history sidebar, center transcript + player, collapsible right summary panel; all three column dividers are draggable for custom widths
- **Session state machine** — all UI is driven by a single `_render()` from the selected session's `type + status`; file and realtime sessions coexist safely in the same history list
- **History sidebar** — lists all sessions (active first, then newest-first); click any entry to switch the center panel; live sessions show a 🔴 indicator
- **Engine selector** — dynamically lists all downloaded ASR model variants; each option is a specific model (e.g. `Qwen3-ASR 1.7B`, `Whisper Large v3 Turbo`); refreshes after download or delete
- **Upload File button** — native file picker for audio/video files (sidebar footer); blocked while a recording is active
- **Start Recording button** — launch live microphone transcription from the sidebar footer; blocked while a file transcription is in progress
- **Drag-and-drop** — drop a file onto the center panel to start transcription; blocked while a recording is active
- **Transcription progress** — live progress bar + status message while pipeline runs
- **Transcription cancel** — Cancel button in the progress panel aborts an in-flight transcription and returns the UI to idle
- **Transcription retry** — if a transcription fails the error panel shows a Retry button that re-launches the same file with one click
- **Transcript list** — speaker-labelled blocks with timestamps; long single-speaker stretches are automatically split on pauses / excessive duration to improve readability and seeking; terminal punctuation is added only at transcript ends or pause-backed boundaries, while existing punctuation and uncertain speaker hand-offs are preserved; click any row to seek the player
- **Inline editing** — double-click a row's text to edit in-place (Enter/Blur saves, Escape cancels); unsaved rows highlighted in orange
- **Speaker rename** — click any speaker label to open a rename menu; rename all segments for that speaker in one step (bulk) or just the individual segment (single); changes are reflected immediately in the transcript list
- **Save button** — flush all edits back to the JSON transcript; `.md` sidecar regenerated automatically
- **Export transcript** — "Export ▾" button in the transcript header exports the current transcript as `.txt` (plain text with timestamps and speakers) or `.md` (Markdown with speaker headings) via a native Save dialog
- **Audio player** — HTML5 playback panel; playhead position synced to transcript highlight in real time; spacebar toggles play/pause when the player is visible (spacebar never activates toolbar buttons such as Start Recording)
- **Output files** — per-job `.json` (full word-level data) + `.md` (human-readable) saved to the platform data directory
- **Summary panel** — collapsible right panel; LLM-powered streaming summarization with Markdown rendering; up to 3 versions saved per job with a version switcher; interactive Summary Agent chat for multi-turn editing and Q&A (see [Summary panel](#summary-panel))
- **Export summary** — "Export ▾" button in the summary panel exports the current summary as `.txt` or `.md` via a native Save dialog
- **Markdown rendering** — summary text and Summary Agent replies are rendered as Markdown (bold, headings, lists, code blocks) using `marked.js` + `DOMPurify`; raw `**` symbols are never shown to the user
- **Obsidian vault sync** — configure a target vault folder in Settings; every time a transcript is saved or a summary is generated the corresponding session is automatically written as `YYYY-MM-DD display_name.md` with YAML frontmatter (date, duration, speakers, source, job_id) plus summary and transcript sections; session renames propagate to the vault filename; on first launch all existing sessions are back-filled (see Settings → Obsidian Sync)
- **System and mixed audio capture (r02-b6)** — `capture_mode` supports `mic`, `system`, and `mix`; `AudioAssistCaptureHelper` uses Swift + ScreenCaptureKit for macOS system audio, mixes microphone input when requested, and streams 16 kHz / mono / float32 PCM to the existing VAD + ASR pipeline; subsequent c03-c07 work added manual high-accuracy refinement, DMG packaging, bundled runtime dependencies, and a torchcodec-free diarization path
- **Realtime transcription** — live microphone transcription with Silero VAD; pause/resume mid-session; full session `.wav` auto-saved; on Finish the pipeline runs speaker diarization only (ASR already done live) to produce the final transcript (see [Realtime transcription](#realtime-transcription))
- **Realtime timestamps** — each live utterance records absolute `start`/`end` times (seconds from session start); displayed as `[MM:SS]` prefix in the live panel and passed to the diarizer for accurate speaker labelling
- **Finish → diarize only** — when a realtime session ends, the already-transcribed segments are used for instant diarize-only output; the draft transcript is shown immediately
- **Manual high-accuracy refine** — a "高精度转写" button appears in the transcript header for realtime quick-draft sessions; clicking it triggers the full ASR pipeline (Phase 2) on demand and reports its current phase/percentage inline; eligibility and the non-secret model/backend options are persisted under `meta.json.high_accuracy_refine`, so the action survives an app restart; failure or cancellation keeps the draft retryable, while success consumes the action and replaces the transcript. Pre-metadata quick drafts are recognized conservatively through the original realtime session's `transcribed_job_id` link, the absence of a completed Qwen backend marker, and empty word-level alignment; because their original engine was not stored, refinement uses the ASR model currently selected in the UI.
- **Short recording guard** — if a recording is stopped with < 5 seconds of audio, a confirmation dialog asks whether to keep or discard the session; discarding deletes the WAV file and removes the entry from history
- **Screen sleep prevention** — while a realtime recording is active, `caffeinate` (macOS) is held to prevent the display and system from sleeping; released automatically when recording stops or finishes
- **Summary panel reset on new recording** — starting a new realtime recording resets the right-hand summary panel so stale content from the previous session is never shown
- **Session rename** — hover over any history item and click ✏ to rename inline (Enter to save, Esc to cancel)
- **Session delete** — hover and click 🗑 to delete; removes transcript JSON and summary file after confirmation
- **Model library modal** — toolbar "Models" button opens a modal listing all available models (ASR + Diarizer) with download status, Download button with animated indeterminate progress bar, and Delete button to free disk space; badge reflects actual post-delete state (remains "✓ Downloaded" if HF cache still intact); shows "⚠ Incomplete" when a partial download is detected
- **Settings modal** — toolbar ⚙ button opens a modal for API config (base URL, key, model), template management, and Obsidian vault sync configuration
- **Summary toggle** — toolbar "Summary" button shows/hides the summary panel
- **First-run setup panel** — on launch the app checks whether the ASR and diarizer models are present; if either is missing a guided setup panel is shown with individual Download buttons and progress bars; the main UI becomes accessible once both models are ready (see [First-run model setup](#first-run-model-setup))
- **Agent-callable local CLI (R03-a)** — session listing/detail/status, synchronous file transcription, live recording draft reads, and summary generation are available through `python -m src.cli` or the installed `audioassist` command; `--json` provides a stable response envelope while progress stays on stderr
- **Bounded local HTTP/MCP (R03-b)** — the visible app owns an authenticated loopback recording API; `audioassist serve --read-only` provides foreground session reads while the app is closed; `audioassist-mcp` exposes nine stdio tools without adding a daemon or background inference queue
- **Local realtime inference by default** — microphone, system, and mix capture resolve the selected Qwen or Whisper model through `ModelManager` and pass its validated local directory to the backend. Recordings never download model weights. The optional Token Plan recording mode sends voice batches to its documented Beijing endpoint and saves the WAV and live draft on this Mac.

## Development status

- R01 and R02 are integrated into `main`; the current application is a functional MVP.
- R03-a local CLI and R03-b bounded HTTP/MCP adapters are complete; see [docs/http-mcp-contract.md](docs/http-mcp-contract.md).
- Before the next feature milestone, keep the unit suite green and run the manual real-audio smoke checklist against the packaged app.
- Maintainer references: [development status](docs/development-status.md), [development backlog](docs/development-backlog.md), [local HTTP/MCP contract](docs/http-mcp-contract.md), [system audio architecture](docs/system-audio-architecture.md), and [release checklist](docs/release-checklist.md).

## Requirements

- Python 3.12.2 (see `.python-version`)
- ffmpeg + ffprobe installed and in PATH
- **System audio capture (`system` / `mix` mode) requires macOS 13.0+** and the compiled `AudioAssistCaptureHelper` binary (see [Build the Swift helper](#build-the-swift-helper) below)

### Install ffmpeg

| Platform | Command |
|----------|---------|
| macOS    | `brew install ffmpeg` |
| Linux    | `sudo apt install ffmpeg` |
| Windows  | Download from https://ffmpeg.org/download.html and add to PATH |

## Setup

```bash
# Install runtime, platform ASR, test, and build dependencies into .venv.
bash scripts/bootstrap.sh
```

The bootstrap command uses the committed `uv.lock`, selects `mlx-whisper` on
Apple Silicon and `faster-whisper` elsewhere, and installs Playwright Chromium.
See [Development Environment](docs/development-environment.md) for dependency
policy, canonical commands, and global-command cleanup.

## Build the Swift helper

The `system` and `mix` capture modes require the native helper to be compiled first.
This is **macOS-only** and requires macOS 13.0+.

### 1. Install Xcode Command Line Tools

```bash
xcode-select --install
```

Verify the installation:

```bash
swift --version   # should print Swift 5.9 or later
```

> If you have Xcode installed but `swift` is still not found, run:
> `sudo xcode-select -s /Applications/Xcode.app/Contents/Developer`

### 2. Build the helper

```bash
cd native/AudioAssistCaptureHelper
swift build -c release
```

The compiled binary is placed at:

```
native/AudioAssistCaptureHelper/.build/release/AudioAssistCaptureHelper
```

`NativeCaptureHelper` in `src/native_capture.py` resolves this path automatically.
No additional installation step is needed — just build once before using `system` or `mix` mode.

### 3. Grant capture permissions

The required macOS permission depends on the selected recording mode: Mic needs
**Microphone**, System needs **Screen & System Audio Recording**, and Mix needs
both. AudioAssist requests each permission separately on first use. If a prompt
does not appear, open the matching pane under:

```
System Settings → Privacy & Security
```

Some macOS versions require a full app restart after granting Screen & System Audio
Recording permission.

## Run

```bash
.venv/bin/python run.py
# Debug mode (opens DevTools):
.venv/bin/python run.py --debug
```

### Local CLI

```bash
.venv/bin/audioassist --json sessions list
.venv/bin/audioassist --json sessions show JOB_ID
.venv/bin/audioassist --json status JOB_ID
.venv/bin/audioassist --json recording status
.venv/bin/audioassist --json recording transcript --active --after-seq 0
.venv/bin/audioassist --json recording transcript SESSION_ID --after-seq 100
.venv/bin/audioassist --json transcribe /path/to/audio.m4a
.venv/bin/audioassist --json transcribe /path/to/audio.m4a --qwen-backend mlx
.venv/bin/audioassist --json summarize JOB_ID
.venv/bin/audioassist --json serve --read-only
.venv/bin/audioassist-mcp
```

See [the CLI contract](docs/cli-contract.md) and [local HTTP/MCP contract](docs/http-mcp-contract.md)
for installation, stdout, lifecycle, security, error, and exit-code guarantees.

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
| `local-api.json` | Active local server discovery and bearer token; mode `0600`, removed on clean shutdown |
| `local-api.lock` | Advisory single-instance lock held only while a local server is active |
| `output/meetings/<job_id>/transcript.json` | Full transcript with word-level timing and speaker labels (new layout) |
| `output/meetings/<job_id>/transcript.md` | Human-readable transcript sidecar (regenerated on each save) |
| `output/meetings/<job_id>/summary.json` | Saved summary versions (up to 3) |
| `output/meetings/<job_id>/agent_chat.json` | Summary Agent conversation history |
| `output/meetings/<job_id>/source_audio.*` | Copy of the original audio file |
| `output/meetings/<job_id>/realtime_recording.wav` | Full WAV from a realtime session |
| `output/meetings/<job_id>/realtime_draft.jsonl` | Append-only provisional utterances readable during Mic/System/Mix capture and in history when the recording has no committed transcript |
| `output/meetings/<job_id>/transcription_task.json` | Checkpoint file (present while transcription is running or interrupted) |
| `models/` | Downloaded ASR and diarization model weights |

Finishing a recording keeps its session ID through post-processing. The desktop
reads the saved draft while the job runs and after a cancellation or failure
when no transcript has been committed. Older sessions can still use separate
recording and transcript directories.

### ASR engine

- **Qwen3-ASR** (`engine="qwen"`): best accuracy for Chinese + 30 languages; the official PyTorch adapter selects CUDA when available and CPU otherwise. Apple Silicon supports explicit `AUDIOASSIST_QWEN_BACKEND=mlx` and diagnostic `pytorch-mps` overrides; the QWEN-03 evidence hold keeps automatic selection unchanged (see [benchmark record](docs/qwen-benchmark-results.md)).
- **Whisper** (`engine="whisper"`): Apple Silicon — uses `mlx-whisper` on the Apple GPU through Metal; other platforms — uses `faster-whisper` (CPU/CUDA).

Settings → **Qwen 推理后端 → 默认后端** persists `qwen_backend` in the existing
App Data `config.json`. Choose once: CPU, experimental MPS/MLX, Auto (CPU on
Mac), or CUDA where available. The preference applies to subsequent Qwen file
transcription, re-transcription, refinement and Mic/System/Mix sessions; it does
not affect Whisper or mutate an already submitted job. Resuming a checkpoint
retains its recorded backend. The shared CLI service reads the same preference
from its selected config file and supports a per-job `--qwen-backend` override.
Precedence: checkpoint backend, explicit CLI/submitted-job snapshot,
`AUDIOASSIST_QWEN_BACKEND`, saved preference, then Auto.
The UI shows environment overrides and disables unavailable choices. Saving a
preference never loads/downloads a model or requests capture permission. A failed
backend reports an error; there is no automatic fallback. This personal default
does not promote MPS/MLX to the product's out-of-box automatic policy.

Qwen adapters share local float32 audio decoding and lossless low-energy chunks
of at most 30 seconds (`src/qwen_audio.py`). Inference is serial, with a 1024-token
budget per chunk. The explicit MPS path uses float32 for both ASR and alignment;
MLX remains float16. The pinned qwen-asr 0.0.6 adapter restores the missing
non-FA2 audio window mask through removable, instance-local hooks. A dependency
upgrade requires reviewing this shim; package builds preserve qwen-asr metadata.

Generation limits, non-EOS stops, and invalid chunk timestamps fail the job
instead of returning a successful partial transcript. Diagnostic per-chunk
metadata is included in Qwen benchmark reports without transcript content;
`generated_tokens` excludes the EOS sentinel on both runtimes. Large trailing
gaps remain a *possible* truncation signal, not proof of an error or a reason
to clamp timestamps. Successful Qwen CLI/session output records the resolved
`qwen_backend` used by the transcription pipeline.

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

Click **🎙 Start Recording** in the history sidebar footer to start live transcription.

### Capture modes

Three recording modes are available via the segmented control above the **Start Recording** button:

| Mode | Icon | Audio source | When to use |
|------|------|-------------|-------------|
| **Mic** | 🎙 | Microphone only | Default. Works on all macOS versions. Use for in-person meetings, dictation, or any scenario where only your voice needs to be captured. |
| **System** | 🔊 | Mac's speaker output (system audio) | Online calls (Zoom, Meet, Teams), media playback, or any app-routed audio — without needing a physical speaker in the room. Requires macOS 13.0+ and Screen & System Audio Recording permission; it does not require Microphone permission. |
| **Mix** | ⊕ | Microphone + system audio (mixed) | Online calls where you want to capture both your own voice and the remote participant. Requires both Microphone and Screen & System Audio Recording permissions. |

#### macOS 13.0+ requirement

System and Mix modes use **ScreenCaptureKit** (`SCStreamOutputType.audio`), which requires macOS 13.0 Ventura or later. Selecting System or Mix on an older OS shows an inline notice and blocks the session from starting.

#### Capture permission matrix

AudioAssist checks and requests only the permissions required by the selected mode:

| Mode | Microphone | Screen & System Audio Recording |
|---|---:|---:|
| Mic | Required | Not required |
| System | Not required | Required |
| Mix | Required | Required |

The inline notice lists each missing permission separately and provides a dedicated
settings button for each. Permission requests happen before model loading, Session/WAV
creation, or starting PortAudio/ScreenCaptureKit.

To grant Screen & System Audio Recording access:

1. Open **System Settings → Privacy & Security → Screen & System Audio Recording**.
2. Enable the toggle next to **AudioAssist** (or the terminal / IDE you use to run it).
3. Relaunch AudioAssist if prompted.

Alternatively, use the separate **Open Microphone Settings** or
**Open Screen & System Audio Settings** action shown by the in-app notice.

#### Microphone permission and local ad-hoc builds

Mic mode checks the macOS microphone authorization state before loading the realtime
model. A denied or restricted state shows a direct link to the Microphone privacy pane;
a first request is made through AVFoundation before model loading or Session creation.
While macOS presents its prompt, **Cancel Permission Wait** keeps the app usable; the
request also times out after 60 seconds without starting PortAudio or leaving a WAV.
Locally rebuilt ad-hoc candidates have a different CDHash after their executable
changes, so macOS can require authorization again even when an older AudioAssist entry
is enabled. Developer ID signed release upgrades use a stable designated requirement.

#### Microphone degradation (Mix mode)

After both permissions pass preflight, a runtime microphone device/converter failure in
Mix mode is non-fatal: AudioAssist falls back to system-audio-only capture and shows an
inline notice without stopping the session.

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

`sounddevice`, `silero-vad`, and `numpy` are declared in `pyproject.toml` and installed automatically by `scripts/bootstrap.sh`. No separate pip command is needed.

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
4. Use the **Summary Agent** chat area at the bottom of the panel to interactively edit the summary or ask questions about the meeting (see [Summary Agent](#summary-agent)).
5. Click **⚙** in the toolbar to open the Settings modal and configure:
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

## Summary Agent

The **Summary Agent** is an interactive LLM assistant embedded in the summary panel. It gives you a multi-turn chat interface to edit summaries and ask questions about the meeting — without leaving the app.

### How it works

The agent uses the same OpenAI-compatible API configured in Settings. It has access to four tools:

| Tool | What it does |
|------|-------------|
| `get_transcript` | Reads the meeting transcript (truncated to 6 000 chars) |
| `get_current_summary` | Reads the latest saved summary version |
| `get_summary_versions` | Lists all saved versions with previews |
| `update_summary` | Saves a new summary version and immediately updates the panel |

The agent runs a tool-calling loop (up to 5 iterations): it calls tools as needed, then streams the final answer. If the provider does not support `tool_choice` (e.g. some local models), it falls back automatically to a no-tool one-shot mode where the transcript and summary are injected directly into the system prompt.

### Usage

1. After opening the summary panel for a job, type in the chat input at the bottom.
2. Press **Send** or **Enter** (Shift+Enter for a new line).
3. The agent may call tools (shown as `⚙ tool_name…` while running), then stream a reply.
4. If the agent rewrites the summary, `update_summary` is called automatically — the summary output area and version switcher update immediately.
5. Conversation history is saved to `{job_id}_agent_chat.json` (last 20 turns retained) and reloaded when you re-open the same job.
6. Click **Clear** to delete the session history.

### Provider compatibility

- **Tool calling supported** (OpenAI, DeepSeek, Qwen, etc.): full tool loop with per-tool status indicator.
- **Tool calling not supported** (some local models): automatic fallback to one-shot mode; the agent still answers correctly but cannot call `update_summary`.

## Known fixes

### Open File dialog broken with `dialog_type=0` (pywebview ≥ 5.0)

**Symptom:** Clicking **Open File** or **choose a file** threw a JavaScript-swallowed exception; `_startTranscription` was never reached.

**Root cause:** `create_file_dialog` requires `dialog_type=webview.OPEN_DIALOG` (the symbolic constant). Passing the raw integer `0` is not a valid value in pywebview ≥ 5.0 and raises an internal error. Additionally, the `file_types` description string must not contain a forward slash — `"Audio/Video (*.mp3;…)"` caused a parse failure on some platforms; the description is now `"Audio Video (*.mp3;…)"`.

**Fix (r01-c05, `32df34c`):**
- `dialog_type=0` → `dialog_type=webview.OPEN_DIALOG` (imported lazily inside `select_file()` to keep the test environment, which has no pywebview installed, importable)
- `"Audio/Video (…)"` → `"Audio Video (…)"`

## Running tests

```bash
bash scripts/bootstrap.sh
.venv/bin/python -m pytest tests/ -v
```
