# MZ Censor

A Windows image-censor editor for **RPG Maker MV and MZ**, forked from
[kamin1ii/alice-censor](https://github.com/kamin1ii/alice-censor).
It keeps Alice Censor's gallery, review statuses, region editor, stickers, text,
blur, pixelation, solid layers, live preview, and scene-group batch editing.

## Download and use

Download **MZCensor.exe** from [Releases](https://github.com/kamin1ii/mz-censor/releases).
Python and alice.exe are not needed. Run the executable, then:

1. For a normal game, choose **File > New MV/MZ Folder Project** and select the
   game folder (or `www`) plus an empty working folder outside its content folder.
2. For a supported Enigma-packed demo, choose **File > Import Packed EXE**,
   then select an empty folder for a playable copy and a separate empty project folder.
   Import starts the game runtime to read its virtual files. The original EXE is read only.
3. Use **All Images**. Double-click an image, draw censor regions, and click Save.
   Review statuses do not censor pixels; enabled layers do. Filename flags are only suggestions.
4. Choose **Apply Censor Edits**, then restart the game. Use **Launch Game** to
   launch the editable copy, rather than the original packed executable.
5. **Restore Original Images** removes deployed edits but retains your saved layers.

Folders remain nested exactly as in the game. Scene grouping preserves numbered pose/scene
IDs and suggests variant groups. Use **Review > Assign Selected to Scene Group** to
correct a suggestion or group arbitrary selections, then use the editor's batch-apply
button. Manual groups persist and work with the group filter. **Flag by Filename**
accepts a regular expression, previews the match count, and touches only unreviewed
images. Its default pattern avoids accidental matches in words like "sandwich".

Reopen `project.rgcproj.json` to continue. The executable also accepts
`--project "path/to/project.rgcproj.json"`.

## Image coverage

The gallery includes subfolders, UI art, sprites, tiles, icons, animation sheets,
particles, and old/unused variants. There is no default filter hiding unreviewed images.
**Audit All Images** checks every cached image, verifies its import hash, and compares
it with the recorded inventory. The report is saved beside the project as `image-audit.json`.

The custom packed importer captures the runtime's complete file table. It traverses
purely virtual directories that ordinary root listings omit, checks unusual extensions
by signature, and refuses a successful import if any packed entry is unaccounted for.
This catches an extensionless image in the tested demo.

Local validation against the Luft demo 1.4 found **4,330 packed entries and 2,623 image files**,
with zero unaccounted entries. Two additional 1-pixel image literals embedded in code
are also imported, bringing the gallery to **2,625 images**. All decoded and passed the cache/hash audit.
This demo is **MV 1.6.1**, despite the app's MZ Censor name.

The packed importer also scans scripts and compiled startup bytes for literal base64
images and places them in the `embedded` category. Drawings generated dynamically by
game code and video frames are not separate gallery assets. Imported files are not
automatically judged safe: review them all. Unsupported or corrupt image files fail
import visibly instead of being silently skipped. Animated GIF/APNG and SVG require
separate handling; the importer does not flatten them and pretend they are fully edited.

## Applying and restoring

For ordinary MV/MZ games, `.rpgmvp` and `.png_` images are decoded using the game's
own `System.json` key and re-encrypted on export. Only edited files are replaced.
Backups and a recovery ledger live in `www/.rpg-censor/` (or the equivalent MZ root).
Applying twice starts from cached originals; removing layers and applying restores
previously edited files. External changes and damaged backups are detected before writes.

For the supported packed demo, `game.bin` and native binaries remain unchanged.
A small startup bridge preserves the original startup bytes for the packer's environment
fingerprint and supplies the rendered image overrides. Apply switches a single override
map atomically. Restore clears that map. This supports the tested SecuPacker 1.1.6 MV
layout; it is not a promise that every custom packer or packed MZ title works.
A failed packed import may leave its newly created copy and staging folder for diagnosis.

Saved games are separate from image edits. Copy your existing saves to the playable
copy's `www/save` folder if you want to continue progress from the original executable.

Keep the project folder: it holds your original image cache and non-destructive edits.
Game files, decrypted images, keys, and personal projects are never uploaded by the app.

## Development

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m rpg_censor
.venv\Scripts\python -m pytest -q
.\build.ps1
```

The build produces `dist/MZCensor.exe` plus a SHA-256 checksum. CI tests and builds on Windows.
The new workflow is in `rpg_censor/`; reusable editor/gallery/rendering code remains in
`alice_censor/` with its regression tests. The original AliceSoft workflows are preserved
in the source history and shared package, but are not exposed by MZ Censor's UI.

## License

GPL-3.0-or-later, inherited from Alice Censor. See [LICENSE](LICENSE) and
[THIRD_PARTY.md](THIRD_PARTY.md). No game assets are included in this repository or releases.
