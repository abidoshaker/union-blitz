"""Stopping things, deleting things, and finding things.

The complaints these cover: a project that would not delete, a Cancel that
sat on "cancelling", and no way to reach one scene out of two hundred without
scrolling to it.
"""

from __future__ import annotations

import os
import tempfile
import threading
import time

import pytest

os.environ.setdefault("CASEFILE_DATA_DIR", tempfile.mkdtemp(prefix="casefile-control-"))

from fastapi.testclient import TestClient       # noqa: E402

from app import ffmpeg                          # noqa: E402
from app.main import app                        # noqa: E402
from app.queue.base import JobCanceled          # noqa: E402
from app.services.pipeline import image_sources, video_sources, _rotate  # noqa: E402

needs_ffmpeg = pytest.mark.skipif(
    not ffmpeg.capabilities().ok, reason="needs FFmpeg with libx264 and libass"
)

SCRIPT = (
    "The shipment left the container port at Cartagena in March of 1991. "
    "Federal agents had been listening to the wiretap for nine long weeks. "
    "The arrest took less than forty seconds in the courtyard of the compound. "
) * 4

FAST = {
    "tts_provider": "draft", "voice_id": "silence", "visual_source": "placeholder",
    "llm_provider": "heuristic", "width": 320, "height": 180, "fps": 12,
    "preset": "ultrafast", "image_pool_per_chapter": 2,
}


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def _wait(client, job_id: int, seconds: int = 240) -> dict:
    deadline = time.time() + seconds
    status: dict = {}
    while time.time() < deadline:
        status = client.get(f"/api/jobs/{job_id}").json()
        if status["status"] in ("done", "error", "canceled"):
            return status
        time.sleep(0.2)
    raise AssertionError(f"job {job_id} did not finish: {status}")


def _built(client, title: str, stages=("segment", "images", "narrate")) -> int:
    pid = client.post("/api/projects", json={"title": title, "settings": FAST}).json()["id"]
    client.post(f"/api/projects/{pid}/script", json={"raw_text": SCRIPT})
    for stage in stages:
        assert _wait(client, client.post(f"/api/projects/{pid}/run/{stage}", json={})
                     .json()["job_id"])["status"] == "done"
    return pid


# ---------------------------------------------------------------------------
# Cancelling
# ---------------------------------------------------------------------------

@needs_ffmpeg
def test_ffmpeg_is_killed_when_the_job_is_cancelled(tmp_path):
    """Cancel used to wait out whatever encode was in flight.

    A scene clip is seconds to a minute, and the final assembly is a pass over
    the whole hour, so "between scenes" was nowhere near often enough.
    """
    flag = {"stop": False}

    def should_stop():
        if flag["stop"]:
            raise JobCanceled()

    threading.Timer(1.5, lambda: flag.__setitem__("stop", True)).start()
    started = time.monotonic()
    with pytest.raises(JobCanceled):
        ffmpeg.run(
            ["-f", "lavfi", "-i", "testsrc=size=640x360:rate=30:duration=600",
             "-c:v", "libx264", "-preset", "veryslow", "-crf", "18",
             str(tmp_path / "long.mp4")],
            should_stop=should_stop,
        )
    # 1.5s trigger plus at most one 0.25s poll, and generous slack for CI.
    assert time.monotonic() - started < 6.0


@needs_ffmpeg
def test_a_cancelled_encode_does_not_leave_ffmpeg_running(tmp_path):
    import subprocess

    flag = {"stop": False}

    def should_stop():
        if flag["stop"]:
            raise JobCanceled()

    marker = "casefile_cancel_probe=1"
    threading.Timer(1.0, lambda: flag.__setitem__("stop", True)).start()
    with pytest.raises(JobCanceled):
        ffmpeg.run(
            ["-f", "lavfi", "-i", "testsrc=size=320x180:rate=30:duration=600",
             "-metadata", f"comment={marker}",
             "-c:v", "libx264", "-preset", "veryslow", str(tmp_path / "x.mp4")],
            should_stop=should_stop,
        )
    time.sleep(0.5)
    found = subprocess.run(["pgrep", "-f", marker], capture_output=True, text=True)
    # pgrep matches this test process's own argv too, so look for ffmpeg only.
    stray = [
        pid for pid in found.stdout.split()
        if "ffmpeg" in subprocess.run(["ps", "-p", pid, "-o", "comm="],
                                      capture_output=True, text=True).stdout
    ]
    assert not stray, f"ffmpeg survived the cancel: {stray}"


