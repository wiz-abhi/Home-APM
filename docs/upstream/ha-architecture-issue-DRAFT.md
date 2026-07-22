# DRAFT — Home Assistant architecture issue (NOT YET FILED)

> **This is an unsent draft.** It is written to be filed by the project author,
> **manually, after the hackathon submission**, at
> <https://github.com/home-assistant/architecture/issues>. Nothing here has been
> posted to Home Assistant. Before filing: re-verify the thread links still
> resolve, confirm no duplicate architecture issue exists, and soften/expand per
> current community norms. The author files it as themselves — Home APM does not
> auto-file anything.

---

**Title:** Native OpenTelemetry trace export for automation runs

**Category:** Architecture proposal / feature request

**Body:**

### The problem

Home Assistant already records rich execution traces for every automation run
(`trace/get` over the WebSocket API), but the only way to read them is the
built-in trace view, and for non-trivial automations that view is hard to use.
This is a recurring, documented complaint, not a one-off:

- In community thread [767431][t1] a self-identified professional developer
  calls the built-in trace view *"mostly useless"*: nested steps render as
  cryptic node-path strings like `conditions/0/conditions/1/conditions/0`, the
  graph cannot be scrolled, you cannot click a node you cannot see, and the
  logbook panel often comes up empty. Home Assistant also keeps only about five
  traces per automation, so the run you actually need to debug is frequently
  *"Chosen trace is no longer available"* by the time you look.

- In community thread [795531][t2] a user asks, essentially verbatim, whether
  Home Assistant can export its traces to an OpenTelemetry Collector, describing
  it as a *"tremendous benefit"*. There is currently **no** such path: Home
  Assistant has no native OpenTelemetry trace export.

The data to solve this already exists inside Home Assistant. What is missing is a
supported way to get it into the mature, purpose-built tooling the wider software
world already uses for exactly this shape of problem (distributed traces / flame
graphs / span waterfalls).

### The proposal

Add an **optional** OpenTelemetry (OTLP) trace exporter for automation and script
runs. Concretely:

1. On each automation/script run, in addition to storing the trace, emit the run
   as an OTLP span tree to a configured OTLP endpoint (HTTP `:4318` / gRPC
   `:4317`), gated behind an opt-in config entry (endpoint, headers, on/off).
2. Model the run as a span tree: a root span per run, child spans per executed
   step, using the execution timestamps Home Assistant already records. Carry the
   existing structured detail as span attributes (node path, step type, result,
   changed variables, template errors, `context.id`), and set OTel status =
   ERROR on failed steps.
3. Reuse `context.id` as the correlation key so logs/logbook entries and the
   trace of the run that produced them can be linked.

This is deliberately narrow: it does not change the trace format, the trace view,
or retention. It adds one optional outbound exporter. Users who want it point it
at any OTLP-compatible backend (an OpenTelemetry Collector, Jaeger, Grafana
Tempo, SigNoz, etc.); users who do not are unaffected.

### Prior art / proof this is feasible

A working out-of-tree implementation already exists as evidence the data and the
mapping are sufficient: **Home APM**, a Python sidecar that subscribes to the WS
API, pulls each run's `trace/get` payload, reconstructs it into an OTLP span tree
(using the real per-element start timestamps HA records; step *ends* are inferred
as next-in-scope start since HA stores no per-step end), and exports it over OTLP
to a self-hosted SigNoz. Cryptic node paths become named, clickable spans;
parallel/repeat blocks render correctly; `context.id` links logs to the
originating run. It demonstrates that native export is a tractable, additive
change — the sidecar only exists because there is no in-core path.

For completeness, adjacent projects that instrument Home Assistant but do **not**
solve this specific problem: `ha-kafka-net` instruments its own .NET automation
framework (not HA's native automation traces), and `detektr` produces spans for a
computer-vision pipeline. Neither converts Home Assistant's own automation trace
records — reinforcing that a native exporter would fill a real gap.

### Why in-core rather than only a sidecar

A sidecar must re-derive structure from `trace/get` payloads and duplicate
knowledge of Home Assistant's step model; it also cannot see per-step *end*
times that the engine itself has at execution time. An in-core exporter can emit
spans directly from the automation engine with exact timings and no
reconstruction, and would be maintained alongside the trace format it depends on.

### Scope / non-goals

- Opt-in only; default off. No behavior change for users who don't enable it.
- No new required dependency in the default install (exporter deps behind the
  optional integration).
- Not a replacement for the built-in trace view — a complement for users who
  want persistence, flame-graph UIs, and correlation with the rest of their
  observability stack.

### Open questions for maintainers

- Preferred surface: a core integration/config entry vs. an extension point the
  existing trace machinery calls?
- Should scripts, scenes, and template entities share the same exporter path?
- Attribute schema: is there appetite to standardize the span attribute names so
  downstream tooling is portable across HA versions?

[t1]: https://community.home-assistant.io/t/767431
[t2]: https://community.home-assistant.io/t/795531

---

_Draft prepared as part of the Home APM project (Agents of SigNoz, Track 3).
Author to file manually post-submission. All facts above were verified against
primary sources; re-verify thread contents before posting._
