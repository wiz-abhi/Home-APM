# Ask your house

A tiny CLI that answers plain-English questions about your Home Assistant
automations in **one sentence**, grounded in live trace data from SigNoz. Spec
item #8 — the "ask your house" front door.

```
$ python tools/ask/ask.py "why did my hallway lights turn on at 3am?"
The hallway lights turned on because the template condition in your automation
evaluated to true, triggering the first branch of your choose action. This
entire automation process completed in exactly 1ms.

  trace_id:    2af0c130dbeead47d1597e6cf0ab4ecd
  flame graph: http://localhost:8080/trace/2af0c130dbeead47d1597e6cf0ab4ecd
```

## How it works (single-turn orchestration)

1. **Parse** the question with `gemini-3.1-flash-lite` into a structured intent
   `{question_type: why|slow|failed|status, automation_hint, time_range}`.
   (Deterministic heuristic fallback if the LLM is unreachable.)
2. **Deterministic MCP tool chain** over the SigNoz MCP server (raw JSON-RPC,
   `mcp_client.py`):
   - `signoz_get_field_values` → fuzzy-resolve the hint to a real
     `automation.name`;
   - `signoz_execute_builder_query` → find the single most relevant run
     (slowest span for *slow*, most-recent error span for *failed*, latest run
     for *why/status*);
   - `signoz_execute_builder_query` → pull that run's full span tree, projecting
     the frozen §0A attributes (`ha.step_type`, `ha.node_path`, `ha.result`,
     `ha.template_errors`, durations, errors).
3. **Extract** the causal facts (trigger, choose branches, silently-passing
   conditions, the slowest step, service-call effects, template errors).
4. **Narrate** in one sentence + one supporting detail via a final Gemini call
   (deterministic fallback narration if the LLM is unreachable). Print the
   answer, the `trace_id`, and a SigNoz flame-graph deep link.

Every causal fact comes from real trace data — Gemini only translates natural
language at the two ends. Typical end-to-end latency: **2–4 s** (target < 10 s).

## The three demo questions

```bash
python tools/ask/ask.py "why did my hallway lights turn on at 3am?"
python tools/ask/ask.py "why is my morning routine slow?"
python tools/ask/ask.py "did anything fail tonight?"
```

- **Hallway** → names the silently-passing `template` condition and the taken
  `choose` branch.
- **Morning routine** → names `wait_for_trigger` as 99.99% of the run (~51.5 s).
- **Did anything fail** → names the `good_night` template error
  (`ZeroDivisionError: division by zero` on `persistent_notification.create`).

## Requirements

- Runs with the project venv (only `httpx`, already a project dependency — no
  `openai` package needed; Gemini is called over its OpenAI-compatible HTTP
  endpoint directly).
- `GEMINI_API_KEY` in the environment (on this Windows host it is also read from
  the `HKCU\Environment` user store automatically). The key is never printed.
- The SigNoz MCP server reachable at `http://localhost:8000/mcp` and the sidecar
  emitting `ha.automation` traces to SigNoz.

If no run matches (empty result), the CLI says so and suggests widening the
window or triggering a run; trigger fresh traces with the demo burst
(`script.demo_burst`) or wait 1–3 min for the simulators.

## MCP auth

The MCP server authenticates with a service-account key baked into the
`signoz-mcp` container. This session's key is **valid** (verified: no 401/403).
If it ever expires, `mcp_client.py` raises a clear `McpError` naming the 401/403;
to rotate, create a new SigNoz API key and restart the MCP container with the new
`SIGNOZ_API_KEY`. If MCP is wholly unavailable, the same causal chain can be run
against the SigNoz `query_range` HTTP API — but MCP is the shipped, primary path.

## Files

- `ask.py` — the CLI and single-turn orchestrator.
- `mcp_client.py` — minimal raw JSON-RPC MCP client (initialize → session header
  → `notifications/initialized` → `tools/call`; handles SSE responses).
- `test_ask.py` — offline unit tests for intent parsing and fact extraction
  (`python -m pytest tools/ask/test_ask.py`, no house or network required).
