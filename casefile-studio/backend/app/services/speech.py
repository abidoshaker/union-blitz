"""Turning written prose into something that sounds like a person reading it.

Two jobs, both aimed at the same complaint: chunked TTS narration sounds
mechanical.

**Spoken form.** A true-crime script is dense with the exact things text-to-
speech reads badly: years, sums of money, calibres, agency initialisms, street
numbers, dates. "In 1991 the DEA seized 4,500 kg worth $2.5m" is read by most
engines as "in one thousand nine hundred ninety-one the dee-uh seized four
thousand five hundred kay-gee worth two point five em". Every one of those is a
moment the listener notices the machine. Rewriting them before synthesis costs
nothing and fixes all of them.

The rewrite never touches the script itself. Captions, the storyboard and the
alignment pass all keep the author's words; only the string handed to the voice
changes. `align_service._retime_known_text` is built for exactly this - it
keeps the script's tokens and borrows the recogniser's timings - so a scene
that *says* "nineteen ninety-one" still captions "1991".

**Rhythm.** Scenes are synthesised one at a time, so every scene begins with
the model's neutral opening pitch and ends with its neutral close. Butt-joining
those with an identical 0.25 s gap 200 times is what produces the flat,
list-reading cadence. A reader instead pauses by punctuation: barely at a
comma, properly at a full stop, longer at a question, and longest when the
subject changes. `pause_after` reproduces that, with a small deterministic
wobble so the beats are not metronomic.
"""

from __future__ import annotations

import hashlib
import re

# ---------------------------------------------------------------------------
# Spoken form
# ---------------------------------------------------------------------------

UNITS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight",
         "nine", "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen",
         "sixteen", "seventeen", "eighteen", "nineteen"]
TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy",
        "eighty", "ninety"]

# Initialisms a narrator spells out. Deliberately a list rather than a rule:
# "NATO" and "OPEC" are said as words, and guessing which is which by shape
# gets it wrong often enough to be worse than saying nothing.
SPELLED_OUT = {
    "DEA", "FBI", "CIA", "ATF", "DOJ", "IRS", "NSA", "ICE", "CBP", "DHS",
    "NYPD", "LAPD", "RCMP", "MI5", "MI6", "GRU", "KGB", "FARC", "ELN",
    "AUC", "PCC", "MS13", "UN", "EU", "UK", "US", "USA", "TSA", "SWAT",
    "CCTV", "DNA", "ID", "PD", "DA", "AG", "POW", "AWOL", "APB", "BOLO",
}

MONTHS = {
    "jan": "January", "feb": "February", "mar": "March", "apr": "April",
    "jun": "June", "jul": "July", "aug": "August", "sep": "September",
    "sept": "September", "oct": "October", "nov": "November", "dec": "December",
}

ABBREVIATIONS = {
    r"\bMr\.": "Mister", r"\bMrs\.": "Missus", r"\bDr\.": "Doctor",
    r"\bSt\.": "Saint", r"\bMt\.": "Mount", r"\bAve\.": "Avenue",
    r"\bBlvd\.": "Boulevard", r"\bSgt\.": "Sergeant", r"\bLt\.": "Lieutenant",
    r"\bCol\.": "Colonel", r"\bGen\.": "General", r"\bDet\.": "Detective",
    r"\bapprox\.": "approximately", r"\bvs\.": "versus", r"\bNo\.\s*(?=\d)": "number ",
    r"\be\.g\.": "for example", r"\bi\.e\.": "that is",
}

UNIT_WORDS = [
    (r"\bkg\b", "kilograms"), (r"\bkgs\b", "kilograms"), (r"\bkm\b", "kilometres"),
    (r"\blbs\b", "pounds"), (r"\bmph\b", "miles per hour"), (r"\bkph\b", "kilometres per hour"),
    (r"\bft\b", "feet"), (r"\bhrs\b", "hours"), (r"\bsq\b", "square"),
]


def _under_hundred(n: int) -> str:
    if n < 20:
        return UNITS[n]
    tens, rest = divmod(n, 10)
    return TENS[tens] + (f"-{UNITS[rest]}" if rest else "")


def _under_thousand(n: int) -> str:
    hundreds, rest = divmod(n, 100)
    if not hundreds:
        return _under_hundred(rest)
    head = f"{UNITS[hundreds]} hundred"
    return f"{head} and {_under_hundred(rest)}" if rest else head


