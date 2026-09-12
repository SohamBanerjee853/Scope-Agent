# Scope submission kit

Use `scope-submission-clean.mp4` as the visual track and record Soham and Arjun
reading `soham-arjun-script.md` over it. The voiceover is 248 words, approximately
124 words per minute. The footage lasts exactly 2:00 and has no audio track.

`scope-submission-captioned.mp4` includes the script as timed captions. The SRT
file contains the same cues for an editor. Both videos are 1920×1080, 10 fps,
H.264 in an MP4 container. No music or artificial voices are included.

## Speaking handoffs

- Soham, 0:00–0:40: problem, bounded permissions, publishing boundary.
- Arjun, 0:40–1:42: prediction, separate consent, observed failure, repair, verification.
- Soham, 1:42–2:00: revocation, receipt, closing line.

## What the recording demonstrates

A checkout acknowledgement is lost after a payment succeeds. A retry with a new
idempotency key produces a duplicate charge. The local fixture reproduces that
bug, fixes the key to use the stable order identity, and passes the same three
regression tests afterward.

The right pane is Scope's actual review interface. The left pane is a scripted
demo driver displaying verified results. Both were captured continuously from
real tmux terminal viewports, then their ANSI cells were rendered into video
pixels with chapter labels and captions. This is a terminal screencast, not a
desktop-camera capture, fabricated app mockup or live coding-model session.

Permission hooks, authenticated watcher requests, typed predictions, separate
probe consent, local executions, regression tests, fixture next-step delivery,
revocation and receipt generation all ran. Reviewer inputs and the exact repair
are scripted and labeled. The payment service is in-memory and uses no network
or real money. The synthetic git push was never executed.

The receipt preserves distinct permission and understanding evidence. A matched
prediction does not establish human mastery, and the fixture does not benchmark
developer time saved. `recording-manifest.json` records capture provenance and
video hashes; `receipt.json` contains the recorded workflow evidence.

The kit is ready for editing and your narration. It has not been submitted or
published to a competition service.
