# clipper

Say **"clip"** while recording in OBS and get the last few seconds saved as a separate video.

| You say | You get |
|---|---|
| "clip" | last 30 s (change with `--default`) |
| "clip ten" / "clip forty five seconds" | last 10 s / 45 s |
| "clip one minute" | last 60 s (max) |

Speech recognition runs offline with [Vosk](https://alphacephei.com/vosk/). Clips are cut from the recording OBS is writing, using ffmpeg. The recording itself isn't changed.

## Requirements

- Linux, Python 3.10+
- OBS Studio 28+ (WebSocket server built in)
- ffmpeg
- A microphone

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# Speech model (~40 MB)
curl -LO https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip
unzip vosk-model-small-en-us-0.15.zip && mv vosk-model-small-en-us-0.15 model
```

In OBS: **Tools → WebSocket Server Settings → Enable WebSocket server**. The password is read from OBS's config automatically, or set `OBS_WS_PASSWORD`.

## Run

```bash
.venv/bin/python clipper.py
```

Start recording in OBS, then say "clip". Clips go to `~/Videos/clips/` and a desktop notification shows each one.

## Options

| Flag | Default | What it does |
|---|---|---|
| `--default N` | 30 | Seconds for a plain "clip" (5–60) |
| `--out DIR` | `~/Videos/clips` | Where clips are saved |
| `--precise` | off | Re-encode so clips start exactly N s back (slower) |
| `--mic NAME_OR_INDEX` | system default | Input device; see `--list-mics` |
| `--port N` | 4455 | OBS WebSocket port |
| `--cooldown S` | 3 | Ignore repeated "clip" for S seconds |
| `--flush-wait S` | 2 | Wait for OBS to write the latest video before cutting |
