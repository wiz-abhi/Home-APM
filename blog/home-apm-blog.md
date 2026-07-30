# Give your house an APM: tracing Home Assistant automations with SigNoz

*Home Assistant records a complete execution trace for every automation it runs — then shows it to you as an icon graph with no durations. I spent a week turning those traces into real OpenTelemetry span trees in SigNoz, built a demo house full of deliberately broken automations to prove it works, and found three things about HA's trace format that aren't written down anywhere.*

**[▶ Watch the demo](https://youtu.be/zidlC4Qj3lo)** · **[⚡ Try it live — no install](https://wiz-abhi-home-apm-demo.static.hf.space)** · **[Code](https://github.com/wiz-abhi/Home-APM)**

> **TL;DR** — Home Assistant records a full trace of every automation, then renders it as unreadable strings like `conditions/0/conditions/1/conditions/0` and keeps only ~5 runs per automation, so the run you need is usually gone. **Home APM** is the missing export: a Python sidecar reads each run's raw `trace/get` payload over the WebSocket API, reconstructs it into an OpenTelemetry span tree, and ships it to self-hosted **SigNoz**. Every automation run becomes a flame graph.

---

## The problem is real, and it's big

Home Assistant isn't a niche hobby: it went from one million to **over 2,000,000 active installations** in 2024 ([State of the Open Home 2025](https://www.home-assistant.io/blog/2025/04/16/state-of-the-open-home-recap/)). Those users share one pain. In a [22-reply thread](https://community.home-assistant.io/t/767431), a professional developer calls the built-in trace view *"mostly useless"*: unscrollable, unclickable, and only ~5 runs kept, so the one you need is often *"no longer available."* [A separate thread](https://community.home-assistant.io/t/795531) asks outright for OpenTelemetry export — a *"tremendous benefit"* — with no solution.

And there isn't one: as of Home Assistant 2026.7, core ships **no OTLP trace exporter** (verified against the integrations list, July 2026). Home APM is that export.

![Home Assistant's native trace view — an icon-only node graph with no durations](https://raw.githubusercontent.com/wiz-abhi/Home-APM/main/docs/screenshots/09-ha-native-trace.png)
*What Home Assistant shows you today: an icon-only graph, no per-step durations, "runtime: 0.00 seconds." The data is all there — it's just unreadable.*

## A demo house, built to break on purpose

Here's the honest version of the methodology, because it's the part that makes everything below reproducible.

I didn't wait for my own house to misbehave. I built a **seeded Home Assistant instance with four automations engineered to fail in four specific ways** — each chosen to break a different part of a naive trace parser, and each firing itself on a `time_pattern` trigger so the whole thing reproduces unattended in about three minutes:

| Automation | The engineered bug | What it stresses |
|---|---|---|
| `hallway_lights_3am` | a `choose` condition that ORs in `now().hour >= 0` — true at every hour | silently-passing branches |
| `morning_routine` | a `wait_for_trigger` on an alarm the simulator never fires | one step dominating a run |
| `good_night` | a `parallel` block, a `repeat` loop, and `{{ 1 / 0 }}` | concurrency + error spans |
| `garage_check` | nothing — a clean run | the false-positive check |

That last row is the one people skip. If your tool only ever sees broken runs, you never learn whether it also flags healthy ones.

## How it works

The pipeline is deliberately boring: `HA WebSocket → sidecar → OTLP :4318 → SigNoz`. The sidecar grabs each run's raw `trace/get` payload and rebuilds it. The catch: Home Assistant doesn't hand you a tree — it hands you a **flat dict keyed by node path**. Rebuilding the tree from those strings is the whole trick.

![A flat node-path dict reconstructed into a nested OTLP span tree](https://raw.githubusercontent.com/wiz-abhi/Home-APM/main/docs/diagrams/reconstruction.png)

The reconstruction is a pure, I/O-free `dict → list[SpanSpec]` function — which is what makes it golden-testable with no house running. Clone, `pytest`, done.

## Three things I only learned by doing it

**1. A `repeat` loop is a *list*, not a tree.** HA stores `node_path → list[TraceElement]`. A loop that ran three times is *one* key with a three-element list. Walk the dict naively and you render one span for a loop that ran fifty times — you have to enumerate the list and mint one span per iteration.

**2. The trigger step breaks its own naming.** Every step is `action/0`, `choose/0/sequence/1`… except the trigger, keyed as the bare string `trigger`, with no index. Assume a uniform `segment/index` shape and you quietly mis-parent the first span of every trace. That cost me an hour of "why is my root span parented to a condition."

**3. Inside a `parallel` block, HA attaches step results to the wrong branch.** This one I found by accident, and it's my favourite.

A `delay` step records its own duration in `result: {"delay": 3.0, "done": true}` — handy, because it's an exact number rather than an inference. But in `good_night`, branch 0 holds a 3-second delay and branch 1 holds a 5-second delay, and the trace reports them **swapped**:

```text
config:  parallel/0 → delay 3s        parallel/1 → delay 5s
trace:   parallel/0 → {"delay": 5.0}  parallel/1 → {"delay": 3.0}
```

Which is right? The measured elapsed time settles it: branch 1's next step starts 5.0015 s after its delay begins, so branch 1 really waited 5 seconds — matching the **config**, not the recorded result. Concurrent branches race when HA writes results onto trace elements, and the values land on each other's steps.

The practical rule: `result.delay` is trustworthy in sequential and `repeat` context, and **not** trustworthy inside `parallel`. Home APM uses the recorded value where it's safe, falls back to the config-declared duration inside `parallel` blocks (static config can't race), and stamps every span with `ha.end_inferred` — `recorded`, `config_declared`, `next_sibling`, `parent_boundary` — so you can always tell a measured bar from an inferred one.

That attribute exists because of the honest limitation under all of this: **HA records a real start per step, but no end.** Real starts are why `parallel` bars overlap truthfully and `repeat` iterations stack correctly. But a duration is still, in the general case, inferred — and a tool that draws a five-second bar should tell you when it's guessing.

## The three beats

**The 3am lights.** Filter Trace Explorer to `Hallway Lights 3AM`, open the run, and there it is: a trigger fired a `choose` branch whose condition silently passed. Buried in the native viewer; a named, clickable span here — and the untaken branch is now tagged `ha.result: skipped`, so "which branch ran" is a glance rather than a deduction.

![The 3am hallway run as a named SigNoz waterfall](https://raw.githubusercontent.com/wiz-abhi/Home-APM/main/docs/screenshots/02-trace-3am-choose.png)

**The slow morning.** One `wait_for_trigger` span eating **99.99% of the run** — about 51.5 seconds of a 51.55-second execution. In HA's own view this is invisible; the run reports "0.00 seconds."

![Morning routine — a wait_for_trigger span dominating the run](https://raw.githubusercontent.com/wiz-abhi/Home-APM/main/docs/screenshots/03-wait-span.png)

**Parallel, repeat, and a division by zero.** `good_night` exercises everything a naive HA-trace reader gets wrong at once: a `parallel` block (two overlapping bars), a `repeat` loop rendered as stacked iterations, and a `template` action that quietly divides by zero, surfacing as a real ERROR span with `ZeroDivisionError` attached. The question every skeptic asks — *"but can it actually show parallel branches?"* — answered by a screenshot.

![good_night — overlapping parallel bars, stacked repeat iterations, and a red ERROR span](https://raw.githubusercontent.com/wiz-abhi/Home-APM/main/docs/screenshots/01-trace-good-night.png)

## Logs, and asking in plain English

A trace tells you *where*; logs tell you *why*. The sidecar owns the `run_id → trace_id` map, so logbook entries are exported as OTLP logs stamped with the originating run's real `trace_id`/`span_id` — a log line and its flame graph are one step apart.

And because a query builder isn't for everyone, there's a small MCP-backed CLI. Gemini parses the question, a deterministic tool chain over SigNoz's MCP server pulls the real span tree, and Gemini narrates:

```text
$ ask "why is my morning routine slow?"
Morning Routine was slow because its wait_for_trigger step took ~51.5s —
99.99% of the whole run.
  trace_id:    ec639ed66cbf45bcdd365a2e8f229cfc
```

Every causal fact comes from the real span tree; the LLM only translates at the two ends, with a deterministic fallback if it's unreachable.

![The ask CLI answering a plain-English question, grounded in real trace data](https://raw.githubusercontent.com/wiz-abhi/Home-APM/main/docs/screenshots/08-ask-your-house.png)

## The board, and the loop back into the house

The dashboard is where it becomes a product: English-titled panels (*"Which automations are slowest?"*) and a `$room` selector. Because the sidecar gives each HA domain its own `service.name` and stamps `peer.service` on CLIENT spans, SigNoz draws a **service map of your house** — `ha.automation → ha.light / ha.cover / ha.climate`.

![The Home APM dashboard — English-titled panels and a $room selector](https://raw.githubusercontent.com/wiz-abhi/Home-APM/main/docs/screenshots/05-dashboard.png)

And the loop closes: three alerts (error-rate, dead-automation, low-battery) fire *back into Home Assistant* over a webhook, so a SigNoz alert becomes a `persistent_notification` in the HA bell. Kill the garage battery, and "Garage automation dead" pops up in the very app that caused the problem.

![A SigNoz FIRING alert arriving as a notification inside Home Assistant](https://raw.githubusercontent.com/wiz-abhi/Home-APM/main/docs/screenshots/07-alert-in-ha.png)

## Reproduce it — no house required

Because the reconstruction is a pure function, the whole thing is provable without a house: clone the repo, run `pytest`, and **73 golden and unit tests** replay dumped `trace/get` fixtures through the parser — no Home Assistant, no network. `mypy --strict` and `ruff` run green in CI on every push. For the full stack, `foundryctl cast` (`casting.yaml` + `.lock`) stands up SigNoz, the MCP server, the seeded house, and the sidecar. Or just **[try the live demo](https://wiz-abhi-home-apm-demo.static.hf.space)** — the real reconstruction, running in your browser, no install and no key.

One honest note on reproduction: HA's `.storage/` holds user accounts, and committing it would mean publishing credentials — so the seeded house ships as YAML config only. On first boot you complete HA's onboarding and mint a long-lived token; the repo documents the exact steps.

## What broke along the way

- **HA 2026.7 deleted the template formats my demo house was built on.** The legacy `light: platform: template` and `cover: platform: template` schemas were removed; everything had to move to the modern `template:` key. A config that "worked in every tutorial" simply didn't boot.
- **The ~5-stored-traces race is real.** Poll lazily and the run you wanted is already `"no longer available."` The fix is to fetch `trace/get` the instant `automation_triggered` fires.
- **A byte-order mark will make Linux refuse to run your shell script.** My token-seeding script had a UTF-8 BOM before the `#!` — invisible in an editor, and enough to make the kernel refuse to exec the file. Windows-authored, Linux-deployed: check your bytes.

## Takeaways

Home Assistant has always had these traces. It just never let anyone see them. Two million installs, an upvoted complaint, an explicit request for this exact export, and no native path to it — that gap is the whole opportunity. And building the demo house deliberately, instead of waiting for real failures, is what makes every claim in this post something you can re-run yourself.

## AI-usage disclosure

Built solo with heavy AI assistance (Anthropic's Claude / Claude Code), disclosed here per the hackathon rules. I used it to research Home Assistant's trace internals and prior art, scaffold and golden-test the reconstruction, build the tooling, and edit this post. I ran the stack, dumped and verified the real `trace/get` payloads, took every screenshot from my own SigNoz instance, and chased the `parallel` result-race down against the fixture data. The frozen span schema and the design decisions are my own.

## Prior art

`ha-kafka-net` bridges HA to .NET via Kafka (instrumenting its *own* framework, not HA's traces). `detektr` emits OTel spans for *its* CV pipeline. Neither converts Home Assistant's native `trace/get` node-path payloads into OTLP span trees — that's the specific gap this fills.

## Resources

- **Code:** [github.com/wiz-abhi/Home-APM](https://github.com/wiz-abhi/Home-APM)
- **Live demo:** [wiz-abhi-home-apm-demo.static.hf.space](https://wiz-abhi-home-apm-demo.static.hf.space)
- **Demo video:** [youtu.be/zidlC4Qj3lo](https://youtu.be/zidlC4Qj3lo)
- Built for the [Agents of SigNoz](https://www.wemakedevs.org/hackathons/signoz) hackathon by [@wemakedevs](https://x.com/wemakedevs) and [SigNoz](https://signoz.io)
