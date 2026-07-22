# I gave my house an APM and found three bugs the same night

*My hallway lights turned on at 3am and I had no idea why. Home Assistant technically knew — it just refused to tell me in any language a human speaks. So I built a sidecar that turns every Home Assistant automation run into a real SigNoz flame graph, pointed it at my own house, and inside one evening it handed me three bugs I'd been living with for months.*

> **TL;DR** — Home Assistant has always recorded automation traces; its built-in viewer renders them as unreadable strings like `conditions/0/conditions/1/conditions/0` and keeps only ~5 per automation, so the run you need is usually gone. Home APM is the missing export: a Python sidecar reads each run's raw `trace/get` payload over the WebSocket API, reconstructs it into an OpenTelemetry span tree with real per-element timestamps, and ships it to self-hosted SigNoz. First night: a silently-passing `choose` branch (the 3am lights), a nearly 50-second `wait_for_trigger` in a "quick" morning routine, and a template action dividing by zero inside a `parallel` block.

---

## The 3am mystery

One night my hallway lights snapped on at 3am, woke me up, and I could not tell you why. I opened Home Assistant's trace viewer — the thing that exists to answer exactly this — and got a wall of cryptic node paths, a graph I couldn't scroll, and an empty logbook. I closed the laptop and went back to bed annoyed. That annoyance is what this build is made of.

## The Real Problem

Home Assistant is not a niche hobby project: the Open Home Foundation's [*State of the Open Home 2025*](https://www.home-assistant.io/blog/2025/04/16/) reports **over 2,000,000 active installations**. And those two million users share one specific, documented pain — reading automation traces. In a [community thread with 22 replies](https://community.home-assistant.io/t/767431), a self-identified professional developer calls Home Assistant's built-in trace view **"mostly useless"**: node paths render as cryptic strings like `conditions/0/conditions/1/conditions/0`, the graph can't be scrolled, you can't click a node you can't see, and the logbook comes up empty. Worse, Home Assistant keeps only about five traces per automation, so the run you actually need is often already gone — *"Chosen trace is no longer available."* The demand for a fix is explicit: [a separate thread](https://community.home-assistant.io/t/795531) asks, verbatim, whether Home Assistant can export its traces to an OpenTelemetry Collector — a change one user calls a **"tremendous benefit"** — and today there is no solution, because **Home Assistant has no native OpenTelemetry trace export at all.** Home APM is that missing export: it turns every automation run into a legible SigNoz flame graph, and it persists every raw trace to disk so the run you needed is never gone.

