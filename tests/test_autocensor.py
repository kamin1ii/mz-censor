"""Turning detections into proposed censor layers.

No model runs here. Fake detectors stand in, because what needs testing is
the decisions made about what they say, not the models themselves, and tens
of megabytes of download have no business in a test suite.
"""

import json

import pytest
from PIL import Image

from alice_censor.autocensor import (
    DEFAULT_STYLES,
    ScanResult,
    apply_scan,
    layer_for,
    scan_project,
    spread_across_scene_groups,
)
from alice_censor.detection import Detection, DetectorUnavailable
from alice_censor.project import ImageRecord, ImageStatus, LayerType, ProjectState


class FakeSpec:
    def __init__(self, key="fake", threshold=0.25):
        self.key = key
        self.threshold = threshold


class FakeDetector:
    """Answers with whatever the test told it to find."""

    def __init__(self, findings=None, key="fake"):
        self.findings = list(findings or [])
        self.spec = FakeSpec(key)
        self.seen = []
        self.deep_calls = 0

    def detect(self, image, *, threshold=None, deep=False):
        self.seen.append((image.size, threshold, deep))
        self.deep_calls += bool(deep)
        return list(self.findings)


def found(label="penis", rect=(0.4, 0.4, 0.1, 0.1), score=0.8):
    return Detection(label=label, score=score, rect=rect)


def project_with(tmp_path, names, size=(64, 64)):
    extract = tmp_path / "extract"
    extract.mkdir(parents=True, exist_ok=True)
    for name in names:
        Image.new("RGB", size, (120, 120, 120)).save(extract / name)
    project = ProjectState(extract_dir=str(extract))
    for name in names:
        project.images[name] = ImageRecord()
    return project


class Group:
    """Shaped like grouping.GroupInfo, which is all that is used here."""

    def __init__(self, key, members):
        self.key = key
        self.members = list(members)


# ===== one detection becoming one layer


def test_each_kind_gets_the_style_set_for_it():
    nipple = layer_for(found("nipple"))
    penis = layer_for(found("penis"))

    assert nipple.type == LayerType.PIXELATE
    assert penis.params["block_size"] > nipple.params["block_size"], "heavier by default"


def test_every_kind_the_models_know_has_a_style():
    from alice_censor import detection

    assert set(DEFAULT_STYLES) == set(detection.KINDS)


def test_a_proposed_layer_arrives_disabled():
    """A scan proposes. Nothing renders differently until you say so."""
    assert layer_for(found("penis")).enabled is False


def test_the_box_is_grown_a_little_to_cover_more_than_it_found():
    tight = layer_for(found("penis", rect=(0.4, 0.4, 0.2, 0.2)), padding=0.0)
    padded = layer_for(found("penis", rect=(0.4, 0.4, 0.2, 0.2)), padding=0.5)

    assert padded.rect[2] > tight.rect[2] and padded.rect[3] > tight.rect[3]
    assert padded.rect[0] < tight.rect[0], "grown around the middle, not off one side"


def test_growing_a_box_cannot_push_it_off_the_image():
    layer = layer_for(found("penis", rect=(0.0, 0.0, 1.0, 1.0)), padding=0.5)

    left, top, width, height = layer.rect
    assert left >= 0 and top >= 0
    assert left + width <= 1.0 and top + height <= 1.0


def test_a_kind_with_no_style_is_ignored():
    """Which is how somebody turns one off."""
    only_penis = {"penis": DEFAULT_STYLES["penis"]}

    assert layer_for(found("nipple"), styles=only_penis) is None
    assert layer_for(found("penis"), styles=only_penis) is not None


def test_the_rect_is_plain_floats_so_a_project_can_be_saved():
    """The models hand back numpy floats and json cannot encode those."""
    layer = layer_for(found("penis"))

    assert all(type(v) is float for v in layer.rect)
    json.dumps(list(layer.rect))


def test_every_layer_gets_its_own_id():
    assert len({layer_for(found("penis")).id for _ in range(20)}) == 20


# ===== scanning a project


def test_a_scan_proposes_layers_for_what_was_found(tmp_path):
    project = project_with(tmp_path, ["a.png", "b.png"])
    detector = FakeDetector([found("penis"), found("nipple", rect=(0.1, 0.1, 0.1, 0.1))])

    result = scan_project(project, ["a.png", "b.png"], detectors=[detector])

    assert result.images_with_proposals == 2
    assert result.layer_count == 4
    assert result.skipped == []


def test_two_models_are_pooled(tmp_path):
    """Neither wins everywhere, so both run and what they say is merged."""
    project = project_with(tmp_path, ["a.png"])
    older = FakeDetector([found("nipple", rect=(0.1, 0.1, 0.1, 0.1))], key="anime")
    newer = FakeDetector([found("penis", rect=(0.7, 0.7, 0.1, 0.1))], key="erax")

    result = scan_project(project, ["a.png"], detectors=[older, newer])

    assert result.layer_count == 2


