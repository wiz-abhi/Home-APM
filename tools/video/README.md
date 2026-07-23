# tools/video â€” the demo video pipeline

Builds `docs/video/home-apm-demo.mp4` (1080p, H.264, ~3:20) with **zero manual
editing**: one continuous Playwright chromium session drives the whole storyboard,
title cards are dark HTML pages navigated to *inside the same recorded context*, and
the raw `.webm` is transcoded once with ffmpeg.

## One-command rebuild

From the repo root, with the stack + sidecar up and credentials exported
(`SIGNOZ_EMAIL`, `SIGNOZ_PASSWORD`, `HA_USERNAME`, `HA_PASSWORD` â€” see
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
- **`record_demo.py`** â€” pre-auth pass saves a combined `storage_state` (SigNoz +
  Home Assistant) so the recorded session has *no login screens*; then one recorded
  context walks the storyboard: cold-open â†’ HA's cryptic native trace â†’ the 3am
  waterfall (silent choose branch) â†’ sidecar logs â†’ the 52s `wait_for_trigger` â†’
  good_night parallel/repeat/error â†’ a live `ask.py` terminal â†’ the room dashboard â†’
  house service map â†’ the "Automation failing" alert â†’ the notification back inside HA
  â†’ close cards. Pacing holds scale with the `PACE` env var (default 1.0).
- **`assemble.py`** â€” single ffmpeg re-encode (VP8 webm â†’ H.264 yuv420p, 30fps,
  padded to 1920x1080, `+faststart`).
- **`_work/`** â€” scratch: the raw `.webm`, the pre-auth `storage_state.json`, the
  generated `terminal.html`, run logs, and verification frames. Disposable; not a
  deliverable.

## AI voiceover (`narrate.py`)
`narrate.py` dubs the silent `home-apm-demo.mp4` using **Sarvam TTS** (model
`bulbul:v2`, speaker `hitesh`, en-IN). Each narration beat in
`docs/video/NARRATION.md` is synthesized, measured with ffprobe, and **fit
inside its beat window** â€” first via Sarvam `pace` (â‰¤1.15), then ffmpeg
`atempo` (â‰¤1.2); a couple of lines were shortened so nothing needs more. Clips
are delayed to their timestamps, summed over a silence bed, `loudnorm`'d to
~-16 LUFS, and muxed onto the **copied** video stream (never re-encoded) as
`home-apm-demo-narrated.mp4`.

```bash
# key: .ha-runtime/sarvam.key (gitignored) or env SARVAM_API_KEY
.venv/Scripts/python.exe tools/video/narrate.py          # full build
.venv/Scripts/python.exe tools/video/narrate.py --test   # audition speakers
```
Intermediates (per-beat wavs, the assembled track) go to `_work/` (gitignored).

## Final cut (`home-apm-demo-final.mp4`) â€” intro + measured sync + burned captions
The final deliverable prepends a ~32s title-card **intro**, syncs the voiceover to a
**measured** timeline (fixing the drift in the older narrated cut), and **burns the
captions into the frame**. Rebuild = record intro â†’ concat â†’ measure â†’ narrate â†’ burn:

```bash
# 1. record the 4-card intro (Playwright, same dark card style as record_demo.py)
"C:/Users/abhis/Desktop/OSS/Signoz/warmup-agent/.venv/Scripts/python.exe" \
  tools/video/record_intro.py
# transcode the intro webm to mp4 matching the master (H.264 High, yuv420p, 30fps,
# level 4.0, timescale 15360) so it concats losslessly:
ffmpeg -y -i tools/video/_work/intro_raw/*.webm \
  -vf "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,fps=30,format=yuv420p" \
  -c:v libx264 -profile:v high -level 4.0 -preset medium -crf 20 \
  -video_track_timescale 15360 -movflags +faststart -an tools/video/_work/intro.mp4

# 2. concat intro + silent master -> one silent timeline (stream copy)
printf "file 'intro.mp4'\nfile '%s/docs/video/home-apm-demo.mp4'\n" "$(pwd)" \
  > tools/video/_work/concat_list.txt
ffmpeg -y -f concat -safe 0 -i tools/video/_work/concat_list.txt -c copy \
  -movflags +faststart tools/video/_work/timeline.mp4

# 3. MEASURE the timeline: scene detection + frame inspection -> tools/video/manifest.json
#    (ffmpeg select='gt(scene,0.045)',showinfo for cuts; contact sheets via the
#     `tile` filter, viewed frame-by-frame, to label every beat. Card->card and
#     dark-UI->dark-UI transitions score too low to detect, so those are read off
#     the sheets and interpolated.) The manifest is hand-verified, not auto-generated.

# 4. narrate from the manifest (reuses the natural-pace clips in _work; only
#    re-synthesizes beats that need compression) -> _work/narration_full.m4a + CAPTIONS.srt
.venv/Scripts/python.exe tools/video/narrate_final.py

# 5. burn captions + mux audio -> docs/video/home-apm-demo-final.mp4
cp docs/video/CAPTIONS.srt tools/video/_work/cap.srt
STYLE="FontName=Segoe UI,Fontsize=15,PrimaryColour=&H00FFFFFF,OutlineColour=&H90000000,BorderStyle=3,Outline=3,Shadow=0,Alignment=2,MarginV=20,Bold=1"
ffmpeg -y -i tools/video/_work/timeline.mp4 -i tools/video/_work/narration_full.m4a \
  -filter_complex "[0:v]subtitles=tools/video/_work/cap.srt:force_style='${STYLE}'[v]" \
  -map "[v]" -map "1:a:0" -c:v libx264 -profile:v high -level 4.0 -preset medium -crf 18 \
  -pix_fmt yuv420p -r 30 -c:a aac -b:a 192k -ar 48000 -movflags +faststart -shortest \
  docs/video/home-apm-demo-final.mp4
```

`narrate_final.py` rule: each clip starts 0.3s after its beat's measured on-screen
start (voice never leads the visual) and is placed so consecutive clips never
overlap; fit is `pace` then `atempo` (intro â‰¤1.05/â‰¤1.10, body â‰¤1.15/â‰¤1.20); track is
loudnorm'd to âˆ’16 LUFS. Captions are emitted from the *actual* clip placements
(cue text = spoken line, split to â‰¤2 lines).

## Companion files (in `docs/video/`)
- `home-apm-demo.mp4` â€” the silent master (caption-driven), untouched.
- `home-apm-demo-narrated.mp4` â€” the older narrated cut (estimated-timeline sync).
- `home-apm-demo-final.mp4` â€” **the final cut**: intro + measured sync + burned captions.
- `NARRATION.md` â€” the per-beat voiceover script (exact spoken lines) + measured timestamps.
- `CAPTIONS.srt` â€” subtitle sidecar, regenerated from `manifest.json` by `narrate_final.py`.

## Measured timeline (`tools/video/manifest.json`)
The single source of truth for sync: `{id, start, end, label}` per beat, in the
concatenated 235.267s timeline. Edit measured times here â€” never hand-tune
timestamps in `NARRATION.md`.

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
