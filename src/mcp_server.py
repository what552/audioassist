"""Small stdio MCP adapter for AudioAssist session reads and recording control."""
from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Sequence
from typing import Any, TextIO

from .local_api import LocalAPIError, request_desktop, validate_model_id
from .runtime_paths import resolve_runtime_paths
from .service import AudioAssistError, AudioAssistService

logger = logging.getLogger(__name__)

LATEST_PROTOCOL_VERSION = "2025-11-25"
SUPPORTED_PROTOCOL_VERSIONS = frozenset({
    "2025-11-25",
    "2025-06-18",
    "2025-03-26",
})


def _object_schema(properties: dict[str, Any] | None = None, required: list[str] | None = None) -> dict:
    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties or {},
        "additionalProperties": False,
    }
    if required:
        schema["required"] = required
    return schema


TOOLS = [
    {
        "name": "audioassist_list_sessions",
        "description": "List locally stored AudioAssist sessions, newest first.",
        "inputSchema": _object_schema(),
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "audioassist_get_session",
        "description": "Read one AudioAssist session, transcript, status, and summaries.",
        "inputSchema": _object_schema({"job_id": {"type": "string"}}, ["job_id"]),
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "audioassist_get_status",
        "description": "Read durable processing status for one AudioAssist session.",
        "inputSchema": _object_schema({"job_id": {"type": "string"}}, ["job_id"]),
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "audioassist_recording_status",
        "description": "Read live recording state from the visible AudioAssist desktop app.",
        "inputSchema": _object_schema(),
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "audioassist_get_live_transcript",
        "description": "Read paged provisional utterances recognized during a recording.",
        "inputSchema": _object_schema(
            {
                "session_id": {"type": "string"},
                "after_seq": {"type": "integer", "minimum": 0},
                "limit": {"type": "integer", "minimum": 1, "maximum": 500},
            },
            ["session_id"],
        ),
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "audioassist_start_recording",
        "description": "Ask the visible AudioAssist app to start a local recording.",
        "inputSchema": _object_schema(
            {
                "capture_mode": {"type": "string", "enum": ["mic", "system", "mix"]},
                "model_id": {"type": ["string", "null"]},
            },
            ["capture_mode"],
        ),
    },
    {
        "name": "audioassist_pause_recording",
        "description": "Pause the active AudioAssist recording.",
        "inputSchema": _object_schema(),
    },
    {
        "name": "audioassist_resume_recording",
        "description": "Resume the paused AudioAssist recording.",
        "inputSchema": _object_schema(),
    },
    {
        "name": "audioassist_stop_recording",
        "description": "Stop the active AudioAssist recording.",
        "inputSchema": _object_schema(),
    },
]


