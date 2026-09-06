"""Contract and integration tests for the authenticated local HTTP adapter."""
from __future__ import annotations

import http.client
import json
import os
import socket
import stat
import subprocess
import sys
import threading
import time

import pytest

from src.local_api import (
    DISCOVERY_FILENAME,
    HOST,
    LocalAPIApplication,
    LocalAPIError,
    LocalAPIServer,
    DesktopRecordingController,
    SingleInstanceLock,
    _remove_owned_discovery,
    failure,
    read_discovery,
    request_desktop,
    success,
)
from src.service import AudioAssistService


def _write_transcript(output_dir, job_id="job-1"):
    session_dir = output_dir / "meetings" / job_id
    session_dir.mkdir(parents=True)
    (session_dir / "transcript.json").write_text(json.dumps({
        "job_id": job_id,
        "filename": "meeting.wav",
        "audio": "/audio/meeting.wav",
        "language": "en",
        "created_at": "2026-09-06T12:00:00",
        "segments": [],
    }), encoding="utf-8")


class FakeRecordingBackend:
    def __init__(self):
        self.state = "idle"
        self.calls = []
        self.preflight = {
            "supported": True,
            "microphone_permission": "authorized",
        }

    def get_recording_status(self):
        return {
            "state": self.state,
            "session_id": None,
            "capture_mode": None,
            "started_at": None,
            "error": None,
        }

    def preflight_capture(self, mode):
        self.calls.append(("preflight", mode))
        return self.preflight

    def start_realtime(self, options):
        self.calls.append(("start", options))
        self.state = "starting"
        return {"status": "started"}

    def pause_realtime(self):
        self.calls.append(("pause", None))
        self.state = "pausing"
        return {"status": "pausing"}

    def resume_realtime(self):
        self.calls.append(("resume", None))
        self.state = "resuming"
        return {"status": "resuming"}

    def stop_realtime(self):
        self.calls.append(("stop", None))
        self.state = "stopping"
        return {"status": "stopped"}


@pytest.fixture
def application(tmp_path):
    _write_transcript(tmp_path)
    backend = FakeRecordingBackend()
    app = LocalAPIApplication(
        AudioAssistService(str(tmp_path)),
        token="t" * 43,
        port=43210,
        mode="desktop",
        instance_id="instance",
        recording=DesktopRecordingController(backend),
    )
    headers = {"host": "127.0.0.1:43210", "authorization": "Bearer " + "t" * 43}
    return app, backend, headers


def test_envelopes_match_cli_shape():
    assert success({"x": 1}) == {"ok": True, "data": {"x": 1}, "error": None}
    assert failure("bad", "no") == {
        "ok": False, "data": None, "error": {"code": "bad", "message": "no"}
    }


@pytest.mark.parametrize(
    ("headers", "peer"),
    [
        ({"host": "127.0.0.1:43210"}, HOST),
        ({"host": "127.0.0.1:43210", "authorization": "Bearer wrong"}, HOST),
        ({"host": "evil.test:43210", "authorization": "Bearer " + "t" * 43}, HOST),
        ({"host": "127.0.0.1:43210", "authorization": "Bearer " + "t" * 43}, "10.0.0.2"),
    ],
)
def test_auth_host_and_peer_are_required(application, headers, peer):
    app, _, _ = application
    status, payload = app.dispatch("GET", "/v1/health", headers, b"", peer)
    assert status == 401
    assert payload["error"]["code"] == "unauthorized"


def test_resource_routes_use_shared_service(application):
    app, _, headers = application
    status, listed = app.dispatch("GET", "/v1/sessions", headers, b"", HOST)
    shown_status, shown = app.dispatch("GET", "/v1/sessions/job-1", headers, b"", HOST)
    state_status, state = app.dispatch(
        "GET", "/v1/sessions/job-1/status", headers, b"", HOST
    )
    assert status == shown_status == state_status == 200
    assert listed["data"][0]["job_id"] == "job-1"
    assert shown["data"]["session"]["job_id"] == "job-1"
    assert state["data"]["status"] == "completed"


@pytest.mark.parametrize("job_id", ["..", "%2E%2E", "%2Ftmp", "bad%2Fid"])
def test_session_route_rejects_path_traversal(application, job_id):
    app, _, headers = application
    status, payload = app.dispatch(
        "GET", f"/v1/sessions/{job_id}", headers, b"", HOST
    )
    assert status == 400
    assert payload["error"]["code"] == "invalid_request"


