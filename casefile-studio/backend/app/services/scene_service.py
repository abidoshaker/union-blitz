"""Single-scene operations: preview it, re-record it, replace its picture.

An hour-long project is 200-plus scenes and a full render is hours. Almost all
real editing is therefore one scene at a time - listen to that line, look at
that clip, swap that photograph - and every one of these has to work without
touching the other 200.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from sqlmodel import Session, select

from .. import ffmpeg
from ..config import settings
from ..models import Asset, Scene
from ..providers.image import AssetCandidate, get_provider as get_image_provider
from ..providers.tts import TTSOpts, get_provider as get_tts_provider
from . import align_service, image_service, query, render_service, segmentation
from . import subtitle_service, tts_service, video_service, vision

log = logging.getLogger("casefile.scene")


def preview_dir(project_id: int) -> Path:
    path = settings.project_dir(project_id) / "previews"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _cfg(session: Session, project_id: int) -> dict[str, Any]:
    from ..models import Project

    from .pipeline import project_settings

    project = session.get(Project, project_id)
    if not project:
        raise ValueError("Project not found.")
    return project_settings(project)


# ---------------------------------------------------------------------------
# Preview one scene, exactly as it will appear
# ---------------------------------------------------------------------------

def render_preview(session: Session, scene: Scene, *, force: bool = False) -> Path:
    """Render this one scene to a playable clip, with its own audio muxed in.

    This is the finished article for that scene - Ken Burns, burned captions,
    overlays, face blur - not an approximation, because the point is to decide
    whether it is right before committing to hours of rendering.

    The crossfade tail is deliberately dropped: a preview should end on its own
    last frame rather than half-way into the next scene's picture.
    """
    cfg = _cfg(session, int(scene.project_id))
    from .pipeline import _render_opts

    opts = _render_opts(cfg)

    visual = session.get(Asset, scene.asset_id) if scene.asset_id else None
    audio = session.get(Asset, scene.audio_asset_id) if scene.audio_asset_id else None
    if not visual or not Path(visual.local_path).exists():
        raise ValueError("This scene has no picture yet. Source an image first.")

    picture = visual.blurred_path if (scene.blur_faces and visual.blurred_path) else visual.local_path

    audio_path = Path(audio.local_path) if audio and Path(audio.local_path).exists() else None
    seconds = audio.duration if audio else segmentation.estimate_runtime(scene.text)
    seconds = max(1.0, float(seconds or 1.0))
    frames = max(1, int(round(seconds * opts.fps)))

    words = [align_service.Word(t, s, e) for t, s, e in (scene.words_json or [])]
    if not words:
        words = align_service.proportional(scene.text, seconds)

    blur_tracks: list = []
    poster = None
    if scene.media_kind == "video":
        source = Path(picture)
        poster = source.with_suffix(".jpg")
        if not poster.exists():
            ffmpeg.run(["-i", str(source), "-frames:v", "1", "-q:v", "3", str(poster)])
        if scene.blur_faces:
            try:
                blur_tracks = video_service.face_tracks(source)
            except Exception as exc:
                log.warning("no face regions for preview of scene %s: %s", scene.id, exc)

    spec = render_service.SceneSpec(
        index=int(scene.order_index),
        image=Path(picture),
        frames=frames,
        kenburns=scene.kenburns or "auto",
        words=words,
        banner=str(cfg.get("disclaimer_text") or "") if scene.ai_disclaimer else "",
        media_kind=scene.media_kind,
        media_in=float(scene.media_in or 0.0),
        media_duration=float(visual.duration or 0.0),
        poster=poster,
        blur_tracks=blur_tracks,
    )
    render_service.plan_transitions([spec], opts)   # single scene: no outgoing fade

    work = preview_dir(int(scene.project_id))
    clip, cached = render_service.render_scene_clip(spec, work, opts, force=force)

    dest = work / f"scene_{scene.id}_preview.mp4"
    if cached and dest.exists() and not force:
        return dest

    if audio_path:
        ffmpeg.run([
            "-i", str(clip.resolve()), "-i", str(audio_path.resolve()),
            "-c:v", "copy", "-c:a", "aac", "-b:a", "160k",
            "-map", "0:v:0", "-map", "1:a:0", "-shortest",
            "-movflags", "+faststart", str(dest.resolve()),
        ])
    else:
        ffmpeg.run(["-i", str(clip.resolve()), "-c", "copy",
                    "-movflags", "+faststart", str(dest.resolve())])
    return dest


# ---------------------------------------------------------------------------
# Re-record one scene
# ---------------------------------------------------------------------------

def regenerate_audio(session: Session, scene: Scene, *, voice_id: str | None = None,
                     provider_name: str | None = None) -> dict[str, Any]:
    """Synthesise this scene's line again, optionally in a different voice."""
    project_id = int(scene.project_id)
    cfg = _cfg(session, project_id)
    provider_name = provider_name or str(cfg["tts_provider"])
    voice_id = voice_id or str(cfg["voice_id"])

    provider = get_tts_provider(provider_name)
    usable, reason = provider.available()
    if not usable:
        raise ValueError(reason)

    if scene.audio_mode == "soundbite" and scene.asset_id:
        # The footage speaks here, so "re-record" means re-extracting the
        # clip's audio rather than calling a voice.
        visual = session.get(Asset, scene.asset_id)
        if visual and Path(visual.local_path).exists():
            dest = settings.project_dir(project_id) / "audio" / f"soundbite_{scene.id}.wav"
            duration = video_service.extract_audio(Path(visual.local_path), dest)
            return _attach_audio(session, scene, dest, duration, "clip")

    result = tts_service.synthesize_one(
        project_id=project_id, scene_id=int(scene.id), text=scene.text,
        provider=provider, voice_id=voice_id,
        opts=TTSOpts(sample_rate=tts_service.SAMPLE_RATE),
    )
    return _attach_audio(session, scene, result.path, result.duration, provider_name,
                         cost=result.cost)


