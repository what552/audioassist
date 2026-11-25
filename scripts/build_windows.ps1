param([Parameter(Mandatory=$true)][string]$FFmpegDir)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
$taskPython = Join-Path $taskRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $taskPython)) { throw 'Run the locked uv sync first.' }
foreach ($taskTool in @('ffmpeg.exe','ffprobe.exe')) {
    if (-not (Test-Path -LiteralPath (Join-Path $FFmpegDir $taskTool))) { throw "Missing $taskTool in $FFmpegDir" }
}
$env:AUDIOASSIST_FFMPEG_DIR = (Resolve-Path -LiteralPath $FFmpegDir).Path
$env:PYINSTALLER_CONFIG_DIR = Join-Path $taskRoot 'build\pyinstaller-cache'
$env:MPLCONFIGDIR = Join-Path $taskRoot 'build\matplotlib-cache'
$env:GRADIO_ANALYTICS_ENABLED = 'False'
Push-Location $taskRoot
try {
    & $taskPython -c 'import importlib.metadata as m,tomllib; p=tomllib.load(open("pyproject.toml","rb"))["project"]["version"]; assert m.version("audioassist-local")==p; print("Packaging AudioAssist",p)'
    if ($LASTEXITCODE -ne 0) { throw 'Package metadata mismatch; refresh the editable install.' }
    & $taskPython -m PyInstaller --noconfirm --clean AudioAssist.windows.spec
    if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed.' }
} finally { Pop-Location }
