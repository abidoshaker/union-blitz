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

## Making your first video

0. **Need a script?** `docs/SCRIPT_PROMPT.md` has a ready-made prompt for
   Claude that produces text shaped for this pipeline — right length, no stage
   directions, and worded so the app can find matching footage.
1. **Projects → New project.** One project per video.
2. **Script tab.** Paste the whole script and press *Split into scenes*. Your
   text is never rewritten — scenes are cut out of it by position, so what gets
   narrated is exactly what you wrote.
3. **Storyboard tab.** *Narrate all*, then *Source all images*. Scenes about a
   real named person are flagged with red tape; those get archival photos, and
   AI generation on them is blocked.
4. **Reorder anything that reads out of sequence.** Drag the ⠿ handle on a
   card, use the ▲▼ arrows for one step, or type a new position into the little
   number box and press Enter — that last one is the quick way to send a scene
   from position 12 to position 190. Tick several scenes first and dragging one
   of them moves the whole group together.
5. **Sourcing panel** (top of the Storyboard tab). Turn on *Mix in video
   clips* if you want motion as well as stills, pick where clips come from, and
   choose what happens when a clip has its own sound. The useful one is
   **"Let it speak — pause the voiceover"**: the narration stops for that scene
   so the two never talk over each other.
6. **Render tab.** Press **Preview render · first chapter** first. Check the
   voice, the captions and the pacing on a few minutes before spending hours on
   the whole thing.
7. When it looks right, **Render the full video**. You get an MP4, an `.srt`,
   a chapters file to paste into your YouTube description, and suggested
   mid-roll ad positions.

You can close the app or restart your PC during a render. It resumes from the
last finished scene.

### Try it with no API keys at all

A new project defaults to the built-in splitter, a silent draft voice, and
placeholder images. That produces a real, correctly timed video with captions
and chapters, so you can see the whole pipeline work before signing up for
anything. Swap in Fish Audio and real images in Settings when you are ready.

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
  about **$0.78** and takes a few minutes. Rendering that same hour to 1080p is
  the slow part. Measured on photographic stills at 1080p30, preset veryfast:

  | Video length | 4 cores | 8 cores |
  |---|---|---|
  | 15 minutes | ~30 min | ~15 min |
  | 30 minutes | ~1 h | ~30 min |
  | 60 minutes | ~2 h | ~1 h |

  `doctor.bat` works this out for your actual core count. Video-backed scenes
  cost more than stills, so treat these as the lower end if you turn clips on.
- **Always render one chapter first.** The app has a preview render that does a
  single chapter. Check the voice, the subtitle style, and the pacing there
  before you commit to the full hour.

Renders resume, and this is tested rather than hoped for: a 51-scene render was
killed outright at 46%, the app restarted, and it reused all 23 finished clips
and completed the rest. If you stop a render, close the app, or your PC
restarts, it picks up from the last finished scene.

## About face blurring

The app can blur faces automatically — on scenes flagged as showing a real
person, or on everything. It works on both photographs and video.

**Treat it as an assist, not a guarantee.** It finds most clear, front-on
faces. It misses faces turned away, in shadow, small in frame, motion-blurred,
or in a crowd. If a face gets through and you publish it, that is your problem
and not the software's — so watch the preview render before you upload, and
blur anything it missed by hand.

## API keys you will want

| Service | What for | Cost |
|---|---|---|
| **Fish Audio** (fish.audio) | the narrator voice, including your cloned voice | $15 per 1M characters ≈ $0.78/hour of video |
| Anthropic or OpenAI | splitting the script into scenes | a few cents per script |
| Pexels / Pixabay | free stock photos **and video clips** | free |
| Internet Archive | archival footage, no key needed | free |
| Library of Congress | US archival photographs, no key needed | free |
| US National Archives | federal records and photographs (DEA, FBI, customs) | free key from catalog.archives.gov |
| fal.ai or OpenAI images | optional AI B-roll | ~$0.03 per image |

## Where the pictures come from, and how to tell

Every scene carries a badge saying what kind of picture it got:

* **Matched subject** — it came from an archive on a search that named the
  actual place, person or year in the line. This is as close as the app gets
  to a photograph of the event.
* **Atmosphere** — a stock library picture chosen for mood. Right feeling,
  wrong specifics.
* **Generic B-roll** — a fallback. Nothing in the line was searchable, so it
  took something neutral.

Hover the badge to see the exact words that found the picture. The
**Generic B-roll** filter above the storyboard collects everything that needs
a human eye.

Scenes that name a real place, person or year are sent to the archives first —
National Archives, Library of Congress, Wikimedia, Internet Archive, in that
order — and only fall through to stock libraries if none of them has anything.

**A warning worth repeating:** the famous press photographs of most arrests
are owned by AP, Reuters or Getty, and this app has no scraper. If a free
archival copy does not exist, no amount of searching will produce one. The
honest options are then: buy the picture, use atmosphere and say so, or use
the **Change picture** tools below.

## Replacing a picture you are not happy with

Open any scene and press **Change picture**. Four ways in:

* **Search libraries** — the archives are marked with a green dot; those are
  the ones that may hold the real thing. The rest are mood.
* **Make with AI** — a dramatisation, not a record. Turn on the disclaimer
  overlay for scenes that use one. This is **blocked on scenes flagged as
  showing a real person**, in the server, so it cannot be worked around from
  the interface.
* **From a link** — paste the address of an image you have already licensed
  or downloaded. The app records it as supplied by you; checking you have the
  right to publish it is on you.
* **Upload mine** — the same, from a file on your machine.

Anything you choose by hand is marked **Matched subject**, because you chose
it. Changing a picture throws away that scene's preview so the next **Watch**
shows the new one.

Kokoro and Edge-TTS need no key at all, and are the right way to hear a full
draft before spending anything.

Enter keys in the app's Setup Wizard, not in a file. They are encrypted and
stored in the Windows Credential Manager.
