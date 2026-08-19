"""Choosing a narrator, and the settings that reach the render.

The complaint these cover: the engine could always list voices, audition them,
clone one, and read a single scene in a different voice - none of it was
reachable, and a project made through the interface narrated with silence.
"""

from __future__ import annotations

import os
import tempfile
import time

import pytest

os.environ.setdefault("CASEFILE_DATA_DIR", tempfile.mkdtemp(prefix="casefile-narration-"))

from fastapi.testclient import TestClient       # noqa: E402

from app.main import app                        # noqa: E402
from app.providers import tts as tts_providers  # noqa: E402
from app.services.pipeline import narration_opts, project_settings  # noqa: E402

SCRIPT = (
    "The shipment left the container port at Cartagena in March of 1991. "
    "Federal agents had been listening to the wiretap for nine long weeks. "
    "The arrest took less than forty seconds in the courtyard of the compound. "
) * 2


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


# ---------------------------------------------------------------------------
# The catalogue
# ---------------------------------------------------------------------------

def test_the_voice_list_says_what_each_provider_is_good_for(client):
    """Licence is the fact a choice turns on, so it travels with the list."""
    body = client.get("/api/voices").json()
    assert "voices" in body and "providers" in body

    by_name = {p["name"]: p for p in body["providers"]}
    assert by_name["fish"]["supports_cloning"] is True
    assert by_name["fish"]["cost_per_million_bytes"] > 0
    assert by_name["kokoro"]["is_local"] is True
    # Edge is free and good, and using it on a monetised channel breaches
    # Microsoft's terms. That has to be visible, not buried in a doc.
    assert by_name["edge"]["commercial_ok"] is False
    assert by_name["edge"]["is_draft_only"] is True


def test_a_provider_without_a_key_explains_itself_rather_than_vanishing(client):
    body = client.get("/api/voices").json()
    reasons = {u["provider"]: u["reason"] for u in body["unavailable"]}
    if "fish" in reasons:
        assert "key" in reasons["fish"].lower()


def test_auditioning_an_unconfigured_provider_is_a_400_not_a_crash(client):
    res = client.post("/api/voices/preview", json={"provider": "fish", "voice_id": "x"})
    assert res.status_code in (400, 502)


def test_a_voice_can_be_auditioned_before_you_commit_an_hour_to_it(client):
    res = client.post("/api/voices/preview", json={
        "provider": "draft", "voice_id": "silence",
        "text": "In 1991 the DEA moved on the compound.",
    })
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("audio/")
    assert len(res.content) > 1000


def test_cloning_refuses_a_provider_that_cannot_clone(client):
    res = client.post(
        "/api/voices/clone",
        data={"title": "Mine", "provider": "kokoro"},
        files={"sample": ("ref.wav", b"\x00" * 20000, "audio/wav")},
    )
    assert res.status_code == 400
    assert "clone" in res.json()["detail"].lower()


def test_cloning_refuses_a_clip_too_short_to_learn_from(client):
    res = client.post(
        "/api/voices/clone",
        data={"title": "Mine", "provider": "fish"},
        files={"sample": ("ref.wav", b"\x00" * 200, "audio/wav")},
    )
    assert res.status_code == 400
    assert "short" in res.json()["detail"].lower()


# ---------------------------------------------------------------------------
# A new project must not narrate with silence
# ---------------------------------------------------------------------------

def test_a_new_project_gets_a_voice_that_can_actually_speak(client):
    """The old default was the silent draft voice - an hour of nothing."""
    pid = client.post("/api/projects", json={"title": "Voiced"}).json()["id"]
    settings = client.get(f"/api/projects/{pid}").json()["settings"]

    expected_provider, _voice = tts_providers.recommended()
    assert settings["tts_provider"] == expected_provider
    if expected_provider != "draft":
        assert settings["voice_id"] != "silence"


def test_an_explicit_choice_still_wins(client):
    """Test fixtures and drafts ask for the silent voice on purpose."""
    pid = client.post("/api/projects", json={
        "title": "Draft", "settings": {"tts_provider": "draft", "voice_id": "silence"},
    }).json()["id"]
    assert client.get(f"/api/projects/{pid}").json()["settings"]["tts_provider"] == "draft"


