"""Job handlers: segment, narrate, source images, render.

Each handler is checkpointed at the unit of work that actually takes time - a
scene - so an hour-long render that is paused, crashed, or restarted picks up
where it stopped instead of starting the hour again.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from sqlmodel import select

from .. import ffmpeg
from ..config import settings
from ..db import session_scope
from ..models import Asset, Chapter, Project, RenderOutput, Scene, Script
from ..providers.tts import TTSOpts
from ..queue.base import JobContext
from . import audio as audio_service
from . import (
    align_service, image_service, query, render_service, segmentation,
    subtitle_service, tts_service, video_service, vision,
)

log = logging.getLogger("casefile.pipeline")

DEFAULTS: dict[str, Any] = {
    "tts_provider": "draft",
    "voice_id": "silence",
    "llm_provider": None,
    "visual_source": "placeholder",
    "image_pool_per_chapter": 8,
    # Sourcing behaviour
    "video_enabled": False,
    "video_provider": "pexels_video",
    "video_share": 0.35,              # fraction of scenes that get motion
    "archival_source": "",
    "blur_faces": "real_person",      # 'off' | 'real_person' | 'all'
    "soundbite_max_sec": 25.0,
    "ambient_gain_db": -9.0,
    "subtitle_style": subtitle_service.DEFAULT_STYLE,
    "karaoke": True,
    "burn_subtitles": True,
    "kenburns": "auto",
    "transition_sec": settings.transition_sec,
    "width": settings.width,
    "height": settings.height,
    "fps": settings.fps,
    "crf": settings.crf,
    "preset": settings.preset,
    "encoder": settings.encoder,
    "music_path": "",
    "loudness_lufs": settings.loudness_lufs,
    "spend_ceiling": None,
    "align_method": "auto",
    "whisper_model": "small",
    "disclaimer_text": "",
}


def project_settings(project: Project) -> dict[str, Any]:
    merged = dict(DEFAULTS)
    merged.update(project.settings_json or {})
    return merged


def _render_opts(cfg: dict[str, Any]) -> render_service.RenderOpts:
    return render_service.RenderOpts(
        width=int(cfg["width"]), height=int(cfg["height"]), fps=int(cfg["fps"]),
        crf=int(cfg["crf"]), preset=str(cfg["preset"]), encoder=str(cfg["encoder"]),
        transition_sec=float(cfg["transition_sec"]),
        subtitle_style=str(cfg["subtitle_style"]),
        karaoke=bool(cfg["karaoke"]), burn_subtitles=bool(cfg["burn_subtitles"]),
    )


def _scenes(session, project_id: int) -> list[Scene]:
    return list(session.exec(
        select(Scene).where(Scene.project_id == project_id).order_by(Scene.order_index)
    ).all())


# ---------------------------------------------------------------------------
# Segment
# ---------------------------------------------------------------------------

def handle_segment(ctx: JobContext) -> dict[str, Any]:
    project_id = int(ctx.params["project_id"])
    with session_scope() as session:
        project = session.get(Project, project_id)
        script = session.exec(
            select(Script).where(Script.project_id == project_id).order_by(Script.id.desc())
        ).first()
        if not project or not script:
            raise ValueError("Project has no script yet.")
        cfg = project_settings(project)
        raw = script.raw_text

    ctx.progress(0.05, "reading the script", force=True)
    drafts, method = segmentation.segment(
        raw,
        llm_name=ctx.params.get("llm_provider") or cfg.get("llm_provider"),
        progress=lambda f: ctx.progress(0.05 + f * 0.75, f"grouping scenes ({method_hint(f)})"),
    )
    ctx.progress(0.85, f"writing {len(drafts)} scenes", force=True)

    chapter_marks = segmentation.assign_chapters(drafts)
    with session_scope() as session:
        for old in _scenes(session, project_id):
            session.delete(old)
        for old_chapter in session.exec(select(Chapter).where(Chapter.project_id == project_id)).all():
            session.delete(old_chapter)
        session.flush()

        chapter_ids: dict[int, int] = {}
        for order, (title, first_scene) in enumerate(chapter_marks):
            chapter = Chapter(project_id=project_id, order_index=order, title=title)
            session.add(chapter)
            session.flush()
            chapter_ids[first_scene] = int(chapter.id)

        current_chapter: int | None = None
        for i, draft in enumerate(drafts):
            if i in chapter_ids:
                current_chapter = chapter_ids[i]
            session.add(Scene(
                project_id=project_id, order_index=i, chapter_id=current_chapter,
                text=draft.text,
                source_char_start=draft.start, source_char_end=draft.end,
                image_prompt=draft.image_prompt,
                depicts_real_person=draft.depicts_real_person,
                visual_source="stock" if draft.depicts_real_person else "ai",
                kenburns=cfg["kenburns"],
                status="new",
            ))

        script_row = session.exec(
            select(Script).where(Script.project_id == project_id).order_by(Script.id.desc())
        ).first()
        if script_row:
            script_row.word_count = segmentation.word_count(raw)
            script_row.estimated_runtime_sec = segmentation.estimate_runtime(raw)
            script_row.segmentation_checksum = method
            session.add(script_row)

        project = session.get(Project, project_id)
        if project:
            project.status = "segmented"
            session.add(project)

    ctx.progress(1.0, f"{len(drafts)} scenes ready", force=True)
    return {"scenes": len(drafts), "chapters": len(chapter_marks), "method": method}


def method_hint(fraction: float) -> str:
    return f"{int(fraction * 100)}%"


# ---------------------------------------------------------------------------
# Narrate
# ---------------------------------------------------------------------------

def handle_narrate(ctx: JobContext) -> dict[str, Any]:
    project_id = int(ctx.params["project_id"])
    only = set(ctx.params.get("scene_ids") or [])

    with session_scope() as session:
        project = session.get(Project, project_id)
        if not project:
            raise ValueError("Project not found.")
        cfg = project_settings(project)
        cfg.update({k: v for k, v in ctx.params.items() if k in DEFAULTS and v is not None})
        scenes = [s for s in _scenes(session, project_id) if not only or s.id in only]
        assets = {int(a.id): a.local_path for a in session.exec(
            select(Asset).where(Asset.project_id == project_id)).all()}
        # A soundbite scene is spoken by the footage, so it gets no voiceover
        # at all. Its slot in the timeline is the clip's own audio, which is
        # what makes overlap impossible rather than merely quiet.
        soundbites = [
            (int(s.id), assets.get(s.asset_id or -1, ""), s.media_in)
            for s in scenes if s.audio_mode == "soundbite" and s.asset_id
        ]
        spoken = {sid for sid, _p, _i in soundbites}
        items = [(int(s.id), s.text) for s in scenes if int(s.id) not in spoken]

    if not items and not soundbites:
        return {"scenes": 0}

    ctx.progress(0.02, f"narrating {len(items)} scenes", force=True)
    results = {} if not items else tts_service.synthesize_batch(
        project_id=project_id,
        items=items,
        provider_name=str(cfg["tts_provider"]),
        voice_id=str(cfg["voice_id"]),
        opts=TTSOpts(sample_rate=tts_service.SAMPLE_RATE),
        spend_ceiling=cfg.get("spend_ceiling"),
        on_progress=lambda f, m: ctx.progress(0.02 + f * 0.95, m),
        should_stop=ctx.check_stop,
    )

    # Clip audio for the scenes the footage speaks over.
    clip_audio: dict[int, tuple[Path, float]] = {}
    for scene_id, source, media_in in soundbites:
        ctx.check_stop()
        if not source or not Path(source).exists():
            continue
        dest = settings.project_dir(project_id) / "audio" / f"soundbite_{scene_id}.wav"
        try:
            duration = video_service.extract_audio(Path(source), dest)
            clip_audio[scene_id] = (dest, duration)
        except Exception as exc:
            log.warning("could not take audio from the clip for scene %s: %s", scene_id, exc)

    with session_scope() as session:
        for scene_id, (path, duration) in clip_audio.items():
            asset = Asset(
                project_id=project_id, type="audio", source_provider="clip",
                local_path=str(path), duration=duration, content_hash=f"soundbite-{scene_id}",
            )
            session.add(asset)
            session.flush()
            scene = session.get(Scene, scene_id)
            if scene:
                scene.audio_asset_id = int(asset.id)
                scene.duration = duration
                scene.status = "audio_ready"
                session.add(scene)

        for scene in _scenes(session, project_id):
            audio = results.get(int(scene.id))
            if not audio:
                continue
            asset = Asset(
                project_id=project_id, type="audio", source_provider=str(cfg["tts_provider"]),
                local_path=str(audio.path), duration=audio.duration,
                content_hash=audio.path.stem,
            )
            session.add(asset)
            session.flush()
            scene.audio_asset_id = int(asset.id)
            scene.duration = audio.duration
            scene.status = "audio_ready"
            session.add(scene)

    total = sum(r.duration for r in results.values()) + sum(d for _p, d in clip_audio.values())
    cost = sum(r.cost for r in results.values())
    ctx.progress(1.0, "narration ready", force=True)
    return {
        "scenes": len(results) + len(clip_audio),
        "soundbites": len(clip_audio),
        "cached": sum(1 for r in results.values() if r.cached),
        "audio_sec": round(total, 2),
        "cost_usd": round(cost, 4),
    }


# ---------------------------------------------------------------------------
# Images
# ---------------------------------------------------------------------------

def handle_images(ctx: JobContext) -> dict[str, Any]:
    """Source a still or a clip for every scene, straight from the script.

    Each scene builds a ladder of search queries (services/query.py) and this
    walks down it until something comes back, so a scene about a named person
    goes to the archives and a scene about a mood goes to the stock libraries.
    """
    project_id = int(ctx.params["project_id"])
    only = set(ctx.params.get("scene_ids") or [])
    done = ctx.done_units("images")

    with session_scope() as session:
        project = session.get(Project, project_id)
        if not project:
            raise ValueError("Project not found.")
        cfg = project_settings(project)
        cfg.update({k: v for k, v in ctx.params.items() if k in DEFAULTS and v is not None})
        scenes = [s for s in _scenes(session, project_id) if not only or s.id in only]
        rows = [
            {
                "id": int(s.id), "prompt": s.image_prompt, "chapter": s.chapter_id,
                "real": s.depicts_real_person, "text": s.text,
                "source": s.visual_source, "order": s.order_index,
                "duration": s.duration or segmentation.estimate_runtime(s.text),
            }
            for s in scenes
        ]

    if not rows:
        return {"images": 0, "videos": 0}

    wanted_video = _plan_video_scenes(rows, cfg)
    used_urls: set[str] = set()
    counts = {"images": 0, "videos": 0, "blurred": 0, "faces": 0}
    failures: list[str] = []

    # Stills are pooled per chapter; clips are per scene because a repeated
    # clip is far more obvious than a repeated photograph.
    pools: dict[Any, list] = {}

    for i, row in enumerate(rows):
        ctx.check_stop()
        ctx.progress(i / max(len(rows), 1), f"sourcing media {i + 1}/{len(rows)}")
        if str(row["id"]) in done:
            continue

        try:
            if row["id"] in wanted_video:
                _source_video_for(project_id, row, cfg, used_urls, counts)
            else:
                _source_image_for(project_id, row, cfg, used_urls, counts, pools)
            ctx.mark_done("images", str(row["id"]))
        except Exception as exc:
            failures.append(f"scene {row['order'] + 1}: {exc}")
            log.warning("sourcing failed for scene %s: %s", row["id"], exc)

    ctx.progress(1.0, "media ready", force=True)
    return {**counts, "failures": failures[:20]}


def _plan_video_scenes(rows: list[dict], cfg: dict[str, Any]) -> set[int]:
    """Which scenes get motion.

    Spread evenly rather than clustered, and never on a scene flagged as a real
    person - those are routed to archival stills where the licensing and the
    right-of-publicity position are clearest.
    """
    explicit = {r["id"] for r in rows if r["source"] == "video"}
    if not cfg.get("video_enabled"):
        return explicit

    share = max(0.0, min(1.0, float(cfg.get("video_share", 0.35))))
    eligible = [r for r in rows if not r["real"] and r["source"] != "upload"]
    target = int(round(len(eligible) * share))
    if target <= 0:
        return explicit

    step = max(1, len(eligible) // target)
    return explicit | {r["id"] for r in eligible[::step][:target]}


def _source_video_for(project_id: int, row: dict, cfg: dict[str, Any],
                      used: set[str], counts: dict[str, int]) -> None:
    from ..providers import video as video_providers

    provider = video_providers.get_provider(str(cfg.get("video_provider") or "pexels_video"))
    usable, reason = provider.available()
    if not usable:
        raise RuntimeError(reason)

    queries = query.build(row["text"], row["prompt"])
    needed = max(4.0, float(row["duration"] or 8.0))

    candidate = None
    chosen_query = ""
    for term in queries.ladder(prefer_archival=provider.is_archival)[:5]:
        try:
            results = provider.search(term, count=10)
        except Exception as exc:
            log.debug("video search %r failed: %s", term, exc)
            continue
        candidate = query.pick(results, query=term, used=used)
        if candidate:
            chosen_query = term
            break
    if candidate is None:
        raise RuntimeError("no video results for any query in the ladder")

    used.add(candidate.url)
    stored = video_service.store_candidate(
        project_id, candidate, target_width=int(cfg["width"])
    )

    audio_mode = "narration"
    if stored.has_audio and video_service.has_audible_audio(stored.path):
        # Archival footage that actually speaks gets the voiceover out of its
        # way; stock B-roll ambience sits underneath instead.
        default = str(cfg.get("clip_audio_default") or
                      ("soundbite" if provider.is_archival else "ambient"))
        if default == "soundbite" and stored.duration <= float(cfg.get("soundbite_max_sec", 25.0)):
            audio_mode = "soundbite"
        elif default in ("ambient", "soundbite"):
            audio_mode = "ambient"

    blur_policy = str(cfg.get("blur_faces", "real_person"))
    should_blur = blur_policy == "all" or (blur_policy == "real_person" and row["real"])
    faces = 0
    if should_blur:
        try:
            tracks = video_service.face_tracks(stored.path)
            faces = len(tracks)
            counts["faces"] += faces
            if faces:
                counts["blurred"] += 1
        except Exception as exc:
            log.warning("face detection failed on %s: %s", stored.path.name, exc)

    with session_scope() as session:
        asset = Asset(
            project_id=project_id, type="video", source_provider=stored.provider,
            source_url=stored.source_url, local_path=str(stored.path),
            license_str=stored.license, attribution_str=stored.attribution,
            width=stored.width, height=stored.height, duration=stored.duration,
            content_hash=stored.content_hash, has_audio=stored.has_audio,
            faces_found=faces,
        )
        session.add(asset)
        session.flush()
        scene = session.get(Scene, row["id"])
        if scene:
            scene.asset_id = int(asset.id)
            scene.media_kind = "video"
            scene.visual_source = "video"
            scene.media_in = video_service.pick_in_point(stored.duration, needed)
            scene.audio_mode = audio_mode
            scene.blur_faces = should_blur
            scene.notes = (scene.notes or "") if scene.notes else f"query: {chosen_query}"
            session.add(scene)
    counts["videos"] += 1


def _source_image_for(project_id: int, row: dict, cfg: dict[str, Any],
                      used: set[str], counts: dict[str, int], pools: dict) -> None:
    from ..providers.image import get_provider

    provider_name = _archival_provider(cfg) if row["real"] else str(cfg["visual_source"])
    provider = get_provider(provider_name)

    if provider.kind == "ai":
        stored = image_service.source_image(
            project_id=project_id, prompt=row["prompt"], provider_name=provider_name,
            depicts_real_person=row["real"],
        )
    else:
        queries = query.build(row["text"], row["prompt"])
        candidate = None
        for term in queries.ladder(prefer_archival=row["real"])[:5]:
            try:
                results = provider.search(term, count=12)
            except Exception as exc:
                log.debug("image search %r failed: %s", term, exc)
                continue
            candidate = query.pick(results, query=term, used=used)
            if candidate:
                break
        if candidate is None:
            raise RuntimeError("no image results for any query in the ladder")
        used.add(candidate.url)
        stored = image_service.store_candidate(project_id, candidate)

    blur_policy = str(cfg.get("blur_faces", "real_person"))
    should_blur = blur_policy == "all" or (blur_policy == "real_person" and row["real"])
    faces = 0
    blurred_path = ""
    if should_blur and vision.available()[0]:
        try:
            target = stored.path.with_name(stored.path.stem + "_deid.jpg")
            faces = vision.blur_image_faces(stored.path, target)
            blurred_path = str(target)
            counts["faces"] += faces
            if faces:
                counts["blurred"] += 1
        except Exception as exc:
            log.warning("face blur failed on %s: %s", stored.path.name, exc)

    _attach_asset(project_id, row["id"], stored,
                  blurred_path=blurred_path, faces=faces, blur=should_blur)
    counts["images"] += 1


def _attach_asset(project_id: int, scene_id: int, stored: image_service.StoredImage,
                  *, blurred_path: str = "", faces: int = 0, blur: bool = False) -> None:
    with session_scope() as session:
        asset = session.exec(
            select(Asset).where(Asset.project_id == project_id,
                                Asset.content_hash == stored.content_hash,
                                Asset.type == "image")
        ).first()
        if asset is None:
            asset = Asset(
                project_id=project_id, type="image", source_provider=stored.provider,
                source_url=stored.source_url, local_path=str(stored.path),
                license_str=stored.license, attribution_str=stored.attribution,
                width=stored.width, height=stored.height, content_hash=stored.content_hash,
                blurred_path=blurred_path, faces_found=faces,
            )
            session.add(asset)
            session.flush()
        elif blurred_path and not asset.blurred_path:
            asset.blurred_path = blurred_path
            asset.faces_found = faces
            session.add(asset)
        scene = session.get(Scene, scene_id)
        if scene:
            scene.asset_id = int(asset.id)
            scene.media_kind = "image"
            scene.blur_faces = blur
            scene.status = "visual_ready" if scene.status == "audio_ready" else scene.status
            session.add(scene)


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------

def handle_render(ctx: JobContext) -> dict[str, Any]:
    project_id = int(ctx.params["project_id"])
    chapter_id = ctx.params.get("chapter_id")
    variant = str(ctx.params.get("variant", "16:9"))

    with session_scope() as session:
        project = session.get(Project, project_id)
        if not project:
            raise ValueError("Project not found.")
        cfg = project_settings(project)
        cfg.update({k: v for k, v in ctx.params.items() if k in DEFAULTS and v is not None})
        all_scenes = _scenes(session, project_id)
        scenes = [s for s in all_scenes if chapter_id is None or s.chapter_id == chapter_id]
        if not scenes:
            raise ValueError("No scenes to render.")
        missing_audio = [s.order_index for s in scenes if not s.audio_asset_id]
        missing_image = [s.order_index for s in scenes if not s.asset_id]
        assets = {int(a.id): a for a in session.exec(
            select(Asset).where(Asset.project_id == project_id)).all()}
        # Detached ORM instances cannot lazy-load, so everything the render
        # needs is copied out as plain data while the session is open.
        chapters = [
            {"id": int(c.id), "title": c.title, "order_index": c.order_index}
            for c in session.exec(
                select(Chapter).where(Chapter.project_id == project_id).order_by(Chapter.order_index)
            ).all()
        ]
        rows = []
        for s in scenes:
            visual = assets.get(s.asset_id or -1)
            # A blurred derivative always wins over the original: if the blur
            # exists, the original must never reach the render.
            picture = ""
            if visual:
                picture = visual.blurred_path if (s.blur_faces and visual.blurred_path) else visual.local_path
            rows.append({
                "id": int(s.id), "index": s.order_index, "text": s.text,
                "audio": assets[s.audio_asset_id].local_path if s.audio_asset_id in assets else "",
                "image": picture,
                "media_kind": s.media_kind, "media_in": s.media_in,
                "media_duration": visual.duration if visual else 0.0,
                "audio_mode": s.audio_mode, "blur_faces": s.blur_faces,
                "kenburns": s.kenburns, "chapter": s.chapter_id,
                "disclaimer": s.ai_disclaimer,
            })

    if missing_audio:
        raise ValueError(f"{len(missing_audio)} scene(s) have no narration yet. Run 'Narrate' first.")
    if missing_image:
        raise ValueError(f"{len(missing_image)} scene(s) have no image yet. Run 'Source images' first.")

    opts = _render_opts(cfg)
    base = settings.ensure_project_dirs(project_id)
    workdir = base / ("clips" if chapter_id is None else f"clips_ch{chapter_id}")
    renders = base / "renders"
    renders.mkdir(parents=True, exist_ok=True)

    # --- 1. narration timeline ------------------------------------------
    ctx.progress(0.02, "assembling narration", force=True)
    narration = base / "audio" / (f"narration_{chapter_id or 'full'}.wav")
    timeline = audio_service.concat_wavs([Path(r["audio"]) for r in rows], narration)

    check = render_service.preflight(len(rows), timeline.total, opts, workdir)
    if not check["ok"]:
        raise RuntimeError(" ".join(check["problems"]))
    ctx.set_state("preflight", check)

    with session_scope() as session:
        for row, start, end in zip(rows, timeline.starts, timeline.ends):
            scene = session.get(Scene, row["id"])
            if scene:
                scene.start_time, scene.end_time = round(start, 3), round(end, 3)
                scene.duration = round(end - start, 3)
                session.add(scene)

    # --- 2. word timings -------------------------------------------------
    method = str(cfg["align_method"])
    aligned = ctx.done_units("align")
    ctx.progress(0.08, f"timing captions ({align_service.best_method() if method == 'auto' else method})", force=True)
    words_by_scene: dict[int, list[align_service.Word]] = {}
    for i, row in enumerate(rows):
        ctx.check_stop()
        duration = timeline.ends[i] - timeline.starts[i]
        cache = workdir / "words" / f"{row['id']}.json"
        if str(row["id"]) in aligned and cache.exists():
            words_by_scene[row["id"]] = _load_words(cache)
        else:
            words = align_service.align_scene(
                Path(row["audio"]), row["text"], duration,
                method=method, model_size=str(cfg["whisper_model"]),
            )
            words_by_scene[row["id"]] = words
            _save_words(cache, words)
            ctx.mark_done("align", str(row["id"]))
        ctx.progress(0.08 + 0.17 * (i + 1) / len(rows), f"timing captions {i + 1}/{len(rows)}")

    with session_scope() as session:
        for row in rows:
            scene = session.get(Scene, row["id"])
            if scene:
                scene.words_json = [[w.text, w.start, w.end] for w in words_by_scene[row["id"]]]
                session.add(scene)

    # --- 3. scene clips --------------------------------------------------
    frames = render_service.frame_plan(timeline.starts, timeline.total, opts.fps)
    disclaimer = str(cfg.get("disclaimer_text") or "")
    chapter_titles = {c["id"]: c["title"] for c in chapters}
    seen_chapters: set[int] = set()

    specs: list[render_service.SceneSpec] = []
    for i, row in enumerate(rows):
        lower = ""
        if row["chapter"] and row["chapter"] not in seen_chapters:
            seen_chapters.add(row["chapter"])
            lower = chapter_titles.get(row["chapter"], "")
        poster = None
        blur_tracks: list = []
        if row["media_kind"] == "video":
            source = Path(row["image"])
            poster = source.with_suffix(".jpg")
            if not poster.exists():
                ffmpeg.run(["-i", str(source), "-frames:v", "1", "-q:v", "3", str(poster)])
            if row["blur_faces"]:
                try:
                    blur_tracks = video_service.face_tracks(source)
                except Exception as exc:
                    log.warning("face regions unavailable for scene %s: %s", row["id"], exc)

        specs.append(render_service.SceneSpec(
            index=i,
            image=Path(row["image"]),
            frames=frames[i],
            kenburns=row["kenburns"] or "auto",
            words=words_by_scene[row["id"]],
            banner=disclaimer if row["disclaimer"] else "",
            lower_third=lower,
            media_kind=row["media_kind"],
            media_in=float(row["media_in"] or 0.0),
            media_duration=float(row["media_duration"] or 0.0),
            poster=poster,
            blur_tracks=blur_tracks,
        ))
    render_service.plan_transitions(specs, opts)

    ctx.progress(0.26, f"rendering {len(specs)} scene clips on {settings.render_workers} cores", force=True)
    clips = render_service.render_scene_clips(
        specs, workdir, opts,
        workers=settings.render_workers,
        on_progress=lambda done, total: ctx.progress(
            0.26 + 0.55 * done / max(total, 1), f"scene clip {done}/{total}"
        ),
        should_stop=ctx.check_stop,
    )
    ctx.set_state("clips", len(clips))

    with session_scope() as session:
        for spec, clip, row in zip(specs, clips, rows):
            scene = session.get(Scene, row["id"])
            if scene:
                scene.clip_path = str(clip)
                scene.clip_hash = clip.stem.split("_")[-1]
                scene.status = "ready"
                session.add(scene)

    # --- 4. master audio -------------------------------------------------
    ctx.progress(0.83, "mixing audio and setting loudness", force=True)
    music = Path(cfg["music_path"]) if cfg.get("music_path") else None
    ambient = _build_ambient_bed(project_id, rows, timeline, chapter_id)
    master_audio = audio_service.build_master(
        narration=narration,
        dest=base / "audio" / f"master_{chapter_id or 'full'}.m4a",
        music=music if music and music.exists() else None,
        ambient=ambient,
        ambient_gain_db=float(cfg.get("ambient_gain_db", -9.0)),
        target_lufs=float(cfg["loudness_lufs"]),
        total_sec=timeline.total,
        on_progress=lambda f: ctx.progress(0.83 + f * 0.06, "mixing audio"),
    )

    # --- 5. stream-copy assembly ----------------------------------------
    ctx.progress(0.90, "assembling the final file", force=True)
    suffix = "full" if chapter_id is None else f"chapter{chapter_id}"
    out_path = renders / f"{suffix}_{opts.height}p.mp4"
    render_service.concat_clips(clips, out_path, audio=master_audio)

    # --- 6. sidecars -----------------------------------------------------
    ctx.progress(0.96, "writing chapters and captions", force=True)
    absolute: list[align_service.Word] = []
    for i, row in enumerate(rows):
        absolute.extend(align_service.shift(words_by_scene[row["id"]], timeline.starts[i]))
    srt_path = renders / f"{suffix}.srt"
    srt_path.write_text(subtitle_service.build_srt(absolute), encoding="utf-8")

    chapter_marks: list[tuple[str, float]] = []
    for chapter in chapters:
        first = next((i for i, r in enumerate(rows) if r["chapter"] == chapter["id"]), None)
        if first is not None:
            chapter_marks.append((chapter["title"], timeline.starts[first]))
    chapters_txt = renders / f"{suffix}_chapters.txt"
    if chapter_marks:
        render_service.write_chapters_txt(chapter_marks, chapters_txt)

    ad_breaks = render_service.suggest_ad_breaks(timeline.starts, timeline.total)

    with session_scope() as session:
        for chapter in chapters:
            first = next((i for i, r in enumerate(rows) if r["chapter"] == chapter["id"]), None)
            if first is None:
                continue
            last = max(i for i, r in enumerate(rows) if r["chapter"] == chapter["id"])
            row = session.get(Chapter, chapter["id"])
            if row:
                row.start_time = round(timeline.starts[first], 2)
                row.end_time = round(timeline.ends[last], 2)
                session.add(row)

        session.add(RenderOutput(
            project_id=project_id, variant=variant, local_path=str(out_path),
            duration=round(timeline.total, 2), size_bytes=out_path.stat().st_size,
            chapters_txt_path=str(chapters_txt) if chapter_marks else "",
            ad_breaks_json=ad_breaks,
        ))
        project = session.get(Project, project_id)
        if project:
            project.status = "rendered"
            session.add(project)

    ctx.progress(1.0, "done", force=True)
    return {
        "path": str(out_path),
        "duration_sec": round(timeline.total, 2),
        "size_bytes": out_path.stat().st_size,
        "scenes": len(rows),
        "chapters_txt": str(chapters_txt) if chapter_marks else "",
        "srt": str(srt_path),
        "ad_breaks": ad_breaks,
    }


def _build_ambient_bed(project_id: int, rows: list[dict], timeline, chapter_id) -> Path | None:
    """A track the same length as the narration, holding clip ambience.

    Built as one segment per scene - silence, or that scene's clip audio fitted
    to its slot - and concatenated. Same construction as the narration track,
    so the two line up sample for sample without any delay arithmetic.
    """
    if not any(r["audio_mode"] == "ambient" for r in rows):
        return None

    work = settings.project_dir(project_id) / "audio" / "ambient"
    work.mkdir(parents=True, exist_ok=True)
    segments: list[Path] = []

    for i, row in enumerate(rows):
        # Each segment spans the scene's whole *slot*, up to the next scene's
        # start, so the inter-scene breaths are included. Sizing by scene
        # length alone would leave the bed short by one gap per scene and drift
        # it steadily earlier across an hour.
        slot_end = timeline.starts[i + 1] if i + 1 < len(timeline.starts) else timeline.total
        length = max(0.05, slot_end - timeline.starts[i])
        segment = work / f"seg_{i:05d}.wav"
        if row["audio_mode"] == "ambient" and row["media_kind"] == "video" and row["image"]:
            raw = work / f"raw_{i:05d}.wav"
            try:
                video_service.extract_audio(Path(row["image"]), raw)
                audio_service.fit(raw, segment, length)
            except Exception as exc:
                log.warning("ambient audio failed for scene %s: %s", row["id"], exc)
                audio_service.silence(length, segment)
            finally:
                raw.unlink(missing_ok=True)
        else:
            audio_service.silence(length, segment)
        segments.append(segment)

    dest = settings.project_dir(project_id) / "audio" / f"ambient_{chapter_id or 'full'}.wav"
    audio_service.concat_wavs(segments, dest, gap_sec=0.0)
    return dest


def _save_words(path: Path, words: list[align_service.Word]) -> None:
    import json

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([[w.text, w.start, w.end] for w in words]), encoding="utf-8")


def _load_words(path: Path) -> list[align_service.Word]:
    import json

    return [align_service.Word(t, s, e) for t, s, e in json.loads(path.read_text(encoding="utf-8"))]


# ---------------------------------------------------------------------------
# Everything, in order
# ---------------------------------------------------------------------------

def handle_build(ctx: JobContext) -> dict[str, Any]:
    """Segment -> narrate -> images -> render, resuming per stage."""
    stages = ctx.checkpoint.setdefault("stages", [])
    results: dict[str, Any] = ctx.checkpoint.setdefault("results", {})

    # Media first: a soundbite scene's audio is extracted from its clip, so the
    # footage has to exist before the narration timeline can be built.
    plan = [("segment", handle_segment), ("images", handle_images),
            ("narrate", handle_narrate), ("render", handle_render)]
    if ctx.params.get("skip_segment"):
        plan = plan[1:]

    for name, handler in plan:
        if name in stages:
            continue
        ctx.check_stop()
        ctx.progress(len(stages) / len(plan), f"stage: {name}", force=True)
        results[name] = handler(ctx)
        stages.append(name)
        ctx.set_state("stages", stages)
        ctx.set_state("results", results)
    return results


HANDLERS = {
    "segment": handle_segment,
    "narrate": handle_narrate,
    "images": handle_images,
    "render": handle_render,
    "build": handle_build,
}
