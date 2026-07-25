"""Build a fully static, server-free version of the demo (for a free HF Static Space).

Runs the REAL ``trace_reconstruct.reconstruct`` on the recorded fixtures now, bakes
the resulting span data into ``static/index.html``, and ships the flame-graph +
"ask" logic as client-side JS. No Python server, no API key — a judge just opens
the page. Regenerate with:  python demo/build_static.py
"""

from __future__ import annotations

import json
import pathlib

import app  # reuse the exact same _build_trace / TRACES

HERE = pathlib.Path(__file__).resolve().parent
REPO_URL = app.REPO_URL

HTML = """<!doctype html><html lang="en"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Home APM — live trace demo</title>
<style>
  :root{--bg:#0b0f17;--panel:#121826;--panel2:#0e1420;--line:#1e2636;--text:#e7edf6;--muted:#8b98ad;--accent:#5b9bff;--good:#3ecf8e;--bad:#ff6b6b}
  *{box-sizing:border-box}
  body{margin:0;background:radial-gradient(1100px 520px at 50% -8%,#141c2b,var(--bg));color:var(--text);font:15px/1.5 "Segoe UI",system-ui,sans-serif;padding:0 18px 60px}
  .wrap{max-width:1080px;margin:0 auto}
  header{padding:34px 0 6px;text-align:center}
  .brand{font-size:26px;font-weight:800}.brand .a{color:var(--accent)}
  .sub{color:var(--muted);margin-top:6px;font-size:14px}.sub a{color:var(--accent);text-decoration:none}
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
  .axis{color:var(--muted);font-size:11px;border-top:1px dashed var(--line);margin-top:6px;padding-top:4px;display:flex;justify-content:space-between}
  #tip{position:fixed;pointer-events:none;background:#0a1424;border:1px solid var(--line);border-radius:8px;padding:9px 11px;font-size:12.5px;max-width:320px;display:none;z-index:9;box-shadow:0 8px 24px rgba(0,0,0,.5)}
  #tip b{color:var(--accent)}#tip .k{color:var(--muted)}
  footer{color:var(--muted);font-size:12.5px;text-align:center;margin-top:26px;line-height:1.8}
  footer a{color:var(--accent);text-decoration:none}
</style></head><body><div class="wrap">
  <header>
    <div class="brand">Home&nbsp;<span class="a">APM</span></div>
    <div class="sub">Home Assistant automation traces, reconstructed into flame graphs. &nbsp;<a href="__REPO__" target="_blank">source on GitHub →</a></div>
    <div class="pill">● Real recorded traces, reconstructed by the actual pure function. No SigNoz, no key.</div>
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
    <div id="flame"></div>
  </section>
  <footer>
    Reconstructed by <code>trace_reconstruct()</code> — the same pure function covered by 68 golden tests.<br/>
    In the full project this is what <b>SigNoz</b> renders, alongside metrics, logs, dashboards, alerts, and the MCP "ask" server. &nbsp;<a href="__REPO__" target="_blank">Home APM →</a>
  </footer>
</div>
<div id="tip"></div>
<script>
const TRACES=__TRACES__;
const EX=["why did my hallway lights turn on at 3am?","why is my morning routine slow?","did anything fail tonight?"];
const $=s=>document.querySelector(s);let cur=0;
const esc=s=>String(s).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
const bySlug=s=>TRACES.find(t=>t.slug===s);
const HINTS=[["hallway","hallway_lights_3am"],["3am","hallway_lights_3am"],["3 am","hallway_lights_3am"],["light","hallway_lights_3am"],["morning","morning_routine"],["routine","morning_routine"],["good night","good_night"],["night","good_night"],["garage","garage_check"]];
function pickTrace(q){const ql=q.toLowerCase();
  for(const [k,slug] of HINTS){if(ql.includes(k))return bySlug(slug);}
  if(/(fail|error|broke|wrong|crash)/.test(ql)){const e=TRACES.find(t=>t.error_count);if(e)return e;}
  if(/(slow|latency|long|delay)/.test(ql))return TRACES.reduce((a,b)=>b.total_ms>a.total_ms?b:a);
  return bySlug("hallway_lights_3am");}
function answer(q){const t=pickTrace(q),ql=q.toLowerCase();
  const nonRoot=t.spans.filter(s=>s.node_path!=="__root__");
  const slowest=nonRoot.reduce((a,b)=>(!a||b.dur_ms>a.dur_ms)?b:a,null);
  const errs=t.spans.filter(s=>s.error);
  const trigger=(t.spans.find(s=>s.step_type==="trigger")||{}).name;
  const calls=t.spans.filter(s=>s.step_type==="service_call");
  let ans;
  if(errs.length||/(fail|error|wrong|broke)/.test(ql)){
    if(errs.length){const e=errs[0],d=e.template_errors||"an error";
      ans=`Yes — <b>${esc(t.title)}</b> failed: its <code>${esc(e.name)}</code> step raised <code>${esc(d)}</code>. It surfaced on the <code>${esc(e.service)}</code> service call inside the run.`;}
    else ans=`No failures in <b>${esc(t.title)}</b> — every step finished cleanly across ${t.span_count} spans.`;
  }else if(/(slow|long|latency|delay)/.test(ql)&&slowest){const pct=Math.round(slowest.dur_ms/t.total_ms*100);
    ans=`<b>${esc(t.title)}</b> was slow because its <code>${esc(slowest.name)}</code> step took <b>${(slowest.dur_ms/1000).toFixed(1)}s</b> — ${pct}% of the whole ${(t.total_ms/1000).toFixed(1)}s run.`;
  }else{const eff=calls.length?calls[0].name:"a service call";
    ans=`<b>${esc(t.title)}</b> ran because its <code>${esc(trigger||'trigger')}</code> fired and a <code>choose</code> branch executed, which invoked <code>${esc(eff)}</code>. The whole run took ${(t.total_ms/1000).toFixed(1)}s across ${t.span_count} steps.`;}
  return {answer:ans,trace:t.slug};}
function chips(){$("#chips").innerHTML="";EX.forEach(q=>{const b=document.createElement("span");b.className="chip";b.textContent=q;b.onclick=()=>{$("#q").value=q;ask();};$("#chips").appendChild(b);});}
function ask(){const q=$("#q").value.trim();if(!q)return;const d=answer(q);const a=$("#ans");a.classList.add("show");
  a.innerHTML=d.answer+(d.trace?` &nbsp;<a href="#" style="color:var(--accent);text-decoration:none" onclick="select('${d.trace}');return false;">show the flame graph →</a>`:"");if(d.trace)select(d.trace);}
function tabs(){const el=$("#tabs");el.innerHTML="";TRACES.forEach((t,i)=>{const d=document.createElement("div");d.className="tab"+(i===cur?" on":"");
  d.innerHTML=`<div class="t">${esc(t.title)}<span class="badge ${t.error_count?'err':'ok'}">${t.error_count?t.error_count+' err':'ok'}</span></div><div class="b">${esc(t.blurb)}</div>`;
  d.onclick=()=>{cur=i;render();};el.appendChild(d);});}
window.select=slug=>{const i=TRACES.findIndex(t=>t.slug===slug);if(i>=0){cur=i;render();window.scrollTo({top:$(".viz").offsetTop-20,behavior:"smooth"});}};
function render(){tabs();const t=TRACES[cur];
  $("#meta").innerHTML=`${t.total_ms>=1000?(t.total_ms/1000).toFixed(1)+'s':t.total_ms+'ms'} · ${t.span_count} spans · depth ${t.max_depth}${t.room?' · '+esc(t.room):''}`;
  const f=$("#flame"),W=Math.max(680,f.clientWidth-28),rowH=26,H=(t.max_depth+1)*rowH+36;f.style.height=H+"px";f.innerHTML="";
  t.spans.forEach(s=>{const x=s.start_ms/t.total_ms*W,w=Math.max(3,s.dur_ms/t.total_ms*W);
    const b=document.createElement("div");b.className="bar"+(s.error?" err":"");
    b.style.left=x+"px";b.style.top=(s.depth*rowH)+"px";b.style.width=w+"px";b.style.background=s.color;
    if(w>46)b.textContent=s.name;
    b.onmousemove=e=>{const tip=$("#tip");tip.style.display="block";tip.style.left=Math.min(e.clientX+14,innerWidth-330)+"px";tip.style.top=(e.clientY+14)+"px";
      tip.innerHTML=`<b>${esc(s.name)}</b><br/><span class="k">service</span> ${esc(s.service)} · <span class="k">${esc(s.step_type)}</span><br/><span class="k">duration</span> ${s.dur_ms>=1000?(s.dur_ms/1000).toFixed(2)+'s':s.dur_ms+'ms'}<br/><span class="k">path</span> ${esc(s.node_path)}`+(s.template_errors?`<br/><span style="color:var(--bad)">${esc(s.template_errors)}</span>`:"");};
    b.onmouseleave=()=>{$("#tip").style.display="none";};f.appendChild(b);});
  const ax=document.createElement("div");ax.className="axis";ax.style.position="absolute";ax.style.top=((t.max_depth+1)*rowH+6)+"px";ax.style.left="14px";ax.style.right="14px";
  ax.innerHTML=`<span>0</span><span>${t.total_ms>=1000?(t.total_ms/1000).toFixed(1)+'s':t.total_ms+'ms'}</span>`;f.appendChild(ax);}
$("#go").onclick=ask;$("#q").addEventListener("keydown",e=>{if(e.key==="Enter")ask();});
addEventListener("resize",render);chips();render();
</script></body></html>
"""


def main() -> None:
    out = HERE / "static"
    out.mkdir(exist_ok=True)
    html = (
        HTML.replace("__REPO__", REPO_URL)
        .replace("__TRACES__", json.dumps(app.TRACES, separators=(",", ":")))
    )
    (out / "index.html").write_text(html, encoding="utf-8")
    print(f"wrote {out / 'index.html'}  ({len(html)} bytes, {len(app.TRACES)} traces)")


if __name__ == "__main__":
    main()
