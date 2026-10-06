from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path

from PIL import Image
import pytest

from alice_censor.project import CensorLayer, LayerType
from rpg_censor.engine import HEADER, decrypt_image, encrypt_image, safe_path
from rpg_censor.project import RpgProject, import_game
from rpg_censor.deploy import apply_project, restore_project
from rpg_censor.audit import audit_project

KEY = '00112233445566778899aabbccddeeff'


def png(color='white'):
    buffer = BytesIO()
    Image.new('RGBA', (20, 16), color).save(buffer, format='PNG')
    return buffer.getvalue()


@pytest.fixture
def game(tmp_path):
    root = tmp_path / '日本語ゲーム' / 'www'
    (root / 'data').mkdir(parents=True)
    (root / 'data/System.json').write_text(json.dumps({'gameTitle': 'Test', 'encryptionKey': KEY}), encoding='utf-8')
    (root / 'js').mkdir()
    (root / 'js/rmmz_core.js').write_text('// fixture')
    for name in ['img/pictures/scene01.png_', 'img/pictures/battle/old/scene02.png_', 'icon/Icon.png']:
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(encrypt_image(png(), KEY) if name.endswith('_') else png())
    return root


def layer():
    return CensorLayer('test', LayerType.SOLID, (0, 0, .5, .5), {'color': '#000000'})


def test_encryption_matches_engine_header_and_xor():
    data = png()
    encoded = encrypt_image(data, KEY)
    assert encoded[:16] == HEADER
    assert encoded[32:] == data[16:]
    assert bytes(a ^ b for a, b in zip(encoded[16:32], bytes.fromhex(KEY))) == data[:16]
    assert decrypt_image(encoded, KEY) == data
    with pytest.raises(ValueError):
        decrypt_image(encoded, 'ff' * 16)


@pytest.mark.parametrize('name', ['../escape.png', '/escape.png', 'C:/escape.png', 'file:stream', r'img\..\bad'])
def test_unsafe_path_refused(tmp_path, name):
    with pytest.raises(ValueError):
        safe_path(tmp_path, name)


def test_import_covers_nested_old_folders_and_icons(game, tmp_path):
    p = import_game(game.parent, tmp_path / 'project')
    assert p.engine == 'MZ'
    assert len(p.assets) == 3
    assert 'img/pictures/battle/old/scene02.png' in p.assets
    assert 'icon/Icon.png' in p.assets
    assert RpgProject.load(p.project_file).assets == p.assets


def test_reimport_cannot_overwrite_review_work(game, tmp_path):
    p = import_game(game, tmp_path / 'project')
    before = p.project_file.read_bytes()
    with pytest.raises(ValueError, match='empty'):
        import_game(game, tmp_path / 'project')
    assert p.project_file.read_bytes() == before


def test_apply_repeat_remove_and_restore_are_lossless(game, tmp_path):
    before = {f: f.read_bytes() for f in game.rglob('*') if f.is_file()}
    p = import_game(game, tmp_path / 'project')
    name = 'img/pictures/scene01.png'
    p.images[name].layers.append(layer())
    assert apply_project(p) == 1
    target = game / p.assets[name]['source']
    first = target.read_bytes()
    assert first != before[target]
    assert decrypt_image(first, KEY).startswith(b'\x89PNG')
    assert apply_project(p) == 1
    assert target.read_bytes() == first
    for file, data in before.items():
        if file != target:
            assert file.read_bytes() == data
    p.images[name].layers.clear()
    assert apply_project(p) == 0
    assert target.read_bytes() == before[target]
    p.images[name].layers.append(layer())
    apply_project(p)
    restore_project(p)
    assert target.read_bytes() == before[target]
    assert p.images[name].layers


def test_external_modification_blocks_before_writes(game, tmp_path):
    p = import_game(game, tmp_path / 'project')
    for record in p.images.values():
        record.layers.append(layer())
    target = game / 'img/pictures/scene01.png_'
    target.write_bytes(b'updated by game patch')
    other = game / 'icon/Icon.png'
    before = other.read_bytes()
    with pytest.raises(ValueError, match='changed'):
        apply_project(p)
    assert other.read_bytes() == before


def test_corrupt_backup_never_restored(game, tmp_path):
    p = import_game(game, tmp_path / 'project')
    name = 'icon/Icon.png'
    p.images[name].layers.append(layer())
    apply_project(p)
    (game / '.rpg-censor/originals/icon/Icon.png').write_bytes(b'bad')
    with pytest.raises(ValueError, match='Backup changed'):
        restore_project(p)


def test_cached_original_changes_block_render(game, tmp_path):
    p = import_game(game, tmp_path / 'project')
    name = 'icon/Icon.png'
    p.images[name].layers.append(layer())
    (Path(p.extract_dir) / name).write_bytes(png('red'))
    with pytest.raises(ValueError, match='Cached original changed'):
        apply_project(p)


