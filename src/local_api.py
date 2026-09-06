"""Authenticated loopback HTTP adapter for AudioAssist resources and recording."""
from __future__ import annotations

import datetime as dt
import hmac
import http.client
import json
import logging
import os
import secrets
import tempfile
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Protocol
from urllib.parse import unquote, urlsplit

from platformdirs import user_data_dir

from .service import (
    AudioAssistError,
    AudioAssistService,
    InvalidJobIdError,
    SessionNotFoundError,
)

logger = logging.getLogger(__name__)

SCHEMA = "audioassist-local-api/v1"
HOST = "127.0.0.1"
MAX_BODY_BYTES = 64 * 1024
MAX_WORKERS = 8
DISCOVERY_FILENAME = "local-api.json"
LOCK_FILENAME = "local-api.lock"
CAPTURE_MODES = frozenset({"mic", "system", "mix"})


def validate_model_id(model_id: Any) -> None:
    if model_id is None:
        return
    if not isinstance(model_id, str):
        raise LocalAPIError("invalid_request", "model_id must be a string or null", 400)
    from .model_manager import CATALOG

    if model_id not in {model.id for model in CATALOG}:
        raise LocalAPIError("invalid_request", "Unknown model_id", 400)


class LocalAPIError(RuntimeError):
    """Stable local API failure with an HTTP status mapping."""

    def __init__(self, code: str, message: str, status: int) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


def success(data: Any) -> dict[str, Any]:
    return {"ok": True, "data": data, "error": None}


def failure(code: str, message: str) -> dict[str, Any]:
    return {
        "ok": False,
        "data": None,
        "error": {"code": code, "message": message},
    }


@dataclass(frozen=True)
class DiscoveryRecord:
    schema: str
    instance_id: str
    pid: int
    mode: str
    host: str
    port: int
    token: str
    started_at: str


def local_api_paths(app_data_dir: str | None = None) -> tuple[str, str]:
    directory = app_data_dir or user_data_dir("TranscribeApp", appauthor=False)
    return (
        os.path.join(directory, LOCK_FILENAME),
        os.path.join(directory, DISCOVERY_FILENAME),
    )


def _validate_discovery(data: Any) -> DiscoveryRecord:
    if not isinstance(data, dict):
        raise ValueError("Discovery record must be an object")
    required = {
        "schema", "instance_id", "pid", "mode", "host", "port", "token", "started_at"
    }
    if set(data) != required:
        raise ValueError("Discovery record fields are invalid")
    if data["schema"] != SCHEMA or data["mode"] not in {"desktop", "read-only"}:
        raise ValueError("Discovery schema or mode is unsupported")
    if data["host"] != HOST:
        raise ValueError("Discovery host is not loopback")
    if not isinstance(data["pid"], int) or data["pid"] <= 0:
        raise ValueError("Discovery PID is invalid")
    if not isinstance(data["port"], int) or not 1 <= data["port"] <= 65535:
        raise ValueError("Discovery port is invalid")
    if not isinstance(data["token"], str) or len(data["token"]) < 43:
        raise ValueError("Discovery token is invalid")
    uuid.UUID(str(data["instance_id"]))
    return DiscoveryRecord(**{field: data[field] for field in required})


def read_discovery(app_data_dir: str | None = None) -> DiscoveryRecord:
    _, path = local_api_paths(app_data_dir)
    try:
        with open(path, encoding="utf-8") as file:
            return _validate_discovery(json.load(file))
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as error:
        raise LocalAPIError("app_unavailable", "AudioAssist local API is unavailable", 503) from error


def _write_discovery(path: str, record: DiscoveryRecord) -> None:
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".local-api-", suffix=".json", dir=directory)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump(asdict(record), file, ensure_ascii=False, separators=(",", ":"))
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _remove_owned_discovery(path: str, instance_id: str) -> None:
    try:
        with open(path, encoding="utf-8") as file:
            current = json.load(file)
        if current.get("instance_id") == instance_id:
            os.unlink(path)
    except (OSError, ValueError, TypeError, AttributeError):
        return


