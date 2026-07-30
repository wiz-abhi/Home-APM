# Home APM — Narration Script (record in one continuous take)

> Note: spoken durations are approximate; the reproducible fixture value is 51.5 s (99.99% of the run).

**How to record (please read once first):**
- Quiet room, one continuous take. Speak naturally and conversationally — like explaining it to a friend, not reading.
- **Pause ~2 seconds between each numbered scene** (say the pause, don't say the numbers). Those gaps let me split and sync cleanly.
- If you flub a line: pause a beat, then redo just that sentence — I'll cut the bad take out.
- Export as `.wav`, `.m4a`, or `.mp3` and send it. That's all — I handle the rest.
- Total spoken time ≈ **3.5–4 minutes**. Don't rush; the visuals stretch to fit you.

Everything in **[brackets]** is what will be on screen — you don't read it.

---

### 1 · The hook  *(≈15s)*  — [sketch: a dark house, a light flicks on, a big "?"]

A few weeks ago, my hallway lights turned on at three in the morning. Nothing was broken — no error, no crash. Home Assistant just… did it, and it couldn't tell me why. And that bugged me — because Home Assistant actually records a full trace of every automation it runs. It just never lets you see it.

*(pause ~2s)*

### 2 · The problem  *(≈22s)*  — [sketch: "2,000,000 homes" counter; a cryptic trace path scribbled out]

And this isn't some niche hobby problem. Over two million homes run on Home Assistant. Every one of them has automations — and when one misbehaves, you're basically on your own. Its built-in trace view shows you cryptic paths like "conditions, zero, conditions, one." You can't scroll it, you can't click it, and it only keeps the last five runs — so the one you actually need is usually already gone. People have literally asked for a way to export these traces into real observability tools. Nobody built it. So I did.

*(pause ~2s)*

### 3 · The idea  *(≈18s)*  — [sketch: Home Assistant → OpenTelemetry → SigNoz; a house morphing into a flame graph]

This is Home APM. It's a small Python sidecar that grabs each automation's raw trace, rebuilds it into an OpenTelemetry span tree, and ships it to SigNoz — the open-source, OpenTelemetry-native observability platform. The result: every automation run becomes a real flame graph. Your house, as a distributed trace.

*(pause ~2s)*

### 4 · How it works  *(≈28s)*  — [sketch animation: a flat path-keyed dict → a pure function → a nested span tree drawing itself]

Here's the part I'm proud of. Home Assistant doesn't hand you a tree — it hands you a flat dictionary, keyed by these node paths. The whole trick is rebuilding the tree from those strings: working out which step lives inside which branch, handling parallel blocks that genuinely overlap in time, and repeat loops that show up as a list under a single key. And that reconstruction is one pure function — no network, no side effects — which means I can test it offline against real recorded traces. Sixty-eight of those tests, all passing.

*(pause ~2s)*

### 5 · See it on SigNoz  *(≈50s)*  — [screen recording: the real SigNoz UI]

So let's actually look. Here's that three a.m. run in SigNoz. Instead of a cryptic path, it's a named waterfall — and right away you can see the choose branch that fired when it shouldn't have. Here's my morning routine: one single step — a wait — eating forty-nine seconds. Completely invisible in Home Assistant; obvious here. And this is my good-night automation: a parallel block with overlapping bars, a repeat loop as stacked iterations, and a red error span — a template that divided by zero. And every run's log line names its exact trace, so a log and its flame graph are one click apart. There's a dashboard with plain-English panels, a live service map of the whole house — and alerts, which route right back into Home Assistant as a notification. Observability that tells the house about itself.

*(pause ~2s)*

### 6 · The UI I built  *(≈25s)*  — [screen recording: the Home APM demo UI / console you built]

I also built a front end for it. You can ask your house a question in plain English — "why is my morning routine slow?" — and it answers in one sentence, grounded in the real trace data, and drops you straight onto the flame graph. And the best part: it's live right now, in your browser — no install, no key. The exact same reconstruction you just saw, running on real recorded traces.

*(pause ~2s)*

### 7 · Reproduce it & close  *(≈20s)*  — [sketch: one terminal command; then "Try it live" + repo]

And the whole thing installs with a single command through Foundry — SigNoz, a seeded demo house, and the sidecar, all at once. So you can reproduce every single thing I just showed you. Home Assistant always had these traces. It just never let anyone see them. Now it does. Give your house an APM.

---

*Built for the Agents of SigNoz hackathon (Track 3). Repo: github.com/wiz-abhi/Home-APM · Live: wiz-abhi-home-apm-demo.static.hf.space*
