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

### The installer window used to close itself around step 4

Fixed. If you saw this, pull the latest and re-run — nothing needs undoing,
the script is safe to run again over a half-finished install.

What happened: after each install the script re-read the saved PATH out of the
registry and stuck it on the front of the PATH it already had. Since the PATH
it already had was that same list, every one of those calls added the whole
thing again. Three calls — Python, FFmpeg, Node — turn a 2,100-character PATH
into 8,400, and cmd cannot hold a variable longer than 8,191. At that point the
script dies, and a window opened by double-clicking dies with it, which is why
there was nothing to read.

Anyone whose Windows PATH is under about 2,000 characters never saw it. Over
that, it always failed, always at step 4.

Three changes:

* PATH is now merged and de-duplicated instead of prepended, so calling it ten
  times gives the same PATH as calling it once. It also refuses a result over
  7,000 characters rather than trying and dying.
* The installer runs its real work in a child window and **holds the console
  open no matter how that child ends**. If anything else ever kills it
  mid-way, you will get to read the screen and the exit code.
* Every step records the current PATH length in `install-log.txt`, and warns
  on screen past 7,500 characters — so a PATH problem announces itself instead
  of ending the install.

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

## Choosing the narrator

Open a project, go to **Storyboard**, and the **Narration** panel is at the
top. Pick a provider, then a voice, then press **▶ Hear it** — the audition
uses a line from your own script, through the same rewrite and trim the real
narration gets, so what you hear is what you get.

| Provider | Voices | Publish on a monetised channel? |
|---|---|---|
| **Fish Audio** | your cloned voices first, then their public models | Yes — the cloud API is licensed for it |
| **Kokoro-82M** | 8 curated, ~54 in total, all offline | Yes — Apache-2.0 |
| **Edge TTS** | 6 curated | **No** — Microsoft's terms forbid it. Drafting only |

Each voice carries its licence as a badge, because on a monetised channel that
is the fact the choice actually turns on. Fish can **clone your own voice** —
ten to fifteen seconds of clean speech, no music, no room echo.

One scene can have a voice of its own: **♪ Change voice** on any scene card.
Useful for a courtroom quote or a wiretap transcript. Only that scene changes.

## Making it sound less mechanical

Each scene is recorded as a separate take, which is what lets an hour resume
after a crash — but it is also what makes chunked narration sound like a list
being read. Three things in the Narration panel fix that:

* **Numbers and initials — say them aloud.** A true-crime script is full of
  the things engines read badly. On, "In 1991 the DEA seized 4,500 kg worth
  $2.5m at 3:15 a.m." is spoken as *"in nineteen ninety-one the D-E-A seized
  four thousand five hundred kilograms worth two point five million dollars at
  three fifteen a m"*. Off, you get "one thousand nine hundred ninety-one" and
  "two point five em". Your captions still read 1991 — only the voice's copy
  is rewritten.
* **Pauses between scenes.** Rather than an identical gap 200 times, the rest
  follows the punctuation: short after a comma, longer after a full stop,
  longer still after a question, and nearly a second where a chapter turns
  over. Set the overall length from Tight to Slow and heavy.
* **Pace.** 0.85× unhurried to 1.15× urgent, applied by the voice itself
  rather than by stretching the audio afterwards.

Takes are also trimmed of the padding providers leave on each end, so the
pause you asked for is the pause you get.

## Output settings

**Render → Show settings** reaches everything the encoder already honoured:
frame size (including 1080×1920 for Shorts), frame rate, encoder — including
NVENC and QuickSync — quality preset, caption style and word highlighting,
caption timing method, Ken Burns, crossfade length, loudness target, a music
bed, the AI disclaimer overlay, and your chapter titles.

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

## Working on 200 scenes without scrolling

* **Find a scene by its words.** The box above the storyboard searches the
  narration, the image brief and the terms that found each picture — on the
  server, so it reaches every scene and not just the 60 on screen.
* **Filters and search stack.** "Generic B-roll" plus a search for a name
  gives you the scenes about that person that got filler.
* **Select, then act on just those.** Tick some scenes and the batch bar gains
  **Pictures for these** and **Voice for these** — the whole point of
  selecting five scenes is usually that those five need something different
  from the rest.
  * *Pictures* lets you pick which libraries to try for this batch only, and
    whether to replace what is there or just fill in the empty ones. Filling
    in never discards a picture you chose by hand.
  * *Voice* re-records only those scenes, in a provider and voice you choose.
    Pace and pauses stay the project's, so a retake still sits inside the rest
    of the narration.

## Choosing where pictures come from

Sourcing is no longer one library at a time. In **Storyboard → Sourcing**,
tick as many as you like — Pexels, Pixabay, Wikimedia, Internet Archive,
Library of Congress, the National Archives — or press **use everything
available**. The green dot marks an archive.

Each scene walks the list until something comes back, and the **starting point
rotates scene by scene**, so a hundred consecutive frames do not all come from
whichever one happened to be first. Generators — AI images and the offline
placeholder card — are always tried last, because they can never fail and
would otherwise stop the search before an archive was ever asked.

Clips work the same way when motion footage is switched on.

## Deleting a project

Two places, because when a video is finished you are inside it, not on the
projects list: **Render → Delete this project**, or the Delete button on the
project card. Either one stops anything still running first, then removes the
script, the scenes, every finished video and everything downloaded.

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
