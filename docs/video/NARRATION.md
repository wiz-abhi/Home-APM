# Home APM — demo narration script (final cut)

Narration + burned-caption script for **`docs/video/home-apm-demo-final.mp4`**
(3:55, 1080p) — a ~32s title-card intro prepended to the silent master, with an
AI voiceover synced to a **measured** timeline and captions burned into the frame.

> **Timestamps below are MEASURED**, not estimated. They are the voiceover start
> for each beat in the *concatenated* video (32.133s intro + 203.133s master =
> 235.267s), taken from `tools/video/manifest.json` — the content-visible start of
> every scene, detected by ffmpeg scene detection + frame inspection. Each line is
> placed 0.3s **after** its scene appears (voice never leads the visual) and is fit
> to end inside its window (Sarvam `pace`, then ffmpeg `atempo`).
>
> Voice: Sarvam `bulbul:v2`, speaker `hitesh`, en-IN. Rebuild:
> `python tools/video/narrate_final.py` (track + captions) then burn (see
> `tools/video/README.md`). `CAPTIONS.srt` is the sidecar for the same cues.

## Intro (approved script — 4 title cards, ~32s)

| # | Time | Card (on-screen) | Voiceover (as spoken) |
|---|------|------------------|-----------------------|
| I1 | 0:01 | HOME APM — distributed tracing for your smart home | "This is Home APM — my Track 3 project for the Agents of SigNoz hackathon." |
| I2 | 0:07 | The problem: 2,000,000+ Home Assistant homes. Zero observability. | "Over two million homes run on Home Assistant automations — but when one misbehaves, there's no way to see why. Its built-in trace view is famously unreadable." |
| I3 | 0:16 | The idea: every automation run → a real OTLP trace → a SigNoz flame graph | "Home APM bridges Home Assistant's hidden internal traces into OpenTelemetry, so every automation run becomes a flame graph in SigNoz — with logs, metrics, dashboards, and alerts to match." |
| I4 | 0:26 | One command: foundryctl cast -f casting.yaml | "It installs with one command. Here's what it found in my house on the first night." |

## Body (the silent master, offset by the 32.133s intro)

| # | Time | Beat | Voiceover (as spoken) |
|---|------|------|-----------------------|
| 1 | 0:32 | Cold-open card | "My hallway lights turned on at three in the morning. And I had no idea why." |
| 2 | 0:38 | Home Assistant's native trace | "Home Assistant does record a trace. But this is what it gives you: cryptic node paths, a run that says it finished in zero seconds, and it keeps only the last five." |
| 3 | 0:50 | "Couldn't tell me" card | "So it couldn't actually tell me what happened." |
| 4 | 0:54 | "Gave my house an APM" card | "So I gave my house an APM." |
| 5 | 0:56 | Saved-views hub | "A sidecar turns every automation run into a real OpenTelemetry trace, and sends it to SigNoz. Four saved views, one per kind of failure. Let's open the three a.m. mystery." |
| 6 | 1:08 | The 3am waterfall (the reveal) | "There it is. A sun-and-time trigger fired, a choose branch was taken, and its condition passed silently, turning the hallway lights on. The branch Home Assistant would never show me is now a span I can click." |
| 7 | 1:22 | Logs ↔ traces card | "And the log line and the trace are one click apart." |
| 8 | 1:29 | Sidecar logs | "The bridge narrates itself: every converted-run log carries the trace id it produced. Logs and traces, natively correlated." |
| 9 | 1:42 | Latency card | "Next mystery: my morning routine felt slow." |
| 10 | 1:47 | Morning-routine trace | "One span explains all of it: a wait-for-trigger that sat idle for fifty-two seconds. The villain is impossible to miss." |
| 11 | 2:00 | Parallel / repeat / error card | "This is the part every naive trace reader gets wrong." |
| 12 | 2:05 | Good-night trace | "A real tracer. The parallel block draws as overlapping bars, one branch clearly slower. The repeat loop stacks one span per iteration. And a template divides by zero, so it lights up as a red error span." |
| 13 | 2:18 | "Ask your house" card | "You don't even have to read a flame graph." |
| 14 | 2:21 | Terminal — ask.py | "Ask in plain English. Through the SigNoz MCP server, it finds the run, reads the span tree, and answers in one sentence: the silent branch, the fifty-two-second wait, the divide-by-zero, each with the exact trace id and a link to the flame graph." |
| 15 | 2:44 | "One board" card | "And there's a board my partner could read." |
| 16 | 2:51 | Home APM dashboard | "Panels titled as questions: how often are my automations running, which are slowest, is anything failing, plus a room selector that refocuses the whole board." |
| 17 | 3:11 | House service map | "Every domain your automations touch, drawn as a service map of your house, with the failing path to persistent-notification flagged in red." |
| 18 | 3:21 | Alert card | "And when something breaks, it says so." |
| 19 | 3:26 | SigNoz alert rules | "A traces-based rule, Automation failing, is firing on those error spans." |
| 20 | 3:38 | Notification inside Home Assistant | "And the loop closes: the SigNoz alert lands back inside Home Assistant as a notification, right where you'd look for it." |
| 21 | 3:45 | Close card 1 | "Home Assistant always had traces. It just never let anyone see them." |
| 22 | 3:50 | Close card 2 | "So give your house an APM. One command, and the dashboard's already live." |

## Delivery notes
- Tone: curious and calm, like debugging out loud — not an ad read.
- The three "mystery" beats (3am / morning / good-night) are the spine; let the
  waterfalls breathe — the visuals do the work, the VO just points.
- Beat 5 opens over the "gave an APM" card + the SigNoz splash ("…sends it to
  SigNoz") and lands "four saved views" as the list renders (~1:00), so its longer
  line fits without leading the visual.
- Sync is driven entirely by `tools/video/manifest.json`; edit measured times there,
  never by hand-tuning timestamps in this file.
