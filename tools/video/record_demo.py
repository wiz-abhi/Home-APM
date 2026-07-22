"""Home APM — caption-driven demo recorder (Track 3, Agents of SigNoz).

Records the whole 3–3.5 min demo as ONE continuous Playwright chromium session at
1920x1080. Title cards are rendered as full-screen dark HTML pages navigated to
*inside the same recorded context* (data: URLs), so the finished .webm needs zero
post-production — every cut is already in the tape.

Pipeline
--------
1.  Pre-auth pass (NOT recorded): log in to SigNoz and Home Assistant once, save a
    combined ``storage_state`` so the recorded session shows zero login screens.
2.  Capture the real ``ask.py`` Q&A (live), falling back to known-good strings.
3.  Recording pass: one context, one page, drive the storyboard beat by beat.
4.  Emit the raw .webm path for the assembler (assemble.py) to transcode.

Run from repo root:
    C:/Users/abhis/Desktop/OSS/Signoz/warmup-agent/.venv/Scripts/python.exe \
        tools/video/record_demo.py

Env knobs:
    PACE          float multiplier on every hold (default 1.0)
    SIGNOZ_EMAIL / SIGNOZ_PASSWORD / HA_USERNAME / HA_PASSWORD  (required)
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import urllib.parse
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

# --------------------------------------------------------------------------- #
# paths / constants
# --------------------------------------------------------------------------- #

REPO = Path(r"C:\Users\abhis\Desktop\OSS\Signoz\Track3\home-apm")
OUT_DIR = REPO / "docs" / "video"
WORK = REPO / "tools" / "video" / "_work"
VIDEO_RAW_DIR = WORK / "raw"
STATE_FILE = WORK / "storage_state.json"
DEMO_LINKS = REPO / "tools" / "views" / "DEMO-LINKS.md"
PROJECT_PY = REPO / ".venv" / "Scripts" / "python.exe"

SIGNOZ = "http://localhost:8080"
HA = "http://localhost:8123"
DASHBOARD = "019f8a8f-d7f4-77dd-a5b8-b69d2a7fad3b"

W, H = 1920, 1080
PACE = float(os.environ.get("PACE", "1.0"))

EMAIL = os.environ.get("SIGNOZ_EMAIL", "")
PASSWORD = os.environ.get("SIGNOZ_PASSWORD", "")
HA_USER = os.environ.get("HA_USERNAME", "homeapm")
HA_PW = os.environ.get("HA_PASSWORD", "")

# Known-good ask.py outputs (fallback if the live run errors at record time).
FALLBACK_ASK = [
    (
        "why did my hallway lights turn on at 3am?",
        "The hallway lights turned on because the template condition in your\n"
        "automation evaluated to true, triggering the first branch of your\n"
        "choose action. The entire run completed in just 3 milliseconds.",
        "cf402e5f2b13dd6c1b01b604045e440a",
    ),
    (
        "why is my morning routine slow?",
        "Your morning routine is slow because the automation paused to wait for\n"
        "a trigger event. This wait step accounted for the entire 52.5s\n"
        "duration of the run.",
        "6544b9eae4a70aed58058d73e9c5b808",
    ),
    (
        "did anything fail tonight?",
        "The automation failed because the persistent_notification service hit a\n"
        "division-by-zero error while rendering its data template. It surfaced\n"
        "on the final service call of the 8.0s run.",
        "b624dba1530e3d3382eefffab5fbc8df",
    ),
]


def hold(page: Page, seconds: float) -> None:
    page.wait_for_timeout(int(seconds * 1000 * PACE))


# --------------------------------------------------------------------------- #
# deep-link discovery (fresh trace ids from DEMO-LINKS.md)
# --------------------------------------------------------------------------- #


def read_trace_ids() -> dict[str, str]:
    """Parse the three specific-run trace ids out of DEMO-LINKS.md."""
    text = DEMO_LINKS.read_text(encoding="utf-8")
    out: dict[str, str] = {}
    patterns = {
        "hallway": r"Hallway Lights 3AM' run:.*?/trace/([0-9a-f]{16,})",
        "morning": r"Morning Routine' run:.*?/trace/([0-9a-f]{16,})",
        "goodnight": r"Good Night' run.*?/trace/([0-9a-f]{16,})",
    }
    for key, pat in patterns.items():
        m = re.search(pat, text)
        if m:
            out[key] = m.group(1)
    return out


def read_logs_url() -> str:
    """The pre-filtered sidecar Logs Explorer URL from DEMO-LINKS.md."""
    text = DEMO_LINKS.read_text(encoding="utf-8")
    m = re.search(r"(http://localhost:8080/logs/logs-explorer\?[^\s)]+)", text)
    return m.group(1) if m else f"{SIGNOZ}/logs/logs-explorer"


def read_explorer_url() -> str:
    """The pre-filtered ha.automation Trace Explorer URL (Beats 1-4)."""
    text = DEMO_LINKS.read_text(encoding="utf-8")
    m = re.search(r"(http://localhost:8080/traces-explorer\?[^\s)]+)", text)
    return m.group(1) if m else f"{SIGNOZ}/traces-explorer"


# --------------------------------------------------------------------------- #
# live ask.py capture
# --------------------------------------------------------------------------- #


def capture_ask() -> list[tuple[str, str, str]]:
    """Run ask.py live for the 3 canonical questions; fall back on any failure."""
    questions = [q for q, _, _ in FALLBACK_ASK]
    env = dict(os.environ, PYTHONPATH=str(REPO / "src"))
    results: list[tuple[str, str, str]] = []
    for i, q in enumerate(questions):
        try:
            proc = subprocess.run(
                [str(PROJECT_PY), str(REPO / "tools" / "ask" / "ask.py"), q],
                cwd=str(REPO),
                env=env,
                capture_output=True,
                text=True,
                timeout=45,
            )
            out = proc.stdout.strip()
            tid = ""
            mt = re.search(r"trace_id:\s*([0-9a-f]{16,})", out)
            if mt:
                tid = mt.group(1)
            # answer = everything before the blank line / "trace_id:" block
            answer = out.split("\n\n")[0].strip() if out else ""
            if answer and "error" not in answer.lower()[:12]:
                results.append((q, answer, tid))
                print(f"  ask ok: {q!r}")
                continue
        except Exception as exc:  # noqa: BLE001
            print(f"  ask failed ({q!r}): {exc}")
        results.append(FALLBACK_ASK[i])
        print(f"  ask fallback: {q!r}")
    return results


# --------------------------------------------------------------------------- #
# title cards (data: URLs rendered inside the recorded context)
# --------------------------------------------------------------------------- #

_CARD_CSS = """
*{margin:0;padding:0;box-sizing:border-box}
html,body{height:100%}
body{background:radial-gradient(circle at 50% 38%,#141a26 0%,#0a0d13 70%);
 display:flex;align-items:center;justify-content:center;
 font-family:'Segoe UI',system-ui,-apple-system,sans-serif;color:#f4f6fb}
.wrap{max-width:1400px;text-align:center;padding:0 80px;
 animation:fade .7s ease both}
.kick{font-size:22px;letter-spacing:.32em;text-transform:uppercase;
 color:#5b9dff;font-weight:600;margin-bottom:34px}
h1{font-size:74px;line-height:1.15;font-weight:700;letter-spacing:-.5px}
h1 .accent{color:#5b9dff}
.sub{margin-top:40px;font-size:34px;line-height:1.4;color:#9aa6bd;font-weight:400}
.mono{font-family:'Cascadia Code','Consolas',monospace;font-size:30px;
 color:#7ee3b0;margin-top:44px;background:#0e131c;border:1px solid #1e2836;
 border-radius:12px;padding:22px 30px;display:inline-block}
@keyframes fade{from{opacity:0;transform:translateY(14px)}to{opacity:1;transform:none}}
"""


def card(page: Page, headline_html: str, *, kicker: str = "", sub: str = "",
         mono: str = "", seconds: float = 3.0) -> None:
    parts = ['<div class="wrap">']
    if kicker:
        parts.append(f'<div class="kick">{kicker}</div>')
    parts.append(f"<h1>{headline_html}</h1>")
    if sub:
        parts.append(f'<div class="sub">{sub}</div>')
    if mono:
        parts.append(f'<div class="mono">{mono}</div>')
    parts.append("</div>")
    html = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<style>{_CARD_CSS}</style></head><body>{''.join(parts)}</body></html>"
    )
    page.goto("data:text/html;charset=utf-8," + urllib.parse.quote(html))
    hold(page, seconds)


# --------------------------------------------------------------------------- #
# terminal beat (typed ask.py Q&A)
# --------------------------------------------------------------------------- #


def build_terminal_html(qa: list[tuple[str, str, str]]) -> Path:
    import json

    data = json.dumps(qa)
    html = """<!doctype html><html><head><meta charset='utf-8'><style>
*{margin:0;padding:0;box-sizing:border-box}
html,body{height:100%;background:#0a0d13}
body{font-family:'Cascadia Code','Consolas',monospace;color:#d6deeb;
 padding:56px 90px;font-size:27px;line-height:1.52}
.bar{display:flex;gap:10px;margin-bottom:34px;align-items:center}
.dot{width:15px;height:15px;border-radius:50%}
.r{background:#ff5f56}.y{background:#ffbd2e}.g{background:#27c93f}
.title{margin-left:16px;color:#6b7689;font-size:22px}
#scr{white-space:pre-wrap}
.prompt{color:#7ee3b0}.q{color:#eaeff8}
.ans{color:#9fd0ff}.meta{color:#5f6b82}
.cur{display:inline-block;width:12px;height:26px;background:#7ee3b0;
 vertical-align:-4px;animation:b 1s step-end infinite}
@keyframes b{50%{opacity:0}}
</style></head><body>
<div class="bar"><span class="dot r"></span><span class="dot y"></span>
<span class="dot g"></span><span class="title">ask your house — Home APM · MCP → SigNoz</span></div>
<div id="scr"></div>
<script>
const QA = __DATA__;
const scr = document.getElementById('scr');
const sleep = ms => new Promise(r=>setTimeout(r,ms));
function span(cls,t){const s=document.createElement('span');s.className=cls;s.textContent=t;scr.appendChild(s);}
async function type(cls,t,sp){const s=document.createElement('span');s.className=cls;scr.appendChild(s);
 for(const ch of t){s.textContent+=ch;await sleep(sp);}}
async function run(){
 for(const [q,a,tid] of QA){
  await type('prompt','$ ',6);
  await type('q','python tools/ask/ask.py "'+q+'"\\n',26);
  await sleep(420);
  const s=document.createElement('span');s.className='ans';scr.appendChild(s);
  for(const ln of a.split('\\n')){s.textContent+=ln+'\\n';await sleep(150);}
  await sleep(160);
  span('meta','  trace_id:    '+tid+'\\n');
  span('meta','  flame graph: http://localhost:8080/trace/'+tid+'\\n\\n');
  await sleep(1300);
 }
 const c=document.createElement('span');c.className='cur';scr.appendChild(c);
}
run();
</script></body></html>"""
    html = html.replace("__DATA__", data)
    p = WORK / "terminal.html"
    p.write_text(html, encoding="utf-8")
    return p


# --------------------------------------------------------------------------- #
# SigNoz / HA helpers
# --------------------------------------------------------------------------- #


def signoz_login(page: Page) -> None:
    page.goto(f"{SIGNOZ}/login", wait_until="domcontentloaded")
    page.wait_for_selector('input[type="email"]', timeout=30000)
    page.fill('input[type="email"]', EMAIL)
    page.click('button:has-text("Next")')
    page.wait_for_selector('input[type="password"]', timeout=15000)
    page.fill('input[type="password"]', PASSWORD)
    page.click('button:has-text("Sign in with Password")')
    page.wait_for_url("**/home**", timeout=30000)
    print("  signoz: logged in")


def ha_login(page: Page) -> None:
    page.goto(HA, wait_until="domcontentloaded")
    page.wait_for_timeout(2800)
    try:
        page.fill('input[name="username"]', HA_USER)
        page.fill('input[name="password"]', HA_PW)
        page.wait_for_timeout(300)
        page.keyboard.press("Enter")
        page.wait_for_timeout(5000)
        print("  ha: logged in")
    except Exception as exc:  # noqa: BLE001
        print(f"  ha: login skipped ({exc}) — maybe already authed")


def wait_trace(page: Page) -> None:
    """Wait for a SigNoz trace-detail waterfall to actually render its spans.

    The "Flame Graph" section header paints almost immediately (before the span
    data arrives, while a skeleton shimmer shows), so waiting on it can catch a
    half-loaded page. The "Spans: N" count badge only appears once the span tree
    has loaded, so we wait on that; larger traces (e.g. the 20-span good_night)
    need the extra headroom.
    """
    try:
        page.wait_for_selector("text=/Spans:\\s*\\d+/", timeout=22000)
    except Exception:  # noqa: BLE001
        # fall back to the header if the badge text ever changes
        try:
            page.wait_for_selector("text=Flame Graph", timeout=8000)
        except Exception:  # noqa: BLE001
            pass
    page.wait_for_timeout(2800)


def open_trace(page: Page, trace_id: str, hold_s: float) -> None:
    page.goto(f"{SIGNOZ}/trace/{trace_id}", wait_until="domcontentloaded")
    wait_trace(page)
    hold(page, hold_s)


# --------------------------------------------------------------------------- #
# pre-auth pass (no recording)
# --------------------------------------------------------------------------- #


def preauth(pw) -> None:
    print("pre-auth: logging into SigNoz + HA (not recorded)…")
    browser = pw.chromium.launch()
    ctx = browser.new_context(viewport={"width": W, "height": H})
    page = ctx.new_page()
    signoz_login(page)
    ha_login(page)
    ctx.storage_state(path=str(STATE_FILE))
    browser.close()
    print(f"  saved storage_state -> {STATE_FILE}")


# --------------------------------------------------------------------------- #
# recording pass
# --------------------------------------------------------------------------- #


def record(pw, ids: dict[str, str], qa: list[tuple[str, str, str]]) -> Path:
    terminal = build_terminal_html(qa)
    logs_url = read_logs_url()
    explorer_url = read_explorer_url()

    browser = pw.chromium.launch()
    ctx = browser.new_context(
        viewport={"width": W, "height": H},
        storage_state=str(STATE_FILE),
        record_video_dir=str(VIDEO_RAW_DIR),
        record_video_size={"width": W, "height": H},
    )
    page = ctx.new_page()

    # ---- Cold open ----------------------------------------------------------
    card(
        page,
        'My hallway lights turned on at <span class="accent">3am.</span>',
        kicker="Home APM",
        sub="I had no idea why.",
        seconds=4.2,
    )

    # ---- HA native trace (the cryptic one) ----------------------------------
    page.goto(f"{HA}/config/automation/trace/hallway_lights_3am",
              wait_until="domcontentloaded")
    page.wait_for_timeout(6500)
    try:
        page.click('text=Trace timeline', timeout=3500)
        page.wait_for_timeout(1500)
    except Exception:  # noqa: BLE001
        pass
    hold(page, 5.5)

    card(page, "Home Assistant couldn&rsquo;t tell me.",
         sub="Cryptic node paths. Five traces of history. Then gone.", seconds=3.4)

    # ---- The fix -----------------------------------------------------------
    card(page, 'So I gave my house an <span class="accent">APM.</span>',
         sub="Every automation run becomes a SigNoz flame graph.", seconds=3.6)

    # saved views hub
    page.goto(f"{SIGNOZ}/traces/saved-views", wait_until="domcontentloaded")
    page.wait_for_timeout(3500)
    hold(page, 4.0)

    # the 3am run as a named waterfall
    if ids.get("hallway"):
        open_trace(page, ids["hallway"], 6.0)
        # best-effort: hover a choose/branch span
        for needle in ("branch", "choose", "condition"):
            try:
                page.hover(f"text=/{needle}/i", timeout=1500)
                break
            except Exception:  # noqa: BLE001
                continue
        hold(page, 5.5)

    # ---- Logs <-> traces ---------------------------------------------------
    card(page, "The log line and the trace are <span class='accent'>one click apart.</span>",
         kicker="Logs ↔ Traces", seconds=3.0)
    page.goto(logs_url, wait_until="domcontentloaded")
    page.wait_for_timeout(7500)  # let the log rows paint before the hold
    # dismiss the one-time "Edit your quick filters" onboarding popover if present
    try:
        page.click("button:has-text('Okay')", timeout=1500)
    except Exception:  # noqa: BLE001
        pass
    try:
        page.click("div[role='row'] >> nth=1", timeout=2500)
        page.wait_for_timeout(1500)
    except Exception:  # noqa: BLE001
        pass
    hold(page, 5.5)

    # ---- Latency -----------------------------------------------------------
    card(page, 'A <span class="accent">47-second</span> wait, hiding in plain sight.',
         kicker="The slow morning", seconds=3.0)
    if ids.get("morning"):
        open_trace(page, ids["morning"], 9.5)

    # ---- Parallel / repeat / error -----------------------------------------
    card(page, "Parallel branches. A repeat loop. A real <span class='accent'>error.</span>",
         kicker="This is a real tracer",
         sub="The #1 question every naive HA-trace reader gets wrong.", seconds=3.4)
    if ids.get("goodnight"):
        open_trace(page, ids["goodnight"], 10.0)

    # ---- Ask your house ----------------------------------------------------
    card(page, 'Or just <span class="accent">ask your house.</span>',
         kicker="MCP · natural language", seconds=3.0)
    page.goto(terminal.as_uri(), wait_until="domcontentloaded")
    hold(page, 23.0)

    # ---- The board ---------------------------------------------------------
    card(page, "One board for the <span class='accent'>whole house.</span>",
         kicker="Room-centric dashboard", seconds=2.8)
    page.goto(f"{SIGNOZ}/dashboard/{DASHBOARD}", wait_until="domcontentloaded")
    page.wait_for_timeout(5500)
    hold(page, 5.0)
    # best-effort: change the $room variable
    try:
        page.click("text=/room/i", timeout=2500)
        page.wait_for_timeout(800)
        page.click("text=/Bedroom/i", timeout=2500)
        page.wait_for_timeout(1200)
    except Exception:  # noqa: BLE001
        pass
    hold(page, 5.5)

    # house service map
    page.goto(f"{SIGNOZ}/service-map", wait_until="domcontentloaded")
    page.wait_for_timeout(5000)
    hold(page, 6.0)

    # ---- The alert ---------------------------------------------------------
    card(page, "And it tells Home Assistant when something <span class='accent'>breaks.</span>",
         kicker="The loop closes", seconds=3.0)
    # The Alert Rules list reliably renders the configured rules + their state
    # ("Automation failing" among them); the Triggered-Alerts tab can render an
    # empty body, so we show the rules list as the SigNoz-side alerting surface.
    page.goto(f"{SIGNOZ}/alerts", wait_until="domcontentloaded")
    page.wait_for_timeout(5000)
    hold(page, 4.5)

    # notification back inside HA
    page.goto(HA, wait_until="domcontentloaded")
    page.wait_for_timeout(3500)
    try:
        page.click("text=Notifications", timeout=3500)
        page.wait_for_timeout(2200)
    except Exception:  # noqa: BLE001
        pass
    hold(page, 5.5)

    # ---- Close -------------------------------------------------------------
    card(page, "Home Assistant always had traces.<br>It just never let anyone <span class='accent'>see</span> them.",
         seconds=4.6)
    card(page, "Give your house an APM.",
         mono="foundryctl cast -f casting.yaml",
         sub="github.com/wiz-abhi/home-apm", seconds=5.2)

    page.wait_for_timeout(600)
    path = Path(page.video.path())
    ctx.close()  # finalizes the webm
    browser.close()
    print(f"  raw video -> {path}")
    return path


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #


def main() -> int:
    if not EMAIL or not PASSWORD or not HA_PW:
        print("ERROR: set SIGNOZ_EMAIL / SIGNOZ_PASSWORD / HA_PASSWORD in env.")
        return 2
    WORK.mkdir(parents=True, exist_ok=True)
    VIDEO_RAW_DIR.mkdir(parents=True, exist_ok=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    ids = read_trace_ids()
    print(f"trace ids: {ids}")

    print("capturing ask.py Q&A live…")
    qa = capture_ask()

    with sync_playwright() as pw:
        preauth(pw)
        raw = record(pw, ids, qa)

    # stash the raw path for the assembler
    (WORK / "last_raw.txt").write_text(str(raw), encoding="utf-8")
    print(f"DONE. raw webm: {raw}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
