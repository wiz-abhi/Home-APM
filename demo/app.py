"""Home APM — self-contained demo (Hugging Face Space).

The full project needs a 9-container stack (SigNoz + ClickHouse + Home Assistant
+ the sidecar). This demo is the one piece that stands alone: it takes the *real
recorded* ``trace/get`` payloads shipped in ``fixtures/``, runs the **actual**
pure reconstruction (``trace_reconstruct.reconstruct`` — the same function the 68
golden tests cover), and renders the resulting span tree as a flame graph right
in the browser. No SigNoz, no Home Assistant, no API key — the reconstruction
runs here, live.

Stdlib only (``http.server``). Listens on ``$PORT`` (7860 on HF Spaces).
"""

from __future__ import annotations

import json
import os
import pathlib
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from trace_reconstruct import Result, SpanSpec, StepType, reconstruct

HERE = pathlib.Path(__file__).resolve().parent
PORT = int(os.environ.get("PORT", "7860"))
REPO_URL = "https://github.com/wiz-abhi/Home-APM"

# The four purpose-built demo automations, each a real recorded run.
DEMOS = [
    ("hallway_lights_3am", "Hallway Lights 3AM", "the 3am mystery — a choose branch that passed when it shouldn't"),
    ("morning_routine", "Morning Routine", "the slow one — a long wait_for_trigger dominates the run"),
    ("good_night", "Good Night", "parallel + repeat + a red ERROR span (division by zero)"),
    ("garage_check", "Garage Check", "a clean run — nothing wrong here"),
]

# service.name -> flame-bar colour
SERVICE_COLOR = {
    "ha.automation": "#5b9bff",
    "ha.light": "#f2b64c",
    "ha.climate": "#3ecf8e",
    "ha.cover": "#8b78d8",
    "ha.input_boolean": "#6b7d99",
    "ha.input_number": "#6b7d99",
    "ha.persistent_notification": "#e07a5f",
    "ha.sensor": "#3ecf8e",
}


# --------------------------------------------------------------------------- #
# build the trace models once at startup
# --------------------------------------------------------------------------- #


def _depth_map(spans: list[SpanSpec]) -> dict[str, int]:
    by_id = {s.span_id: s for s in spans}
    depth: dict[str, int] = {}

    def d(sid: str) -> int:
        if sid in depth:
            return depth[sid]
        s = by_id[sid]
        depth[sid] = 0 if s.parent_span_id is None else d(s.parent_span_id) + 1
        return depth[sid]

    for s in spans:
        d(s.span_id)
    return depth


def _build_trace(slug: str, title: str, blurb: str) -> dict[str, Any]:
    payload = json.loads((HERE / "fixtures" / slug / "trace_get.json").read_text(encoding="utf-8"))
    spans = reconstruct(payload)
    t0 = min(s.start_unix_nano for s in spans)
    end = max(s.end_unix_nano for s in spans)
    total_ms = max(1.0, (end - t0) / 1e6)
    depth = _depth_map(spans)
    errors = sum(1 for s in spans if s.status_error)
    out_spans = []
    for s in sorted(spans, key=lambda x: (depth[x.span_id], x.start_unix_nano)):
        out_spans.append(
            {
                "id": s.span_id,
                "depth": depth[s.span_id],
                "name": s.name,
                "service": s.service_name,
                "kind": s.kind.value,
                "step_type": s.step_type.value,
                "node_path": s.node_path,
                "result": s.result.value,
                "error": s.status_error,
                "template_errors": s.template_errors,
                "start_ms": round((s.start_unix_nano - t0) / 1e6, 1),
                "dur_ms": round((s.end_unix_nano - s.start_unix_nano) / 1e6, 1),
                "color": SERVICE_COLOR.get(s.service_name, "#5b9bff"),
            }
        )
    return {
        "slug": slug,
        "title": title,
        "blurb": blurb,
        "room": next((s.automation_room for s in spans if s.automation_room), ""),
        "total_ms": round(total_ms, 1),
        "span_count": len(spans),
        "error_count": errors,
        "max_depth": max(depth.values()),
        "spans": out_spans,
    }


TRACES = [_build_trace(slug, title, blurb) for slug, title, blurb in DEMOS]
TRACES_BY_SLUG = {t["slug"]: t for t in TRACES}


# --------------------------------------------------------------------------- #
# "ask your house" — offline heuristic over the reconstructed spans
# --------------------------------------------------------------------------- #