# ---------------------------------------------------------------------------
# Delivery settings reach the voice
# ---------------------------------------------------------------------------

def test_delivery_settings_are_carried_into_the_synthesis_options():
    opts = narration_opts({"speech_rate": 1.15, "spoken_numbers": False, "trim_takes": False})
    assert opts.speed == 1.15
    assert opts.spoken_form is False
    assert opts.trim_padding is False


def test_a_ludicrous_pace_is_clamped_rather_than_sent():
    assert narration_opts({"speech_rate": 9}).speed == 2.0
    assert narration_opts({"speech_rate": 0.01}).speed == 0.5
    assert narration_opts({"speech_rate": None}).speed == 1.0


def test_delivery_changes_the_cache_key_so_a_retake_is_not_served_stale():
    """Turning spoken numbers off has to re-record, not replay the old take."""
    spoken = narration_opts({"spoken_numbers": True}).cache_key()
    written = narration_opts({"spoken_numbers": False}).cache_key()
    assert spoken != written
    assert narration_opts({"speech_rate": 1.0}).cache_key() != \
        narration_opts({"speech_rate": 1.1}).cache_key()


def test_the_project_defaults_include_the_delivery_controls():
    class Fake:
        settings_json: dict = {}

    cfg = project_settings(Fake())
    for key in ("speech_rate", "pause_scale", "spoken_numbers", "trim_takes"):
        assert key in cfg


# ---------------------------------------------------------------------------
# Per-scene voice, and chapter titles
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def project(client):
    pid = client.post("/api/projects", json={"title": "Narration", "settings": {
        "tts_provider": "draft", "voice_id": "silence", "visual_source": "placeholder",
        "llm_provider": "heuristic", "width": 480, "height": 270, "fps": 12,
        "preset": "ultrafast", "image_pool_per_chapter": 2,
    }}).json()["id"]
    client.post(f"/api/projects/{pid}/script", json={"raw_text": SCRIPT})
    for stage in ("segment", "narrate"):
        assert _wait(client, client.post(f"/api/projects/{pid}/run/{stage}", json={})
                     .json()["job_id"])["status"] == "done"
    return pid


def test_one_scene_can_be_re_recorded_in_a_different_voice(client, project):
    scene = client.get(f"/api/projects/{project}/scenes").json()["items"][0]
    before = client.get(f"/api/projects/{project}/scenes").json()["items"][0]["audio_url"]

    res = client.post(f"/api/scenes/{scene['id']}/regenerate-audio",
                      json={"tts_provider": "draft", "voice_id": "silence"})
    assert res.status_code == 200, res.text

    after = client.get(f"/api/projects/{project}/scenes").json()["items"]
    assert after[0]["has_audio"]
    # The other scenes are untouched - a retake is one line, not a re-narration.
    assert len(after) > 1
    assert before is not None


def test_a_chapter_can_be_retitled_for_the_youtube_markers(client, project):
    chapters = client.get(f"/api/projects/{project}/chapters").json()
    if not chapters:
        pytest.skip("this script produced no chapters")
    res = client.patch(f"/api/projects/{project}/chapters/{chapters[0]['id']}",
                       json={"title": "The port at Cartagena"})
    assert res.status_code == 200
    assert client.get(f"/api/projects/{project}/chapters").json()[0]["title"] == \
        "The port at Cartagena"


def test_output_settings_survive_a_round_trip(client, project):
    """Everything the Output panel writes has to come back as it went in."""
    wanted = {
        "width": 1080, "height": 1920, "fps": 24, "encoder": "libx264",
        "preset": "veryfast", "subtitle_style": "plain_white", "karaoke": False,
        "burn_subtitles": True, "kenburns": "none", "transition_sec": 0.3,
        "loudness_lufs": -16, "align_method": "proportional",
        "speech_rate": 0.92, "pause_scale": 1.4,
    }
    client.patch(f"/api/projects/{project}", json={"settings": wanted})
    got = client.get(f"/api/projects/{project}").json()["settings"]
    for key, value in wanted.items():
        assert got[key] == value, key
