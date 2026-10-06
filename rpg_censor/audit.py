"""Inventory reports explicitly distinguish coverage, review and applied edits."""
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path

from PIL import Image
from .engine import IMAGE_EXTENSIONS, safe_path


def audit_project(project, progress=lambda message: None):
    errors = []
    for index, (name, asset) in enumerate(project.assets.items()):
        try:
            path = safe_path(Path(project.extract_dir), name)
            if sha256(path.read_bytes()).hexdigest() != asset['original_sha256']:
                raise ValueError('cached original differs from import')
            with Image.open(path) as image:
                image.verify()
        except Exception as error:
            errors.append(f'{name}: {error}')
        if index % 100 == 0:
            progress(f'Checking image {index + 1}/{len(project.assets)}')
    inventory_file = project.project_file.parent / 'inventory.json'
    inventory = json.loads(inventory_file.read_text(encoding='utf-8')) if inventory_file.exists() else []
    indexed_sources = {a['source'] for a in project.assets.values()}
    inventoried_images = {i['path'] for i in inventory if i['image']}
    missing = sorted(inventoried_images - indexed_sources)
    errors.extend(f'Image omitted from gallery: {name}' for name in missing)
    counts = Counter(str(Path(a['source']).parent).replace('\\', '/') for a in project.assets.values())
    packed_report = project.project_file.parent / 'packed-import.json'
    packed = json.loads(packed_report.read_text(encoding='utf-8')) if packed_report.exists() else {}
    if project.packed and (not packed or packed.get('omitted') != 0):
        errors.append('Packed-file completeness report is missing or contains omitted entries')
    report = {
        'game': project.title, 'engine': project.engine,
        'images': len(project.assets), 'inventory_files': len(inventory),
        'packed_entries': packed.get('packedEntries'), 'unaccounted_packed_entries': packed.get('omitted'),
        'inventory_images': len(inventoried_images), 'folders': dict(sorted(counts.items())),
        'embedded_images': sum(bool(row.get('embedded')) for row in inventory),
        'status_counts': dict(Counter(r.status.value for r in project.images.values())),
        'edited_images': sum(bool(project.enabled_layers(p)) for p in project.assets),
        'errors': errors, 'passed': not errors,
        'scope': 'All supported image files exposed by the imported filesystem, including nested folders, icons, '
                 'extensionless images, and recognized literal base64 images in code. '
                 'Runtime-generated drawings and video frames are not separate editable files. '
                 'Review every gallery image; flags are filename suggestions, not content detection.',
    }
    from .deploy import write_json
    write_json(project.project_file.parent / 'image-audit.json', report)
    progress(f"Audit {'passed' if not errors else 'FAILED'}: {len(project.assets)} images, {len(errors)} errors")
    return report
