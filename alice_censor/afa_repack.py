"""Rebuilds an AFA archive with the censored images substituted in.

This is the alternative to the export folder and `ar pack`, and it does the
same job the ALD path does, working straight from the original archive.
Nothing here calls alice-tools.

The reason to prefer it is that `ar pack` builds a new archive out of a
folder, so every entry is written afresh and every offset in the file moves,
even for images nobody touched. This copies untouched entries as the bytes
they already were and leaves them where they were. Rebuilding an archive
with nothing edited reproduces it exactly, which is a property worth having
when the output is going back into a game.

It also steps around three things the export path has to work around. There
is no conversion cache to keep in step, so the class of bug where a stale
cache entry silently replaces an edit cannot happen. There is no manifest,
so the quoting problem with paths containing spaces cannot happen. And the
filename corruption in alice-tools issue 92 cannot happen, because the table
is written here.

What it cannot do is decode formats this package does not understand yet. An
edited DCF still needs its pixels from somewhere, and they come from the
extracted PNG the gallery is already showing. Untouched ones are copied
without being decoded at all, so they are unaffected either way.
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from .alice_tools import ensure_archive_backup
from . import formats
from .formats import qnt
from .formats import dcf
from .formats.afa import (
    AfaEntry,
    AfaError,
    AfaReader,
    copy_of,
    replacement_for,
    write_afa,
)
from .manifest import Manifest
from .paths import resolve_fs_path
from .project import ProjectState
from .repack_progress import RepackProgress
from .rendering import RenderError, render_layers

# Edited images are written back as QNT whatever they started as. It is
# lossless, it is what this package can write, and for a DCF it is also the
# only safe answer, since a re-encoded difference image renders as a black
# screen in game.
REENCODE_FORMAT = "qnt"

# Encoding is where the time goes, and it parallelises well. Capped at eight
# because this runs while the window is still meant to answer, and measuring
# on a sixteen core machine showed eight threads reaching 3.5 times and all
# sixteen only 3.9, which is not worth the whole machine.
DEFAULT_WORKERS = max(2, min(8, os.cpu_count() or 4))


class AfaRepackError(RuntimeError):
    """The archive cannot be rebuilt at all.

    A single image failing is not this. That is reported per path in the
    result so the rest of the archive still gets written.
    """


@dataclass
class AfaRepackResult:
    rebuilt_paths: list[str] = field(default_factory=list)  # censored, re-encoded
    copied_count: int = 0  # untouched, byte for byte
    converted_formats: dict[str, str] = field(default_factory=dict)  # path -> old extension
    errors: dict[str, str] = field(default_factory=dict)
    archive_path: Path | None = None
    expected_names: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def _stem(name: str) -> str:
    """A name without its extension.

    Manifest paths are the archive's own names with the extension swapped
    for .png, and the names can contain characters that look like path
    separators but are not, so the whole name is kept rather than only its
    last part.
    """
    return name.rsplit(".", 1)[0].lower()


def repack_afa(
    project: ProjectState,
    manifest: Manifest,
    *,
    source_archive: str | Path,
    output_archive: str | Path,
    extract_dir: str | Path | None = None,
    sticker_resolver=None,
    on_progress=None,
    workers: int = DEFAULT_WORKERS,
) -> AfaRepackResult:
    """Write `output_archive` as `source_archive` with censor layers applied.

    `source_archive` must be the pristine original rather than a previous
    output, so that repacking twice renders from clean pixels instead of
    drawing a censor on top of one already there.

    Entries are written in the order the source archive had them. The game
    indexes into that order, so it is not ours to tidy.
    """
    source_archive = Path(source_archive)
    extract_dir = Path(extract_dir) if extract_dir else None
    result = AfaRepackResult()

    try:
        reader = AfaReader(source_archive)
    except AfaError as exc:
        raise AfaRepackError(f"could not read {source_archive.name}, {exc}") from exc

    with reader:
        paths_by_stem = {_stem(path): path for path in manifest.paths()}
        plan = [(entry, paths_by_stem.get(_stem(entry.name))) for entry in reader.entries]

        # Copies first. They cost nothing, and settling them here leaves a
        # list of just the images that actually need work.
        outgoing: list = [None] * len(plan)
        todo = []
        for index, (entry, path) in enumerate(plan):
            layers = project.enabled_layers(path)
            if layers:
                todo.append((index, path, layers))
            else:
                outgoing[index] = copy_of(reader, entry)
                result.copied_count += 1

        _censor_all(
            reader, plan, todo, outgoing, result,
            extract_dir=extract_dir,
            sticker_resolver=sticker_resolver,
            on_progress=on_progress,
            copies=result.copied_count,
            workers=workers,
        )

        write_afa(
            output_archive,
            outgoing,
            version=reader.version,
            has_ids=reader.has_ids,
            header_unknown=reader.header_unknown,
            # Back where the source had it, which is what makes an
            # untouched rebuild come out byte for byte identical. The
            # writer moves it along if the table has outgrown the space.
            data_start=reader.data_start or None,
        )
        result.expected_names = [entry.name for entry in reader.entries]

    result.archive_path = Path(output_archive)
    return result


def _censor_all(
    reader: AfaReader,
    plan: list,
    todo: list,
    outgoing: list,
    result: AfaRepackResult,
    *,
    extract_dir: Path | None,
    sticker_resolver,
    on_progress,
    copies: int,
    workers: int,
) -> None:
    """Render and re-encode every image that has layers.

    Spread over threads, because encoding is where almost all the time goes
    and the two expensive parts of it, zlib and Pillow, both let go of the
    interpreter lock while they work. Reading from the archive stays on this
    thread, since that is one file handle being seeked and it costs nothing
    anyway.

    Work goes out in waves rather than all at once so only a few images are
    decoded at a time. Their encoded bytes have to be held until the archive
    is written, because the table records every size before any data goes
    down, and keeping the sources alive as well would double that for
    nothing.
    """
    if not todo:
        return
    workers = max(1, workers)
    wave = max(1, workers * 2)
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="censor") as pool:
        for start in range(0, len(todo), wave):
            pending = {}
            for done, (index, path, layers) in enumerate(
                todo[start:start + wave], start=start + 1
            ):
                entry = plan[index][0]
                if on_progress:
                    on_progress(RepackProgress(done, len(todo), copies, path))
                pending[pool.submit(
                    _censor_one, reader.read(entry), entry, path, layers,
                    extract_dir, sticker_resolver,
                )] = (index, path)

            # In submission order, so an archive comes out the same whatever
            # the threads happened to finish first.
            for future, (index, path) in pending.items():
                entry = plan[index][0]
                try:
                    data = future.result()
                except (OSError, UnidentifiedImageError, RenderError, AfaError,
                        qnt.QntError) as exc:
                    result.errors[path] = str(exc)
                    outgoing[index] = copy_of(reader, entry)
                    continue

                old_extension = entry.name.rsplit(".", 1)[-1]
                if old_extension.lower() != REENCODE_FORMAT:
                    # The entry keeps its name. Renaming it would be honest
                    # about the contents and wrong about everything else,
                    # since the game and the manifest look files up by name.
                    result.converted_formats[path] = old_extension
                outgoing[index] = replacement_for(entry, data)
                result.rebuilt_paths.append(path)


def _censor_one(
    data: bytes, entry: AfaEntry, path: str | None, layers,
    extract_dir: Path | None, sticker_resolver,
) -> bytes:
    """One image from raw bytes to raw bytes. Runs on a worker thread."""
    base = _load_image(data, entry, path, extract_dir)
    rendered = render_layers(base, layers, sticker_resolver=sticker_resolver)
    return qnt.encode(rendered)


def _load_image(
    data: bytes, entry: AfaEntry, path: str | None, extract_dir: Path | None
) -> Image.Image:
    """Get the pixels for one entry that is about to be censored.

    Straight from the archive's own bytes when the format is one this
    package reads, which is the authoritative source and needs nothing else
    on disk. For anything else the extracted PNG stands in, which is the
    same picture the gallery showed and the editor drew on.

    A difference image is the one that still needs the fallback. Composing
    one means finding the image it differs from, and that lookup belongs to
    whoever holds the archive rather than here.
    """
    if formats.can_decode(data) and not dcf.is_dcf(data):
        return formats.decode_image(data)

    if extract_dir and path:
        source = resolve_fs_path(extract_dir, path)
        if source.is_file():
            with Image.open(source) as opened:
                image = opened.convert("RGBA")
                image.load()
            return image

    raise AfaError(
        f"{entry.name} is a .{entry.name.rsplit('.', 1)[-1]}, which this build cannot "
        f"decode on its own, and no extracted copy was found to fall back on"
    )


def repack_afa_in_place(
    project: ProjectState,
    manifest: Manifest,
    *,
    extract_dir: str | Path | None = None,
    sticker_resolver=None,
    on_progress=None,
    workers: int = DEFAULT_WORKERS,
) -> AfaRepackResult:
    """Rebuild the project's own archive, censored, overwriting it.

    Reads from the pristine backup rather than from the archive being
    written, and makes that backup first if it is not there yet.
    """
    archive_path = Path(manifest.resolved_archive_path())
    ensure_archive_backup(archive_path)
    backup_path = archive_path.with_name(archive_path.name + ".orig-backup")
    source = backup_path if backup_path.exists() else archive_path

    if source == archive_path:
        raise AfaRepackError(
            "There is no pristine copy of this archive to rebuild from. "
            "Rebuilding in place would read the file it is writing."
        )

    return repack_afa(
        project,
        manifest,
        source_archive=source,
        output_archive=archive_path,
        extract_dir=extract_dir,
        sticker_resolver=sticker_resolver,
        on_progress=on_progress,
        workers=workers,
    )


def verify_afa(archive_path: str | Path, expected: AfaRepackResult) -> list[str]:
    """Read the finished archive back and report anything wrong with it.

    The export path asks alice-tools to list the result, partly to catch
    the filename corruption in issue 92. Here the table was written by this
    package, so this package reads it back, which proves every entry is
    reachable at the offset its table row claims and that no name was lost
    along the way.
    """
    problems: list[str] = []
    try:
        reader = AfaReader(archive_path)
    except AfaError as exc:
        return [f"the rebuilt archive does not read back, {exc}"]

    with reader:
        got = [entry.name for entry in reader.entries]
        if len(got) != len(expected.expected_names):
            problems.append(
                f"the rebuilt archive holds {len(got)} files and the original held "
                f"{len(expected.expected_names)}"
            )
        missing = set(expected.expected_names) - set(got)
        if missing:
            problems.append(f"{len(missing)} name(s) missing from the rebuilt archive")
        if got != expected.expected_names:
            problems.append(
                "the rebuilt archive lists its files in a different order to the "
                "original, which the game indexes into"
            )
        for entry in reader.entries:
            if entry.size == 0:
                problems.append(f"{entry.name} is empty")
    return problems
