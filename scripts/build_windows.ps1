param(
    [string]$PythonExe = "python",
    [string]$VenvDir = ".venv-build-win",
    [string]$FfmpegBinDir = "",
    [ValidateSet("cpu", "cuda")]
    [string]$TorchVariant = "cpu",
    [switch]$Clean,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$VenvPath = Join-Path $RepoRoot $VenvDir
$VenvPython = Join-Path $VenvPath "Scripts\python.exe"
$RequirementsPath = Join-Path $RepoRoot "requirements-windows-build.txt"
$SpecPath = Join-Path $RepoRoot "packaging\windows\audioassist.spec"
$DistPath = Join-Path $RepoRoot "dist"
$BuildPath = Join-Path $RepoRoot "build"

function Invoke-Step {
    param(
        [string]$Message,
        [scriptblock]$Action
    )

    Write-Host "==> $Message"
    if (-not $DryRun) {
        & $Action
    }
}

if (-not $DryRun -and [string]::IsNullOrWhiteSpace($FfmpegBinDir)) {
    throw "FfmpegBinDir is required. Point it to a directory containing ffmpeg.exe and ffprobe.exe."
}

if ($Clean) {
    Invoke-Step "Removing previous build outputs" {
        foreach ($Path in @($BuildPath, $DistPath, $VenvPath)) {
            if (Test-Path $Path) {
                Remove-Item -Recurse -Force -LiteralPath $Path
            }
        }
    }
}

if (-not (Test-Path $VenvPython)) {
    Invoke-Step "Creating build virtual environment at $VenvPath" {
        & $PythonExe -m venv $VenvPath
    }
}

Invoke-Step "Upgrading pip/setuptools/wheel" {
    & $VenvPython -m pip install --upgrade pip setuptools wheel
}

Invoke-Step "Installing Windows build dependencies" {
    & $VenvPython -m pip install -r $RequirementsPath
}

if ($TorchVariant -eq "cuda") {
    Invoke-Step "Upgrading torch/torchaudio to CUDA wheels (cu128)" {
        & $VenvPython -m pip install --upgrade --force-reinstall `
            torch==2.10.0 torchaudio==2.10.0 `
            --index-url https://download.pytorch.org/whl/cu128
    }
}

if (-not [string]::IsNullOrWhiteSpace($FfmpegBinDir)) {
    $ResolvedFfmpeg = if ($DryRun) { $FfmpegBinDir } else { (Resolve-Path $FfmpegBinDir).Path }
    $env:AUDIOASSIST_FFMPEG_DIR = $ResolvedFfmpeg
    Write-Host "==> Using FFmpeg binaries from $ResolvedFfmpeg"
}

Invoke-Step "Running PyInstaller" {
    & $VenvPython -m PyInstaller --noconfirm --clean $SpecPath
}

if ($DryRun) {
    Write-Host "Dry run complete. No environment changes were made."
} else {
    Write-Host "Build complete. Output: $DistPath\\AudioAssist"
}
