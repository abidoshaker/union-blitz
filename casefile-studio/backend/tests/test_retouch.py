"""Per-scene retouching, previewing, and clearing up afterwards.

These are the operations you reach for constantly on a 200-scene project:
watch one scene, re-record one line, swap one picture, then reclaim the disk
when the video is finished.
"""

from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path

import pytest

os.environ.setdefault("CASEFILE_DATA_DIR", tempfile.mkdtemp(prefix="casefile-retouch-"))

from fastapi.testclient import TestClient  # noqa: E402

from app import ffmpeg  # noqa: E402
from app.config import settings  # noqa: E402
from app.main import app  # noqa: E402

needs_ffmpeg = pytest.mark.skipif(
    not ffmpeg.capabilities().ok, reason="needs FFmpeg with libx264 and libass"
)

SCRIPT = (
    "The detective opened the case file on a cold Tuesday morning and set it down on the desk. "
    "He had waited eleven long years for somebody to bring him this particular folder. "
    "The tape recording was played to the courtroom in full, without any interruption at all. "
) * 3


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def _wait(client, job_id: int, seconds: int = 180) -> dict:
    deadline = time.time() + seconds
    status: dict = {}
    while time.time() < deadline:
        status = client.get(f"/api/jobs/{job_id}").json()
        if status["status"] in ("done", "error", "canceled"):
            return status
        time.sleep(0.25)
    raise AssertionError(f"job {job_id} did not finish: {status}")


@pytest.fixture(scope="module")
def project(client):
    pid = client.post("/api/projects", json={"title": "Retouch", "settings": {
        "tts_provider": "draft", "voice_id": "silence", "visual_source": "placeholder",
        "llm_provider": "heuristic", "width": 480, "height": 270, "fps": 12,
        "preset": "ultrafast", "image_pool_per_chapter": 2,
    }}).json()["id"]
    client.post(f"/api/projects/{pid}/script", json={"raw_text": SCRIPT})
    for stage in ("segment", "images", "narrate"):
        job = client.post(f"/api/projects/{pid}/run/{stage}", json={}).json()["job_id"]
        assert _wait(client, job)["status"] == "done"
    return pid


def _scenes(client, project_id: int) -> list[dict]:
    return client.get(f"/api/projects/{project_id}/scenes").json()["items"]


# ---------------------------------------------------------------------------
# Watch one scene before committing to a full render
# ---------------------------------------------------------------------------

@needs_ffmpeg
def test_a_single_scene_can_be_watched_before_any_render(client, project):
    scene = _scenes(client, project)[0]
    res = client.post(f"/api/scenes/{scene['id']}/preview")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["duration"] > 0
    assert body["size_bytes"] > 5_000

    played = client.get(body["url"])
    assert played.status_code == 200
    assert played.headers["content-type"] == "video/mp4"


@needs_ffmpeg
def test_preview_length_matches_the_scene_audio(client, project):
    scene = _scenes(client, project)[1]
    body = client.post(f"/api/scenes/{scene['id']}/preview").json()
    assert abs(body["duration"] - scene["duration"]) < 0.3


def test_preview_refuses_a_scene_with_no_picture(client, project):
    from sqlmodel import Session

    from app.db import engine
    from app.models import Scene

    scene_id = _scenes(client, project)[2]["id"]
    with Session(engine) as session:
        scene = session.get(Scene, scene_id)
        keep, scene.asset_id = scene.asset_id, None
        session.add(scene)
        session.commit()
    try:
        res = client.post(f"/api/scenes/{scene_id}/preview")
        assert res.status_code == 400
        assert "picture" in res.json()["detail"].lower()
    finally:
        with Session(engine) as session:
            scene = session.get(Scene, scene_id)
            scene.asset_id = keep
            session.add(scene)
            session.commit()


# ---------------------------------------------------------------------------
# Retouch one scene
# ---------------------------------------------------------------------------

def test_re_recording_one_scene_replaces_only_that_scene(client, project):
    before = _scenes(client, project)
    target = before[0]

    res = client.post(f"/api/scenes/{target['id']}/regenerate-audio", json={})
    assert res.status_code == 200, res.text
    assert res.json()["duration"] > 0

    after = _scenes(client, project)
    assert after[0]["has_audio"]
    # Every other scene keeps the audio it already had.
    for old, new in zip(before[1:], after[1:]):
        assert old["duration"] == new["duration"]


