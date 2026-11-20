# R03-a CLI Contract

Status: synchronous CLI implemented on 2026-09-05; bounded read-only server added by
R03-B2 on 2026-09-06.

## Installation and invocation

From a development checkout:

```bash
bash scripts/bootstrap.sh
.venv/bin/audioassist --help
```

The bootstrap command installs the checkout and all locked runtime/test/build
dependencies into the repository `.venv`. Direct module invocation uses the
same interpreter:

```bash
.venv/bin/python -m src.cli --help
```

Global options must precede the command:

```text
audioassist [--output-dir PATH] [--config PATH] [--json] COMMAND
audioassist [--json] --version
```

`--version` reports the release version, runtime mode, and actual source or `.app`
path. Use it before CLI tests to distinguish repository, global-shim, `dist`, and
installed `/Applications` runs. Its JSON data includes `version`, `mode`, `app_path`,
`source_root`, `entrypoint`, and `python_executable`.

If `--output-dir` is omitted, the CLI uses `storage.output_dir` from the AudioAssist
configuration when it points to an existing directory, otherwise the platform default.

## Commands

```bash
audioassist sessions list
audioassist sessions show JOB_ID
audioassist status JOB_ID
audioassist recording status
audioassist recording transcript SESSION_ID [--after-seq N] [--limit 1..500]
audioassist recording transcript --active [--after-seq N] [--limit 1..500]
audioassist transcribe FILE [--engine qwen|whisper] [--model-id ID]
                          [--qwen-backend auto|pytorch-cpu|pytorch-mps|mlx|pytorch-cuda]
audioassist summarize JOB_ID [--prompt TEXT]
audioassist serve --read-only [--port PORT]
```

`transcribe` is synchronous: the process remains alive until the transcript is complete or
fails. Progress is written to stderr, so stdout remains safe for automation. Checkpoint data
continues to support recovery after an interruption; background submission is not part of
R03-a.

`recording status` reads the visible desktop app's recording state through its existing
authenticated loopback API and reports the active `session_id`. `recording transcript`
reads a **provisional live ASR draft** from that session's output directory; it does not
start, stop, pause, or alter recording. `--active` resolves the session id from the
visible app; an explicit `SESSION_ID` can also read the draft after the app closes.
The command does not require recording permissions because it only reads saved text.
It must use the same configured `--output-dir` as the desktop app.

Each recognized utterance has a stable `seq`, `start`, `end`, and `text`. `--after-seq`
returns only later utterances and `--limit` bounds one response (default 100). The JSON
data also contains `session_id`, `provisional: true`, `next_cursor`, and `has_more`.
`has_more` describes pagination only. `draft_status` is `incomplete` during recording
or after an interrupted writer, `failed` after a detected queue failure, `closed`
after a clean stop, or `missing` when no draft file exists. `draft_complete` is true
only for a cleanly closed journal; it does **not** mean the speaker-labelled final
transcript is ready. An incomplete page includes `draft_warning`. When `--active`
is used, `live_writer` reports accepted and persisted sequence numbers from the app;
a write failure returns `live_transcript_unavailable` instead of a silent partial
summary. An explicit `SESSION_ID` read also checks the running app's writer health
when available; matching writer failures are reported as `failed`. If the app is
unavailable and there is no clean close marker, the draft remains `incomplete` and
must not be treated as a complete meeting snapshot. A damaged/unreadable journal
uses exit code 5, not argument-error code 2.
For example, an agent can request a snapshot during a meeting, then pass the returned
`next_cursor` to the next call. Empty `segments` means no new utterance has finished
recognition yet, not that the meeting was silent. The draft has no speaker labels and
is never a replacement for the final diarized transcript. The visible app continues
to own capture and its macOS permissions.

Token Plan recordings add an optional `live_asr` object to recording status and
draft pages. `provider` is `token-plan`, `closed` indicates the cloud worker
finished, `error` is the latest sanitized error code, and `processed_through`
is the end of the latest handled batch in recording seconds (null when journal
persistence is unconfirmed). `missing_count` counts failed or dropped audio
batches/utterances, and `missing_ranges` lists merged `[start, end]` ranges in
recording seconds. When `missing_ranges_truncated` is true, the list retains only
the most recent ranges. `draft_status: closed` still describes the journal.
Cloud gaps and unconfirmed cloud coverage set `draft_complete` to false and
leave saved utterances readable. The same fields are returned by the MCP draft
tool. The desktop selector enables cloud mode; CLI/MCP recording reads do not
start an upload or require a Token Plan key.

`--qwen-backend` selects the inference backend for one Qwen job without changing the
saved desktop preference. An explicit command-line value takes precedence over the
`AUDIOASSIST_QWEN_BACKEND` environment variable and the saved preference. Without the
flag, the environment variable takes precedence over the saved preference, then `auto`.
The option is rejected for Whisper models. The successful JSON response includes the
selected `qwen_backend`; progress on stderr reports the resolved backend when `auto` is
used. Jobs resumed from a checkpoint retain the backend recorded by that checkpoint.

`summarize` uses the existing OpenAI-compatible settings in `config.json` and saves the
result as a new summary version.

`serve --read-only` is an explicit foreground loopback HTTP process. It exposes the
session read routes in [the local HTTP/MCP contract](http-mcp-contract.md), writes its
authenticated discovery record beside `config.json`, and cleans it up on SIGINT or
SIGTERM. It does not daemonize, transcribe, summarize, or control recording. The visible
desktop app owns the recording-capable server.

The separate `audioassist-mcp` executable speaks newline-delimited MCP JSON-RPC over
stdin/stdout. Its session tools read the same service directly; recording tools require
the visible desktop app.

## JSON response

With `--json`, stdout contains exactly one JSON object:

```json
{"ok":true,"data":{},"error":null}
```

Errors use the same envelope:

```json
{"ok":false,"data":null,"error":{"code":"session_not_found","message":"Session not found: example"}}
```

Argument errors also use this envelope when `--json` is present. Direct output from model
libraries is redirected to stderr so it cannot corrupt stdout. A missing Python inference
package reports `dependency_not_ready` with exit code 4.

Fields may be added to `data` compatibly. Removing or renaming existing fields, changing
their types, or changing exit-code meaning requires a documented contract revision.

Transcript and session `language` fields use canonical short codes such as `zh`, `en`,
`yue`, `ja`, and `ko`. Substantial Chinese/English code-switching is reported as
`mixed`. Full language names and common ISO variants from inference libraries are
normalized at the shared service boundary. Legacy transcript files remain readable and
are normalized in API responses without rewriting the stored file.

## Exit codes

| Code | Meaning |
|---:|---|
| 0 | Success |
| 2 | Invalid arguments or identifier |
| 3 | Input file or session not found |
| 4 | Required configuration or local model is not ready |
| 5 | Processing failed |
