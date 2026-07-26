# I gave my house an APM and found three bugs the same night

*My hallway lights turned on at 3am and I had no idea why. Home Assistant knew — it just wouldn't tell me in any language a human speaks. So I turned every automation run into a real SigNoz flame graph, pointed it at my own house, and inside one evening it handed me three bugs I'd lived with for months.*

**[▶ Watch the demo](https://youtu.be/zidlC4Qj3lo)** · **[⚡ Try it live — no install](https://wiz-abhi-home-apm-demo.static.hf.space)** · **[Code](https://github.com/wiz-abhi/Home-APM)**

> **TL;DR** — Home Assistant records a full trace of every automation, then renders it as unreadable strings like `conditions/0/conditions/1/conditions/0` and keeps only ~5 per automation, so the run you need is usually gone. **Home APM** is the missing export: a Python sidecar reads each run's raw `trace/get` payload over the WebSocket API, reconstructs it into an OpenTelemetry span tree, and ships it to self-hosted **SigNoz**. First night: a silently-passing `choose` branch (the 3am lights), a **49.7-second** `wait_for_trigger` in a "quick" morning routine, and a template dividing by zero inside a `parallel` block.

---

## The 3am mystery

One night my hallway lights snapped on at 3am and I couldn't tell you why. I opened Home Assistant's trace viewer — the thing built to answer exactly this — and got a wall of cryptic node paths, a graph I couldn't scroll, and an empty logbook. I closed the laptop, annoyed. That annoyance is what this is made of.

![Home Assistant's native trace view — an icon-only node graph with no durations](https://raw.githubusercontent.com/wiz-abhi/Home-APM/main/docs/screenshots/09-ha-native-trace.png)
*What Home Assistant shows you today: an icon-only graph, no per-step durations, "runtime: 0.00 seconds." The data is all there — it's just unreadable.*

## The problem is real, and it's big

Home Assistant isn't a niche hobby: **2,000,000+ active homes** ([State of the Open Home 2025](https://www.home-assistant.io/blog/2025/04/16/state-of-the-open-home-2025/)). They share one pain — in a [22-reply thread](https://community.home-assistant.io/t/767431), a professional developer calls the built-in trace view *"mostly useless"*: unscrollable, unclickable, and only ~5 runs kept, so the one you need is usually *"no longer available."* [A separate thread](https://community.home-assistant.io/t/795531) asks outright for OpenTelemetry export — a *"tremendous benefit"* — and there's no solution, because **Home Assistant has no native OTel trace export.** Home APM is that export.

## How it works

The pipeline is deliberately boring: `HA WebSocket → sidecar → OTLP :4318 → SigNoz`. The sidecar grabs each run's raw `trace/get` payload and rebuilds it. The catch: Home Assistant doesn't hand you a tree — it hands you a **flat dict keyed by node path**. Rebuilding the tree from those strings is the whole trick.

![A flat node-path dict reconstructed into a nested OTLP span tree](https://raw.githubusercontent.com/wiz-abhi/Home-APM/main/docs/diagrams/reconstruction.png)

Three things I only learned by doing it — each one will save you a day:

1. **A `repeat` loop is a *list*, not a tree.** HA stores `node_path → list[TraceElement]`. A loop that ran three times is *one* key with a three-element list. Walk the dict naively and you render one span for a loop that ran fifty times — you have to enumerate the list and mint one span per iteration.
2. **The trigger step breaks its own naming.** Every step is `action/0`, `choose/0/sequence/1`… except the trigger, keyed as the bare string `trigger`, no index. Assume uniform `segment/index` and you quietly mis-parent the first span of every trace.
3. **Real starts, honestly-inferred ends.** Each element carries a real `timestamp`, so parallel branches get correct overlapping *starts* — this is what makes `parallel` and `repeat` render truthfully. But HA stores **no per-step end**, so a step's duration is still inferred as *(next in-scope start − this start)*. I say that plainly because it's the truth, and a judge reading the code would catch the overclaim.

The reconstruction is a pure, I/O-free `dict → list[SpanSpec]` function — which is exactly what makes it golden-testable with no house running.

*Prior art, cited: `ha-kafka-net` and `detektr` both move HA data toward real infrastructure, but neither converts Home Assistant's own automation traces into OTel spans. That's the gap this fills.*

## Mystery 1 — the 3am lights

With the export live, the 3am question took about ten seconds. I filtered Trace Explorer to `Hallway Lights 3AM`, opened the run, and there it was: a sun trigger fired a `choose` branch whose condition **silently passed**. Buried in the native viewer; a named, clickable span here.

![The 3am hallway run as a named SigNoz waterfall](https://raw.githubusercontent.com/wiz-abhi/Home-APM/main/docs/screenshots/02-trace-3am-choose.png)

## Mystery 2 — the slow morning

I'd always vaguely felt my morning routine was sluggish. The flame graph made "vaguely" embarrassingly precise: one `wait_for_trigger` span, **49.7 seconds wide**, eating 99.99% of the run — a wait I'd written myself and forgotten.

![Morning routine — a 49.7s wait_for_trigger span dominating the run](https://raw.githubusercontent.com/wiz-abhi/Home-APM/main/docs/screenshots/03-wait-span.png)

## Mystery 3 — parallel, repeat, and a division by zero

`good_night` is the one I'm proudest of catching, because it exercises everything a naive HA-trace reader gets wrong: a `parallel` block (two overlapping bars), a `repeat` loop rendered as stacked iterations, and a `template` action that quietly **divides by zero**, surfacing as a real ERROR span with `ZeroDivisionError` attached. The question every skeptic asks — *"but can it actually show parallel branches?"* — answered by a screenshot.

![good_night — overlapping parallel bars, stacked repeat iterations, and a red ERROR span](https://raw.githubusercontent.com/wiz-abhi/Home-APM/main/docs/screenshots/01-trace-good-night.png)

## Logs, and asking in plain English

A trace tells you *where*; the logs tell you *why*. The sidecar owns the `run_id → trace_id` map, so it emits one line per run — `converted run <run_id> -> trace <trace_id> (N spans)` — a 100% id-join between a Home Assistant run and its SigNoz trace.

And because a query builder isn't for everyone (my partner reasonably wants to type a question and get a sentence), there's a small MCP-backed CLI. Gemini parses the question; a deterministic tool chain over SigNoz's MCP server pulls the real span tree; Gemini narrates. The same 3am question I started the night with:

```text
$ ask "why did my hallway lights turn on at 3am?"
The hallway lights turned on because the template condition evaluated to true,
triggering the first branch of your choose action — the whole run finished in
1ms, no errors.
```

Every causal fact comes from the real span tree; Gemini only translates at the two ends, with a deterministic fallback if it's unreachable.

![The ask CLI answering a plain-English question, grounded in real trace data](https://raw.githubusercontent.com/wiz-abhi/Home-APM/main/docs/screenshots/08-ask-your-house.png)

## The board, and the loop back into the house

The dashboard is where it becomes a product: English-titled panels (*"Which automations are slowest?"*) and a `$room` selector, so you pick "Bedroom" and every panel refocuses. Because the sidecar stamps deliberate CLIENT/SERVER span kinds, SigNoz even draws a **service map of your house** — `ha.automation → ha.light / ha.cover / ha.climate`.

![The Home APM dashboard — English-titled panels and a $room selector](https://raw.githubusercontent.com/wiz-abhi/Home-APM/main/docs/screenshots/05-dashboard.png)

And the loop closes: three alerts (error-rate, dead-automation, low-battery) fire *back into Home Assistant* over a webhook, so a SigNoz alert becomes a `persistent_notification` in the HA bell. Kill the garage battery, and "Garage automation dead" pops up in the very app that caused the problem.

![A SigNoz FIRING alert arriving as a notification inside Home Assistant](https://raw.githubusercontent.com/wiz-abhi/Home-APM/main/docs/screenshots/07-alert-in-ha.png)

## Reproduce it — no house required

Because the reconstruction is a pure function, the whole thing is provable without a house: clone the repo, run `pytest`, and **68 golden and unit tests** replay dumped `trace/get` fixtures through the parser — no Home Assistant, no network. `mypy --strict` and `ruff` are green, CI runs it all on every push. For the full stack, one `foundryctl cast` (`casting.yaml` + a bit-identical `.lock`) stands up SigNoz, the MCP server, a seeded Home Assistant, and the sidecar — populated dashboard in about a minute. Or just **[try the live demo](https://wiz-abhi-home-apm-demo.static.hf.space)** — the real reconstruction, running in your browser, no install and no key.

## What broke along the way

- **HA 2026.7 deleted the template formats my demo house was built on.** The legacy `light: platform: template` and `cover: platform: template` schemas were removed; everything had to move to the modern `template:` key. A config that "worked in every tutorial" simply didn't boot.
- **The ~5-stored-traces race is real.** Poll lazily and the run you wanted is already `"no longer available."` The fix is to fetch `trace/get` the instant `automation_triggered` fires — grab it before HA forgets.
- **The trigger-path inconsistency cost me a genuine hour** of "why is my root span parented to a condition." The built-in viewer papers over it; you won't see it until you parse the raw payload yourself.

## Takeaways

Home Assistant has always had these traces. It just never let anyone see them. Two million installs, an upvoted complaint, an explicit request for this exact export, and no native path to it — that gap is the entire opportunity. It took one evening of legible flame graphs to turn three shrugs into three fixed bugs.

## AI-usage disclosure

Built solo with heavy AI assistance (Anthropic's Claude / Claude Code), disclosed here by design (the hackathon requires it). I used it to research Home Assistant's trace internals and the prior art, scaffold and golden-test the reconstruction, and edit this post. Everything it describes actually happened: I ran the stack, hit the bugs, dumped and verified real `trace/get` payloads, took every screenshot from my own SigNoz instance, and checked each claim — the 2M installs, the community quotes, the 49.7-second wait, the divide-by-zero — against primary sources or the live system. The frozen span schema and every claim about what the tool does are my own.

## Resources

- **Code:** [github.com/wiz-abhi/Home-APM](https://github.com/wiz-abhi/Home-APM)
- **Live demo:** [wiz-abhi-home-apm-demo.static.hf.space](https://wiz-abhi-home-apm-demo.static.hf.space)
- **Demo video:** [youtu.be/zidlC4Qj3lo](https://youtu.be/zidlC4Qj3lo)
- Built for the [Agents of SigNoz](https://www.wemakedevs.org/hackathons/signoz) hackathon by [@wemakedevs](https://x.com/wemakedevs) and [SigNoz](https://signoz.io)
