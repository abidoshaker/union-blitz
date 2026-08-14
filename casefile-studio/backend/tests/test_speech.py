"""Delivery: how the words are said, and how the silences between them fall.

These are the two things that decide whether an hour of chunked TTS sounds
like a narrator or like a machine reading a list.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

os.environ.setdefault("CASEFILE_DATA_DIR", tempfile.mkdtemp(prefix="casefile-speech-"))

from app import ffmpeg                                  # noqa: E402
from app.services import speech                         # noqa: E402
from app.services import audio as audio_service         # noqa: E402
from app.services import tts_service                    # noqa: E402

needs_ffmpeg = pytest.mark.skipif(
    not ffmpeg.capabilities().ok, reason="needs FFmpeg with libx264 and libass"
)


# ---------------------------------------------------------------------------
# Spoken form
# ---------------------------------------------------------------------------

def test_a_year_is_said_the_way_people_say_years():
    """'One thousand nine hundred and ninety-one' is the giveaway of a machine."""
    assert speech.say_year(1991) == "nineteen ninety-one"
    assert speech.say_year(1905) == "nineteen oh five"
    assert speech.say_year(1900) == "nineteen hundred"
    assert speech.say_year(2000) == "two thousand"
    assert speech.say_year(2007) == "two thousand seven"
    assert speech.say_year(2019) == "twenty nineteen"


def test_the_vocabulary_of_a_drug_case_survives_the_rewrite():
    said = speech.to_spoken(
        "In 1991 the DEA seized 4,500 kg worth $2.5m at 3:15 a.m."
    )
    assert "nineteen ninety-one" in said
    assert "D-E-A" in said                      # spelled, not pronounced "deeya"
    assert "four thousand five hundred kilograms" in said
    assert "two point five million dollars" in said
    assert "three fifteen a m" in said
    assert "$" not in said and "%" not in said


def test_decades_and_dates_take_their_irregular_forms():
    assert "nineteen eighties" in speech.to_spoken("Through the 1980s.")
    assert "nineteen seventies" in speech.to_spoken("Through the 1970s.")
    # A person says "November twelfth", never "November twelve".
    assert "November twelfth" in speech.to_spoken("On November 12, 1993.")
    assert "March third" in speech.to_spoken("On March 3 the tape was played.")


def test_an_acronym_that_is_said_as_a_word_is_left_alone():
    """NATO is a word; the DEA is three letters. Guessing by shape gets this wrong."""
    said = speech.to_spoken("NATO and the DEA both sent people.")
    assert "NATO" in said
    assert "D-E-A" in said


def test_ordinary_prose_comes_through_untouched():
    line = "The detective opened the case file and set it down on the desk."
    assert speech.to_spoken(line) == line


def test_the_rewrite_never_reaches_the_script_itself():
    """Captions, alignment and the storyboard all keep the author's words.

    Only the string handed to the voice changes - so this is a pure function
    over a copy, and a scene that says 'nineteen ninety-one' still reads 1991.
    """
    original = "The raid happened in 1991."
    speech.to_spoken(original)
    assert original == "The raid happened in 1991."


# ---------------------------------------------------------------------------
# Rhythm
# ---------------------------------------------------------------------------

def test_a_reader_rests_by_punctuation():
    comma = speech.pause_after("He waited, and then,")
    stop = speech.pause_after("He waited.")
    question = speech.pause_after("Who told them?")
    assert comma < stop < question


def test_the_longest_rest_is_where_the_subject_changes():
    inside = speech.pause_after("He waited.", chapter_break=False)
    between = speech.pause_after("He waited.", chapter_break=True)
    assert between > inside * 2


def test_the_wobble_is_stable_across_runs():
    """A re-render must produce the identical timeline.

    The frame plan and every cached clip are keyed off these boundaries, so a
    random jitter would quietly invalidate a checkpointed hour of work.
    """
    first = speech.pause_after("He waited.", seed="scene-7")
    again = speech.pause_after("He waited.", seed="scene-7")
    assert first == again


def test_two_scenes_do_not_get_the_identical_beat():
    """Identical rests 200 times is the metronome we are trying to avoid."""
    gaps = {
        speech.pause_after(f"Scene {i} ended here.", seed=f"scene-{i}")
        for i in range(12)
    }
    assert len(gaps) > 6


def test_pause_scale_of_zero_butt_joins_the_scenes():
    gaps = speech.plan_pauses(
        [{"id": 1, "text": "One."}, {"id": 2, "text": "Two."}], scale=0.0,
    )
    assert gaps[0] <= 0.05
    assert gaps[-1] == 0.0        # nothing follows the last scene


def test_the_plan_gives_one_gap_per_boundary():
    scenes = [{"id": i, "text": f"Line {i}.", "chapter": 1} for i in range(5)]
    scenes[3]["chapter"] = 2
    gaps = speech.plan_pauses(scenes)
    assert len(gaps) == len(scenes)
    assert gaps[-1] == 0.0
    # The gap before the chapter change is the long one.
    assert gaps[2] == max(gaps)


# ---------------------------------------------------------------------------
# Trimming, and the timeline it feeds
# ---------------------------------------------------------------------------

@needs_ffmpeg
def test_trimming_takes_the_padding_and_leaves_the_line(tmp_path):
    tone = tmp_path / "tone.wav"
    pad = tmp_path / "pad.wav"
    padded = tmp_path / "padded.wav"
    ffmpeg.run(["-f", "lavfi", "-i", "sine=f=220:r=48000:d=1.2", "-ac", "1", str(tone)])
    ffmpeg.run(["-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono", "-t", "0.35", str(pad)])
    listing = tmp_path / "list.txt"
    listing.write_text(f"file '{pad}'\nfile '{tone}'\nfile '{pad}'\n")
    ffmpeg.run(["-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy", str(padded)])

    raw = padded.read_bytes()
    loose = tts_service.normalize_to_wav(raw, tmp_path / "loose.wav", trim=False)
    tight = tts_service.normalize_to_wav(raw, tmp_path / "tight.wav", trim=True)

    assert loose == pytest.approx(1.9, abs=0.05)
    assert tight < loose - 0.4          # the padding went
    assert tight > 1.2                  # the line did not


@needs_ffmpeg
def test_a_silent_take_survives_trimming(tmp_path):
    """The draft voice is silence by design; trimming must not delete a scene."""
    silent = tmp_path / "silent.wav"
    ffmpeg.run(["-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono", "-t", "0.6", str(silent)])
    kept = tts_service.normalize_to_wav(silent.read_bytes(), tmp_path / "out.wav", trim=True)
    assert kept == pytest.approx(0.6, abs=0.05)


@needs_ffmpeg
def test_the_timeline_honours_a_gap_per_scene(tmp_path):
    clips = []
    for i in range(3):
        clip = tmp_path / f"c{i}.wav"
        ffmpeg.run(["-f", "lavfi", "-i", "sine=f=300:r=48000:d=0.5", "-ac", "1", str(clip)])
        clips.append(clip)

    timeline = audio_service.concat_wavs(
        clips, tmp_path / "joined.wav", gaps=[0.1, 0.8, 0.0],
    )
    assert timeline.starts[1] - timeline.ends[0] == pytest.approx(0.1, abs=0.01)
    assert timeline.starts[2] - timeline.ends[1] == pytest.approx(0.8, abs=0.01)
    # Nothing is added after the last scene.
    assert timeline.total == pytest.approx(timeline.ends[-1], abs=0.001)