def test_missing_session_has_stable_404(application):
    app, _, headers = application
    status, payload = app.dispatch("GET", "/v1/sessions/missing", headers, b"", HOST)
    assert status == 404
    assert payload["error"]["code"] == "session_not_found"


def test_method_body_and_query_validation(application):
    app, _, headers = application
    status, payload = app.dispatch("POST", "/v1/sessions", headers, b"{}", HOST)
    assert (status, payload["error"]["code"]) == (405, "method_not_allowed")

    status, payload = app.dispatch("GET", "/v1/sessions?all=true", headers, b"", HOST)
    assert (status, payload["error"]["code"]) == (400, "invalid_request")

    status, payload = app.dispatch(
        "POST", "/v1/recording/stop", headers, b"{" + b"x" * (64 * 1024), HOST
    )
    assert (status, payload["error"]["code"]) == (413, "request_too_large")


def test_recording_start_validates_fields_and_preflight(application):
    app, backend, headers = application
    json_headers = {**headers, "content-type": "application/json"}

    status, payload = app.dispatch(
        "POST", "/v1/recording/start", json_headers,
        json.dumps({"capture_mode": "system", "model_id": None}).encode(), HOST,
    )
    assert status == 202
    assert payload["data"]["accepted"] == "start"
    assert backend.calls == [
        ("preflight", "system"),
        ("start", {"capture_mode": "system", "model_id": None}),
    ]

    backend.state = "idle"
    backend.calls.clear()
    backend.preflight = {
        "supported": True,
        "microphone_permission": "not_determined",
    }
    status, payload = app.dispatch(
        "POST", "/v1/recording/start", json_headers,
        json.dumps({"capture_mode": "mic", "model_id": None}).encode(), HOST,
    )
    assert (status, payload["error"]["code"]) == (503, "capture_unavailable")
    assert "microphone_permission_not_determined" in payload["error"]["message"]
    assert backend.calls == [("preflight", "mic")]
    assert backend.state == "idle"

    backend.state = "idle"
    backend.preflight = {
        "supported": True,
        "microphone_permission": "authorized",
    }
    status, payload = app.dispatch(
        "POST", "/v1/recording/start", json_headers,
        json.dumps({"capture_mode": "invalid"}).encode(), HOST,
    )
    assert (status, payload["error"]["code"]) == (400, "invalid_request")

    status, payload = app.dispatch(
        "POST", "/v1/recording/start", json_headers,
        json.dumps({"capture_mode": "mic", "model_id": "not-in-catalog"}).encode(), HOST,
    )
    assert (status, payload["error"]["code"]) == (400, "invalid_request")


def test_recording_transition_conflict_and_read_only_boundary(application, tmp_path):
    app, backend, headers = application
    backend.state = "paused"
    status, payload = app.dispatch("POST", "/v1/recording/pause", headers, b"", HOST)
    assert (status, payload["error"]["code"]) == (409, "recording_state_conflict")

    read_only = LocalAPIApplication(
        AudioAssistService(str(tmp_path)), token="t" * 43, port=43210,
        mode="read-only", instance_id="read-only",
    )
    status, payload = read_only.dispatch("GET", "/v1/recording", headers, b"", HOST)
    assert (status, payload["error"]["code"]) == (503, "app_unavailable")


def test_recording_can_stop_after_backend_enters_error(application):
    app, backend, headers = application
    backend.state = "error"
    status, payload = app.dispatch("POST", "/v1/recording/stop", headers, b"", HOST)
    assert status == 202
    assert payload["data"]["accepted"] == "stop"


def test_recording_mutations_are_serialized(application):
    app, _, headers = application
    controller = app.recording
    assert controller is not None
    assert controller._mutation_lock.acquire(blocking=False)
    try:
        status, payload = app.dispatch(
            "POST", "/v1/recording/start",
            {**headers, "content-type": "application/json"},
            b'{"capture_mode":"mic"}', HOST,
        )
    finally:
        controller._mutation_lock.release()
    assert (status, payload["error"]["code"]) == (409, "operation_in_progress")


def _request(record, path="/v1/health", token=None):
    connection = http.client.HTTPConnection(record.host, record.port, timeout=2)
    connection.request(
        "GET", path, headers={"Authorization": "Bearer " + (token or record.token)}
    )
    response = connection.getresponse()
    payload = json.loads(response.read().decode())
    connection.close()
    return response.status, payload


