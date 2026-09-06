# R03-b Local HTTP and MCP Contract

Status: R03-B1 design approved; bounded R03-B2 implementation completed on 2026-09-06.

This document is the versioned contract for local HTTP resources, desktop recording
control, and the separate MCP adapter. It deliberately does **not** introduce a
daemon or a durable background transcription queue.

## Decision and scope

R03-B2 may proceed with these boundaries:

- The visible desktop process may host a small HTTP server in `desktop` mode.
- `audioassist serve --read-only` may host the same resource endpoints as an
  explicit foreground process while the desktop app is closed.
- The MCP adapter is a separate stdio process. It reads sessions through
  `AudioAssistService` and sends recording mutations only to a discovered live
  `desktop` server.
- Session discovery, transcript reads, status interpretation, and summary versions
  remain in `src/service.py`. HTTP and MCP are adapters, not second implementations.
- R03-B2 includes session resources and recording controls. File transcription,
  summary generation, search, and Q&A remain the synchronous CLI/current app paths;
  they are not presented as asynchronous HTTP jobs.

The restricted scope is the reason this design passes the lifecycle gate: there is
no orphanable inference worker, job queue, or false promise of cross-process recovery.

## Ownership model

| Component | Lifetime owner | Mutations | Behavior when desktop is closed |
|---|---|---|---|
| Desktop HTTP server | Visible AudioAssist process | Recording controls | Not running |
| Read-only HTTP server | User-invoked foreground CLI process | None | Session reads work |
| MCP adapter | MCP client/host through stdio | Recording controls via desktop HTTP | Reads work; recording returns `app_unavailable` |
| `AudioAssistService` | Calling process | Existing synchronous use cases | Reads durable files directly |

Neither the HTTP server nor MCP adapter launches or relaunches the desktop app. Neither
continues recording after the visible app exits. The desktop process remains the owner
of capture permissions, model instances, live audio handles, and UI notifications.

## Process lifecycle

### Desktop mode

1. `run.py` creates the `API` and PyWebView window.
2. After a window exists, the process acquires the local-API lock, binds
   `127.0.0.1` on an ephemeral port, and writes discovery metadata atomically.
3. HTTP requests run on a bounded server thread; recording mutations are serialized
   before calling the existing desktop API.
4. PyWebView close first stops the HTTP accept loop and removes discovery metadata
   owned by that instance. Existing desktop capture shutdown remains authoritative.

HTTP startup failure must not prevent the desktop UI from opening. It is logged with
an actionable reason. In particular, the app never steals another process's lock.

### Read-only mode

`audioassist serve --read-only [--port PORT]` is a foreground command:

- it acquires the same single-instance lock;
- it binds loopback only (`127.0.0.1`), with port `0` as the default;
- it writes discovery metadata with `mode: "read-only"`;
- `SIGINT`/`SIGTERM` stops accepting requests, drains current short reads, removes its
  own discovery record, releases the lock, and exits;
- it never forks, daemonizes, creates a launch agent, or starts an inference worker.

If the lock is already held, startup fails with `service_already_running`. If an
explicit port cannot bind, startup fails with `port_unavailable`; it does not silently
select a different port. An ephemeral-port request records the actual bound port.

### Single-instance lock and discovery

Both files live in the platform `TranscribeApp` data directory:

```text
local-api.lock
local-api.json
```

- The process holds a non-blocking OS advisory lock for its full server lifetime.
  Process death releases the lock automatically; the mere presence of the file is not
  treated as ownership.
- `local-api.json` is written through `os.replace` with mode `0600` and contains:

```json
{
  "schema": "audioassist-local-api/v1",
  "instance_id": "random UUID",
  "pid": 12345,
  "mode": "desktop",
  "host": "127.0.0.1",
  "port": 49152,
  "token": "256-bit URL-safe random bearer token",
  "started_at": "RFC3339 UTC timestamp"
}
```

- Shutdown removes discovery only when `instance_id` still matches, so an old process
  cannot delete a newer record.
- Clients treat malformed metadata, a dead PID, an unlocked lock, an unexpected mode,
  or a failed authenticated health request as unavailable. They never trust PID alone.

## Local security boundary

