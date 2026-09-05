# System Audio Architecture

Status: implemented in R02-b6

## Decision

AudioAssist keeps Python as the application, session, ASR, and UI control layer. A separate Swift helper owns macOS system-audio capture because ScreenCaptureKit is a native platform API.

The application exposes three capture modes:

| Mode | Capture backend |
|---|---|
| `mic` | Python `sounddevice` path |
| `system` | Swift `AudioAssistCaptureHelper` using ScreenCaptureKit |
| `mix` | Swift helper using ScreenCaptureKit plus AVAudioEngine microphone capture |

## Process boundary

The Python wrapper in `src/native_capture.py` starts and controls the helper. The helper uses separate channels for data and control:

- A named pipe carries raw float32 PCM.
- Standard output carries one NDJSON control event per line.
- Standard error carries diagnostic logs and is drained continuously to prevent subprocess blocking.

The public Python interface mirrors the microphone transcriber contract: start, pause, resume, stop, and retrieve segments. This keeps the application lifecycle independent of the selected capture backend.

## Audio contract

- Stream format: 16 kHz, mono, float32, 512 samples per VAD frame.
- Archive format: 16 kHz, mono, PCM16 WAV.
- System and microphone streams are converted before mixing.
- Mix buffers advance independently in aligned 512-sample chunks; the shorter side is zero-padded only when required.
- An overflow threshold prevents unbounded buffering if one input disappears.

Passing a waveform dictionary to pyannote avoids its file-path decoder and therefore avoids torchcodec in the packaged application. Audio is loaded with `soundfile`, converted to a channels-first torch tensor, and passed with its sample rate.

## Lifecycle and failure behavior

- `SIGUSR1` pauses helper output without ending the session.
- `SIGUSR2` resumes the same session and WAV file.
- Stop drains pending PCM and ASR work before closing the FIFO and WAV.
- Screen Recording permission or a missing helper blocks session startup with an actionable message.
- A microphone failure in `mix` mode is non-fatal: the UI reports degradation and the session continues with system audio.

## Constraints

- System and mix capture require macOS 13 or later.
- Capture mode is selected before recording and cannot change mid-session.
- The helper is bundled into the macOS application by the PyInstaller/DMG build.
- Echo cancellation and Windows system-audio capture are outside this architecture and remain roadmap work.
