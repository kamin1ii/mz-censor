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

from . import detection
from .detection import Detection, DetectorUnavailable, detect_with
from .paths import resolve_fs_path
from .project import CensorLayer, ImageRecord, ImageStatus, LayerType, ProjectState

# What to draw over each thing the detector knows about. Genitals get a
# heavier hand than nipples by default, which is the usual convention, and
# every part of this is meant to be overridden.
DEFAULT_STYLES: dict[str, tuple[LayerType, dict]] = {
    detection.NIPPLE: (LayerType.PIXELATE, {"block_size": 10}),
    detection.PENIS: (LayerType.PIXELATE, {"block_size": 18}),
    detection.VAGINA: (LayerType.PIXELATE, {"block_size": 18}),
    detection.ANUS: (LayerType.PIXELATE, {"block_size": 14}),
}

# A detector box hugs what it found. A censor usually wants to cover a
# little more than that, so boxes are grown by this fraction of their own
# size before becoming layers.
DEFAULT_PADDING = 0.15

# Two regions overlapping by more than this are the same thing. Used when
# pooling a scene group, so a nipple found in four frames of six does not
# become four boxes on the two that were missing it.
SAME_REGION = 0.5


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
    found: Detection,
    styles: dict[str, tuple[LayerType, dict]] | None = None,
    padding: float = DEFAULT_PADDING,
) -> CensorLayer | None:
    """One detection as one disabled layer, or None for a class with no style.

    A style of None is how a class gets turned off, so somebody who does
    not want nipples censored simply removes that entry.
    """
    chosen = (styles or DEFAULT_STYLES).get(found.label)
    if chosen is None:
        return None
    layer_type, params = chosen
    return CensorLayer(
        id=uuid.uuid4().hex[:12],
        type=layer_type,
        rect=_padded(found.rect, padding),
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
    detectors,
    thresholds: dict[str, float] | None = None,
    deep: bool = False,
    styles: dict[str, tuple[LayerType, dict]] | None = None,
    padding: float = DEFAULT_PADDING,
    on_progress=None,
) -> ScanResult:
    """Look at each of `paths` and work out what could be censored.

    Reads the extracted PNGs rather than the archive, because that is what
    the gallery is showing and what the editor draws on, so what the
    detectors saw is what you will see.

    `detectors` is a list, since the two models are better at different
    things and pooling them beats either alone.
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
                found = detect_with(
                    detectors, image, thresholds=thresholds, deep=deep
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


def spread_across_scene_groups(result: ScanResult, groups, *, within=None) -> int:
    """Give every image in a scene group the same set of censor regions.

    A scene group is one CG in its variations, the same drawing with a
    different expression or a later moment. Each is read on its own, so the
    detectors can easily find a nipple in H03 and miss it in H04, which
    leaves a scene censored in patches. That looks worse than not censoring
    it at all, and it is the kind of gap only noticed in game.

    So whatever was found anywhere in a group is pooled and given to every
    member of it. Rects are fractions of image size and the members of a
    group are the same composition, so a region from one lands in the right
    place on the rest.

    Returns how many regions were added.
    """
    added = 0
    for group in groups:
        members = [m for m in group.members if within is None or m in within]
        if len(members) < 2:
            continue

        pooled: list[CensorLayer] = []
        for member in members:
            for layer in result.proposed.get(member, []):
                if not _already_covered(layer, pooled):
                    pooled.append(layer)
        if not pooled:
            continue

        for member in members:
            have = result.proposed.setdefault(member, [])
            for layer in pooled:
                if _already_covered(layer, have):
                    continue
                have.append(replace(layer, id=uuid.uuid4().hex[:12]))
                added += 1
            if member in result.skipped:
                result.skipped.remove(member)
    return added


def _already_covered(layer: CensorLayer, existing) -> bool:
    """Whether something in `existing` is already the same region.

    Without this, a nipple found in four frames of six would put four
    boxes on the two that were missing it.
    """
    return any(other.type == layer.type
               and detection.overlap(layer.rect, other.rect) > SAME_REGION
               for other in existing)
