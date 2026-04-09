# Windows Packaging

This directory contains the first stage of the Windows release pipeline:

- a `PyInstaller` onedir spec
- a PowerShell build script
- runtime dependency manifests for production builds

## Scope

This packaging prep intentionally does **not** bundle model weights.

Release artifacts are expected to:

- bundle Python runtime dependencies
- bundle `ffmpeg.exe` and `ffprobe.exe`
- ship the local `ui/` assets
- let end users download ASR / diarization models from the app's setup flow

That keeps GitHub release artifacts smaller and avoids baking machine-specific
model caches into distributable builds.

## Current output

Running the build script produces a PyInstaller `onedir` bundle suitable for
local installer work. It does **not** create the final installer yet.

The planned next step is an installer layer, e.g. `Inno Setup`, to handle:

- app install / uninstall
- shortcuts
- WebView2 runtime bootstrap
- signed release artifacts

## Build prerequisites

- Python 3.13 on Windows
- `ffmpeg.exe` and `ffprobe.exe` available in one directory
- Git checkout of the repository

## Dry-run example

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build_windows.ps1 `
  -DryRun `
  -FfmpegBinDir C:\ffmpeg\bin
```

## Actual build example

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build_windows.ps1 `
  -FfmpegBinDir C:\ffmpeg\bin `
  -TorchVariant cpu
```