- Bind only the IPv4 loopback address. `0.0.0.0`, LAN interfaces, Unix wildcard binds,
  and remote configuration are out of scope.
- Every `/v1/*` request, including health, requires
  `Authorization: Bearer <token>` using constant-time comparison.
- Reject requests whose peer is not loopback or whose `Host` is not `127.0.0.1` or
  `localhost` with the active port. Do not emit permissive CORS headers.
- Accept `application/json` only for bodies, cap bodies at 64 KiB, reject duplicate or
  unknown control fields, and never evaluate paths supplied by callers.
- Discovery and token files are user-readable only. The trust boundary is the signed-in
  local OS user: another process running as that user can already read AudioAssist data.
- Log method, route, status, request ID, and duration, but never the bearer token,
  transcript content, summary content, or request body.

Loopback HTTP is intentionally not TLS: confidentiality comes from the OS-local network
boundary and bearer token. This API must not be exposed through port forwarding.

## HTTP schema v1

All replies use the CLI-compatible envelope and `Content-Type: application/json`:

```json
{"ok": true, "data": {}, "error": null}
```

```json
{"ok": false, "data": null, "error": {"code": "session_not_found", "message": "..."}}
```

Fields may be added compatibly inside `data`. Removing/renaming fields, changing field
types, route semantics, or error meaning requires `/v2`.

| Method | Route | Mode | Result |
|---|---|---|---|
| `GET` | `/v1/health` | both | schema, app version, mode, instance ID |
| `GET` | `/v1/sessions` | both | `AudioAssistService.list_sessions()` |
| `GET` | `/v1/sessions/{job_id}` | both | `AudioAssistService.get_session()` |
| `GET` | `/v1/sessions/{job_id}/status` | both | `AudioAssistService.get_status()` |
| `GET` | `/v1/recording` | desktop | current recording state |
| `POST` | `/v1/recording/start` | desktop | accept one start transition |
| `POST` | `/v1/recording/pause` | desktop | accept one pause transition |
| `POST` | `/v1/recording/resume` | desktop | accept one resume transition |
| `POST` | `/v1/recording/stop` | desktop | accept one stop transition |

`start` accepts exactly:

```json
{"capture_mode": "mic", "model_id": "qwen3-asr-1.7b"}
```

`capture_mode` is one of `mic`, `system`, or `mix`; `model_id` may be `null` or a
catalog ID. Other recording mutations require `{}` or an empty body. Successful
mutations return the accepted transition, not a claim that asynchronous hardware/model
work has completed. Clients observe `/v1/recording` for the resulting state.

### Recording state machine

```text
idle -> starting -> recording -> pausing -> paused -> resuming -> recording
                         \                              /
                          +---------> stopping --------+
                                         |
                                         v
                                        idle

starting/recording/pausing/paused/resuming/stopping -> error -> stop or next start
```

Only one recording mutation may be accepted at a time. An error with a still-owned
capture backend may be stopped; a start error that already released its backend may be
retried. Invalid transitions return
HTTP `409` with `recording_state_conflict`; a second mutation still executing returns
`operation_in_progress`. A start request performs the same system/mix preflight as the
UI. The state response contains only stable fields:

```json
{
  "state": "recording",
  "session_id": "UUID or null",
  "capture_mode": "mic or null",
  "started_at": "RFC3339 timestamp or null",
  "error": null
}
```

## Locking, cancellation, recovery, and shutdown

- HTTP resource reads rely on atomic session JSON and existing service locks. They do
  not acquire the desktop bridge lock.
- A dedicated recording-controller lock guards state transitions. It must not be held
  during model loading, audio processing, network I/O, or HTTP response writes.
- The existing transcription per-session lock remains authoritative for transcript
  mutation; HTTP v1 exposes no competing transcript write endpoint.
- Recording `stop` is the cancellation operation for an active/starting recording.
  It is idempotent only while a stop is already accepted; a stop from `idle` is a
  `recording_state_conflict`.
- Disconnecting an HTTP client does not cancel an accepted recording transition.
  Mutation responses are short acceptance responses, not long-running streams.
- Server shutdown stops HTTP acceptance only. In desktop mode the app's own close path
  stops live capture; the server must not independently race a second capture stop.
