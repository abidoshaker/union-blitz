# CaseFile Studio — Build Spec (long-form edition)

> **Status: implemented.** This is no longer only a plan. The backend, the
> pipeline and the web interface are in this repository and a full
> paste-script-to-finished-MP4 run is covered by `backend/tests/test_pipeline.py`,
> which renders a real video offline with no API keys. Phases 1–3 of §14 are
> built; §14 Phase 4 (GPU encoders, extra providers) is not.
>
> Two places where the implementation deliberately departs from this spec are
> flagged in §15.2 and §7 — both changes exist because the original approach
> does not survive at hour-long scale.

This supersedes `docs/original-research-spec.md`, which is kept verbatim for
provenance. Three things changed:

1. **A Windows dependency installer exists** — `install-deps.bat`, plus
   `run.bat`, `doctor.bat`, and the `requirements/` tiers. The original spec
   assumed Docker; that is a bad fit for a beginner on Windows who wants one
   double-click. Docker stays as an optional path, not the primary one.
2. **The target is hour-long scripts, not 20-minute ones.** ~9,000 words,
   200–300 scenes, a 60-minute 1080p render. That is a 3× change in script
   length but a much larger change in architecture — see §15. Several parts of
   the original render pipeline do not survive at this length and are replaced.
3. **"fishai" is Fish Audio** (fish.audio), confirmed as the default TTS. See
   §0 for the naming, package, and licensing detail.

---

## 0. Fish Audio — naming and the one licensing rule that matters

"fishai" / "Fish AI" / "FishAudio" all refer to **Fish Audio**, https://fish.audio.
Relevant identifiers, because these get mixed up constantly:

| Thing | Value |
|---|---|
| Cloud API base | `https://api.fish.audio` |
| Default model header | `s2.1-pro` (free tier: `s2.1-pro-free`) |
| Open-source repo | `fishaudio/fish-speech` (the local weights) |
| Python SDK repo | `fishaudio/fish-audio-python` |
| Python SDK **PyPI package** | `fish-audio-sdk` ← install this, not `fish-audio-python` |
| Pricing | $15 per 1M UTF-8 bytes |

**The rule:** use the **cloud API**. The open-source fish-speech weights are
CC-BY-NC-SA-4.0 — non-commercial only — which makes them unusable for a
monetised channel, and they are far slower than real time on CPU regardless.
The cloud API is commercially licensed and fast.

**Implementation requirement:** the Fish adapter talks to the REST API with
`httpx` as its primary path. The `fish-audio-sdk` package is an optional
convenience only, and the app must run fully without it. This is deliberate —
the installer treats that package as non-fatal precisely because the adapter
does not depend on it.

Keep the `fish_mode = 'cloud' | 'local'` toggle, and when set to `local` show
the red **"Non-commercial (CC-BY-NC-SA) — testing only, very slow on CPU"**
banner.

### Cost at hour-long scale

~9,000 words ≈ ~52,000 UTF-8 bytes ≈ **$0.78 of Fish narration per
hour-long video.** Even at three videos a week that is under $10/month. Fish
cloud is the right default; Kokoro exists for free drafting passes, not to save
money on finals.

---

## 1. Project overview and goals

Build **CaseFile Studio**, a locally-hosted web app at `http://localhost:8760`
that turns a pasted narration script into a finished 1080p YouTube video with AI
or archival visuals, cloned or selected TTS narration, karaoke subtitles, music,
chapters, and export — plus vertical Shorts cut from the long video.

Core principles: **CPU-first** (GPU optional later via config), **long-form
first** (every stage must survive a 9,000-word script without falling over),
provider-adapter architecture for TTS and images, batch operations everywhere,
encrypted local key storage, idempotent re-renders via content hashing,
resumable jobs, and a colourful beginner-friendly UI.

**Primary target: a 45–75 minute video from a single pasted script.** Anything
that only works at 3,000 words is a bug.

## 2. Tech stack

- **Backend:** Python 3.12, FastAPI, Uvicorn, Pydantic v2, SQLModel/SQLAlchemy 2.x
  + SQLite, `httpx`, WebSocket for progress.
