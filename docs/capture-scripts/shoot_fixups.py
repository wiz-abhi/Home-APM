"""Re-shoot 05 (dashboard, taller) + 06 (services, cropped) + service-map candidate."""
import os
from datetime import datetime, timezone
from playwright.sync_api import sync_playwright

BASE = "http://localhost:8080"
OUT = r"C:\Users\abhis\Desktop\OSS\Signoz\Track3\home-apm\docs\screenshots"
EMAIL = os.environ["SIGNOZ_EMAIL"]
PASSWORD = os.environ["SIGNOZ_PASSWORD"]
DASH = "019f8a8f-d7f4-77dd-a5b8-b69d2a7fad3b"
DSF = 2
NAV_W = 56


def now_ms():
    return int(datetime.now(tz=timezone.utc).timestamp() * 1000)


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


def dismiss(pg):
    for label in ("Okay", "Got it", "Skip", "Close"):
        try:
            pg.click(f'button:has-text("{label}")', timeout=1000)
            pg.wait_for_timeout(300)
        except Exception:
            pass


def shot(pg, name, width, clip_h, top=0):
    pg.screenshot(path=os.path.join(OUT, name),
                  clip={"x": NAV_W, "y": top, "width": width - NAV_W, "height": clip_h})
    print("saved", name)


def run(pw, width, height, fn):
    b = pw.chromium.launch()
    pg = b.new_page(viewport={"width": width, "height": height}, device_scale_factor=DSF)
    login(pg)
    fn(pg, width, height)
    b.close()


with sync_playwright() as pw:
    def dash(pg, w, h):
        pg.goto(f"{BASE}/dashboard/{DASH}?startTime={START}&endTime={END}",
                wait_until="networkidle")
        pg.wait_for_timeout(11000)
        dismiss(pg)
        # measure full content height
        ch = pg.evaluate("document.body.scrollHeight")
        print("dash scrollHeight", ch)
        shot(pg, "05-dashboard.png", w, clip_h=min(ch, h))
    run(pw, 1680, 1560, dash)

    def services(pg, w, h):
        pg.goto(f"{BASE}/services?startTime={START}&endTime={END}", wait_until="networkidle")
        pg.wait_for_selector("text=ha.automation", timeout=30000)
        dismiss(pg)
        pg.wait_for_timeout(2500)
        shot(pg, "06-services-map.png", w, clip_h=700)
    run(pw, 1680, 900, services)

    def smap(pg, w, h):
        pg.goto(f"{BASE}/service-map?startTime={START}&endTime={END}", wait_until="networkidle")
        pg.wait_for_timeout(7000)
        dismiss(pg)
        shot(pg, "06b-service-map-graph.png", w, clip_h=h)
    run(pw, 1680, 1000, smap)

print("FIXUPS DONE")