_HINTS = {
    "hallway": "hallway_lights_3am",
    "3am": "hallway_lights_3am",
    "3 am": "hallway_lights_3am",
    "light": "hallway_lights_3am",
    "morning": "morning_routine",
    "slow": "morning_routine",
    "routine": "morning_routine",
    "good night": "good_night",
    "night": "good_night",
    "garage": "garage_check",
}


def _fmt_ms(ms: float) -> str:
    """One formatter for every duration we print — a 2.3ms run must not read '0.0s'."""
    return f"{ms / 1000:.1f}s" if ms >= 1000 else f"{ms:g}ms"


def _pick_trace(q: str) -> tuple[dict[str, Any], bool]:
    """Return (trace, sure) — ``sure`` is False when nothing in q named an automation."""
    ql = q.lower()
    for key, slug in _HINTS.items():
        if key in ql:
            return TRACES_BY_SLUG[slug], True
    if any(w in ql for w in ("fail", "error", "broke", "wrong", "crash")):
        errs = [t for t in TRACES if t["error_count"]]
        if errs:
            return errs[0], False
    if any(w in ql for w in ("slow", "slug", "latency", "long", "delay")):
        return max(TRACES, key=lambda t: t["total_ms"]), False
    return TRACES_BY_SLUG["hallway_lights_3am"], False


def _answer(q: str) -> dict[str, Any]:
    t, sure = _pick_trace(q)
    ql = q.lower()
    spans = t["spans"]
    non_root = [s for s in spans if s["node_path"] != "__root__"]
    slowest = max(non_root, key=lambda s: s["dur_ms"]) if non_root else None
    errs = [s for s in spans if s["error"]]
    trigger = next((s["name"] for s in spans if s["step_type"] == "trigger"), None)
    calls = [s for s in spans if s["step_type"] == "service_call"]

    if errs or any(w in ql for w in ("fail", "error", "wrong", "broke")):
        if errs:
            e = errs[0]
            detail = e["template_errors"] or "an error"
            ans = (
                f"Yes — <b>{t['title']}</b> failed: its <code>{e['name']}</code> step raised "
                f"<code>{detail}</code>. It surfaced on the <code>{e['service']}</code> service call inside the run."
            )
        else:
            ans = f"No failures in <b>{t['title']}</b> — every step finished cleanly across {t['span_count']} spans."
    elif any(w in ql for w in ("slow", "long", "latency", "delay")) and slowest:
        pct = round(slowest["dur_ms"] / t["total_ms"] * 100)
        ans = (
            f"<b>{t['title']}</b> was slow because its <code>{slowest['name']}</code> step took "
            f"<b>{_fmt_ms(slowest['dur_ms'])}</b> — {pct}% of the whole {_fmt_ms(t['total_ms'])} run."
        )
    else:
        tail = f"The whole run took {_fmt_ms(t['total_ms'])} across {t['span_count']} steps."
        if calls:
            ans = (
                f"<b>{t['title']}</b> ran because its <code>{trigger or 'trigger'}</code> fired and a "
                f"<code>choose</code> branch executed, which invoked <code>{calls[0]['name']}</code>. {tail}"
            )
        else:
            # no service_call spans at all — don't claim an invocation that never happened
            conds = sum(1 for s in spans if s["step_type"] == "condition")
            branch = (
                f" and its <code>choose</code> branch evaluated {conds} condition{'s' if conds > 1 else ''}"
                if conds
                else ""
            )
            ans = (
                f"<b>{t['title']}</b> ran because its <code>{trigger or 'trigger'}</code> fired{branch} — "
                f"no service call ran, so nothing in the house actually changed. {tail}"
            )
    if not sure:
        ans = (
            '<span class="hedge">I couldn\'t match that to an automation by name — '
            f"showing the closest trace I have, <b>{t['title']}</b>.</span>" + ans
        )
    return {"answer": ans, "trace": t["slug"], "title": t["title"]}


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_: Any) -> None:
        return

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        route = urlparse(self.path)
        if route.path in ("/", "/index.html"):
            # Prefer the shiny static build (single source of truth, generated by
            # build_static.py) so the local server matches the deployed Space; fall
            # back to the built-in PAGE if it hasn't been built yet.
            static = HERE / "static" / "index.html"
            body = static.read_bytes() if static.exists() else PAGE.encode()
            self._send(200, body, "text/html; charset=utf-8")
        elif route.path == "/api/traces":
            self._send(200, json.dumps(TRACES).encode(), "application/json")
        elif route.path == "/api/ask":
            q = (parse_qs(route.query).get("q") or [""])[0].strip()
            body = _answer(q) if q else {"answer": "Ask me about one of the four automations.", "trace": None}
            self._send(200, json.dumps(body).encode(), "application/json")
        else:
            self._send(404, b"not found", "text/plain")


