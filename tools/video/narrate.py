"""Synthesize an AI voiceover for docs/video/home-apm-demo.mp4 with Sarvam TTS.

Pipeline:
  1. For each narration beat (see BEATS below), call the Sarvam text-to-speech
     API (model bulbul:v2, speaker `hitesh`, en-IN) -> a mono 48kHz wav clip.
  2. Measure each clip with ffprobe and FIT it inside its beat window:
     first Sarvam `pace` (<=1.15), then ffmpeg `atempo` (<=1.2). Lines are
     pre-trimmed so nothing needs more than that.
  3. Assemble the full 203s track: each clip delayed to its beat timestamp,
     summed over a full-length silence bed, loudnorm'd to ~-16 LUFS, 48kHz
     stereo AAC.
  4. Mux with the ORIGINAL video stream copied (never re-encoded) ->
     docs/video/home-apm-demo-narrated.mp4. The silent original is untouched.

Secrets: the Sarvam key is read at runtime from env SARVAM_API_KEY or
.ha-runtime/sarvam.key. It is never printed and never written to disk.

Intermediates land in tools/video/_work/ (gitignored).

Usage:
    python tools/video/narrate.py            # full build
    python tools/video/narrate.py --test     # audition 2 speakers, opening line
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from shutil import which

import httpx

REPO = Path(r"C:\Users\abhis\Desktop\OSS\Signoz\Track3\home-apm")
WORK = REPO / "tools" / "video" / "_work"
VIDEO = REPO / "docs" / "video" / "home-apm-demo.mp4"
OUT = REPO / "docs" / "video" / "home-apm-demo-narrated.mp4"
KEY_FILE = REPO / ".ha-runtime" / "sarvam.key"

API_URL = "https://api.sarvam.ai/text-to-speech"
MODEL = "bulbul:v2"
SPEAKER = "hitesh"          # male, warm/energetic — chosen after audition
LANG = "en-IN"
SAMPLE_RATE = 48000
GUARD = 0.30                # leave this much silence at the tail of each window
MAX_PACE = 1.15
MAX_ATEMPO = 1.20
VIDEO_DUR = 203.133         # measured with ffprobe


@dataclass
class Beat:
    idx: int
    start: float            # seconds into the video
    text: str


# start = beat timestamp; window = next beat's start (last beat -> VIDEO_DUR).
# Text is the spoken line. A few lines were trimmed from NARRATION.md so they
# fit their window at a calm pace; NARRATION.md + CAPTIONS.srt were updated to
# match what the voice actually says.
BEATS: list[Beat] = [
    Beat(1, 0.0,
         "My hallway lights turned on at three in the morning. And I had no idea why."),
    Beat(2, 5.0,
         "Home Assistant does record a trace. But this is what it gives you: cryptic node paths, "
         "a run that says it finished in zero seconds, and it keeps only the last five."),
    Beat(3, 16.0,
         "So it couldn't actually tell me what happened."),
    Beat(4, 20.0,
         "So I gave my house an APM."),
    Beat(5, 24.0,
         "A sidecar turns every automation run into a real OpenTelemetry trace, and sends it "
         "to SigNoz. Four saved views, one per kind of failure. Let's open the three a.m. mystery."),
    Beat(6, 40.0,
         "There it is. A sun-and-time trigger fired, a choose branch was taken, and its condition "
         "passed silently, turning the hallway lights on. The branch Home Assistant would never "
         "show me is now a span I can click."),
    Beat(7, 53.0,
         "And the log line and the trace are one click apart."),
    Beat(8, 56.0,
         "The bridge narrates itself: every converted-run log carries the trace id it produced. "
         "Logs and traces, natively correlated."),
    Beat(9, 66.0,
         "Next mystery: my morning routine felt slow."),
    Beat(10, 69.0,
         "One span explains all of it: a wait-for-trigger that sat idle for fifty-two seconds. "
         "The villain is impossible to miss."),
    Beat(11, 87.0,
         "This is the part every naive trace reader gets wrong."),
    Beat(12, 91.0,
         "A real tracer. The parallel block draws as overlapping bars, one branch clearly slower. "
         "The repeat loop stacks one span per iteration. And a template divides by zero, so it "
         "lights up as a red error span."),
    Beat(13, 106.0,
         "You don't even have to read a flame graph."),
    Beat(14, 109.0,
         "Ask in plain English. Through the SigNoz MCP server, it finds the run, reads the span "
         "tree, and answers in one sentence: the silent branch, the fifty-two-second wait, the "
         "divide-by-zero, each with the exact trace id and a link to the flame graph."),
    Beat(15, 133.0,
         "And there's a board my partner could read."),
    Beat(16, 136.0,
         "Panels titled as questions: how often are my automations running, which are slowest, "
         "is anything failing, plus a room selector that refocuses the whole board."),
    Beat(17, 156.0,
         "Every domain your automations touch, drawn as a service map of your house, with the "
         "failing path to persistent-notification flagged in red."),
    Beat(18, 164.0,
         "And when something breaks, it says so."),
    Beat(19, 167.0,
         "A traces-based rule, Automation failing, is firing on those error spans."),
    Beat(20, 177.0,
         "And the loop closes: the SigNoz alert lands back inside Home Assistant as a "
         "notification, right where you'd look for it."),
    Beat(21, 186.0,
         "Home Assistant always had traces. It just never let anyone see them."),
    Beat(22, 191.0,
         "So give your house an APM. One command, and the dashboard's already live."),
]


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


FFMPEG = _tool("ffmpeg")
FFPROBE = _tool("ffprobe")


def api_key() -> str:
    key = os.environ.get("SARVAM_API_KEY")
    if key:
        return key.strip()
    if KEY_FILE.exists():
        return KEY_FILE.read_text(encoding="utf-8-sig").strip()
    raise SystemExit("ERROR: no Sarvam key (set SARVAM_API_KEY or .ha-runtime/sarvam.key)")


def synth(text: str, pace: float, out_wav: Path, key: str) -> None:
    """One TTS request -> wav on disk. Text is well under the 1500-char v2 cap."""
    body = {
        "text": text,
        "target_language_code": LANG,
        "speaker": SPEAKER,
        "model": MODEL,
        "pace": round(pace, 3),
        "loudness": 1.0,
        "speech_sample_rate": SAMPLE_RATE,
        "enable_preprocessing": True,
        "output_audio_codec": "wav",
    }
    resp = httpx.post(
        API_URL,
        headers={"api-subscription-key": key, "Content-Type": "application/json"},
        json=body,
        timeout=60.0,
    )
    if resp.status_code != 200:
        raise SystemExit(f"ERROR: Sarvam {resp.status_code}: {resp.text[:300]}")
    audio_b64 = resp.json()["audios"][0]
    out_wav.write_bytes(base64.b64decode(audio_b64))


def duration(path: Path) -> float:
    out = subprocess.run(
        [FFPROBE, "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True, check=True,
    )
    return float(out.stdout.strip())


def atempo(src: Path, dst: Path, factor: float) -> None:
    subprocess.run(
        [FFMPEG, "-y", "-i", str(src), "-filter:a", f"atempo={factor:.4f}",
         "-c:a", "pcm_s16le", str(dst)],
        capture_output=True, text=True, check=True,
    )


def test_speakers(key: str) -> None:
    WORK.mkdir(parents=True, exist_ok=True)
    line = BEATS[0].text
    for spk in ("hitesh", "anushka"):
        out = WORK / f"_test_{spk}.wav"
        body = {
            "text": line, "target_language_code": LANG, "speaker": spk,
            "model": MODEL, "pace": 1.0, "speech_sample_rate": SAMPLE_RATE,
            "enable_preprocessing": True, "output_audio_codec": "wav",
        }
        resp = httpx.post(
            API_URL,
            headers={"api-subscription-key": key, "Content-Type": "application/json"},
            json=body, timeout=60.0,
        )
        if resp.status_code != 200:
            print(f"{spk}: HTTP {resp.status_code} {resp.text[:200]}")
            continue
        out.write_bytes(base64.b64decode(resp.json()["audios"][0]))
        print(f"{spk}: {duration(out):.2f}s -> {out}")


def build() -> int:
    WORK.mkdir(parents=True, exist_ok=True)
    key = api_key()

    windows = []
    for i, b in enumerate(BEATS):
        end = BEATS[i + 1].start if i + 1 < len(BEATS) else VIDEO_DUR
        windows.append(end - b.start)

    fitted: list[tuple[Beat, Path, float]] = []
    report: list[str] = []
    overflow = False

    for b, win in zip(BEATS, windows):
        allowed = win - GUARD
        raw = WORK / f"beat{b.idx:02d}.wav"
        synth(b.text, pace=1.0, out_wav=raw, key=key)
        dur = duration(raw)
        clip, factor, note = raw, 1.0, ""

        if dur > allowed:
            # 1) Sarvam pace up to MAX_PACE
            target_pace = min(MAX_PACE, dur / allowed)
            paced = WORK / f"beat{b.idx:02d}_paced.wav"
            synth(b.text, pace=target_pace, out_wav=paced, key=key)
            pdur = duration(paced)
            note = f"pace={target_pace:.2f}"
            clip, dur = paced, pdur
            if pdur > allowed:
                # 2) ffmpeg atempo up to MAX_ATEMPO
                tempo = min(MAX_ATEMPO, pdur / allowed)
                fit = WORK / f"beat{b.idx:02d}_fit.wav"
                atempo(paced, fit, tempo)
                fdur = duration(fit)
                note += f", atempo={tempo:.2f}"
                clip, dur = fit, fdur
                if fdur > allowed + 0.05:
                    note += "  << STILL OVER: SHORTEN TEXT"
                    overflow = True

        fitted.append((b, clip, dur))
        slack = allowed - dur
        report.append(
            f"beat {b.idx:2d}  win={win:5.1f}s  spoken={dur:5.2f}s  "
            f"slack={slack:+5.2f}s  {note}")

    print("\n".join(report))
    print(f"\nTotal characters synthesized: {sum(len(b.text) for b in BEATS)}")
    if overflow:
        print("\n!! Some beats still overflow after pace+atempo — shorten those lines.")
        return 3

    # ---- assemble: silence bed + delayed clips, summed, loudnorm, AAC ----
    inputs: list[str] = ["-f", "lavfi", "-t", f"{VIDEO_DUR}",
                         "-i", f"anullsrc=r={SAMPLE_RATE}:cl=stereo"]
    for _, clip, _ in fitted:
        inputs += ["-i", str(clip)]

    parts = []
    labels = ["[0:a]"]
    for n, (b, _, _) in enumerate(fitted, start=1):
        delay = int(round(b.start * 1000))
        parts.append(
            f"[{n}:a]aresample={SAMPLE_RATE},aformat=channel_layouts=stereo,"
            f"adelay={delay}:all=1[c{n}]")
        labels.append(f"[c{n}]")
    mix = (";".join(parts) + ";" + "".join(labels)
           + f"amix=inputs={len(fitted) + 1}:normalize=0:duration=first[m];"
           + "[m]loudnorm=I=-16:TP=-1.5:LRA=11,"
           + f"aresample={SAMPLE_RATE}[out]")

    track = WORK / "narration.m4a"
    subprocess.run(
        [FFMPEG, "-y", *inputs, "-filter_complex", mix,
         "-map", "[out]", "-c:a", "aac", "-b:a", "192k", "-ar", str(SAMPLE_RATE),
         "-t", f"{VIDEO_DUR}", str(track)],
        capture_output=True, text=True, check=True,
    )
    print(f"narration track -> {track}  ({duration(track):.2f}s)")

    # ---- mux: copy video, add AAC audio ----
    subprocess.run(
        [FFMPEG, "-y", "-i", str(VIDEO), "-i", str(track),
         "-map", "0:v:0", "-map", "1:a:0",
         "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
         "-shortest", "-movflags", "+faststart", str(OUT)],
        capture_output=True, text=True, check=True,
    )
    print(f"DONE -> {OUT}  ({duration(OUT):.2f}s)")
    return 0


def main() -> int:
    if "--test" in sys.argv:
        test_speakers(api_key())
        return 0
    return build()


if __name__ == "__main__":
    raise SystemExit(main())
