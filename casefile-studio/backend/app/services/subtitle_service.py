"""Karaoke ASS subtitles.

Two files come out of the same word timings:

* one ASS per scene, with scene-relative timestamps, burned into that scene's
  clip. Caption lines never cross a scene boundary, which is what lets the
  final concat be a stream copy.
* one project-wide SRT, for uploading alongside the video.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from .align_service import Word

# ASS colours are &HAABBGGRR. Base white, highlight amber #FFB020.
STYLE_PRESETS = {
    "karaoke_amber": {
        "font": "Inter", "size": 54,
        "primary": "&H0020B0FF", "secondary": "&H00FFFFFF",
        "outline": "&H00000000", "back": "&H80000000",
        "outline_w": 3, "shadow": 1, "margin_v": 90,
    },
    "karaoke_cyan": {
        "font": "Inter", "size": 54,
        "primary": "&H00EED322", "secondary": "&H00FFFFFF",
        "outline": "&H00000000", "back": "&H80000000",
        "outline_w": 3, "shadow": 1, "margin_v": 90,
    },
    "plain_white": {
        "font": "Inter", "size": 50,
        "primary": "&H00FFFFFF", "secondary": "&H00FFFFFF",
        "outline": "&H00000000", "back": "&H80000000",
        "outline_w": 3, "shadow": 1, "margin_v": 90,
    },
}
DEFAULT_STYLE = "karaoke_amber"

MAX_WORDS_PER_LINE = 6
MIN_WORDS_PER_LINE = 4


@dataclass
class Line:
    words: list[Word]

    @property
    def start(self) -> float:
        return self.words[0].start

    @property
    def end(self) -> float:
        return self.words[-1].end


def group_lines(
    words: Sequence[Word],
    *,
    max_words: int = MAX_WORDS_PER_LINE,
    min_words: int = MIN_WORDS_PER_LINE,
    max_gap: float = 0.55,
) -> list[Line]:
    """4-6 words per line, breaking early at punctuation or a long pause."""
    lines: list[Line] = []
    current: list[Word] = []
    for i, word in enumerate(words):
        current.append(word)
        ends_clause = word.text.rstrip()[-1:] in ".,!?;:—"
        next_gap = (words[i + 1].start - word.end) if i + 1 < len(words) else 0.0
        if (
            len(current) >= max_words
            or (len(current) >= min_words and ends_clause)
            or next_gap > max_gap
        ):
            lines.append(Line(current))
            current = []
    if current:
        if lines and len(current) < 2:
            lines[-1].words.extend(current)   # no orphan one-word line
        else:
            lines.append(Line(current))
    return lines


def _ass_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{int(hours)}:{int(minutes):02d}:{secs:05.2f}"


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("{", "(").replace("}", ")").replace("\n", " ")


def build_ass(
    words: Sequence[Word],
    *,
    width: int,
    height: int,
    style: str = DEFAULT_STYLE,
    karaoke: bool = True,
    offset: float = 0.0,
    banner: str = "",
    lower_third: str = "",
    lower_third_sec: float = 4.0,
    banner_until: float = 0.0,
) -> str:
    cfg = STYLE_PRESETS.get(style, STYLE_PRESETS[DEFAULT_STYLE])

    # Preset sizes are authored against a 1080-high frame. Scaling by the
    # actual height keeps captions the same relative size at 480p previews and
    # at 1080x1920 for Shorts, instead of overflowing the frame.
    k = height / 1080
    size = max(14, round(cfg["size"] * k))
    margin_v = max(20, round(cfg["margin_v"] * k))
    margin_h = max(20, round(90 * k))
    outline_w = max(1, round(cfg["outline_w"] * k))
    shadow = max(0, round(cfg["shadow"] * k))
    banner_size = max(12, round(size * 0.5))
    lower_size = max(14, round(size * 0.62))

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Caption,{cfg['font']},{size},{cfg['primary']},{cfg['secondary']},{cfg['outline']},{cfg['back']},-1,0,0,0,100,100,0,0,1,{outline_w},{shadow},2,{margin_h},{margin_h},{margin_v},1
Style: Banner,{cfg['font']},{banner_size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H90000000,0,0,0,0,100,100,0,0,3,{max(1, outline_w - 1)},0,7,{margin_h},{margin_h},{margin_v},1
Style: Lower,{cfg['font']},{lower_size},&H0020B0FF,&H00FFFFFF,&H00000000,&H90000000,-1,0,0,0,100,100,0,0,3,{max(1, outline_w - 1)},0,1,{margin_h},{margin_h},{margin_v + round(90 * k)},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    rows: list[str] = []
    # Overlays ride in the same ASS file as the captions. One libass pass
    # instead of a drawtext filter means no font-file hunting on Windows.
    if banner and banner_until > 0:
        rows.append(
            f"Dialogue: 0,{_ass_time(offset)},{_ass_time(offset + banner_until)},"
            f"Banner,,0,0,0,,{_escape(banner)}"
        )
    if lower_third and banner_until > 0:
        end_lt = min(banner_until, lower_third_sec)
        rows.append(
            f"Dialogue: 0,{_ass_time(offset)},{_ass_time(offset + end_lt)},"
            f"Lower,,0,0,0,,{_escape(lower_third)}"
        )
    for line in group_lines(words):
        start = line.start + offset
        end = line.end + offset
        if karaoke:
            parts = []
            for word in line.words:
                centis = max(1, int(round((word.end - word.start) * 100)))
                parts.append(f"{{\\k{centis}}}{_escape(word.text)}")
            body = " ".join(parts)
        else:
            body = _escape(" ".join(w.text for w in line.words))
        rows.append(f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Caption,,0,0,0,,{body}")

    return header + "\n".join(rows) + "\n"


def write_scene_ass(
    words: Sequence[Word],
    dest: Path,
    *,
    width: int,
    height: int,
    style: str = DEFAULT_STYLE,
    karaoke: bool = True,
    banner: str = "",
    lower_third: str = "",
    banner_until: float = 0.0,
) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(
        build_ass(
            words, width=width, height=height, style=style, karaoke=karaoke,
            banner=banner, lower_third=lower_third, banner_until=banner_until,
        ),
        encoding="utf-8",
    )
    return dest


# ---------------------------------------------------------------------------
# SRT sidecar
# ---------------------------------------------------------------------------

def _srt_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    millis = int(round((secs - int(secs)) * 1000))
    return f"{int(hours):02d}:{int(minutes):02d}:{int(secs):02d},{millis:03d}"


def build_srt(words: Sequence[Word]) -> str:
    blocks = []
    for i, line in enumerate(group_lines(words), start=1):
        text = " ".join(w.text for w in line.words)
        blocks.append(f"{i}\n{_srt_time(line.start)} --> {_srt_time(line.end)}\n{text}\n")
    return "\n".join(blocks)
