#!/usr/bin/env python3
"""Voice clipper for OBS.

Say "clip" to save the last DEFAULT seconds, or "clip <number>" (e.g. "clip ten",
"clip forty five seconds", "clip one minute") to save that many seconds (5-60).

How it works: while OBS is recording, on "clip" this script asks OBS (over
obs-websocket) how far into the recording it is, then cuts the last N seconds out
of the recording file with ffmpeg. The recording itself is never touched.
"""
import argparse
import json
import os
import queue
import shutil
import subprocess
import sys
import time
from pathlib import Path

import obsws_python as obs
import sounddevice as sd
from vosk import KaldiRecognizer, Model, SetLogLevel

HERE = Path(__file__).resolve().parent
OBS_WS_CONFIG = Path.home() / ".config/obs-studio/plugin_config/obs-websocket/config.json"
MIN_S, MAX_S = 5, 60

UNITS = {"five": 5, "ten": 10, "fifteen": 15, "twenty": 20, "thirty": 30,
         "forty": 40, "fifty": 50, "sixty": 60}
SPOKEN = {  # every length we accept, as spoken
    **UNITS,
    "twenty five": 25, "thirty five": 35, "forty five": 45, "fifty five": 55,
    "one minute": 60, "a minute": 60,
}


def build_grammar():
    phrases = ["clip", "clip clip", "clip that"]
    for words in SPOKEN:
        phrases += [f"clip {words}", f"clip {words} seconds", f"clip clip {words}"]
    return json.dumps(phrases + ["[unk]"])


def parse_length(text, default):
    """'clip forty five seconds' -> 45, 'clip' -> default, no 'clip' -> None."""
    if "clip" not in text.split():
        return None
    for words in sorted(SPOKEN, key=len, reverse=True):  # longest match first
        if words in text:
            return max(MIN_S, min(MAX_S, SPOKEN[words]))
    return default


def notify(msg):
    print(msg, flush=True)
    if shutil.which("notify-send"):
        subprocess.run(["notify-send", "-a", "Clipper", "-t", "2500", msg], check=False)


def ws_password():
    if os.environ.get("OBS_WS_PASSWORD"):
        return os.environ["OBS_WS_PASSWORD"]
    try:
        cfg = json.loads(OBS_WS_CONFIG.read_text())
        return cfg.get("server_password", "") if cfg.get("auth_required") else ""
    except (OSError, ValueError):
        return ""


def connect_obs(port):
    try:
        return obs.ReqClient(host="localhost", port=port, password=ws_password(), timeout=5)
    except Exception as e:  # obsws raises a few different types
        sys.exit(f"Can't reach OBS on port {port}: {e}\n"
                 "In OBS: Tools > WebSocket Server Settings > tick 'Enable WebSocket server'.")


VIDEO_EXT = {".mp4", ".mkv", ".mov", ".flv", ".ts", ".m4v"}


def check_recording(cl):
    if cl.get_record_status().output_active:
        print("OBS is recording. Clips will be cut from that recording.")
    else:
        print("OBS isn't recording yet. Start recording, then say 'clip'.")


def current_recording(cl):
    """Newest video file in OBS's recording folder (the one being written)."""
    folder = Path(cl.get_record_directory().record_directory)
    files = [p for p in folder.glob("*") if p.suffix.lower() in VIDEO_EXT and p.is_file()]
    return max(files, key=lambda p: p.stat().st_mtime) if files else None


def save_clip(cl, seconds, out_dir, precise, flush_wait):
    status = cl.get_record_status()
    if not status.output_active:
        notify("Not recording in OBS, nothing to clip.")
        return
    elapsed = status.output_duration / 1000.0  # seconds into the recording right now
    src = current_recording(cl)
    if not src:
        notify("Can't find the recording file in OBS's recording folder.")
        return
    start = max(0.0, elapsed - seconds)
    length = elapsed - start
    time.sleep(flush_wait)  # let OBS write the last few seconds to disk

    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%d_%H-%M-%S")
    out = out_dir / f"clip_{stamp}_{round(length)}s.mp4"
    codec = (["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-c:a", "aac"]
             if precise else ["-c", "copy"])
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
           "-ss", f"{start:.2f}", "-i", str(src), "-t", f"{length:.2f}",
           *codec, "-avoid_negative_ts", "make_zero", "-movflags", "+faststart", str(out)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0 or not out.exists() or out.stat().st_size == 0:
        notify(f"Clip failed in ffmpeg: {r.stderr.strip()[:200]}")
        return
    notify(f"Clipped {round(length)}s -> {out}")


def main():
    ap = argparse.ArgumentParser(description="Say 'clip' to save the last few seconds from OBS.")
    ap.add_argument("--default", type=int, default=30, help="seconds for a plain 'clip' (5-60, default 30)")
    ap.add_argument("--out", type=Path, default=Path.home() / "Videos/clips", help="where clips go")
    ap.add_argument("--model", type=Path, default=HERE / "model", help="Vosk model folder")
    ap.add_argument("--port", type=int, default=4455, help="obs-websocket port")
    ap.add_argument("--mic", default=None, help="input device name or index (see --list-mics)")
    ap.add_argument("--list-mics", action="store_true")
    ap.add_argument("--flush-wait", type=float, default=2.0,
                    help="seconds to wait for OBS to write the latest video before cutting")
    ap.add_argument("--precise", action="store_true",
                    help="re-encode so the clip starts exactly N s back (slower; default cuts at nearest keyframe)")
    ap.add_argument("--cooldown", type=float, default=3.0, help="ignore new 'clip' for this many seconds")
    args = ap.parse_args()

    if args.list_mics:
        print(sd.query_devices())
        return
    default = max(MIN_S, min(MAX_S, args.default))
    if not args.model.is_dir():
        sys.exit(f"Vosk model not found at {args.model}")

    cl = connect_obs(args.port)
    check_recording(cl)

    SetLogLevel(-1)
    rate = 16000
    rec = KaldiRecognizer(Model(str(args.model)), rate, build_grammar())
    audio = queue.Queue()
    mic = int(args.mic) if args.mic and args.mic.isdigit() else args.mic

    def on_audio(indata, frames, t, status):
        audio.put(bytes(indata))

    last = 0.0
    with sd.RawInputStream(samplerate=rate, blocksize=4000, dtype="int16", channels=1,
                           device=mic, callback=on_audio):
        notify(f"Listening. Say 'clip' ({default}s) or 'clip ten' ... 'clip one minute'. Ctrl+C to stop.")
        while True:
            if not rec.AcceptWaveform(audio.get()):
                continue
            text = json.loads(rec.Result()).get("text", "")
            secs = parse_length(text, default)
            if secs is None or time.time() - last < args.cooldown:
                continue
            last = time.time()
            print(f'heard: "{text}"')
            try:
                save_clip(cl, secs, args.out, args.precise, args.flush_wait)
            except Exception as e:
                notify(f"Clip failed: {e}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped.")
