"""Rebuilding an .afa without alice-tools.

The archive here is a real one in miniature, built with the same helper the
AFA tests use and holding real QNT images made by the QNT encoder, so a
repack goes through every step it would in earnest.
"""

import pytest
from PIL import Image

from alice_censor.afa_repack import (
    AfaRepackError,
    repack_afa,
    repack_afa_in_place,
    verify_afa,
)
from alice_censor.formats import qnt
from alice_censor.formats.afa import AfaReader
from alice_censor.manifest import parse_manifest
from alice_censor.project import CensorLayer, ImageRecord, LayerType, ProjectState

from .test_afa import make_afa

NAMES = ["first.qnt", "second.qnt", "third.qnt"]


def _image(colour):
    return Image.new("RGBA", (16, 12), colour)


def _archive(tmp_path, names=NAMES, extra=()):
    """An archive of real QNTs, plus anything else asked for."""
    files = [(name, qnt.encode(_image((10 + i * 40, 60, 90, 255))))
             for i, name in enumerate(names)]
    files += list(extra)
    path = tmp_path / "Game.afa"
    path.write_bytes(make_afa(files))
    return path


def _manifest(tmp_path, archive, names=NAMES):
    path = tmp_path / "manifest.txt"
    posix = str(archive).replace("\\", "/")
    lines = [f'#ALICEPACK "--src-dir={str(tmp_path).replace(chr(92), "/")}"', f'"{posix}"']
    lines += [f"{name.rsplit('.', 1)[0]}.png,qnt" for name in names]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return parse_manifest(path)


def _solid(colour="#FF0000"):
    return CensorLayer(id="l1", type=LayerType.SOLID, rect=(0.0, 0.0, 1.0, 1.0),
                       params={"color": colour})


def _project(edited=()):
    project = ProjectState()
    for name in NAMES:
        path = name.rsplit(".", 1)[0] + ".png"
        project.images[path] = ImageRecord(layers=[_solid()] if path in edited else [])
    return project


def test_rebuilding_with_nothing_edited_reproduces_the_archive(tmp_path):
    """The property that makes this worth having over ar pack."""
    archive = _archive(tmp_path)
    out = tmp_path / "out.afa"

    result = repack_afa(_project(), _manifest(tmp_path, archive),
                        source_archive=archive, output_archive=out)

    assert result.copied_count == 3
    assert result.rebuilt_paths == []
    assert out.read_bytes() == archive.read_bytes()


def test_an_edited_image_is_re_encoded_and_the_rest_are_untouched(tmp_path):
    archive = _archive(tmp_path)
    out = tmp_path / "out.afa"
    before = {e.name: d for e, d in _entries(archive)}

    result = repack_afa(_project(edited={"second.png"}),
                        _manifest(tmp_path, archive),
                        source_archive=archive, output_archive=out)

    assert result.rebuilt_paths == ["second.png"]
    assert result.copied_count == 2
    assert result.errors == {}
    after = {e.name: d for e, d in _entries(out)}
    assert after["first.qnt"] == before["first.qnt"]
    assert after["third.qnt"] == before["third.qnt"]
    assert after["second.qnt"] != before["second.qnt"]
    assert qnt.decode(after["second.qnt"]).getpixel((8, 6)) == (255, 0, 0, 255)


def test_the_order_the_game_indexes_into_is_kept(tmp_path):
    archive = _archive(tmp_path)
    out = tmp_path / "out.afa"

    repack_afa(_project(edited={"first.png"}), _manifest(tmp_path, archive),
               source_archive=archive, output_archive=out)

    with AfaReader(out) as ar:
        assert [e.name for e in ar] == NAMES


def test_a_disabled_layer_counts_as_no_layers(tmp_path):
    archive = _archive(tmp_path)
    out = tmp_path / "out.afa"
    project = _project(edited={"first.png"})
    project.images["first.png"].layers[0].enabled = False

    result = repack_afa(project, _manifest(tmp_path, archive),
                        source_archive=archive, output_archive=out)

    assert result.rebuilt_paths == []
    assert out.read_bytes() == archive.read_bytes()


