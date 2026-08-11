# Build Spec + Prompt: Local Browser-Based "Script-to-Video" Pipeline for a True-Crime YouTube Channel

## TL;DR
- **Default your TTS to the Fish Audio *cloud* API, not local fish-speech.** The open-source Fish-Speech / OpenAudio S1-mini weights are **CC-BY-NC-SA-4.0 (non-commercial only)** — a license violation for a monetized channel — and are far slower than real-time on CPU. The cloud API is commercially licensed at **$15 per 1M UTF-8 bytes (~180,000 English words / ~12 hours)**, so a 20-minute video costs roughly $0.25. Add **Kokoro-82M (Apache-2.0)** as a free, CPU-fast, commercially-safe local fallback.
- **Use archival/licensed photos for real named people and AI generation only for atmospheric/dramatization B-roll.** Most AI image APIs (OpenAI, Sora) prohibit realistic likenesses of real people without consent, and AI-depicting real crime figures creates defamation/right-of-publicity risk. Keep visuals non-graphic to stay monetizable under YouTube's documentary-context rules.
- The full spec below is a copy-pasteable build prompt for an AI coding agent: FastAPI + React/Vite/Tailwind/shadcn, provider-adapter architecture for TTS and images, FFmpeg CPU render pipeline (Ken Burns + xfade + karaoke subs + loudnorm to -14 LUFS), a SQLite-backed job queue with live progress, encrypted key storage, content-hash idempotency, batch operations, and a phased MVP-first build order.

## Key Findings

**Fish Audio licensing is the single most important design constraint.** The fish-speech GitHub README states: *"This codebase is released under Apache License and all model weights are released under CC-BY-NC-SA-4.0 License,"* and the Hugging Face model gate requires agreeing *"I agree to use this model for non-commercial use ONLY."* Fish staff confirmed in GitHub discussions that only those who can train the model from scratch may use it commercially, and that generated outputs from the open weights fall under the non-commercial restriction. For a monetized YouTube channel, the **cloud API is the compliant path** (pay-as-you-go, no monthly minimum; a free `s2.1-pro-free` tier exists subject to fair-use). Fish also requires proof of rights for commercial use of cloned voices.

