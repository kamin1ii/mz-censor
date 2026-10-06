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