def say_number(n: int) -> str:
    """Cardinal, spelled the way a reader says it aloud."""
    if n < 0:
        return f"minus {say_number(-n)}"
    if n < 1000:
        return _under_thousand(n)
    for size, name in ((1_000_000_000, "billion"), (1_000_000, "million"), (1000, "thousand")):
        if n >= size:
            head, rest = divmod(n, size)
            out = f"{say_number(head)} {name}"
            if not rest:
                return out
            joiner = " and " if rest < 100 else " "
            return out + joiner + say_number(rest)
    return str(n)


def say_year(n: int) -> str:
    """1991 is 'nineteen ninety-one', not 'one thousand nine hundred ninety-one'."""
    if not 1000 <= n <= 2099:
        return say_number(n)
    if 2000 <= n <= 2009:
        return f"two thousand{'' if n == 2000 else ' ' + UNITS[n - 2000]}"
    head, tail = divmod(n, 100)
    if tail == 0:
        return f"{_under_hundred(head)} hundred"
    if tail < 10:                       # 1905 -> nineteen oh five
        return f"{_under_hundred(head)} oh {UNITS[tail]}"
    return f"{_under_hundred(head)} {_under_hundred(tail)}"


_MONEY = re.compile(r"\$\s?([\d,]+(?:\.\d+)?)\s*(billion|million|bn|m|k|thousand)?\b", re.I)
_YEAR = re.compile(r"\b(1[0-9]{3}|20[0-9]{2})\b")
_DECADE = re.compile(r"\b(1[89]|20)([0-9]0)s\b")
_TIME = re.compile(r"\b([01]?\d|2[0-3]):([0-5]\d)\s*(a\.?m\.?|p\.?m\.?)?", re.I)
_ORDINAL = re.compile(r"\b(\d+)(st|nd|rd|th)\b", re.I)
_PLAIN = re.compile(r"\b\d[\d,]*(?:\.\d+)?\b")
_INITIALISM = re.compile(r"\b([A-Z]{2,5})\b")
_PERCENT = re.compile(r"\b([\d,]+(?:\.\d+)?)\s?%")
_MONTH_NAMES = ("January|February|March|April|May|June|July|August|September|"
                "October|November|December")
_DATE_DAY = re.compile(rf"\b({_MONTH_NAMES})\s+(\d{{1,2}})\b(?!\s*[:.]\d)")


def _money(match: re.Match[str]) -> str:
    raw, scale = match.group(1).replace(",", ""), (match.group(2) or "").lower()
    value = float(raw)
    scale_word = {"bn": "billion", "m": "million", "k": "thousand"}.get(scale, scale)
    if value.is_integer():
        spoken = say_number(int(value))
    else:
        whole, frac = str(value).split(".")
        spoken = f"{say_number(int(whole))} point {' '.join(UNITS[int(d)] for d in frac)}"
    if scale_word:
        return f"{spoken} {scale_word} dollars"
    return f"{spoken} dollar{'' if value == 1 else 's'}"


def _time(match: re.Match[str]) -> str:
    hour, minute, meridiem = int(match.group(1)), int(match.group(2)), match.group(3)
    said = _under_hundred(hour if hour else 12)
    if minute == 0:
        said += " o'clock" if not meridiem else ""
    elif minute < 10:
        said += f" oh {UNITS[minute]}"
    else:
        said += f" {_under_hundred(minute)}"
    if meridiem:
        said += " a m" if meridiem.lower().startswith("a") else " p m"
    return said


def _decade(match: re.Match[str]) -> str:
    """The 1980s are the 'nineteen eighties' - the plural is irregular."""
    head, tail = _under_hundred(int(match.group(1))), _under_hundred(int(match.group(2)))
    plural = tail[:-1] + "ies" if tail.endswith("y") else tail + "s"
    return f"{head} {plural}"


def _ordinal_of(n: int) -> str:
    return _ordinal(re.match(r"(\d+)(th)", f"{n}th"))     # reuse the one rule


def _ordinal(match: re.Match[str]) -> str:
    n = int(match.group(1))
    words = say_number(n)
    head, _, last = words.rpartition(" ")
    last, joiner = (last, f"{head} ") if head else (words, "")
    special = {"one": "first", "two": "second", "three": "third", "five": "fifth",
               "eight": "eighth", "nine": "ninth", "twelve": "twelfth"}
    stem, sep, tail = last.rpartition("-")
    target = tail or last
    if target in special:
        target = special[target]
    elif target.endswith("y"):
        target = target[:-1] + "ieth"
    else:
        target += "th"
    return joiner + (f"{stem}{sep}{target}" if sep else target)


