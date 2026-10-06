"""Verified exports and reversible application. Never render over cached originals."""
from __future__ import annotations

from hashlib import sha256
from io import BytesIO
import json
import os
from pathlib import Path
import tempfile

from PIL import Image
from alice_censor.rendering import render_layers
from alice_censor.stickers import make_sticker_resolver
from .engine import encrypt_image, safe_path


def atomic_write(path: Path, data: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix='.tmp')
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def write_json(path, value):
    atomic_write(path, json.dumps(value, ensure_ascii=False, indent=2).encode('utf-8'))


def rendered(project, progress=lambda message: None):
    resolver = make_sticker_resolver(project.sticker_dir)
    results = {}
    for name, asset in project.assets.items():
        layers = project.enabled_layers(name)
        if not layers:
            continue
        original = safe_path(Path(project.extract_dir), name).read_bytes()
        if sha256(original).hexdigest() != asset['original_sha256']:
            raise ValueError(f"Cached original changed: {name}. Refusing to compound edits.")
        with Image.open(BytesIO(original)) as opened:
            base = opened.convert('RGBA')
        output = render_layers(base, layers, sticker_resolver=resolver)
        buffer = BytesIO()
        output.save(buffer, format='PNG')
        png = buffer.getvalue()
        with Image.open(BytesIO(png)) as verified:
            if verified.convert('RGBA').tobytes() != output.convert('RGBA').tobytes():
                raise ValueError(f"Rendered PNG verification failed: {name}")
        results[name] = png
        progress(f"Rendered {name}")
    return results


def apply_packed(project, results, progress):
    root = Path(project.game_root)
    if not (root / 'censor-boot.js').is_file():
        raise ValueError("Packed-game bridge is missing. Re-import the executable into a new copy.")
    mapping = {}
    for name, png in results.items():
        # Content addressing makes switching the single map an atomic update.
        relative = 'censor-images/' + sha256(png).hexdigest() + '.png'
        target = safe_path(root, relative)
        atomic_write(target, png)
        if target.read_bytes() != png:
            raise ValueError(f"Export verification failed: {name}")
        mapping[name] = relative
        source = project.assets[name]['source']
        if not Path(source).suffix:
            mapping[source] = relative
    write_json(root / 'censor-overrides.json', mapping)
    progress(f"Applied {len(mapping)} image overrides. Restart the playable copy to see them.")
    return len(results)


def _ledger(root):
    file = root / '.rpg-censor/deployed.json'
    ledger = json.loads(file.read_text(encoding='utf-8')) if file.exists() else {}
    for name in ledger:
        safe_path(root, name)
    return file, ledger


def apply_loose(project, results, progress):
    root = Path(project.game_root)
    ledger_file, ledger = _ledger(root)
    wanted = {}
    for name, png in results.items():
        asset = project.assets[name]
        if asset['encrypted']:
            data = encrypt_image(png, project.encryption_key)
        elif Path(asset['source']).suffix.lower() == '.png':
            data = png
        else:
            # Keep the original format. PNG bytes under .jpg are browser-readable,
            # but preserving the format also supports plugins that inspect headers.
            with Image.open(BytesIO(png)) as image:
                buf = BytesIO()
                ext = Path(asset['source']).suffix.lower()
                fmt = Image.registered_extensions().get(ext)
                if not fmt:
                    with Image.open(safe_path(Path(project.extract_dir), name)) as original:
                        fmt = original.format
                if not fmt:
                    raise ValueError(f"Export format is not supported: {name}")
                if fmt == 'JPEG':
                    image = image.convert('RGB')
                image.save(buf, format=fmt)
                data = buf.getvalue()
        wanted[asset['source']] = (data, asset['sha256'])
    targets = set(wanted) | set(ledger)
    plan = []
    # Validate every target and backup before changing any game image.
    for name in sorted(targets):
        target = safe_path(root, name)
        current = target.read_bytes()
        digest = sha256(current).hexdigest()
        backup = safe_path(root / '.rpg-censor/originals', name)
        record = ledger.get(name)
        if record:
            original = backup.read_bytes()
            if sha256(original).hexdigest() != record['original']:
                raise ValueError(f"Backup changed: {name}")
            if digest not in record['allowed']:
                raise ValueError(f"Game file changed outside this tool: {name}. Restore or re-import the updated game.")
        else:
            original = current
            if digest != wanted[name][1]:
                raise ValueError(f"Game file changed since import: {name}")
            if backup.exists() and backup.read_bytes() != original:
                raise ValueError(f"An unrelated backup already exists: {name}")
        new = wanted[name][0] if name in wanted else original
        plan.append((name, target, backup, original, new, digest))
    for name, target, backup, original, new, digest in plan:
        if not backup.exists():
            atomic_write(backup, original)
        ledger[name] = {'original': sha256(original).hexdigest(),
                        'allowed': list({digest, sha256(original).hexdigest(), sha256(new).hexdigest()})}
    # Write recovery data first: interrupted deployment accepts either old or new
    # bytes on the next run, but never an unrelated external modification.
    write_json(ledger_file, ledger)
    for name, target, backup, original, new, digest in plan:
        atomic_write(target, new)
        if target.read_bytes() != new:
            raise ValueError(f"Write verification failed: {name}. Backups are available for Restore.")
        ledger[name]['allowed'] = list({sha256(original).hexdigest(), sha256(new).hexdigest()})
        progress(f"Applied {name}" if name in wanted else f"Restored {name}")
    write_json(ledger_file, ledger)
    return len(wanted)


def apply_project(project, progress=lambda message: None):
    results = rendered(project, progress)
    return (apply_packed if project.packed else apply_loose)(project, results, progress)


def restore_project(project, progress=lambda message: None):
    # Does not discard layers: users can restore the game and reapply later.
    return (apply_packed if project.packed else apply_loose)(project, {}, progress)
