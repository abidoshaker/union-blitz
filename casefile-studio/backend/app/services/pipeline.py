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
from . import align_service, image_service, render_service, segmentation, subtitle_service, tts_service

log = logging.getLogger("casefile.pipeline")

DEFAULTS: dict[str, Any] = {
    "tts_provider": "draft",
    "voice_id": "silence",
    "llm_provider": None,
    "visual_source": "placeholder",
    "image_pool_per_chapter": 8,
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
        items = [(int(s.id), s.text) for s in scenes]

    if not items:
        return {"scenes": 0}

    ctx.progress(0.02, f"narrating {len(items)} scenes", force=True)
    results = tts_service.synthesize_batch(
        project_id=project_id,
        items=items,
        provider_name=str(cfg["tts_provider"]),
        voice_id=str(cfg["voice_id"]),
        opts=TTSOpts(sample_rate=tts_service.SAMPLE_RATE),
        spend_ceiling=cfg.get("spend_ceiling"),
        on_progress=lambda f, m: ctx.progress(0.02 + f * 0.95, m),
        should_stop=ctx.check_stop,
    )

    with session_scope() as session:
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

    total = sum(r.duration for r in results.values())
    cost = sum(r.cost for r in results.values())
    ctx.progress(1.0, "narration ready", force=True)
    return {
        "scenes": len(results),
        "cached": sum(1 for r in results.values() if r.cached),
        "audio_sec": round(total, 2),
        "cost_usd": round(cost, 4),
    }


# ---------------------------------------------------------------------------
# Images
# ---------------------------------------------------------------------------

def handle_images(ctx: JobContext) -> dict[str, Any]:
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
        chapters = {int(c.id): c for c in session.exec(
            select(Chapter).where(Chapter.project_id == project_id)).all()}
        rows = [
            {
                "id": int(s.id), "prompt": s.image_prompt, "chapter": s.chapter_id,
                "real": s.depicts_real_person, "has_asset": bool(s.asset_id),
                "source": s.visual_source,
            }
            for s in scenes
        ]

    provider_name = str(cfg["visual_source"])
    pool_size = int(cfg["image_pool_per_chapter"])
    by_chapter: dict[Any, list[dict]] = {}
    for row in rows:
        by_chapter.setdefault(row["chapter"], []).append(row)

    stored_total = 0
    failures: list[str] = []
    processed = 0

    for chapter_id, chapter_rows in by_chapter.items():
        ctx.check_stop()
        # Real-person scenes never share a generated pool; each is sourced from
        # archival material on its own.
        pooled = [r for r in chapter_rows if not r["real"]]
        solo = [r for r in chapter_rows if r["real"]]

        pool: list[image_service.StoredImage] = []
        if pooled:
            try:
                pool = image_service.build_chapter_pool(
                    project_id=project_id,
                    prompts=[r["prompt"] for r in pooled],
                    provider_name=provider_name,
                    pool_size=min(pool_size, len(pooled)),
                )
            except Exception as exc:
                failures.append(f"chapter {chapter_id}: {exc}")

        for i, row in enumerate(pooled):
            ctx.check_stop()
            processed += 1
            ctx.progress(processed / max(len(rows), 1), f"sourcing images {processed}/{len(rows)}")
            if str(row["id"]) in done or not pool:
                continue
            stored = pool[i % len(pool)]
            _attach_asset(project_id, row["id"], stored)
            ctx.mark_done("images", str(row["id"]))
            stored_total += 1

        for row in solo:
            ctx.check_stop()
            processed += 1
            ctx.progress(processed / max(len(rows), 1), f"sourcing archival image {processed}/{len(rows)}")
            if str(row["id"]) in done:
                continue
            archival = _archival_provider(cfg)
            try:
                stored = image_service.source_image(
                    project_id=project_id, prompt=row["prompt"],
                    provider_name=archival, depicts_real_person=True,
                )
            except Exception as exc:
                failures.append(f"scene {row['id']}: {exc}")
                continue
            _attach_asset(project_id, row["id"], stored)
            ctx.mark_done("images", str(row["id"]))
            stored_total += 1

    ctx.progress(1.0, "images ready", force=True)
    return {"images": stored_total, "failures": failures[:20]}


def _archival_provider(cfg: dict[str, Any]) -> str:
    from ..providers.image import ARCHIVAL_PROVIDERS, get_provider

    preferred = str(cfg.get("archival_source") or "")
    if preferred:
        return preferred
    for name in ARCHIVAL_PROVIDERS:
        if get_provider(name).available()[0]:
            return name
    return "wikimedia"


def _attach_asset(project_id: int, scene_id: int, stored: image_service.StoredImage) -> None:
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
            )
            session.add(asset)
            session.flush()
        scene = session.get(Scene, scene_id)
        if scene:
            scene.asset_id = int(asset.id)
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
        rows = [
            {
                "id": int(s.id), "index": s.order_index, "text": s.text,
                "audio": assets[s.audio_asset_id].local_path if s.audio_asset_id in assets else "",
                "image": assets[s.asset_id].local_path if s.asset_id in assets else "",
                "kenburns": s.kenburns, "chapter": s.chapter_id,
                "disclaimer": s.ai_disclaimer,
            }
            for s in scenes
        ]

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
        specs.append(render_service.SceneSpec(
            index=i,
            image=Path(row["image"]),
            frames=frames[i],
            kenburns=row["kenburns"] or "auto",
            words=words_by_scene[row["id"]],
            banner=disclaimer if row["disclaimer"] else "",
            lower_third=lower,
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
    master_audio = audio_service.build_master(
        narration=narration,
        dest=base / "audio" / f"master_{chapter_id or 'full'}.m4a",
        music=music if music and music.exists() else None,
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

    plan = [("segment", handle_segment), ("narrate", handle_narrate),
            ("images", handle_images), ("render", handle_render)]
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