def test_the_same_thing_seen_by_both_models_is_one_region(tmp_path):
    project = project_with(tmp_path, ["a.png"])
    same = (0.4, 0.4, 0.2, 0.2)
    older = FakeDetector([found("penis", rect=same, score=0.4)], key="anime")
    newer = FakeDetector([found("penis", rect=same, score=0.9)], key="erax")

    result = scan_project(project, ["a.png"], detectors=[older, newer])

    assert result.layer_count == 1


def test_an_image_with_nothing_found_is_recorded_as_skipped(tmp_path):
    project = project_with(tmp_path, ["a.png"])

    result = scan_project(project, ["a.png"], detectors=[FakeDetector()])

    assert result.proposed == {}
    assert result.skipped == ["a.png"]


def test_a_missing_extracted_image_is_reported_not_crashed_on(tmp_path):
    project = project_with(tmp_path, ["a.png"])
    project.images["gone.png"] = ImageRecord()

    result = scan_project(project, ["a.png", "gone.png"],
                          detectors=[FakeDetector([found("penis")])])

    assert list(result.errors) == ["gone.png"]
    assert "a.png" in result.proposed, "the rest of the scan carries on"


def test_the_deep_option_is_passed_through(tmp_path):
    project = project_with(tmp_path, ["a.png"])
    detector = FakeDetector([found("penis")])

    scan_project(project, ["a.png"], detectors=[detector], deep=True)

    assert detector.deep_calls == 1


def test_each_model_gets_its_own_threshold(tmp_path):
    """They do not agree on what a sensible cutoff is."""
    project = project_with(tmp_path, ["a.png"])
    older = FakeDetector(key="anime")
    newer = FakeDetector(key="erax")

    scan_project(project, ["a.png"], detectors=[older, newer],
                 thresholds={"anime": 0.3, "erax": 0.1})

    assert older.seen[0][1] == 0.3
    assert newer.seen[0][1] == 0.1


def test_progress_is_reported_per_image(tmp_path):
    project = project_with(tmp_path, ["a.png", "b.png"])
    seen = []

    scan_project(project, ["a.png", "b.png"], detectors=[FakeDetector()],
                 on_progress=seen.append)

    assert seen == ["a.png", "b.png"]


def test_a_project_with_no_extraction_folder_is_refused():
    with pytest.raises(DetectorUnavailable, match="extraction folder"):
        scan_project(ProjectState(), ["a.png"], detectors=[FakeDetector()])


# ===== keeping a scene group consistent
#
# A scene group is one CG in its variations. Each is read on its own, so a
# nipple can be found in one frame and missed in the next, which leaves a
# scene censored in patches.


def test_a_region_found_in_one_frame_reaches_the_whole_group():
    result = ScanResult(proposed={"h01.png": [layer_for(found("penis"))]},
                        skipped=["h02.png", "h03.png"])

    added = spread_across_scene_groups(
        result, [Group("h", ["h01.png", "h02.png", "h03.png"])])

    assert added == 2
    assert set(result.proposed) == {"h01.png", "h02.png", "h03.png"}
    assert result.skipped == [], "they are no longer images with nothing on them"


def test_the_pooled_regions_are_the_union_across_the_group():
    result = ScanResult(proposed={
        "h01.png": [layer_for(found("penis", rect=(0.1, 0.1, 0.1, 0.1)))],
        "h02.png": [layer_for(found("nipple", rect=(0.7, 0.2, 0.1, 0.1)))],
    })

    spread_across_scene_groups(result, [Group("h", ["h01.png", "h02.png"])])

    assert len(result.proposed["h01.png"]) == 2
    assert len(result.proposed["h02.png"]) == 2


def test_a_region_most_frames_already_have_is_not_stacked():
    """Four frames finding one nipple must not put four boxes on the two
    that missed it."""
    same = (0.4, 0.4, 0.2, 0.2)
    result = ScanResult(
        proposed={name: [layer_for(found("nipple", rect=same))]
                  for name in ("a.png", "b.png", "c.png")},
        skipped=["d.png"])

    spread_across_scene_groups(
        result, [Group("g", ["a.png", "b.png", "c.png", "d.png"])])

    assert all(len(v) == 1 for v in result.proposed.values())


def test_each_copy_gets_its_own_id():
    """Otherwise editing one region would look like editing all of them."""
    result = ScanResult(proposed={"a.png": [layer_for(found("penis"))]})

    spread_across_scene_groups(result, [Group("g", ["a.png", "b.png"])])

    ids = [layer.id for layers in result.proposed.values() for layer in layers]
    assert len(set(ids)) == len(ids)


def test_copies_are_still_disabled():
    result = ScanResult(proposed={"a.png": [layer_for(found("penis"))]})

    spread_across_scene_groups(result, [Group("g", ["a.png", "b.png"])])

    assert all(not layer.enabled
               for layers in result.proposed.values() for layer in layers)


def test_a_group_where_nothing_was_found_is_left_alone():
    result = ScanResult(skipped=["a.png", "b.png"])

    added = spread_across_scene_groups(result, [Group("g", ["a.png", "b.png"])])

    assert added == 0
    assert result.proposed == {}


