# Home APM — demo narration script

Optional voiceover for `docs/video/home-apm-demo.mp4` (3:23, 1080p). The video is
**caption-driven** — the on-screen title cards already carry the story, so a dub is
*optional polish*. If you record VO, the timestamps below line up with the cut; each
line is written to be spoken comfortably inside its window (aim for a calm, ~150 wpm
delivery — the windows have a little slack on purpose).

> Timestamps are approximate (±2s) — they track the assembled `home-apm-demo.mp4`.
> Re-running `record_demo.py` reshuffles page-load timing by a second or two, so nudge
> the VO to taste. `CAPTIONS.srt` in this folder carries the exact on-screen card text.

| # | Time | Beat | Voiceover |
|---|------|------|-----------|
| 1 | 0:00 | Cold-open card | "My hallway lights turned on at three in the morning. And I had no idea why." |
| 2 | 0:05 | Home Assistant's native trace | "Home Assistant *does* record a trace. But this is what it gives you — cryptic node paths, a graph you can't scroll, and a run that says it finished in zero seconds. It keeps only the last five, then they're gone." |
| 3 | 0:16 | "Couldn't tell me" card | "So it couldn't actually tell me what happened." |
| 4 | 0:20 | "Gave my house an APM" card | "So I gave my house an APM. A small sidecar turns every automation run into a real OpenTelemetry trace — and sends it to SigNoz." |
| 5 | 0:24 | Saved-views hub | "Four saved views, one per kind of failure I wanted to hunt. Let's open the three-a-m mystery." |
| 6 | 0:40 | The 3am waterfall (the reveal) | "There it is. A sun-and-time trigger fired, a *choose* branch was taken, and its condition passed — silently — turning the hallway lights on. The branch that Home Assistant would never show me is now a named span I can click." |
| 7 | 0:53 | Logs ↔ traces card | "And the log line and the trace are one click apart." |
| 8 | 0:56 | Sidecar logs | "The bridge narrates itself: every 'converted run' log carries the trace id it produced — logs and traces, natively correlated." |
| 9 | 1:06 | Latency card | "Next mystery: my morning routine felt slow." |
| 10 | 1:09 | Morning-routine trace | "One span explains all of it — a `wait_for_trigger` that sat idle for fifty-two seconds. The villain is impossible to miss." |
| 11 | 1:27 | Parallel / repeat / error card | "This is the part every naive trace reader gets wrong." |
| 12 | 1:31 | Good-night trace | "A real tracer. The parallel block draws as overlapping bars — one branch clearly slower. The repeat loop stacks one span per iteration. And a template action divides by zero, so it lights up as a red error span." |
| 13 | 1:46 | "Ask your house" card | "You don't even have to read a flame graph." |
| 14 | 1:49 | Terminal — ask.py | "Ask in plain English. Through the SigNoz MCP server, it finds the run, reads the span tree, and answers in one sentence — the silent branch, the fifty-two-second wait, the divide-by-zero — each with the exact trace id and a link to the flame graph." |
| 15 | 2:13 | "One board" card | "And there's a board my partner could read." |
| 16 | 2:16 | Home APM dashboard | "Panels titled as questions — how often are my automations running, which are slowest, is anything failing — plus a room selector that refocuses the whole board." |
| 17 | 2:36 | House service map | "Every domain your automations touch, drawn as a service map of your house — with the failing path to persistent-notification flagged in red." |
| 18 | 2:44 | Alert card | "And when something breaks, it says so." |
| 19 | 2:47 | SigNoz alert rules | "A traces-based rule — 'Automation failing' — is firing on those error spans." |
| 20 | 2:57 | Notification inside Home Assistant | "And the loop closes: the SigNoz alert lands back inside Home Assistant as a notification — firing, right where you'd look for it." |
| 21 | 3:06 | Close card 1 | "Home Assistant always had traces. It just never let anyone see them." |
| 22 | 3:11 | Close card 2 | "So give your house an APM. One command, and the dashboard's already live." |

## Delivery notes
- Tone: curious and calm, like debugging out loud — not an ad read.
- The three "mystery" beats (3am / morning / good-night) are the spine; let the
  waterfalls breathe — the visuals do the work, VO just points.
- Beats 5, 15 and 18 are short bridges; keep them to one breath.
- If you cut the "ask your house" beat (14) for time, the story still lands — just
  bridge from the good-night trace (12) straight to the board card (15).
