"""Rebuilds an ALD archive with the censored images substituted in.

The AFA path renders every listed file into an export folder and hands the
whole folder to `ar pack`. That is not available here, since alice-tools
cannot write ALD, so this rebuilds the archive directly using ald.py.

Working from the original archive rather than from an export folder turns
out to be the better design regardless of that, because it makes the repack
lossless everywhere the user did not draw. An entry with no enabled layers
is copied across as the exact bytes it already was, never decoded and never
re-encoded. Only the handful actually censored get rebuilt.

That matters most for the formats alice-tools cannot write back. A Rance 02
archive is roughly half AJP, and AJP is lossy. Extracting it to PNG and
re-encoding would degrade every one of those images, which is why
alice-tools' own manifest quietly rewrites them all to QNT instead. Copying
untouched entries verbatim avoids the choice altogether. Only an edited AJP
has to become QNT, because there is no AJP encoder anywhere.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from . import formats
from .alice_tools import AliceTools, AliceToolsError, ensure_archive_backup
from .formats import qnt
from .formats.ald import AldArchive, AldEntry, read_ald, write_ald
from .manifest import Manifest
from .paths import basename
from .project import ProjectState
from .repack_progress import RepackProgress
from .rendering import RenderError, render_layers

# The only CG format alice-tools can encode that is also lossless. An
# edited entry ends up in this format whatever it started as.
REENCODE_FORMAT = "qnt"


class AldRepackError(RuntimeError):
    """Raised when an archive cannot be rebuilt at all, as opposed to a
    single image failing, which is reported per path in the result."""


@dataclass
class AldRepackResult:
    rebuilt_paths: list[str] = field(default_factory=list)  # censored, re-encoded
    copied_count: int = 0  # untouched, byte for byte
    converted_formats: dict[str, str] = field(default_factory=dict)  # path -> old extension
    errors: dict[str, str] = field(default_factory=dict)
    archive_path: Path | None = None
    # Recorded for the verify pass, which reads the finished archive back
    # without needing the project or manifest again.
    expected_indices: list[int] = field(default_factory=list)
    expected_volume: str = ""

    @property
    def ok(self) -> bool:
        return not self.errors


def _stem(name: str) -> str:
    return basename(name).rsplit(".", 1)[0].lower()


def find_sibling_volumes(archive_path: str | Path) -> list[Path]:
    """Every volume alice-tools would load alongside `archive_path`.

    An ALD set can be split across fooA.ald, fooB.ald and so on, and
    open_ald_archive (src/core/ar/open.c) globs the directory for the whole
    set and presents it as one archive. An extraction therefore spans all
    of them, while this module rebuilds one file, so a split set has to be
    refused rather than silently rebuilt with most of its files missing.
    """
    archive_path = Path(archive_path)
    base = archive_path.name
    if len(base) < 5:
        return [archive_path]
    prefix = base[: len(base) - 5].lower()
    found = []
    for sibling in archive_path.parent.iterdir():
        name = sibling.name
        if len(name) < len(prefix) + 5 or not name.lower().endswith(".ald"):
            continue
        if name[: len(prefix)].lower() != prefix:
            continue
        if not name[len(name) - 5].isalpha():
            continue
        found.append(sibling)
    return sorted(found) or [archive_path]


def volume_letter(archive_path: str | Path) -> str:
    """The volume letter alice-tools reads out of an ALD filename.

    open.c takes the character immediately before the extension and maps it
    to a volume number, so a rebuilt archive has to keep the original name.
    Writing the same bytes to censored.ald instead of rance02GA.ald makes
    the file unreadable, since every link table entry points at volume 1
    and there would no longer be a volume 1 in the directory.
    """
    return Path(archive_path).name[-5:-4]


def _index_entries_by_stem(archive: AldArchive) -> dict[str, AldEntry]:
    """Map each archive entry to its name without extension.

    The manifest refers to files by their converted name, cg00001.png,
    while the archive holds cg00001.QNT, so the extension cannot be part of
    the match. A duplicate stem would make the mapping ambiguous and is
    refused rather than guessed at.
    """
    by_stem: dict[str, AldEntry] = {}
    for entry in archive.entries:
        stem = _stem(entry.name)
        if stem in by_stem:
            raise ValueError(
                f"archive has two entries named {stem!r} "
                f"({by_stem[stem].name} and {entry.name}), so manifest paths cannot be matched"
            )
        by_stem[stem] = entry
    return by_stem


def _encode_qnt(image: Image.Image) -> bytes:
    """Encode a censored image as QNT.

    Always done here rather than through alice.exe. The built in encoder
    was checked against alice-tools across every distinct image shape in a
    real archive and writes the same bytes, so there is nothing to choose
    between them beyond one of them needing a subprocess.
    """
    return qnt.encode(image)


def repack_ald(
    project: ProjectState,
    manifest: Manifest,
    tools: AliceTools,
    *,
    source_archive: str | Path,
    output_archive: str | Path,
    sticker_resolver=None,
    on_progress=None,
) -> AldRepackResult:
    """Write `output_archive` as `source_archive` with censor layers applied.

    `source_archive` must be the pristine original, not a previous output,
    so that repeated repacks re-render from clean pixels instead of
    compounding on an earlier render. Every entry the project has no
    enabled layers for is copied without being decoded.
    """
    source_archive = Path(source_archive)
    result = AldRepackResult()

    archive = read_ald(source_archive)
    by_stem = _index_entries_by_stem(archive)

    rebuilt: list[AldEntry] = []
    with tempfile.TemporaryDirectory(prefix="alice-censor-qnt-") as tmp:
        work_dir = Path(tmp)
        # Counted before the loop so the first log line can say how much
        # work there is. Paths the archive does not have are left out of
        # both totals, since they become an error rather than an image.
        paths = list(manifest.paths())
        present = [path for path in paths if _stem(path) in by_stem]
        total = sum(1 for path in present if project.enabled_layers(path))
        copies = len(present) - total
        done = 0

        for path in paths:
            entry = by_stem.get(_stem(path))
            if entry is None:
                result.errors[path] = "listed in the manifest but not present in the archive"
                continue

            layers = project.enabled_layers(path)
            if not layers:
                rebuilt.append(entry)
                result.copied_count += 1
                continue

            # Reported here rather than at the top of the loop, so the log
            # is a list of images being worked on instead of a list of
            # every name in the manifest.
            done += 1
            if on_progress:
                on_progress(RepackProgress(done, total, copies, path))

            try:
                # Decode from the archive's own bytes rather than the
                # extraction folder, so the repack does not depend on that
                # folder still existing or still matching.
                decoded = _decode_entry_to_image(tools, entry, work_dir)
                rendered = render_layers(decoded, layers, sticker_resolver=sticker_resolver)
                data = _encode_qnt(rendered)
            except (OSError, UnidentifiedImageError, RenderError, AliceToolsError) as e:
                result.errors[path] = str(e)
                continue

            old_ext = entry.name.rsplit(".", 1)[-1]
            new_name = entry.name
            if old_ext.lower() != REENCODE_FORMAT:
                # No encoder exists for the original format, so the entry
                # changes format. The game finds files by the index in the
                # link table, which is preserved, not by this name.
                new_name = f"{entry.name.rsplit('.', 1)[0]}.{REENCODE_FORMAT}"
                result.converted_formats[path] = old_ext
            rebuilt.append(
                AldEntry(
                    index=entry.index,
                    name=new_name,
                    data=data,
                    timestamp=entry.timestamp,
                )
            )
            result.rebuilt_paths.append(path)

    write_ald(output_archive, AldArchive(entries=rebuilt, trailer=archive.trailer))
    result.archive_path = Path(output_archive)
    result.expected_indices = [entry.index for entry in rebuilt]
    result.expected_volume = volume_letter(source_archive)
    return result


def _decode_entry_to_image(
    tools: AliceTools | None, entry: AldEntry, work_dir: Path
) -> Image.Image:
    """Turn one archive entry's raw bytes into a PIL image.

    Read here when the format is one this build knows, which is every image
    a Rance 02 archive holds. Otherwise it goes out to alice-tools, which
    still reads formats this does not.
    """
    if formats.can_decode(entry.data):
        return formats.decode_image(entry.data)
    if tools is None:
        raise AldRepackError(
            f"{entry.name} is a format this build cannot read and there is no "
            f"alice.exe to fall back on"
        )
    src = work_dir / f"in_{entry.index}.{entry.name.rsplit('.', 1)[-1].lower()}"
    dst = work_dir / f"in_{entry.index}.png"
    src.write_bytes(entry.data)
    tools.cg_convert(src, dst, to="png")
    with Image.open(dst) as opened:
        image = opened.convert("RGBA")
        image.load()
    return image


def repack_ald_in_place(
    project: ProjectState,
    manifest: Manifest,
    tools: AliceTools,
    *,
    sticker_resolver=None,
    on_progress=None,
) -> AldRepackResult:
    """Rebuild the project's own archive, censored, overwriting it.

    Reads from the pristine backup rather than from the archive being
    written, which is what makes repacking twice produce the same result
    instead of rendering a censor on top of an already censored image. The
    backup is made first if it does not exist yet, so the original bytes
    are always recoverable.
    """
    archive_path = Path(manifest.resolved_archive_path())

    volumes = find_sibling_volumes(archive_path)
    if len(volumes) > 1:
        names = ", ".join(v.name for v in volumes)
        raise AldRepackError(
            f"This archive is split across {len(volumes)} volumes ({names}). "
            "An extraction covers all of them at once, so rebuilding a single "
            "volume would drop the files held in the others. Multi-volume "
            "rebuilding is not supported."
        )

    ensure_archive_backup(archive_path)
    backup_path = archive_path.with_name(archive_path.name + ".orig-backup")
    source = backup_path if backup_path.exists() else archive_path

    return repack_ald(
        project,
        manifest,
        tools,
        source_archive=source,
        output_archive=archive_path,
        sticker_resolver=sticker_resolver,
        on_progress=on_progress,
    )


def verify_ald(archive_path: str | Path, expected: AldRepackResult) -> list[str]:
    """Read a freshly written archive back and report anything wrong.

    The AFA path does this with `ar list` because alice-tools owns that
    format. Here the archive was written by this module, so it is read back
    by this module, which at least proves the tables are self-consistent
    and every entry is reachable at the offset its pointer claims.
    """
    problems: list[str] = []
    archive = read_ald(archive_path)
    got = {entry.index for entry in archive.entries}
    want = set(expected.expected_indices)
    missing = sorted(want - got)
    if missing:
        problems.append(f"{len(missing)} file number(s) missing from the rebuilt archive")
    for entry in archive.entries:
        if not entry.data:
            problems.append(f"file {entry.index} ({entry.name}) is empty")
    if volume_letter(archive_path).upper() != expected.expected_volume.upper():
        problems.append(
            f"archive was written as {Path(archive_path).name}, whose volume letter does not "
            f"match the original. alice-tools locates volumes by that letter and will not "
            f"find this file."
        )
    return problems
