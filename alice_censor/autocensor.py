"""Turning what the detector found into censor layers you can edit.

Kept apart from detection.py because the two answer different questions.
That module says what is in a picture. This one decides what to do about
it, which is a matter of taste and belongs where taste can be changed.

Nothing here applies anything. Layers come out disabled, so a scan proposes
and you dispose. Detection finds about four in five of the images that
need work and occasionally boxes a wall, so a pass that silently edited a
few thousand images would be worse than no pass at all.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, replace
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from .detection import Detection, Detector, DetectorUnavailable
from .paths import resolve_fs_path
from .project import CensorLayer, ImageRecord, ImageStatus, LayerType, ProjectState

# What to draw over each thing the detector knows about. Genitals get a
# heavier hand than nipples by default, which is the usual convention, and
# every part of this is meant to be overridden.
DEFAULT_STYLES: dict[str, tuple[LayerType, dict]] = {
    "nipple_f": (LayerType.PIXELATE, {"block_size": 10}),
    "penis": (LayerType.PIXELATE, {"block_size": 18}),
    "pussy": (LayerType.PIXELATE, {"block_size": 18}),
}

# A detector box hugs what it found. A censor usually wants to cover a
# little more than that, so boxes are grown by this fraction of their own
# size before becoming layers.
DEFAULT_PADDING = 0.15


@dataclass
class ScanResult:
    proposed: dict[str, list[CensorLayer]] = field(default_factory=dict)
    skipped: list[str] = field(default_factory=list)  # nothing found
    errors: dict[str, str] = field(default_factory=dict)

    @property
    def images_with_proposals(self) -> int:
        return len(self.proposed)

    @property
    def layer_count(self) -> int:
        return sum(len(v) for v in self.proposed.values())


def layer_for(
    detection: Detection,
    styles: dict[str, tuple[LayerType, dict]] | None = None,
    padding: float = DEFAULT_PADDING,
) -> CensorLayer | None:
    """One detection as one disabled layer, or None for a class with no style.

    A style of None is how a class gets turned off, so somebody who does
    not want nipples censored simply removes that entry.
    """
    chosen = (styles or DEFAULT_STYLES).get(detection.label)
    if chosen is None:
        return None
    layer_type, params = chosen
    return CensorLayer(
        id=uuid.uuid4().hex[:12],
        type=layer_type,
        rect=_padded(detection.rect, padding),
        params=dict(params),
        enabled=False,
    )


def _padded(rect, padding: float):
    """Grow a box a little, without letting it leave the image."""
    x, y, w, h = rect
    grow_x, grow_y = w * padding, h * padding
    left = max(0.0, x - grow_x / 2)
    top = max(0.0, y - grow_y / 2)
    right = min(1.0, x + w + grow_x / 2)
    bottom = min(1.0, y + h + grow_y / 2)
    return left, top, max(0.0, right - left), max(0.0, bottom - top)


def scan_project(
    project: ProjectState,
    paths,
    *,
    detector: Detector,
    threshold: float | None = None,
    deep: bool = False,
    styles: dict[str, tuple[LayerType, dict]] | None = None,
    padding: float = DEFAULT_PADDING,
    on_progress=None,
) -> ScanResult:
    """Look at each of `paths` and work out what could be censored.

    Reads the extracted PNGs rather than the archive, because that is what
    the gallery is showing and what the editor draws on, so what the
    detector saw is what you will see.
    """
    result = ScanResult()
    extract_dir = Path(project.extract_dir) if project.extract_dir else None
    if extract_dir is None:
        raise DetectorUnavailable("this project has no extraction folder to read")

    for path in paths:
        if on_progress:
            on_progress(path)
        source = resolve_fs_path(extract_dir, path)
        if not source.is_file():
            result.errors[path] = "no extracted image to look at"
            continue
        try:
            with Image.open(source) as opened:
                image = opened.convert("RGB")
                found = detector.detect(
                    image,
                    **({"threshold": threshold} if threshold is not None else {}),
                    deep=deep,
                )
        except (OSError, UnidentifiedImageError) as exc:
            result.errors[path] = str(exc)
            continue

        layers = [layer for layer in (layer_for(d, styles, padding) for d in found) if layer]
        if layers:
            result.proposed[path] = layers
        else:
            result.skipped.append(path)
    return result


def apply_scan(
    project: ProjectState,
    result: ScanResult,
    *,
    replace_existing: bool = False,
    flag: bool = True,
) -> int:
    """Add the proposed layers to the project. Returns how many were added.

    They arrive disabled, so nothing renders differently until you turn
    them on. Images that already have layers are left alone unless asked
    otherwise, since work done by hand should not be overwritten by a
    guess.
    """
    added = 0
    for path, layers in result.proposed.items():
        record = project.images.setdefault(path, ImageRecord())
        if record.layers and not replace_existing:
            continue
        if replace_existing:
            record.layers = []
        record.layers.extend(replace(layer) for layer in layers)
        added += len(layers)
        if flag and record.status == ImageStatus.UNREVIEWED:
            record.status = ImageStatus.FLAGGED
    return added