- **Job queue:** SQLite-backed persistent worker (`jobs` table + worker thread
  pool). No Redis. Behind a `JobQueue` interface. **Jobs must checkpoint
  sub-steps** — see §15.7.
- **Media:** FFmpeg system binary (needs libx264 + libass), driven through a thin
  subprocess builder module. Never MoviePy for the encode.
- **TTS:** Fish Audio cloud REST (default); adapters for Kokoro
  (`kokoro-onnx`), Edge-TTS, Piper, ElevenLabs, OpenAI TTS.
- **Caption timing:** `faster-whisper` INT8 with `word_timestamps=True` as the
  default tier; WhisperX wav2vec2 forced alignment as an optional
  higher-accuracy tier. See §15.4 — this is a change from the original spec,
  which made torch mandatory.
- **LLM:** provider-adapter for Anthropic Claude and OpenAI.
- **Frontend:** React 18 + Vite + TypeScript, TailwindCSS, shadcn/ui, TanStack
  Query, Zustand, `dnd-kit`, `wavesurfer.js`, plus **`@tanstack/react-virtual`**
  — a 300-scene storyboard must be virtualised.
- **Packaging:** `install-deps.bat` + `run.bat` as the primary path on Windows
  (§16). `docker compose up` supported as an alternative.

## 3. Data model (SQLite)

Unchanged from the original spec except where noted.

- **Project**: id, title, genre_preset, created_at, updated_at, status,
  settings_json (resolution, fps, default_voice_id, default_visual_source,
  subtitle_style, music_track, loudness_target).
- **Script**: id, project_id, raw_text, word_count, chapters_json,
  **estimated_runtime_sec**, **segmentation_checksum**.
- **Scene**: id, project_id, order_index, text, start_time, end_time, duration,
  image_prompt, visual_source, asset_id, audio_asset_id, status, content_hash,
  ai_disclaimer, depicts_real_person, notes, **chapter_id**,
  **source_char_start**, **source_char_end** (byte offsets back into
  `Script.raw_text`, used to prove the script was not altered — §15.2),
  **clip_path**, **clip_hash**.
- **Chapter** *(new)*: id, project_id, order_index, title, start_time,
  end_time, scene_range. Drives YouTube chapter markers and per-chapter renders.
- **Asset**: unchanged — id, project_id, type, source_provider, source_url,
  local_path, license_str, attribution_str, width, height, duration,
  content_hash, created_at.
- **Voice**: id, provider, external_voice_id (Fish `reference_id`), title,
  is_clone, reference_audio_path, sample_path, tags_json, license_note,
  commercial_ok.
- **Job**: id, project_id, type, params_json, status, progress, message,
  result_json, error_str, timestamps, parent_batch_id, **checkpoint_json**
  (what is already done, so a restart resumes), **eta_sec**.
- **RenderOutput**: id, project_id, variant, local_path, duration, size_bytes,
  created_at, **chapters_txt_path**, **ad_breaks_json**.
- **Setting/Secret**: key, value_encrypted, provider.

## 4. Directory structure

```
casefile-studio/
  install-deps.bat          <- Windows one-click dependency install
  run.bat                   <- start the app
  doctor.bat                <- re-check the environment
  requirements/             <- core / tts-local / align-lite / align-full / optional
  tools/                    <- doctor.py, fetch_models.py
  models/                   <- gitignored: kokoro-v1.0.onnx, voices-v1.0.bin
  docker-compose.yml        <- optional alternative to the .bat path
  pyproject.toml
  backend/
    app/
      main.py               # FastAPI app, static serving, WS
      config.py  db.py  models.py  security.py
      queue/                # JobQueue interface + sqlite_worker.py
      providers/
        tts/                # base.py + fish.py, kokoro.py, piper.py, elevenlabs.py, openai_tts.py, edge.py
        image/              # base.py + fal_flux.py, openai_image.py, imagen.py, ideogram.py,
                            #          pexels.py, pixabay.py, unsplash.py, wikimedia.py, loc.py, internet_archive.py
        llm/                # base.py + claude.py, openai.py
      services/
        segmentation.py     # chunked LLM script -> scenes + image prompts (§15.2)
        tts_service.py      # concurrency-limited batch synthesis (§15.3)
        align_service.py    # per-scene word timestamps (§15.4)
        subtitle_service.py # per-scene ASS with karaoke
        render_service.py   # per-scene clip render + stream-copy concat (§15.5)
        chapter_service.py  # chapters + mid-roll ad break markers (§15.8)
        cost_estimator.py  hashing.py
      routers/              # projects, scripts, scenes, voices, assets, batch, render, settings, jobs
    tests/
  frontend/
    src/  pages/ components/ hooks/ store/ lib/ theme/
  data/                     # gitignored
    projects/<project_id>/{assets,audio,scenes,clips,renders,cache}/
```

