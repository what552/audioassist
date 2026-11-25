# Windows x64 build

The first Windows artifact uses the requested source version **3.0.0**. It is
built from `codex/token-plan-live-asr` at
`4c69d2f43c8e5bb77ee564fc736ddffed2c736d0` on a separate Windows branch.
`AudioAssist.spec` and the macOS DMG entrypoint remain unchanged.

## Build

Use Windows x64, Python 3.12 and the repository-owned `.venv`:

```powershell
uv sync --frozen --extra other --extra dev --extra build
.\scripts\build_windows.ps1 -FFmpegDir C:\path\to\ffmpeg\bin
```

The script requires package metadata to match `pyproject.toml`. FFmpeg and
ffprobe are embedded in the bundle; the finished program does not need Python,
uv, a checkout, or FFmpeg installed. Microsoft Edge WebView2 Runtime is required
for the GUI. Local model weights are managed separately and are not included
in the ZIP. A transcription job never downloads ASR weights automatically.

Output: `dist/AudioAssist-3.0.0-windows-x64/AudioAssist.exe`.
Keep the **whole directory**, including `_internal`, together. The companion
`AudioAssist-CLI.exe` exposes the existing CLI, for example:

```powershell
.\AudioAssist-CLI.exe --version
.\AudioAssist-CLI.exe --json sessions list
```

Windows supports local file transcription and Mic recording. System/Mix return
an explicit unsupported-mode result. Token Plan is opt-in and may read
`QWEN_TOKEN_PLAN_API_KEY`; packaging and offline checks do not read or require
that credential and do not exercise cloud transcription.

## Bundle acceptance

```powershell
.\AudioAssist-CLI.exe --bundle-check-report C:\test\bundle-check.json
.\AudioAssist-CLI.exe --bundle-check-report C:\test\offline-asr.json --audio C:\test\synthetic.wav --model-dir C:\test\faster-whisper-tiny
```

The second check requires an explicitly prepared local CTranslate2 model and
uses the real Whisper adapter in CPU/int8 mode with Hugging Face offline mode
enabled. It checks ASR rather than the optional full speaker-diarization pass.
Use synthetic/public audio only. Both checks import the bundled local inference
dependencies, check UI files and execute bundled FFmpeg/ffprobe. The JSON report
records the actual executable, version, dependency results, and any traceback.

Run regressions with `.venv\Scripts\python.exe -m pytest -q --browser-channel chrome`
when an installed Chrome supplies the Playwright browser. macOS FIFO/helper tests
are marked as platform-specific; they do not validate Windows system capture.

FFmpeg used for this build comes from the [Windows builds linked by FFmpeg](https://ffmpeg.org/download.html)
and [Gyan's build archive](https://www.gyan.dev/ffmpeg/builds/). The downloaded
ZIP was checked against the publisher's SHA-256. Its LICENSE and README are
copied into `licenses/ffmpeg` inside the bundle.
