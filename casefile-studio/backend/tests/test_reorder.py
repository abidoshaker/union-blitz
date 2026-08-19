"""Reordering a paginated, chaptered project.

The storyboard only ever holds one page of a 240-scene project, so the risk
here is a partial list quietly renumbering the whole running order.
"""

from __future__ import annotations

import os
import tempfile

import pytest

os.environ.setdefault("CASEFILE_DATA_DIR", tempfile.mkdtemp(prefix="casefile-reorder-"))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

# Long enough to be split across several chapters: the chapter-contiguity
# behaviour is the subtlest part of reordering and must not skip.
SCRIPT = " ".join(
    f"In the winter of nineteen seventy four, entry number {i} sat in the ledger "
    f"on Mercer Street and nobody thought to close it."
    for i in range(400)
)


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def project(client):
    created = client.post("/api/projects", json={
        "title": "Reorder case",
        "settings": {"llm_provider": "heuristic", "tts_provider": "draft"},
    })
    pid = created.json()["id"]
    client.post(f"/api/projects/{pid}/script", json={"raw_text": SCRIPT})

    job = client.post(f"/api/projects/{pid}/run/segment", json={}).json()["job_id"]
    import time

    # The queue is shared with the other test modules, so this job can sit
    # behind a render. Waiting must fail loudly rather than handing back a
    # project with no scenes in it.
    deadline = time.time() + 180
    status = {}
    while time.time() < deadline:
        status = client.get(f"/api/jobs/{job}").json()
        if status["status"] in ("done", "error", "canceled"):
            break
        time.sleep(0.25)
    assert status.get("status") == "done", f"segment job did not finish: {status}"

    scenes = client.get(f"/api/projects/{pid}/scenes?limit=200").json()
    assert scenes["total"] >= 20, f"expected a multi-chapter project, got {scenes['total']} scenes"
    return pid


def order_of(client, pid: int) -> list[int]:
    page = client.get(f"/api/projects/{pid}/scenes?limit=200").json()
    return [s["id"] for s in page["items"]]


# ---------------------------------------------------------------------------

def test_partial_reorder_is_rejected(client, project):
    """The bug this guards: renumbering page one over the top of the project."""
    ids = order_of(client, project)
    assert len(ids) > 5

    res = client.post(f"/api/projects/{project}/scenes/reorder", json={"scene_ids": ids[:5]})
    assert res.status_code == 400
    assert "every scene" in res.json()["detail"]
    assert order_of(client, project) == ids     # nothing moved


def test_full_reorder_is_accepted(client, project):
    ids = order_of(client, project)
    reversed_ids = list(reversed(ids))
    res = client.post(f"/api/projects/{project}/scenes/reorder", json={"scene_ids": reversed_ids})
    assert res.status_code == 200
    assert order_of(client, project) == reversed_ids


def test_move_one_scene_down(client, project):
    ids = order_of(client, project)
    mover = ids[1]
    res = client.post(f"/api/projects/{project}/scenes/move",
                      json={"scene_ids": [mover], "to_index": 5})
    assert res.status_code == 200

    after = order_of(client, project)
    assert after.index(mover) == 5
    # Everything else keeps its relative order.
    assert [i for i in after if i != mover] == [i for i in ids if i != mover]


def test_move_one_scene_up(client, project):
    ids = order_of(client, project)
    mover = ids[6]
    client.post(f"/api/projects/{project}/scenes/move", json={"scene_ids": [mover], "to_index": 0})
    after = order_of(client, project)
    assert after[0] == mover
    assert [i for i in after if i != mover] == [i for i in ids if i != mover]


def test_move_many_keeps_their_relative_order(client, project):
    ids = order_of(client, project)
    movers = [ids[7], ids[2], ids[4]]          # deliberately out of order
    client.post(f"/api/projects/{project}/scenes/move",
                json={"scene_ids": movers, "to_index": 0})

    after = order_of(client, project)
    assert after[:3] == [ids[2], ids[4], ids[7]]


def test_move_clamps_out_of_range_targets(client, project):
    ids = order_of(client, project)
    client.post(f"/api/projects/{project}/scenes/move",
                json={"scene_ids": [ids[0]], "to_index": 9999})
    assert order_of(client, project)[-1] == ids[0]

    client.post(f"/api/projects/{project}/scenes/move",
                json={"scene_ids": [ids[0]], "to_index": -50})
    assert order_of(client, project)[0] == ids[0]


def test_move_rejects_foreign_scenes(client, project):
    res = client.post(f"/api/projects/{project}/scenes/move",
                      json={"scene_ids": [999_999], "to_index": 0})
    assert res.status_code == 400


def test_no_scene_is_lost_or_duplicated(client, project):
    ids = order_of(client, project)
    for target in (3, 0, len(ids) - 1, 2):
        client.post(f"/api/projects/{project}/scenes/move",
                    json={"scene_ids": [ids[4]], "to_index": target})
    after = order_of(client, project)
    assert sorted(after) == sorted(ids)
    assert len(set(after)) == len(after)


def test_indices_stay_dense_after_moves(client, project):
    ids = order_of(client, project)
    client.post(f"/api/projects/{project}/scenes/move",
                json={"scene_ids": [ids[3], ids[8]], "to_index": 1})
    page = client.get(f"/api/projects/{project}/scenes?limit=200").json()
    assert [s["order_index"] for s in page["items"]] == list(range(len(ids)))


def test_chapters_stay_contiguous(client, project):
    """A scene dragged into another chapter joins it, rather than interleaving.

    Without this the YouTube chapter timestamps come out non-monotonic.
    """
    page = client.get(f"/api/projects/{project}/scenes?limit=200").json()["items"]
    chapters = [s["chapter_id"] for s in page]
    if len(set(chapters)) < 2:
        pytest.skip("this script produced a single chapter")

    last = page[-1]
    client.post(f"/api/projects/{project}/scenes/move",
                json={"scene_ids": [last["id"]], "to_index": 1})

    after = client.get(f"/api/projects/{project}/scenes?limit=200").json()["items"]
    seen: list[int] = []
    for scene in after:
        chapter = scene["chapter_id"]
        if not seen or seen[-1] != chapter:
            assert chapter not in seen, "chapter reappears after being left behind"
            seen.append(chapter)


def test_move_clears_stale_timings(client, project):
    """Start/end times refer to the old running order and must not survive it."""
    from sqlmodel import Session, select

    from app.db import engine
    from app.models import Scene

    with Session(engine) as session:
        scenes = session.exec(
            select(Scene).where(Scene.project_id == project).order_by(Scene.order_index)
        ).all()
        for scene in scenes:
            scene.start_time, scene.end_time = 10.0, 20.0
            session.add(scene)
        session.commit()
        mover = int(scenes[2].id)

    client.post(f"/api/projects/{project}/scenes/move",
                json={"scene_ids": [mover], "to_index": 0})

    page = client.get(f"/api/projects/{project}/scenes?limit=200").json()["items"]
    assert all(s["start_time"] == 0.0 and s["end_time"] == 0.0 for s in page)