class MCPServer:
    def __init__(self, service: AudioAssistService, *, app_data_dir: str) -> None:
        self.service = service
        self.app_data_dir = app_data_dir
        self.initialized = False

    @staticmethod
    def _rpc_result(request_id: Any, result: Any) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    @staticmethod
    def _rpc_error(request_id: Any, code: int, message: str) -> dict[str, Any]:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": code, "message": message},
        }

    def handle(self, message: Any) -> dict[str, Any] | None:
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            return self._rpc_error(None, -32600, "Invalid Request")
        request_id = message.get("id")
        method = message.get("method")
        params = message.get("params", {})
        if not isinstance(method, str) or not isinstance(params, dict):
            return self._rpc_error(request_id, -32600, "Invalid Request")
        if "id" not in message:
            if method == "notifications/initialized":
                self.initialized = True
            return None
        if method == "initialize":
            requested = params.get("protocolVersion")
            protocol = requested if requested in SUPPORTED_PROTOCOL_VERSIONS else LATEST_PROTOCOL_VERSION
            return self._rpc_result(request_id, {
                "protocolVersion": protocol,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "audioassist", "version": "2.0.0"},
                "instructions": (
                    "Session reads work without the desktop app. Recording tools require "
                    "the visible AudioAssist app and never launch it automatically."
                ),
            })
        if method == "ping":
            return self._rpc_result(request_id, {})
        if not self.initialized:
            return self._rpc_error(request_id, -32002, "Server not initialized")
        if method == "tools/list":
            return self._rpc_result(request_id, {"tools": TOOLS})
        if method == "tools/call":
            name = params.get("name")
            arguments = params.get("arguments", {})
            if not isinstance(name, str) or not isinstance(arguments, dict):
                return self._rpc_error(request_id, -32602, "Invalid params")
            if name not in {tool["name"] for tool in TOOLS}:
                return self._rpc_error(request_id, -32602, f"Unknown tool: {name}")
            return self._rpc_result(request_id, self._call_tool(name, arguments))
        return self._rpc_error(request_id, -32601, "Method not found")

    def _call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        try:
            data = self._execute_tool(name, arguments)
            return {
                "content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False)}],
                "structuredContent": data,
                "isError": False,
            }
        except (AudioAssistError, LocalAPIError, ValueError) as error:
            code = getattr(error, "code", "invalid_request")
            data = {"ok": False, "data": None, "error": {"code": code, "message": str(error)}}
            return {
                "content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False)}],
                "structuredContent": data,
                "isError": True,
            }
        except Exception as error:
            logger.exception("MCP tool failed: %s", name)
            data = {
                "ok": False,
                "data": None,
                "error": {"code": "internal_error", "message": "Internal server error"},
            }
            return {
                "content": [{"type": "text", "text": json.dumps(data)}],
                "structuredContent": data,
                "isError": True,
            }

    def _execute_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        if name == "audioassist_list_sessions":
            self._require_fields(arguments, set(), set())
            return self.service.list_sessions()
        if name in {"audioassist_get_session", "audioassist_get_status"}:
            self._require_fields(arguments, {"job_id"}, {"job_id"})
            job_id = arguments["job_id"]
            if not isinstance(job_id, str):
                raise ValueError("job_id must be a string")
            method = self.service.get_session if name.endswith("get_session") else self.service.get_status
            return method(job_id)
        if name == "audioassist_recording_status":
            self._require_fields(arguments, set(), set())
            return request_desktop("GET", "/v1/recording", app_data_dir=self.app_data_dir)
        if name == "audioassist_get_live_transcript":
            self._require_fields(arguments, {"session_id", "after_seq", "limit"}, {"session_id"})
            if not isinstance(arguments["session_id"], str):
                raise ValueError("session_id must be a string")
            after_seq = arguments.get("after_seq", 0)
            limit = arguments.get("limit", 100)
            if type(after_seq) is not int or after_seq < 0:
                raise ValueError("after_seq must be a nonnegative integer")
            if type(limit) is not int or not 1 <= limit <= 500:
                raise ValueError("limit must be an integer from 1 to 500")
            return self.service.get_live_transcript(
                arguments["session_id"], after_seq=after_seq, limit=limit
            )
        action = name.removeprefix("audioassist_").removesuffix("_recording")
        if action == "start":
            self._require_fields(arguments, {"capture_mode", "model_id"}, {"capture_mode"})
            if arguments["capture_mode"] not in {"mic", "system", "mix"}:
                raise ValueError("capture_mode must be mic, system, or mix")
            model_id = arguments.get("model_id")
            validate_model_id(model_id)
            return request_desktop(
                "POST", "/v1/recording/start", arguments, app_data_dir=self.app_data_dir
            )
        self._require_fields(arguments, set(), set())
        return request_desktop(
            "POST", f"/v1/recording/{action}", {}, app_data_dir=self.app_data_dir
        )

    @staticmethod
    def _require_fields(arguments: dict[str, Any], allowed: set[str], required: set[str]) -> None:
        if set(arguments) - allowed or not required.issubset(arguments):
            raise ValueError("Invalid tool arguments")


def run_stream(server: MCPServer, input_stream: TextIO, output_stream: TextIO) -> None:
    """Process newline-delimited JSON-RPC until the MCP client closes stdin."""
    for line in input_stream:
        if not line.strip():
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            response = server._rpc_error(None, -32700, "Parse error")
        else:
            response = server.handle(message)
        if response is not None:
            output_stream.write(json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n")
            output_stream.flush()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="audioassist-mcp")
    parser.add_argument("--output-dir")
    parser.add_argument("--config")
    parser.add_argument("--app-data-dir")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    output_dir, config_path, default_app_data = resolve_runtime_paths(
        args.output_dir, args.config
    )
    server = MCPServer(
        AudioAssistService(output_dir, config_path),
        app_data_dir=args.app_data_dir or default_app_data,
    )
    run_stream(server, sys.stdin, sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