## 5. TTS provider-adapter interface

```python
class TTSProvider:
    name: str
    supports_cloning: bool
    is_local: bool
    commercial_ok: bool           # Kokoro True; fish-local-weights False; fish-cloud True
    max_chars_per_request: int    # chunking is the caller's job, not the provider's
    max_concurrency: int          # Fish Starter = 5

    def list_voices(self) -> list[VoiceInfo]: ...
    def clone_voice(self, ref_audio: bytes, ref_text: str | None, title: str) -> str: ...
    def synthesize(self, text: str, voice_id: str, opts: TTSOpts) -> AudioResult: ...
    def estimate_cost(self, text: str) -> float: ...
```

- **FishProvider (default).** `POST /v1/tts` with the `model` header
  (`s2.1-pro`), body `{text, reference_id, format}`. Cloning via `POST /model`
  (multipart: `type=tts`, `title`, `train_mode=fast`, `voices=@sample.wav`)
  returning a `reference_id`; inline `references` supported for instant clones.
  Respect the **Starter limit of 5 concurrent requests** (15 at $100 prepaid,
  50 at $1,000) — see §15.3 for how this is enforced across 250 scenes.
  Cost = $15 / 1M UTF-8 bytes.
- **KokoroProvider.** Local ONNX, Apache-2.0, 54 voices, no cloning, ~2–6×
  real time on CPU. Model files land in `models/` via `tools/fetch_models.py`.
- Others: Edge-TTS (free, no key — the cheapest way to hear a full hour before
  paying), Piper, ElevenLabs, OpenAI TTS. Each declares `commercial_ok` and the
  UI shows it as a badge.

## 6. Image provider-adapter interface

`ImageProvider`: `search(query, opts)` for stock, `generate(prompt, opts)` for
AI, both returning `AssetCandidate(url, thumb, license, attribution, provider,
width, height)`.

- **Stock/archival:** Pexels, Pixabay (cache results per TOS; zero
  indemnification), Unsplash (**must** send attribution and fire the
  download-tracking endpoint), Wikimedia Commons, Library of Congress,
  Internet Archive.
- **AI:** fal.ai Flux (~$0.025 Schnell / ~$0.05 Pro), Replicate Flux, OpenAI
  gpt-image (~$0.005 mini / ~$0.04 std), Google Imagen 4 ($0.02 / $0.04 / $0.06,
  SynthID-watermarked), Ideogram 3 (~$0.03).
- **`real_person_guard` (must implement):** a scene flagged
  `depicts_real_person=true` has AI generation **blocked** in the UI and is
  routed to archival/licensed search, with a right-of-publicity warning. AI
  B-roll can carry an "AI-generated / dramatization" overlay.
- **Long-form addition — image reuse.** 250 scenes × $0.04 is $10 per video,
  which is more than the narration by an order of magnitude. So: an
  **image pool per chapter**. Generate or source N images per chapter
  (default 6–10), assign them across that chapter's scenes with different Ken
  Burns moves and crops so repeats do not read as repeats, and only generate
  scene-unique images where the user marks a scene "hero". This drops a
  hour-long video from ~250 images to ~60–80. Make the pool size a per-project
  setting with the cost shown live.

## 7. Render pipeline — replaced for long-form

The original pipeline concatenated scene clips and then burned subtitles,
overlays, and music into the whole 60-minute timeline in a final pass. At an
hour that final pass is a second full re-encode of the entire video and it is
not resumable. Replaced with **bake-at-the-scene-level, stream-copy at the
end**:

1. **Per-scene TTS** → normalised 48 kHz WAV. Skipped when `content_hash` is
   unchanged.
2. **Global narration track** — sample-accurate concat of the scene WAVs.
   Scene boundaries are known exactly because we generated each piece, so scene
   start/end times come from WAV lengths, **not** from ASR.
3. **Word timings** — per scene, in parallel (§15.4). Never one pass over 60
   minutes of audio.
4. **Per-scene ASS** — karaoke `\k` word highlighting, 4–6 words per line,
   white→yellow, timestamps *relative to the scene start*. Caption lines never
   cross a scene boundary.
5. **Per-scene video clip**, rendered in parallel across cores. One FFmpeg
   invocation per scene produces a finished, self-contained clip:
   `zoompan` Ken Burns → `scale`/`pad` to 1920×1080 → `format=yuv420p` →
   lower-third/name-card/disclaimer overlays → `ass=` subtitle burn-in →
   x264 with **fixed, identical encoder settings and a keyframe on frame 0**
   (`-g <fps*2> -keyint_min <fps*2> -sc_threshold 0 -force_key_frames "expr:eq(n,0)"`).
   Video only, no audio. Cached by `clip_hash`.
   Keep the zoompan pre-upscale **modest (2–4× output width, never 8000px)** —
   it is the CPU bottleneck and it is largely single-threaded.
6. **Crossfades without a second encode.** A scene's clip renders its *own*
   outgoing transition: its filtergraph takes the next scene's still as a second
   input and `xfade`s into it over the last T seconds. The blend is therefore
   already inside the clip, and the final assembly stays a stream copy.
7. **Global audio, once.** Narration + music bed with `sidechaincompress`
   ducking, two-pass `loudnorm` to **-14 LUFS**, encoded to AAC. One pass over
   the hour, cheap because there is no video in it.
8. **Assemble** — FFmpeg concat *demuxer* (not the concat protocol) over a clip
   list file, `-c:v copy`, muxed against the single audio track,
   `-c:a copy -movflags +faststart`. A 60-minute assembly finishes in
   under a minute because nothing is re-encoded.
9. **Chapters and ad breaks** written out (§15.8).
10. **Shorts variant** — 9:16 reframe of selected chapters or user-picked
    ranges, re-encoded from the source stills at 1080×1920 rather than cropped
    from the finished 16:9, so quality holds.

Provide an `encoder` config for future `h264_qsv` / `h264_videotoolbox` /
`h264_nvenc`. Emit progress over WebSocket at every step, with a scene counter.

## 8. API endpoints

Projects: `GET/POST/PATCH/DELETE /projects`.
Scripts: `POST /projects/{id}/script`, `POST /projects/{id}/segment`.
Scenes: `GET/PATCH /scenes` (**paginated — never return 250 scenes in one
payload**), `POST /scenes/{id}/regenerate-image`,
`POST /scenes/{id}/regenerate-audio`, `POST /scenes/reorder`.
Chapters: `GET/PATCH /projects/{id}/chapters`.
Voices: `GET /voices`, `POST /voices/clone`, `POST /voices/{id}/preview`.
Assets: `GET /assets`, `POST /assets/upload`, `POST /assets/search`,
`POST /assets/generate`.
Batch: `POST /batch/{op}` where op ∈ {regenerate-audio, swap-images,
apply-settings, re-render, set-visual-source, find-replace}.
Render: `POST /projects/{id}/render`, `POST /projects/{id}/render/preview`
(**renders one chapter only** — the single most important long-form endpoint),
`GET /renders`.
Jobs: `GET /jobs`, `GET /jobs/{id}`, `POST /jobs/{id}/cancel`,
`POST /jobs/{id}/resume`, `WS /ws/jobs`.
Settings: `GET/PUT /settings`, `POST /settings/keys`,
`POST /settings/keys/{provider}/test`.

## 9. UI spec

