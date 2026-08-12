"""Script -> scenes, for scripts up to and beyond an hour.

Design note, and a deliberate departure from the original spec: the LLM is
never asked to emit the narration text. It is given numbered sentences and
returns *groupings* and image prompts. Scene text is then sliced straight out
of `Script.raw_text` by character offset.

That change buys two things at hour-long scale:

* Paraphrase becomes structurally impossible rather than something we check
  for afterwards. The narration is the author's words because it is literally
  the same bytes.
* Output tokens drop by roughly half, which is what makes 240 scenes
  affordable and keeps every window well clear of a truncated response.

The verbatim assertion is still run, as a cheap guard against a bug here.
"""

from __future__ import annotations

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

from ..config import settings
from ..providers import llm as llm_providers

log = logging.getLogger("casefile.segmentation")

# Sentence end: . ! ? plus any closing quote or bracket, then whitespace.
# Abbreviations that end in a period ("Det. Halloran") would over-split here;
# _merge_abbreviations below stitches those back together.
_SENT_END = re.compile(r'(?<=[.!?])["\'’”)\]]*\s+')
_ABBREVIATIONS = {
    "mr", "mrs", "ms", "dr", "prof", "st", "jr", "sr", "lt", "sgt", "det",
    "capt", "cmdr", "gov", "sen", "rep", "no", "vs", "approx", "inc", "co",
    "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec",
}
_TRAILING_TOKEN = re.compile(r"([A-Za-z]{1,6})\.\s*$")
_WORD = re.compile(r"\b[\w'’-]+\b")

STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "of", "to", "in", "on", "at", "for", "with",
    "was", "were", "is", "are", "be", "been", "had", "has", "have", "he", "she", "they",
    "it", "his", "her", "their", "its", "that", "this", "there", "then", "than", "as",
    "by", "from", "into", "out", "up", "down", "over", "who", "would", "could", "not",
    "no", "so", "if", "when", "what", "which", "one", "two", "would", "about", "after",
    "before", "him", "them", "you", "your", "we", "our", "all", "more", "most", "other",
}


def word_count(text: str) -> int:
    return len(_WORD.findall(text))


def estimate_runtime(text: str, wpm: int | None = None) -> float:
    return word_count(text) / max(wpm or settings.words_per_minute, 1) * 60.0


# ---------------------------------------------------------------------------
# Sentences
# ---------------------------------------------------------------------------

@dataclass
class Sentence:
    start: int
    end: int          # extends to the start of the next sentence, so spans tile
    text: str         # the trimmed sentence, for prompts and word counts

    @property
    def words(self) -> int:
        return word_count(self.text)


def split_sentences(raw: str) -> list[Sentence]:
    """Split into spans that tile `raw` completely with no gaps.

    Tiling matters: it is what lets the verbatim check be an exact byte
    comparison instead of a fuzzy one.
    """
    if not raw:
        return []

    boundaries = [0]
    for match in _SENT_END.finditer(raw):
        # "Det. Halloran" and "Jan. 1994" are not sentence ends.
        token = _TRAILING_TOKEN.search(raw[boundaries[-1] : match.start() + 1])
        if token and token.group(1).lower() in _ABBREVIATIONS:
            continue
        boundaries.append(match.end())
    boundaries.append(len(raw))

    out: list[Sentence] = []
    for i in range(len(boundaries) - 1):
        start, end = boundaries[i], boundaries[i + 1]
        if end <= start:
            continue
        chunk = raw[start:end]
        if not chunk.strip():
            # Blank run: glue it onto the previous sentence rather than making
            # a sentence out of whitespace.
            if out:
                out[-1] = Sentence(out[-1].start, end, out[-1].text)
            continue
        out.append(Sentence(start, end, chunk.strip()))

    if out:
        out[-1] = Sentence(out[-1].start, len(raw), out[-1].text)
    return out


# ---------------------------------------------------------------------------
# Grouping
# ---------------------------------------------------------------------------

@dataclass
class SceneDraft:
    start: int
    end: int
    text: str
    image_prompt: str = ""
    depicts_real_person: bool = False
    chapter_title: str = ""
    sentence_indices: list[int] = field(default_factory=list)


def _target_words() -> int:
    return max(12, int(settings.scene_target_sec / 60.0 * settings.words_per_minute))


def group_by_length(sentences: list[Sentence], target: int | None = None) -> list[list[int]]:
    """Deterministic fallback grouping, and the seed the LLM is asked to refine."""
    target = target or _target_words()
    groups: list[list[int]] = []
    current: list[int] = []
    running = 0
    for i, sent in enumerate(sentences):
        current.append(i)
        running += sent.words
        if running >= target:
            groups.append(current)
            current, running = [], 0
    if current:
        # A runt tail gets folded into the previous scene rather than left alone.
        if groups and running < target * 0.45:
            groups[-1].extend(current)
        else:
            groups.append(current)
    return groups