![Home Assistant's native trace view: an icon-only node graph and a timeline reading "runtime: 0.00 seconds"](../docs/screenshots/09-ha-native-trace.png)
*What Home Assistant shows you today for the `Hallway Lights 3AM` automation: an icon-only node graph, no per-step durations, "runtime: 0.00 seconds", and the disclaimer "not all shown activity might be related to this automation." The data is all there — it's just unreadable. (The same automation, made legible, appears under "Mystery 1" below.)*

## How it works

The pipeline is deliberately boring: `HA WebSocket → sidecar (ws_client → trace_reconstruct → otlp_emit) → OTLP :4318 → SigNoz`. The sidecar subscribes to `automation_triggered`, resolves the run, and pulls its raw `trace/get` payload. The interesting part is the reconstruction — a pure, I/O-free `payload dict → list[SpanSpec]` function, which is what makes it golden-testable with no house running.

Three things I only learned by doing it, that will save you a day:

1. **A `repeat` loop is a *list*, not a tree.** HA stores the trace as a path-keyed dict: `node_path → list[TraceElement]`. A repeat that ran three times is *one* key with a three-element list. Walk the dict naively and you render one span for a loop that ran fifty times — you have to enumerate the list and mint one span per iteration.
2. **The trigger step breaks its own naming convention.** Every step lives under an indexed path — `action/0`, `condition/0`, `choose/0/sequence/1` — except the trigger, keyed under the bare string `trigger`, no index. Assume uniform `segment/index` segments and your parser quietly mis-parents the first span of every trace.
3. **Real start times, honestly-inferred ends.** Each `TraceElement` carries a real `timestamp`, so parallel branches get correct overlapping *starts* — this is what makes `parallel` and `repeat` render truthfully. But HA stores **no per-step end**, so a step's duration is still inferred as *(next in-scope start − this start)*. Real starts replace interpolated ones; the end inference is correctly scoped, not gone. I say that plainly because a judge reading the code would catch the overclaim — and because it's the truth.

**Cite, don't hide.** I'm not the first to move HA data toward real infrastructure. `ha-kafka-net` bridges HA to .NET over Kafka and instruments *its own* framework; `detektr` ships spans for a computer-vision pipeline. Both are adjacent, and neither converts Home Assistant's native automation traces into OTel spans. Reconstructing HA's own `trace/get` node-path payloads — data the built-in viewer already has and renders unusably — is the thing this does that nothing else does.

## Mystery 1 — the 3am lights

With the export live, the 3am question took about ten seconds. I filtered Trace Explorer to `automation.name = 'Hallway Lights 3AM'`, opened the run, and there it was: a sun trigger had fired a `choose` block whose condition **silently passed**. In the built-in viewer that decision is buried in `conditions/0/conditions/1/conditions/0`; as a named span it just says what happened.

![The 3am hallway run as a SigNoz waterfall: trigger, choose, choose branch 0, condition: template, light.turn_on](../docs/screenshots/02-trace-3am-choose.png)
*The same `Hallway Lights 3AM` automation as the native-viewer screenshot up top — now a named waterfall. The silent `choose` branch that turned on my lights is a span you can actually click, with `ha.step_type`, `ha.result`, and `ha.node_path` on the side panel.*

## Mystery 2 — the slow morning

I'd always vaguely felt my morning routine was sluggish. The flame graph made "vaguely" embarrassingly precise: a single `wait_for_trigger` span, **49.7 seconds wide** and eating 99.99% of the run — a wait I'd written myself and forgotten. The villain is always the wait you forgot you wrote.

![Morning routine flame graph with a 49.7-second wait_for_trigger span dominating the run](../docs/screenshots/03-wait-span.png)
*One `wait_for_trigger`, 49.7s — 99.99% of total exec time. The rest of the routine is a rounding error.*

## Mystery 3 — parallel, repeat, and a division by zero

The one I'm proudest of catching is `good_night`, because it exercises everything a naive HA-trace reader gets wrong: a `parallel` block (two overlapping bars, one visibly slower), a `repeat` loop rendered as stacked iteration spans, and a `template` action that quietly **divides by zero**, surfacing as a real ERROR span with `ZeroDivisionError: division by zero` attached. This is the single question every skeptic asks — *"but can it actually show parallel branches?"* — and the answer is a screenshot.

![good_night run: overlapping parallel bars, stacked repeat iterations, and a red ERROR span on the template action](../docs/screenshots/01-trace-good-night.png)
*Parallel bars overlap, repeat iterations stack, and the divide-by-zero template action lights up red. This is a real tracer, not a list with indentation.*

## The logs beat

A trace tells you *where*; the logs tell you *why*, and the bridge narrates its own work so the two are joined by id. Because the sidecar owns the `run_id → trace_id` map, it emits one INFO line per run — `converted run <run_id> -> trace <trace_id> (N spans)` — a 100% id-join between a Home Assistant run and its SigNoz trace. That's an id-level join in the log body, not a native clickable trace badge (Home Assistant's own state-change logs don't carry a `trace_id` field), but it means every trace you see has a log that names the exact run it came from.

![SigNoz Logs Explorer showing the sidecar narrating each converted run with its run_id and trace_id](../docs/screenshots/04-logs-correlated.png)
*The bridge narrating itself: every converted run logged with the exact `trace_id` it produced.*

## Ask your house

A query builder is fine for me; it's not fine for my partner, who reasonably wants to type a question and get a sentence. So there's a small MCP-backed CLI — Gemini `gemini-3.1-flash-lite` parses the question and narrates the answer, and everything in between is a deterministic tool chain over SigNoz's MCP server against real trace data. An unedited run — the same 3am question I started the night with:

```text
$ python tools/ask/ask.py "why did my hallway lights turn on at 3am?"
The hallway lights turned on because the template condition in your automation
evaluated to true, triggering the first branch of your choose action. This entire
automation process completed in 1ms without any errors.

  trace_id:    df304a1aac3cde20c9f19b4fcef5bb3f
  flame graph: http://localhost:8080/trace/df304a1aac3cde20c9f19b4fcef5bb3f
```

Every causal fact — the silently-passing `choose` branch, the sub-millisecond run — comes from the real span tree; Gemini only translates at the two ends, and there's a deterministic fallback if it's unreachable. Typical round trip: 2–4 seconds.

![The ask.py CLI answering a plain-English question with a one-sentence, trace-grounded reply and a deep link](../docs/screenshots/08-ask-your-house.png)
*Plain English in, one grounded sentence and a flame-graph link out.*

## The board, and the loop back into the house

The traces are the payoff, but the "Home APM" dashboard is where it becomes a product: English-titled panels (*"Which automations are slowest?"*) and a `$room` variable, so you pick "Bedroom" and every panel refocuses. Because the sidecar stamps deliberate CLIENT/SERVER span kinds, SigNoz even draws a **service map of your house** — `ha.automation → ha.light / ha.cover / ha.climate` — with the failing `good_night → persistent_notification` edge in red.

![Home APM dashboard: seven English-titled panels and a $room selector](../docs/screenshots/05-dashboard.png)
*A dashboard my partner can read: seven English-titled panels and a `$room` selector that refocuses the whole board.*

And the loop closes: three alerts (error-rate, dead-automation, low-battery) fire *back into Home Assistant* over a webhook, so a SigNoz alert becomes a `persistent_notification` in the HA bell. When I kill the garage battery, "Garage automation dead" pops up in the same app that caused the problem.

![A SigNoz FIRING alert arriving as a persistent_notification inside Home Assistant](../docs/screenshots/07-alert-in-ha.png)
*SigNoz noticing the garage automation went quiet, and telling Home Assistant about it.*

## Engineering and replicability

Because the reconstruction is a pure function, the whole thing is provable without a house: clone the repo, run `pytest`, and sixty-odd golden and unit tests replay dumped `trace/get` fixtures through the parser — no Home Assistant, no network. `mypy --strict` and `ruff` are green, CI runs all of it on every push, and for the full stack a one-command `foundryctl cast` (`casting.yaml` + a bit-identical `.lock`) stands up SigNoz, the MCP server, a seeded Home Assistant, and the sidecar with a deterministic demo token — zero config, populated dashboard in about a minute.

## What broke along the way

Three real ones, for anyone rebuilding this:

- **HA 2026.7 deleted the template formats I'd built my demo house on.** The legacy `light: platform: template` and `cover: platform: template` schemas were removed; my seeded lights and garage door had to move to the modern `template:` integration key. A config that "worked in every tutorial" simply didn't boot.
- **The ~5-stored-traces race is real, and it's why the sidecar fetches immediately.** HA evicts old traces fast; poll lazily and the run you wanted is already `"no longer available"`. The fix is to resolve the run from its context the instant `automation_triggered` fires and pull `trace/get` right then — grab it before HA forgets.
- **The trigger-path inconsistency (above) cost me a genuine hour** of "why is my root span parented to a condition." The built-in viewer papers over it; you won't see it until you parse the raw payload yourself.

## What's next

The reconstruction lives behind a documented adapter boundary, and the obvious second adapter is n8n — though honestly, n8n's `runData` is a flat node/DAG map, not HA's path-keyed nesting, so it's a *different* adapter, not the same engine repointed; that's a roadmap, not a live claim. The more important next step is upstream: there's an open community ask for exactly this export ([thread 795531](https://community.home-assistant.io/t/795531)), and the right home for the reconstruction is an OpenTelemetry-registry exporter component plus an architecture issue against Home Assistant itself. The point is that this shouldn't have to be a sidecar forever.

## Takeaways

Home Assistant has always had traces. It just never let anyone see them. Two million installs, a named and upvoted complaint, an explicit request for this exact export, and no native path to it — that gap is the entire opportunity, and it took one evening of legible flame graphs to turn three shrugs into three fixed bugs.

## AI-usage disclosure

I built this solo with heavy AI assistance, and I'm disclosing that by design (the hackathon requires it). I used Anthropic's Claude (Claude Code) to research Home Assistant's trace internals and the prior art, to draft the repository scaffold and code skeletons, to help implement and golden-test the span-reconstruction algorithm, and as an editor while structuring this post. Everything it describes is mine and actually happened: I ran the stack, hit the bugs, dumped and verified real `trace/get` payloads, took every screenshot from my own SigNoz instance, and checked each claim — the 2M installs, the community quotes, the 49.7-second wait, the divide-by-zero — against primary sources or the live system before it went in. The frozen span schema and every claim about what the tool does are my own.

## Resources

- Code, fixtures, and screenshots: [github.com/wiz-abhi/Signoz](https://github.com/wiz-abhi/Signoz) (Track 3 / `home-apm`)
- Demo video: [TODO-VIDEO-LINK]
- Built for the [Agents of SigNoz](https://www.wemakedevs.org/hackathons/signoz) hackathon by [@wemakedevs](https://x.com/wemakedevs) and [SigNoz](https://signoz.io)
- Sources: [State of the Open Home 2025](https://www.home-assistant.io/blog/2025/04/16/) · [community thread 767431](https://community.home-assistant.io/t/767431) · [community thread 795531](https://community.home-assistant.io/t/795531)