**Palette:** dark base `#0F1117`, surface `#1A1D29`, primary accent gradient
electric magenta `#FF2D75` → violet `#7C3AED`, secondary amber `#FFB020`,
info cyan `#22D3EE`, success `#22C55E`, danger `#EF4444`. Dark surfaces, vivid
gradient accents, subtle red evidence-tape motifs.
**Type:** headings "Space Grotesk", body "Inter", timecodes "JetBrains Mono".
Rounded-2xl cards, soft glow shadows, generous spacing.

Screens: Dashboard/Projects · First-run Setup Wizard (paste keys, "Test" button
per key) · New Project Wizard · Script Editor · Scene Storyboard · Voice Studio ·
Asset Library · Batch Operations · Render Queue · Settings/API Keys · Export.

**Long-form UI changes:**
- **Storyboard is virtualised and grouped by chapter**, collapsed by default. A
  flat 250-card grid is unusable and slow. Show a chapter strip at the top with
  duration per chapter; clicking scrolls.
- **Filter bar** on the storyboard: "needs image", "no audio yet", "flagged as
  real person", "changed since last render". At this length the user navigates
  by filter, not by scrolling.
- **Render Queue shows scene-level progress** — "scene 138/247, ~41 min left" —
  and a per-step breakdown, not one bar for an hour.
- **Every long job has a Pause and a Resume.** Rendering an hour is something
  you interrupt to use your computer.
- Cost estimator runs before any paid batch and is shown per-chapter as well as
  per-project.

## 10. Batch operations

Multi-select scenes (or select-all, or **select-chapter**) → one parent Job with
child Jobs, live progress, idempotent (unchanged content hashes are skipped).
Destructive batches show a diff/preview and require confirmation. Operations:
regenerate audio, swap images, apply settings, find-and-replace across scenes,
bulk regenerate. At hour-long scale, "select all" can mean 250 paid API calls —
the confirm dialog must show the count and the dollar figure in the button
itself, e.g. **"Regenerate 247 scenes — $0.81"**.

## 11. Settings / API keys

Keys encrypted at rest with a Fernet key held in the OS keyring (Windows
Credential Manager), falling back to a local key file with restrictive
permissions, never committed. `.env` supported for power users; the encrypted DB
store is primary. Never log keys. "Test key" per provider. Show only the last 4
characters after save.

## 12. Error handling and CPU performance

- All long tasks run in the queue, survive a restart, and are cancelable **and
  resumable**.
- Retry transient API errors with exponential backoff. Surface Fish
  concurrency/rate-limit responses as "waiting, will retry" rather than an
  error.
- Cap parallel scene renders at CPU core count (leave one core free by default
  so the machine stays usable during an hour-long render — make it a setting).
- Detect at startup whether FFmpeg has libass and libx264 and warn if missing;
  `tools/doctor.py` already does this and the backend should reuse it.
- Preflight disk space before a render and refuse to start if it cannot fit the
  estimate (§15.6).

## 13. Acceptance criteria

- Paste a **9,000-word** script → auto-split into scenes with per-scene image
  prompts, **and the concatenated scene text is byte-identical to the input
  script** after whitespace normalisation.
- Choose or clone a Fish voice → narrate the full hour → word-level captions
  align within ~150 ms (align-full tier) or ~300 ms (default tier).
- Mixed sourcing works: some scenes AI, some stock, some uploaded, chosen
  per-batch or per-chapter.
- Real-person guard blocks AI generation on flagged scenes.
- Render a full 60-minute 16:9 video with Ken Burns, crossfades, karaoke subs,
  ducked music, -14 LUFS, chapters file, and ad-break markers.
- **Kill the app mid-render and restart it: the render resumes from the last
  completed scene**, not from zero.
- Re-render after editing 2 scenes re-processes only those 2 and the one
  preceding each (transitions), then stream-copies the rest.
- Export 16:9 and 9:16; every asset carries license and attribution; no
  plaintext keys anywhere.
- `install-deps.bat` on a clean Windows 11 machine, then `run.bat`, reaches a
  working app with no other manual steps.

## 14. Phased build order

