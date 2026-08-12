"""Turning a scene of narration into search queries that return usable footage.

The naive approach - hand the image prompt to a stock API - fails in a specific
way on true crime. Stock libraries have nothing for "Vincent Moretti Mercer
Street 1974"; they have a great deal for "warehouse night rain". Archival
libraries are the opposite: they have the named subject and nothing generic.

So each scene produces a *ladder* of queries from most specific to most
generic, and sourcing walks down it until something comes back. Named entities
are routed to archival sources, atmosphere to stock ones.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_WORD = re.compile(r"\b[\w'’-]+\b")
# Allows a trailing period so a title abbreviation stays attached to the name
# it belongs to: "Det. Halloran" is one entity, not "Det" and "Halloran".
_PROPER_RUN = re.compile(
    r"\b[A-Z][\w'’-]*\.?(?:\s+(?:of|de|la|van|von|del)\s+[A-Z][\w'’-]*|\s+[A-Z][\w'’-]*\.?)*"
)
_YEAR = re.compile(r"\b(1[89]\d{2}|20[0-2]\d)\b")

# Titles lead a name but are useless as a search on their own.
TITLES = {
    "mr", "mrs", "ms", "dr", "det", "sgt", "lt", "capt", "cmdr", "gov", "sen",
    "rep", "prof", "st", "jr", "sr", "officer", "detective", "judge", "agent",
}
CALENDAR = {
    "january", "february", "march", "april", "may", "june", "july", "august",
    "september", "october", "november", "december", "monday", "tuesday",
    "wednesday", "thursday", "friday", "saturday", "sunday",
}

SENTENCE_START_STOP = {
    "The", "A", "An", "It", "He", "She", "They", "That", "This", "There", "But",
    "And", "By", "In", "On", "At", "For", "When", "After", "Before", "His",
    "Her", "Their", "Its", "No", "Nobody", "Then", "Within", "With", "As", "Two",
    "One", "By", "From", "Money", "Most", "Every", "Each", "What", "Which",
}

# Atmosphere vocabulary. These carry the true-crime look and, unlike names,
# stock libraries are full of them.
MOOD_BY_TOPIC = {
    "warehouse": "abandoned warehouse interior night",
    "street": "empty city street at night rain",
    "court": "empty courtroom interior",
    "courtroom": "empty courtroom interior",
    "police": "police lights at night",
    "prison": "prison corridor bars",
    "jail": "prison corridor bars",
    "money": "cash bundles on a table low light",
    "ledger": "old ledger book handwriting close up",
    "car": "vintage car on a dark road",
    "phone": "old rotary telephone dim light",
    "gun": "evidence table dim light",
    "body": "police tape at night",
    "murder": "police tape at night",
    "trial": "empty courtroom interior",
    "family": "old family photographs on a table",
    "winter": "snow falling on a city street at night",
    "night": "city skyline at night",
    "document": "typewritten documents close up",
    "evidence": "evidence boxes in a storage room",
    "detective": "detective desk case files lamp",
    "harbour": "docks at night fog",
    "harbor": "docks at night fog",
    "warehouseman": "loading dock at night",
    "bar": "empty bar interior dim",
    "restaurant": "empty restaurant booth dim light",
    "hotel": "hotel corridor dim light",
    "funeral": "cemetery in fog",
    "grave": "cemetery in fog",
}

GENERIC_FALLBACKS = [
    "dark empty street at night",
    "rain on a window at night",
    "old newspaper archive close up",
    "dim room with venetian blind shadows",
    "city skyline at night moody",
]

STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "of", "to", "in", "on", "at", "for",
    "with", "was", "were", "is", "are", "be", "been", "had", "has", "have",
    "he", "she", "they", "it", "his", "her", "their", "its", "that", "this",
    "there", "then", "than", "as", "by", "from", "into", "out", "up", "down",
    "over", "who", "would", "could", "not", "no", "so", "if", "when", "what",
    "which", "one", "two", "about", "after", "before", "him", "them", "you",
    "your", "we", "our", "all", "more", "most", "other", "still", "later",
    "would", "never", "always", "very", "just", "only", "also", "first", "last",
}


@dataclass
class SceneQueries:
    """Search terms for one scene, most specific first."""
    archival: list[str] = field(default_factory=list)   # named subjects, for archives
    stock: list[str] = field(default_factory=list)      # atmosphere, for stock libraries
    entities: list[str] = field(default_factory=list)
    years: list[str] = field(default_factory=list)

    def ladder(self, prefer_archival: bool) -> list[str]:
        """Queries to try in order, always ending somewhere that has results."""
        primary = self.archival if prefer_archival else self.stock
        secondary = self.stock if prefer_archival else self.archival
        seen: set[str] = set()
        out: list[str] = []
        for query in [*primary, *secondary, *GENERIC_FALLBACKS]:
            key = query.lower().strip()
            if key and key not in seen:
                seen.add(key)
                out.append(query)
        return out


def entities(text: str) -> list[str]:
    """Proper-noun runs, minus the ones that are just sentence openers."""
    found: list[str] = []
    for match in _PROPER_RUN.finditer(text):
        phrase = match.group(0).strip()
        words = phrase.split()
        # Strip a leading capitalised stopword ("The Calabria family").
        while words and words[0] in SENTENCE_START_STOP:
            words = words[1:]
        if not words:
            continue
        # Stop at a period that actually ends a sentence, so a run cannot run
        # from one sentence into the capitalised first word of the next
        # ("Mercer Street. It"). A title or a single initial keeps its period.
        clipped: list[str] = []
        for word in words:
            bare = word.rstrip(".")
            clipped.append(word)
            if word.endswith(".") and bare.lower() not in TITLES and len(bare) > 2:
                clipped[-1] = bare
                break
        words = clipped
        # A bare title or a date is not a subject anyone can search for.
        meaningful = [w for w in words if w.rstrip(".").lower() not in TITLES]
        if not meaningful:
            continue
        phrase = " ".join(words)
        bare = [w.rstrip(".").lower() for w in meaningful]
        if all(w in CALENDAR for w in bare):
            continue
        if len(phrase.rstrip(".")) < 3 or phrase.lower() in STOPWORDS:
            continue
        if phrase not in found:
            found.append(phrase)
    return found


def _topic_terms(text: str) -> list[str]:
    """Atmosphere phrases for topics the scene actually mentions.

    Matched on word boundaries: a substring test makes "careful" hit "car"
    and quietly sources a photo of a vintage car for a scene about paperwork.
    """
    tokens = {w.lower() for w in _WORD.findall(text)}
    hits = [phrase for keyword, phrase in MOOD_BY_TOPIC.items() if keyword in tokens]
    return list(dict.fromkeys(hits))


def _content_words(text: str, limit: int = 5) -> list[str]:
    out: list[str] = []
    for word in _WORD.findall(text):
        low = word.lower()
        if low in STOPWORDS or len(low) < 4 or word[:1].isupper():
            continue
        if low not in out:
            out.append(low)
        if len(out) >= limit:
            break
    return out


def build(text: str, image_prompt: str = "") -> SceneQueries:
    """Query ladder for one scene."""
    names = entities(text)
    years = _YEAR.findall(text)
    topics = _topic_terms(f"{text} {image_prompt}")
    words = _content_words(text)

    archival: list[str] = []
    for name in names[:3]:
        archival.append(f"{name} {years[0]}" if years else name)
    archival.extend(names[:3])
    if names and topics:
        archival.append(f"{names[0]} {topics[0]}")

    stock: list[str] = []
    stock.extend(topics[:3])
    if words:
        stock.append(" ".join(words[:3]))
    if topics and years:
        stock.append(f"{topics[0]} {years[0][:3]}0s")
    # The image prompt's leading clause is usually a decent visual description.
    if image_prompt:
        head = image_prompt.split(" - ")[0].strip()
        if 3 < len(head) < 80:
            stock.append(head)

    return SceneQueries(
        archival=[q for q in dict.fromkeys(archival) if q],
        stock=[q for q in dict.fromkeys(stock) if q],
        entities=names,
        years=years,
    )


# ---------------------------------------------------------------------------
# Choosing between results
# ---------------------------------------------------------------------------

def score_candidate(candidate, *, query: str, want_landscape: bool = True) -> float:
    """Rank search hits. Deliberately simple and explainable."""
    score = 0.0
    title = f"{getattr(candidate, 'title', '')}".lower()
    for term in query.lower().split():
        if len(term) > 3 and term in title:
            score += 1.5

    width = getattr(candidate, "width", 0) or 0
    height = getattr(candidate, "height", 0) or 0
    if width and height:
        if want_landscape and width > height:
            score += 1.0
        if width >= 1920:
            score += 1.0
        elif width < 1280:
            score -= 1.5      # will go soft once Ken Burns crops into it

    duration = getattr(candidate, "duration", 0) or 0
    if duration:
        # Very short clips loop visibly; very long ones are a slow download for
        # the ten seconds we actually use.
        if 5 <= duration <= 60:
            score += 1.0
        elif duration < 3:
            score -= 1.0
    return score


def pick(candidates: list, *, query: str, used: set[str], want_landscape: bool = True):
    """Best unused candidate, so a chapter does not show one photo repeatedly."""
    ranked = sorted(
        candidates,
        key=lambda c: score_candidate(c, query=query, want_landscape=want_landscape),
        reverse=True,
    )
    for candidate in ranked:
        key = candidate.url or getattr(candidate, "title", "")
        if key and key not in used:
            return candidate
    return ranked[0] if ranked else None
