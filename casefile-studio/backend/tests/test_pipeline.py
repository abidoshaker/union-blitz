"""End-to-end: paste a script, get a rendered MP4.

Runs entirely offline using the draft (silent) voice and placeholder images, so
it needs no API keys and no model downloads - only FFmpeg.
"""

from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path

import pytest

os.environ.setdefault("CASEFILE_DATA_DIR", tempfile.mkdtemp(prefix="casefile-test-"))

from app import ffmpeg  # noqa: E402
from app.services import render_service, segmentation  # noqa: E402

SCRIPT = """Vincent Moretti was twenty-three when he first walked into the warehouse on Mercer Street. It was February of 1974, and the city was still deep in the worst winter it had seen in a decade.

The ledger on the desk was open. Nobody had thought to close it. Det. Halloran would later testify that this single oversight unravelled eleven years of careful work.

Moretti did not run. He sat down, poured himself a drink, and waited for the men who were coming. That decision defined everything that followed.

By 1981 the organisation had grown past anything the family could control. Money moved through four states. The names in that ledger belonged to people who had never met each other.

The end came quietly, on a Tuesday, in a courtroom that was almost empty."""


def _needs_ffmpeg():
    return pytest.mark.skipif(not ffmpeg.capabilities().ok,
                              reason="needs FFmpeg with libx264 and libass")


# ---------------------------------------------------------------------------
# Segmentation
# ---------------------------------------------------------------------------

def test_scenes_tile_the_script_exactly():
    """The narration must be the author's bytes, not a paraphrase."""
    drafts, method = segmentation.segment(SCRIPT, llm_name="heuristic")
    assert drafts
    rebuilt = "".join(SCRIPT[d.start : d.end] for d in drafts)
    assert rebuilt == SCRIPT
    assert method == "heuristic"


def test_abbreviations_do_not_split_sentences():
    sentences = segmentation.split_sentences("Det. Halloran met Dr. Weiss on Jan. 4. They spoke.")
    assert [s.text for s in sentences] == [
        "Det. Halloran met Dr. Weiss on Jan. 4.",
        "They spoke.",
    ]


def test_hour_long_script_stays_under_the_scene_cap():
    long_script = (SCRIPT + "\n\n") * 60          # ~9,000 words
    drafts, _ = segmentation.segment(long_script, llm_name="heuristic")
    assert len(drafts) <= 400
    assert "".join(long_script[d.start : d.end] for d in drafts) == long_script


# ---------------------------------------------------------------------------
# Timing
# ---------------------------------------------------------------------------

def test_frame_plan_tracks_the_audio_timeline():
    """Per-scene rounding would drift over 240 scenes; boundary rounding cannot."""
    starts = [i * 12.37 for i in range(240)]
    total = 240 * 12.37
    frames = render_service.frame_plan(starts, total, 30)
    assert sum(frames) == round(total * 30)


def test_ad_breaks_land_on_scene_boundaries():
    starts = [i * 15.0 for i in range(240)]
    breaks = render_service.suggest_ad_breaks(starts, 3600.0)
    assert breaks
    assert all(b in starts for b in breaks)
    assert all(120 < b < 3480 for b in breaks)


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------

def test_real_person_guard_blocks_ai_generation():
    from app.services.image_service import RealPersonBlocked, real_person_guard

    with pytest.raises(RealPersonBlocked):
        real_person_guard(depicts_real_person=True, provider_name="placeholder")
    real_person_guard(depicts_real_person=True, provider_name="wikimedia")
    real_person_guard(depicts_real_person=False, provider_name="placeholder")


def test_spend_ceiling_stops_a_runaway_batch():
    from app.services.tts_service import CostMeter, SpendCeilingReached

    meter = CostMeter(1.00)
    meter.reserve(0.60)
    with pytest.raises(SpendCeilingReached):
        meter.reserve(0.60)


# ---------------------------------------------------------------------------
# Full render
# ---------------------------------------------------------------------------

@_needs_ffmpeg()
def test_build_a_video_end_to_end():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        created = client.post("/api/projects", json={
            "title": "Test case",
            "settings": {
                "tts_provider": "draft", "voice_id": "silence",
                "visual_source": "placeholder", "llm_provider": "heuristic",
                "width": 640, "height": 360, "fps": 24, "preset": "ultrafast",
                "image_pool_per_chapter": 3,
            },
        })
        assert created.status_code == 201
        project_id = created.json()["id"]

        script = client.post(f"/api/projects/{project_id}/script", json={"raw_text": SCRIPT})
        assert script.status_code == 200
        assert script.json()["word_count"] > 100

        started = client.post(f"/api/projects/{project_id}/run/build", json={})
        assert started.status_code == 202
        job_id = started.json()["job_id"]

        deadline = time.time() + 300
        job = {}
        while time.time() < deadline:
            job = client.get(f"/api/jobs/{job_id}").json()
            if job["status"] in ("done", "error", "canceled"):
                break
            time.sleep(0.5)

        assert job["status"] == "done", job.get("error")

        renders = client.get(f"/api/projects/{project_id}/renders").json()
        assert renders
        output = Path(renders[0]["path"])
        assert output.exists() and output.stat().st_size > 10_000

        # Video and audio must be the same length: the frame plan is derived
        # from the audio timeline precisely so this holds.
        info = ffmpeg.probe(output)
        video = next(s for s in info["streams"] if s["codec_type"] == "video")
        audio = next(s for s in info["streams"] if s["codec_type"] == "audio")
        assert abs(float(video["duration"]) - float(audio["duration"])) < 0.5
        assert video["codec_name"] == "h264"
        assert audio["codec_name"] == "aac"

        scenes = client.get(f"/api/projects/{project_id}/scenes").json()
        assert scenes["total"] > 0
        assert all(s["has_audio"] and s["has_image"] for s in scenes["items"])


@_needs_ffmpeg()
def test_rerender_reuses_cached_clips():
    """Editing nothing should re-render nothing."""
    from app.providers.image import get_provider
    from app.services.align_service import proportional

    work = Path(tempfile.mkdtemp(prefix="casefile-cache-"))
    candidate = get_provider("placeholder").generate("a quiet street")
    image = work / "img.jpg"
    image.write_bytes(candidate.extra["bytes"])

    opts = render_service.RenderOpts(width=320, height=180, fps=12, preset="ultrafast")
    spec = render_service.SceneSpec(
        index=0, image=image, frames=24, words=proportional("Two short words here.", 2.0)
    )
    render_service.plan_transitions([spec], opts)

    _, cached_first = render_service.render_scene_clip(spec, work / "clips", opts)
    _, cached_second = render_service.render_scene_clip(spec, work / "clips", opts)
    assert cached_first is False
    assert cached_second is True