- After server or app crash, durable sessions/checkpoints are rediscovered by
  `AudioAssistService`. In-memory recording state is not reconstructed or advertised as
  active. Any completed WAV remains under its session directory for existing recovery
  and history behavior.
- Read-only mode performs no recovery writes. Stale discovery metadata is replaced only
  after the new process successfully acquires the OS lock.

## Stable error mapping

| HTTP | Code | Meaning |
|---:|---|---|
| 400 | `invalid_request` | Invalid JSON, unknown fields, invalid capture mode/model/job ID |
| 401 | `unauthorized` | Missing or incorrect bearer token |
| 404 | `session_not_found` | Durable session does not exist |
| 405 | `method_not_allowed` | Known route with wrong HTTP method |
| 409 | `service_already_running` | Server lock is held (startup/CLI error) |
| 409 | `recording_state_conflict` | Recording transition is invalid for current state |
| 409 | `operation_in_progress` | Another recording mutation is being accepted |
| 413 | `request_too_large` | Body exceeds 64 KiB |
| 503 | `app_unavailable` | Mutation requested without a live desktop-mode server |
| 503 | `capture_unavailable` | Permission, helper, model, or device preflight failed |
| 500 | `internal_error` | Unexpected failure; details are logged, not leaked |

Mic recording through HTTP/MCP never triggers the macOS permission dialog. The
desktop backend must already report `authorized` (or `not_applicable` off macOS),
otherwise `POST /v1/recording/start` returns `503 capture_unavailable` before a
Session, WAV, model, or PortAudio stream is created. First-use authorization is
completed in the visible app.

## MCP adapter contract

The separate executable uses MCP over stdin/stdout. Protocol messages are the only
stdout content; logs go to stderr. Initial tools are:

| Tool | Implementation |
|---|---|
| `audioassist_list_sessions` | Direct `AudioAssistService.list_sessions()` |
| `audioassist_get_session` | Direct `AudioAssistService.get_session(job_id)` |
| `audioassist_get_status` | Direct `AudioAssistService.get_status(job_id)` |
| `audioassist_recording_status` | Authenticated desktop HTTP request |
| `audioassist_start_recording` | Authenticated desktop HTTP request |
| `audioassist_pause_recording` | Authenticated desktop HTTP request |
| `audioassist_resume_recording` | Authenticated desktop HTTP request |
| `audioassist_stop_recording` | Authenticated desktop HTTP request |

The adapter never fabricates success when discovery or health validation fails. Read
tools do not require a running HTTP server. Recording tools require discovery mode
`desktop` and otherwise return the stable `app_unavailable` error.

## R03-B2 acceptance gates

- Unit tests: envelope/error mapping, route/method/body validation, path traversal,
  bearer/Host/peer checks, body cap, controller state transitions, mutation
  serialization, discovery permissions, stale/mismatched cleanup, lock contention,
  and graceful shutdown.
- Integration tests: desktop-mode HTTP session reads and mocked recording lifecycle;
  read-only foreground startup/read/shutdown; MCP stdio initialize/list/call; MCP reads
  with the app closed; MCP recording error without the app; MCP recording success with
  a discovered desktop server.
- Regression: `python -m pytest -q`, `python -m pytest tests/frontend -q`, CLI JSON
  stdout purity, local-only inference behavior, and frozen-import/package checks.
- Manual release gate: signed app binds loopback only, discovery is `0600`, recording
  start/stop works for mic/system/mix with permissions, app quit removes discovery, and
  no server survives the visible app process.

## Explicit non-goals for v1

- No daemon, launch agent, auto-start, remote/LAN access, WebSocket, SSE, or browser UI.
- No background HTTP transcription/summarization jobs and therefore no cross-process
  inference queue, resumable job lease, or cancel endpoint for such a queue.
- No HTTP transcript edits, model downloads/deletes, API-key configuration, arbitrary
  filesystem paths, shell execution, or unrestricted agent prompts.
- No promise that a live recording survives app/process/device failure.

## Protocol references

The stdio framing and tool result/error behavior follow the official MCP specifications:

- https://modelcontextprotocol.io/specification/2025-11-25/basic/transports
- https://modelcontextprotocol.io/specification/2025-06-18/server/tools
