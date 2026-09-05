# R03-a CLI Contract

Status: initial local CLI implemented on 2026-09-05.

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
```

If `--output-dir` is omitted, the CLI uses `storage.output_dir` from the AudioAssist
configuration when it points to an existing directory, otherwise the platform default.

## Commands

```bash
audioassist sessions list
audioassist sessions show JOB_ID
audioassist status JOB_ID
audioassist transcribe FILE [--engine qwen|whisper] [--model-id ID]
audioassist summarize JOB_ID [--prompt TEXT]
```

`transcribe` is synchronous: the process remains alive until the transcript is complete or
fails. Progress is written to stderr, so stdout remains safe for automation. Checkpoint data
continues to support recovery after an interruption; background submission is not part of
R03-a.

`summarize` uses the existing OpenAI-compatible settings in `config.json` and saves the
result as a new summary version.

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

## Exit codes

| Code | Meaning |
|---:|---|
| 0 | Success |
| 2 | Invalid arguments or identifier |
| 3 | Input file or session not found |
| 4 | Required configuration or local model is not ready |
| 5 | Processing failed |