def test_re_recording_clears_stale_word_timings(client, project):
    from sqlmodel import Session

    from app.db import engine
    from app.models import Scene

    scene_id = _scenes(client, project)[0]["id"]
    with Session(engine) as session:
        scene = session.get(Scene, scene_id)
        scene.words_json = [["stale", 0.0, 1.0]]
        scene.start_time = 99.0
        session.add(scene)
        session.commit()

    client.post(f"/api/scenes/{scene_id}/regenerate-audio", json={})

    with Session(engine) as session:
        scene = session.get(Scene, scene_id)
        assert scene.words_json == []
        assert scene.start_time == 0.0


def test_uploading_your_own_picture_replaces_the_sourced_one(client, project):
    fixture = Path(__file__).parent / "data" / "face.jpg"
    if not fixture.exists():
        pytest.skip("no image fixture available")

    scene = _scenes(client, project)[1]
    before = scene["image_url"]

    with open(fixture, "rb") as fh:
        res = client.post(
            f"/api/scenes/{scene['id']}/upload-image",
            files={"file": ("mine.jpg", fh, "image/jpeg")},
        )
    assert res.status_code == 200, res.text

    after = _scenes(client, project)[1]
    assert after["image_url"] != before
    assert after["license"] == "Uploaded by the user"


def test_changing_the_picture_drops_the_stale_preview(client, project):
    fixture = Path(__file__).parent / "data" / "face.jpg"
    if not fixture.exists():
        pytest.skip("no image fixture available")

    scene = _scenes(client, project)[0]
    if ffmpeg.capabilities().ok:
        client.post(f"/api/scenes/{scene['id']}/preview")
        preview = settings.project_dir(project) / "previews" / f"scene_{scene['id']}_preview.mp4"
        assert preview.exists()

        with open(fixture, "rb") as fh:
            client.post(f"/api/scenes/{scene['id']}/upload-image",
                        files={"file": ("mine.jpg", fh, "image/jpeg")})
        # Showing the old preview after the picture changed would be a lie.
        assert not preview.exists()


def test_search_reports_which_query_it_used(client, project):
    scene = _scenes(client, project)[0]
    res = client.post(f"/api/scenes/{scene['id']}/search-images",
                      json={"provider": "placeholder"})
    assert res.status_code == 200
    body = res.json()
    assert "query" in body and "results" in body


# ---------------------------------------------------------------------------
# Clearing up
# ---------------------------------------------------------------------------

def test_usage_is_broken_down_by_what_it_is(client, project):
    usage = client.get(f"/api/projects/{project}/usage").json()
    assert usage["total_bytes"] > 0
    assert usage["source_bytes"] > 0
    for key in ("clip_bytes", "render_bytes", "preview_bytes"):
        assert key in usage


@needs_ffmpeg
def test_freeing_space_keeps_the_sources(client, project):
    client.post(f"/api/scenes/{_scenes(client, project)[0]['id']}/preview")
    before = client.get(f"/api/projects/{project}/usage").json()
    assert before["preview_bytes"] > 0

    freed = client.post(f"/api/projects/{project}/clear", json={}).json()
    assert freed["freed_bytes"] > 0

    after = client.get(f"/api/projects/{project}/usage").json()
    assert after["preview_bytes"] == 0
    # Downloads and narration survive: they cost money or time to recreate.
    assert after["source_bytes"] == before["source_bytes"]


def test_clearing_everything_keeps_the_script_and_scenes(client, project):
    scenes_before = len(_scenes(client, project))

    client.post(f"/api/projects/{project}/clear",
                json={"drop_renders": True, "drop_sources": True})

    after = _scenes(client, project)
    assert len(after) == scenes_before
    assert all(not s["has_image"] and not s["has_audio"] for s in after)
    assert client.get(f"/api/projects/{project}/script").json()["word_count"] > 0


def test_deleting_a_project_removes_its_files(client):
    pid = client.post("/api/projects", json={"title": "Doomed", "settings": {
        "llm_provider": "heuristic", "tts_provider": "draft", "visual_source": "placeholder",
    }}).json()["id"]
    client.post(f"/api/projects/{pid}/script", json={"raw_text": SCRIPT})
    job = client.post(f"/api/projects/{pid}/run/segment", json={}).json()["job_id"]
    assert _wait(client, job)["status"] == "done"

    directory = settings.project_dir(pid)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "assets").mkdir(exist_ok=True)
    (directory / "assets" / "junk.bin").write_bytes(b"x" * 4096)

    res = client.delete(f"/api/projects/{pid}")
    assert res.status_code == 200, res.text
    assert res.json()["freed_bytes"] >= 4096

    assert not directory.exists()
    assert client.get(f"/api/projects/{pid}").status_code == 404
    assert client.get(f"/api/projects/{pid}/scenes").json()["total"] == 0


def test_deleting_an_unknown_project_is_a_404(client):
    assert client.delete("/api/projects/999999").status_code == 404
