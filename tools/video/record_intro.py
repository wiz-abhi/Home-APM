"""Home APM — intro segment recorder (4 approved title cards, ~31s).

Records the four intro cards as ONE continuous Playwright chromium session at
1920x1080, reusing the EXACT dark card style from record_demo.py. Cards are
rendered as full-screen dark HTML pages (data: URLs) inside the recorded
context, so the raw .webm needs no post-production. Transcode with
assemble.py-style ffmpeg (or record_intro then the caller transcodes).

The card hold times are sized so each approved VO line sits inside its card at a
gentle pace (measured intro durations: 4.6 / 9.0 / 10.7 / 4.5 s):
    card 1 = 6.0s   card 2 = 9.0s   card 3 = 10.0s   card 4 = 6.0s

Run (Playwright lives in the warmup-agent venv):
    C:/Users/abhis/Desktop/OSS/Signoz/warmup-agent/.venv/Scripts/python.exe \
        tools/video/record_intro.py
"""

from __future__ import annotations

import urllib.parse
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

REPO = Path(r"C:\Users\abhis\Desktop\OSS\Signoz\Track3\home-apm")
WORK = REPO / "tools" / "video" / "_work"
VIDEO_RAW_DIR = WORK / "intro_raw"

W, H = 1920, 1080

# --- EXACT card style from record_demo.py --------------------------------- #
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
    page.wait_for_timeout(int(seconds * 1000))


def record(pw) -> Path:
    browser = pw.chromium.launch()
    ctx = browser.new_context(
        viewport={"width": W, "height": H},
        record_video_dir=str(VIDEO_RAW_DIR),
        record_video_size={"width": W, "height": H},
    )
    page = ctx.new_page()

    # small lead so the very first frame isn't mid-fade
    page.goto("data:text/html,<body style='margin:0;background:#0a0d13'></body>")
    page.wait_for_timeout(300)

    # Card 1 (0:00-0:06) — what it is
    card(
        page,
        'Distributed tracing for your <span class="accent">smart home.</span>',
        kicker="Home APM",
        sub="My Track 3 project — Agents of SigNoz",
        seconds=6.0,
    )

    # Card 2 (0:06-0:15) — the problem
    card(
        page,
        '<span class="accent">2,000,000+</span> Home Assistant homes.',
        kicker="The problem",
        sub="Zero observability.",
        seconds=9.0,
    )

    # Card 3 (0:15-0:25) — the idea
    card(
        page,
        'Every automation run<br>&rarr; a real OTLP trace<br>'
        '&rarr; a SigNoz <span class="accent">flame graph.</span>',
        kicker="The idea",
        seconds=10.0,
    )

    # Card 4 (0:25-0:31) — one command
    card(
        page,
        'It installs in <span class="accent">one command.</span>',
        kicker="One command",
        mono="foundryctl cast -f casting.yaml",
        seconds=6.0,
    )

    page.wait_for_timeout(400)
    path = Path(page.video.path())
    ctx.close()  # finalizes the webm
    browser.close()
    print(f"  raw intro webm -> {path}")
    return path


def main() -> int:
    WORK.mkdir(parents=True, exist_ok=True)
    VIDEO_RAW_DIR.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        raw = record(pw)
    (WORK / "intro_last_raw.txt").write_text(str(raw), encoding="utf-8")
    print(f"DONE. raw intro webm: {raw}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
