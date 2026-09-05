# Release Checklist

Use this checklist for a packaged release or after changing audio capture, persistence, model loading, or the Python/JavaScript bridge. Routine changes do not require every manual scenario.

## Automated checks

- Run `python -m pytest -q --ignore=tests/frontend`.
- Run `python -m pytest -q tests/frontend` with Playwright Chromium available.
- Build `native/AudioAssistCaptureHelper` in release mode on macOS.
- Build the packaged application and confirm the helper and required model/runtime data are included.

## File transcription

- Import one short file and a multi-file batch.
- Confirm sequential queue behavior, progress, cancellation, retry, and checkpoint resume.
- Confirm timestamps seek the player and edits persist to JSON and Markdown.
- Confirm re-transcription and manual high-accuracy transcription replace the intended session output.

## Realtime capture

- Verify microphone, system, and mix modes independently.
- Verify pause, playback while paused, resume, finish, and short-recording discard.
- Confirm Screen Recording permission guidance and missing-helper errors are actionable.
- Confirm a denied/unavailable microphone degrades mix mode to system-only without ending the session.
- Confirm the final WAV and diarized transcript stay aligned.

## Session outputs

- Rename and delete sessions, then restart and verify persistence.
- Export transcript and summary as TXT and Markdown.
- Generate multiple summary versions and continue a Summary Agent conversation.
- If Obsidian sync is enabled, verify filename changes, frontmatter, summary, and transcript updates.

## Packaging

- Launch on a machine without the development checkout or shared virtual environment.
- Complete first-run model setup from an empty application data directory.
- Confirm no runtime dependency is loaded from a developer-only path.
- Check the release notes against `README.md` and `README.dev.md`.
