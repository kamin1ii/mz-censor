"""Finding the parts of an image that need censoring, automatically.

Uses anime_censor_detection from deepghs, an MIT licensed object detector
trained on drawn art rather than photographs. It knows three things, which
happen to be the three worth finding here.

    nipple_f   an exposed nipple
    penis
    pussy

Trained on drawings matters more than it sounds. A detector trained on
photographs was measured against the same archive and reached a median
confidence of 0.005 on drawn male genitalia, which is to say it could not
see them at all, against 0.44 on breasts. This one finds a penis in 31% of
the images that needed censoring where the other managed 2%.

What it cannot do is worth knowing before trusting it. Measured against one
real project it finds about 81% of the images that were censored by hand,
so roughly one in five still has to be spotted the old way, and it has no
notion of buttocks, anus, or anything censored for framing rather than for
what it shows.

The model is not shipped with the app. It is 44 MB, it is only wanted by
people who turn this on, and fetching it on demand keeps the download small
for everyone else.
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

LABELS = ("nipple_f", "penis", "pussy")

MODEL_URL = (
    "https://huggingface.co/deepghs/anime_censor_detection/"
    "resolve/main/censor_detect_v1.0_s/model.onnx"
)
MODEL_SHA256 = "2c2524824d7d320c5619a0a73702a2e2186f619067c823d211e94f7cfe489cba"
MODEL_BYTES = 44586353
MODEL_FILENAME = "anime_censor_detection_v1.0_s.onnx"

# The model was trained near this size. Measured on a real archive, 640
# beats 1024 on every class and runs in under half the time, so bigger is
# not better here.
INFERENCE_SIZE = 640

# Below this the boxes stop being worth looking at. 0.25 was measured at
# 81% of censored images found against 2.4% of scenery wrongly flagged.
DEFAULT_THRESHOLD = 0.25

# Overlap for the slower second pass. Enough that a part straddling a tile
# edge lands whole in a neighbouring one.
TILE_OVERLAP = 0.2

# Two boxes overlapping by more than this are treated as the same thing.
MERGE_IOU = 0.45

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
    label: str
    score: float
    rect: tuple[float, float, float, float]


def cache_dir() -> Path:
    """Where a downloaded model lives.

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


def model_path() -> Path:
    return cache_dir() / "models" / MODEL_FILENAME


def model_is_present() -> bool:
    path = model_path()
    return path.is_file() and path.stat().st_size == MODEL_BYTES


def runtime_is_present() -> bool:
    """Whether this build can run a model at all.

    onnxruntime roughly triples the size of the exe, so a build without it
    is a reasonable thing to be using. The menu explains that rather than
    failing when the button is pressed.
    """
    try:
        import onnxruntime  # noqa: F401
    except ImportError:
        return False
    return True


def unavailable_reason() -> str | None:
    """Why detection cannot run, or None if it can."""
    if not runtime_is_present():
        return (
            "This build does not include the detection runtime. Install "
            "onnxruntime and run from source, or use the build that has it."
        )
    if not model_is_present():
        return "The detection model has not been downloaded yet."
    return None


def download_model(on_progress=None) -> Path:
    """Fetch the model, checking it arrived intact.

    Written to a temporary name and moved into place at the end, so an
    interrupted download cannot leave something that looks usable.
    """
    destination = model_path()
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(".partial")

    digest = hashlib.sha256()
    try:
        with urllib.request.urlopen(MODEL_URL, timeout=60) as response, \
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
                    on_progress(done, MODEL_BYTES)
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        partial.unlink(missing_ok=True)
        raise DetectorUnavailable(f"could not download the detection model, {exc}") from exc

    if digest.hexdigest() != MODEL_SHA256:
        partial.unlink(missing_ok=True)
        raise DetectorUnavailable(
            "the downloaded model does not match its expected checksum, so it "
            "was discarded rather than used"
        )
    shutil.move(str(partial), str(destination))
    return destination


