"""Standard Enigma + loose MV files, including shadowed image variants."""
import base64
from hashlib import sha256
from pathlib import Path
import re
import shutil

from .deploy import write_json
from .engine import detect_game, image_paths, safe_path
from .enigma import file_sha256
from .project import import_game
from .runtime import install_launcher

INLINE = re.compile(rb'data:image/(png|gif|jpeg|jpg|webp|bmp);base64,([A-Za-z0-9+/=]+)', re.I)


def merge_loose(source, playable, entries, progress):
    """Virtual files take precedence; expose differing loose copies as variants."""
    packed = {row['path'].casefold(): row for row in entries}
    loose_images = {p.resolve() for p in image_paths(detect_game(source.parent))}
    loose_rows, variants = [], []
    for file in sorted(source.parent.rglob('*')):
        if not file.is_file():
            continue
        relative = file.relative_to(source.parent).as_posix()
        safe_path(source.parent, relative)
        # Saves stay with the original game; unrelated installers are not launched/copied.
        if any(p in {'save', '.rpg-censor', 'censor-images'} for p in Path(relative).parts):
            continue
        if '/' not in relative and file.suffix.lower() == '.exe':
            continue
        digest = file_sha256(file)
        row = {'path': relative, 'sha256': digest, 'size': file.stat().st_size}
        existing = packed.get(relative.casefold())
        if existing:
            row['resolution'] = 'same' if digest == existing['sha256'] else 'packed takes precedence'
            if digest != existing['sha256'] and file.resolve() in loose_images:
                variant = 'www/loose-variants/' + relative.removeprefix('www/')
                target = safe_path(playable, variant)
                if target.exists():
                    raise ValueError(f'Loose variant path collision: {variant}')
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(file, target)
                row['variant'] = variant
                variants.append(variant)
        else:
            target = safe_path(playable, relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(file, target)
            row['resolution'] = 'copied'
        loose_rows.append(row)
        if len(loose_rows) % 100 == 0:
            progress(f'Combined {len(loose_rows)} loose files with the packed inventory')
    return loose_rows, variants


def collect_inline(root):
    """Give literal images a real editable file and retain every referring origin."""
    found = {}
    for file in sorted(root.rglob('*')):
        if not file.is_file() or file.suffix.lower() not in {'.js', '.json', '.html', '.css', '.txt'}:
            continue
        if any(p in {'save', '.rpg-censor', 'loose-variants'} for p in file.relative_to(root).parts):
            continue
        for match in INLINE.finditer(file.read_bytes()):
            data = base64.b64decode(match[2], validate=True)
            digest = sha256(data).hexdigest()
            ext = match[1].decode().lower().replace('jpeg', 'jpg')
            relative = f'embedded/{digest}.{ext}'
            target = safe_path(root, relative)
            if target.exists() and target.read_bytes() != data:
                raise ValueError(f'Embedded image path collision: {relative}')
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            row = found.setdefault(relative, {'path': relative, 'image': True, 'embedded': True,
                                              'size': len(data), 'sha256': digest, 'origins': []})
            origin = file.relative_to(root).as_posix()
            if origin not in row['origins']:
                row['origins'].append(origin)
    return list(found.values())


def install_inline_bridge(root, rows):
    if not rows:
        return
    write_json(root / 'censor-inline.json', {row['sha256']: row['path'] for row in rows})
    shutil.copyfile(Path(__file__).with_name('inline_boot.js'), root / 'censor-inline.js')
    index = root / 'index.html'
    html = index.read_text(encoding='utf-8-sig')
    # Hook Image.src before the engine/plugins first evaluate inline literals.
    html, count = re.subn(r'(<head\b[^>]*>)', r'\1\n<script src="censor-inline.js"></script>', html, count=1, flags=re.I)
    if count != 1:
        raise ValueError('Cannot install embedded-image support: HTML head is missing')
    index.write_text(html, encoding='utf-8')


def import_extracted(source, playable, workspace, entries, progress):
    loose, variants = merge_loose(source, playable, entries, progress)
    root = detect_game(playable).root
    if root != playable / 'www':
        raise ValueError('This Enigma layout requires a standard www game folder')
    # Never run the wrapped host: it would invisibly reload uncensored originals.
    install_launcher(playable)
    embedded = collect_inline(root)
    install_inline_bridge(root, embedded)
    images = {f.relative_to(root).as_posix() for f in image_paths(detect_game(playable))}
    inline_rows = {row['path']: row for row in embedded}
    inventory = []
    for file in sorted(root.rglob('*')):
        if file.is_file():
            rel = file.relative_to(root).as_posix()
            inventory.append(inline_rows.get(rel) or {'path': rel, 'image': rel in images,
                                                       'size': file.stat().st_size})
    # Verify packed files survived the merge byte for byte. Startup HTML is loose.
    for row in entries:
        if file_sha256(safe_path(playable, row['path'])) != row['sha256']:
            raise ValueError(f"Packed file changed during import: {row['path']}")
    write_json(root / 'inventory.json', inventory)
    project = import_game(root, workspace, progress)
    write_json(workspace / 'inventory.json', inventory)
    write_json(workspace / 'container-inventory.json', {'packed': entries, 'loose': loose})
    write_json(workspace / 'packed-import.json', {
        'layout': 'enigma-loose', 'packedEntries': len(entries), 'omitted': 0,
        'packedImagesMissingFromLoose': sum(row['path'].startswith('www/') and
            row['path'][4:] in images and not (source.parent / row['path']).is_file() for row in entries),
        'looseImageVariants': len(variants), 'embeddedImages': len(embedded),
        'count': len(project.assets), 'engine': project.engine,
        'filePrecedence': 'packed files win; differing loose images appear under loose-variants',
    })
    return project
