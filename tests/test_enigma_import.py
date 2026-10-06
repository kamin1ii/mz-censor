from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import struct
import zlib

from PIL import Image
import pytest

from alice_censor.project import CensorLayer, LayerType
from rpg_censor.engine import encrypt_image, decrypt_image
from rpg_censor.image_decode import validated_image
from rpg_censor.project import import_game
from rpg_censor.deploy import apply_project, restore_project
from rpg_censor.extracted import merge_loose, collect_inline, install_inline_bridge

KEY = '00112233445566778899aabbccddeeff'


def png(color='white'):
    buffer = BytesIO()
    Image.new('RGBA', (12, 10), color).save(buffer, format='PNG')
    return buffer.getvalue()


def chunk(kind, data, bad_crc=False):
    crc = (zlib.crc32(kind + data) & 0xffffffff) ^ int(bad_crc)
    return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', crc)


def setup_game(root):
    (root / 'data').mkdir(parents=True)
    (root / 'data/System.json').write_text(json.dumps({'gameTitle': 'Test', 'encryptionKey': KEY}))
    (root / 'js').mkdir()
    (root / 'js/rpg_core.js').write_text('// fixture')
    (root / 'index.html').write_text('<html><head></head><body></body></html>')


def test_bad_icc_repaired_without_changing_pixel_chunks():
    original = png()
    damaged = original[:33] + chunk(b'iCCP', b'bad profile', True) + original[33:]
    repaired, messages = validated_image(damaged)
    assert repaired == original
    assert len(messages) == 1 and 'iCCP' in messages[0]


@pytest.mark.parametrize('kind', [b'IHDR', b'IDAT', b'PLTE', b'tRNS'])
def test_pixel_relevant_corruption_is_never_repaired(kind):
    data = png()
    damaged = data[:33] + chunk(kind, b'invalid', True) + data[33:]
    with pytest.raises(ValueError, match='checksum'):
        validated_image(damaged)


def test_truncated_png_and_corrupt_compressed_pixels_fail():
    with pytest.raises(ValueError, match='Truncated'):
        validated_image(png()[:-2])
    data = png()
    data = data[:33] + chunk(b'IDAT', b'not zlib') + chunk(b'IEND', b'')
    with pytest.raises(OSError):
        validated_image(data)


def test_repair_audited_and_restore_keeps_exact_original_bytes(tmp_path):
    root = tmp_path / 'game/www'
    setup_game(root)
    data = png()
    damaged = data[:33] + chunk(b'iCCP', b'bad profile', True) + data[33:]
    target = root / 'image.rpgmvp'
    original = encrypt_image(damaged, KEY)
    target.write_bytes(original)
    project = import_game(root, tmp_path / 'project')
    assert project.assets['image.png']['repairs']
    assert (Path(project.extract_dir) / 'image.png').read_bytes() == data
    assert target.read_bytes() == original
    project.images['image.png'].layers.append(CensorLayer('t', LayerType.SOLID, (0, 0, 1, 1), {'color': '#000000'}))
    apply_project(project)
    assert decrypt_image(target.read_bytes(), KEY) != damaged
    restore_project(project)
    assert target.read_bytes() == original
    from rpg_censor.audit import audit_project
    report = audit_project(project)
    assert report['passed'] and 'image.png' in report['metadata_repairs']


def test_merge_preserves_packed_priority_and_all_different_image_variants(tmp_path):
    source = tmp_path / 'source'
    setup_game(source / 'www')
    (source / 'Game.exe').write_bytes(b'fixture')
    (source / 'www/shared.png').write_bytes(png('red'))
    (source / 'www/loose.png').write_bytes(png('green'))
    (source / 'www/same.png').write_bytes(png('white'))
    dest = tmp_path / 'copy'
    (dest / 'www').mkdir(parents=True)
    (dest / 'www/shared.png').write_bytes(png('blue'))
    (dest / 'www/same.png').write_bytes(png('white'))
    entries = [{'path': f.relative_to(dest).as_posix(), 'sha256': sha256(f.read_bytes()).hexdigest()}
               for f in (dest / 'www').iterdir()]
    rows, variants = merge_loose(source / 'Game.exe', dest, entries, lambda _: None)
    assert variants == ['www/loose-variants/shared.png']
    assert (dest / 'www/shared.png').read_bytes() == png('blue')
    assert (dest / variants[0]).read_bytes() == png('red')
    assert (dest / 'www/loose.png').is_file()
    assert not (dest / 'Game.exe').exists()
    assert any(r['resolution'] == 'same' for r in rows)


