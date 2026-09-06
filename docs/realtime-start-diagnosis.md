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
  only explicit diagnostic records are persisted, rotation is cross-process safe, and
  the realtime callback performs no file I/O.
- Preflight microphone authorization. Block denied/restricted states with the correct
  settings link, and use a cancelable, 60-second-bounded AVFoundation request before
  model loading, Session/WAV creation, or PortAudio.
- Do not add a sample-rate workaround for this incident.
- Treat stable Developer ID signing/notarization as the mechanism that preserves the
  designated requirement across production upgrades. Ad-hoc QA builds must be
  re-authorized whenever their executable CDHash changes.

## Follow-up: no first-use microphone prompt after TCC reset

At 23:01 the user could record System audio after re-authorization, but Mic still
showed permission guidance, never opened a native prompt, and never appeared in
the Microphone Settings list. This was a second, independent defect introduced
by the explicit AVFoundation preflight path; resetting TCC cannot repair it.

A fresh process with the project's actual PyObjC runtime reproduced this call:

```python
objc.loadBundle("AVFoundation", globals(),
                bundle_path="/System/Library/Frameworks/AVFoundation.framework")
objc.lookUpClass("AVCaptureDevice").requestAccessForMediaType_completionHandler_(
    "soun", callback)
```

It raised `TypeError: Argument 3 is a block, but no signature available` before
reaching TCC. Loading the framework dynamically provides the selector's `@?`
encoding, but not the completion block's `void (^)(BOOL)` ABI. Our mock-only
authorization tests bypassed this bridge and therefore did not catch the bug.
The exception also lacked the marker required by the persistent diagnostic log.

Commit `a373c82` registers the missing selector metadata, persists bridge failures,
and distinguishes an unrequested grant from a denied grant in the UI. A fresh
macOS subprocess regression checks the real bridge metadata without prompting
or changing privacy settings. A separate native smoke call accepted the callback
and delivered `False`; that validates the ABI, not the installed app's permission.

Verification: `.venv/bin/python -m pytest -q` passed 913 tests (894 warnings);
`.venv/bin/python -m pytest tests/frontend -q` passed 21 tests. Independent review
of exact commit `a373c82d256fa437008be038dbb444e36674e177` passed; the reviewer
also independently ran all 10 microphone-permission request tests. The 2.0.1
packaged native prompt and non-empty audio checks remain separate release gates.
