"""Finding the parts of an image that need censoring, automatically.

Two models, because neither wins everywhere. Compared over one real project
of 529 hand censored images, the newer one found more in 177 of them and
the older one found more in 109, so pooling what both say beats either.

    EraX-NSFW-V1.0          Apache-2.0, 2025, YOLO11
                            anus, nipple, penis, vagina
    anime_censor_detection  MIT, 2023, trained on drawn art
                            nipple, penis, vagina

Measured on that project, at the thresholds below:

                     finds the image   penis   anus   boxes on scenery
    2023 alone             82%          30%     none        2.0%
    2025 alone             84%          48%     16%         0.0%
    both together          89%          57%     16%         3.0%

What neither can do is worth knowing before trusting them. About one image
in ten that was censored by hand shows nothing to either, and neither has
any notion of something censored for framing rather than for what it shows.

Models are not shipped with the app. They are tens of megabytes, only
wanted by people who turn this on, and fetching them on demand keeps the
download small for everyone else.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

# What the app talks about, whatever a given model happens to call it.
NIPPLE = "nipple"
PENIS = "penis"
VAGINA = "vagina"
ANUS = "anus"
KINDS = (NIPPLE, PENIS, VAGINA, ANUS)


@dataclass(frozen=True)
class ModelSpec:
    """One detector: where to get it, and how to read what it says."""
    key: str
    title: str
    filename: str
    url: str
    sha256: str
    size_bytes: int
    raw_labels: tuple[str, ...]
    # Raw label to one of KINDS. Anything left out is ignored, which is how
    # EraX's make_love class, a whole scene rather than a part, is dropped.
    kinds: dict[str, str]
    threshold: float
    licence: str
    note: str
    inference_size: int = 640

    @property
    def can_be_downloaded(self) -> bool:
        return bool(self.url and self.sha256)


ANIME_MODEL = ModelSpec(
    key="anime",
    title="Drawn art model (2023)",
    filename="anime_censor_detection_v1.0_s.onnx",
    url=("https://huggingface.co/deepghs/anime_censor_detection/"
         "resolve/main/censor_detect_v1.0_s/model.onnx"),
    sha256="2c2524824d7d320c5619a0a73702a2e2186f619067c823d211e94f7cfe489cba",
    size_bytes=44586353,
    raw_labels=("nipple_f", "penis", "pussy"),
    kinds={"nipple_f": NIPPLE, "penis": PENIS, "pussy": VAGINA},
    # Where this model's own published F1 peaks.
    threshold=0.238,
    licence="MIT, deepghs/anime_censor_detection",
    note="Trained on drawn art. Finds images the newer one misses.",
)

# Converted to ONNX from the PyTorch weights the authors publish, since
# they publish no ONNX themselves. Apache-2.0 permits that with the licence
# and attribution kept, which is what `licence` carries. The url is filled
# in once the converted file is published somewhere to fetch it from; until
# then a copy dropped into the models folder by hand still works.
ERAX_MODEL = ModelSpec(
    key="erax",
    title="EraX NSFW (2025)",
    filename="erax-nsfw-v1.0-s.onnx",
    url="",
    sha256="",
    size_bytes=0,
    raw_labels=("anus", "make_love", "nipple", "penis", "vagina"),
    kinds={"anus": ANUS, "nipple": NIPPLE, "penis": PENIS, "vagina": VAGINA},
    threshold=0.15,
    licence="Apache-2.0, EraX-AI, Pham Dinh Thuc and Nguyen Anh Nguyen",
    note="Much better at penises, and the only one that knows anus.",
)

MODELS: dict[str, ModelSpec] = {spec.key: spec for spec in (ANIME_MODEL, ERAX_MODEL)}

# Two boxes overlapping by more than this are treated as the same thing,
# which is what stops both models reporting one nipple twice.
MERGE_IOU = 0.45

# Overlap for the slower second pass. Enough that a part straddling a tile
# edge lands whole in a neighbouring one.
TILE_OVERLAP = 0.2

_LETTERBOX_GREY = (114, 114, 114)


class DetectorUnavailable(RuntimeError):
    """Detection cannot run, and the message says what is missing."""


@dataclass(frozen=True)
class Detection:
    """One thing found, in the same units a censor layer uses.

    The rect is fractions of image size rather than pixels, matching
    CensorLayer, so a detection can become a layer without a conversion and
    survives the image being a different size later.
    """
    label: str  # one of KINDS
    score: float
    rect: tuple[float, float, float, float]
    found_by: str = ""


def cache_dir() -> Path:
    """Where downloaded models live.

    Beside the user's other application data rather than next to the exe,
    which may be somewhere they cannot write, and rather than in the
    project folder, since one model serves every project.
    """
    base = os.environ.get("ALICE_CENSOR_HOME")
    if base:
        return Path(base)
    local = os.environ.get("LOCALAPPDATA")
    if local:
        return Path(local) / "Alice Censor"
    return Path.home() / ".alice-censor"


def model_path(spec: ModelSpec) -> Path:
    return cache_dir() / "models" / spec.filename


def model_is_present(spec: ModelSpec) -> bool:
    """Whether the file is there and is the one expected.

    A size of zero in the spec means no published download to compare
    against, so the file being there is all there is to go on. That is what
    lets a converted model be dropped in by hand before it is hosted.
    """
    path = model_path(spec)
    if not path.is_file():
        return False
    return spec.size_bytes == 0 or path.stat().st_size == spec.size_bytes


def runtime_is_present() -> bool:
    """Whether this build can run a model at all.

    onnxruntime nearly doubles the size of the exe, so a build without it
    is a reasonable thing to be using. The menu explains that rather than
    failing when the button is pressed.
    """
    try:
        import onnxruntime  # noqa: F401
    except ImportError:
        return False
    return True


def usable_models() -> list[ModelSpec]:
    """Models this installation could actually run right now."""
    if not runtime_is_present():
        return []
    return [spec for spec in MODELS.values() if model_is_present(spec)]


def missing_models() -> list[ModelSpec]:
    """Models that could be fetched but have not been."""
    return [spec for spec in MODELS.values()
            if spec.can_be_downloaded and not model_is_present(spec)]


def unavailable_reason() -> str | None:
    """Why detection cannot run, or None if it can."""
    if not runtime_is_present():
        return (
            "This build does not include the detection runtime. Install "
            "onnxruntime and run from source, or use the build that has it."
        )
    if not usable_models():
        return "No detection model has been downloaded yet."
    return None


def download_model(spec: ModelSpec, on_progress=None) -> Path:
    """Fetch a model, checking it arrived intact.

    Written to a temporary name and moved into place at the end, so an
    interrupted download cannot leave something that looks usable.
    """
    if not spec.can_be_downloaded:
        raise DetectorUnavailable(f"{spec.title} has no published download yet")

    destination = model_path(spec)
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(".partial")

    digest = hashlib.sha256()
    try:
        with urllib.request.urlopen(spec.url, timeout=60) as response, \
                partial.open("wb") as out:
            done = 0
            while True:
                chunk = response.read(1 << 18)
                if not chunk:
                    break
                out.write(chunk)
                digest.update(chunk)
                done += len(chunk)
                if on_progress:
                    on_progress(done, spec.size_bytes)
    except (urllib.error.URLError, OSError, TimeoutError, ValueError) as exc:
        partial.unlink(missing_ok=True)
        raise DetectorUnavailable(f"could not download {spec.title}, {exc}") from exc

    if digest.hexdigest() != spec.sha256:
        partial.unlink(missing_ok=True)
        raise DetectorUnavailable(
            f"the downloaded {spec.title} does not match its expected checksum, "
            "so it was discarded rather than used"
        )
    shutil.move(str(partial), str(destination))
    return destination


class Detector:
    """One loaded model, ready to be asked about images.

    Loading takes long enough to be worth doing once, so a caller should
    keep one for a whole scan rather than per image.
    """

    def __init__(self, spec: ModelSpec, path: str | Path | None = None):
        try:
            import onnxruntime
        except ImportError as exc:
            raise DetectorUnavailable(
                "this build has no detection runtime, so nothing can be detected"
            ) from exc

        self.spec = spec
        resolved = Path(path) if path else model_path(spec)
        if not resolved.is_file():
            raise DetectorUnavailable(f"{spec.title} is not at {resolved}")
        self._session = onnxruntime.InferenceSession(
            str(resolved), providers=["CPUExecutionProvider"]
        )
        self._input = self._session.get_inputs()[0].name

    def detect(
        self,
        image: Image.Image,
        *,
        threshold: float | None = None,
        deep: bool = False,
    ) -> list[Detection]:
        """Everything this model finds, in the app's own vocabulary."""
        if image.mode != "RGB":
            image = image.convert("RGB")
        width, height = image.size
        if width == 0 or height == 0:
            return []

        cutoff = self.spec.threshold if threshold is None else threshold
        found = self._pass(image, cutoff, (0, 0), (width, height))
        if deep:
            for box in _tiles(width, height):
                crop = image.crop(box)
                found += self._pass(crop, cutoff, box[:2], (width, height))
        return merge(found)

    def _pass(self, image, threshold, origin, full_size) -> list[Detection]:
        import numpy as np

        # Padded into the corner of a square, then scaled. Ultralytics
        # centres its padding instead, and on this material the corner is
        # measurably better, 84% of images found against 82%.
        side = max(image.size)
        canvas = Image.new("RGB", (side, side), _LETTERBOX_GREY)
        canvas.paste(image, (0, 0))
        size = self.spec.inference_size
        blob = np.asarray(
            canvas.resize((size, size), Image.BILINEAR), dtype=np.float32
        ) / 255.0
        raw = self._session.run(
            None, {self._input: blob.transpose(2, 0, 1)[None, ...]}
        )[0][0]

        # One row per candidate box. Which axis is which varies with how the
        # model was exported, so it is decided by the axis whose length is
        # the number of fields rather than by whichever happens to be
        # larger, which would get it backwards on an image with almost
        # nothing in it.
        labels = self.spec.raw_labels
        fields = 4 + len(labels)
        if raw.ndim != 2:
            return []
        if raw.shape[0] == fields and raw.shape[1] != fields:
            raw = raw.T
        if raw.shape[1] < fields:
            return []

        scores = raw[:, 4:fields]
        confidence = scores.max(1)
        keep = confidence >= threshold
        if not keep.any():
            return []

        scale = side / size
        origin_x, origin_y = origin
        full_width, full_height = full_size
        out = []
        for (cx, cy, w, h), index, score in zip(
            raw[keep, :4], scores.argmax(1)[keep], confidence[keep]
        ):
            kind = self.spec.kinds.get(labels[int(index)])
            if kind is None:
                continue
            left = (cx - w / 2) * scale + origin_x
            top = (cy - h / 2) * scale + origin_y
            # Plain floats, not numpy ones. These end up in the project
            # file, and json cannot encode a float32.
            out.append(Detection(
                label=kind,
                score=float(score),
                rect=(float(left / full_width), float(top / full_height),
                      float(w * scale / full_width), float(h * scale / full_height)),
                found_by=self.spec.key,
            ))
        return out


