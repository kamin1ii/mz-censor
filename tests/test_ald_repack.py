"""Rebuilding an .ald, and what it says while it does.

Decoding is the one step here that still goes out to alice.exe, since
nothing in this package reads the formats an ALD holds. It is stubbed out
with a real image, so every step after it runs in earnest.
"""

from PIL import Image

from alice_censor import ald_repack
from alice_censor.formats.ald import AldArchive, AldEntry, write_ald
from alice_censor.manifest import parse_manifest
from alice_censor.project import CensorLayer, ImageRecord, LayerType, ProjectState

NAMES = ["cg00001.QNT", "cg00002.QNT", "cg00003.QNT"]


def _archive(tmp_path, names=NAMES):
    path = tmp_path / "gameA.ald"
    entries = [AldEntry(index=i, name=name, data=b"QNT\0" + name.encode(), timestamp=0)
               for i, name in enumerate(names)]
    write_ald(path, AldArchive(entries=entries, trailer=b""))
    return path


def _manifest(tmp_path, archive, names=NAMES):
    path = tmp_path / "manifest.txt"
    posix = str(archive).replace("\\", "/")
    lines = [f'#ALICEPACK "--src-dir={str(tmp_path).replace(chr(92), "/")}"', f'"{posix}"']
    lines += [f"{name.rsplit('.', 1)[0]}.png,QNT" for name in names]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return parse_manifest(path)


def _project(edited=()):
    project = ProjectState()
    for name in NAMES:
        path = name.rsplit(".", 1)[0] + ".png"
        layers = [CensorLayer(id="l1", type=LayerType.SOLID, rect=(0.0, 0.0, 1.0, 1.0),
                              params={"color": "#FF0000"})] if path in edited else []
        project.images[path] = ImageRecord(layers=layers)
    return project


def _stub_decode(monkeypatch):
    """Stand in for the alice.exe round trip with a real image."""
    monkeypatch.setattr(
        ald_repack, "_decode_entry_to_image",
        lambda tools, entry, work_dir: Image.new("RGBA", (8, 8), (0, 0, 255, 255)),
    )


def _repack(tmp_path, project, on_progress=None, names=NAMES):
    archive = _archive(tmp_path, names)
    return ald_repack.repack_ald(
        project, _manifest(tmp_path, archive), tools=None,
        source_archive=archive, output_archive=tmp_path / "out.ald",
        on_progress=on_progress,
    )


def test_progress_is_reported_only_for_images_that_need_work(monkeypatch, tmp_path):
    """This path used to report every name in the manifest, including the
    copies it never opened."""
    _stub_decode(monkeypatch)
    seen = []

    result = _repack(tmp_path, _project(edited={"cg00002.png"}), seen.append)

    assert [p.path for p in seen] == ["cg00002.png"]
    assert (seen[0].done, seen[0].total, seen[0].copies) == (1, 1, 2)
    assert result.rebuilt_paths == ["cg00002.png"]
    assert result.copied_count == 2


def test_progress_counts_up_across_the_run(monkeypatch, tmp_path):
    _stub_decode(monkeypatch)
    seen = []

    _repack(tmp_path, _project(edited=set(_project().images)), seen.append)

    assert [p.done for p in seen] == [1, 2, 3]
    assert {p.total for p in seen} == {3}
    assert {p.copies for p in seen} == {0}


def test_a_path_the_archive_does_not_have_is_left_out_of_the_totals(monkeypatch, tmp_path):
    """It becomes an error rather than work, so counting it would leave the
    run finishing at 1 of 2."""
    _stub_decode(monkeypatch)
    project = _project(edited={"cg00002.png", "cg00003.png"})
    seen = []

    result = _repack(tmp_path, project, seen.append, names=NAMES[:2])

    assert [p.done for p in seen] == [1]
    assert {p.total for p in seen} == {1}
    assert "cg00003.png" in result.errors
