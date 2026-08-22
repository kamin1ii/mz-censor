# Alice Censor

A desktop app for censoring CG images extracted from AliceSoft visual novels, built on
[alice-tools](https://github.com/nunuhara/alice-tools).

Doing this by hand means running `ar extract`, opening a folder of a thousand or more PNGs,
finding the ones that need work, editing them in an image editor, remembering to clear
alice-tools' conversion cache, and running `ar pack`. Alice Censor wraps that whole loop in one
window: extract, review in a thumbnail gallery, draw censor regions with live preview, repack,
and verify the result.

Requires Python 3.11 or newer. **alice.exe is not needed.** Alice Censor reads and writes the
archive and image formats itself, so a new project asks for your archive and a folder and nothing
else.

You can still put it through [alice-tools](https://github.com/nunuhara/alice-tools) if you would
rather, by turning off **Advanced > Use built in format support**, in which case you need a recent
nightly alice.exe and not 0.13.0.

## Screenshots

[![Extract and repack tab](https://i.postimg.cc/zN4VkYmB/alicecensor-ss1-repack.png)](https://postimg.cc/6TqJcMZY)

[![Gallery with thumbnails and filters](https://i.postimg.cc/Fvw7xXtc/alicecensor-ss2-gallery.png)](https://postimg.cc/G9twKXDZ)

[![Region editor with layers](https://i.postimg.cc/S4HJGb0x/alicecensor-ss3-editor.png)](https://postimg.cc/9z05bL9b)

## Features

**Extract and repack**

- Reads and writes `.afa` and `.ald` archives itself, and the QNT, AJP, DCF and PMS images inside
  them, so extracting and repacking need no external program. Turn off **Advanced > Use built in
  format support** to run `alice ar extract` and `ar pack` instead, streaming their output into
  the app.
- Backs up the original archive to `<name>.orig-backup` before the first repack, since a repack
  overwrites its target in place with no undo.
- **Only the images you edited are rewritten.** Everything else goes back into the archive as the
  exact bytes it came out as, never decoded and never re-encoded, so a repack cannot quietly
  alter a thousand files you never touched. Rebuilding an archive you have not edited at all
  reproduces it byte for byte.

**Review gallery**

- Virtualized thumbnail grid over every extracted image, so it stays responsive at 1300+ files.
- Tag each image as unreviewed, flagged for censor, reviewed clean, or needs manual edit.
- Filter by folder tree, status, scene group, filename, and whether the image has censor edits.
- Sorted by name, with numbers ordered by value, so a scene reads H01 to H26 in sequence
  rather than in the order the archive happens to store them.
- Thumbnails are cached on disk and show the censored result, not the raw source.
- Auto-flag explicit scenes by naming convention (H01 through H13, 挿入, 射精, and similar) for
  archives whose filenames carry that signal. Only unreviewed images are touched, so it never
  overwrites a decision you already made.

**Region editor**

- Draw rectangular regions on an image and apply solid color, blur, pixelate, or image/sticker
  layers, with live preview of the exact render the export will produce.
- Layers are non-destructive. Nothing is ever written back over the extracted originals, so edits
  never compound on each other, and rects are stored as fractions of image size so they survive a
  re-export at a different resolution.
- Configure a layer's settings once with nothing selected, then stamp out several regions with it.
- **Batch Apply to Scene Group** copies the current layers onto every checked variant image in the same
  scene, each with independent copies so they stay individually adjustable afterwards.
- A managed sticker library, with a thumbnail picker, instead of browsing to a file every time.

**Correctness checks**

- After every repack, the freshly written archive is read back and checked against what the
  manifest says should be in it, rather than trusting that the write reported success. A missing
  file, an empty one, or a change in the order the game indexes into fails verification with an
  explanation instead of a silent "Repack complete".
- Re-opening a project rescans the extraction folder and reports what is new, changed, or missing
  since last time, so a game update does not mean re-reviewing everything.
- The project file is written atomically (temp file plus rename), so a crash mid-save cannot
  corrupt it.

## Sharing a project

**File > Share Project…** writes a zip holding your review statuses, every censor layer you have
drawn, and the stickers those layers use. No images and no paths, so it stays small: a real
project with 771 layers across 529 images comes to about 5 MB.

**File > Open Shared Project…** applies one on top of the project you have open. Images are
matched by the path they have inside the archive, so both sides need the same archive. Anything that
does not line up is reported and skipped rather than invented. Stickers are unpacked into your own
library, and a name you already have is left alone rather than overwritten.

A shared project holds the review work, not the images, so it can only be applied to images you
already have. If you have not extracted the archive yet, it says so and offers to set the project
up first, then applies the shared work once the extraction finishes.

A project file on its own is not shareable, which is what this exists to solve. Every path in it
is absolute and points at one machine, and its overlay layers name stickers that live in that
machine's library. On a real project that is most of the work, since 668 of those 771 layers are
overlays.

## Rebuilding .ald archives (experimental)

> **This is new and lightly tested.** alice-tools cannot write `.ald` at all, so Alice Censor
> writes the format itself. It has been checked against one real archive, which it reproduces
> byte for byte, and edited images come back out of it pixel for pixel. A rebuilt archive has
> also been played in a game, where a few edited images on the save and load menu and the
> splash screens displayed correctly.
>
> That is the whole of the testing. No other game, no other archive, and only a handful of
> interface images rather than scene CGs. Expect the possibility of a missing or broken image
> somewhere nobody has looked yet. Keep your `.orig-backup`, treat a rebuilt archive as something
> to test rather than something to trust, and reports either way are welcome.

alice-tools can extract `.ald` but cannot write it, so Alice Censor writes that format itself
(`alice_censor/formats/ald.py`, following the [format spec](https://haniwa.technology/tech/ald.html)).

Rebuilding from the original archive rather than from an export folder makes the result better
than a general-purpose repack could be. Any image you did not censor is copied across as the
exact bytes it already was, never decoded and never re-encoded. Only the images you actually
drew on are rebuilt.

That matters because roughly half of one tested archive is AJP, which is lossy and which alice-tools
cannot encode at all. Extracting those to PNG and re-encoding would degrade every one of them,
which is why alice-tools' own manifest quietly rewrites the whole lot to QNT. Copying untouched
entries verbatim sidesteps the question. Only an image you edited that was originally AJP has to
become QNT, because no AJP encoder exists anywhere.

The original is backed up to `<name>.orig-backup` first, and every rebuild reads from that backup,
so repacking twice gives the same result instead of stacking a censor on top of a censor.

## Setup

```
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
```

Or install the package itself:

```
pip install -e .
```

## Running

```
.venv\Scripts\python -m alice_censor
```

If installed as a package, the `alice-censor` command does the same thing.

## Building a standalone exe

```
.\build.ps1
```

That runs the tests, builds, checks the exe actually launches, and tidies up, producing a single
`dist\AliceCensor.exe` of about 26 MB. It carries the app icon and needs no Python install on the
target machine, and no `alice.exe` either.

Flags: `-SkipTests` to build without running them first, `-KeepBuildDir` to leave PyInstaller's
intermediate `build\` folder for inspection. If a copy of the app is already running it is closed
first, since a running exe cannot be overwritten and PyInstaller reports that as an unhelpful
`PermissionError`.

The launch check is the part worth keeping. A PyInstaller build can succeed and still produce an
exe that dies instantly, which is exactly what happened the first time this was built, so the
script starts the result and fails if it does not stay up.

To do it by hand instead:

```
.venv\Scripts\pip install pyinstaller
.venv\Scripts\pyinstaller alice-censor.spec
```

**CI**: `.github/workflows/build.yml` runs the same sequence on `windows-latest` for every push
and pull request, uploads the exe as a build artifact, and attaches it to the release when a
`v*` tag is pushed. It also fails if the exe comes out much larger than expected, which catches
an exclusion in the spec quietly ceasing to match after a Qt update.

The size is worth a note, since a PySide6 environment is around 700 MB. Alice Censor uses
QtCore, QtGui and QtWidgets and nothing else, so `alice-censor.spec` drops the rest by name. The
big items are QtWebEngine (an embedded Chromium, roughly 195 MB), Qt's translations, QML and
Quick, the bundled ffmpeg, a software OpenGL fallback, Pillow's AVIF codec, and the networking
and TLS stack, none of which this app touches. The two exclusion lists in the spec are
deliberate rather than copied, and anything wrongly removed shows up immediately as a failure to
launch rather than as a subtle bug.

It is a one-file build, so the first launch unpacks to a temp directory and takes a second or
two. Swap `EXE(...)` for a `COLLECT(...)` build if you would rather have a folder that starts
instantly.

## How it stores your progress

A project is a single JSON file next to your working folder. It holds the paths needed to reopen
the project, per image review status, and the censor layers you have drawn, but no image data.
The extracted images stay pristine. For an `.afa`, censored output is rendered into a separate
folder at export time; for an `.ald`, the archive is rebuilt straight from the backup and there is
no export folder at all. Either way you can delete the working folder and regenerate it by
re-extracting without losing a single review decision or region.

## Project layout

```
alice_censor/
  formats/          Readers and writers for the formats themselves
    qnt.py            QNT images, read and write, lossless, most of a CG archive
    ajp.py            AJP images, read, a scrambled JPEG plus a separate mask
    pms.py            PMS, read, the run length format an AJP mask is stored in
    dcf.py            DCF, read, the chunks that differ from another image
    afa.py            AFA archives, read and write
    ald.py            ALD archives, read and write
  extraction.py     Takes an archive apart into PNG files and a manifest
  afa_repack.py     Rebuilds an .afa with the censored images substituted in
  ald_repack.py     The same for an .ald
  export.py         The alice-tools route instead, an output folder for ar pack
  manifest.py       Parser and writer for alice-tools ALICEPACK manifests
  alice_tools.py    Subprocess wrapper over the alice CLI, archive backups
  project.py        The .acproj.json schema: statuses, groups, censor layers
  scanning.py       Reconciles a manifest against saved state and files on disk
  grouping.py       Scene grouping, by naming convention or numeric proximity
  rendering.py      The single render path shared by preview, thumbnails and export
  verify.py         Post-repack integrity check for the alice-tools route
  stickers.py       The managed sticker library
  paths.py          Archive-internal path handling
  session.py        The open project, its manifest and its tools as one value
  share.py          Packaging a project so someone else can apply it
  gui/              Main window, project dialog, settings, background workers
  gallery/          Thumbnail grid, model, folder tree, disk thumbnail cache
  editor/           Region canvas, layer panel, batch apply, sticker picker
tests/              519 tests, no alice.exe or real archives required
main.py             Entry point for the frozen build
alice-censor.spec   PyInstaller build definition
build.ps1           Test, build, verify the exe launches
make_icon.py        Regenerate the app icon from a source image
```

`rendering.py` is deliberately the only place layers get applied to pixels. The editor preview,
the gallery thumbnails, and the export pipeline all call it, so what you see in the editor cannot
drift from what ends up in the archive.

`formats/` imports nothing from the rest of the app, so it could be lifted out into a library of
its own. `formats/__init__.py` is the only place that decides which format a given lump of bytes
is; nothing else tests magic numbers for itself.

## Development

```
.venv\Scripts\pytest
```

The suite runs offscreen (`QT_QPA_PLATFORM=offscreen`) and needs neither a display nor a real
`alice.exe`, so it works in CI. Manifest fixtures captured from real archives live in
`tests/fixtures/`.

## Automatic detection, on a branch

There is an experiment that looks through the images and proposes censor regions on its own,
using two object detection models. It is not part of this app. It lives on the `autodetect`
branch, with a build attached to the `detect-0.7.0` pre-release.

It is not accurate enough to lean on. In one real project it found something in roughly nine of
ten images that had been censored by hand, which sounds better than it is: it still misses
things, still boxes the occasional wall, and everything it proposes has to be looked at anyway.
On top of that it needs a machine learning runtime that nearly doubles the size of the exe, and
model files of tens of megabytes. Not good enough to rely on and not small, so the ordinary build
leaves it out.

## Notes and caveats

- alice-tools is a separate project and is not bundled here. It is optional, and only used if you
  turn off **Advanced > Use built in format support**. If you do, use the latest nightly and not
  0.13.0 or you will experience missing events/cards/items/areas/etc. Alice Censor checks this for
  you and refuses to run rather than failing halfway, since 0.13.0 has no `--manifest` and
  responds to one by printing its usage text, extracting nothing and reporting success.
- The built in support covers QNT, AJP, DCF and PMS images inside .afa and .ald archives, which is
  everything in the archives this was built against. An archive carrying something else needs
  alice.exe. The formats were checked entry by entry against it: all 3683 QNT and DCF images in a
  772 MB archive decode pixel for pixel the same, as does the transparency of all 920 AJP images
  in another, and a rebuilt archive with
  nothing edited comes out byte for byte identical to the original.
- The version number cannot tell you which build you have. The nightlies still report
  `alice-tools version 0.13.0`, the same string the 2023 release reports, so the check asks the
  binary which flags its `ar extract` supports instead.
- **Editing and rebuilding `.ald` archives is experimental.** That path is written by Alice
  Censor rather than by alice-tools, which has no `.ald` writer at all (`ar_pack` rejects any
  output that is not `.afa`, and `write_afa.c` is its only archive writer). It has had far less
  real use than the `.afa` path. A rebuilt archive has been played in a game, far enough to
  confirm a few edited interface images render correctly, and that is the extent of it. The app
  says so when you open such a project, and asks again before overwriting the archive.
  See **Rebuilding .ald archives** above.
- Multi-volume `.ald` sets (`fooA.ald` plus `fooB.ald`) are refused. alice-tools extracts the
  whole set as one archive, so rebuilding a single volume would drop everything held in the
  others.
- AliceSoft archives have no real directories. The fullwidth solidus in an entry name looks like a
  path separator but every file extracts flat, which is why grouping and file lookup treat it
  differently. See the docstring in `paths.py`.
- Scene grouping for `.ald` archives is a suggestion based on numeric proximity of opaque IDs, not
  a real scene boundary detector, because those filenames carry no naming signal at all.
- Auto-flagging is a naming heuristic, not a content classifier. It is a first pass to save time,
  and manual review is still the thing that decides.
- Some images are stored as a difference against another image (a `.dcf`). Editing one used to
  produce a black screen in game, because the encoder blanks the matching parts including their
  transparency while AliceSoft's own difference images carry none. Alice Censor now writes an
  edited difference image out whole, which fixes it at the cost of some archive size. Untouched
  ones are copied through as their original bytes and are unaffected.
- Repacking rewrites your game archive in place. Keep the automatic `.orig-backup`. For an
  `.afa` it is your safety net, and for an `.ald` it is also the source every rebuild reads from,
  which is what stops a second repack stacking a censor on top of the first one.

## License

GPL-3.0, see [LICENSE](LICENSE). alice-tools is a separate program and keeps its own.

