"""Screenshot the styled terminal card -> 08-ask-your-house.png."""
import os
from playwright.sync_api import sync_playwright

HTML = r"C:\Users\abhis\Desktop\OSS\Signoz\Track3\home-apm\tools\shots\term.html"
OUT = r"C:\Users\abhis\Desktop\OSS\Signoz\Track3\home-apm\docs\screenshots\08-ask-your-house.png"

with sync_playwright() as pw:
    b = pw.chromium.launch()
    pg = b.new_page(viewport={"width": 1080, "height": 500}, device_scale_factor=2)
    pg.goto("file:///" + HTML.replace("\\", "/"))
    pg.wait_for_timeout(800)
    el = pg.query_selector(".term")
    box = el.bounding_box()
    pg.screenshot(path=OUT, clip={
        "x": box["x"] - 40, "y": box["y"] - 40,
        "width": box["width"] + 80, "height": box["height"] + 80,
    })
    print("saved 08-ask-your-house.png")
    b.close()