class SingleInstanceLock:
    """Cross-process advisory lock held for the local server lifetime."""

    def __init__(self, path: str) -> None:
        self.path = path
        self._file = None

    def acquire(self) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        file = open(self.path, "a+b")
        os.chmod(self.path, 0o600)
        try:
            if os.name == "nt":
                import msvcrt

                if os.path.getsize(self.path) == 0:
                    file.write(b"0")
                    file.flush()
                file.seek(0)
                msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError) as error:
            file.close()
            raise LocalAPIError(
                "service_already_running", "Another AudioAssist local API is running", 409
            ) from error
        self._file = file

    def release(self) -> None:
        file, self._file = self._file, None
        if file is None:
            return
        try:
            if os.name == "nt":
                import msvcrt

                file.seek(0)
                msvcrt.locking(file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(file.fileno(), fcntl.LOCK_UN)
        finally:
            file.close()


class RecordingBackend(Protocol):
    def get_recording_status(self) -> dict[str, Any]: ...
    def preflight_capture(self, mode: str) -> dict[str, Any]: ...
    def start_realtime(self, options: dict[str, Any]) -> dict[str, Any]: ...
    def pause_realtime(self) -> dict[str, Any]: ...
    def resume_realtime(self) -> dict[str, Any]: ...
    def stop_realtime(self) -> dict[str, Any]: ...


class DesktopRecordingController:
    """Validate and serialize recording transitions into the desktop API."""

    _ALLOWED = {
        "start": {"idle", "error"},
        "pause": {"recording"},
        "resume": {"paused"},
        "stop": {"starting", "recording", "pausing", "paused", "resuming", "error"},
    }

    def __init__(self, backend: RecordingBackend) -> None:
        self.backend = backend
        self._mutation_lock = threading.Lock()

    def status(self) -> dict[str, Any]:
        return self.backend.get_recording_status()

    def transition(self, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not self._mutation_lock.acquire(blocking=False):
            raise LocalAPIError(
                "operation_in_progress", "Another recording operation is in progress", 409
            )
        try:
            state = self.status().get("state", "idle")
            if action not in self._ALLOWED or state not in self._ALLOWED[action]:
                raise LocalAPIError(
                    "recording_state_conflict",
                    f"Cannot {action} recording while state is {state}",
                    409,
                )

            if action == "start":
                capture_mode = payload["capture_mode"]
                preflight = self.backend.preflight_capture(capture_mode)
                missing_permissions = preflight.get("missing_permissions", [])
                if not preflight.get("supported") or missing_permissions:
                    message = preflight.get("message") or preflight.get("reason")
                    if not message and missing_permissions:
                        permission_reasons = []
                        for permission in missing_permissions:
                            status_key = f"{permission}_permission"
                            status = preflight.get(status_key, "required")
                            permission_reasons.append(f"{status_key}_{status}")
                        message = ",".join(permission_reasons)
                    message = message or "Capture unavailable"
                    raise LocalAPIError("capture_unavailable", str(message), 503)
                result = self.backend.start_realtime({
                    "capture_mode": capture_mode,
                    "model_id": payload.get("model_id"),
                })
            else:
                result = getattr(self.backend, f"{action}_realtime")()

            if result.get("status") == "permission_required":
                missing = result.get("missing_permissions", ["unknown"])
                raise LocalAPIError(
                    "capture_unavailable", f"permissions_required:{','.join(missing)}", 503
                )
            if result.get("status") in {"already_running", "not_running"}:
                raise LocalAPIError(
                    "recording_state_conflict",
                    f"Desktop rejected recording transition: {result['status']}",
                    409,
                )
            return {"accepted": action, "recording": self.status()}
        finally:
            self._mutation_lock.release()


class LocalAPIApplication:
    """Pure request dispatcher used by the HTTP handler and focused tests."""

    def __init__(
        self,
        service: AudioAssistService,
        *,
        token: str,
        port: int,
        mode: str,
        instance_id: str,
        recording: DesktopRecordingController | None = None,
        app_version: str = "2.0.0",
    ) -> None:
        self.service = service
        self.token = token
        self.port = port
        self.mode = mode
        self.instance_id = instance_id
        self.recording = recording
        self.app_version = app_version

    def dispatch(
        self,
        method: str,
        target: str,
        headers: dict[str, str],
        body: bytes,
        peer: str,
    ) -> tuple[int, dict[str, Any]]:
        try:
            self._authorize(headers, peer)
            if len(body) > MAX_BODY_BYTES:
                raise LocalAPIError("request_too_large", "Request body exceeds 64 KiB", 413)
            parsed = urlsplit(target)
            if parsed.query or parsed.fragment:
                raise LocalAPIError("invalid_request", "Query strings are not supported", 400)
            path = parsed.path.rstrip("/") or "/"
            known = self._known_path(path)
            allowed_method = "POST" if path.startswith("/v1/recording/") else "GET"
            if known and method != allowed_method:
                raise LocalAPIError("method_not_allowed", "Method not allowed", 405)

            if method == "GET":
                return 200, success(self._get(path))
            if method == "POST":
                payload = self._parse_body(headers, body)
                return 202, success(self._post(path, payload))
            raise LocalAPIError("method_not_allowed", "Method not allowed", 405)
        except LocalAPIError as error:
            return error.status, failure(error.code, str(error))
        except SessionNotFoundError as error:
            return 404, failure(error.code, str(error))
        except InvalidJobIdError as error:
            return 400, failure("invalid_request", str(error))
        except AudioAssistError as error:
            return 400, failure(error.code, str(error))
        except Exception:
            logger.exception("Unhandled local API request failure")
            return 500, failure("internal_error", "Internal server error")

    def _authorize(self, headers: dict[str, str], peer: str) -> None:
        if peer != HOST:
            raise LocalAPIError("unauthorized", "Loopback access only", 401)
        expected_hosts = {f"{HOST}:{self.port}", f"localhost:{self.port}"}
        if headers.get("host", "").lower() not in expected_hosts:
            raise LocalAPIError("unauthorized", "Invalid Host header", 401)
        authorization = headers.get("authorization", "")
        expected = f"Bearer {self.token}"
        if not hmac.compare_digest(authorization, expected):
            raise LocalAPIError("unauthorized", "Invalid bearer token", 401)

    @staticmethod
    def _known_path(path: str) -> bool:
        if path in {"/v1/health", "/v1/sessions", "/v1/recording"}:
            return True
        if path in {
            "/v1/recording/start", "/v1/recording/pause",
            "/v1/recording/resume", "/v1/recording/stop",
        }:
            return True
        parts = path.split("/")
        return len(parts) in {4, 5} and parts[1:3] == ["v1", "sessions"]

    def _get(self, path: str) -> Any:
        if path == "/v1/health":
            return {
                "schema": SCHEMA,
                "version": self.app_version,
                "mode": self.mode,
                "instance_id": self.instance_id,
            }
        if path == "/v1/sessions":
            return self.service.list_sessions()
        if path == "/v1/recording":
            return self._recording_controller().status()
        parts = path.split("/")
        if len(parts) == 4 and parts[1:3] == ["v1", "sessions"]:
            return self.service.get_session(unquote(parts[3]))
        if len(parts) == 5 and parts[1:3] == ["v1", "sessions"] and parts[4] == "status":
            return self.service.get_status(unquote(parts[3]))
        raise LocalAPIError("invalid_request", "Route not found", 404)

    def _post(self, path: str, payload: dict[str, Any]) -> Any:
        action = path.removeprefix("/v1/recording/")
        if action not in {"start", "pause", "resume", "stop"}:
            raise LocalAPIError("invalid_request", "Route not found", 404)
        if action == "start":
            allowed = {"capture_mode", "model_id"}
            if set(payload) - allowed or "capture_mode" not in payload:
                raise LocalAPIError("invalid_request", "Invalid recording start fields", 400)
            if payload["capture_mode"] not in CAPTURE_MODES:
                raise LocalAPIError("invalid_request", "Invalid capture_mode", 400)
            validate_model_id(payload.get("model_id"))
        elif payload:
            raise LocalAPIError("invalid_request", f"{action} does not accept fields", 400)
        return self._recording_controller().transition(action, payload)

    def _recording_controller(self) -> DesktopRecordingController:
        if self.mode != "desktop" or self.recording is None:
            raise LocalAPIError("app_unavailable", "Visible AudioAssist app is required", 503)
        return self.recording

    @staticmethod
    def _parse_body(headers: dict[str, str], body: bytes) -> dict[str, Any]:
        if not body:
            return {}
        content_type = headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            raise LocalAPIError("invalid_request", "Content-Type must be application/json", 400)
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise LocalAPIError("invalid_request", "Request body must be valid JSON", 400) from error
        if not isinstance(payload, dict):
            raise LocalAPIError("invalid_request", "Request body must be an object", 400)
        return payload


class _BoundedHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, *args, **kwargs) -> None:
        self._worker_slots = threading.BoundedSemaphore(MAX_WORKERS)
        super().__init__(*args, **kwargs)

    def process_request(self, request, client_address) -> None:
        self._worker_slots.acquire()
        try:
            super().process_request(request, client_address)
        except Exception:
            self._worker_slots.release()
            raise

    def process_request_thread(self, request, client_address) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._worker_slots.release()


class _RequestHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "AudioAssistLocalAPI/1"

    def do_GET(self) -> None:  # noqa: N802
        self._handle("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._handle("POST")

    def do_PUT(self) -> None:  # noqa: N802
        self._handle("PUT")

    def do_DELETE(self) -> None:  # noqa: N802
        self._handle("DELETE")

    def _handle(self, method: str) -> None:
        self._request_started = time.monotonic()
        request_id = uuid.uuid4().hex
        if self.headers.get("Transfer-Encoding"):
            self.close_connection = True
            self._send(400, failure("invalid_request", "Transfer-Encoding is not supported"), request_id)
            return
        try:
            raw_length = self.headers.get("Content-Length", "0")
            length = int(raw_length)
            if length < 0:
                raise ValueError
        except ValueError:
            self._send(400, failure("invalid_request", "Invalid Content-Length"), request_id)
            return
        if length > MAX_BODY_BYTES:
            self.close_connection = True
            self._send(413, failure("request_too_large", "Request body exceeds 64 KiB"), request_id)
            return
        body = self.rfile.read(length) if length else b""
        headers = {key.lower(): value for key, value in self.headers.items()}
        application: LocalAPIApplication = self.server.application  # type: ignore[attr-defined]
        status, payload = application.dispatch(
            method, self.path, headers, body, self.client_address[0]
        )
        self._send(status, payload, request_id)

    def _send(self, status: int, payload: dict[str, Any], request_id: str) -> None:
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("X-Request-ID", request_id)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(encoded)
        logger.info(
            "local-api method=%s route=%s status=%s request_id=%s duration_ms=%.1f",
            self.command,
            urlsplit(self.path).path,
            status,
            request_id,
            (time.monotonic() - self._request_started) * 1000,
        )

    def log_message(self, format: str, *args) -> None:
        return


class LocalAPIServer:
    """Own the local HTTP listener, discovery record, and process lock."""

    def __init__(
        self,
        service: AudioAssistService,
        *,
        mode: str,
        app_data_dir: str | None = None,
        port: int = 0,
        recording: DesktopRecordingController | None = None,
        app_version: str = "2.0.0",
    ) -> None:
        if mode not in {"desktop", "read-only"}:
            raise ValueError("mode must be desktop or read-only")
        if mode == "desktop" and recording is None:
            raise ValueError("desktop mode requires a recording controller")
        self.service = service
        self.mode = mode
        self.app_data_dir = app_data_dir
        self.requested_port = port
        self.recording = recording
        self.app_version = app_version
        self.instance_id = str(uuid.uuid4())
        self.token = secrets.token_urlsafe(32)
        lock_path, self.discovery_path = local_api_paths(app_data_dir)
        self._lock = SingleInstanceLock(lock_path)
        self._httpd: _BoundedHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._lifecycle_lock = threading.RLock()
        self.record: DiscoveryRecord | None = None

    def start(self) -> DiscoveryRecord:
        with self._lifecycle_lock:
            if self._httpd is not None:
                return self.record  # type: ignore[return-value]
            self._lock.acquire()
            try:
                try:
                    httpd = _BoundedHTTPServer((HOST, self.requested_port), _RequestHandler)
                except OSError as error:
                    raise LocalAPIError("port_unavailable", f"Cannot bind local API port: {error}", 409) from error
                port = int(httpd.server_address[1])
                application = LocalAPIApplication(
                    self.service,
                    token=self.token,
                    port=port,
                    mode=self.mode,
                    instance_id=self.instance_id,
                    recording=self.recording,
                    app_version=self.app_version,
                )
                httpd.application = application  # type: ignore[attr-defined]
                record = DiscoveryRecord(
                    schema=SCHEMA,
                    instance_id=self.instance_id,
                    pid=os.getpid(),
                    mode=self.mode,
                    host=HOST,
                    port=port,
                    token=self.token,
                    started_at=dt.datetime.now(dt.timezone.utc).isoformat(),
                )
                self._httpd = httpd
                _write_discovery(self.discovery_path, record)
                thread = threading.Thread(
                    target=httpd.serve_forever,
                    name="audioassist-local-api",
                    daemon=True,
                )
                self._thread = thread
                self.record = record
                thread.start()
                return record
            except Exception:
                if self._httpd is not None:
                    self._httpd.server_close()
                self._httpd = None
                self._lock.release()
                raise

    def wait(self) -> None:
        thread = self._thread
        if thread is not None:
            thread.join()

    def stop(self) -> None:
        with self._lifecycle_lock:
            httpd, self._httpd = self._httpd, None
            thread, self._thread = self._thread, None
            try:
                if httpd is not None:
                    httpd.shutdown()
                    httpd.server_close()
                if thread is not None and thread is not threading.current_thread():
                    thread.join(timeout=5)
            finally:
                _remove_owned_discovery(self.discovery_path, self.instance_id)
                self._lock.release()


def _pid_is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def request_desktop(
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    *,
    app_data_dir: str | None = None,
    timeout: float = 2.0,
) -> Any:
    """Call a discovered authenticated desktop server or raise app_unavailable."""
    record = read_discovery(app_data_dir)
    if record.mode != "desktop" or not _pid_is_alive(record.pid):
        raise LocalAPIError("app_unavailable", "Visible AudioAssist app is required", 503)
    def request_once(request_method: str, request_path: str, request_payload: dict[str, Any] | None) -> Any:
        body = None if request_payload is None else json.dumps(request_payload, separators=(",", ":")).encode("utf-8")
        headers = {"Authorization": f"Bearer {record.token}"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        connection = http.client.HTTPConnection(record.host, record.port, timeout=timeout)
        try:
            connection.request(request_method, request_path, body=body, headers=headers)
            response = connection.getresponse()
            parsed = json.loads(response.read().decode("utf-8"))
        except (OSError, http.client.HTTPException, ValueError, json.JSONDecodeError) as error:
            raise LocalAPIError("app_unavailable", "AudioAssist desktop API is unavailable", 503) from error
        finally:
            connection.close()
        if not isinstance(parsed, dict) or not parsed.get("ok"):
            error = parsed.get("error", {}) if isinstance(parsed, dict) else {}
            raise LocalAPIError(
                str(error.get("code", "app_unavailable")),
                str(error.get("message", "AudioAssist desktop API request failed")),
                response.status,
            )
        return parsed.get("data")

    health = request_once("GET", "/v1/health", None)
    if not isinstance(health, dict) or health.get("instance_id") != record.instance_id:
        raise LocalAPIError("app_unavailable", "AudioAssist desktop API identity changed", 503)
    if method == "GET" and path == "/v1/health":
        return health
    return request_once(method, path, payload)