def test_server_discovery_permissions_http_and_cleanup(tmp_path):
    app_data = tmp_path / "app-data"
    output = tmp_path / "output"
    server = LocalAPIServer(
        AudioAssistService(str(output)), mode="read-only", app_data_dir=str(app_data)
    )
    record = server.start()
    try:
        discovered = read_discovery(str(app_data))
        assert discovered == record
        mode = stat.S_IMODE(os.stat(app_data / DISCOVERY_FILENAME).st_mode)
        assert mode == 0o600
        status, payload = _request(record)
        assert status == 200
        assert payload["data"]["mode"] == "read-only"
        assert _request(record, token="wrong")[0] == 401
    finally:
        server.stop()
    assert not (app_data / DISCOVERY_FILENAME).exists()


def test_single_instance_lock_contention_and_release(tmp_path):
    path = str(tmp_path / "local-api.lock")
    first = SingleInstanceLock(path)
    second = SingleInstanceLock(path)
    first.acquire()
    try:
        with pytest.raises(LocalAPIError, match="Another AudioAssist") as raised:
            second.acquire()
        assert raised.value.code == "service_already_running"
    finally:
        first.release()
    second.acquire()
    second.release()


def test_explicit_port_collision_is_reported(tmp_path):
    listener = socket.socket()
    listener.bind((HOST, 0))
    port = listener.getsockname()[1]
    server = LocalAPIServer(
        AudioAssistService(str(tmp_path / "output")), mode="read-only",
        app_data_dir=str(tmp_path / "app-data"), port=port,
    )
    try:
        with pytest.raises(LocalAPIError) as raised:
            server.start()
        assert raised.value.code == "port_unavailable"
    finally:
        server.stop()
        listener.close()


def test_discovery_cleanup_requires_matching_instance(tmp_path):
    path = tmp_path / DISCOVERY_FILENAME
    path.write_text(json.dumps({"instance_id": "new"}), encoding="utf-8")
    _remove_owned_discovery(str(path), "old")
    assert path.exists()
    _remove_owned_discovery(str(path), "new")
    assert not path.exists()


def test_request_desktop_requires_desktop_mode(tmp_path):
    server = LocalAPIServer(
        AudioAssistService(str(tmp_path / "output")), mode="read-only",
        app_data_dir=str(tmp_path / "app-data"),
    )
    server.start()
    try:
        with pytest.raises(LocalAPIError) as raised:
            request_desktop("GET", "/v1/health", app_data_dir=str(tmp_path / "app-data"))
        assert raised.value.code == "app_unavailable"
    finally:
        server.stop()


def test_request_desktop_calls_discovered_server(tmp_path):
    backend = FakeRecordingBackend()
    controller = DesktopRecordingController(backend)
    server = LocalAPIServer(
        AudioAssistService(str(tmp_path / "output")), mode="desktop",
        app_data_dir=str(tmp_path / "app-data"), recording=controller,
    )
    server.start()
    try:
        result = request_desktop(
            "POST", "/v1/recording/start", {"capture_mode": "mic", "model_id": None},
            app_data_dir=str(tmp_path / "app-data"),
        )
        assert result["accepted"] == "start"
        assert backend.calls[-1][0] == "start"
    finally:
        server.stop()


def test_read_only_cli_foreground_lifecycle(tmp_path):
    app_data = tmp_path / "app-data"
    output = tmp_path / "output"
    config = app_data / "config.json"
    process = subprocess.Popen(
        [
            sys.executable, "-m", "src.cli", "--json",
            "--output-dir", str(output), "--config", str(config),
            "serve", "--read-only",
        ],
        cwd=os.path.dirname(os.path.dirname(__file__)),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 5
        while not (app_data / DISCOVERY_FILENAME).exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        record = read_discovery(str(app_data))
        status, payload = _request(record, "/v1/sessions")
        assert status == 200
        assert payload["data"] == []
    finally:
        process.terminate()
        stdout, stderr = process.communicate(timeout=5)
    assert process.returncode == 0
    assert json.loads(stdout)["data"]["mode"] == "read-only"
    assert not (app_data / DISCOVERY_FILENAME).exists()
    assert "Traceback" not in stderr