def detect_with(
    detectors,
    image: Image.Image,
    *,
    thresholds: dict[str, float] | None = None,
    deep: bool = False,
) -> list[Detection]:
    """Pool what several models say about one image.

    Each has its own idea of a sensible threshold, so they are given
    separately rather than sharing one number. Boxes describing the same
    thing collapse to the most confident of them, whichever model found it.
    """
    found: list[Detection] = []
    for detector in detectors:
        cutoff = (thresholds or {}).get(detector.spec.key)
        found += detector.detect(image, threshold=cutoff, deep=deep)
    return merge(found)


def merge(detections: list[Detection]) -> list[Detection]:
    """Drop boxes describing something an earlier box already covers.

    Two models looking at one nipple is the common case, and so is one
    model finding it in the whole image and again in a tile.
    """
    kept: list[Detection] = []
    for candidate in sorted(detections, key=lambda d: -d.score):
        if not any(candidate.label == other.label
                   and overlap(candidate.rect, other.rect) > MERGE_IOU
                   for other in kept):
            kept.append(candidate)
    return kept


def overlap(a, b) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    left, top = max(ax, bx), max(ay, by)
    right, bottom = min(ax + aw, bx + bw), min(ay + ah, by + bh)
    inner = max(0.0, right - left) * max(0.0, bottom - top)
    union = aw * ah + bw * bh - inner
    return inner / union if union > 0 else 0.0


def _tiles(width: int, height: int, grid: int = 2, spare: float = TILE_OVERLAP):
    tile_w = int(width / grid * (1 + spare))
    tile_h = int(height / grid * (1 + spare))
    for row in range(grid):
        for column in range(grid):
            x = min(max(0, int(column * width / grid - tile_w * spare / 2)),
                    max(0, width - tile_w))
            y = min(max(0, int(row * height / grid - tile_h * spare / 2)),
                    max(0, height - tile_h))
            yield x, y, min(width, x + tile_w), min(height, y + tile_h)