def test_a_group_of_one_needs_no_spreading():
    result = ScanResult(proposed={"only.png": [layer_for(found("penis"))]})

    assert spread_across_scene_groups(result, [Group("g", ["only.png"])]) == 0


def test_only_the_images_that_were_scanned_are_touched():
    """Scanning a selection must not put regions on the rest of its group."""
    result = ScanResult(proposed={"a.png": [layer_for(found("penis"))]})

    added = spread_across_scene_groups(
        result, [Group("g", ["a.png", "b.png", "c.png"])], within={"a.png", "b.png"})

    assert added == 1
    assert "c.png" not in result.proposed


def test_different_kinds_of_region_are_kept_apart():
    """A blur and a pixelate in the same place are still two decisions."""
    same = (0.4, 0.4, 0.2, 0.2)
    result = ScanResult(proposed={
        "a.png": [layer_for(found("penis", rect=same))],
        "b.png": [layer_for(found("nipple", rect=same),
                            styles={"nipple": (LayerType.BLUR, {"radius": 8})})],
    })

    spread_across_scene_groups(result, [Group("g", ["a.png", "b.png"])])

    assert len(result.proposed["a.png"]) == 2
    assert {layer.type for layer in result.proposed["a.png"]} == {
        LayerType.PIXELATE, LayerType.BLUR}


# ===== applying what was proposed


def test_applying_adds_the_layers_and_flags_the_image(tmp_path):
    project = project_with(tmp_path, ["a.png"])
    result = scan_project(project, ["a.png"], detectors=[FakeDetector([found("penis")])])

    added = apply_scan(project, result)

    assert added == 1
    assert len(project.images["a.png"].layers) == 1
    assert project.images["a.png"].status == ImageStatus.FLAGGED


def test_work_you_did_by_hand_is_never_overwritten(tmp_path):
    """A guess must not clobber a decision."""
    project = project_with(tmp_path, ["a.png"])
    mine = layer_for(found("nipple"))
    mine.enabled = True
    project.images["a.png"].layers = [mine]
    result = ScanResult(proposed={"a.png": [layer_for(found("penis"))]})

    added = apply_scan(project, result)

    assert added == 0
    assert len(project.images["a.png"].layers) == 1
    assert project.images["a.png"].layers[0].enabled is True


def test_it_can_be_told_to_replace_instead(tmp_path):
    project = project_with(tmp_path, ["a.png"])
    project.images["a.png"].layers = [layer_for(found("nipple"))]
    result = ScanResult(proposed={"a.png": [layer_for(found("penis"))]})

    added = apply_scan(project, result, replace_existing=True)

    assert added == 1
    assert len(project.images["a.png"].layers) == 1


def test_a_status_you_already_set_is_left_alone(tmp_path):
    project = project_with(tmp_path, ["a.png"])
    project.images["a.png"].status = ImageStatus.CLEAN
    result = ScanResult(proposed={"a.png": [layer_for(found("penis"))]})

    apply_scan(project, result)

    assert project.images["a.png"].status == ImageStatus.CLEAN


def test_applied_layers_still_arrive_disabled(tmp_path):
    project = project_with(tmp_path, ["a.png"])
    result = scan_project(project, ["a.png"], detectors=[FakeDetector([found("penis")])])

    apply_scan(project, result)

    assert all(not layer.enabled for layer in project.images["a.png"].layers)


def test_a_project_survives_a_save_after_a_scan(tmp_path):
    """The whole point of keeping the rects as plain floats."""
    project = project_with(tmp_path, ["a.png"])
    result = scan_project(project, ["a.png"], detectors=[FakeDetector([found("penis")])])
    apply_scan(project, result)

    project.save(tmp_path / "p.acproj.json")

    assert ProjectState.load(tmp_path / "p.acproj.json").images["a.png"].layers


def test_counts_may_differ_but_coverage_must_not():
    """An image whose own region happens to cover two pooled ones ends up
    with one box fewer, which is fine. A region nothing covers is not.

    Seen on a real project: one group came out with 8 regions on some
    frames and 9 on others, and every frame was still fully covered.
    """
    from alice_censor.autocensor import SAME_REGION
    from alice_censor.detection import overlap

    wide = layer_for(found("nipple", rect=(0.30, 0.30, 0.30, 0.30)))
    left = layer_for(found("nipple", rect=(0.32, 0.32, 0.10, 0.10)))
    right = layer_for(found("nipple", rect=(0.70, 0.70, 0.10, 0.10)))
    result = ScanResult(proposed={"a.png": [wide], "b.png": [left, right]})

    spread_across_scene_groups(result, [Group("g", ["a.png", "b.png"])])

    pooled = [wide, left, right]
    for member, layers in result.proposed.items():
        for wanted in pooled:
            assert any(l.type == wanted.type and overlap(l.rect, wanted.rect) > SAME_REGION
                       for l in layers), f"{member} has nothing over {wanted.rect}"