def _plain(match: re.Match[str]) -> str:
    raw = match.group(0).replace(",", "")
    if "." in raw:
        whole, frac = raw.split(".", 1)
        return f"{say_number(int(whole or 0))} point {' '.join(UNITS[int(d)] for d in frac if d.isdigit())}"
    return say_number(int(raw))


def _initialism(match: re.Match[str]) -> str:
    word = match.group(1)
    if word.rstrip("0123456789") in SPELLED_OUT or word in SPELLED_OUT:
        # Hyphens make every engine spell rather than blend: D-E-A, not "deeya".
        return "-".join(word)
    return word


def to_spoken(text: str) -> str:
    """Rewrite a line into the words a narrator would actually say.

    Order matters: money and times swallow digits that the plain-number rule
    would otherwise mangle, and years must be claimed before plain numbers turn
    1991 into "one thousand nine hundred and ninety-one".
    """
    if not text:
        return text
    out = text

    for pattern, word in ABBREVIATIONS.items():
        out = re.sub(pattern, word, out)
    for short, full in MONTHS.items():
        out = re.sub(rf"\b{short}\.", full, out, flags=re.I)

    out = _MONEY.sub(_money, out)
    out = _PERCENT.sub(lambda m: f"{_plain(re.match(_PLAIN, m.group(1)) or m)} percent", out)
    out = _TIME.sub(_time, out)
    out = _DECADE.sub(_decade, out)
    # "November 12" is read "November twelfth" by a person, never "twelve".
    out = _DATE_DAY.sub(lambda m: f"{m.group(1)} {_ordinal_of(int(m.group(2)))}", out)
    out = _YEAR.sub(lambda m: say_year(int(m.group(1))), out)
    out = _ORDINAL.sub(_ordinal, out)

    for pattern, word in UNIT_WORDS:
        out = re.sub(pattern, word, out)

    out = _PLAIN.sub(_plain, out)
    out = _INITIALISM.sub(_initialism, out)

    # A dash is a breath, not a hyphenated word; engines run straight through
    # one written as "word—word".
    out = re.sub(r"\s*[—–]\s*", ", ", out)
    return re.sub(r"\s{2,}", " ", out).strip()


# ---------------------------------------------------------------------------
# Rhythm
# ---------------------------------------------------------------------------

# How long a reader rests, by what they have just read.
PAUSE_BY_ENDING = {
    "clause": 0.14,      # comma, semicolon, colon, or a mid-sentence split
    "sentence": 0.34,    # full stop
    "question": 0.46,    # question or exclamation
    "trailing": 0.55,    # ellipsis - the line was left hanging
}
CHAPTER_PAUSE = 0.95     # the subject just changed; let it land
WOBBLE = 0.06            # +/- this much, so the beats are not metronomic


def ending_of(text: str) -> str:
    stripped = (text or "").rstrip()
    if stripped.endswith("…") or stripped.endswith("..."):
        return "trailing"
    if stripped.endswith(("?", "!")):
        return "question"
    if stripped.endswith("."):
        return "sentence"
    return "clause"


def pause_after(text: str, *, chapter_break: bool = False, seed: str = "",
                scale: float = 1.0) -> float:
    """Seconds of silence to leave after this scene.

    The wobble is derived from the scene's own text rather than a random
    number: a re-render has to produce the identical timeline, or the
    checkpointed frame plan and every cached clip stop lining up.
    """
    base = CHAPTER_PAUSE if chapter_break else PAUSE_BY_ENDING[ending_of(text)]
    digest = hashlib.sha1((seed or text).encode("utf-8")).digest()[0]
    wobble = (digest / 255.0 - 0.5) * 2 * WOBBLE
    return max(0.05, round((base + wobble) * max(0.0, scale), 3))


def plan_pauses(scenes: list[dict], *, scale: float = 1.0) -> list[float]:
    """A gap for each scene boundary. The last scene gets none - nothing follows.

    Each scene dict needs `text`, and `chapter` if chapters are in play.
    """
    gaps: list[float] = []
    for i, scene in enumerate(scenes):
        if i == len(scenes) - 1:
            gaps.append(0.0)
            continue
        changed = scene.get("chapter") != scenes[i + 1].get("chapter")
        gaps.append(pause_after(
            str(scene.get("text", "")),
            chapter_break=bool(changed),
            seed=str(scene.get("id", "")) + str(scene.get("text", ""))[:40],
            scale=scale,
        ))
    return gaps
