# Attribution

MZ Censor is derived from [Alice Censor](https://github.com/kamin1ii/alice-censor),
by kamin1ii, under GPL-3.0-or-later. Its editor, gallery, layer renderer, and tests
are retained with Git history.

[evbunpack](https://github.com/mos9527/evbunpack) 0.2.4, by mos9527,
provides the Enigma Virtual Box filesystem parser and decompressor under Apache-2.0.
Its license is included in `licenses/evbunpack-LICENSE` and in executable distributions.

The custom runtime bridge was implemented here; it does not bundle game code,
game assets, encryption keys, or SecuPacker source. Its integration was checked against
the public [SecuPacker source](https://github.com/Churitoring/SecuPacker) and the local demo.

Other dependencies: PySide6/Qt (LGPL/GPL/commercial), Pillow (HPND),
pefile (MIT), and PyInstaller (GPL with its distribution exception).
Their own notices and license terms remain applicable.

The Windows build bundles the unmodified `nw.exe` launcher from the official
[NW.js 0.29.0 Windows ia32 distribution](https://dl.nwjs.io/v0.29.0/nwjs-v0.29.0-win-ia32.zip).
NW.js is MIT licensed, with Chromium and other components under their own terms.
Its complete upstream `credits.html` accompanies the launcher in the bundle and
is copied to `nwjs-credits.html` in imported games. `setup_runtime.py` pins both
the archive and launcher SHA-256 checksums. Only the launcher and notices are
bundled; the matching runtime DLLs are obtained from the user's own game copy.