**Fish-speech on CPU is officially supported but not production-viable.** The repo offers a CPU install (`pip install -e .[cpu]`), but every *published* real-time-factor number is on GPU (≈1:5 on an RTX 4060 mobile, ≈0.2 RTF on A100/H200). A GitHub issue (#897) from a user on a 32-core EPYC reports CPU inference is *"extremely long"* for anything beyond a few words, with cores only 4–8% utilized [GitHub](https://github.com/fishaudio/fish-speech/issues/897) — the autoregressive stage doesn't parallelize across CPU cores. No usable CPU RTF is published by anyone. Verdict: on CPU, get the Fish "sound" from the **cloud API**, and use **Kokoro-82M** for local narration.

**Kokoro-82M is the recommended local fallback.** Apache-2.0 (genuinely commercial-safe), 82M params, 54 fixed voices, ONNX runtime. VisionStory's hands-on review (2026) ran it *"on an Apple M3 Pro on CPU only, with no GPU. A 22-second narration rendered in about 3.5 seconds, roughly six times faster than real time, using the kokoro-onnx runtime."* (An independent benchmark reports a more conservative RTF ~0.47–0.51, i.e. ~2× real-time, for the PyTorch pipeline — either way, comfortably faster than real-time on CPU.) Trade-off: **Kokoro cannot clone voices.** So "clone my own voice" = Fish cloud API (zero-shot from a ~10–15s reference clip → reusable `voice_id`); local fine-tuning/LoRA is not CPU-feasible.

**TTS adapter recommendations by license + CPU-viability:** Kokoro-82M (Apache-2.0, CPU-fast, no cloning) — local default; Piper (MIT/GPL fork, CPU real-time, robotic) — ultra-light; Chatterbox (MIT, cloning, wants GPU) — commercially-safe cloning if a GPU appears; ElevenLabs / OpenAI TTS — cloud, commercial, paid. **Avoid for commercial use:** XTTS-v2 (Coqui CPML non-commercial; company defunct) and F5-TTS (CC-BY-NC).

**Image sourcing split:** For real people, default to stock/archival APIs — **Pexels** and **Pixabay** (free, commercial, no attribution; Pixabay requires result caching and offers zero indemnification), **Unsplash** (free, but the API TOS *mandates* attribution + firing the download-tracking endpoint), **Wikimedia Commons**, **Library of Congress** "Free to Use" sets, and **Internet Archive**. Note none of these guarantee model releases, so recognizable-person photos still carry risk. For AI B-roll: **Flux via fal.ai** (~$0.025 Schnell / ~$0.05 Pro per image) or Replicate; **OpenAI gpt-image** (mini ~$0.005, standard ~$0.04); **Google Imagen 4** — model IDs `imagen-4.0-fast-generate-001` ($0.02), `imagen-4.0-generate-001` ($0.04), `imagen-4.0-ultra-generate-001` ($0.06), each output carrying a SynthID watermark; **Ideogram 3** (~$0.03, best in-image text). **Midjourney has no official public API.**

**Legal/policy cautions for true crime:** (1) YouTube's advertiser-friendly guidelines permit monetization of crime/violence in documentary/educational context but demonetize gratuitous gore, "dead bodies without context," and "implied moment of death." YouTube's monetization policy lead has said the platform wants *"controversial issues discussed in a non-descriptive and non-graphic way [to not be] disincentivized through demonetization,"* and now allows dramatized/non-graphic treatment — so **keep it non-graphic.** (2) OpenAI's Usage Policies prohibit *"use of someone's likeness, including their photorealistic image or voice, without their consent,"* and after the Sora backlash *"any person or character must now opt in before their likeness can be used."* (3) The tool should therefore **default to archival/licensed photos for real people**, restrict AI to atmospheric/recreation B-roll, and offer a visible "AI-generated / dramatization" disclaimer overlay.

**Alignment & subtitles on CPU:** faster-whisper (CTranslate2, INT8) for transcription + WhisperX (wav2vec2 forced alignment) for sub-100ms word timestamps → karaoke ASS captions (`\k` tags, 4–6 words/line, white→yellow). whisper.cpp is the most memory-efficient pure-CPU option. Use `small`/`base` models on CPU. Burn in with FFmpeg `-vf ass=` (requires libass).

**Video assembly:** FFmpeg does the heavy lifting (zoompan Ken Burns, xfade, loudnorm to -14 LUFS, sidechaincompress ducking). MoviePy is convenient but slow/memory-heavy (v2 reportedly up to ~10× slower than v1 on some slideshow jobs) — render with direct FFmpeg subprocess. The dominant CPU cost is zoompan's large `scale` pre-upscale (largely single-threaded), *not* x264. **Render each scene clip in parallel across cores, then concat** (stream-copy for cuts, xfade for transitions); this parallelizes the bottleneck and avoids the memory blow-up of one giant filtergraph. Expect a 15–25 min 1080p Ken Burns video to take on the order of the video's length up to several times longer on a modern multicore CPU — benchmark on the target machine. Presets: ultrafast ≈ 2.5–3× faster than medium but bigger files; **veryfast is the sweet spot.** Architect for optional QSV/VideoToolbox/NVENC later.

The X/Twitter reference post could not be fetched (X blocks automated access); it is treated as generic crime-boss/mafia/cartel biographical narration content, and no claims about its specific contents are made.

---

## THE BUILD PROMPT (copy everything below into your AI coding agent)

> **You are building a local, browser-based "script-to-video" production tool for a solo creator who runs a true-crime YouTube channel (crime-boss / mafia / cartel biographies). The creator writes the script; the tool automates everything else: scene splitting, voiceover, image sourcing, timing, subtitles, assembly, rendering, and export. Build it incrementally in the phases at the end. The user is a complete beginner, runs CPU-only (no NVIDIA GPU) for now, and the app must be very colorful and easy to use.**

### 1. Project overview and goals
Build **"CaseFile Studio"**, a locally-hosted web app (accessed at `http://localhost:8760`) that turns a pasted narration script into a finished 1080p YouTube video with AI or archival visuals, cloned/selected TTS narration, karaoke subtitles, music, and export — plus vertical Shorts variants. Core principles: CPU-first (GPU-optional later via config), provider-adapter architecture for TTS and images, batch operations everywhere, secure local key storage, idempotent re-renders via content hashing, and a beginner-friendly colorful UI.

### 2. Tech stack (explicit versions)
- **Backend:** Python 3.12, FastAPI (latest 0.11x), Uvicorn, Pydantic v2, SQLModel or SQLAlchemy 2.x + SQLite, `httpx` for API calls, `websockets`/SSE for progress.
- **Job queue:** custom SQLite-backed persistent worker (a `jobs` table + a background worker process/thread pool). Do NOT require Redis. Abstract it behind a `JobQueue` interface so Celery/RQ can be dropped in later.
- **Media:** FFmpeg (system binary, must include libx264 + libass), invoked via `subprocess` with a thin builder module. Use MoviePy only for orchestration if at all, never for the main encode.
- **TTS:** Fish Audio cloud API via `fish-audio-python` SDK (default); adapters for Kokoro (`kokoro`/`kokoro-onnx`), Piper, ElevenLabs, OpenAI TTS, Edge-TTS.
- **ASR/alignment:** `faster-whisper` (INT8) + `whisperx` for word timestamps; `whisper.cpp` optional.
- **LLM (scene splitting/prompts):** provider-adapter for Anthropic Claude and OpenAI.
- **Frontend:** React 18 + Vite + TypeScript, TailwindCSS 3.x, shadcn/ui, TanStack Query, Zustand for state, `dnd-kit` for drag-reorder, `wavesurfer.js` for audio preview.
- **Packaging:** `docker compose up` as primary; also provide a `uv`-based one-command launcher (`uv run casefile`) for non-Docker users. Frontend served as a static build by FastAPI in production.

### 3. Data model (SQLite)
- **Project**: id, title, genre_preset, created_at, updated_at, status, settings_json (resolution, fps, default_voice_id, default_visual_source, subtitle_style, music_track, loudness_target).
- **Script**: id, project_id, raw_text, word_count, chapters_json.
- **Scene**: id, project_id, order_index, text, start_time, end_time, duration, image_prompt, visual_source ('ai'|'stock'|'upload'), asset_id, audio_asset_id, status, content_hash, ai_disclaimer (bool), depicts_real_person (bool), notes.
- **Asset**: id, project_id, type ('image'|'audio'|'video'|'music'), source_provider, source_url, local_path, license_str, attribution_str, width, height, duration, content_hash, created_at.
- **Voice**: id, provider, external_voice_id (Fish reference_id), title, is_clone (bool), reference_audio_path, sample_path, tags_json, license_note, commercial_ok (bool).
- **Job**: id, project_id, type, params_json, status ('queued'|'running'|'done'|'error'|'canceled'), progress (0–100), message, result_json, error_str, created_at, started_at, finished_at, parent_batch_id.
- **RenderOutput**: id, project_id, variant ('16:9'|'9:16'), local_path, duration, size_bytes, created_at.
- **Setting/Secret**: key, value_encrypted, provider.

### 4. Directory structure
```
casefile-studio/
  docker-compose.yml
  pyproject.toml
  backend/
    app/
      main.py            # FastAPI app, static serving, WS
      config.py
      db.py              # engine, session, migrations
      models.py
      security.py        # key encryption (keyring + Fernet)
      queue/             # JobQueue interface + sqlite_worker.py
      providers/
        tts/             # base.py + fish.py, kokoro.py, piper.py, elevenlabs.py, openai_tts.py, edge.py
        image/           # base.py + fal_flux.py, openai_image.py, imagen.py, ideogram.py,
                         #          pexels.py, pixabay.py, unsplash.py, wikimedia.py, loc.py, internet_archive.py
        llm/             # base.py + claude.py, openai.py
      services/
        segmentation.py  # LLM script -> scenes + image prompts
        tts_service.py
        image_service.py
        align_service.py # faster-whisper + whisperx word timestamps
        subtitle_service.py # SRT/ASS + karaoke
        render_service.py   # FFmpeg pipeline
        cost_estimator.py
        hashing.py
      routers/           # projects, scripts, scenes, voices, assets, batch, render, settings, jobs
    tests/
  frontend/
    src/
      pages/  components/  hooks/  store/  lib/  theme/
  data/                  # gitignored: sqlite db, project folders
    projects/<project_id>/{assets,audio,scenes,renders,cache}/
```

### 5. TTS provider-adapter interface
```python
class TTSProvider:
    name: str
    supports_cloning: bool
    is_local: bool
    commercial_ok: bool           # Kokoro True; Fish-local-weights False; Fish-cloud True
    def list_voices(self) -> list[VoiceInfo]: ...
    def clone_voice(self, ref_audio: bytes, ref_text: str|None, title: str) -> str: ...  # returns voice_id
    def synthesize(self, text: str, voice_id: str, opts: TTSOpts) -> AudioResult: ...     # wav/mp3 bytes + duration
    def estimate_cost(self, text: str) -> float: ...
```
- **FishProvider (default):** cloud API base `https://api.fish.audio`. TTS via `POST /v1/tts` with `model` header (`s2.1-pro` default, or `s2.1-pro-free`), body `{text, reference_id, format}`. Cloning via `POST /model` (multipart: `type=tts`, `title`, `train_mode=fast`, `voices=@sample.wav`) → returns a model id/`reference_id`; also support inline `references` for an instant clone. `list_voices` pulls public + personal models. Respect the **Starter concurrency limit of 5 simultaneous requests** (rises to 15 at $100 prepaid, 50 at $1,000). Cost = **$15 / 1M UTF-8 bytes** (≈180,000 English words ≈ 12 hours). Expose a flag `fish_mode = 'cloud' | 'local'`; when `local`, show a red **"Non-commercial (CC-BY-NC-SA) — testing only"** banner and warn it is very slow on CPU.
- **KokoroProvider:** local ONNX, Apache-2.0, 54 voices, no cloning, CPU-fast (≈2–6× real-time on CPU) — the recommended local default.
- Others: Piper (local), ElevenLabs/OpenAI TTS (cloud, paid), Edge-TTS (free). Each declares `commercial_ok` and surfaces it in the UI.

### 6. Image provider-adapter interface
`ImageProvider` (abstract): `search(query, opts)` for stock and `generate(prompt, opts)` for AI, each returning `AssetCandidate(url, thumb, license, attribution, provider, width, height)`.
- **Stock/archival:** Pexels, Pixabay (cache results per TOS; zero indemnification), Unsplash (**must** send attribution + trigger the download-tracking endpoint per API TOS), Wikimedia Commons, Library of Congress, Internet Archive. Each returns machine-readable license + attribution stored on the Asset.
- **AI:** fal.ai Flux (Schnell ~$0.025 / Pro ~$0.05), Replicate Flux, OpenAI gpt-image (mini ~$0.005 / std ~$0.04), Google Imagen 4 (Fast $0.02 / Standard $0.04 / Ultra $0.06, SynthID-watermarked), Ideogram 3 (~$0.03). Pass negative/style presets for the dark cinematic true-crime look.
- **Policy guardrail (must implement):** a `real_person_guard`. If a scene is flagged `depicts_real_person=true`, the UI **blocks AI generation** for that scene by default and routes to archival/licensed search, showing a warning about right-of-publicity and API-terms risk. AI B-roll scenes can carry an optional "AI-generated / dramatization" overlay.

### 7. Render pipeline (FFmpeg, CPU-first, per-scene parallel)
1. **Per-scene TTS** → normalized WAV; skip if `content_hash` unchanged (idempotency).
2. **Concatenate scene audio** → full narration track; run **loudnorm to -14 LUFS** (two-pass).
3. **Word-level alignment** with faster-whisper + WhisperX against the narration → per-word timestamps → assign scene start/end times → write scene durations back to DB.
4. **Per-scene visual clip:** for each still, FFmpeg `zoompan` Ken Burns (configurable direction/speed) + `scale`/`pad` to 1920×1080, `format=yuv420p`. Render scene clips **in parallel** across CPU cores (pool sized to cores). Use a **modest intermediate upscale (2–4× output width, NOT 8000px)** to control CPU cost. Cache clips by hash.
5. **Assemble:** concat scene clips (stream-copy for hard cuts; `xfade` for crossfades — normalize size/fps/SAR first). Overlay lower-thirds/name cards, optional dramatization disclaimer, end screen.
6. **Subtitles:** generate ASS with karaoke `\k` word highlighting (4–6 words/line, white→yellow), burn in with `-vf ass=`.
7. **Music:** mix background track under narration with `sidechaincompress` ducking; final `loudnorm`.
8. **Encode:** libx264, `-crf 20`, `-preset veryfast` (configurable ultrafast↔medium), `-pix_fmt yuv420p`, `-movflags +faststart`. Provide an `encoder` config for future `h264_qsv`/`h264_videotoolbox`/`h264_nvenc`.
9. **Shorts variant:** 9:16 crop/reframe (center or configurable focus) of selected chapters.
Emit progress at each step over WebSocket.

### 8. API endpoints (REST + WS)
Projects: `GET/POST/PATCH/DELETE /projects`. Scripts: `POST /projects/{id}/script`, `POST /projects/{id}/segment` (LLM). Scenes: `GET/PATCH /scenes`, `POST /scenes/{id}/regenerate-image`, `POST /scenes/{id}/regenerate-audio`, `POST /scenes/reorder`. Voices: `GET /voices`, `POST /voices/clone`, `POST /voices/{id}/preview`. Assets: `GET /assets`, `POST /assets/upload`, `POST /assets/search`, `POST /assets/generate`. Batch: `POST /batch/{op}` where op ∈ {regenerate-audio, swap-images, apply-settings, re-render, set-visual-source, find-replace}. Render: `POST /projects/{id}/render`, `GET /renders`. Jobs: `GET /jobs`, `GET /jobs/{id}`, `POST /jobs/{id}/cancel`, `WS /ws/jobs`. Settings: `GET/PUT /settings`, `POST /settings/keys` (store encrypted), `POST /settings/keys/{provider}/test`.

### 9. Screen-by-screen UI spec (colorful, beginner-friendly)
**Palette (name these exactly):** dark base `#0F1117`, surface `#1A1D29`, primary accent gradient **electric magenta `#FF2D75` → violet `#7C3AED`**, secondary accent **amber `#FFB020`** (CTAs/highlights), info cyan `#22D3EE`, success `#22C55E`, danger `#EF4444`. True-crime "case file" vibe via dark surfaces + vivid gradient accents + subtle red evidence-tape motifs. **Typography:** headings **"Space Grotesk"**, body **"Inter"**, monospace/timecodes **"JetBrains Mono"**. Rounded-2xl cards, soft glow shadows, generous spacing.
Screens:
1. **Dashboard/Projects** — colorful project cards with thumbnail, status pill, progress ring; gradient "New Project" button.
2. **First-run Setup Wizard** — guided; paste API keys; each key gets a "Test" button that pings the provider and shows green/red; explains free vs paid.
3. **New Project Wizard** — pick genre preset, resolution/fps, default voice, default visual source, subtitle style.
4. **Script Editor** — big paste area, word count + estimated runtime, "Auto-split into scenes" button, chapter detection.
5. **Scene Storyboard** — grid/timeline of scene cards, each showing image thumb, mini audio waveform, editable text, duration, visual-source toggle (AI/Stock/Upload), a `depicts_real_person` flag, status. Drag to reorder. Multi-select checkboxes.
6. **Voice Studio** — browse Fish public voices (search/tags/preview), upload reference audio to clone, name & test clones, per-voice `commercial_ok` badge, set project default voice.
7. **Asset Library** — all assets with license + attribution shown; upload; search stock; generate AI.
8. **Batch Operations panel** — multi-select scenes → apply-to-all/selected: regenerate audio, swap images (choose source), apply settings, find-and-replace across scenes, bulk regenerate with a **diff preview** before committing.
9. **Render Queue** — live job list with progress bars, ETA, cancel; per-step status.
10. **Settings/API Keys** — encrypted key entry per provider with Test buttons; Fish cloud/local toggle with the non-commercial warning; encoder/GPU toggle.
11. **Export** — pick 16:9 and/or 9:16, preview, download, open output folder.
Beginner touches: tooltips everywhere, genre/style presets, a **cost estimator** shown before any paid batch (sums TTS bytes × rate + images × per-image price), plain-language error messages with a "what to do" hint.

### 10. Batch operations spec
Multi-select any set of scenes (or "select all"). Operations create one parent Job with child Jobs, run through the queue with live progress, and are **idempotent** (skip unchanged content hashes). Every destructive batch shows a diff/preview (old vs new image, old vs new audio length, text changes) and requires confirm. Per-batch, the user chooses the visual source (AI vs stock vs uploaded). "Apply settings to all/selected" covers voice, Ken Burns style, subtitle style, and disclaimer overlay.

### 11. Settings / API-key management spec
Keys entered in-app are encrypted at rest using a Fernet key stored in the OS keyring (fallback: a locally-generated key file with 600 perms, never committed). `.env` supported for power users, but the DB-encrypted store is primary. Never log keys. Provide "Test key" per provider. Redact keys in the UI after save (show last 4 only).

### 12. Error handling & CPU-performance requirements
- All long tasks run in the queue, survive restart (jobs table), and are cancelable. Auto-retry transient API errors with backoff; surface Fish concurrency/rate-limit errors as "waiting, will retry."
- CPU perf: cap parallel scene renders to CPU core count; default TTS = Fish cloud (fast) or Kokoro (local). Warn if the user selects fish-local on CPU ("very slow on CPU; use cloud"). Show realistic time estimates before render. Cache/skip unchanged assets on re-render.
- Detect on startup whether FFmpeg has libass/libx264 and warn if missing.

### 13. Acceptance criteria
- Paste a 3,000-word script → auto-split into scenes with per-scene image prompts.
- Choose/clone a Fish voice → generate narration → word-level subtitles align within ~150ms.
- Mixed sourcing works: some scenes AI, some stock, some uploaded, chosen per-batch.
- Real-person guard blocks AI gen on flagged scenes and routes to archival.
- Render a full 16:9 video with Ken Burns, crossfades, karaoke subs, ducked music, -14 LUFS.
- Re-render after editing 2 scenes only re-processes those 2 (hashing works).
- Export 16:9 and 9:16; all assets carry license/attribution; no plaintext keys anywhere.
- One-command launch works (`docker compose up`).

### 14. Phased build order
- **Phase 1 (MVP):** Project + Script + LLM scene split; Fish cloud TTS single voice; stock (Pexels) + upload images; basic FFmpeg still+zoompan+concat render; burned SRT subtitles; SQLite queue + WS progress; Settings/keys (encrypted); Dashboard + Script Editor + Storyboard + Render Queue.
- **Phase 2:** Voice Studio (browse + clone Fish); Kokoro local fallback adapter; AI image adapters (fal Flux, OpenAI) + real-person guard; WhisperX word-level karaoke ASS; music ducking + loudnorm; cost estimator.
- **Phase 3:** Full batch operations with diff preview; caching/idempotency by hash; more stock/archival adapters (Pixabay, Unsplash, Wikimedia, LoC, Internet Archive); Shorts 9:16 export; presets/templates.
- **Phase 4:** GPU-optional config (hardware encoders + GPU TTS/whisper), additional TTS providers, polish, onboarding wizard, error-message pass.

---

## What to do first (setup order, keys, costs, CPU expectations)

**Setup order:** (1) Install Docker (or `uv`) + FFmpeg with libx264/libass. (2) `docker compose up`. (3) Open `http://localhost:8760`, run the Setup Wizard. (4) Paste your API keys and hit "Test" on each. (5) Create a project, paste a short script, and do one test render before committing to a long video.

**API keys to get and rough costs (August 2026):**
- **Fish Audio** (narration, default) — pay-as-you-go **$15 / 1M UTF-8 bytes** (~12 hours of speech); a free `s2.1-pro-free` tier exists. A 20-minute video ≈ 3,000 words ≈ ~$0.25.
- **Kokoro** (local fallback) — free, Apache-2.0, no key.
- **An LLM key** (Anthropic Claude or OpenAI) for scene splitting — a few cents per script.
- **Stock images:** Pexels + Pixabay free (get free API keys); Unsplash free (attribution required).
- **AI images (optional):** fal.ai Flux ~$0.025–$0.05/image, OpenAI gpt-image ~$0.005–$0.04/image, Imagen $0.02–$0.06. A 40-scene video of AI images ≈ $1–$2.

**Realistic CPU-only expectations:** Narration via Fish **cloud** is fast (seconds per paragraph). Do NOT run fish-speech locally on CPU — it is far slower than real-time and non-commercially licensed. Kokoro local narration runs ~2–6× faster than real-time on CPU. The bottleneck is video rendering: a 15–25 minute 1080p Ken Burns + crossfade video may take on the order of the video's length up to several times longer to encode on a multicore CPU. Use `-preset veryfast`, keep the zoompan upscale modest, render scenes in parallel, and cache unchanged assets so re-renders are cheap. Upgrade to a GPU later to enable hardware encoding and local GPU TTS.

## Recommendations

**Stage 1 — Build the MVP around the cloud Fish API + Pexels + basic FFmpeg render.** This is the fastest path to a working video and sidesteps both the CPU-TTS problem and the non-commercial license problem. Threshold to proceed: you can produce one complete 16:9 video end-to-end.

**Stage 2 — Add Kokoro (local, free, commercial-safe) and the real-person guard before touching AI images.** Kokoro removes per-word API cost for drafts; the guard is your legal seatbelt. Threshold: AI generation is provably blocked on `depicts_real_person` scenes.

**Stage 3 — Add batch ops, hashing/idempotency, more archival sources, and Shorts export.** This is where the tool becomes a real production pipeline for a channel rather than a one-off generator.

**Decision triggers that should change the plan:** (a) If you buy an NVIDIA GPU, enable local fish-speech only after training/licensing is resolved, and switch the encoder to NVENC — expect roughly an order-of-magnitude faster render. (b) If Fish changes the open-weights license to something commercial-permissive, revisit local TTS. (c) If a batch cost estimate exceeds your comfort threshold, default that batch to Kokoro + stock rather than Fish + AI images. (d) If YouTube flags videos as "limited ads," audit for graphic visuals/thumbnails and lean harder on non-graphic archival imagery and the dramatization disclaimer.

## Caveats
- **No published CPU real-time-factor exists for fish-speech**; the "far slower than real-time" conclusion is inferred from GPU-only benchmarks and qualitative GitHub reports. Benchmark on your own CPU before relying on any local-TTS path.
- **Render-time estimates are inferences**, not a single cited zoompan benchmark; they depend heavily on the upscale size, fps, preset, and CPU. Treat the "video-length up to several × longer" figure as a planning range and measure on your machine.
- **Pricing and model IDs (Fish, fal, OpenAI, Imagen, Ideogram) are as of mid-2026 and change frequently.** The build prompt intentionally routes all pricing through per-provider adapters and a cost estimator so rates can be updated in one place; re-verify current prices before committing to volume.
- **Free stock licenses do not guarantee model releases or indemnification.** Even "free for commercial use" photos of recognizable people can carry right-of-publicity risk; for real crime figures, prefer clearly public-domain archival sources (Library of Congress PD sets, Wikimedia PD) and keep records of each asset's license/attribution (the data model does this).
- **This is engineering/production guidance, not legal advice.** True crime about real, possibly-living people carries defamation and publicity-rights exposure that varies by jurisdiction; consult a media attorney before monetizing at scale, and keep AI depictions of real named individuals off by default.
- The X reference post was inaccessible; tone/format assumptions are generic to the true-crime narration genre.