def test_packed_map_is_atomic_complete_set(game, tmp_path):
    p = import_game(game, tmp_path / 'project')
    p.packed = True
    (game / 'censor-boot.js').write_text('// bridge')
    name = 'img/pictures/scene01.png'
    p.images[name].layers.append(layer())
    original = (game / p.assets[name]['source']).read_bytes()
    assert apply_project(p) == 1
    mapping = json.loads((game / 'censor-overrides.json').read_text())
    assert name in mapping
    assert (game / mapping[name]).is_file()
    assert (game / p.assets[name]['source']).read_bytes() == original
    restore_project(p)
    assert json.loads((game / 'censor-overrides.json').read_text()) == {}


def test_audit_fails_on_omitted_inventory_image(game, tmp_path):
    p = import_game(game, tmp_path / 'project')
    (p.project_file.parent / 'inventory.json').write_text(json.dumps([
        {'path': 'missing/file.png', 'image': True}]))
    report = audit_project(p)
    assert not report['passed']
    assert 'omitted' in report['errors'][0]


def test_project_rejects_traversal_asset(game, tmp_path):
    p = import_game(game, tmp_path / 'project')
    data = p.to_dict()
    data['assets']['icon/Icon.png']['source'] = '../other.png'
    with pytest.raises(ValueError):
        RpgProject.from_dict(data)


def test_gallery_and_editor_use_imported_images(game, tmp_path, qapp):
    from rpg_censor.gui import MainWindow
    from alice_censor.editor.editor_dialog import RegionEditorDialog
    p = import_game(game, tmp_path / 'project')
    window = MainWindow()
    window.set_project(p)
    assert window.gallery.model.rowCount() == 3
    dialog = RegionEditorDialog(Path(p.extract_dir) / 'icon/Icon.png', p.images['icon/Icon.png'], project=p)
    assert dialog._base_image.size == (20, 16)
    dialog.close()
    window.close()


def test_worker_reports_value_errors(qapp):
    from rpg_censor.gui import Job
    messages = []
    def fail(log):
        raise ValueError('invalid game')
    job = Job(fail)
    job.failure.connect(messages.append)
    job.run()
    assert messages == ['invalid game']


def test_rpg_groups_keep_scene_ids_and_folders_separate():
    from rpg_censor.grouping import scene_key
    assert scene_key('img/pictures/010_Luhutu01_02komaru.png') == 'img/pictures/010_Luhutu01'
    assert scene_key('img/pictures/010_Luhutu02_02komaru.png') == 'img/pictures/010_Luhutu02'
    assert scene_key('img/pictures/CG01_02.png') != scene_key('img/pictures/CG02_02.png')
    assert scene_key('img/pictures/CG01_02.png') != scene_key('img/pictures/old/CG01_02.png')


def test_autoflag_avoids_accidental_h_in_normal_words(game, tmp_path):
    from rpg_censor.grouping import DEFAULT_FLAG_PATTERN, flag_candidates
    import re
    for name in ['fish1.png', '01_sandwich.png', 'Flash.png', 'Earth4.png', 'high.png']:
        assert not re.search(DEFAULT_FLAG_PATTERN, name, re.I)
    for name in ['img/H01.png', 'img/scene_NSFW_01.png', 'img/挿入.png']:
        assert re.search(DEFAULT_FLAG_PATTERN, name, re.I)
    p = import_game(game, tmp_path / 'project')
    p.images['icon/Icon.png'].status = __import__('alice_censor.project', fromlist=['ImageStatus']).ImageStatus.CLEAN
    assert 'icon/Icon.png' not in flag_candidates(p, '.*')


def test_manual_scene_groups_survive_reload_and_filter(game, tmp_path, qapp):
    from rpg_censor.gui import MainWindow
    p = import_game(game, tmp_path / 'project')
    for record in p.images.values():
        record.group_override = 'My scene'
    p.save()
    p = RpgProject.load(p.project_file)
    assert len(p.groups()['My scene'].members) == 3
    window = MainWindow()
    window.set_project(p)
    window.gallery.model.set_filters(group_substr='My scene')
    assert window.gallery.model.rowCount() == 3
    window.close()


def test_new_project_cannot_import_previously_applied_edits_as_originals(game, tmp_path):
    p = import_game(game, tmp_path / 'project')
    p.images['icon/Icon.png'].layers.append(layer())
    apply_project(p)
    with pytest.raises(ValueError, match='Restore this game'):
        import_game(game, tmp_path / 'second-project')
    restore_project(p)
    assert import_game(game, tmp_path / 'second-project').assets


def test_loose_extensionless_image_is_found_and_can_be_applied(game, tmp_path):
    odd = game / 'img/pictures/extensionless'
    odd.write_bytes(png())
    p = import_game(game, tmp_path / 'project')
    assert 'img/pictures/extensionless' in p.assets
    p.images['img/pictures/extensionless'].layers.append(layer())
    assert apply_project(p) == 1
    with Image.open(odd) as image:
        assert image.getpixel((0, 0)) == (0, 0, 0, 255)