def _keyword_prompt(text: str) -> str:
    """Image prompt without an LLM: the scene's own distinctive words."""
    words = [w for w in _WORD.findall(text)]
    proper = [w for w in words if w[:1].isupper() and w.lower() not in STOPWORDS]
    plain = [w.lower() for w in words if w.lower() not in STOPWORDS and len(w) > 3]
    seen: set[str] = set()
    picked: list[str] = []
    for w in proper + plain:
        key = w.lower()
        if key not in seen:
            seen.add(key)
            picked.append(w)
        if len(picked) >= 7:
            break
    subject = ", ".join(picked) if picked else "empty street at night"
    return f"{subject} - atmospheric establishing shot, no people identifiable"


SYSTEM_PROMPT = """You are a video editor segmenting a true-crime narration script into scenes.

You will be given numbered sentences. Group CONSECUTIVE sentences into scenes.

Rules:
- Never reorder, never skip, never split a sentence. Every index must appear exactly once, in order.
- Aim for {target} words per scene. Prefer a natural beat change over hitting the number exactly.
- For each scene write an `image_prompt`: a short visual description for a
  documentary B-roll still. Describe places, objects, atmosphere, era. Do NOT
  describe a recognisable real person.
- Set `real_person` to true when the scene is ABOUT a specific named real
  individual, so the app can route it to archival photos instead of AI.
- Give the first scene of each new topic a `chapter` title of 2-5 words; leave
  `chapter` empty otherwise. Aim for a new chapter every 20-30 scenes.

Return JSON only, of the form:
{{"scenes": [{{"s": [0,1,2], "image_prompt": "...", "real_person": false, "chapter": "Early Years"}}]}}
"""