def test_cancelling_a_queued_job_lands_immediately(client):
    pid = client.post("/api/projects", json={"title": "Queued", "settings": FAST}).json()["id"]
    client.post(f"/api/projects/{pid}/script", json={"raw_text": SCRIPT})
    job = client.post(f"/api/projects/{pid}/run/segment", json={}).json()["job_id"]
    client.post(f"/api/jobs/{job}/cancel")
    assert _wait(client, job, 30)["status"] in ("canceled", "done")


# ---------------------------------------------------------------------------
# Deleting
# ---------------------------------------------------------------------------

def test_a_finished_project_can_be_deleted(client):
    pid = _built(client, "Finished")
    res = client.delete(f"/api/projects/{pid}")
    assert res.status_code == 200, res.text
    assert res.json()["freed_bytes"] >= 0
    assert client.get(f"/api/projects/{pid}").status_code == 404
    assert not any(p["id"] == pid for p in client.get("/api/projects").json())


def test_deleting_stops_the_work_first(client):
    """Deleting out from under a running job used to leave it thrashing.

    The handler carried on scene by scene, failing every insert on a foreign
    key to a project that no longer existed.
    """
    pid = _built(client, "Busy", stages=("segment",))
    job = client.post(f"/api/projects/{pid}/run/images", json={}).json()["job_id"]

    res = client.delete(f"/api/projects/{pid}")
    assert res.status_code == 200, res.text

    # The job is gone with the project, and nothing is still writing.
    assert client.get(f"/api/jobs/{job}").status_code == 404
    time.sleep(1.5)
    assert client.get(f"/api/projects/{pid}").status_code == 404


def test_deleting_a_project_that_is_not_there_is_a_404(client):
    assert client.delete("/api/projects/999999").status_code == 404


# ---------------------------------------------------------------------------
# Searching
# ---------------------------------------------------------------------------

def test_a_scene_can_be_found_by_its_words(client):
    pid = _built(client, "Searchable", stages=("segment",))
    hit = client.get(f"/api/projects/{pid}/scenes", params={"q": "Cartagena"}).json()
    assert hit["total"] >= 1
    assert all("cartagena" in s["text"].lower() or "cartagena" in s["image_prompt"].lower()
               for s in hit["items"])

    miss = client.get(f"/api/projects/{pid}/scenes", params={"q": "zeppelin"}).json()
    assert miss["total"] == 0
    assert miss["items"] == []


def test_search_is_case_insensitive_and_matches_part_of_a_word(client):
    pid = _built(client, "Searchable2", stages=("segment",))
    assert client.get(f"/api/projects/{pid}/scenes",
                      params={"q": "CARTAGEN"}).json()["total"] >= 1


def test_search_combines_with_a_filter(client):
    pid = _built(client, "Searchable3", stages=("segment",))
    both = client.get(f"/api/projects/{pid}/scenes",
                      params={"q": "wiretap", "filter": "needs_image"}).json()
    everything = client.get(f"/api/projects/{pid}/scenes",
                            params={"filter": "needs_image"}).json()
    assert both["total"] <= everything["total"]


# ---------------------------------------------------------------------------
# Choosing several sources
# ---------------------------------------------------------------------------

def test_a_project_with_no_list_still_uses_its_single_source():
    """Projects saved before the list existed must keep working untouched."""
    assert image_sources({"visual_source": "pexels"}) == ["pexels"]
    assert video_sources({"video_provider": "pixabay_video"}) == ["pixabay_video"]


