"""Capture the Home APM SigNoz demo screenshots (docs/screenshots/01..06).

Reuses the warmup-agent Playwright/chromium install and the shots_hq login
pattern. Dark theme, high-DPI, nav-rail cropped. Trace/dashboard ids are passed
on the command line so the script stays reusable as runs age out.
"""
import os
import sys
from datetime import datetime, timezone

from playwright.sync_api import sync_playwright

BASE = "http://localhost:8080"
OUT = r"C:\Users\abhis\Desktop\OSS\Signoz\Track3\home-apm\docs\screenshots"
os.makedirs(OUT, exist_ok=True)

EMAIL = os.environ["SIGNOZ_EMAIL"]
PASSWORD = os.environ["SIGNOZ_PASSWORD"]

# --- fresh run ids (edit per capture session) ------------------------------
GOOD_NIGHT = "f2d687459a19106ca40936dc3fba2b17"
GN_ERR_SPAN = "afde4bdd4e26e246"          # persistent_notification.create ERROR
HALLWAY = "1941c6ec0233924753dc842aefbbb1b0"
HALL_CHOOSE = "1a3e65746db7bc3c"          # choose block
MORNING = "975c63487d9f04ff72c6da224d5a62ea"
MORN_WAIT = "f3229523760e4009"            # wait_for_trigger ~49.7s
DASH = "019f8a8f-d7f4-77dd-a5b8-b69d2a7fad3b"

DSF = 2
NAV_W = 56


def now_ms():
    return int(datetime.now(tz=timezone.utc).timestamp() * 1000)


# a window that comfortably brackets the burst runs
END = now_ms() + 120_000
START = END - 55 * 60 * 1000


def login(pg):
    pg.goto(f"{BASE}/login", wait_until="networkidle")
    pg.fill('input[type="email"]', EMAIL)
    pg.click('button:has-text("Next")')
    pg.wait_for_selector('input[type="password"]', timeout=15000)
    pg.fill('input[type="password"]', PASSWORD)
    pg.click('button:has-text("Sign in with Password")')
    pg.wait_for_url("**/home**", timeout=30000)
    print("logged in")


def dismiss(pg):
    for label in ("Okay", "Got it", "Skip", "Close", "Dismiss"):
        try:
            pg.click(f'button:has-text("{label}")', timeout=1000)
            pg.wait_for_timeout(300)
        except Exception:
            pass


def shot(pg, name, width, height, clip_h=None, top=0):
    path = os.path.join(OUT, name)
    pg.screenshot(path=path, clip={
        "x": NAV_W, "y": top,
        "width": width - NAV_W,
        "height": min(clip_h or height, height - top),
    })
    print(f"saved {name}")


def run(pw, width, height, fn):
    b = pw.chromium.launch()
    pg = b.new_page(viewport={"width": width, "height": height},
                    device_scale_factor=DSF)
    login(pg)
    fn(pg, width, height)
    b.close()


def enable_highlight_errors(pg):
    try:
        pg.click('text=Highlight errors', timeout=2000)
        pg.wait_for_timeout(600)
    except Exception:
        pass


with sync_playwright() as pw:

    # 01 good_night flame graph: parallel + repeat + red ERROR span selected
    def good_night(pg, w, h):
        pg.goto(f"{BASE}/trace/{GOOD_NIGHT}?spanId={GN_ERR_SPAN}",
                wait_until="networkidle")
        pg.wait_for_selector("text=Flame Graph", timeout=30000)
        dismiss(pg)
        enable_highlight_errors(pg)
        pg.wait_for_timeout(2500)
        shot(pg, "01-trace-good-night.png", w, h, clip_h=h)
    run(pw, 1680, 1000, good_night)

    # 02 hallway 3am waterfall with the choose branch
    def hallway(pg, w, h):
        pg.goto(f"{BASE}/trace/{HALLWAY}?spanId={HALL_CHOOSE}",
                wait_until="networkidle")
        pg.wait_for_selector("text=Flame Graph", timeout=30000)
        dismiss(pg)
        pg.wait_for_timeout(2500)
        shot(pg, "02-trace-3am-choose.png", w, h, clip_h=h)
    run(pw, 1680, 1000, hallway)

    # 03 morning routine: the ~50s wait span dominates
    def morning(pg, w, h):
        pg.goto(f"{BASE}/trace/{MORNING}?spanId={MORN_WAIT}",
                wait_until="networkidle")
        pg.wait_for_selector("text=Flame Graph", timeout=30000)
        dismiss(pg)
        pg.wait_for_timeout(2500)
        shot(pg, "03-wait-span.png", w, h, clip_h=h)
    run(pw, 1680, 1000, morning)

    # 04 logs correlated: sidecar OTLP logs carrying trace_id
    def logs(pg, w, h):
        url = (f"{BASE}/logs/logs-explorer?startTime={START}&endTime={END}")
        pg.goto(url, wait_until="networkidle")
        pg.wait_for_timeout(2500)
        dismiss(pg)
        try:
            qb = pg.locator('[contenteditable="true"]').first
            qb.click()
            pg.keyboard.press("Control+A")
            pg.keyboard.press("Delete")
            pg.keyboard.type("service.name = 'ha.sidecar'", delay=15)
            pg.wait_for_timeout(400)
            pg.keyboard.press("Escape")
            pg.click('button:has-text("Run Query")', timeout=3000)
        except Exception as e:
            print("logs filter failed:", e)
        pg.wait_for_timeout(4000)
        dismiss(pg)
        shot(pg, "04-logs-correlated.png", w, h, clip_h=h)
    run(pw, 1680, 1000, logs)

    # 05 dashboard: all panels + $room variable
    def dash(pg, w, h):
        pg.goto(f"{BASE}/dashboard/{DASH}?startTime={START}&endTime={END}",
                wait_until="networkidle")
        pg.wait_for_timeout(9000)
        dismiss(pg)
        shot(pg, "05-dashboard.png", w, h, clip_h=h, top=0)
    run(pw, 1680, 1240, dash)

    # 06 services: RED metrics for the 7 ha.* services
    def services(pg, w, h):
        pg.goto(f"{BASE}/services?startTime={START}&endTime={END}",
                wait_until="networkidle")
        pg.wait_for_selector("text=ha.automation", timeout=30000)
        dismiss(pg)
        pg.wait_for_timeout(2500)
        shot(pg, "06-services-map.png", w, h, clip_h=h)
    run(pw, 1680, 1000, services)

print("SIGNOZ SHOTS DONE")
