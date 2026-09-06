"""REL-01 version, signing-order, and artifact-verification tests."""
from __future__ import annotations

import plistlib
from pathlib import Path
import shutil
import subprocess

import pytest

from scripts import verify_release


ROOT = Path(__file__).resolve().parents[1]


def _release_app(tmp_path: Path, version: str = "0.1.0") -> Path:
    app = tmp_path / "AudioAssist.app"
    contents = app / "Contents"
    frameworks = contents / "Frameworks"
    resources = contents / "Resources"
    executable_dir = contents / "MacOS"
    frameworks.mkdir(parents=True)
    resources.mkdir()
    executable_dir.mkdir()
    with (contents / "Info.plist").open("wb") as output:
        plistlib.dump(
            {
                "CFBundleIdentifier": "com.audioassist.app",
                "CFBundleShortVersionString": version,
                "CFBundleVersion": version,
            },
            output,
        )
    (executable_dir / "AudioAssist").write_bytes(b"\xcf\xfa\xed\xfe" + b"fixture")
    for name in ("AudioAssistCaptureHelper", "ffmpeg", "ffprobe"):
        (frameworks / name).write_bytes(b"\xcf\xfa\xed\xfe" + b"fixture")
    assets = resources / "mlx_qwen3_asr/assets"
    assets.mkdir(parents=True)
    (assets / "mel_filters.npz").write_bytes(b"filters")
    (assets / "korean_dict_jieba.dict").write_text("words", encoding="utf-8")
    metadata = resources / "mlx_qwen3_asr-0.3.5.dist-info/METADATA"
    metadata.parent.mkdir()
    metadata.write_text("Version: 0.3.5\n", encoding="utf-8")
    qwen_metadata = resources / "qwen_asr-0.0.6.dist-info/METADATA"
    qwen_metadata.parent.mkdir()
    qwen_metadata.write_text("Name: qwen-asr\nVersion: 0.0.6\n", encoding="utf-8")
    return app


@pytest.mark.parametrize("defect", ["missing", "duplicate", "wrong_version", "wrong_name"])
def test_release_verifier_rejects_invalid_qwen_metadata(tmp_path, monkeypatch, defect):
    app = _release_app(tmp_path)
    metadata = app / "Contents/Resources/qwen_asr-0.0.6.dist-info/METADATA"
    if defect == "missing":
        metadata.unlink()
    elif defect == "duplicate":
        duplicate = metadata.parent.with_name("qwen_asr-0.0.7.dist-info") / "METADATA"
        duplicate.parent.mkdir()
        duplicate.write_bytes(metadata.read_bytes())
    else:
        metadata.write_text("Name: " + ("other" if defect == "wrong_name" else "qwen-asr")
                            + "\nVersion: " + ("0.0.7" if defect == "wrong_version" else "0.0.6") + "\n")
    monkeypatch.setattr(verify_release, "_run", lambda _: pytest.fail("must reject before signing checks"))
    with pytest.raises(verify_release.VerificationError, match="qwen_asr|Qwen metadata"):
        verify_release.verify_release(app, dmg=None, expected_version="0.1.0",
                                      require_developer_id=False, require_notarized=False)


def _completed(
    command: list[str],
    *,
    developer_id: bool = False,
    leaked_path: bool = False,
    runtime: bool = True,
    timestamp: bool = True,
    team_identifier: str = "TEAM",
    cdhash: str = "ABCDEF1234",
) -> subprocess.CompletedProcess[str]:
    if command[:3] == ["codesign", "--display", "--verbose=4"]:
        if developer_id:
            flags = "flags=0x10000(runtime)" if runtime else "flags=0x0(none)"
            lines = [
                f"CodeDirectory v=20500 {flags}",
                "Authority=Developer ID Application: AudioAssist Test (TEAM)",
                f"TeamIdentifier={team_identifier}",
                f"CDHash={cdhash}",
            ]
            if timestamp:
                lines.append("Timestamp=Sep 6, 2026 at 12:00:00")
            signature = "\n".join(lines)
        else:
            signature = (
                "CodeDirectory v=20400 flags=0x2(adhoc)\n"
                "Signature=adhoc\n"
                "TeamIdentifier=not set\n"
                f"CDHash={cdhash}"
            )
        return subprocess.CompletedProcess(command, 0, "", signature)
    if command[:3] == ["codesign", "--display", "--entitlements"]:
        target = Path(command[-1])
        if target.name in {"AudioAssist.app", "AudioAssist", "AudioAssistCaptureHelper"}:
            entitlements = plistlib.dumps(
                {"com.apple.security.device.audio-input": True}
            ).decode("utf-8")
            return subprocess.CompletedProcess(command, 0, "", entitlements)
        return subprocess.CompletedProcess(command, 0, "", "Executable=fixture")
    if command[:2] == ["otool", "-l"]:
        output = (
            "path /Users/builder/.venv/libbad.dylib"
            if leaked_path
            else "path @rpath/libok.dylib"
        )
        return subprocess.CompletedProcess(command, 0, output, "")
    return subprocess.CompletedProcess(command, 0, "", "")


