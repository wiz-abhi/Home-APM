"""Crop the service-map graph -> 06, and re-shoot 04 logs cleanly."""
import os
from datetime import datetime, timezone
from PIL import Image
from playwright.sync_api import sync_playwright

OUT = r"C:\Users\abhis\Desktop\OSS\Signoz\Track3\home-apm\docs\screenshots"
BASE = "http://localhost:8080"
EMAIL = os.environ["SIGNOZ_EMAIL"]
PASSWORD = os.environ["SIGNOZ_PASSWORD"]
NAV_W = 56

# --- crop the service-map graph (source is 3248x2000, DSF 2) ---------------
src = Image.open(os.path.join(OUT, "06b-service-map-graph.png"))
# graph bounding box (actual px) with padding, keeps all 7 domain labels
box = (1120, 770, 2420, 1700)
src.crop(box).save(os.path.join(OUT, "06-services-map.png"))
print("cropped 06-services-map.png", src.crop(box).size)


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


with sync_playwright() as pw:
    b = pw.chromium.launch()
    pg = b.new_page(viewport={"width": 1680, "height": 1000}, device_scale_factor=2)
    login(pg)
    pg.goto(f"{BASE}/logs/logs-explorer?startTime={START}&endTime={END}",
            wait_until="networkidle")
    pg.wait_for_timeout(2500)
    for label in ("Okay", "Got it", "Skip", "Close"):
        try:
            pg.click(f'button:has-text("{label}")', timeout=1000)
        except Exception:
            pass
    qb = pg.locator('[contenteditable="true"]').first
    qb.click()
    pg.keyboard.press("Control+A")
    pg.keyboard.press("Delete")
    pg.keyboard.type("service.name = 'ha.sidecar'", delay=15)
    pg.keyboard.press("Escape")
    pg.click('button:has-text("Run Query")', timeout=3000)
    pg.wait_for_timeout(4500)
    pg.mouse.move(840, 130)     # neutral: kill hover tooltips
    pg.wait_for_timeout(600)
    pg.screenshot(path=os.path.join(OUT, "04-logs-correlated.png"),
                  clip={"x": NAV_W, "y": 0, "width": 1680 - NAV_W, "height": 1150})
    print("saved 04-logs-correlated.png")
    b.close()
print("DONE")
