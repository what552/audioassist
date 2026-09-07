"""Agent-friendly command-line interface for AudioAssist."""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import signal
import sys
import threading
from collections.abc import Sequence
from typing import Any

from .service import (
    AudioAssistError,
    AudioAssistService,
    ConfigurationError,
    SessionNotFoundError,
)
from .local_api import LocalAPIError, LocalAPIServer
from .preferences import BACKENDS
from .runtime_paths import resolve_runtime_paths
from .runtime_identity import format_runtime_identity, get_runtime_identity


EXIT_OK = 0
EXIT_USAGE = 2
EXIT_NOT_FOUND = 3
EXIT_NOT_READY = 4
EXIT_PROCESSING = 5

DEFAULT_SUMMARY_PROMPT = (
    "Create structured meeting notes with the main topics, key decisions, and action "
    "items with owners. Do not add information that is absent from the transcript."
)


class CLIUsageError(ValueError):
    """Raised instead of exiting so JSON callers receive the normal envelope."""


class AudioAssistArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise CLIUsageError(message)


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return parsed


def _port(value: str) -> int:
    parsed = int(value)
    if not 0 <= parsed <= 65535:
        raise argparse.ArgumentTypeError("must be between 0 and 65535")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = AudioAssistArgumentParser(
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
    parser.add_argument(
        "--version",
        action="store_true",
        help="Show the source/package version and actual runtime location.",
    )

    commands = parser.add_subparsers(dest="command")
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
    transcribe.add_argument(
        "--qwen-backend",
        choices=BACKENDS,
        help=(
            "Override the saved Qwen backend for this job: auto, pytorch-cpu, "
            "pytorch-mps, mlx or pytorch-cuda."
        ),
    )
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

    serve = commands.add_parser("serve", help="Run the foreground loopback HTTP service.")
    serve.add_argument(
        "--read-only",
        action="store_true",
        required=True,
        help="Expose session reads only; recording requires the visible desktop app.",
    )
    serve.add_argument("--port", type=_port, default=0, help="Loopback port (0 chooses one).")
    return parser


def _resolve_paths(args: argparse.Namespace) -> tuple[str, str]:
    output_dir, config_path, _ = resolve_runtime_paths(args.output_dir, args.config)
    return output_dir, config_path


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


def _emit_error(payload: dict, *, json_output: bool, human_text: str) -> None:
    if json_output:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        print(human_text, file=sys.stderr)


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
            qwen_backend=args.qwen_backend,
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


def _run_server(
    args: argparse.Namespace,
    service: AudioAssistService,
    config_path: str,
) -> int:
    server = LocalAPIServer(
        service,
        mode="read-only",
        app_data_dir=os.path.dirname(config_path),
        port=args.port,
    )
    previous_handlers = {}

    def interrupt(_signum, _frame):
        raise KeyboardInterrupt

    try:
        if threading.current_thread() is threading.main_thread():
            for signum in (signal.SIGINT, signal.SIGTERM):
                previous_handlers[signum] = signal.signal(signum, interrupt)
        record = server.start()
        data = {
            "schema": record.schema,
            "instance_id": record.instance_id,
            "mode": record.mode,
            "host": record.host,
            "port": record.port,
            "discovery_path": server.discovery_path,
        }
        _emit(
            _success(data),
            json_output=args.json_output,
            human_text=f"AudioAssist read-only API listening on http://{record.host}:{record.port}",
        )
        server.wait()
        return EXIT_OK
    except KeyboardInterrupt:
        return EXIT_OK
    finally:
        server.stop()
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    raw_argv = list(argv) if argv is not None else sys.argv[1:]
    json_requested = "--json" in raw_argv
    try:
        args = parser.parse_args(raw_argv)
        if args.version and args.command is not None:
            raise CLIUsageError("--version cannot be combined with a command")
        if not args.version and args.command is None:
            raise CLIUsageError("the following arguments are required: command")
        if (
            args.command == "transcribe"
            and args.qwen_backend is not None
            and AudioAssistService._engine_for_model(args.model_id, args.engine) != "qwen"
        ):
            raise CLIUsageError("--qwen-backend can only be used with a Qwen ASR model")
    except CLIUsageError as error:
        _emit_error(
            _failure("invalid_arguments", str(error)),
            json_output=json_requested,
            human_text=f"Error: {error}",
        )
        return EXIT_USAGE
    if args.version:
        identity = get_runtime_identity()
        _emit(
            _success(identity),
            json_output=args.json_output,
            human_text=format_runtime_identity(identity),
        )
        return EXIT_OK
    output_dir, config_path = _resolve_paths(args)
    service = AudioAssistService(output_dir, config_path)
    try:
        if args.command == "serve":
            return _run_server(args, service, config_path)
        if args.json_output:
            # Some ML dependencies print directly instead of using logging.
            # Keep the machine-readable stdout contract even in that case.
            with contextlib.redirect_stdout(sys.stderr):
                payload, human_text = _run(args, service)
        else:
            payload, human_text = _run(args, service)
        _emit(payload, json_output=args.json_output, human_text=human_text)
        return EXIT_OK
    except SessionNotFoundError as error:
        _emit_error(
            _failure(error.code, str(error)),
            json_output=args.json_output,
            human_text=f"Error: {error}",
        )
        return EXIT_NOT_FOUND
    except FileNotFoundError as error:
        _emit_error(
            _failure("file_not_found", str(error)),
            json_output=args.json_output,
            human_text=f"Error: {error}",
        )
        return EXIT_NOT_FOUND
    except ConfigurationError as error:
        _emit_error(
            _failure(error.code, str(error)),
            json_output=args.json_output,
            human_text=f"Error: {error}",
        )
        return EXIT_NOT_READY
    except ImportError as error:
        _emit_error(
            _failure("dependency_not_ready", str(error)),
            json_output=args.json_output,
            human_text=f"Error: {error}",
        )
        return EXIT_NOT_READY
    except AudioAssistError as error:
        _emit_error(
            _failure(error.code, str(error)),
            json_output=args.json_output,
            human_text=f"Error: {error}",
        )
        return EXIT_USAGE
    except LocalAPIError as error:
        _emit_error(
            _failure(error.code, str(error)),
            json_output=args.json_output,
            human_text=f"Error: {error}",
        )
        return EXIT_PROCESSING
    except Exception as error:
        error_code = "model_not_ready" if error.__class__.__name__ == "ModelNotReadyError" else "processing_failed"
        exit_code = EXIT_NOT_READY if error_code == "model_not_ready" else EXIT_PROCESSING
        _emit_error(
            _failure(error_code, str(error)),
            json_output=args.json_output,
            human_text=f"Error: {error}",
        )
        return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