class Detector:
    """A loaded model, ready to be asked about images.

    Loading takes long enough to be worth doing once, so a caller should
    keep one of these for a whole scan rather than per image.
    """

    def __init__(self, path: str | Path | None = None, size: int = INFERENCE_SIZE):
        try:
            import onnxruntime
        except ImportError as exc:
            raise DetectorUnavailable(
                "this build has no detection runtime, so nothing can be detected"
            ) from exc

        self.size = size
        resolved = Path(path) if path else model_path()
        if not resolved.is_file():
            raise DetectorUnavailable(f"the detection model is not at {resolved}")
        self._session = onnxruntime.InferenceSession(
            str(resolved), providers=["CPUExecutionProvider"]
        )
        self._input = self._session.get_inputs()[0].name

    def detect(
        self,
        image: Image.Image,
        *,
        threshold: float = DEFAULT_THRESHOLD,
        deep: bool = False,
    ) -> list[Detection]:
        """Everything the model finds, largest confidence first.

        `deep` also runs four overlapping crops, which finds roughly half
        again of what a single pass misses at the cost of four times the
        work and about three times as many false alarms. Worth it when
        re-checking a handful of images, not as a default over thousands.
        """
        if image.mode != "RGB":
            image = image.convert("RGB")
        width, height = image.size
        if width == 0 or height == 0:
            return []

        found = self._pass(image, threshold, (0, 0), (width, height))
        if deep:
            for box in _tiles(width, height):
                crop = image.crop(box)
                found += self._pass(crop, threshold, box[:2], (width, height))
        return _merge(found)

    def _pass(self, image, threshold, origin, full_size) -> list[Detection]:
        import numpy as np

        side = max(image.size)
        canvas = Image.new("RGB", (side, side), _LETTERBOX_GREY)
        canvas.paste(image, (0, 0))
        blob = np.asarray(
            canvas.resize((self.size, self.size), Image.BILINEAR), dtype=np.float32
        ) / 255.0
        raw = self._session.run(None, {self._input: blob.transpose(2, 0, 1)[None, ...]})[0][0]
        # One row per candidate box. Which axis is which varies with how the
        # model was exported, so it is decided by the axis whose length is
        # the number of fields rather than by whichever happens to be
        # larger, which would get it backwards on an image with almost
        # nothing in it.
        fields = 4 + len(LABELS)
        if raw.ndim != 2:
            return []
        if raw.shape[0] == fields and raw.shape[1] != fields:
            raw = raw.T
        if raw.shape[1] < fields:
            return []

        scores = raw[:, 4:4 + len(LABELS)]
        confidence = scores.max(1)
        keep = confidence >= threshold
        if not keep.any():
            return []

        scale = side / self.size
        origin_x, origin_y = origin
        full_width, full_height = full_size
        out = []
        for (cx, cy, w, h), index, score in zip(
            raw[keep, :4], scores.argmax(1)[keep], confidence[keep]
        ):
            left = (cx - w / 2) * scale + origin_x
            top = (cy - h / 2) * scale + origin_y
            # Plain floats, not numpy ones. These end up in the project
            # file, and json cannot encode a float32.
            out.append(Detection(
                label=LABELS[int(index)],
                score=float(score),
                rect=(float(left / full_width), float(top / full_height),
                      float(w * scale / full_width), float(h * scale / full_height)),
            ))
        return out


def _tiles(width: int, height: int, grid: int = 2, overlap: float = TILE_OVERLAP):
    tile_w = int(width / grid * (1 + overlap))
    tile_h = int(height / grid * (1 + overlap))
    for row in range(grid):
        for column in range(grid):
            x = min(max(0, int(column * width / grid - tile_w * overlap / 2)),
                    max(0, width - tile_w))
            y = min(max(0, int(row * height / grid - tile_h * overlap / 2)),
                    max(0, height - tile_h))
            yield x, y, min(width, x + tile_w), min(height, y + tile_h)


def _merge(detections: list[Detection]) -> list[Detection]:
    """Drop boxes that describe something an earlier box already covers.

    Across tiles the same part is often found several times, and the whole
    image pass finds it again. Keeping the most confident of each cluster
    leaves one box per thing.
    """
    kept: list[Detection] = []
    for candidate in sorted(detections, key=lambda d: -d.score):
        if not any(_overlap(candidate.rect, other.rect) > MERGE_IOU for other in kept):
            kept.append(candidate)
    return kept


def _overlap(a, b) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    left, top = max(ax, bx), max(ay, by)
    right, bottom = min(ax + aw, bx + bw), min(ay + ah, by + bh)
    inner = max(0.0, right - left) * max(0.0, bottom - top)
    union = aw * ah + bw * bh - inner
    return inner / union if union > 0 else 0.0