def test_release_verifier_accepts_complete_ad_hoc_fixture(tmp_path, monkeypatch):
    app = _release_app(tmp_path)
    dmg = tmp_path / "AudioAssist-0.1.0.dmg"
    dmg.write_bytes(b"dmg")
    monkeypatch.setattr(verify_release, "_run", _completed)
    monkeypatch.setattr(
        verify_release,
        "_verify_dmg_payload",
        lambda *args, **kwargs: {"payload_verified": True},
    )

    report = verify_release.verify_release(
        app,
        dmg=dmg,
        expected_version="0.1.0",
        require_developer_id=False,
        require_notarized=False,
    )

    assert report["ok"] is True
    assert report["signature"] == "ad-hoc"
    assert report["capture_helper_signature"] == "ad-hoc"
    assert report["version"] == "0.1.0"
    assert report["macho_files_checked"] == 4
    assert report["nested_signatures_checked"] == 0
    assert report["entitlements"] == {"com.apple.security.device.audio-input": True}
    assert len(report["dmg"]["sha256"]) == 64
    assert report["dmg"]["image_verified"] is True
    assert report["dmg"]["signature"] == "ad-hoc"
    assert report["dmg"]["payload"]["payload_verified"] is True


def test_release_verifier_audits_all_nested_developer_signatures(
    tmp_path, monkeypatch
):
    app = _release_app(tmp_path)

    def fake_run(command: list[str]) -> subprocess.CompletedProcess[str]:
        return _completed(command, developer_id=True)

    monkeypatch.setattr(verify_release, "_run", fake_run)

    report = verify_release.verify_release(
        app,
        dmg=None,
        expected_version="0.1.0",
        require_developer_id=True,
        require_notarized=False,
    )

    assert report["signature"] == "developer-id"
    assert report["team_identifier"] == "TEAM"
    assert report["nested_signatures_checked"] == 4


@pytest.mark.parametrize(
    ("runtime", "timestamp", "message"),
    [
        (False, True, "Hardened Runtime"),
        (True, False, "timestamp"),
    ],
)
def test_release_verifier_requires_distribution_signature_properties(
    tmp_path, monkeypatch, runtime, timestamp, message
):
    app = _release_app(tmp_path)

    def fake_run(command: list[str]) -> subprocess.CompletedProcess[str]:
        return _completed(
            command,
            developer_id=True,
            runtime=runtime,
            timestamp=timestamp,
        )

    monkeypatch.setattr(verify_release, "_run", fake_run)

    with pytest.raises(verify_release.VerificationError, match=message):
        verify_release.verify_release(
            app,
            dmg=None,
            expected_version="0.1.0",
            require_developer_id=True,
            require_notarized=False,
        )


def test_release_verifier_rejects_nested_team_mismatch(tmp_path, monkeypatch):
    app = _release_app(tmp_path)

    def fake_run(command: list[str]) -> subprocess.CompletedProcess[str]:
        if command[:3] == ["codesign", "--display", "--verbose=4"]:
            team_identifier = "OTHER" if Path(command[-1]).name == "ffmpeg" else "TEAM"
            return _completed(
                command,
                developer_id=True,
                team_identifier=team_identifier,
            )
        return _completed(command, developer_id=True)

    monkeypatch.setattr(verify_release, "_run", fake_run)

    with pytest.raises(verify_release.VerificationError, match="TeamIdentifier mismatch"):
        verify_release.verify_release(
            app,
            dmg=None,
            expected_version="0.1.0",
            require_developer_id=True,
            require_notarized=False,
        )