- **Phase 1 (MVP):** Project + Script + chunked LLM scene split; Fish cloud TTS,
  single voice; Pexels + upload images; per-scene clip render + concat; burned
  SRT; SQLite queue with checkpointing and WS progress; encrypted keys;
  Dashboard, Script Editor, virtualised Storyboard, Render Queue.
  **Ship this only once it survives a 9,000-word script end to end.**
- **Phase 2:** Voice Studio (browse + clone Fish); Kokoro local adapter; AI image
  adapters + real-person guard + chapter image pools; per-scene word timing and
  karaoke ASS; music ducking and loudnorm; cost estimator.
- **Phase 3:** Full batch operations with diff preview; hashing/idempotency;
  additional archival adapters; chapters and ad-break export; Shorts 9:16;
  presets.
- **Phase 4:** GPU-optional config; additional TTS providers; onboarding wizard
  polish; error-message pass.

---

## 15. Long-form requirements (the hour-long script path)

Everything here exists because a 9,000-word script behaves differently from a
3,000-word one. Treat this section as binding.

**Reference numbers** for a 60-minute video, used throughout the UI and the
estimator:

| | |
|---|---|
| Words | ~9,000 at ~150 wpm |
| UTF-8 bytes | ~52,000 |
| Fish narration cost | **~$0.78** |
| Scenes at ~15 s | ~240 |
| Images with chapter pooling | ~60–80, not 240 |
| Scene clips on disk | ~4–8 GB before the final mux |
| Final MP4 | ~1.5–2.5 GB at CRF 20 |

### 15.1 Nothing loads the whole project at once
Scene lists are paginated in the API and virtualised in the UI. Audio waveforms
are lazy-loaded per scene. The storyboard renders the visible window only. This
is the difference between a usable app and a browser tab that freezes.

### 15.2 Segmentation is chunked, parallel, and verbatim-checked
A single LLM call cannot reliably emit 240 scenes: the output token budget and
the failure blast radius are both wrong. Instead:

- Split `raw_text` into windows of ~900 words at paragraph boundaries with
  ~100 words of overlap.
- Run windows through the LLM with bounded concurrency (3–5), retrying JSON
  parse failures.
- Stitch: de-duplicate the overlap by matching on the last complete sentence of
  the previous window.
- **Verbatim check (non-negotiable):** concatenate all scene `text` values,
  normalise whitespace, and compare against the source script. The narration
  must be the user's words, not the LLM's paraphrase. On mismatch, re-anchor
  scene boundaries with `difflib` against the source and store
  `source_char_start` / `source_char_end` per scene; if that still fails,
  fall back to a deterministic sentence splitter and mark the project
  "prompts need review". Never ship a paraphrase to TTS.
- Target 20–50 words per scene (8–20 s). Merge runt scenes, split overlong ones.
- Cap at ~400 scenes; beyond that, widen the target duration automatically.
- Detect chapters every ~5–8 minutes and write the `Chapter` rows.

### 15.3 TTS is a bounded-concurrency queue, not a loop
240 scenes against Fish's 5-concurrent Starter limit. Implement a semaphore
sized from `TTSProvider.max_concurrency`, with:
- exponential backoff plus jitter on 429/5xx, surfaced as "waiting, will retry";
- per-scene caching by content hash so a retry never re-bills a finished scene;
- a running cost counter in the UI as the batch proceeds;
- a hard per-batch spend ceiling the user sets, which pauses the job rather than
  blowing past it.
Wall clock for a full hour of narration this way: a few minutes.

### 15.4 Caption timing runs per scene, never over the whole hour
Running ASR over 60 minutes of audio on CPU is slow and invites Whisper
hallucination and drift. Since the app generated each scene's audio from known
text, the problem is forced alignment of a short clip, not transcription:

- **Default tier (`align-lite`, no torch):** `faster-whisper` small INT8 with
  `word_timestamps=True`, run **per scene, in parallel across cores**, with the
  scene's known text supplied as `initial_prompt`. Roughly 100–300 ms accuracy.
  ~250 MB of dependencies.
- **Optional tier (`align-full`):** WhisperX wav2vec2 forced alignment against
  the known transcript, per scene, chunked into ≤30 s windows. Sub-100 ms.
  ~2.5 GB of dependencies — hence a separate opt-in tier and
  `install-deps.bat --with-align-full`.