def test_inline_images_deduplicate_track_origins_and_apply_restore(tmp_path):
    import base64
    root = tmp_path / 'game/www'
    setup_game(root)
    data = png()
    literal = 'data:image/png;base64,' + base64.b64encode(data).decode()
    for name in ['a.js', 'b.js']:
        (root / 'js' / name).write_text('const x="' + literal + '";')
    rows = collect_inline(root)
    assert len(rows) == 1 and rows[0]['origins'] == ['js/a.js', 'js/b.js']
    install_inline_bridge(root, rows)
    assert 'censor-inline.js' in (root / 'index.html').read_text()
    p = import_game(root, tmp_path / 'project')
    name = rows[0]['path']
    p.images[name].layers.append(CensorLayer('t', LayerType.SOLID, (0, 0, 1, 1), {'color': '#000000'}))
    apply_project(p)
    with Image.open(root / name) as im:
        assert im.getpixel((0, 0)) == (0, 0, 0, 255)
    restore_project(p)
    assert (root / name).read_bytes() == data


def test_folder_import_refuses_enigma_before_creating_incomplete_project(tmp_path):
    root = tmp_path / 'game/www'
    setup_game(root)
    (root.parent / 'Game.exe').write_bytes(b'wrapped\x45\x56\x42\x00archive')
    with pytest.raises(ValueError, match='Import Packed EXE'):
        import_game(root, tmp_path / 'project')
    assert not (tmp_path / 'project').exists()


def test_japanese_scene_groups_keep_scene_and_folder_boundaries():
    from rpg_censor.grouping import scene_key
    assert scene_key('img/6_添い寝_01普通.png') == scene_key('img/6_添い寝_赤面.png')
    assert scene_key('img/6_添い寝_01普通.png') != scene_key('img/7_添い寝_01普通.png')
    assert scene_key('img/6_添い寝_01普通.png') != scene_key('loose-variants/img/6_添い寝_01普通.png')


def test_launcher_refuses_a_mismatched_runtime(tmp_path):
    from rpg_censor.runtime import install_launcher
    (tmp_path / 'nw.dll').write_bytes(b'wrong version')
    with pytest.raises(ValueError, match='different NW.js runtime'):
        install_launcher(tmp_path)
    assert not (tmp_path / 'Game.exe').exists()


def test_complete_extracted_import_accounts_for_pack_loose_and_inline(tmp_path, monkeypatch):
    import base64
    from rpg_censor.extracted import import_extracted
    from rpg_censor.audit import audit_project
    source = tmp_path / 'original'
    setup_game(source / 'www')
    (source / 'Game.exe').write_bytes(b'wrapped EVB\x00')
    (source / 'www/shared.png').write_bytes(png('red'))
    (source / 'www/js/literal.js').write_text('x="data:image/png;base64,' + base64.b64encode(png()).decode() + '"')
    dest = tmp_path / 'playable'
    (dest / 'www').mkdir(parents=True)
    (dest / 'www/shared.png').write_bytes(png('blue'))
    entries = [{'path': 'www/shared.png', 'sha256': sha256(png('blue')).hexdigest(), 'size': len(png('blue'))}]
    monkeypatch.setattr('rpg_censor.extracted.install_launcher', lambda p: (p / 'Game.exe').write_bytes(b'clean launcher'))
    project = import_extracted(source / 'Game.exe', dest, tmp_path / 'project', entries, lambda _: None)
    assert len(project.assets) == 3
    assert not project.packed  # Direct file Apply/Restore; no SecuPacker bridge.
    report = audit_project(project)
    assert report['passed'] and report['packed_entries'] == 1
    assert report['unaccounted_packed_entries'] == 0
    assert report['loose_image_variants'] == 1
    assert report['embedded_images'] == 1
    assert (source / 'www/shared.png').read_bytes() == png('red')


@pytest.mark.parametrize('problem', [None, 'unaccounted', 'traversal'])
def test_container_extraction_accounts_for_every_entry(tmp_path, monkeypatch, problem):
    from evbunpack import __main__ as evb
    from rpg_censor.enigma import extract_exe
    exe = tmp_path / 'packed.exe'
    exe.write_bytes(evb.EVB_MAGIC + b'fixture')
    name = '..' if problem == 'traversal' else 'image.png'
    node = {'name': name, 'type': evb.NODE_TYPE_FILE, 'original_size': len(png())}
    nodes = [{'objects_count': 1}, {'name': '%DEFAULT FOLDER%', 'type': evb.NODE_TYPE_FOLDER,
                                  'objects_count': 1}, node]
    if problem == 'unaccounted':
        nodes.append(dict(node, name='omitted.png'))
    monkeypatch.setattr(evb, 'pe_external_tree', lambda stream: iter(nodes))
    monkeypatch.setattr(evb, 'process_file_node', lambda stream, target, record: Path(target).write_bytes(png()))
    if problem:
        with pytest.raises(ValueError, match='Unaccounted|Invalid name'):
            extract_exe(exe, tmp_path / 'copy')
    else:
        rows = extract_exe(exe, tmp_path / 'copy')
        assert rows == [{'path': 'image.png', 'size': len(png()), 'sha256': sha256(png()).hexdigest()}]
