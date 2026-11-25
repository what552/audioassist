"""Zip the complete verified onedir payload, with source and artifact identity."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tomllib
import zipfile


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    version = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    package = root / "dist" / f"AudioAssist-{version}-windows-x64"
    check = json.loads(args.report.read_text(encoding="utf-8"))
    if not check.get("ok") or not check.get("offline_asr") or check["identity"]["version"] != version:
        raise SystemExit("A successful packaged offline ASR check with the matching version is required")
    subprocess.run(["git", "diff", "--quiet", "HEAD"], cwd=root, check=True)
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    stamp = json.loads((package / "_internal/windows-source.json").read_text(encoding="utf-8"))
    binaries = {name: digest(package / name) for name in ("AudioAssist.exe", "AudioAssist-CLI.exe")}
    if stamp != {"source_sha": commit, "version": version} or check.get("source_sha") != commit:
        raise SystemExit("The checked bundle was not built from the current source SHA")
    if binaries != check.get("executables"):
        raise SystemExit("The checked executable hashes do not match the payload")
    if Path(check["identity"]["app_path"]).resolve() != (package / "AudioAssist-CLI.exe").resolve():
        raise SystemExit("The report belongs to a different package directory")
    info = {"version": version, "source_sha": commit,
            "base_sha": "4c69d2f43c8e5bb77ee564fc736ddffed2c736d0",
            "platform": "Windows x64", "offline_asr": check["offline_asr"],
            "executables": binaries}
    (package / "BUILD_INFO.json").write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
    (package / "README.txt").write_text(
        "AudioAssist " + version + " · Windows x64\n\n"
        "请完整解压 ZIP，再双击 AudioAssist.exe。保留 _internal 文件夹，不能只复制 EXE。\n"
        "需要 Microsoft Edge WebView2 Runtime。本地模型另行在 Models 下载。\n"
        "Windows CPU 可选 Whisper Tiny / Small · Windows CPU。System/Mix 暂不支持。\n"
        "命令行：AudioAssist-CLI.exe --version\n\n"
        "Extract the entire ZIP, then open AudioAssist.exe. Keep _internal beside the EXE.\n"
        "The CLI is AudioAssist-CLI.exe. Local model weights are not included.\n",
        encoding="utf-8")
    archive = package.parent / (package.name + ".zip")
    files = sorted(path for path in package.rglob("*") if path.is_file())
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=1) as output:
        for path in files:
            if path.is_symlink():
                raise RuntimeError(f"Unexpected symlink in Windows release: {path}")
            output.write(path, arcname=path.relative_to(package.parent))
    verification = {**info, "archive": str(archive), "archive_sha256": digest(archive),
                    "archive_bytes": archive.stat().st_size, "files": len(files)}
    report = package.parent / f"release-verification-{version}-windows.json"
    report.write_text(json.dumps(verification, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"archive": str(archive), "verification": str(report),
                      "bytes": archive.stat().st_size}, ensure_ascii=False))


if __name__ == "__main__":
    main()
