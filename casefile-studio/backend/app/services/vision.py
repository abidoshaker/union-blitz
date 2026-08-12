"""Face detection and blurring.

Read this before relying on it: automatic face detection is an assist, not a
guarantee. YuNet finds most clear, reasonably frontal faces; it misses faces in
profile, heavy shadow, motion blur, low resolution, and crowds. Publishing a
video with an unblurred identifiable face because a detector missed it is your
exposure, not the detector's.

So: blurring is applied conservatively (generous padding, held across detection
gaps), the count of what was found is always reported back, and the UI tells
the user to eyeball the result. It is never presented as "all faces removed".
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from ..config import settings

log = logging.getLogger("casefile.vision")

MODEL_NAME = "face_detection_yunet_2023mar.onnx"
MODEL_URLS = [
    "https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/"
    "face_detection_yunet/face_detection_yunet_2023mar.onnx",
]

# Boxes are padded outwards before blurring: a detector's box is tight on the
# face and a tight blur leaves hairline, jaw and ears legible.
PAD_RATIO = 0.35
SCORE_THRESHOLD = 0.55
# Detection gaps shorter than this are bridged rather than flickering off.
BRIDGE_SEC = 1.0
# Blocks across a face box. Fixed count, not fixed kernel: identity survives a
# fixed-size blur on a large face.
BLOCKS = 8


@dataclass
class Box:
    x: int
    y: int
    w: int
    h: int
    score: float = 1.0

    def padded(self, frame_w: int, frame_h: int, ratio: float = PAD_RATIO) -> "Box":
        px, py = int(self.w * ratio), int(self.h * ratio)
        x = max(0, self.x - px)
        y = max(0, self.y - py)
        w = min(frame_w - x, self.w + px * 2)
        h = min(frame_h - y, self.h + py * 2)
        return Box(x, y, max(2, w), max(2, h), self.score)

    def iou(self, other: "Box") -> float:
        ax2, ay2 = self.x + self.w, self.y + self.h
        bx2, by2 = other.x + other.w, other.y + other.h
        ix = max(0, min(ax2, bx2) - max(self.x, other.x))
        iy = max(0, min(ay2, by2) - max(self.y, other.y))
        inter = ix * iy
        union = self.w * self.h + other.w * other.h - inter
        return inter / union if union else 0.0

    def union(self, other: "Box") -> "Box":
        x, y = min(self.x, other.x), min(self.y, other.y)
        x2 = max(self.x + self.w, other.x + other.w)
        y2 = max(self.y + self.h, other.y + other.h)
        return Box(x, y, x2 - x, y2 - y, max(self.score, other.score))


@dataclass
class Track:
    """One blurred region, valid over a span of time.

    A moving face becomes several consecutive Tracks rather than one big box:
    a single union box either has to grow to cover the whole path - blurring
    most of the frame - or the face walks out of it. Consecutive regions are
    contiguous in time and overlap once the hold is applied, so coverage has no
    gaps.
    """
    box: Box
    start: float
    end: float


@dataclass
class _Candidate:
    """One face followed across samples, before being cut into regions."""
    samples: list[tuple[float, Box]]
    last_box: Box
    last_t: float

    @property
    def area(self) -> int:
        return self.last_box.w * self.last_box.h


def model_path() -> Path:
    return settings.models_dir / MODEL_NAME


def available() -> tuple[bool, str]:
    try:
        import cv2  # noqa: F401
    except ImportError:
        return False, "opencv-python-headless is not installed. Re-run install-deps.bat."
    if not model_path().exists():
        return False, "The face detection model is missing. Run tools/fetch_models.py."
    return True, ""


_detector = None
_detector_size = (0, 0)


def _get_detector(width: int, height: int):
    global _detector, _detector_size
    import cv2

    if _detector is None:
        _detector = cv2.FaceDetectorYN.create(
            str(model_path()), "", (width, height), SCORE_THRESHOLD, 0.3, 5000
        )
        _detector_size = (width, height)
    elif _detector_size != (width, height):
        _detector.setInputSize((width, height))
        _detector_size = (width, height)
    return _detector


def detect(frame) -> list[Box]:
    """Faces in a single BGR frame."""
    height, width = frame.shape[:2]
    detector = _get_detector(width, height)
    _, faces = detector.detect(frame)
    if faces is None:
        return []
    out = []
    for face in faces:
        x, y, w, h = (int(v) for v in face[:4])
        if w < 8 or h < 8:
            continue
        out.append(Box(max(0, x), max(0, y), w, h, float(face[-1])))
    return out


# ---------------------------------------------------------------------------
# Stills
# ---------------------------------------------------------------------------

def blur_image_faces(src: Path, dest: Path, *, strength: float = 1.0) -> int:
    """Write a copy of `src` with faces blurred. Returns how many were found.

    Baked into a derived file rather than done in the filtergraph: a still's
    faces never move, so this is computed once and cached with the asset.
    """
    usable, reason = available()
    if not usable:
        raise RuntimeError(reason)
    import cv2

    image = cv2.imread(str(src))
    if image is None:
        raise RuntimeError(f"Could not read {src}")

    height, width = image.shape[:2]
    boxes = [b.padded(width, height) for b in detect(image)]
    for box in boxes:
        region = image[box.y : box.y + box.h, box.x : box.x + box.w]
        if region.size == 0:
            continue
        # Pixelate to a fixed *number of blocks* rather than a fixed kernel:
        # scaling the kernel with the face leaves a large face as legible as a
        # small one, which is the failure mode that matters here. Eight blocks
        # across destroys identity at any resolution. The Gaussian afterwards
        # only softens the block edges so it does not read as a censor bar.
        blocks = max(3, int(8 / max(strength, 0.2)))
        aspect = max(1, round(blocks * box.h / max(box.w, 1)))
        small = cv2.resize(region, (blocks, aspect), interpolation=cv2.INTER_AREA)
        pixelated = cv2.resize(small, (box.w, box.h), interpolation=cv2.INTER_NEAREST)
        k = max(3, int(min(box.w, box.h) / 10) | 1)
        image[box.y : box.y + box.h, box.x : box.x + box.w] = cv2.GaussianBlur(pixelated, (k, k), 0)

    dest.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(dest), image, [cv2.IMWRITE_JPEG_QUALITY, 92])
    return len(boxes)


# ---------------------------------------------------------------------------
# Video
# ---------------------------------------------------------------------------

def detect_video_faces(
    src: Path,
    *,
    sample_fps: float = 3.0,
    start: float = 0.0,
    duration: float | None = None,
    max_tracks: int = 40,
) -> list[Track]:
    """Sample the clip and group detections into time-ranged regions.

    Sampling rather than every-frame detection: at 3 fps a 15-second clip is 45
    detections instead of 450, and the padding plus gap bridging covers the
    frames in between.
    """
    usable, reason = available()
    if not usable:
        raise RuntimeError(reason)
    import cv2

    capture = cv2.VideoCapture(str(src))
    if not capture.isOpened():
        raise RuntimeError(f"Could not open {src}")

    try:
        fps = capture.get(cv2.CAP_PROP_FPS) or 25.0
        step = max(1, int(round(fps / max(sample_fps, 0.5))))
        end = start + duration if duration else None
        if start > 0:
            capture.set(cv2.CAP_PROP_POS_MSEC, start * 1000.0)

        candidates: list[_Candidate] = []
        index = 0
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            timestamp = capture.get(cv2.CAP_PROP_POS_MSEC) / 1000.0
            if end is not None and timestamp > end:
                break
            if index % step == 0:
                height, width = frame.shape[:2]
                for box in (b.padded(width, height) for b in detect(frame)):
                    _extend(candidates, box, timestamp)
            index += 1
    finally:
        capture.release()

    tracks: list[Track] = []
    for candidate in candidates:
        tracks.extend(_to_regions(candidate))

    tracks.sort(key=lambda t: (t.start, -t.box.w * t.box.h))
    if len(tracks) > max_tracks:
        # Too many regions would build an unwieldy filtergraph. Coarsen rather
        # than drop coverage: merge neighbours into bigger, longer boxes.
        tracks = _coarsen(tracks, max_tracks)
    return tracks


def _extend(candidates: list[_Candidate], box: Box, timestamp: float) -> None:
    """Attach a detection to the face it most likely belongs to."""
    best: _Candidate | None = None
    best_iou = 0.0
    for candidate in candidates:
        if timestamp - candidate.last_t > BRIDGE_SEC:
            continue
        overlap = candidate.last_box.iou(box)
        if overlap > best_iou:
            best, best_iou = candidate, overlap

    if best is not None and best_iou > 0.15:
        best.samples.append((timestamp, box))
        best.last_box = box
        best.last_t = timestamp
    else:
        candidates.append(_Candidate(samples=[(timestamp, box)], last_box=box, last_t=timestamp))


def _to_regions(candidate: _Candidate, growth_limit: float = 2.0) -> list[Track]:
    """Cut one followed face into contiguous regions that track its movement.

    A region accumulates samples until its box would have to grow past
    `growth_limit` times the area it started at; then it closes and the next
    region begins at that same instant, so the regions tile the face's lifetime.
    """
    samples = candidate.samples
    if not samples:
        return []

    regions: list[Track] = []
    i = 0
    while i < len(samples):
        start_t, box = samples[i]
        accumulated = box
        limit = box.w * box.h * growth_limit
        j = i + 1
        while j < len(samples):
            merged = accumulated.union(samples[j][1])
            if merged.w * merged.h > limit:
                break
            accumulated = merged
            j += 1
        end_t = samples[j][0] if j < len(samples) else candidate.last_t
        regions.append(Track(box=accumulated, start=start_t, end=max(end_t, start_t)))
        i = j
    return regions


def _coarsen(tracks: list[Track], target: int) -> list[Track]:
    """Merge the closest neighbours until the region count fits the budget."""
    tracks = sorted(tracks, key=lambda t: t.start)
    while len(tracks) > target:
        merged: list[Track] = []
        for i in range(0, len(tracks), 2):
            pair = tracks[i : i + 2]
            if len(pair) == 1:
                merged.append(pair[0])
            else:
                merged.append(Track(
                    box=pair[0].box.union(pair[1].box),
                    start=min(pair[0].start, pair[1].start),
                    end=max(pair[0].end, pair[1].end),
                ))
        tracks = merged
    return tracks


def blur_filter_chain(
    tracks: list[Track],
    *,
    label_in: str,
    label_out: str,
    time_offset: float = 0.0,
    hold: float = 0.4,
) -> str:
    """FFmpeg filtergraph that blurs each track's region over its time range.

    Applied in source pixel coordinates, before any scale or crop, because that
    is the space the detector worked in.
    """
    if not tracks:
        return f"[{label_in}]null[{label_out}]"

    parts: list[str] = []
    current = label_in
    for i, track in enumerate(tracks):
        box = track.box
        x, y = box.x // 2 * 2, box.y // 2 * 2
        w, h = max(2, box.w // 2 * 2), max(2, box.h // 2 * 2)
        # Hold the blur slightly either side of the detected span so a face
        # entering or leaving frame is covered before it is recognisable.
        t0 = max(0.0, track.start - time_offset - hold)
        t1 = track.end - time_offset + hold
        nxt = label_out if i == len(tracks) - 1 else f"bl{i}"
        # Same treatment as the stills path: downsample to a fixed block count
        # so a large face is destroyed as thoroughly as a small one, then a
        # light average blur to soften the block edges.
        blocks_y = max(1, round(BLOCKS * h / max(w, 1)))
        k = max(1, min(w, h) // 20)
        parts.append(
            f"[{current}]split[b{i}a][b{i}b];"
            f"[b{i}b]crop={w}:{h}:{x}:{y},"
            f"scale={BLOCKS}:{blocks_y}:flags=neighbor,"
            f"scale={w}:{h}:flags=neighbor,"
            f"avgblur=sizeX={k}:sizeY={k}[b{i}c];"
            f"[b{i}a][b{i}c]overlay={x}:{y}:enable='between(t\\,{t0:.3f}\\,{t1:.3f})'[{nxt}]"
        )
        current = nxt
    return ";".join(parts)
