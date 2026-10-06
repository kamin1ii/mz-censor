"""Enigma import and the demo's SecuPacker image-mod bridge.

Only local copies are instrumented. evbunpack provides the container parser;
the game's own runtime reads its virtual images, without distributing keys.
"""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import time

from .project import import_game


def unpack_exe(source: Path, destination: Path, progress=lambda message: None):
    from .enigma import extract_exe
    return extract_exe(source, destination, progress)


def install_bridge(root: Path):
    index = root / 'index.html'
    original = root / 'censor-original.html'
    if not original.exists():
        original.write_bytes(index.read_bytes())
    text = original.read_text(encoding='utf-8')
    if 'evalNWBin' not in text or 'game.bin' not in text:
        raise ValueError("Unsupported packed loader: expected the SecuPacker game.bin bootstrap")
    source = Path(__file__).with_name('packed_boot.js')
    shutil.copyfile(source, root / 'censor-boot.js')
    # Insert before the compiled loader, then install synchronously after it.
    text = text.replace('<script>try{', '<script src="censor-boot.js"></script>\n<script>try{', 1)
    text = text.replace('</body>', '<script>window.censorInstall();</script>\n</body>')
    index.write_text(text, encoding='utf-8', newline='')


def import_packed(source: str | Path, playable: str | Path, workspace: str | Path,
                  progress=lambda message: None):
    source, playable, workspace = Path(source).resolve(), Path(playable).resolve(), Path(workspace).resolve()
    if workspace.exists() and any(workspace.iterdir()):
        raise ValueError("Choose an empty project folder")
    if workspace.is_relative_to(playable) or playable.is_relative_to(workspace):
        raise ValueError("The playable copy and project folder must be separate")
    if playable.is_relative_to(source.parent) or source.parent.is_relative_to(playable):
        raise ValueError("Keep the playable copy separate from the original game folder")
    if workspace.is_relative_to(source.parent):
        raise ValueError("Keep the project outside the original game folder")
    entries = unpack_exe(source, playable, progress)
    root = playable / 'www'
    if not (root / 'game.bin').is_file():
        from .extracted import import_extracted
        return import_extracted(source, playable, workspace, entries, progress)
    if not (playable / 'Game.exe').is_file():
        raise ValueError("SecuPacker package has no embedded Game.exe")
    install_bridge(root)
    # Staging is outside the playable tree: extra .bin/.dll files would change
    # the packer's environment fingerprint. No package.json changes are made.
    staging = workspace.parent / (workspace.name + '-import')
    if staging.exists():
        raise ValueError(f"Import staging already exists: {staging}")
    staging.mkdir(parents=True)
    result_path = staging / 'result.json'
    request = root / 'censor-import-request.json'
    request.write_text(json.dumps({'output': str(staging), 'result': str(result_path)}), encoding='utf-8')
    progress('Reading packed images with the game runtime. This may take a few minutes…')
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = 0
    proc = subprocess.Popen([str(playable / 'Game.exe')], cwd=playable, startupinfo=startup)
    try:
        deadline = time.monotonic() + 600
        while proc.poll() is None and not result_path.exists():
            if time.monotonic() > deadline:
                raise TimeoutError("The game did not finish its image import within ten minutes")
            time.sleep(0.25)
        if not result_path.exists():
            raise ValueError("The packed game's runtime could not import its images")
        result = json.loads(result_path.read_text(encoding='utf-8'))
        if result.get('error'):
            raise ValueError(result['error'])
        if result.get('engine') != 'MV':
            raise ValueError("This custom packed-game bridge currently supports MV only")
        progress(f"Read {result['count']} packed images; preparing gallery…")
        project = import_game(staging, workspace, progress)
        project.game_root = str(root)
        project.packed = True
        shutil.copyfile(staging / 'inventory.json', workspace / 'inventory.json')
        shutil.copyfile(result_path, workspace / 'packed-import.json')
        project.save()
        return project
    finally:
        request.unlink(missing_ok=True)
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.terminate()
            proc.wait(timeout=10)
        # Staging is kept on failure to make diagnosis/recovery possible.
        if (workspace / 'project.rgcproj.json').exists():
            shutil.rmtree(staging)
