"""Shared Windows GUI/console entrypoint, with a reproducible bundle check."""
from __future__ import annotations
import multiprocessing
import os
from pathlib import Path
import sys

if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def bundle_check(arguments):
    import argparse
    import importlib
    import json
    import subprocess
    import traceback
    from src.runtime_identity import get_runtime_identity
    from src.audio_utils import _find_bin, get_duration

    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle-check-report", required=True)
    parser.add_argument("--audio")
    parser.add_argument("--model-dir")
    args = parser.parse_args(arguments)
    report = {"identity": get_runtime_identity(), "checks": {}, "ok": False}
    try:
        for name in ("torch", "torchaudio", "qwen_asr", "pyannote.audio", "sounddevice", "faster_whisper"):
            importlib.import_module(name)
            report["checks"][name] = "imported"
        base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
        if not (base / "ui/index.html").is_file():
            raise RuntimeError("Bundled UI missing")
        for binary in ("ffmpeg", "ffprobe"):
            path = _find_bin(binary)
            result = subprocess.run([path, "-version"], capture_output=True, text=True,
                                    check=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            report["checks"][binary] = {"path": path, "version": result.stdout.splitlines()[0]}
        if args.audio:
            if not args.model_dir or not Path(args.model_dir).is_dir():
                raise ValueError("A local CTranslate2 model directory is required for the offline audio check")
            os.environ["HF_HUB_OFFLINE"] = "1"
            os.environ["TRANSFORMERS_OFFLINE"] = "1"
            from src.asr_whisper import WhisperASREngine
            engine = WhisperASREngine(model_path=str(Path(args.model_dir).resolve()), language="en")
            result = engine.transcribe(args.audio)
            if not result.text.strip():
                raise RuntimeError("Offline ASR returned no text")
            report["offline_asr"] = {"backend": "faster-whisper/cpu/int8", "text": result.text,
                                     "language": result.language, "words": len(result.words),
                                     "seconds": get_duration(args.audio)}
        report["ok"] = True
    except Exception:
        report["error"] = traceback.format_exc()
    Path(args.bundle_check_report).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if report["ok"] else 1


def main():
    if "--bundle-check-report" in sys.argv:
        return bundle_check(sys.argv[1:])
    if "--cli" in sys.argv or Path(sys.executable).stem.lower() == "audioassist-cli":
        from src.cli import main as cli_main
        return cli_main([arg for arg in sys.argv[1:] if arg != "--cli"])
    from run import main as gui_main
    gui_main()
    return 0


if __name__ == "__main__":
    multiprocessing.freeze_support()
    raise SystemExit(main())
