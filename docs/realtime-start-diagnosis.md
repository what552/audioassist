# Packaged microphone startup diagnosis

Date: 2026-09-06
Scope: `REALTIME-DIAG-01`, installed ad-hoc AudioAssist 2.0.0 candidate

## Symptom

After replacing the installed app with a newly built ad-hoc candidate, the first Mic
start stayed on `Loading…` and created only a 44-byte WAV header. An earlier candidate
had subsequently recorded successfully after microphone permission was enabled.

## Correlated evidence

The bounded runtime log correlated attempt
`90ee3056-032f-4115-95a1-0822900a1df3`:

- Qwen and Silero model load completed in 4.66 seconds.
- WAV creation completed in 0.1 ms.
- sounddevice 0.5.6 imported in 44 ms.
- The final marker was `stream_open.begin` with 16 kHz, mono, 512 frames.
- No `stream_open.done`, `stream_start.begin`, or first-audio-frame marker followed.

A process sample placed the blocked thread in:

`Pa_OpenStream → OpenStream → OpenAndSetupOneAudioUnit → AudioUnitSetProperty →
AudioDeviceCreateIOProcID → HALC_ProxyObject::SetPropertyData → mach_msg`

CoreAudio selected device 89, the 48 kHz built-in MacBook Pro microphone. System TCC
logs at the same timestamp reported that the existing microphone permission code
requirement did not match the new candidate's CDHash, followed by
`AUTHREQ_PROMPTING`. CoreAudio later logged server/proxy errors while the unanswered
authorization request remained pending.

## Conclusion

The observed failure was not evidence that 16 kHz input is unsupported. The rebuilt
ad-hoc app had a different code identity, so macOS required a new microphone decision
and PortAudio blocked synchronously while CoreAudio waited for it. This also explains
why a previously authorized build could record normally.

## Product and release actions

- Keep bounded file logging and per-attempt stage markers for packaged-only failures;
  they contain no transcript or audio content.
- Preflight microphone authorization. Block denied/restricted states with the correct
  settings link, and label a not-yet-determined request as waiting for macOS permission.
- Do not add a sample-rate workaround for this incident.
- Treat stable Developer ID signing/notarization as the mechanism that preserves the
  designated requirement across production upgrades. Ad-hoc QA builds must be
  re-authorized whenever their executable CDHash changes.