def test_release_verifier_rejects_nested_entitlements(tmp_path, monkeypatch):
    app = _release_app(tmp_path)

    def fake_run(command: list[str]) -> subprocess.CompletedProcess[str]:
        if (
            command[:3] == ["codesign", "--display", "--entitlements"]
            and Path(command[-1]).name == "ffmpeg"
        ):
            entitlements = plistlib.dumps(
                {"com.apple.security.device.audio-input": True}
            ).decode("utf-8")
            return subprocess.CompletedProcess(command, 0, "", entitlements)
        return _completed(command, developer_id=True)

    monkeypatch.setattr(verify_release, "_run", fake_run)

    with pytest.raises(verify_release.VerificationError, match="unexpected entitlements"):
        verify_release.verify_release(
            app,
            dmg=None,
            expected_version="0.1.0",
            require_developer_id=True,
            require_notarized=False,
        )


def test_dmg_payload_must_match_source_app(tmp_path, monkeypatch):
    source_app = _release_app(tmp_path / "source")
    stale_app = _release_app(tmp_path / "stale", version="9.9.9")
    dmg = tmp_path / "AudioAssist-0.1.0.dmg"
    dmg.write_bytes(b"dmg")

    def fake_run(command: list[str]) -> subprocess.CompletedProcess[str]:
        if command[:2] == ["hdiutil", "attach"]:
            mount = Path(command[command.index("-mountpoint") + 1])
            shutil.copytree(stale_app, mount / "AudioAssist.app")
        return _completed(command)

    monkeypatch.setattr(verify_release, "_run", fake_run)

    with pytest.raises(verify_release.VerificationError, match="version mismatch"):
        verify_release._verify_dmg_payload(
            dmg,
            source_app=source_app,
            expected_version="0.1.0",
            source_details=_completed(
                ["codesign", "--display", "--verbose=4", str(source_app)]
            ).stderr,
            require_developer_id=False,
            team_identifier=None,
        )


def test_dmg_payload_is_mounted_read_only_and_compared(tmp_path, monkeypatch):
    source_app = _release_app(tmp_path / "source")
    dmg = tmp_path / "AudioAssist-0.1.0.dmg"
    dmg.write_bytes(b"dmg")
    commands: list[list[str]] = []

    def fake_run(command: list[str]) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        if command[:2] == ["hdiutil", "attach"]:
            mount = Path(command[command.index("-mountpoint") + 1])
            shutil.copytree(source_app, mount / "AudioAssist.app")
        return _completed(command)

    monkeypatch.setattr(verify_release, "_run", fake_run)
    payload = verify_release._verify_dmg_payload(
        dmg,
        source_app=source_app,
        expected_version="0.1.0",
        source_details=_completed(
            ["codesign", "--display", "--verbose=4", str(source_app)]
        ).stderr,
        require_developer_id=False,
        team_identifier=None,
    )

    attach = next(command for command in commands if command[:2] == ["hdiutil", "attach"])
    assert "-readonly" in attach
    assert "-nobrowse" in attach
    assert any(command[:2] == ["hdiutil", "detach"] for command in commands)
    assert payload["payload_verified"] is True
    assert payload["cdhash"] == "ABCDEF1234"


def test_release_verifier_ignores_otool_file_header(tmp_path, monkeypatch):
    app = _release_app(tmp_path)

    def fake_run(command: list[str]) -> subprocess.CompletedProcess[str]:
        if command[:2] == ["otool", "-l"]:
            return subprocess.CompletedProcess(command, 0, f"{command[-1]}:\n", "")
        return _completed(command)

    monkeypatch.setattr(verify_release, "_run", fake_run)

    report = verify_release.verify_release(
        app,
        dmg=None,
        expected_version="0.1.0",
        require_developer_id=False,
        require_notarized=False,
    )

    assert report["macho_files_checked"] == 4


def test_release_verifier_rejects_version_drift(tmp_path):
    app = _release_app(tmp_path, version="1.0.0")

    with pytest.raises(verify_release.VerificationError, match="version mismatch"):
        verify_release.verify_release(
            app,
            dmg=None,
            expected_version="0.1.0",
            require_developer_id=False,
            require_notarized=False,
        )


