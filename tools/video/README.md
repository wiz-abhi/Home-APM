# tools/video — the demo video pipeline

Builds `docs/video/home-apm-demo.mp4` (1080p, H.264, ~3:20) with **zero manual
editing**: one continuous Playwright chromium session drives the whole storyboard,
title cards are dark HTML pages navigated to *inside the same recorded context*, and
the raw `.webm` is transcoded once with ffmpeg.

## One-command rebuild

From the repo root, with the stack + sidecar up and credentials exported
(`SIGNOZ_EMAIL`, `SIGNOZ_PASSWORD`, `HA_USERNAME`, `HA_PASSWORD` — see
`.ha-runtime/CREDENTIALS.txt`):

```bash
# 1. warm fresh demo data + regenerate the /trace/<id> deep-links
.venv/Scripts/python.exe -c "import homeapm" 2>/dev/null   # ensure PYTHONPATH=src if importing
HA=http://localhost:8123; TOKEN=$(cat .ha-runtime/token.txt)
curl -s -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -X POST $HA/api/services/script/turn_on -d '{"entity_id":"script.demo_burst"}'
sleep 22
PYTHONPATH=src .venv/Scripts/python.exe tools/views/make_demo_links.py

# 2. record (pre-auths SigNoz+HA off-camera, captures ask.py live, records ~3.5 min)
"C:/Users/abhis/Desktop/OSS/Signoz/warmup-agent/.venv/Scripts/python.exe" \
  tools/video/record_demo.py

# 3. transcode webm -> docs/video/home-apm-demo.mp4
"C:/Users/abhis/Desktop/OSS/Signoz/warmup-agent/.venv/Scripts/python.exe" \
  tools/video/assemble.py
```

`record_demo.py` reads the fresh trace ids straight out of `tools/views/DEMO-LINKS.md`,
so step 1 must run first. Playwright + chromium live in the **warmup-agent** venv
(`C:/Users/abhis/Desktop/OSS/Signoz/warmup-agent/.venv`).

## How it works
- **`record_demo.py`** — pre-auth pass saves a combined `storage_state` (SigNoz +
  Home Assistant) so the recorded session has *no login screens*; then one recorded
  context walks the storyboard: cold-open → HA's cryptic native trace → the 3am
  waterfall (silent choose branch) → sidecar logs → the 52s `wait_for_trigger` →
  good_night parallel/repeat/error → a live `ask.py` terminal → the room dashboard →
  house service map → the "Automation failing" alert → the notification back inside HA
  → close cards. Pacing holds scale with the `PACE` env var (default 1.0).
- **`assemble.py`** — single ffmpeg re-encode (VP8 webm → H.264 yuv420p, 30fps,
  padded to 1920x1080, `+faststart`).
- **`_work/`** — scratch: the raw `.webm`, the pre-auth `storage_state.json`, the
  generated `terminal.html`, run logs, and verification frames. Disposable; not a
  deliverable.

## AI voiceover (`narrate.py`)
`narrate.py` dubs the silent `home-apm-demo.mp4` using **Sarvam TTS** (model
`bulbul:v2`, speaker `hitesh`, en-IN). Each narration beat in
`docs/video/NARRATION.md` is synthesized, measured with ffprobe, and **fit
inside its beat window** — first via Sarvam `pace` (≤1.15), then ffmpeg
`atempo` (≤1.2); a couple of lines were shortened so nothing needs more. Clips
are delayed to their timestamps, summed over a silence bed, `loudnorm`'d to
~-16 LUFS, and muxed onto the **copied** video stream (never re-encoded) as
`home-apm-demo-narrated.mp4`.

```bash
# key: .ha-runtime/sarvam.key (gitignored) or env SARVAM_API_KEY
.venv/Scripts/python.exe tools/video/narrate.py          # full build
.venv/Scripts/python.exe tools/video/narrate.py --test   # audition speakers
```
Intermediates (per-beat wavs, the assembled track) go to `_work/` (gitignored).

## Companion files (in `docs/video/`)
- `home-apm-demo.mp4` — the silent deliverable (caption-driven).
- `home-apm-demo-narrated.mp4` — same video with the AI voiceover track.
- `NARRATION.md` — the per-beat voiceover script (exact spoken lines) + timestamps.
- `CAPTIONS.srt` — subtitle track matching the on-screen cards/beats.

## Verifying a rebuild
Spot-check frames across the timeline and eyeball them:
```bash
for t in 12 46 60 80 100 143 158 172 188; do
  ffmpeg -y -ss $t -i docs/video/home-apm-demo.mp4 -frames:v 1 -q:v 3 _f_$t.jpg -loglevel error
done
```
Each should show a real beat (a waterfall / dashboard / notification), never a login
page or a loading skeleton. `record_demo.py` waits on the "Spans: N" badge before
holding on a trace, which prevents catching a half-loaded flame graph.