PAGE = (
    """<!doctype html><html lang="en"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Home APM — live trace demo</title>
<style>
  :root{--bg:#0b0f17;--panel:#121826;--panel2:#0e1420;--line:#1e2636;--text:#e7edf6;--muted:#8b98ad;--accent:#5b9bff;--good:#3ecf8e;--bad:#ff6b6b}
  *{box-sizing:border-box}
  body{margin:0;background:radial-gradient(1100px 520px at 50% -8%,#141c2b,var(--bg));color:var(--text);font:15px/1.5 "Segoe UI",system-ui,sans-serif;padding:0 18px 60px}
  .wrap{max-width:1080px;margin:0 auto}
  header{padding:34px 0 6px;text-align:center}
  .brand{font-size:26px;font-weight:800}.brand .a{color:var(--accent)}
  .sub{color:var(--muted);margin-top:6px;font-size:14px}
  .sub a{color:var(--accent);text-decoration:none}
  .pill{display:inline-block;margin-top:12px;font-size:12.5px;color:var(--good);background:rgba(62,207,142,.12);border:1px solid rgba(62,207,142,.25);border-radius:999px;padding:5px 13px}
  .ask{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:18px;margin-top:22px}
  .ask h2,.viz h2{margin:0 0 12px;font-size:13px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:1.3px}
  .row{display:flex;gap:10px}
  #q{flex:1;background:var(--panel2);border:1px solid var(--line);color:var(--text);border-radius:10px;padding:12px 14px;font-size:15px;outline:none}
  #q:focus{border-color:var(--accent)}
  button.go{background:var(--accent);color:#04122e;border:0;border-radius:10px;padding:0 20px;font-weight:700;cursor:pointer}
  .chips{display:flex;flex-wrap:wrap;gap:8px;margin-top:11px}
  .chip{background:var(--panel2);border:1px solid var(--line);color:var(--muted);border-radius:999px;padding:6px 12px;font-size:12.5px;cursor:pointer}
  .chip:hover{border-color:var(--accent);color:var(--text)}
  #ans{margin-top:14px;background:var(--panel2);border:1px solid var(--line);border-radius:10px;padding:14px 16px;display:none;font-size:15.5px}
  #ans.show{display:block}#ans code{background:#1b2436;padding:1px 5px;border-radius:4px;font-size:13px}
  .hedge{display:block;color:var(--muted);font-size:13px;margin-bottom:6px}
  .viz{margin-top:22px}
  .tabs{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:14px}
  .tab{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:9px 13px;cursor:pointer;font-size:13.5px}
  .tab .t{font-weight:700}.tab .b{color:var(--muted);font-size:12px;margin-top:2px;max-width:230px}
  .tab.on{border-color:var(--accent);background:#131d31}
  .tab .badge{font-size:11px;font-weight:700;padding:1px 7px;border-radius:999px;margin-left:6px}
  .badge.ok{background:rgba(62,207,142,.14);color:var(--good)}.badge.err{background:rgba(255,107,107,.14);color:var(--bad)}
  .meta{color:var(--muted);font-size:13px;margin:2px 0 10px}
  #flame{position:relative;background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:14px;overflow-x:auto}
  .bar{position:absolute;height:22px;border-radius:4px;font-size:11.5px;line-height:22px;color:#08101f;padding:0 6px;white-space:nowrap;overflow:hidden;cursor:default;border:1px solid rgba(0,0,0,.25)}
  .bar.err{outline:2px solid var(--bad);outline-offset:-2px}
  .fc{position:relative}
  .axis{position:absolute;left:0;right:0;color:var(--muted);font-size:11px;border-top:1px dashed var(--line);padding-top:4px;display:flex;justify-content:space-between}
  .fwrap{position:relative}
  .fwrap .fade{position:absolute;top:1px;right:1px;bottom:1px;width:52px;border-radius:0 12px 12px 0;pointer-events:none;opacity:0;transition:opacity .25s;
    background:linear-gradient(90deg,rgba(11,15,23,0),rgba(11,15,23,.92))}
  .fwrap .shint{position:absolute;right:11px;bottom:8px;font-size:10.5px;color:var(--muted);pointer-events:none;opacity:0;transition:opacity .25s;
    background:rgba(10,20,36,.85);border:1px solid var(--line);border-radius:999px;padding:2px 9px}
  .fwrap.scroll .fade,.fwrap.scroll .shint{opacity:1}
  .fwrap.scroll.atend .fade,.fwrap.scroll.atend .shint{opacity:0}
  .legend{display:flex;flex-wrap:wrap;align-items:center;gap:7px;margin-top:11px}
  .legend .lg{display:inline-flex;align-items:center;gap:6px;background:var(--panel2);border:1px solid var(--line);border-radius:999px;padding:4px 10px;font-size:11px;color:var(--muted)}
  .legend .sw{width:9px;height:9px;border-radius:3px;flex:none}
  .legend .sw.e{background:transparent;box-shadow:0 0 0 1.5px var(--bad)}
  #tip{position:fixed;pointer-events:none;background:#0a1424;border:1px solid var(--line);border-radius:8px;padding:9px 11px;font-size:12.5px;max-width:320px;display:none;z-index:9;box-shadow:0 8px 24px rgba(0,0,0,.5)}
  #tip b{color:var(--accent)}#tip .k{color:var(--muted)}
  #tip .dz{color:var(--muted);font-size:11px;margin-top:7px;border-top:1px solid var(--line);padding-top:6px}
  footer{color:var(--muted);font-size:12.5px;text-align:center;margin-top:26px;line-height:1.8}
  footer a{color:var(--accent);text-decoration:none}
</style></head><body><div class="wrap">
  <header>
    <div class="brand">Home&nbsp;<span class="a">APM</span></div>
    <div class="sub">Home Assistant automation traces, reconstructed into flame graphs. &nbsp;<a href="__REPO__" target="_blank">source on GitHub →</a></div>
    <div class="pill">● Live demo — real recorded traces, reconstructed in your browser. No SigNoz, no key.</div>
  </header>

  <section class="ask">
    <h2>Ask your house</h2>
    <div class="row"><input id="q" placeholder="why is my morning routine slow?" autocomplete="off"/><button class="go" id="go">Ask</button></div>
    <div class="chips" id="chips"></div>
    <div id="ans"></div>
  </section>

  <section class="viz">
    <h2>The flame graph</h2>
    <div class="tabs" id="tabs"></div>
    <div class="meta" id="meta"></div>
    <div class="fwrap"><div id="flame"></div><div class="fade"></div><div class="shint">scroll sideways →</div></div>
    <div class="legend" id="legend"></div>
  </section>

  <footer>
    Reconstructed by <code>trace_reconstruct()</code> — the same pure function covered by 73 golden tests.<br/>
    In the full project this is what <b>SigNoz</b> renders, alongside metrics, logs, dashboards, alerts, and the MCP "ask" server. &nbsp;<a href="__REPO__" target="_blank">Home APM →</a>
  </footer>
</div>
<div id="tip"></div>
<script>
const COLORS=__COLORS__;
const EX=["why did my hallway lights turn on at 3am?","why is my morning routine slow?","did anything fail tonight?"];
const $=s=>document.querySelector(s);let TRACES=[],cur=0;
const esc=s=>String(s).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
// one formatter for every duration we print — sub-second runs must not read "0.0s"
const fmtMs=(x,d)=>x>=1000?(x/1000).toFixed(d||1)+'s':x+'ms';
function chips(){$("#chips").innerHTML="";EX.forEach(q=>{const b=document.createElement("span");b.className="chip";b.textContent=q;b.onclick=()=>{$("#q").value=q;ask();};$("#chips").appendChild(b);});}
async function ask(){const q=$("#q").value.trim();if(!q)return;const a=$("#ans");a.classList.add("show");a.innerHTML="…";
  try{const d=await (await fetch("/api/ask?q="+encodeURIComponent(q))).json();a.innerHTML=d.answer+(d.trace?` &nbsp;<a href="#" style="color:var(--accent);text-decoration:none" onclick="select('${d.trace}');return false;">show the flame graph →</a>`:"");if(d.trace)select(d.trace);}catch(e){a.textContent="error";}}
function tabs(){const el=$("#tabs");el.innerHTML="";TRACES.forEach((t,i)=>{const d=document.createElement("div");d.className="tab"+(i===cur?" on":"");
  d.innerHTML=`<div class="t">${esc(t.title)}<span class="badge ${t.error_count?'err':'ok'}">${t.error_count?t.error_count+' err':'ok'}</span></div><div class="b">${esc(t.blurb)}</div>`;
  d.onclick=()=>{cur=i;render();};el.appendChild(d);});}
window.select=slug=>{const i=TRACES.findIndex(t=>t.slug===slug);if(i>=0){cur=i;render();window.scrollTo({top:$(".viz").offsetTop-20,behavior:"smooth"});}};
// tooltip: hover on desktop, tap-to-pin on touch (bars hold the only copy of the
// span name / service / duration / error text, so a finger must be able to reach it)
let pinned=false,tmoved=false;
const tipHTML=s=>`<b>${esc(s.name)}</b><br/><span class="k">service</span> ${esc(s.service)} · <span class="k">${esc(s.step_type)}</span><br/><span class="k">duration</span> ${fmtMs(s.dur_ms,2)}<br/><span class="k">path</span> ${esc(s.node_path)}`+(s.template_errors?`<br/><span style="color:var(--bad)">${esc(s.template_errors)}</span>`:"");
function showTip(s,x,y){const p=$("#tip");p.innerHTML=tipHTML(s);p.style.display="block";
  p.style.left=Math.max(8,Math.min(x+14,innerWidth-p.offsetWidth-8))+"px";
  p.style.top=Math.max(8,Math.min(y+14,innerHeight-p.offsetHeight-8))+"px";}
function hideTip(){$("#tip").style.display="none";}
function pinTip(s,b){const r=b.getBoundingClientRect();showTip(s,r.left-14,r.bottom-8);pinned=true;
  $("#tip").insertAdjacentHTML("beforeend",'<div class="dz">tap anywhere to close</div>');}
function unpin(){pinned=false;hideTip();}
function legend(){const used=new Set();TRACES.forEach(t=>t.spans.forEach(s=>used.add(s.service)));let h="";
  for(const svc in COLORS){if(!used.has(svc))continue;
    h+=`<span class="lg"><i class="sw" style="background:${esc(COLORS[svc])}"></i>${esc(svc)}</span>`;}
  $("#legend").innerHTML=h+`<span class="lg"><i class="sw e"></i>error</span>`;}
function scrollHint(){const f=$("#flame"),ov=f.scrollWidth-f.clientWidth>4;
  f.parentElement.classList.toggle("scroll",ov);
  f.parentElement.classList.toggle("atend",ov&&f.scrollLeft+f.clientWidth>=f.scrollWidth-6);}
function render(){tabs();unpin();const t=TRACES[cur];
  $("#meta").innerHTML=`${fmtMs(t.total_ms)} · ${t.span_count} spans · depth ${t.max_depth}${t.room?' · '+esc(t.room):''}`;
  const f=$("#flame");f.innerHTML="";
  const W=Math.max(680,f.clientWidth-28),rowH=26,H=(t.max_depth+1)*rowH+36;
  // everything lives inside one W-wide layer so the axis scrolls with the bars
  const fc=document.createElement("div");fc.className="fc";fc.style.width=W+"px";fc.style.height=H+"px";
  t.spans.forEach(s=>{const x=s.start_ms/t.total_ms*W;const w=Math.max(3,s.dur_ms/t.total_ms*W);
    const b=document.createElement("div");b.className="bar"+(s.error?" err":"");
    b.style.left=x+"px";b.style.top=(s.depth*rowH)+"px";b.style.width=w+"px";b.style.background=s.color;
    if(w>46)b.textContent=s.name;
    b.onmousemove=e=>{if(!pinned)showTip(s,e.clientX,e.clientY);};
    b.onmouseleave=()=>{if(!pinned)hideTip();};
    b.onclick=e=>{e.stopPropagation();unpin();pinTip(s,b);};
    b.addEventListener("touchstart",()=>{tmoved=false;},{passive:true});
    b.addEventListener("touchend",e=>{if(tmoved)return;e.stopPropagation();unpin();pinTip(s,b);},{passive:true});
    fc.appendChild(b);});
  const ax=document.createElement("div");ax.className="axis";ax.style.top=((t.max_depth+1)*rowH)+"px";
  ax.innerHTML=`<span>0</span><span>${fmtMs(t.total_ms)}</span>`;fc.appendChild(ax);
  f.appendChild(fc);legend();scrollHint();}
$("#go").onclick=ask;$("#q").addEventListener("keydown",e=>{if(e.key==="Enter")ask();});
$("#flame").addEventListener("scroll",scrollHint,{passive:true});
$("#flame").addEventListener("touchmove",()=>{tmoved=true;},{passive:true});
addEventListener("click",unpin);addEventListener("touchstart",unpin,{passive:true});
fetch("/api/traces").then(r=>r.json()).then(d=>{TRACES=d;chips();render();});
addEventListener("resize",()=>TRACES.length&&render());
</script></body></html>
""".replace("__REPO__", REPO_URL)
    # the legend is generated from this map, so it can never drift from the bars
    .replace("__COLORS__", json.dumps(SERVICE_COLOR, separators=(",", ":")))
)


def main() -> int:
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"Home APM demo on :{PORT}  ({len(TRACES)} traces loaded)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
