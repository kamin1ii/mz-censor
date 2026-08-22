"""Turning detections into proposed censor layers.

No model runs here. A fake detector stands in, because what needs testing
is the decisions made about what it says, not the model itself, and a
44 MB download has no business in a test suite.
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
)
from alice_censor.detection import Detection, DetectorUnavailable
from alice_censor.project import ImageRecord, ImageStatus, LayerType, ProjectState


class FakeDetector:
    """Answers with whatever the test told it to find."""

    def __init__(self, findings=None):
        self.findings = findings or {}
        self.seen = []
        self.deep_calls = 0

    def detect(self, image, *, threshold=0.25, deep=False):
        self.seen.append((image.size, threshold, deep))
        self.deep_calls += bool(deep)
        return self.findings.get("all", [])


def found(label, rect=(0.4, 0.4, 0.1, 0.1), score=0.8):
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


# ===== one detection becoming one layer


def test_each_class_gets_the_style_set_for_it():
    nipple = layer_for(found("nipple_f"))
    penis = layer_for(found("penis"))

    assert nipple.type == LayerType.PIXELATE
    assert penis.type == LayerType.PIXELATE
    assert penis.params["block_size"] > nipple.params["block_size"], "heavier by default"


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


def test_a_class_with_no_style_is_ignored():
    """Which is how somebody turns one off."""
    only_penis = {"penis": DEFAULT_STYLES["penis"]}

    assert layer_for(found("nipple_f"), styles=only_penis) is None
    assert layer_for(found("penis"), styles=only_penis) is not None


def test_the_rect_is_plain_floats_so_a_project_can_be_saved():
    """The model hands back numpy floats and json cannot encode those."""
    layer = layer_for(found("penis"))

    assert all(type(v) is float for v in layer.rect)
    json.dumps(list(layer.rect))


def test_every_layer_gets_its_own_id():
    ids = {layer_for(found("penis")).id for _ in range(20)}

    assert len(ids) == 20


# ===== scanning a project


def test_a_scan_proposes_layers_for_what_was_found(tmp_path):
    project = project_with(tmp_path, ["a.png", "b.png"])
    detector = FakeDetector({"all": [found("penis"), found("nipple_f")]})

    result = scan_project(project, ["a.png", "b.png"], detector=detector)

    assert result.images_with_proposals == 2
    assert result.layer_count == 4
    assert result.skipped == []


def test_an_image_with_nothing_found_is_recorded_as_skipped(tmp_path):
    project = project_with(tmp_path, ["a.png"])

    result = scan_project(project, ["a.png"], detector=FakeDetector({"all": []}))

    assert result.proposed == {}
    assert result.skipped == ["a.png"]


def test_a_missing_extracted_image_is_reported_not_crashed_on(tmp_path):
    project = project_with(tmp_path, ["a.png"])
    project.images["gone.png"] = ImageRecord()

    result = scan_project(project, ["a.png", "gone.png"],
                          detector=FakeDetector({"all": [found("penis")]}))

    assert list(result.errors) == ["gone.png"]
    assert "a.png" in result.proposed, "the rest of the scan carries on"


def test_the_deep_option_is_passed_through(tmp_path):
    project = project_with(tmp_path, ["a.png"])
    detector = FakeDetector({"all": [found("penis")]})

    scan_project(project, ["a.png"], detector=detector, deep=True)

    assert detector.deep_calls == 1


def test_the_threshold_is_passed_through(tmp_path):
    project = project_with(tmp_path, ["a.png"])
    detector = FakeDetector()

    scan_project(project, ["a.png"], detector=detector, threshold=0.6)

    assert detector.seen[0][1] == 0.6


def test_progress_is_reported_per_image(tmp_path):
    project = project_with(tmp_path, ["a.png", "b.png"])
    seen = []

    scan_project(project, ["a.png", "b.png"], detector=FakeDetector(), on_progress=seen.append)

    assert seen == ["a.png", "b.png"]


def test_a_project_with_no_extraction_folder_is_refused(tmp_path):
    with pytest.raises(DetectorUnavailable, match="extraction folder"):
        scan_project(ProjectState(), ["a.png"], detector=FakeDetector())


# ===== applying what was proposed


def test_applying_adds_the_layers_and_flags_the_image(tmp_path):
    project = project_with(tmp_path, ["a.png"])
    result = scan_project(project, ["a.png"], detector=FakeDetector({"all": [found("penis")]}))

    added = apply_scan(project, result)

    assert added == 1
    assert len(project.images["a.png"].layers) == 1
    assert project.images["a.png"].status == ImageStatus.FLAGGED


def test_work_you_did_by_hand_is_never_overwritten(tmp_path):
    """A guess must not clobber a decision."""
    project = project_with(tmp_path, ["a.png"])
    mine = layer_for(found("nipple_f"))
    mine.enabled = True
    project.images["a.png"].layers = [mine]
    result = ScanResult(proposed={"a.png": [layer_for(found("penis"))]})

    added = apply_scan(project, result)

    assert added == 0
    assert len(project.images["a.png"].layers) == 1
    assert project.images["a.png"].layers[0].enabled is True


def test_it_can_be_told_to_replace_instead(tmp_path):
    project = project_with(tmp_path, ["a.png"])
    project.images["a.png"].layers = [layer_for(found("nipple_f"))]
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
    result = scan_project(project, ["a.png"], detector=FakeDetector({"all": [found("penis")]}))

    apply_scan(project, result)

    assert all(not layer.enabled for layer in project.images["a.png"].layers)


def test_a_project_survives_a_save_after_a_scan(tmp_path):
    """The whole point of keeping the rects as plain floats."""
    project = project_with(tmp_path, ["a.png"])
    result = scan_project(project, ["a.png"], detector=FakeDetector({"all": [found("penis")]}))
    apply_scan(project, result)

    project.save(tmp_path / "p.acproj.json")

    assert ProjectState.load(tmp_path / "p.acproj.json").images["a.png"].layers
