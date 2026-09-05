# AudioAssist Development Rules

This file is the development contract for the repository. It replaces the former
permanent builder/researcher/reviewer worktree workflow.

## Working model

- `main` is the only permanent integration branch and checkout.
- Use one short-lived `codex/*` branch or worktree for a bounded medium/high-risk feature.
- A builder is not a mandatory permanent role. The task implementing a change owns its
  code, tests, documentation, and handoff until the acceptance criteria pass.
- Use a separate research task only when an external API, platform behavior, hardware,
  or architecture question is genuinely uncertain.
- Use an independent review task for security-sensitive, concurrency-heavy, storage-
  compatibility, packaging, or large cross-module changes. Review the exact commit that
  will be integrated.
- Remove or archive temporary tasks, worktrees, and branches after integration.

## Change discipline

- Inspect the current behavior and nearby tests before editing.
- Preserve user changes and unrelated dirty files.
- Keep UI adapters thin. Reusable business logic belongs under `src/` and must not import
  PyWebView, browser code, or other presentation-layer dependencies.
- Preserve the session-per-directory layout and legacy read compatibility documented in
  `README.dev.md`.
- Transcription must remain local-only at runtime: never auto-download a model during a
  transcription job.
- Agent-facing interfaces must keep stdout machine-readable, send progress to stderr,
  use documented exit codes, and avoid silent response-schema changes.
- Do not add a daemon or claim asynchronous cross-process jobs without an explicit
  lifecycle, locking, recovery, and cancellation design.

## Verification

- Add focused tests for every behavior change and run them while iterating.
- Before integration, run `python -m pytest -q`.
- Run `python -m pytest tests/frontend -q` for frontend changes.
- Real model, device permission, system-audio, and packaged-app checks follow
  `docs/release-checklist.md`; they are release gates rather than routine unit tests.
- Report the exact commands and results in the development handoff.

## R03 CLI

- The stable command contract is documented in `docs/cli-contract.md`.
- The CLI and desktop API share `src/service.py`; do not duplicate session discovery,
  status interpretation, transcription persistence, or summary-version behavior.
- R03-a commands are synchronous. Background submission belongs to a later service/daemon
  milestone.
