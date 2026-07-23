"""Build the MEASURED-TIMELINE narration track + caption sidecar for the final cut.

Reads tools/video/manifest.json (measured content-visible start/end of every beat
in the CONCATENATED timeline: 32.133s intro + 203.133s master = 235.267s) and:

  1. For each beat, take the spoken line (TEXT below). Reuse the already-synthesized
     natural-pace clip in _work when present (body beats beat01..22.wav, intro
     intro1..4.wav); only RE-synthesize when a beat must be compressed to fit.
  2. FIT each clip inside its measured window: first Sarvam `pace`, then ffmpeg
     `atempo`. Limits: intro pace<=1.05 / atempo<=1.10 ; body pace<=1.15 / atempo<=1.20.
  3. PLACE each clip at max(beat.start + LEAD, prev_clip_end + GAP) so the voice
     never leads the visual and two clips never overlap.
  4. Assemble over a full-length silence bed, loudnorm -16 LUFS, 48kHz stereo AAC
     -> _work/narration_full.m4a.
  5. Emit docs/video/CAPTIONS.srt from the ACTUAL clip placements (cue text = spoken
     line, split to <=2 lines; long lines split into sequential cues).

Secrets: Sarvam key from env SARVAM_API_KEY or .ha-runtime/sarvam.key (never printed).
Run: .venv/Scripts/python.exe tools/video/narrate_final.py
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from shutil import which

import httpx

REPO = Path(r"C:\Users\abhis\Desktop\OSS\Signoz\Track3\home-apm")
WORK = REPO / "tools" / "video" / "_work"
MANIFEST = REPO / "tools" / "video" / "manifest.json"
SRT = REPO / "docs" / "video" / "CAPTIONS.srt"
TRACK = WORK / "narration_full.m4a"

API_URL = "https://api.sarvam.ai/text-to-speech"
MODEL, SPEAKER, LANG, SR = "bulbul:v2", "hitesh", "en-IN", 48000
KEY_FILE = REPO / ".ha-runtime" / "sarvam.key"

LEAD = 0.30          # voice starts this long after the visual appears
GAP = 0.15           # min silence between consecutive clips (no overlap)


def _tool(name: str) -> str:
    exe = which(name)
    if exe:
        return exe
    try:
        import imageio_ffmpeg
        if name == "ffmpeg":
            return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # noqa: BLE001
        pass
    return name


FFMPEG, FFPROBE = _tool("ffmpeg"), _tool("ffprobe")

# --- spoken lines (verbatim). Intro = approved script; body = unchanged. ------ #
TEXT: dict[str, str] = {
    "i1": "This is Home APM — my Track 3 project for the Agents of SigNoz hackathon.",
    "i2": "Over two million homes run on Home Assistant automations — but when one "
          "misbehaves, there's no way to see why. Its built-in trace view is famously unreadable.",
    "i3": "Home APM bridges Home Assistant's hidden internal traces into OpenTelemetry, "
          "so every automation run becomes a flame graph in SigNoz — with logs, metrics, "
          "dashboards, and alerts to match.",
    "i4": "It installs with one command. Here's what it found in my house on the first night.",
    "b1": "My hallway lights turned on at three in the morning. And I had no idea why.",
    "b2": "Home Assistant does record a trace. But this is what it gives you: cryptic node paths, "
          "a run that says it finished in zero seconds, and it keeps only the last five.",
    "b3": "So it couldn't actually tell me what happened.",
    "b4": "So I gave my house an APM.",
    "b5": "A sidecar turns every automation run into a real OpenTelemetry trace, and sends it "
          "to SigNoz. Four saved views, one per kind of failure. Let's open the three a.m. mystery.",
    "b6": "There it is. A sun-and-time trigger fired, a choose branch was taken, and its condition "
          "passed silently, turning the hallway lights on. The branch Home Assistant would never "
          "show me is now a span I can click.",
    "b7": "And the log line and the trace are one click apart.",
    "b8": "The bridge narrates itself: every converted-run log carries the trace id it produced. "
          "Logs and traces, natively correlated.",
    "b9": "Next mystery: my morning routine felt slow.",
    "b10": "One span explains all of it: a wait-for-trigger that sat idle for fifty-two seconds. "
           "The villain is impossible to miss.",
    "b11": "This is the part every naive trace reader gets wrong.",
    "b12": "A real tracer. The parallel block draws as overlapping bars, one branch clearly slower. "
           "The repeat loop stacks one span per iteration. And a template divides by zero, so it "
           "lights up as a red error span.",
    "b13": "You don't even have to read a flame graph.",
    "b14": "Ask in plain English. Through the SigNoz MCP server, it finds the run, reads the span "
           "tree, and answers in one sentence: the silent branch, the fifty-two-second wait, the "
           "divide-by-zero, each with the exact trace id and a link to the flame graph.",
    "b15": "And there's a board my partner could read.",
    "b16": "Panels titled as questions: how often are my automations running, which are slowest, "
           "is anything failing, plus a room selector that refocuses the whole board.",
    "b17": "Every domain your automations touch, drawn as a service map of your house, with the "
           "failing path to persistent-notification flagged in red.",
    "b18": "And when something breaks, it says so.",
    "b19": "A traces-based rule, Automation failing, is firing on those error spans.",
    "b20": "And the loop closes: the SigNoz alert lands back inside Home Assistant as a "
           "notification, right where you'd look for it.",
    "b21": "Home Assistant always had traces. It just never let anyone see them.",
    "b22": "So give your house an APM. One command, and the dashboard's already live.",
}

# on-screen CAPTION text (spoken line, lightly punctuated for reading). Same words.
CAPTION = dict(TEXT)

# existing natural-pace clips to reuse when no compression is needed
REUSE = {f"b{i}": WORK / f"beat{i:02d}.wav" for i in range(1, 23)}
REUSE.update({f"i{i}": WORK / f"intro{i}.wav" for i in range(1, 5)})


@dataclass
class Placed:
    id: str
    start: float
    dur: float
    text: str
    note: str = ""


def api_key() -> str:
    k = os.environ.get("SARVAM_API_KEY")
    if k:
        return k.strip()
    return KEY_FILE.read_text(encoding="utf-8-sig").strip()


def synth(text: str, pace: float, out: Path, key: str) -> None:
    body = {"text": text, "target_language_code": LANG, "speaker": SPEAKER,
            "model": MODEL, "pace": round(pace, 3), "loudness": 1.0,
            "speech_sample_rate": SR, "enable_preprocessing": True,
            "output_audio_codec": "wav"}
    r = httpx.post(API_URL, headers={"api-subscription-key": key,
                   "Content-Type": "application/json"}, json=body, timeout=60.0)
    if r.status_code != 200:
        raise SystemExit(f"Sarvam {r.status_code}: {r.text[:200]}")
    out.write_bytes(base64.b64decode(r.json()["audios"][0]))


def dur(p: Path) -> float:
    o = subprocess.run([FFPROBE, "-v", "error", "-show_entries", "format=duration",
                        "-of", "default=noprint_wrappers=1:nokey=1", str(p)],
                       capture_output=True, text=True, check=True)
    return float(o.stdout.strip())


def atempo(src: Path, dst: Path, factor: float) -> None:
    subprocess.run([FFMPEG, "-y", "-i", str(src), "-filter:a", f"atempo={factor:.4f}",
                    "-c:a", "pcm_s16le", str(dst)], capture_output=True, text=True, check=True)


def fit(bid: str, text: str, allowed: float, key: str) -> tuple[Path, float, str]:
    """Return (clip, duration, note) fitted to <= allowed seconds."""
    is_intro = bid.startswith("i")
    max_pace = 1.05 if is_intro else 1.15
    max_tempo = 1.10 if is_intro else 1.20

    raw = REUSE[bid]
    if not raw.exists():
        raw = WORK / f"final_{bid}.wav"
        synth(text, 1.0, raw, key)
    d = dur(raw)
    if d <= allowed:
        return raw, d, "natural"

    target_pace = min(max_pace, d / allowed)
    paced = WORK / f"final_{bid}_paced.wav"
    synth(text, target_pace, paced, key)
    pd = dur(paced)
    note = f"pace={target_pace:.2f}"
    if pd <= allowed:
        return paced, pd, note

    tempo = min(max_tempo, pd / allowed)
    ft = WORK / f"final_{bid}_fit.wav"
    atempo(paced, ft, tempo)
    fd = dur(ft)
    note += f", atempo={tempo:.2f}"
    if fd > allowed + 0.06:
        note += "  << STILL OVER"
    return ft, fd, note


# --------------------------- caption helpers ------------------------------- #

def split_chunks(text: str, limit: int = 60) -> list[str]:
    """Split a spoken line into in-order caption chunks <= limit chars (whole words)."""
    words = text.strip().split()
    chunks: list[str] = []
    cur = ""
    for w in words:
        cand = w if not cur else f"{cur} {w}"
        if len(cand) <= limit:
            cur = cand
        else:
            if cur:
                chunks.append(cur)
            cur = w
    if cur:
        chunks.append(cur)
    return chunks or [text.strip()]


def wrap2(chunk: str, width: int = 30) -> str:
    """Wrap a chunk to at most 2 balanced lines."""
    if len(chunk) <= width:
        return chunk
    words = chunk.split()
    # find split near middle
    best, target = None, len(chunk) / 2
    acc = 0
    for i, w in enumerate(words[:-1]):
        acc += len(w) + 1
        if best is None or abs(acc - target) < abs(best[1] - target):
            best = (i, acc)
    i = best[0]
    return " ".join(words[: i + 1]) + "\n" + " ".join(words[i + 1:])


def srt_ts(t: float) -> str:
    if t < 0:
        t = 0
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = int(t % 60)
    ms = int(round((t - int(t)) * 1000))
    if ms == 1000:
        ms = 0
        s += 1
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def write_srt(placed: list[Placed], beats: dict[str, dict]) -> int:
    cues: list[tuple[float, float, str]] = []
    for p in placed:
        chunks = split_chunks(CAPTION[p.id])
        total = sum(len(c) for c in chunks)
        clip_end = p.start + p.dur
        t = p.start
        for j, c in enumerate(chunks):
            frac = len(c) / total
            seg = p.dur * frac
            cs = t
            ce = t + seg
            if j == len(chunks) - 1:
                ce = clip_end + 0.3          # small hold on the last chunk
            # clamp to this beat's visual window end
            ce = min(ce, beats[p.id]["end"] + 0.35)
            cues.append((cs, max(ce, cs + 0.4), wrap2(c)))
            t += seg
    # prevent overlap between consecutive cues
    for i in range(len(cues) - 1):
        cs, ce, tx = cues[i]
        ncs = cues[i + 1][0]
        if ce > ncs - 0.05:
            cues[i] = (cs, ncs - 0.05, tx)
    lines = []
    for i, (cs, ce, tx) in enumerate(cues, 1):
        lines.append(str(i))
        lines.append(f"{srt_ts(cs)} --> {srt_ts(ce)}")
        lines.append(tx)
        lines.append("")
    SRT.write_text("\n".join(lines), encoding="utf-8")
    return len(cues)


# ------------------------------- build ------------------------------------- #

def build() -> int:
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    beats = {b["id"]: b for b in data["beats"]}
    vdur = data["video_duration"]
    key = api_key()

    placed: list[Placed] = []
    prev_end = 0.0
    report = []
    for b in data["beats"]:
        bid = b["id"]
        start = max(b["start"] + LEAD, prev_end + GAP)
        allowed = b["end"] - start
        if allowed < 0.4:
            allowed = 0.4
        clip, d, note = fit(bid, TEXT[bid], allowed, key)
        placed.append(Placed(bid, start, d, TEXT[bid], note))
        prev_end = start + d
        slack = b["end"] - prev_end
        report.append(f"{bid:>3}  start={start:7.2f}  dur={d:5.2f}  "
                      f"end={prev_end:7.2f}  winEnd={b['end']:7.2f}  slack={slack:+5.2f}  {note}")

    print("\n".join(report))
    over = [p.id for p in placed if p.start + p.dur > beats[p.id]["end"] + 0.25]
    if over:
        print("WARN beats spilling >0.25s past their window:", over)

    # ---- assemble track ----
    inputs = ["-f", "lavfi", "-t", f"{vdur}", "-i", f"anullsrc=r={SR}:cl=stereo"]
    fitted_clips = []
    for p in placed:
        # re-derive the exact clip path used by fit()
        for cand in (WORK / f"final_{p.id}_fit.wav", WORK / f"final_{p.id}_paced.wav",
                     WORK / f"final_{p.id}.wav", REUSE[p.id]):
            if cand.exists() and abs(dur(cand) - p.dur) < 0.02:
                fitted_clips.append(cand)
                break
        else:
            fitted_clips.append(REUSE[p.id])
    for c in fitted_clips:
        inputs += ["-i", str(c)]

    parts, labels = [], ["[0:a]"]
    for n, p in enumerate(placed, start=1):
        delay = int(round(p.start * 1000))
        parts.append(f"[{n}:a]aresample={SR},aformat=channel_layouts=stereo,"
                     f"adelay={delay}:all=1[c{n}]")
        labels.append(f"[c{n}]")
    mix = (";".join(parts) + ";" + "".join(labels)
           + f"amix=inputs={len(placed)+1}:normalize=0:duration=first[m];"
           + "[m]loudnorm=I=-16:TP=-1.5:LRA=11," + f"aresample={SR}[out]")

    subprocess.run([FFMPEG, "-y", *inputs, "-filter_complex", mix,
                    "-map", "[out]", "-c:a", "aac", "-b:a", "192k", "-ar", str(SR),
                    "-t", f"{vdur}", str(TRACK)],
                   capture_output=True, text=True, check=True)
    print(f"\nnarration track -> {TRACK}  ({dur(TRACK):.2f}s)")

    n = write_srt(placed, beats)
    print(f"captions        -> {SRT}  ({n} cues)")
    return 0


if __name__ == "__main__":
    raise SystemExit(build())