- **Fallback with zero dependencies:** proportional timing by character count
  within the scene. Good enough for a draft preview; never for a final.

Absolute timestamps come from the scene's known offset in the narration track
plus the within-scene word time. Drift cannot accumulate across the hour because
each scene is anchored independently.

### 15.5 Assembly is a stream copy
See §7.5–§7.8. The rule: **anything burned into the picture is burned at the
scene level.** The final concat re-encodes nothing. Consequences to honour:
- every scene clip uses byte-identical encoder settings;
- every clip starts on a keyframe;
- use the concat demuxer with a list file, not the concat protocol, and not a
  240-input filtergraph — that would exhaust memory;
- editing scene 138 re-renders scene 138 and 137 (137 owns the transition into
  138), and nothing else.

### 15.6 Disk and preflight
Estimate `scenes × avg_clip_bytes + final_mp4 + audio` before starting and
refuse to begin a render with less than 1.5× that free. Show the number. Garbage
collect the clip cache on a policy (keep the last 2 renders per project). An
hour-long project that renders weekly will otherwise fill a drive within a
month.

### 15.7 Every long job checkpoints
`Job.checkpoint_json` records completed units (scene ids, clip hashes). On
startup the worker picks up `running` jobs left behind by a crash and resumes
from the checkpoint. Pause/Resume is a first-class control, not a cancel. ETA is
computed from a rolling average of completed units, not a fixed guess.

### 15.8 Chapters, ad breaks, and the preview render
- Write `chapters.txt` in YouTube's description format (`00:00 Title`), with the
  first chapter forced to `00:00`.
- Suggest **mid-roll ad break points** at scene boundaries roughly every 8–10
  minutes, avoiding breaks inside a sentence or mid-chapter, exported as
  `ad_breaks.json`. On an hour-long video mid-rolls are most of the revenue.
- Optionally write chapter markers into the MP4 via an ffmetadata file.
- **`POST /projects/{id}/render/preview` renders one chapter.** Nobody should
  discover a wrong voice or a broken subtitle style after a three-hour render.
  Make this prominent in the UI and default the first render of any project to
  it.

### 15.9 Realistic expectations to show the user
On a modern 8-core CPU with `-preset veryfast`, a 60-minute 1080p Ken Burns
video lands roughly in the **1–3 hours** range to render, dominated by
`zoompan`. `tools/doctor.py` prints an estimate scaled to the actual core count
of the machine; show the same number in the UI before a render starts, and
never present it as precise.

---

## 16. Windows dependency bootstrap

Already implemented in this repo — the coding agent should keep and extend
these, not replace them with a Docker-only path.

| File | Purpose |
|---|---|
| `install-deps.bat` | Installs Python 3.12, FFmpeg (libx264 + libass verified), Node LTS, the venv, the Python tiers, and the Kokoro models. Idempotent, no admin needed. |
| `run.bat` | Starts Uvicorn on :8760 and opens a browser. |
| `doctor.bat` → `tools/doctor.py` | Re-checks the environment and prints hour-long time/disk estimates for this machine. |
| `tools/fetch_models.py` | Resumable download of the Kokoro ONNX model and voice pack; warms the faster-whisper cache. |
| `requirements/*.txt` | Tiered so a beginner is not forced into a 2.5 GB torch download to get a working app. |

Design rules the installer follows and that must be preserved:

- **winget first, direct download as fallback.** Not everyone has winget.
- **FFmpeg is verified, not assumed.** Many Windows FFmpeg builds lack libass;
  the installer checks `-buildconf` and swaps in a full GPL build if needed.
- **Optional tiers fail soft.** A missing wheel for one provider degrades that
  provider, never the install. The installer retries a failed tier package by
  package and reports what it skipped.
- **No `setx PATH`.** The FFmpeg location is recorded in `.env.local` and
  prepended by `run.bat`. Rewriting the system PATH from a batch file truncates
  it at 1024 characters and is a classic way to break a machine.
- **Re-runnable.** Every step checks before it acts.
