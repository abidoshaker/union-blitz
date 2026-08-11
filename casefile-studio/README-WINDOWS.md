# CaseFile Studio — Windows setup

Start here if you have never done this before. You do **not** need to be an
administrator, and you do not need to install anything by hand.

## Install

1. Download or clone this repository.
2. Open the `casefile-studio` folder.
3. Double-click **`install-deps.bat`**.
4. Wait. First run takes 10–25 minutes, mostly downloads.

If Windows shows a blue "Windows protected your PC" box, click **More info →
Run anyway**. That appears for any unsigned script.

When it finishes it prints a summary and an environment report. Anything marked
`[FAIL]` needs fixing before the app can render video; `[warn]` items are
optional.

## What it installs

| | |
|---|---|
| Python 3.12 | the backend runs on it |
| FFmpeg with libx264 + libass | does all the video work and burns the subtitles |
| Node.js LTS | only needed to develop the frontend, skip with `--no-node` |
| `.venv` | a private Python environment inside this folder, so nothing on your PC is disturbed |
| Kokoro-82M voice model | ~340 MB, the free offline narrator |
| faster-whisper | works out subtitle timing |

Nothing is installed system-wide except Python, FFmpeg, and Node — and each of
those is skipped if you already have it.

## Options

```
install-deps.bat                    normal install
install-deps.bat --minimal          smallest install, cloud voices only
install-deps.bat --with-align-full  tighter subtitle timing, adds a 2.5 GB download
install-deps.bat --with-optional    adds the Fish / Anthropic / OpenAI SDKs
install-deps.bat --no-node          backend only
install-deps.bat --all              everything
```

Re-running is always safe. It skips whatever is already done.

## Run

Double-click **`run.bat`**. It starts the app and opens
<http://localhost:8760>. Leave the black window open while you work — closing it
stops the app.

## If something goes wrong

- Double-click **`doctor.bat`**. It says exactly what is missing.
- Read `install-log.txt` in this folder — every command's real output is there.
- If Python or FFmpeg was installed but the script still cannot see it, close
  the window, open a new one, and run `install-deps.bat` again. Windows only
  shows newly installed programs to new windows.

## About the hour-long scripts

This build is set up for full-length videos, roughly 9,000 words / 60 minutes.
Two things worth knowing before your first one:

- **Narration is cheap, rendering is not.** An hour of Fish Audio narration is
  about **$0.78**. Rendering that same hour to 1080p takes roughly **1–3 hours**
  on a typical 8-core CPU. `doctor.bat` estimates it for your actual machine.
- **Always render one chapter first.** The app has a preview render that does a
  single chapter. Check the voice, the subtitle style, and the pacing there
  before you commit to the full hour.

Renders resume. If you stop one, or your PC restarts, it picks up from the last
finished scene rather than starting over.

## API keys you will want

| Service | What for | Cost |
|---|---|---|
| **Fish Audio** (fish.audio) | the narrator voice, including your cloned voice | $15 per 1M characters ≈ $0.78/hour of video |
| Anthropic or OpenAI | splitting the script into scenes | a few cents per script |
| Pexels / Pixabay | free stock photos | free |
| fal.ai or OpenAI images | optional AI B-roll | ~$0.03 per image |

Kokoro and Edge-TTS need no key at all, and are the right way to hear a full
draft before spending anything.

Enter keys in the app's Setup Wizard, not in a file. They are encrypted and
stored in the Windows Credential Manager.