def test_release_verifier_rejects_ad_hoc_when_developer_id_required(
    tmp_path, monkeypatch
):
    app = _release_app(tmp_path)
    monkeypatch.setattr(verify_release, "_run", _completed)

    with pytest.raises(verify_release.VerificationError, match="Developer ID"):
        verify_release.verify_release(
            app,
            dmg=None,
            expected_version="0.1.0",
            require_developer_id=True,
            require_notarized=False,
        )


def test_release_verifier_requires_developer_id_on_dmg(tmp_path, monkeypatch):
    app = _release_app(tmp_path)
    dmg = tmp_path / "AudioAssist-0.1.0.dmg"
    dmg.write_bytes(b"dmg")

    def fake_run(command: list[str]) -> subprocess.CompletedProcess[str]:
        developer_id = (
            command[:3] == ["codesign", "--display", "--verbose=4"]
            and not str(command[-1]).endswith(".dmg")
        )
        return _completed(command, developer_id=developer_id)

    monkeypatch.setattr(verify_release, "_run", fake_run)

    with pytest.raises(verify_release.VerificationError, match="Developer ID.*DMG"):
        verify_release.verify_release(
            app,
            dmg=dmg,
            expected_version="0.1.0",
            require_developer_id=True,
            require_notarized=False,
        )


def test_release_verifier_rejects_developer_load_paths(tmp_path, monkeypatch):
    app = _release_app(tmp_path)

    def fake_run(command: list[str]) -> subprocess.CompletedProcess[str]:
        return _completed(command, leaked_path=True)

    monkeypatch.setattr(verify_release, "_run", fake_run)

    with pytest.raises(verify_release.VerificationError, match="developer path"):
        verify_release.verify_release(
            app,
            dmg=None,
            expected_version="0.1.0",
            require_developer_id=False,
            require_notarized=False,
        )


def test_build_contract_versions_signs_and_packages_in_order():
    script = (ROOT / "scripts/build_dmg.sh").read_text(encoding="utf-8")
    spec = (ROOT / "AudioAssist.spec").read_text(encoding="utf-8")

    assert 'APP_VERSION="$(cd "$ROOT"' in script
    assert 'DMG_NAME="${APP_NAME}-${APP_VERSION}.dmg"' in script
    helper_sign = script.index('"$HELPER_IN_BUNDLE"')
    app_sign = script.index('"$APP_PATH"', helper_sign)
    dmg_create = script.index("Creating and signing $DMG_NAME")
    assert helper_sign < app_sign < dmg_create
    assert "codesign --deep --force" not in script
    assert "hdiutil create" in script
    assert "create-dmg" not in script
    assert "notarytool submit" in script
    assert "stapler staple" in script
    assert "scripts/verify_release.py" in script
    assert "Developer ID builds cannot reuse an ad-hoc PyInstaller bundle" in script

    assert 'APP_VERSION = tomllib.load(project_file)["project"]["version"]' in spec
    assert "version=APP_VERSION" in spec
    assert '"CFBundleVersion":             APP_VERSION' in spec
    assert '"CFBundleShortVersionString":  "1.0.0"' not in spec
    assert 'shutil.which("ffmpeg")' in spec
    assert 'shutil.which("ffprobe")' in spec
    assert "/opt/homebrew" not in spec
    assert '"NSMicrophoneUsageDescription"' in spec
    assert '"NSScreenCaptureUsageDescription"' in spec
    assert '"NSAudioCaptureUsageDescription"' in spec


def test_release_entitlements_and_acceleration_claims_match_implementation():
    for name in ("entitlements.plist", "entitlements_helper.plist"):
        with (ROOT / name).open("rb") as source:
            assert plistlib.load(source) == {"com.apple.security.device.audio-input": True}

    product_text = "\n".join(
        [
            (ROOT / "README.dev.md").read_text(encoding="utf-8"),
            (ROOT / "src/model_manager.py").read_text(encoding="utf-8"),
        ]
    )
    assert "Neural Engine" not in product_text
    assert "速度快8倍" not in product_text
    assert "Apple GPU/Metal" in product_text