def _attach_audio(session: Session, scene: Scene, path: Path, duration: float,
                  provider: str, *, cost: float = 0.0) -> dict[str, Any]:
    asset = Asset(
        project_id=scene.project_id, type="audio", source_provider=provider,
        local_path=str(path), duration=duration, content_hash=path.stem,
    )
    session.add(asset)
    session.flush()
    scene.audio_asset_id = int(asset.id)
    scene.duration = duration
    scene.status = "audio_ready"
    # Timings and word positions belong to the old take.
    scene.words_json = []
    scene.start_time = 0.0
    scene.end_time = 0.0
    session.add(scene)
    session.commit()
    return {"asset_id": int(asset.id), "duration": round(duration, 2), "cost_usd": round(cost, 4)}


# ---------------------------------------------------------------------------
# Change the picture
# ---------------------------------------------------------------------------

def search_for_scene(scene: Scene, *, provider_name: str, count: int = 12,
                     custom_query: str | None = None) -> dict[str, Any]:
    """Candidates for this scene, using the same ladder the auto pass uses."""
    provider = get_image_provider(provider_name)
    usable, reason = provider.available()
    if not usable:
        raise ValueError(reason)

    if custom_query:
        terms = [custom_query]
    else:
        terms = query.build(scene.text, scene.image_prompt).ladder(
            prefer_archival=scene.depicts_real_person
        )[:4]

    for term in terms:
        try:
            results = provider.search(term, count=count)
        except Exception as exc:
            log.debug("scene search %r failed: %s", term, exc)
            continue
        if results:
            return {
                "query": term,
                "tried": terms[: terms.index(term) + 1],
                "results": [_shape_candidate(c) for c in results],
            }
    return {"query": terms[0] if terms else "", "tried": terms, "results": []}


def _shape_candidate(c: AssetCandidate) -> dict[str, Any]:
    return {
        "url": c.url, "thumb": c.thumb or c.url, "license": c.license,
        "attribution": c.attribution, "provider": c.provider,
        "width": c.width, "height": c.height, "title": c.title,
        "kind": c.kind, "duration": c.duration,
    }


def apply_image(session: Session, scene: Scene, candidate: AssetCandidate) -> dict[str, Any]:
    """Store a chosen picture against this scene, blurring faces if required."""
    project_id = int(scene.project_id)
    image_service.real_person_guard(
        depicts_real_person=scene.depicts_real_person,
        provider_name=candidate.provider or "upload",
    )

    stored = image_service.store_candidate(project_id, candidate)

    blurred_path, faces = "", 0
    if scene.blur_faces and vision.available()[0]:
        try:
            target = stored.path.with_name(stored.path.stem + "_deid.jpg")
            faces = vision.blur_image_faces(stored.path, target)
            blurred_path = str(target)
        except Exception as exc:
            log.warning("face blur failed on %s: %s", stored.path.name, exc)

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

    scene.asset_id = int(asset.id)
    scene.media_kind = "image"
    # A picture you supplied yourself - by file or by link - is not stock, and
    # the provenance column should not claim it is.
    scene.visual_source = "upload" if stored.provider in ("upload", "manual") else "stock"
    if stored.provider in ("upload", "manual"):
        scene.match_level = "subject"    # you chose it, so it is the subject
        scene.source_query = "chosen by hand"
    session.add(scene)
    session.commit()

    _drop_preview(project_id, int(scene.id))
    return {
        "asset_id": int(asset.id), "faces_blurred": faces,
        "license": stored.license, "attribution": stored.attribution,
    }


def generate_image(session: Session, scene: Scene, *, provider_name: str,
                   prompt: str | None = None) -> dict[str, Any]:
    """Generate a picture for one scene and attach it."""
    image_service.real_person_guard(
        depicts_real_person=scene.depicts_real_person, provider_name=provider_name,
    )
    provider = get_image_provider(provider_name)
    usable, reason = provider.available()
    if not usable:
        raise ValueError(reason)
    if provider.kind != "ai":
        raise ValueError(f"{provider.label} does not generate images - search it instead.")

    wording = (prompt or scene.image_prompt or scene.text[:160]).strip()
    candidate = provider.generate(wording)
    result = apply_image(session, scene, candidate)

    scene.visual_source = "ai"
    scene.match_level = "atmosphere"
    scene.source_query = wording[:160]
    session.add(scene)
    session.commit()
    return {**result, "prompt": wording}


def fetch_from_url(session: Session, scene: Scene, url: str, *,
                   attribution: str = "") -> dict[str, Any]:
    """Use a picture from a URL you already have the rights to."""
    if not url.lower().startswith(("http://", "https://")):
        raise ValueError("That does not look like a web address.")
    candidate = AssetCandidate(
        url=url, provider="manual",
        license="Supplied by the user - you are responsible for the rights",
        attribution=attribution or "Supplied by the user",
        title=url.rsplit("/", 1)[-1][:120],
    )
    result = apply_image(session, scene, candidate)
    scene.match_level = "subject"     # a picture chosen by hand is the subject
    scene.source_query = "chosen by hand"
    session.add(scene)
    session.commit()
    return result


def _drop_preview(project_id: int, scene_id: int) -> None:
    """A scene that changed must not keep showing its old preview."""
    work = preview_dir(project_id)
    for stale in work.glob(f"scene_{scene_id}_preview.mp4"):
        stale.unlink(missing_ok=True)


def invalidate_preview(project_id: int, scene_id: int) -> None:
    _drop_preview(project_id, scene_id)
