"""Home APM Console — a small web front door for the project.

Home APM deliberately makes SigNoz the observability UI (dashboards, flame
graphs, service map, alerts). This console is the *front door* to that: a single
page that

  * lets anyone ask their house a plain-English question (the ``ask.py`` pipeline,
    over HTTP instead of a terminal),
  * shows the house is live (recent automation runs, straight from SigNoz), and
  * hands off into SigNoz and Home Assistant with one click.

It is a dependency-light stdlib ``http.server`` (no web framework) that reuses the
exact ``ask`` pipeline, so there is one source of truth for "ask your house".

Run:
    python tools/console/server.py            # serves on :8090

Environment:
    CONSOLE_PORT        console listen port (default 8090)
    SIGNOZ_PUBLIC_URL   browser-facing SigNoz origin for deep links (default
                        http://localhost:8080) — set to http://<vm-ip>:8080 on a VM
    HA_PUBLIC_URL       browser-facing Home Assistant origin (default
                        http://localhost:8123)
    MCP_URL             SigNoz MCP endpoint the server calls (default
                        http://localhost:8000/mcp)
    HOMEAPM_REPO_URL    repo link shown in the footer
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

# Reuse the ask pipeline (tools/ask is a script dir, not a package).
_ASK_DIR = pathlib.Path(__file__).resolve().parent.parent / "ask"
sys.path.insert(0, str(_ASK_DIR))

import ask  # noqa: E402
from mcp_client import McpClient  # noqa: E402

CONSOLE_PORT = int(os.environ.get("CONSOLE_PORT", "8090"))
SIGNOZ_PUBLIC_URL = os.environ.get("SIGNOZ_PUBLIC_URL", "http://localhost:8080").rstrip("/")
HA_PUBLIC_URL = os.environ.get("HA_PUBLIC_URL", "http://localhost:8123").rstrip("/")
REPO_URL = os.environ.get("HOMEAPM_REPO_URL", "https://github.com/wiz-abhi/Home-APM")

_ROOT_FILTER = "service.name = 'ha.automation' AND ha.node_path = '__root__'"
_RUN_SELECT = ["trace_id", "automation.name", "durationNano", "has_error", "timestamp"]


# --------------------------------------------------------------------------- #
# data helpers (reuse ask's query builder)
# --------------------------------------------------------------------------- #


def _recent_runs(time_range: str, limit: int) -> list[dict[str, Any]]:
    """Fetch recent automation-run roots from SigNoz via the MCP builder query."""
    query = ask._builder_query(_ROOT_FILTER, _RUN_SELECT, time_range=time_range, limit=limit)
    with McpClient() as client:
        rows = ask._rows_from_builder(
            client.call_tool("signoz_execute_builder_query", {"query": query})
        )
    runs: list[dict[str, Any]] = []
    for row in rows:
        trace_id = str(row.get("trace_id") or "")
        if not trace_id:
            continue
        name = str(row.get("automation.name") or "automation")
        runs.append(
            {
                "name": name,
                "is_sim": name.startswith("[sim]"),
                "seconds": round(ask._as_int(row.get("durationNano")) / 1e9, 2),
                "ok": not ask._truthy(row.get("has_error")),
                "trace_url": f"{SIGNOZ_PUBLIC_URL}/trace/{trace_id}",
                "ago": _ago(row.get("timestamp")),
            }
        )
    return runs


def _ago(raw: Any) -> str:
    """Best-effort 'N s/m ago' from a SigNoz timestamp (ISO string or epoch ns)."""
    now = time.time()
    ts: float | None = None
    if isinstance(raw, str) and raw:
        try:
            from datetime import datetime

            ts = datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
        except ValueError:
            ts = None
    if ts is None:
        n = ask._as_int(raw)
        if n > 1_000_000_000_000_000:  # nanoseconds
            ts = n / 1e9
        elif n > 1_000_000_000_000:  # milliseconds
            ts = n / 1e3
        elif n > 1_000_000_000:  # seconds
            ts = float(n)
    if ts is None:
        return ""
    delta = max(0, int(now - ts))
    if delta < 60:
        return f"{delta}s ago"
    if delta < 3600:
        return f"{delta // 60}m ago"
    return f"{delta // 3600}h ago"


def _status() -> dict[str, Any]:
    """Liveness + a recent-runs table for the console."""
    try:
        runs = _recent_runs("15m", 12)
        live = len(runs) > 0
        if not runs:  # quiet window — widen so the table is not empty
            runs = _recent_runs("6h", 12)
        return {"live": live, "runs": runs, "error": None}
    except Exception as err:  # surface any failure to the console UI, never 500
        return {"live": False, "runs": [], "error": str(err)}


def _links() -> dict[str, str]:
    return {
        "signoz": SIGNOZ_PUBLIC_URL,
        "dashboards": f"{SIGNOZ_PUBLIC_URL}/dashboards",
        "traces": f"{SIGNOZ_PUBLIC_URL}/traces-explorer",
        "services": f"{SIGNOZ_PUBLIC_URL}/services",
        "alerts": f"{SIGNOZ_PUBLIC_URL}/alerts",
        "home_assistant": HA_PUBLIC_URL,
        "repo": REPO_URL,
    }


# --------------------------------------------------------------------------- #
# HTTP handler
# --------------------------------------------------------------------------- #


class Handler(BaseHTTPRequestHandler):
    server_version = "HomeAPMConsole/1.0"

    def log_message(self, *_: Any) -> None:  # quiet default logging
        return

    def _send(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj: Any, code: int = 200) -> None:
        self._send(code, json.dumps(obj).encode("utf-8"), "application/json; charset=utf-8")

    def do_GET(self) -> None:  # BaseHTTPRequestHandler API name (do_VERB)
        route = urlparse(self.path)
        if route.path in ("/", "/index.html"):
            self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
            return
        if route.path == "/api/links":
            self._json(_links())
            return
        if route.path == "/api/status":
            self._json(_status())
            return
        if route.path == "/api/ask":
            params = parse_qs(route.query)
            question = (params.get("q") or [""])[0].strip()
            if not question:
                self._json({"error": "empty question"}, code=400)
                return
            result = ask.answer_question_result(question)
            self._json(
                {
                    "question": result.question,
                    "answer": result.answer,
                    "trace_id": result.trace_id,
                    "flame_url": result.flame_url,
                    "error": result.error,
                }
            )
            return
        self._send(404, b"not found", "text/plain; charset=utf-8")


# --------------------------------------------------------------------------- #
# the page (inline; no external assets so it works air-gapped on a VM)
# --------------------------------------------------------------------------- #

PAGE = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Home APM Console</title>
<style>
  :root{
    --bg:#0b0f17; --panel:#121826; --panel2:#0e1420; --line:#1e2636;
    --text:#e7edf6; --muted:#8b98ad; --accent:#5b9bff; --good:#3ecf8e; --bad:#ff6b6b;
  }
  *{box-sizing:border-box}
  body{margin:0;background:radial-gradient(1200px 600px at 50% -10%,#141c2b,var(--bg));
    color:var(--text);font:16px/1.55 "Segoe UI",system-ui,-apple-system,sans-serif;padding:0 20px 64px}
  .wrap{max-width:940px;margin:0 auto}
  header{padding:46px 0 8px;text-align:center}
  .brand{font-size:30px;font-weight:800;letter-spacing:-.5px}
  .brand .apm{color:var(--accent)}
  .tag{color:var(--muted);margin-top:6px}
  .live{display:inline-flex;align-items:center;gap:8px;margin-top:14px;font-size:13px;color:var(--muted)}
  .dot{width:9px;height:9px;border-radius:50%;background:#555}
  .dot.on{background:var(--good);box-shadow:0 0 0 4px rgba(62,207,142,.16)}
  .dot.off{background:var(--bad)}
  section{background:var(--panel);border:1px solid var(--line);border-radius:14px;
    padding:22px;margin-top:22px}
  h2{margin:0 0 14px;font-size:15px;font-weight:700;color:var(--muted);
    text-transform:uppercase;letter-spacing:1.4px}
  .askrow{display:flex;gap:10px}
  #q{flex:1;background:var(--panel2);border:1px solid var(--line);color:var(--text);
    border-radius:10px;padding:14px 16px;font-size:16px;outline:none}
  #q:focus{border-color:var(--accent)}
  button.go{background:var(--accent);color:#04122e;border:0;border-radius:10px;
    padding:0 20px;font-size:16px;font-weight:700;cursor:pointer}
  button.go:disabled{opacity:.55;cursor:default}
  .chips{display:flex;flex-wrap:wrap;gap:8px;margin-top:12px}
  .chip{background:var(--panel2);border:1px solid var(--line);color:var(--muted);
    border-radius:999px;padding:7px 13px;font-size:13px;cursor:pointer}
  .chip:hover{border-color:var(--accent);color:var(--text)}
  .answer{margin-top:16px;background:var(--panel2);border:1px solid var(--line);
    border-radius:10px;padding:16px 18px;display:none}
  .answer.show{display:block}
  .answer .txt{font-size:17px}
  .answer a{display:inline-block;margin-top:12px;color:var(--accent);text-decoration:none;
    font-weight:600}
  .answer a:hover{text-decoration:underline}
  .spin{color:var(--muted)}
  table{width:100%;border-collapse:collapse;font-size:14px}
  th,td{text-align:left;padding:9px 8px;border-bottom:1px solid var(--line)}
  th{color:var(--muted);font-weight:600;font-size:12px;text-transform:uppercase;letter-spacing:1px}
  td.sim{color:var(--muted)}
  .pill{font-size:12px;font-weight:700;padding:2px 9px;border-radius:999px}
  .pill.ok{background:rgba(62,207,142,.14);color:var(--good)}
  .pill.err{background:rgba(255,107,107,.14);color:var(--bad)}
  td a{color:var(--accent);text-decoration:none}
  td a:hover{text-decoration:underline}
  .btns{display:flex;flex-wrap:wrap;gap:12px}
  a.card{flex:1 1 180px;background:var(--panel2);border:1px solid var(--line);
    border-radius:10px;padding:16px;text-decoration:none;color:var(--text)}
  a.card:hover{border-color:var(--accent)}
  a.card .k{font-weight:700}
  a.card .d{color:var(--muted);font-size:13px;margin-top:3px}
  footer{color:var(--muted);font-size:13px;text-align:center;margin-top:28px;line-height:1.9}
  footer a{color:var(--accent);text-decoration:none}
  .note{color:var(--muted);font-size:12.5px;margin-top:8px}
</style></head>
<body><div class="wrap">
  <header>
    <div class="brand">Home&nbsp;<span class="apm">APM</span></div>
    <div class="tag">Distributed tracing for your smart home — every Home Assistant automation run, as a flame graph in SigNoz.</div>
    <div class="live"><span id="dot" class="dot"></span><span id="livetxt">checking…</span></div>
  </header>

  <section>
    <h2>Ask your house</h2>
    <div class="askrow">
      <input id="q" placeholder="why did my hallway lights turn on at 3am?" autocomplete="off"/>
      <button class="go" id="go">Ask</button>
    </div>
    <div class="chips" id="chips"></div>
    <div class="answer" id="ans"><div class="txt" id="anstxt"></div><a id="anslink" target="_blank"></a></div>
    <div class="note">Answers are generated live from real trace data in SigNoz (via the SigNoz MCP server).</div>
  </section>

  <section>
    <h2>Live house — recent automation runs</h2>
    <table><thead><tr><th>Automation</th><th>Duration</th><th>Status</th><th>When</th><th></th></tr></thead>
      <tbody id="runs"><tr><td colspan="5" class="spin">loading…</td></tr></tbody></table>
    <div class="note">Auto-refreshes every 15s. The seeded demo house fires automations on its own, so this stays live with no input.</div>
  </section>

  <section>
    <h2>Open in SigNoz</h2>
    <div class="btns" id="links"></div>
    <div class="note">Home APM makes SigNoz the observability UI — traces, dashboards, service map and alerts all live there.</div>
  </section>

  <footer id="foot"></footer>
</div>
<script>
const EX = ["why did my hallway lights turn on at 3am?","why is my morning routine slow?","did anything fail tonight?"];
const $ = s => document.querySelector(s);

function renderChips(){
  const c = $("#chips"); c.innerHTML="";
  EX.forEach(q=>{const b=document.createElement("span");b.className="chip";b.textContent=q;
    b.onclick=()=>{$("#q").value=q;ask();};c.appendChild(b);});
}
async function ask(){
  const q = $("#q").value.trim(); if(!q) return;
  const go=$("#go"); go.disabled=true;
  const ans=$("#ans"), txt=$("#anstxt"), link=$("#anslink");
  ans.classList.add("show"); txt.innerHTML='<span class="spin">asking your house…</span>'; link.style.display="none";
  try{
    const r = await fetch("/api/ask?q="+encodeURIComponent(q));
    const d = await r.json();
    if(d.error){ txt.textContent="⚠ "+d.error; }
    else{
      txt.textContent = d.answer;
      if(d.flame_url){ link.href=d.flame_url; link.textContent="Open the flame graph →"; link.style.display="inline-block"; }
    }
  }catch(e){ txt.textContent="⚠ "+e; }
  go.disabled=false;
}
async function refresh(){
  try{
    const d = await (await fetch("/api/status")).json();
    const dot=$("#dot"), lt=$("#livetxt");
    if(d.error){ dot.className="dot off"; lt.textContent="SigNoz not reachable"; }
    else if(d.live){ dot.className="dot on"; lt.textContent="live — receiving traces"; }
    else{ dot.className="dot off"; lt.textContent="idle — no runs in the last 15 min"; }
    const tb=$("#runs"); tb.innerHTML="";
    if(!d.runs || !d.runs.length){ tb.innerHTML='<tr><td colspan="5" class="spin">no runs yet</td></tr>'; }
    d.runs.forEach(r=>{
      const tr=document.createElement("tr");
      const pill = r.ok?'<span class="pill ok">ok</span>':'<span class="pill err">error</span>';
      tr.innerHTML = `<td class="${r.is_sim?'sim':''}">${r.name}</td><td>${r.seconds}s</td>`+
        `<td>${pill}</td><td class="sim">${r.ago||''}</td>`+
        `<td><a href="${r.trace_url}" target="_blank">trace →</a></td>`;
      tb.appendChild(tr);
    });
  }catch(e){ $("#livetxt").textContent="status error"; }
}
async function loadLinks(){
  const d = await (await fetch("/api/links")).json();
  const defs = [
    ["Dashboard", d.dashboards, "Home APM board — p95, errors, rooms"],
    ["Traces & saved views", d.traces, "Every automation run as a flame graph"],
    ["Service map", d.services, "Your house: automation → light/climate/cover"],
    ["Alerts", d.alerts, "Rules that notify back into Home Assistant"],
    ["Home Assistant", d.home_assistant, "The house itself"],
  ];
  const box=$("#links"); box.innerHTML="";
  defs.forEach(([k,href,dd])=>{const a=document.createElement("a");a.className="card";a.href=href;a.target="_blank";
    a.innerHTML=`<div class="k">${k} →</div><div class="d">${dd}</div>`;box.appendChild(a);});
  $("#foot").innerHTML = `Home APM · <a href="${d.repo}" target="_blank">source on GitHub</a> · `+
    `built for the Agents of SigNoz hackathon (Track 3). This instance is read-only for viewing.`;
}
$("#go").onclick=ask;
$("#q").addEventListener("keydown",e=>{if(e.key==="Enter")ask();});
renderChips(); loadLinks(); refresh(); setInterval(refresh,15000);
</script>
</body></html>
"""


def main() -> int:
    server = ThreadingHTTPServer(("0.0.0.0", CONSOLE_PORT), Handler)
    print(f"Home APM Console on http://0.0.0.0:{CONSOLE_PORT}  (SigNoz: {SIGNOZ_PUBLIC_URL})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
