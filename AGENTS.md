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

## Packaging and versioning

- Treat every newly generated `.app` or DMG intended for installation or user testing as
  a new release artifact. Increment the SemVer version in `pyproject.toml` before building;
  never rebuild or replace an artifact while reusing its previous version number.
- Use a patch bump for fixes, a minor bump for backward-compatible features, and a major
  bump for incompatible product or data-contract changes. The current packaged baseline
  is `2.0.1`, so the next packaged build must be `2.0.2` or higher.
- Keep the application bundle version, DMG filename, and
  `dist/release-verification-<version>.json` aligned with the `pyproject.toml` version,
  and update the packaged baseline in this file when that release is produced.
- Do not increment the version merely for source or UI development runs that do not
  generate an installable/testable `.app` or DMG.
- Treat privacy-permission continuity as a release gate. An ad-hoc local candidate may
  reset and re-authorize Microphone and Screen & System Audio Recording because each
  changed build has a new code identity, but never present that as a production upgrade
  workflow.
- A distributable signed upgrade must be installed over the previous release and retain
  its existing recording permissions without a TCC reset. Test fresh-install prompting
  separately after a reset. If an upgrade requires users to reset or toggle privacy
  permissions again, block the release and fix its signing/designated-requirement
  continuity.

## R03 CLI

- The stable command contract is documented in `docs/cli-contract.md`.
- The CLI and desktop API share `src/service.py`; do not duplicate session discovery,
  status interpretation, transcription persistence, or summary-version behavior.
- R03-a commands are synchronous. Background submission belongs to a later service/daemon
  milestone.
