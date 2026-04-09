param(
    [string]$DistDir = ".\dist\AudioAssist",
    [string]$OutputDir = ".\release",
    [string]$AppVersion = "",
    [string]$Publisher = "stevenSHN",
    [string]$InnoSetupCompiler = "",
    [switch]$DownloadWebView2,
    [switch]$Clean
)

$ErrorActionPreference = "Stop"

function Find-InnoSetupCompiler {
    param(
        [string]$RepoRoot
    )

    if ($env:INNO_SETUP_COMPILER -and (Test-Path $env:INNO_SETUP_COMPILER)) {
        return $env:INNO_SETUP_COMPILER
    }

    $RegistryCandidates = @(
        "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\Inno Setup 6_is1",
        "HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\Inno Setup 6_is1",
        "HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\Inno Setup 6_is1"
    )

    foreach ($KeyPath in $RegistryCandidates) {
        try {
            $InstallLocation = (Get-ItemProperty -Path $KeyPath -ErrorAction Stop).InstallLocation
            if ($InstallLocation) {
                $Candidate = Join-Path $InstallLocation "ISCC.exe"
                if (Test-Path $Candidate) {
                    return $Candidate
                }
            }
        } catch {
        }
    }

    foreach ($Candidate in @(
        "ISCC.exe",
        (Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe"),
        (Join-Path $env:ProgramFiles "Inno Setup 6\ISCC.exe"),
        (Join-Path $RepoRoot ".tools\inno\ISCC.exe")
    )) {
        if ($Candidate -and (Test-Path $Candidate)) {
            return $Candidate
        }
    }

    throw "ISCC.exe not found. Install Inno Setup 6, put ISCC.exe on PATH, set INNO_SETUP_COMPILER, or pass -InnoSetupCompiler."
}

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$ResolvedDistDir = if ([System.IO.Path]::IsPathRooted($DistDir)) { $DistDir } else { Join-Path $RepoRoot $DistDir }
$ResolvedOutputDir = if ([System.IO.Path]::IsPathRooted($OutputDir)) { $OutputDir } else { Join-Path $RepoRoot $OutputDir }
$IssPath = Join-Path $RepoRoot "packaging\windows\AudioAssist.iss"
$BootstrapperDir = Join-Path $RepoRoot "build\installer"
$BootstrapperPath = Join-Path $BootstrapperDir "MicrosoftEdgeWebView2Setup.exe"
$CompilerPath = if ($InnoSetupCompiler) { $InnoSetupCompiler } else { Find-InnoSetupCompiler -RepoRoot $RepoRoot }

if (-not (Test-Path $ResolvedDistDir)) {
    throw "DistDir not found: $ResolvedDistDir"
}

if (-not (Test-Path (Join-Path $ResolvedDistDir "AudioAssist.exe"))) {
    throw "AudioAssist.exe not found inside $ResolvedDistDir"
}

if ($Clean) {
    foreach ($Path in @($ResolvedOutputDir, $BootstrapperDir)) {
        if (Test-Path $Path) {
            Remove-Item -Recurse -Force -LiteralPath $Path
        }
    }
}

New-Item -ItemType Directory -Force -Path $ResolvedOutputDir | Out-Null
New-Item -ItemType Directory -Force -Path $BootstrapperDir | Out-Null

if ($DownloadWebView2 -or -not (Test-Path $BootstrapperPath)) {
    Write-Host "==> Downloading WebView2 bootstrapper"
    Invoke-WebRequest `
        -Uri "https://go.microsoft.com/fwlink/p/?LinkId=2124703" `
        -OutFile $BootstrapperPath
}

if (-not (Test-Path $BootstrapperPath)) {
    throw "WebView2 bootstrapper not found: $BootstrapperPath"
}

if (-not (Test-Path $CompilerPath)) {
    throw "ISCC.exe not found: $CompilerPath"
}

if ([string]::IsNullOrWhiteSpace($AppVersion)) {
    $GitVersion = git -C $RepoRoot rev-parse --short HEAD
    $AppVersion = "0.0.0-$GitVersion"
}

Write-Host "==> Building installer with Inno Setup"
& $CompilerPath `
    "/DMyAppVersion=$AppVersion" `
    "/DMyAppPublisher=$Publisher" `
    "/DMySourceDir=$ResolvedDistDir" `
    "/DMyWebView2Bootstrapper=$BootstrapperPath" `
    "/O$ResolvedOutputDir" `
    $IssPath

Write-Host "Installer build complete. Output: $ResolvedOutputDir"