def test_the_list_wins_when_it_is_set():
    cfg = {"visual_source": "pexels", "image_sources": ["nara", "loc", "wikimedia"]}
    assert image_sources(cfg) == ["nara", "loc", "wikimedia"]


def test_duplicates_and_blanks_are_dropped():
    assert image_sources({"image_sources": ["pexels", "pexels", "", "  ", "loc"]}) == \
        ["pexels", "loc"]


def test_a_single_string_is_accepted_as_a_list_of_one():
    assert image_sources({"image_sources": "pexels"}) == ["pexels"]


def test_the_starting_source_rotates_so_one_library_does_not_supply_everything():
    names = ["pexels", "pixabay", "wikimedia"]
    starts = {_rotate(names, i)[0] for i in range(6)}
    assert starts == set(names)
    # Every scene still gets the whole list, only in a different order.
    for i in range(6):
        assert sorted(_rotate(names, i)) == sorted(names)


def test_a_generator_is_never_rotated_in_front_of_a_real_library():
    """The placeholder card and AI always succeed, so they end the search.

    Rotated to the front they would answer for a third of the scenes and the
    archives would never be asked - the opposite of picking several sources.
    """
    names = ["pexels", "placeholder", "wikimedia"]
    for i in range(6):
        order = _rotate(names, i)
        assert order[-1] == "placeholder", order
        assert set(order) == set(names)


def test_rotation_is_a_no_op_for_a_single_source():
    assert _rotate(["pexels"], 7) == ["pexels"]
    assert _rotate([], 3) == []
    assert _rotate(["placeholder"], 3) == ["placeholder"]


def test_batch_sourcing_can_be_told_which_libraries_to_use(client):
    pid = _built(client, "Batch", stages=("segment",))
    scenes = client.get(f"/api/projects/{pid}/scenes").json()["items"][:3]
    ids = [s["id"] for s in scenes]

    body = {"project_id": pid, "scene_ids": ids, "op": "swap-images",
            "payload": {"image_sources": ["placeholder"]}}
    preview = client.post("/api/batch/preview", json=body).json()
    assert preview["scene_count"] == len(ids)

    applied = client.post("/api/batch/apply", json=body)
    assert applied.status_code == 202, applied.text
    assert applied.json()["applied"] == len(ids)
    assert _wait(client, applied.json()["job_id"])["status"] == "done"


def test_filling_in_the_gaps_leaves_chosen_pictures_alone(client):
    """`source-images` must never discard a picture someone chose by hand."""
    pid = _built(client, "Fill")
    scenes = client.get(f"/api/projects/{pid}/scenes").json()["items"]
    assert all(s["has_image"] for s in scenes)

    body = {"project_id": pid, "scene_ids": [s["id"] for s in scenes],
            "op": "source-images", "payload": {}}
    assert client.post("/api/batch/preview", json=body).json()["affected"] == 0
    # Nothing to do, and it says so rather than quietly re-sourcing everything.
    assert client.post("/api/batch/apply", json=body).status_code == 400


def test_batch_voice_reaches_the_narrate_job(client):
    pid = _built(client, "BatchVoice")
    ids = [s["id"] for s in client.get(f"/api/projects/{pid}/scenes").json()["items"][:2]]
    body = {"project_id": pid, "scene_ids": ids, "op": "regenerate-audio",
            "payload": {"tts_provider": "draft", "voice_id": "silence"}}
    res = client.post("/api/batch/apply", json=body)
    assert res.status_code == 202, res.text
    assert _wait(client, res.json()["job_id"])["status"] == "done"


def test_the_settings_endpoint_lists_both_kinds_of_source(client):
    body = client.get("/api/settings").json()
    assert body["images"] and body["video"]
    archival = {p["name"] for p in body["images"] if p.get("is_archival")}
    # The distinction the picker's green dot depends on.
    assert "nara" in archival and "loc" in archival
    assert "placeholder" not in archival