def _llm_group(
    provider: llm_providers.LLMProvider,
    sentences: list[Sentence],
    offset: int,
) -> list[dict[str, Any]] | None:
    listing = "\n".join(f"{i}. {s.text[:400]}" for i, s in enumerate(sentences))
    system = SYSTEM_PROMPT.format(target=_target_words())
    try:
        payload = provider.complete_json(
            system, listing, max_tokens=min(8000, 400 + 90 * max(1, len(sentences) // 2))
        )
    except Exception as exc:
        log.warning("LLM grouping failed for window at %d: %s", offset, exc)
        return None

    scenes = payload.get("scenes") if isinstance(payload, dict) else payload
    if not isinstance(scenes, list) or not scenes:
        return None
    return scenes


def _validate_groups(raw_scenes: list[dict[str, Any]], count: int) -> list[dict[str, Any]] | None:
    """Reject a grouping that drops, duplicates, or reorders sentences.

    Silently accepting a bad grouping would mean silently dropping narration,
    which is the one failure mode that must never reach a render.
    """
    seen: list[int] = []
    cleaned: list[dict[str, Any]] = []
    for scene in raw_scenes:
        if not isinstance(scene, dict):
            return None
        idx = scene.get("s") or scene.get("sentences") or []
        if not isinstance(idx, list):
            return None
        idx = [int(i) for i in idx if isinstance(i, (int, float, str)) and str(i).lstrip("-").isdigit()]
        if not idx:
            continue
        seen.extend(idx)
        cleaned.append({**scene, "s": idx})

    if seen != sorted(seen) or seen != list(range(count)):
        return None
    return cleaned


def segment(
    raw_text: str,
    *,
    llm_name: str | None = None,
    window_words: int = 900,
    max_workers: int = 4,
    progress: Any = None,
) -> tuple[list[SceneDraft], str]:
    """Split a script into scenes. Returns (scenes, method_used)."""
    sentences = split_sentences(raw_text)
    if not sentences:
        return [], "empty"

    provider = llm_providers.get_provider(llm_name) if llm_name else llm_providers.best_available()
    use_llm = provider.name != "heuristic" and provider.available()[0]

    # Windows of whole sentences. No overlap is needed because the LLM only
    # regroups indices we already own, so there is no seam to stitch.
    windows: list[tuple[int, int]] = []
    start = 0
    running = 0
    for i, sent in enumerate(sentences):
        running += sent.words
        if running >= window_words:
            windows.append((start, i + 1))
            start, running = i + 1, 0
    if start < len(sentences):
        windows.append((start, len(sentences)))

    groupings: dict[int, list[dict[str, Any]]] = {}
    method = "heuristic"

    if use_llm:
        def work(item: tuple[int, tuple[int, int]]) -> tuple[int, list[dict[str, Any]] | None]:
            wi, (a, b) = item
            chunk = sentences[a:b]
            result = _llm_group(provider, chunk, a)
            return wi, _validate_groups(result, len(chunk)) if result else None

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            for wi, result in pool.map(work, list(enumerate(windows))):
                if result:
                    groupings[wi] = result
                if progress:
                    progress(len(groupings) / max(len(windows), 1))
        if groupings:
            method = f"llm:{provider.name}" if len(groupings) == len(windows) else f"llm:{provider.name}+fallback"

    drafts: list[SceneDraft] = []
    for wi, (a, b) in enumerate(windows):
        chunk = sentences[a:b]
        result = groupings.get(wi)
        if result:
            for scene in result:
                idx = [a + i for i in scene["s"]]
                drafts.append(_make_draft(
                    sentences, idx,
                    prompt=str(scene.get("image_prompt") or "").strip(),
                    real_person=bool(scene.get("real_person")),
                    chapter=str(scene.get("chapter") or "").strip(),
                ))
        else:
            for group in group_by_length(chunk):
                drafts.append(_make_draft(sentences, [a + i for i in group]))

    drafts = _enforce_limits(drafts, sentences)
    _assert_verbatim(drafts, raw_text)
    return drafts, method


def _make_draft(
    sentences: list[Sentence],
    indices: list[int],
    *,
    prompt: str = "",
    real_person: bool = False,
    chapter: str = "",
) -> SceneDraft:
    indices = sorted(indices)
    start = sentences[indices[0]].start
    end = sentences[indices[-1]].end
    text = " ".join(sentences[i].text for i in indices)
    return SceneDraft(
        start=start, end=end, text=text,
        image_prompt=prompt or _keyword_prompt(text),
        depicts_real_person=real_person,
        chapter_title=chapter,
        sentence_indices=indices,
    )


def _enforce_limits(drafts: list[SceneDraft], sentences: list[Sentence]) -> list[SceneDraft]:
    """Keep the scene count sane on a very long script by merging, never by
    dropping. Merging preserves the tiling; dropping would lose narration."""
    if len(drafts) <= settings.max_scenes:
        return drafts

    factor = (len(drafts) + settings.max_scenes - 1) // settings.max_scenes
    merged: list[SceneDraft] = []
    for i in range(0, len(drafts), factor):
        window = drafts[i : i + factor]
        idx = [j for d in window for j in d.sentence_indices]
        merged.append(_make_draft(
            sentences, idx,
            prompt=window[0].image_prompt,
            real_person=any(d.depicts_real_person for d in window),
            chapter=next((d.chapter_title for d in window if d.chapter_title), ""),
        ))
    log.info("merged %d scenes down to %d to stay under the cap", len(drafts), len(merged))
    return merged


def _assert_verbatim(drafts: list[SceneDraft], raw_text: str) -> None:
    """The narration must be the author's bytes. Non-negotiable (spec 15.2)."""
    if not drafts:
        return
    rebuilt = "".join(raw_text[d.start : d.end] for d in drafts)
    if rebuilt != raw_text:
        raise ValueError(
            "Segmentation did not tile the script exactly - refusing to continue. "
            f"Rebuilt {len(rebuilt)} chars from {len(raw_text)}."
        )


# ---------------------------------------------------------------------------
# Chapters
# ---------------------------------------------------------------------------

def assign_chapters(drafts: list[SceneDraft], *, min_scenes: int = 12) -> list[tuple[str, int]]:
    """Return (title, first_scene_index) pairs.

    Honours LLM-supplied chapter titles, then falls back to an even split so an
    hour-long video always has usable YouTube chapter markers.
    """
    marks = [(d.chapter_title, i) for i, d in enumerate(drafts) if d.chapter_title]
    # Drop chapters that would be too short to be worth a marker.
    filtered: list[tuple[str, int]] = []
    for title, index in marks:
        if not filtered or index - filtered[-1][1] >= min_scenes:
            filtered.append((title, index))
    if filtered and filtered[0][1] != 0:
        filtered.insert(0, ("Introduction", 0))
    if filtered:
        return filtered

    target = max(min_scenes, len(drafts) // max(1, round(len(drafts) / 25)) or 25)
    return [(f"Part {n + 1}", i) for n, i in enumerate(range(0, len(drafts), target))]


def to_json(drafts: list[SceneDraft]) -> str:
    return json.dumps([d.__dict__ for d in drafts], ensure_ascii=False)
