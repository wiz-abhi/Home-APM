"""Home Assistant UI screenshots: 07 (SigNoz alert notification) + 09 (native trace).

HA login: homeapm / homeapm-spike-2026.
"""
import os
import sys
from playwright.sync_api import sync_playwright

HA = "http://localhost:8123"
USER = "homeapm"
PW = "homeapm-spike-2026"
OUT = r"C:\Users\abhis\Desktop\OSS\Signoz\Track3\home-apm\docs\screenshots"
STEP = sys.argv[1] if len(sys.argv) > 1 else "debug"


def login(pg):
    pg.goto(HA, wait_until="networkidle")
    pg.wait_for_timeout(2500)
    # HA wraps inputs in web components; target raw inputs
    pg.fill('input[name="username"]', USER)
    pg.fill('input[name="password"]', PW)
    pg.wait_for_timeout(300)
    pg.keyboard.press("Enter")
    pg.wait_for_timeout(5000)


with sync_playwright() as pw:
    b = pw.chromium.launch()
    pg = b.new_page(viewport={"width": 1500, "height": 950}, device_scale_factor=2)
    login(pg)

    if STEP == "debug":
        pg.screenshot(path=os.path.join(OUT, "_ha_debug.png"))
        print("saved _ha_debug.png; url:", pg.url)

    elif STEP == "notif":
        pg.click('text=Notifications')
        pg.wait_for_timeout(2500)
        try:
            pg.locator('text=SigNoz [FIRING]: Automation failing').scroll_into_view_if_needed(timeout=4000)
        except Exception as e:
            print("scroll failed:", e)
        pg.wait_for_timeout(1200)
        # crop to the notifications drawer (left panel)
        pg.screenshot(path=os.path.join(OUT, "07-alert-in-ha.png"),
                      clip={"x": 0, "y": 0, "width": 660, "height": 950})
        print("saved 07-alert-in-ha.png")

    elif STEP == "trace":
        pg.goto(f"{HA}/config/automation/trace/hallway_lights_3am",
                wait_until="networkidle")
        pg.wait_for_timeout(6000)
        try:
            pg.click('text=Trace timeline', timeout=4000)
            pg.wait_for_timeout(2500)
        except Exception as e:
            print("timeline tab click failed:", e)
        pg.screenshot(path=os.path.join(OUT, "09-ha-native-trace.png"))
        print("saved 09-ha-native-trace.png; url:", pg.url)

    b.close()
print("HA STEP DONE:", STEP)
