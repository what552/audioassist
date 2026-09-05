"""Agent-friendly command-line interface for AudioAssist."""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from typing import Any

from platformdirs import user_data_dir

from .service import (
    AudioAssistError,
    AudioAssistService,
    ConfigurationError,
    SessionNotFoundError,
)


EXIT_OK = 0
EXIT_USAGE = 2
EXIT_NOT_FOUND = 3
EXIT_NOT_READY = 4
EXIT_PROCESSING = 5

DEFAULT_SUMMARY_PROMPT = (
    "Create structured meeting notes with the main topics, key decisions, and action "
    "items with owners. Do not add information that is absent from the transcript."
)


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="audioassist",
        description="Inspect and process AudioAssist sessions without opening the GUI.",
    )
    parser.add_argument(
        "--output-dir",
        help="Override the configured AudioAssist output directory.",
    )
    parser.add_argument(
        "--config",
        help="Override the AudioAssist config.json path.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Write one stable JSON response envelope to stdout.",
    )

    commands = parser.add_subparsers(dest="command", required=True)
    sessions = commands.add_parser("sessions", help="Read stored sessions.")
    session_commands = sessions.add_subparsers(dest="session_command", required=True)
    session_commands.add_parser("list", help="List sessions newest first.")
    show = session_commands.add_parser("show", help="Show one session and its resources.")
    show.add_argument("job_id")

    status = commands.add_parser("status", help="Read durable state for one session.")
    status.add_argument("job_id")

    transcribe = commands.add_parser("transcribe", help="Synchronously transcribe one file.")
    transcribe.add_argument("file")
    transcribe.add_argument("--engine", choices=("qwen", "whisper"), default="qwen")
    transcribe.add_argument("--model-id")
    transcribe.add_argument("--num-speakers", type=_positive_int)
    transcribe.add_argument("--job-id")
    transcribe.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress progress messages on stderr.",
    )

    summarize = commands.add_parser("summarize", help="Generate and save a summary version.")
    summarize.add_argument("job_id")
    summarize.add_argument("--prompt", default=DEFAULT_SUMMARY_PROMPT)
    return parser


def _resolve_paths(args: argparse.Namespace) -> tuple[str, str]:
    app_data_dir = user_data_dir("TranscribeApp", appauthor=False)
    config_path = os.path.abspath(
        os.path.expanduser(args.config or os.path.join(app_data_dir, "config.json"))
    )
    default_output = os.path.join(app_data_dir, "output")
    output_dir = args.output_dir
    if not output_dir:
        try:
            with open(config_path, encoding="utf-8") as file:
                configured = json.load(file).get("storage", {}).get("output_dir", "")
            if configured and os.path.isdir(os.path.expanduser(configured)):
                output_dir = configured
        except (OSError, ValueError, TypeError, AttributeError):
            pass
    return os.path.abspath(os.path.expanduser(output_dir or default_output)), config_path


def _success(data: Any) -> dict:
    return {"ok": True, "data": data, "error": None}


def _failure(code: str, message: str) -> dict:
    return {
        "ok": False,
        "data": None,
        "error": {"code": code, "message": message},
    }


def _emit(payload: dict, *, json_output: bool, human_text: str | None = None) -> None:
    if json_output or human_text is None:
        print(json.dumps(payload, ensure_ascii=False, indent=None if json_output else 2))
    else:
        print(human_text)


def _human_sessions(items: list[dict]) -> str:
    if not items:
        return "No sessions found."
    lines = []
    for item in items:
        status = item.get("status", "completed" if item.get("type") == "file" else "recorded")
        lines.append(
            f"{item['job_id']}\t{status}\t{item.get('date', '')}\t{item.get('filename', '')}"
        )
    return "\n".join(lines)


def _run(args: argparse.Namespace, service: AudioAssistService) -> tuple[dict, str | None]:
    if args.command == "sessions" and args.session_command == "list":
        items = service.list_sessions()
        return _success(items), _human_sessions(items)

    if args.command == "sessions" and args.session_command == "show":
        data = service.get_session(args.job_id)
        return _success(data), json.dumps(data, ensure_ascii=False, indent=2)

    if args.command == "status":
        data = service.get_status(args.job_id)
        text = f"{data['job_id']}\t{data['status']}\t{data['progress']:.0%}"
        return _success(data), text

    if args.command == "transcribe":
        def progress(percent: float, message: str) -> None:
            if not args.quiet:
                print(f"[{percent:>6.1%}] {message}", file=sys.stderr, flush=True)

        data = service.transcribe(
            args.file,
            engine=args.engine,
            model_id=args.model_id,
            num_speakers=args.num_speakers,
            job_id=args.job_id,
            hf_token=os.environ.get("HF_TOKEN"),
            progress_callback=progress,
        )
        text = f"Completed {data['job_id']}\n{data['json_path']}"
        return _success(data), text

    if args.command == "summarize":
        result = service.summarize(args.job_id, args.prompt, stream=False)
        text = str(result)
        version = service.save_summary_version(args.job_id, text)
        data = {"job_id": args.job_id, "summary": text, "version": version}
        return _success(data), text

    raise AssertionError(f"Unhandled command: {args.command}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    output_dir, config_path = _resolve_paths(args)
    service = AudioAssistService(output_dir, config_path)
    try:
        payload, human_text = _run(args, service)
        _emit(payload, json_output=args.json_output, human_text=human_text)
        return EXIT_OK
    except SessionNotFoundError as error:
        _emit(
            _failure(error.code, str(error)),
            json_output=args.json_output,
            human_text=f"Error: {error}",
        )
        return EXIT_NOT_FOUND
    except FileNotFoundError as error:
        _emit(
            _failure("file_not_found", str(error)),
            json_output=args.json_output,
            human_text=f"Error: {error}",
        )
        return EXIT_NOT_FOUND
    except ConfigurationError as error:
        _emit(
            _failure(error.code, str(error)),
            json_output=args.json_output,
            human_text=f"Error: {error}",
        )
        return EXIT_NOT_READY
    except AudioAssistError as error:
        _emit(
            _failure(error.code, str(error)),
            json_output=args.json_output,
            human_text=f"Error: {error}",
        )
        return EXIT_USAGE
    except Exception as error:
        error_code = "model_not_ready" if error.__class__.__name__ == "ModelNotReadyError" else "processing_failed"
        exit_code = EXIT_NOT_READY if error_code == "model_not_ready" else EXIT_PROCESSING
        _emit(
            _failure(error_code, str(error)),
            json_output=args.json_output,
            human_text=f"Error: {error}",
        )
        return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