def test_a_format_this_build_cannot_decode_falls_back_to_the_extracted_png(tmp_path):
    """An edited DCF still needs pixels, and the gallery already has them."""
    archive = _archive(tmp_path, extra=[("fourth.dcf", b"dcf " + b"x" * 40)])
    extract = tmp_path / "extract"
    extract.mkdir()
    _image((7, 8, 9, 255)).save(extract / "fourth.png")
    manifest = _manifest(tmp_path, archive, NAMES + ["fourth.dcf"])

    project = _project(edited=set())
    project.images["fourth.png"] = ImageRecord(layers=[_solid("#00FF00")])
    result = repack_afa(project, manifest, source_archive=archive,
                        output_archive=tmp_path / "out.afa", extract_dir=extract)

    assert result.rebuilt_paths == ["fourth.png"]
    assert result.converted_formats == {"fourth.png": "dcf"}
    assert result.errors == {}
    after = {e.name: d for e, d in _entries(tmp_path / "out.afa")}
    assert qnt.decode(after["fourth.dcf"]).getpixel((8, 6)) == (0, 255, 0, 255)


def test_an_undecodable_image_with_no_fallback_is_reported_not_dropped(tmp_path):
    """One bad image must not cost the other three thousand."""
    archive = _archive(tmp_path, extra=[("fourth.dcf", b"dcf " + b"x" * 40)])
    manifest = _manifest(tmp_path, archive, NAMES + ["fourth.dcf"])
    project = _project()
    project.images["fourth.png"] = ImageRecord(layers=[_solid()])

    result = repack_afa(project, manifest, source_archive=archive,
                        output_archive=tmp_path / "out.afa")

    assert "fourth.png" in result.errors
    assert "cannot decode" in result.errors["fourth.png"]
    after = {e.name: d for e, d in _entries(tmp_path / "out.afa")}
    assert len(after) == 4, "the entry is still there"
    assert after["fourth.dcf"] == b"dcf " + b"x" * 40, "and carries its original bytes"


def test_an_image_the_project_does_not_know_about_is_copied(tmp_path):
    archive = _archive(tmp_path, extra=[("stray.qnt", qnt.encode(_image((1, 2, 3, 255))))])

    result = repack_afa(_project(), _manifest(tmp_path, archive),
                        source_archive=archive, output_archive=tmp_path / "out.afa")

    assert result.copied_count == 4
    assert result.errors == {}


def test_a_source_that_is_not_an_archive_is_refused_up_front(tmp_path):
    """Named like one, so it gets as far as being opened."""
    junk = tmp_path / "notes.afa"
    junk.write_bytes(b"not an archive" * 8)

    with pytest.raises(AfaRepackError, match="could not read"):
        repack_afa(_project(), _manifest(tmp_path, junk),
                   source_archive=junk, output_archive=tmp_path / "out.afa")


# ===== in place, and reading back what was written


def test_rebuilding_in_place_makes_a_backup_and_reads_from_it(tmp_path):
    archive = _archive(tmp_path)
    original = archive.read_bytes()
    manifest = _manifest(tmp_path, archive)

    repack_afa_in_place(_project(edited={"first.png"}), manifest)
    repack_afa_in_place(_project(edited={"first.png"}), manifest)

    backup = tmp_path / "Game.afa.orig-backup"
    assert backup.read_bytes() == original, "the backup stays pristine"
    after = {e.name: d for e, d in _entries(archive)}
    # Twice over the same source gives the same answer. Reading from the
    # archive being written would censor an already censored image.
    assert qnt.decode(after["first.qnt"]).getpixel((8, 6)) == (255, 0, 0, 255)


def test_a_clean_rebuild_verifies(tmp_path):
    archive = _archive(tmp_path)
    out = tmp_path / "out.afa"
    result = repack_afa(_project(edited={"second.png"}), _manifest(tmp_path, archive),
                        source_archive=archive, output_archive=out)

    assert verify_afa(out, result) == []


def test_verify_notices_a_file_that_did_not_survive(tmp_path):
    archive = _archive(tmp_path)
    out = tmp_path / "out.afa"
    result = repack_afa(_project(), _manifest(tmp_path, archive),
                        source_archive=archive, output_archive=out)
    result.expected_names.append("vanished.qnt")

    problems = verify_afa(out, result)

    assert any("missing" in p for p in problems)


