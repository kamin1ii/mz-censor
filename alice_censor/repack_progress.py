"""What a repack says about itself while it runs.

Both repack paths do the same two things to an archive. Images that have
layers are decoded, drawn on and encoded again, and everything else is
copied through as the bytes it already was. Only the first kind is worth a
line, since a copy is a memcpy and there can be thousands of them.

The wording lives here rather than in the window because the two paths had
already drifted. The AFA one reported only the images it changed, the ALD
one reported every entry in the manifest, and both called it "reading",
which is the least of what happens to an image and untrue of the copies.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RepackProgress:
    """One image about to be worked on, and where it sits in the run."""

    done: int     # 1 based, counting only images that need work
    total: int    # how many need work
    copies: int   # how many are going through untouched
    path: str


def describe(progress: RepackProgress) -> str:
    """The log text for this image, which opens with a header on the first.

    The path is given whole. Trimming it to the filename was tried and is a
    bad trade. Half the names in a real archive are only distinguishable by
    the folder they sit in, several hundred images being called the
    equivalent of "default", and the full name is barely longer anyway.

    The header waits for the first image rather than being logged up front
    because the counts are only known once the archive has been read and
    the work divided up.
    """
    line = f"  processing {progress.done} of {progress.total}  {progress.path}"
    if progress.done != 1:
        return line
    header = f"  {progress.total} to process, {progress.copies} copied unchanged"
    return f"{header}\n{line}"
