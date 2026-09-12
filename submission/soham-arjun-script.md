# Scope — two-minute submission script

Runtime: **2:00**. Spoken script: **248 words**. Soham: 126 words / 58 seconds. Arjun: 122 words / 62 seconds.

The video is silent screen footage for your own voices. Read naturally at roughly 124 words per minute; the time ranges include breathing room.

| Time | Speaker | Say | On screen |
| --- | --- | --- | --- |
| 0:00–0:10 | **Soham** | I'm Soham, and this is Arjun. We built Scope to help developers review a coding agent's permissions and understand what its changes actually do. | Buggy retry key on the left; actual Scope review inbox on the right. |
| 0:10–0:26 | **Soham** | Here's a checkout bug: a payment succeeds, its reply gets lost, and the retry charges the customer again. We approve one narrow command scope, with a command budget, an expiry, and session boundaries. | Open the permission card, review its exact command and budget, select Approve scope. |
| 0:26–0:40 | **Soham** | That approval is reusable only within those bounds. But a publishing command, like git push, still gets no automatic approval. This boundary check is synthetic; nothing is pushed. | Show the actual T3 classification and empty hook decision for an inert git push request. |
| 0:40–1:03 | **Arjun** | I'm Arjun. Scope also checks the developer's mental model. Before the probe runs, we record a prediction and a reason. Here, we predict one charge because there's only one order. Saving that prediction does not authorize execution. We separately review the exact probe and choose Run once. | Enter prediction 1 and its reason; save it; open the separate consent form and choose Run once. |
| 1:03–1:14 | **Arjun** | The actual result is two charges. Our prediction was wrong, and the regression test fails. Each retry created a new payment identity. | Show observed charge count 2, mismatched prediction, and the original failing regression. |
| 1:14–1:24 | **Arjun** | We select a smaller next step: keep the order ID stable. The demo applies that repair and records the source change. | Choose the smaller next step, then show the actual stable-order-key code change. |
| 1:24–1:42 | **Arjun** | Now we make a fresh prediction and approve another probe. The original scope is reused, but execution consent stays separate. This time we observe one charge, and all three regression tests pass. | Save a fresh prediction, separately consent again, then show one charge and three passing tests. |
| 1:42–1:52 | **Soham** | We can revoke the remaining scope immediately. Another matching request loses automatic approval. The receipt keeps permissions, predictions, and observed results distinct. | Press Ctrl-r, open a new matching request, pass it to the host, and show the receipt. |
| 1:52–2:00 | **Soham** | That's Scope: bounded autonomy with evidence you can inspect. This recording uses a local payment fixture and scripted inputs. | Hold the receipt and the closing line. |

## Recording notes

- Record your own two voices over the silent MP4. Keep the handoffs at 0:40 and 1:42.
- Use the clean MP4 if your editor will add subtitles or presenter camera footage; use the captioned version for a readable standalone walkthrough.
- Keep faces outside the command cards and form fields. The footage is 1920×1080, H.264, 10 fps and exactly 120 seconds.
- The left pane is a scripted demo driver; the right pane is the actual Scope interface. The local payment code, permission hooks, probes, tests, next-step fixture handoff and receipt all ran.
- Do not call this a live Codex/Claude session, a real payment integration, measured human learning, or a benchmark of time saved. No live model was launched; reviewer inputs and the repair were scripted.
- Sponsor integrations that are only proposed are deliberately absent from the pitch.

## Evidence shown

- Before repair: two local charges; one failed and two passed regression tests.
- After repair: one local charge; three passed regression tests.
- One bounded scope, two scoped allowances, one T3 hard ask, and successful revocation.
- Two predictions, two separately consented probe executions, and two recorded observations.
- The synthetic push was an input to the permission hook only; it never executed.
