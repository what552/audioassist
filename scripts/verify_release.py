#!/usr/bin/env python3
"""Verify an AudioAssist macOS release bundle and emit a JSON evidence record."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import plistlib
import re
import subprocess
import sys
import tomllib
from typing import Sequence


ROOT = Path(__file__).resolve().parents[1]
BUNDLE_ID = "com.audioassist.app"
MACHO_MAGICS = {
    b"\xca\xfe\xba\xbe",
    b"\xbe\xba\xfe\xca",
    b"\xca\xfe\xba\xbf",
    b"\xbf\xba\xfe\xca",
    b"\xfe\xed\xfa\xce",
    b"\xce\xfa\xed\xfe",
    b"\xfe\xed\xfa\xcf",
    b"\xcf\xfa\xed\xfe",
}
REQUIRED_FILES = (
    "Contents/MacOS/AudioAssist",
    "Contents/Frameworks/AudioAssistCaptureHelper",
    "Contents/Frameworks/ffmpeg",
    "Contents/Frameworks/ffprobe",
    "Contents/Resources/mlx_qwen3_asr/assets/mel_filters.npz",
    "Contents/Resources/mlx_qwen3_asr/assets/korean_dict_jieba.dict",
)
EXPECTED_AUDIO_ENTITLEMENTS = {"com.apple.security.device.audio-input": True}


class VerificationError(RuntimeError):
    """The release artifact does not satisfy a mechanical release invariant."""


def project_version() -> str:
    with (ROOT / "pyproject.toml").open("rb") as project_file:
        return str(tomllib.load(project_file)["project"]["version"])


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True, check=False)


def _require_success(command: list[str], label: str) -> subprocess.CompletedProcess[str]:
    result = _run(command)
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()
        raise VerificationError(f"{label} failed: {detail}")
    return result


def _codesign_details(path: Path) -> str:
    result = _require_success(
        ["codesign", "--display", "--verbose=4", str(path)],
        f"codesign details for {path.name}",
    )
    return "\n".join(part for part in (result.stdout, result.stderr) if part)


def _codesign_entitlements(path: Path) -> dict[str, object]:
    result = _require_success(
        ["codesign", "--display", "--entitlements", "-", "--xml", str(path)],
        f"codesign entitlements for {path.name}",
    )
    output = result.stdout if "<?xml" in result.stdout else result.stderr
    start = output.find("<?xml")
    if start < 0:
        return {}
    end = output.find("</plist>", start)
    if end < 0:
        raise VerificationError(f"invalid entitlement plist for {path.name}")
    return plistlib.loads(output[start : end + len("</plist>")].encode("utf-8"))


def _signature_kind(details: str) -> str:
    if "Authority=Developer ID Application:" in details:
        return "developer-id"
    if "Signature=adhoc" in details:
        return "ad-hoc"
    return "other"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_macho(path: Path) -> bool:
    if path.is_symlink() or not path.is_file():
        return False
    try:
        with path.open("rb") as source:
            return source.read(4) in MACHO_MAGICS
    except OSError:
        return False


def _verify_macho_load_paths(app: Path) -> int:
    checked = 0
    for path in app.rglob("*"):
        if not _is_macho(path):
            continue
        checked += 1
        result = _require_success(["otool", "-l", str(path)], f"otool for {path.name}")
        load_commands = result.stdout
        header = f"{path}:"
        leaked = next(
            (
                line.strip()
                for line in load_commands.splitlines()
                if line.strip() != header
                and (
                    "/Users/" in line
                    or "/.venv/" in line
                    or "/private/tmp/" in line
                )
            ),
            None,
        )
        if leaked:
            relative = path.relative_to(app)
            raise VerificationError(f"developer path in {relative}: {leaked}")
    if checked == 0:
        raise VerificationError("app contains no Mach-O binaries")
    return checked


def _verify_symlinks(app: Path) -> int:
    app_root = app.resolve()
    checked = 0
    for path in app.rglob("*"):
        if not path.is_symlink():
            continue
        checked += 1
        try:
            path.resolve(strict=True).relative_to(app_root)
        except (FileNotFoundError, ValueError) as exc:
            relative = path.relative_to(app)
            raise VerificationError(f"external or broken app symlink: {relative}") from exc
    return checked


def verify_release(
    app: Path,
    *,
    dmg: Path | None,
    expected_version: str,
    require_developer_id: bool,
    require_notarized: bool,
) -> dict[str, object]:
    app = app.resolve()
    if not app.is_dir():
        raise VerificationError(f"app bundle does not exist: {app}")
    info_path = app / "Contents/Info.plist"
    if not info_path.is_file():
        raise VerificationError(f"Info.plist does not exist: {info_path}")
    with info_path.open("rb") as info_file:
        info = plistlib.load(info_file)
    versions = {
        "short": str(info.get("CFBundleShortVersionString", "")),
        "build": str(info.get("CFBundleVersion", "")),
    }
    if set(versions.values()) != {expected_version}:
        raise VerificationError(
            f"bundle version mismatch: expected {expected_version}, found {versions}"
        )
    bundle_id = str(info.get("CFBundleIdentifier", ""))
    if bundle_id != BUNDLE_ID:
        raise VerificationError(f"bundle identifier mismatch: {bundle_id!r}")

    missing = [relative for relative in REQUIRED_FILES if not (app / relative).is_file()]
    metadata = list(
        app.glob("Contents/Resources/mlx_qwen3_asr-*.dist-info/METADATA")
    )
    if len(metadata) != 1:
        missing.append("Contents/Resources/mlx_qwen3_asr-*.dist-info/METADATA")
    if missing:
        raise VerificationError(f"required packaged files missing: {', '.join(missing)}")

    _require_success(
        ["codesign", "--verify", "--all-architectures", "--deep", "--strict", str(app)],
        "strict app signature verification",
    )
    app_details = _codesign_details(app)
    signature_kind = _signature_kind(app_details)
    if require_developer_id and signature_kind != "developer-id":
        raise VerificationError("Developer ID signature required but not present")

    helper = app / "Contents/Frameworks/AudioAssistCaptureHelper"
    helper_signature_kind = _signature_kind(_codesign_details(helper))
    if require_developer_id and helper_signature_kind != "developer-id":
        raise VerificationError("Developer ID capture-helper signature required but not present")
    for target in (app, helper):
        entitlements = _codesign_entitlements(target)
        if entitlements != EXPECTED_AUDIO_ENTITLEMENTS:
            raise VerificationError(
                f"unexpected entitlements for {target.name}: {entitlements}"
            )

    team_match = re.search(r"^TeamIdentifier=(.+)$", app_details, re.MULTILINE)
    team_identifier = team_match.group(1) if team_match else None
    if team_identifier == "not set":
        team_identifier = None
    report: dict[str, object] = {
        "ok": True,
        "app": str(app),
        "bundle_id": bundle_id,
        "version": expected_version,
        "signature": signature_kind,
        "capture_helper_signature": helper_signature_kind,
        "team_identifier": team_identifier,
        "entitlements": EXPECTED_AUDIO_ENTITLEMENTS,
        "macho_files_checked": _verify_macho_load_paths(app),
        "symlinks_checked": _verify_symlinks(app),
        "required_files": list(REQUIRED_FILES),
        "mlx_qwen_metadata": str(metadata[0].relative_to(app)),
    }

    if dmg is not None:
        dmg = dmg.resolve()
        if not dmg.is_file():
            raise VerificationError(f"DMG does not exist: {dmg}")
        _require_success(
            ["codesign", "--verify", "--verbose=2", str(dmg)],
            "DMG signature verification",
        )
        dmg_signature_kind = _signature_kind(_codesign_details(dmg))
        if require_developer_id and dmg_signature_kind != "developer-id":
            raise VerificationError("Developer ID DMG signature required but not present")
        _require_success(["hdiutil", "verify", str(dmg)], "DMG image verification")
        if require_notarized:
            if signature_kind != "developer-id" or dmg_signature_kind != "developer-id":
                raise VerificationError("notarization requires Developer ID app and DMG signatures")
            _require_success(
                ["xcrun", "stapler", "validate", str(dmg)],
                "DMG notarization ticket validation",
            )
        report["dmg"] = {
            "path": str(dmg),
            "sha256": _sha256(dmg),
            "image_verified": True,
            "signature": dmg_signature_kind,
            "notarization_required": require_notarized,
        }
    elif require_notarized:
        raise VerificationError("notarization validation requires --dmg")
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", required=True, type=Path)
    parser.add_argument("--dmg", type=Path)
    parser.add_argument("--expected-version", default=project_version())
    parser.add_argument("--require-developer-id", action="store_true")
    parser.add_argument("--require-notarized", action="store_true")
    parser.add_argument("--output", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = verify_release(
            args.app,
            dmg=args.dmg,
            expected_version=args.expected_version,
            require_developer_id=args.require_developer_id,
            require_notarized=args.require_notarized,
        )
    except VerificationError as exc:
        report = {"ok": False, "error": str(exc)}
        print(json.dumps(report, ensure_ascii=False))
        return 1
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    sys.stdout.write(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
