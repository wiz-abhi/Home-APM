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
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;600&display=swap" rel="stylesheet">
<style>
  :root{--bg:#05070c;--panel:rgba(19,25,37,.6);--glass:rgba(255,255,255,.045);--glass2:rgba(255,255,255,.02);
    --line:rgba(255,255,255,.09);--line2:rgba(255,255,255,.16);--text:#eaf0f8;--muted:#8592a8;
    --accent:#5b9bff;--accent2:#7c5cff;--good:#37e0a0;--bad:#ff5d6c;--mono:'JetBrains Mono',ui-monospace,monospace}
  *{box-sizing:border-box}
  html{background:#04050a}
  body{margin:0;min-height:100vh;color:var(--text);-webkit-font-smoothing:antialiased;
    font:15px/1.55 'Inter',system-ui,-apple-system,sans-serif;padding:0 18px 70px;
    background:
      radial-gradient(880px 480px at 12% -8%,rgba(91,155,255,.14),transparent 60%),
      radial-gradient(760px 460px at 92% -4%,rgba(124,92,255,.12),transparent 55%),
      radial-gradient(1200px 700px at 50% 120%,rgba(55,224,160,.06),transparent 60%),
      linear-gradient(180deg,#06080e 0%,#04050a 100%);background-attachment:fixed}
  .wrap{max-width:1060px;margin:0 auto}
  header{padding:44px 0 8px;text-align:center}
  .brand{font-size:34px;font-weight:800;letter-spacing:-.6px;
    background:linear-gradient(92deg,#f2f6ff,#a9c6ff 60%,#8fb0ff);-webkit-background-clip:text;background-clip:text;color:transparent;
    filter:drop-shadow(0 2px 18px rgba(91,155,255,.28))}
  .brand .a{background:linear-gradient(92deg,#6aa6ff,#8f7cff);-webkit-background-clip:text;background-clip:text;color:transparent}
  .sub{color:var(--muted);margin-top:8px;font-size:14px}.sub a{color:#8fb6ff;text-decoration:none}.sub a:hover{color:#bcd4ff}
  .pill{display:inline-flex;align-items:center;gap:8px;margin-top:15px;font-size:12.5px;color:var(--good);
    background:linear-gradient(180deg,rgba(55,224,160,.14),rgba(55,224,160,.05));border:1px solid rgba(55,224,160,.28);
    border-radius:999px;padding:6px 15px;box-shadow:0 0 24px -6px rgba(55,224,160,.35)}
  .pill .dot{width:7px;height:7px;border-radius:50%;background:var(--good);box-shadow:0 0 10px var(--good)}
  .card{background:linear-gradient(180deg,var(--glass),var(--glass2));border:1px solid var(--line);border-radius:18px;
    padding:20px;margin-top:24px;backdrop-filter:blur(14px);-webkit-backdrop-filter:blur(14px);
    box-shadow:0 1px 0 rgba(255,255,255,.06) inset,0 30px 60px -34px rgba(0,0,0,.9)}
  h2{margin:0 0 14px;font-size:12px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:1.6px}
  .row{display:flex;gap:10px}
  #q{flex:1;background:rgba(0,0,0,.35);border:1px solid var(--line2);color:var(--text);border-radius:12px;padding:13px 15px;font-size:15px;outline:none;transition:.15s}
  #q::placeholder{color:#5d6a80}#q:focus{border-color:var(--accent);box-shadow:0 0 0 3px rgba(91,155,255,.16)}
  button.go{background:linear-gradient(180deg,#7bb0ff,#4d85ee);color:#04122e;border:0;border-radius:12px;padding:0 22px;font-weight:700;font-size:15px;cursor:pointer;
    box-shadow:0 10px 24px -10px rgba(91,155,255,.7),inset 0 1px 0 rgba(255,255,255,.45);transition:.15s}
  button.go:hover{filter:brightness(1.07);transform:translateY(-1px)}
  .chips{display:flex;flex-wrap:wrap;gap:8px;margin-top:12px}
  .chip{background:var(--glass);border:1px solid var(--line);color:var(--muted);border-radius:999px;padding:7px 13px;font-size:12.5px;cursor:pointer;transition:.15s}
  .chip:hover{border-color:var(--accent);color:var(--text);box-shadow:0 0 18px -6px rgba(91,155,255,.5)}
  #ans{margin-top:16px;background:rgba(0,0,0,.32);border:1px solid var(--line);border-radius:12px;padding:15px 17px;display:none;font-size:15.5px;line-height:1.65}
  #ans.show{display:block}#ans code{font-family:var(--mono);background:rgba(124,92,255,.14);color:#c9d6ff;padding:1px 6px;border-radius:5px;font-size:12.5px}
  #ans a{color:#8fb6ff;text-decoration:none;font-weight:600}#ans a:hover{color:#bcd4ff}
  .tabs{display:flex;flex-wrap:wrap;gap:9px;margin-bottom:15px}
  .tab{background:linear-gradient(180deg,var(--glass),var(--glass2));border:1px solid var(--line);border-radius:12px;padding:10px 14px;cursor:pointer;font-size:13.5px;transition:.15s}
  .tab:hover{border-color:var(--line2)}
  .tab .t{font-weight:700}.tab .b{color:var(--muted);font-size:12px;margin-top:3px;max-width:230px}
  .tab.on{border-color:var(--accent);background:linear-gradient(180deg,rgba(91,155,255,.16),rgba(91,155,255,.04));box-shadow:0 0 26px -8px rgba(91,155,255,.55)}
  .tab .badge{font-size:11px;font-weight:700;padding:1px 8px;border-radius:999px;margin-left:6px}
  .badge.ok{background:rgba(55,224,160,.16);color:var(--good)}.badge.err{background:rgba(255,93,108,.16);color:var(--bad)}
  .meta{color:var(--muted);font-size:13px;margin:2px 0 12px;font-family:var(--mono)}
  #flame{position:relative;background:linear-gradient(180deg,rgba(0,0,0,.35),rgba(0,0,0,.15));border:1px solid var(--line);border-radius:14px;padding:16px;overflow-x:auto;
    box-shadow:0 1px 0 rgba(255,255,255,.05) inset}
  .bar{position:absolute;height:23px;border-radius:6px;font-size:11.5px;line-height:23px;color:#07101f;padding:0 7px;white-space:nowrap;overflow:hidden;cursor:default;font-weight:600;
    box-shadow:0 2px 6px rgba(0,0,0,.45),inset 0 1px 0 rgba(255,255,255,.3)}
  .bar::after{content:"";position:absolute;inset:0;border-radius:inherit;background:linear-gradient(180deg,rgba(255,255,255,.28),rgba(255,255,255,0) 55%);pointer-events:none}
  .bar.err{box-shadow:0 0 0 1.5px var(--bad),0 0 16px -2px rgba(255,93,108,.7),inset 0 1px 0 rgba(255,255,255,.3)}
  .fc{position:relative}
  .axis{position:absolute;left:0;right:0;color:var(--muted);font-size:11px;border-top:1px dashed var(--line2);padding-top:5px;display:flex;justify-content:space-between;font-family:var(--mono)}
  .fwrap{position:relative}
  .fwrap .fade{position:absolute;top:1px;right:1px;bottom:1px;width:56px;border-radius:0 14px 14px 0;pointer-events:none;opacity:0;transition:opacity .25s;
    background:linear-gradient(90deg,rgba(5,7,12,0),rgba(5,7,12,.92))}
  .fwrap .shint{position:absolute;right:12px;bottom:9px;font-size:10.5px;font-family:var(--mono);color:var(--muted);pointer-events:none;opacity:0;transition:opacity .25s;
    background:rgba(8,12,20,.85);border:1px solid var(--line2);border-radius:999px;padding:2px 9px}
  .fwrap.scroll .fade,.fwrap.scroll .shint{opacity:1}
  .fwrap.scroll.atend .fade,.fwrap.scroll.atend .shint{opacity:0}
  .legend{display:flex;flex-wrap:wrap;align-items:center;gap:7px;margin-top:12px}
  .legend .lg{display:inline-flex;align-items:center;gap:6px;background:var(--glass);border:1px solid var(--line);border-radius:999px;
    padding:4px 10px;font-size:11px;color:var(--muted);font-family:var(--mono)}
  .legend .sw{width:9px;height:9px;border-radius:3px;flex:none}
  .legend .sw.e{background:transparent;box-shadow:0 0 0 1.5px var(--bad),0 0 10px -2px rgba(255,93,108,.9)}
  .hedge{display:block;color:var(--muted);font-size:13px;margin-bottom:6px}
  #tip{position:fixed;pointer-events:none;background:rgba(8,12,20,.92);border:1px solid var(--line2);border-radius:10px;padding:10px 12px;font-size:12.5px;max-width:330px;display:none;z-index:9;
    backdrop-filter:blur(8px);box-shadow:0 12px 34px rgba(0,0,0,.6)}
  #tip b{color:#8fb6ff}#tip .k{color:var(--muted)}
  #tip .dz{color:var(--muted);font-size:11px;margin-top:7px;border-top:1px solid var(--line);padding-top:6px}
  footer{color:var(--muted);font-size:12.5px;text-align:center;margin-top:30px;line-height:1.9}
  footer code{font-family:var(--mono);color:#a9c6ff}footer a{color:#8fb6ff;text-decoration:none}
</style></head><body><div class="wrap">
  <header>
    <div class="brand">Home&nbsp;<span class="a">APM</span></div>
    <div class="sub">Home Assistant automation traces, reconstructed into flame graphs. &nbsp;<a href="__REPO__" target="_blank">source on GitHub →</a></div>
    <div class="pill"><span class="dot"></span>Real recorded traces, reconstructed by the actual pure function. No SigNoz, no key.</div>
  </header>
  <section class="card">
    <h2>Ask your house</h2>
    <div class="row"><input id="q" placeholder="why is my morning routine slow?" autocomplete="off"/><button class="go" id="go">Ask</button></div>
    <div class="chips" id="chips"></div>
    <div id="ans"></div>
  </section>
  <section class="card">
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
const TRACES=__TRACES__;
const COLORS=__COLORS__;
const EX=["why did my hallway lights turn on at 3am?","why is my morning routine slow?","did anything fail tonight?"];
const $=s=>document.querySelector(s);let cur=0;
const esc=s=>String(s).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
const bySlug=s=>TRACES.find(t=>t.slug===s);
// one formatter for every duration we print — sub-second runs must not read "0.0s"
const fmtMs=(x,d)=>x>=1000?(x/1000).toFixed(d||1)+'s':x+'ms';
const HINTS=[["hallway","hallway_lights_3am"],["3am","hallway_lights_3am"],["3 am","hallway_lights_3am"],["light","hallway_lights_3am"],["morning","morning_routine"],["routine","morning_routine"],["good night","good_night"],["night","good_night"],["garage","garage_check"]];
// returns {t, sure} — `sure` is false when nothing in the question named an automation
function pickTrace(q){const ql=q.toLowerCase();
  for(const [k,slug] of HINTS){if(ql.includes(k))return{t:bySlug(slug),sure:true};}
  if(/(fail|error|broke|wrong|crash)/.test(ql)){const e=TRACES.find(t=>t.error_count);if(e)return{t:e,sure:false};}
  if(/(slow|latency|long|delay)/.test(ql))return{t:TRACES.reduce((a,b)=>b.total_ms>a.total_ms?b:a),sure:false};
  return{t:bySlug("hallway_lights_3am"),sure:false};}
function answer(q){const p=pickTrace(q),t=p.t,ql=q.toLowerCase();
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
    ans=`<b>${esc(t.title)}</b> was slow because its <code>${esc(slowest.name)}</code> step took <b>${fmtMs(slowest.dur_ms)}</b> — ${pct}% of the whole ${fmtMs(t.total_ms)} run.`;
  }else{const eff=calls.length?calls[0].name:null,conds=t.spans.filter(s=>s.step_type==="condition").length;
    const tail=`The whole run took ${fmtMs(t.total_ms)} across ${t.span_count} steps.`;
    ans=eff
      ?`<b>${esc(t.title)}</b> ran because its <code>${esc(trigger||'trigger')}</code> fired and a <code>choose</code> branch executed, which invoked <code>${esc(eff)}</code>. ${tail}`
      :`<b>${esc(t.title)}</b> ran because its <code>${esc(trigger||'trigger')}</code> fired${conds?` and its <code>choose</code> branch evaluated ${conds} condition${conds>1?'s':''}`:``} — no service call ran, so nothing in the house actually changed. ${tail}`;}
  if(!p.sure)ans=`<span class="hedge">I couldn't match that to an automation by name — showing the closest trace I have, <b>${esc(t.title)}</b>.</span>`+ans;
  return {answer:ans,trace:t.slug};}
function chips(){$("#chips").innerHTML="";EX.forEach(q=>{const b=document.createElement("span");b.className="chip";b.textContent=q;b.onclick=()=>{$("#q").value=q;ask();};$("#chips").appendChild(b);});}
function ask(){const q=$("#q").value.trim();if(!q)return;const d=answer(q);const a=$("#ans");a.classList.add("show");
  a.innerHTML=d.answer+(d.trace?` &nbsp;<a href="#" onclick="select('${d.trace}');return false;">show the flame graph →</a>`:"");if(d.trace)select(d.trace);}
function tabs(){const el=$("#tabs");el.innerHTML="";TRACES.forEach((t,i)=>{const d=document.createElement("div");d.className="tab"+(i===cur?" on":"");
  d.innerHTML=`<div class="t">${esc(t.title)}<span class="badge ${t.error_count?'err':'ok'}">${t.error_count?t.error_count+' err':'ok'}</span></div><div class="b">${esc(t.blurb)}</div>`;
  d.onclick=()=>{cur=i;render();};el.appendChild(d);});}
window.select=slug=>{const i=TRACES.findIndex(t=>t.slug===slug);if(i>=0){cur=i;render();window.scrollTo({top:$(".tabs").getBoundingClientRect().top+scrollY-90,behavior:"smooth"});}};
// tooltip: hover on desktop, tap-to-pin on touch (bars are the only place span
// name / service / duration / error text live, so they must be reachable by finger)
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
  const W=Math.max(680,f.clientWidth-32),rowH=27,H=(t.max_depth+1)*rowH+38;
  // everything lives inside one W-wide layer so the axis scrolls with the bars
  const fc=document.createElement("div");fc.className="fc";fc.style.width=W+"px";fc.style.height=H+"px";
  t.spans.forEach(s=>{const x=s.start_ms/t.total_ms*W,w=Math.max(3,s.dur_ms/t.total_ms*W);
    const b=document.createElement("div");b.className="bar"+(s.error?" err":"");
    b.style.left=x+"px";b.style.top=(s.depth*rowH)+"px";b.style.width=w+"px";b.style.background=s.color;
    if(w>46)b.textContent=s.name;
    b.onmousemove=e=>{if(!pinned)showTip(s,e.clientX,e.clientY);};
    b.onmouseleave=()=>{if(!pinned)hideTip();};
    b.onclick=e=>{e.stopPropagation();unpin();pinTip(s,b);};
    b.addEventListener("touchstart",()=>{tmoved=false;},{passive:true});
    b.addEventListener("touchend",e=>{if(tmoved)return;e.stopPropagation();unpin();pinTip(s,b);},{passive:true});
    fc.appendChild(b);});
  const ax=document.createElement("div");ax.className="axis";ax.style.top=((t.max_depth+1)*rowH+8)+"px";
  ax.innerHTML=`<span>0</span><span>${fmtMs(t.total_ms)}</span>`;fc.appendChild(ax);
  f.appendChild(fc);legend();scrollHint();}
$("#go").onclick=ask;$("#q").addEventListener("keydown",e=>{if(e.key==="Enter")ask();});
$("#flame").addEventListener("scroll",scrollHint,{passive:true});
$("#flame").addEventListener("touchmove",()=>{tmoved=true;},{passive:true});
addEventListener("click",unpin);addEventListener("touchstart",unpin,{passive:true});
addEventListener("resize",()=>{render();});chips();render();
</script></body></html>
"""


def main() -> None:
    out = HERE / "static"
    out.mkdir(exist_ok=True)
    html = (
        HTML.replace("__REPO__", REPO_URL)
        .replace("__TRACES__", json.dumps(app.TRACES, separators=(",", ":")))
        # the legend is generated from this map, so it can never drift from the bars
        .replace("__COLORS__", json.dumps(app.SERVICE_COLOR, separators=(",", ":")))
    )
    (out / "index.html").write_text(html, encoding="utf-8")
    print(f"wrote {out / 'index.html'}  ({len(html)} bytes, {len(app.TRACES)} traces)")


if __name__ == "__main__":
    main()
