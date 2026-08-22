"""Persistent project state. The JSON sidecar that is the source of truth
for review status, group overrides, and censor-layer definitions, plus
every path needed to reopen a project without re-specifying anything.

One `.acproj.json` file per project, typically saved next to the manifest.
All paths are stored resolved to absolute form at save time.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1


class ProjectTooNew(ValueError):
    """Raised when a project file comes from a newer build than this one.

    Refused rather than opened, because loading drops unknown fields and
    the app saves straight after, so the newer data would be gone.
    """


class ImageStatus(str, Enum):
    UNREVIEWED = "unreviewed"
    FLAGGED = "flagged"          # flagged for censor
    CLEAN = "clean"               # reviewed, no censoring needed
    NEEDS_EDIT = "needs_edit"     # needs manual/custom edit


class LayerType(str, Enum):
    SOLID = "solid"
    BLUR = "blur"
    PIXELATE = "pixelate"
    OVERLAY = "overlay"
    TEXT = "text"


@dataclass
class CensorLayer:
    """One non-destructive censor layer on one image.

    `rect` is (x, y, w, h) as fractions of the image's width and height,
    from 0.0 to 1.0, so layers survive re-export at different resolutions
    and batch apply can translate a region onto same-sized siblings.

    `params` holds type-specific fields, for example
      - solid:    {"color": "#000000", "opacity": 1.0}
      - blur:     {"radius": 12}
      - pixelate: {"block_size": 12}
      - overlay:  {"sticker": "relative/path.png", "fit": "contain"|"stretch"|"tile",
                   "rotation": 0, "opacity": 1.0}
    """

    id: str
    type: LayerType
    rect: tuple[float, float, float, float]
    params: dict[str, Any] = field(default_factory=dict)
    enabled: bool = True

    def __post_init__(self) -> None:
        # Coerce once, here, so `type` is a LayerType for every reader no
        # matter how the layer was built. LayerType subclasses str, so a
        # plain string is easy to pass in by accident and impossible to
        # spot afterward, and readers used to each carry their own
        # isinstance check to cope. An unknown value now raises at
        # construction, while the project file that carried it is still in
        # hand, instead of surfacing much later as a failed render.
        if not isinstance(self.type, LayerType):
            self.type = LayerType(self.type)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type.value,
            "rect": list(self.rect),
            "params": self.params,
            "enabled": self.enabled,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> CensorLayer:
        return cls(
            id=d["id"],
            type=LayerType(d["type"]),
            rect=tuple(d["rect"]),  # type: ignore[arg-type]
            params=dict(d.get("params", {})),
            enabled=bool(d.get("enabled", True)),
        )


@dataclass
class ImageRecord:
    status: ImageStatus = ImageStatus.UNREVIEWED
    group_key: str | None = None       # suggested group, computed by grouping.py
    group_override: str | None = None  # user-assigned group, wins over group_key if set
    layers: list[CensorLayer] = field(default_factory=list)
    size: int | None = None
    mtime: float | None = None
    notes: str = ""

    @property
    def effective_group(self) -> str | None:
        return self.group_override or self.group_key

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "group_key": self.group_key,
            "group_override": self.group_override,
            "layers": [layer.to_dict() for layer in self.layers],
            "size": self.size,
            "mtime": self.mtime,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ImageRecord:
        return cls(
            status=ImageStatus(d.get("status", ImageStatus.UNREVIEWED.value)),
            group_key=d.get("group_key"),
            group_override=d.get("group_override"),
            layers=[CensorLayer.from_dict(x) for x in d.get("layers", [])],
            size=d.get("size"),
            mtime=d.get("mtime"),
            notes=d.get("notes", ""),
        )


@dataclass
class GroupOverride:
    """A manual merge or split of suggested groups. `members` is
    authoritative for this group key once present, and `source_keys`
    records which suggested groups were merged to form it, for display and
    undo purposes.

    Nothing writes either field yet. The schema slot exists ahead of the
    merge and split feature that grouping.py's ALD clustering was designed
    to need."""

    members: list[str] = field(default_factory=list)
    source_keys: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"members": self.members, "source_keys": self.source_keys}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> GroupOverride:
        return cls(members=list(d.get("members", [])), source_keys=list(d.get("source_keys", [])))


@dataclass
class ProjectState:
    schema_version: int = SCHEMA_VERSION

    archive_path: str = ""
    manifest_path: str = ""
    alice_exe_path: str = ""
    extract_dir: str = ""
    output_dir: str = ""
    sticker_dir: str = ""
    archive_format: str = ""  # "afa" | "ald"

    images: dict[str, ImageRecord] = field(default_factory=dict)
    group_overrides: dict[str, GroupOverride] = field(default_factory=dict)

    project_file: Path | None = None  # set on load/save; not persisted itself

    # ===== (de)serialization

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "archive_path": self.archive_path,
            "manifest_path": self.manifest_path,
            "alice_exe_path": self.alice_exe_path,
            "extract_dir": self.extract_dir,
            "output_dir": self.output_dir,
            "sticker_dir": self.sticker_dir,
            "archive_format": self.archive_format,
            "images": {path: rec.to_dict() for path, rec in self.images.items()},
            "group_overrides": {k: v.to_dict() for k, v in self.group_overrides.items()},
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ProjectState:
        return cls(
            schema_version=d.get("schema_version", SCHEMA_VERSION),
            archive_path=d.get("archive_path", ""),
            manifest_path=d.get("manifest_path", ""),
            alice_exe_path=d.get("alice_exe_path", ""),
            extract_dir=d.get("extract_dir", ""),
            output_dir=d.get("output_dir", ""),
            sticker_dir=d.get("sticker_dir", ""),
            archive_format=d.get("archive_format", ""),
            images={
                path: ImageRecord.from_dict(rec) for path, rec in d.get("images", {}).items()
            },
            group_overrides={
                k: GroupOverride.from_dict(v) for k, v in d.get("group_overrides", {}).items()
            },
        )

    def enabled_layers(self, path: str | None) -> list[CensorLayer]:
        """The layers to draw for `path`, which is empty for most images.

        Both repack paths ask this of every entry in the archive to decide
        whether it needs decoding at all, so it answers for paths it has
        never heard of rather than making each caller check first.
        """
        if not path:
            return []
        record = self.images.get(path)
        return [layer for layer in record.layers if layer.enabled] if record else []

    def save(self, path: str | Path | None = None) -> None:
        target = Path(path) if path else self.project_file
        if target is None:
            raise ValueError("no project file path given or previously set")
        target.parent.mkdir(parents=True, exist_ok=True)

        # Atomic write. Write to a temp file in the same directory, then
        # replace, so a crash mid-write can't corrupt the project file.
        fd, tmp_name = tempfile.mkstemp(
            dir=str(target.parent), prefix=target.name + ".", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self.to_dict(), f, ensure_ascii=False, indent=2, sort_keys=True)
            os.replace(tmp_name, target)
        except BaseException:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
            raise
        self.project_file = target

    @classmethod
    def load(cls, path: str | Path) -> ProjectState:
        target = Path(path)
        with open(target, encoding="utf-8") as f:
            data = json.load(f)
        found = data.get("schema_version", SCHEMA_VERSION)
        if found > SCHEMA_VERSION:
            # from_dict ignores fields it does not know, and the app saves
            # after almost every action, so opening this would quietly
            # rewrite the file without whatever the newer build put there.
            # Losing someone's work to an out of date copy is worse than
            # refusing to open it.
            raise ProjectTooNew(
                f"{target.name} was made by a newer version of Alice Censor "
                f"(project format {found}, this build understands {SCHEMA_VERSION}).\n\n"
                f"Opening it here would save it back without the parts this "
                f"version does not recognise. Update Alice Censor to open it."
            )
        state = cls.from_dict(data)
        state.project_file = target
        return state

    # ===== syncing with the extracted-image folder

    def sync_with_paths(
        self, current_paths: list[str], group_keys: dict[str, str] | None = None
    ) -> tuple[list[str], list[str]]:
        """Reconcile `images` against the current set of manifest paths
        (typically re-run after a game update changed which files exist).

        Returns (new_paths, missing_paths). New paths get a fresh
        ImageRecord. Existing records for paths no longer present are kept,
        so a transient extraction problem can't lose review work, but they
        are reported as `missing_paths` for the caller to surface.
        """
        current_set = set(current_paths)
        existing_set = set(self.images.keys())

        new_paths = sorted(current_set - existing_set)
        missing_paths = sorted(existing_set - current_set)

        for path in new_paths:
            group_key = (group_keys or {}).get(path)
            self.images[path] = ImageRecord(group_key=group_key)

        if group_keys:
            for path, key in group_keys.items():
                if path in self.images:
                    self.images[path].group_key = key

        return new_paths, missing_paths
