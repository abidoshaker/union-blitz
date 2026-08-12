"""Video scenes, face blurring, and the clip-audio modes.

Runs offline against locally generated clips. The stock and archival providers
are exercised for shape only - hitting Pexels, Pixabay or the Internet Archive
needs keys and outbound network, so their behaviour is not asserted here.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

import pytest

os.environ.setdefault("CASEFILE_DATA_DIR", tempfile.mkdtemp(prefix="casefile-media-"))

from app import ffmpeg  # noqa: E402
from app.services import audio as audio_service  # noqa: E402
from app.services import query, render_service, video_service, vision  # noqa: E402

needs_ffmpeg = pytest.mark.skipif(
    not ffmpeg.capabilities().ok, reason="needs FFmpeg with libx264 and libass"
)
needs_vision = pytest.mark.skipif(not vision.available()[0], reason=vision.available()[1])


@pytest.fixture(scope="module")
def clips(tmp_path_factory) -> dict[str, Path]:
    """A silent clip, a clip with a tone, and a clip containing a moving face."""
    out = tmp_path_factory.mktemp("clips")
    face_src = Path(__file__).parent / "data" / "face.jpg"

    subprocess.run([
        ffmpeg.settings.ffmpeg, "-y", "-loglevel", "error",
        "-f", "lavfi", "-i", "color=c=0x223344:s=640x360:d=6:r=24",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        str(out / "silent.mp4"),
    ], check=True)

    subprocess.run([
        ffmpeg.settings.ffmpeg, "-y", "-loglevel", "error",
        "-f", "lavfi", "-i", "color=c=0x332211:s=640x360:d=5:r=24",
        "-f", "lavfi", "-i", "sine=frequency=320:duration=5",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest", str(out / "talking.mp4"),
    ], check=True)

    made = {"silent": out / "silent.mp4", "talking": out / "talking.mp4"}
    if face_src.exists():
        subprocess.run([
            ffmpeg.settings.ffmpeg, "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", "color=c=0x1A1D29:s=960x540:d=6:r=24",
            "-loop", "1", "-i", str(face_src),
            "-filter_complex", "[1:v]scale=260:-1[f];[0:v][f]overlay=x='60+t*90':y='90+40*sin(t)'",
            "-t", "6", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            str(out / "face.mp4"),
        ], check=True)
        made["face"] = out / "face.mp4"
    return made


# ---------------------------------------------------------------------------
# Query ladder
# ---------------------------------------------------------------------------

def test_entities_survive_title_abbreviations():
    assert query.entities("Det. Halloran testified.") == ["Det. Halloran"]


def test_entities_do_not_run_across_a_sentence_boundary():
    found = query.entities("He walked onto Mercer Street. It was February.")
    assert "Mercer Street" in found
    assert not any("It" in e for e in found)


def test_entities_ignore_bare_dates_and_titles():
    assert query.entities("It happened on a Tuesday in March.") == []


def test_topics_match_whole_words_only():
    """'careful' must not source a photograph of a car."""
    q = query.build("Eleven years of careful work unravelled.")
    assert not any("car " in term or term.startswith("vintage car") for term in q.stock)
    assert "vintage car on a dark road" in query.build("He left the car running.").stock


def test_ladder_always_ends_somewhere_searchable():
    ladder = query.build("").ladder(prefer_archival=False)
    assert ladder and ladder[-1] in query.GENERIC_FALLBACKS


def test_ladder_puts_names_first_for_archival():
    q = query.build("Vincent Moretti was arrested in 1974 outside the warehouse.")
    assert q.ladder(prefer_archival=True)[0].startswith("Vincent Moretti")
    assert q.ladder(prefer_archival=False)[0] != "Vincent Moretti 1974"


def test_pick_avoids_reusing_the_same_asset():
    from app.providers.image.base import AssetCandidate

    a = AssetCandidate(url="one", title="warehouse night", width=1920, height=1080)
    b = AssetCandidate(url="two", title="warehouse night", width=1920, height=1080)
    first = query.pick([a, b], query="warehouse night", used=set())
    second = query.pick([a, b], query="warehouse night", used={first.url})
    assert second is not None and second.url != first.url


# ---------------------------------------------------------------------------
# Face blur
# ---------------------------------------------------------------------------

@needs_vision
def test_blurred_still_is_no_longer_a_detectable_face(tmp_path):
    src = Path(__file__).parent / "data" / "face.jpg"
    if not src.exists():
        pytest.skip("no face fixture available")
    import cv2

    assert vision.detect(cv2.imread(str(src))), "fixture should contain a face"
    dest = tmp_path / "blurred.jpg"
    assert vision.blur_image_faces(src, dest) >= 1
    assert vision.detect(cv2.imread(str(dest))) == []


@needs_vision
@needs_ffmpeg
def test_every_face_in_a_clip_falls_inside_a_blur_region(clips):
    """The invariant that matters: coverage of the source, frame by frame.

    Re-running the detector on the blurred output is not the test - it fires on
    the blurred blob itself. What must hold is that every face found in the
    original is inside an active region at that moment.
    """
    if "face" not in clips:
        pytest.skip("no face fixture available")
    import cv2

    tracks = vision.detect_video_faces(clips["face"], sample_fps=4)
    assert tracks

    capture = cv2.VideoCapture(str(clips["face"]))
    total = covered = 0
    try:
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            timestamp = capture.get(cv2.CAP_PROP_POS_MSEC) / 1000.0
            for face in vision.detect(frame):
                total += 1
                active = [t for t in tracks if t.start - 0.4 <= timestamp <= t.end + 0.4]
                best = 0.0
                for region in active:
                    ix = max(0, min(face.x + face.w, region.box.x + region.box.w) - max(face.x, region.box.x))
                    iy = max(0, min(face.y + face.h, region.box.y + region.box.h) - max(face.y, region.box.y))
                    best = max(best, ix * iy / float(face.w * face.h))
                if best >= 0.9:
                    covered += 1
    finally:
        capture.release()

    assert total > 0
    assert covered == total, f"only {covered}/{total} face detections were inside a blur region"


def test_blur_chain_is_a_passthrough_when_nothing_was_found():
    chain = vision.blur_filter_chain([], label_in="0:v", label_out="out")
    assert chain == "[0:v]null[out]"


# ---------------------------------------------------------------------------
# Video scenes
# ---------------------------------------------------------------------------

@needs_ffmpeg
def test_video_scene_renders_to_the_exact_frame_count(clips, tmp_path):
    opts = render_service.RenderOpts(width=320, height=180, fps=12, preset="ultrafast")
    spec = render_service.SceneSpec(
        index=0, image=clips["silent"], frames=36,
        media_kind="video", media_in=0.5, media_duration=6.0,
    )
    render_service.plan_transitions([spec], opts)
    clip, _ = render_service.render_scene_clip(spec, tmp_path / "clips", opts)
    assert abs(ffmpeg.duration_of(clip) - 36 / 12) < 0.05


@needs_ffmpeg
def test_a_scene_longer_than_its_clip_loops_rather_than_freezing(clips, tmp_path):
    opts = render_service.RenderOpts(width=320, height=180, fps=12, preset="ultrafast")
    spec = render_service.SceneSpec(
        index=0, image=clips["silent"], frames=120,   # 10s from a 6s clip
        media_kind="video", media_in=0.0, media_duration=6.0,
    )
    render_service.plan_transitions([spec], opts)
    clip, _ = render_service.render_scene_clip(spec, tmp_path / "clips", opts)
    assert abs(ffmpeg.duration_of(clip) - 10.0) < 0.1


@needs_ffmpeg
def test_stills_and_clips_concatenate_by_stream_copy(clips, tmp_path):
    """Mixing media kinds must not cost a re-encode at assembly."""
    from app.providers.image import get_provider

    still = tmp_path / "still.jpg"
    still.write_bytes(get_provider("placeholder").generate("a dark street").extra["bytes"])

    opts = render_service.RenderOpts(width=320, height=180, fps=12, preset="ultrafast")
    specs = [
        render_service.SceneSpec(index=0, image=still, frames=24),
        render_service.SceneSpec(index=1, image=clips["silent"], frames=36,
                                 media_kind="video", media_duration=6.0,
                                 poster=tmp_path / "poster.jpg"),
        render_service.SceneSpec(index=2, image=still, frames=24),
    ]
    ffmpeg.run(["-i", str(clips["silent"]), "-frames:v", "1", str(tmp_path / "poster.jpg")])
    render_service.plan_transitions(specs, opts)

    rendered = render_service.render_scene_clips(specs, tmp_path / "clips", opts, workers=2)
    out = render_service.concat_clips(rendered, tmp_path / "mixed.mp4")
    assert abs(ffmpeg.duration_of(out) - 84 / 12) < 0.1


def test_video_scenes_never_take_a_ken_burns_motion_handoff(clips):
    """A clip starts on its own first frame - the frame the fade landed on."""
    opts = render_service.RenderOpts(width=320, height=180, fps=12)
    specs = [
        render_service.SceneSpec(index=0, image=Path("a.jpg"), frames=60),
        render_service.SceneSpec(index=1, image=Path("b.mp4"), frames=60, media_kind="video"),
    ]
    render_service.plan_transitions(specs, opts)
    assert specs[0].motion_offset_frames == 0
    assert specs[1].motion_offset_frames == 0      # video, so no handoff
    assert specs[0].next_kind == "video"


# ---------------------------------------------------------------------------
# Clip audio
# ---------------------------------------------------------------------------

@needs_ffmpeg
def test_audible_audio_is_distinguished_from_a_silent_track(clips):
    assert video_service.has_audible_audio(clips["talking"]) is True
    assert video_service.has_audible_audio(clips["silent"]) is False


@needs_ffmpeg
def test_extracted_clip_audio_matches_the_narration_format(clips, tmp_path):
    dest = tmp_path / "bite.wav"
    duration = video_service.extract_audio(clips["talking"], dest)
    assert duration > 4.0
    info = ffmpeg.probe(dest)
    stream = next(s for s in info["streams"] if s["codec_type"] == "audio")
    assert int(stream["sample_rate"]) == audio_service.SAMPLE_RATE
    assert int(stream["channels"]) == 1


@needs_ffmpeg
def test_ambient_bed_is_forced_to_the_length_of_its_slot(clips, tmp_path):
    raw = tmp_path / "raw.wav"
    video_service.extract_audio(clips["talking"], raw)

    short = audio_service.fit(raw, tmp_path / "short.wav", 2.0)
    long = audio_service.fit(raw, tmp_path / "long.wav", 9.0)
    assert abs(ffmpeg.duration_of(short) - 2.0) < 0.02
    assert abs(ffmpeg.duration_of(long) - 9.0) < 0.02


@needs_ffmpeg
def test_soundbite_occupies_its_own_slot_so_nothing_overlaps(clips, tmp_path):
    """The narration track holds the clip's audio in that scene's place.

    That is the whole mechanism: the voiceover does not exist during a
    soundbite, so it cannot talk over the footage.
    """
    from app.providers.tts import TTSOpts, get_provider
    from app.services.tts_service import normalize_to_wav

    drafter = get_provider("draft")
    speech = normalize_to_wav(
        drafter.synthesize("A line of narration before the clip.", "silence", TTSOpts()).audio,
        tmp_path / "a.wav",
    )
    bite = tmp_path / "bite.wav"
    bite_len = video_service.extract_audio(clips["talking"], bite)

    timeline = audio_service.concat_wavs(
        [tmp_path / "a.wav", bite, tmp_path / "a.wav"], tmp_path / "narration.wav"
    )
    # The soundbite's slot is exactly as long as the clip's audio, and the
    # scenes around it are untouched.
    assert abs((timeline.ends[1] - timeline.starts[1]) - bite_len) < 0.05
    assert abs((timeline.ends[0] - timeline.starts[0]) - speech) < 0.05
    assert timeline.starts[1] >= timeline.ends[0]


@needs_ffmpeg
def test_master_mixes_an_ambient_bed_without_changing_length(clips, tmp_path):
    from app.providers.tts import TTSOpts, get_provider
    from app.services.tts_service import normalize_to_wav

    drafter = get_provider("draft")
    for name in ("s1.wav", "s2.wav"):
        normalize_to_wav(
            drafter.synthesize("Narration continues over the footage here.", "silence",
                               TTSOpts()).audio,
            tmp_path / name,
        )
    timeline = audio_service.concat_wavs(
        [tmp_path / "s1.wav", tmp_path / "s2.wav"], tmp_path / "narration.wav"
    )

    raw = tmp_path / "amb_raw.wav"
    video_service.extract_audio(clips["talking"], raw)
    # Segments span whole slots, including the gap between scenes, so the bed
    # is exactly as long as the narration.
    segments = [
        audio_service.silence(timeline.starts[1] - timeline.starts[0], tmp_path / "amb0.wav"),
        audio_service.fit(raw, tmp_path / "amb1.wav", timeline.total - timeline.starts[1]),
    ]
    ambient_path = tmp_path / "ambient.wav"
    audio_service.concat_wavs(segments, ambient_path, gap_sec=0.0)

    master = audio_service.build_master(
        narration=tmp_path / "narration.wav",
        dest=tmp_path / "master.m4a",
        ambient=tmp_path / "ambient.wav",
        total_sec=timeline.total,
    )
    assert abs(ffmpeg.duration_of(master) - timeline.total) < 0.15
    # Bed and narration must be the same length to the sample, or the mix gets
    # cut short by sidechaincompress and the ambience drifts out of sync.
    assert abs(ffmpeg.duration_of(ambient_path) - timeline.total) < 0.01


@needs_ffmpeg
def test_a_short_bed_cannot_truncate_the_master(clips, tmp_path):
    """sidechaincompress emits min(main, sidechain); the master must not."""
    from app.providers.tts import TTSOpts, get_provider
    from app.services.tts_service import normalize_to_wav

    normalize_to_wav(
        get_provider("draft").synthesize("A reasonably long line of narration here.",
                                         "silence", TTSOpts()).audio,
        tmp_path / "voice.wav",
    )
    voice_len = ffmpeg.duration_of(tmp_path / "voice.wav")
    audio_service.silence(voice_len / 2, tmp_path / "short_bed.wav")

    master = audio_service.build_master(
        narration=tmp_path / "voice.wav",
        dest=tmp_path / "master_short.m4a",
        ambient=tmp_path / "short_bed.wav",
        total_sec=voice_len,
    )
    assert abs(ffmpeg.duration_of(master) - voice_len) < 0.15