def test_verify_notices_the_order_changing(tmp_path):
    archive = _archive(tmp_path)
    out = tmp_path / "out.afa"
    result = repack_afa(_project(), _manifest(tmp_path, archive),
                        source_archive=archive, output_archive=out)
    result.expected_names.reverse()

    assert any("different order" in p for p in verify_afa(out, result))


def test_verify_reports_an_archive_that_will_not_read_back(tmp_path):
    broken = tmp_path / "broken.afa"
    broken.write_bytes(b"AFAH" + bytes(60))
    result = type("R", (), {"expected_names": []})()

    assert verify_afa(broken, result) == ["the rebuilt archive does not read back, "
                                          "broken.afa has an AlicArch label this cannot read"]


def _entries(path):
    with AfaReader(path) as ar:
        return [(e, ar.read(e)) for e in ar.entries]


# ===== doing it on several threads
#
# Encoding is where nearly all the time goes, so it is spread over threads.
# The archive that comes out must not depend on how many.


@pytest.mark.parametrize("workers", [1, 2, 8])
def test_the_result_does_not_depend_on_how_many_threads_ran_it(tmp_path, workers):
    archive = _archive(tmp_path, names=[f"cg{i:03}.qnt" for i in range(9)])
    manifest = _manifest(tmp_path, archive, [f"cg{i:03}.qnt" for i in range(9)])
    project = ProjectState()
    for i in range(9):
        path = f"cg{i:03}.png"
        project.images[path] = ImageRecord(layers=[_solid()] if i % 2 else [])

    out = tmp_path / f"out{workers}.afa"
    result = repack_afa(project, manifest, source_archive=archive,
                        output_archive=out, workers=workers)

    assert result.rebuilt_paths == [f"cg{i:03}.png" for i in (1, 3, 5, 7)]
    assert result.copied_count == 5
    assert result.errors == {}
    if workers != 1:
        serial = tmp_path / "serial.afa"
        repack_afa(project, manifest, source_archive=archive,
                   output_archive=serial, workers=1)
        assert out.read_bytes() == serial.read_bytes()


def test_one_image_failing_does_not_take_the_batch_with_it(tmp_path):
    """A thread raising must land as one error, not as a dead repack."""
    names = [f"cg{i:03}.qnt" for i in range(4)]
    archive = _archive(tmp_path, names=names,
                       extra=[("broken.dcf", b"dcf " + b"x" * 30)])
    manifest = _manifest(tmp_path, archive, names + ["broken.dcf"])
    project = ProjectState()
    for name in names:
        project.images[name.rsplit(".", 1)[0] + ".png"] = ImageRecord(layers=[_solid()])
    project.images["broken.png"] = ImageRecord(layers=[_solid()])

    result = repack_afa(project, manifest, source_archive=archive,
                        output_archive=tmp_path / "out.afa", workers=4)

    assert list(result.errors) == ["broken.png"]
    assert len(result.rebuilt_paths) == 4, "the other four still got done"


def test_progress_is_reported_only_for_images_that_need_work(tmp_path):
    """Copies are the overwhelming majority and cost nothing, so a line
    each would bury the images actually being worked on."""
    archive = _archive(tmp_path)
    seen = []

    repack_afa(_project(edited={"second.png"}), _manifest(tmp_path, archive),
               source_archive=archive, output_archive=tmp_path / "out.afa",
               on_progress=seen.append)

    assert [p.path for p in seen] == ["second.png"]
    assert (seen[0].done, seen[0].total, seen[0].copies) == (1, 1, 2)


def test_progress_counts_up_across_the_run(tmp_path):
    archive = _archive(tmp_path)
    seen = []

    repack_afa(_project(edited=set(NAMES and {"first.png", "second.png", "third.png"})),
               _manifest(tmp_path, archive),
               source_archive=archive, output_archive=tmp_path / "out.afa",
               on_progress=seen.append, workers=2)

    assert [p.done for p in seen] == [1, 2, 3]
    assert {p.total for p in seen} == {3}
    assert {p.copies for p in seen} == {0}
