"""The detectors, their model files, and how they are fetched.

The real models are tens of megabytes and live on the internet, so neither
is used here. A tiny ONNX graph shaped like a real one stands in, which is
enough to check the preprocessing, the decoding of what comes back and the
merging, all of which are ours rather than the models'.
"""

import hashlib
import io
import urllib.error
from dataclasses import replace

import pytest
from PIL import Image

from alice_censor import detection
from alice_censor.detection import (
    ANIME_MODEL,
    ERAX_MODEL,
    KINDS,
    Detection,
    Detector,
    DetectorUnavailable,
    detect_with,
    merge,
    overlap,
)

onnx = pytest.importorskip("onnx", reason="building a stand in model needs onnx")
np = pytest.importorskip("numpy")


@pytest.fixture(autouse=True)
def scratch_home(tmp_path, monkeypatch):
    """Never touch the real model cache, in either direction."""
    monkeypatch.setenv("ALICE_CENSOR_HOME", str(tmp_path / "home"))


def spec_for(path, labels=("nipple_f", "penis", "pussy"), threshold=0.25):
    """A model spec pointing at a stand in file."""
    return replace(
        ANIME_MODEL,
        key="stub",
        title="Stand in",
        filename=path.name,
        raw_labels=tuple(labels),
        kinds={"nipple_f": detection.NIPPLE, "penis": detection.PENIS,
               "pussy": detection.VAGINA, "nipple": detection.NIPPLE,
               "vagina": detection.VAGINA, "anus": detection.ANUS},
        threshold=threshold,
    )


def make_model(path, boxes, labels=("nipple_f", "penis", "pussy")):
    """A graph that ignores its input and returns fixed boxes.

    Shaped exactly like a real one, four plus however many classes by
    however many candidate boxes, so everything downstream is exercised.
    """
    from onnx import TensorProto, helper, numpy_helper

    rows = 4 + len(labels)
    data = np.zeros((1, rows, max(1, len(boxes))), dtype=np.float32)
    for i, (cx, cy, w, h, label, score) in enumerate(boxes):
        data[0, 0:4, i] = (cx, cy, w, h)
        data[0, 4 + labels.index(label), i] = score
    const = numpy_helper.from_array(data, name="fixed")
    graph = helper.make_graph(
        [helper.make_node("Identity", ["fixed"], ["output0"])],
        "stub",
        [helper.make_tensor_value_info("images", TensorProto.FLOAT, [1, 3, None, None])],
        [helper.make_tensor_value_info("output0", TensorProto.FLOAT, list(data.shape))],
        initializer=[const],
    )
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 13)])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(model.SerializeToString())
    return path


def picture(size=(640, 640)):
    return Image.new("RGB", size, (200, 150, 120))


def detector_for(tmp_path, boxes, labels=("nipple_f", "penis", "pussy"), threshold=0.25):
    path = make_model(tmp_path / "m.onnx", boxes, labels)
    return Detector(spec_for(path, labels, threshold), path)


# ===== what the app calls things


def test_the_two_models_map_onto_one_vocabulary():
    """Whatever a model calls a thing, the app has one name for it."""
    assert set(ANIME_MODEL.kinds.values()) <= set(KINDS)
    assert set(ERAX_MODEL.kinds.values()) <= set(KINDS)
    assert detection.PENIS in ANIME_MODEL.kinds.values()
    assert detection.ANUS in ERAX_MODEL.kinds.values()


def test_a_class_with_no_meaning_here_is_dropped():
    """EraX detects a whole scene as make_love, which is not a region."""
    assert "make_love" in ERAX_MODEL.raw_labels
    assert "make_love" not in ERAX_MODEL.kinds


# ===== decoding what a model says


def test_a_box_comes_back_as_fractions_of_the_image(tmp_path):
    # Centred, a quarter of the frame, in the model's 640 space.
    detector = detector_for(tmp_path, [(320, 320, 160, 160, "penis", 0.9)])

    found = detector.detect(picture())

    assert len(found) == 1
    assert found[0].label == detection.PENIS
    assert found[0].score == pytest.approx(0.9, abs=0.01)
    x, y, w, h = found[0].rect
    assert (x, y) == pytest.approx((0.375, 0.375), abs=0.01)
    assert (w, h) == pytest.approx((0.25, 0.25), abs=0.01)


def test_it_records_which_model_found_it(tmp_path):
    detector = detector_for(tmp_path, [(320, 320, 100, 100, "penis", 0.9)])

    assert detector.detect(picture())[0].found_by == "stub"


def test_rects_are_plain_floats_so_a_project_can_be_saved(tmp_path):
    detector = detector_for(tmp_path, [(320, 320, 100, 100, "pussy", 0.7)])

    found = detector.detect(picture())

    assert all(type(v) is float for v in found[0].rect)
    assert type(found[0].score) is float


def test_anything_under_the_threshold_is_dropped(tmp_path):
    detector = detector_for(tmp_path, [(320, 320, 100, 100, "penis", 0.3)])

    assert detector.detect(picture(), threshold=0.2)
    assert detector.detect(picture(), threshold=0.5) == []


def test_the_models_own_threshold_is_used_by_default(tmp_path):
    detector = detector_for(tmp_path, [(320, 320, 100, 100, "penis", 0.3)], threshold=0.5)

    assert detector.detect(picture()) == []
    assert detector.detect(picture(), threshold=0.1)


def test_a_non_square_image_is_padded_not_stretched(tmp_path):
    """A box in the model's frame has to land in the same place either way."""
    detector = detector_for(tmp_path, [(160, 160, 80, 80, "penis", 0.9)])

    square = detector.detect(picture((640, 640)))[0].rect
    wide = detector.detect(picture((640, 320)))[0].rect

    # Same fraction across, twice the fraction down, because the image is
    # half as tall while the padding makes up the rest.
    assert wide[0] == pytest.approx(square[0], abs=0.01)
    assert wide[1] == pytest.approx(square[1] * 2, abs=0.02)


def test_an_image_with_no_area_finds_nothing(tmp_path):
    detector = detector_for(tmp_path, [(320, 320, 100, 100, "penis", 0.9)])

    assert detector.detect(Image.new("RGB", (0, 0))) == []


def test_a_greyscale_image_is_converted_rather_than_refused(tmp_path):
    detector = detector_for(tmp_path, [(320, 320, 100, 100, "penis", 0.9)])

    assert detector.detect(Image.new("L", (640, 640), 128))


def test_the_deep_pass_looks_at_more_of_the_image(tmp_path):
    detector = detector_for(tmp_path, [(320, 320, 40, 40, "penis", 0.9)])

    shallow = detector.detect(picture(), deep=False)
    deep = detector.detect(picture(), deep=True)

    assert len(shallow) == 1
    assert len(deep) > 1, "each crop contributes its own box"


# ===== two models at once


def test_both_models_contribute(tmp_path):
    left = detector_for(tmp_path / "a", [(100, 100, 60, 60, "penis", 0.9)])
    right = detector_for(tmp_path / "b", [(500, 500, 60, 60, "nipple_f", 0.9)])

    found = detect_with([left, right], picture())

    assert {d.label for d in found} == {detection.PENIS, detection.NIPPLE}


def test_the_same_thing_seen_twice_is_reported_once(tmp_path):
    same = (320, 320, 100, 100)
    left = detector_for(tmp_path / "a", [(*same, "penis", 0.4)])
    right = detector_for(tmp_path / "b", [(*same, "penis", 0.95)])

    found = detect_with([left, right], picture())

    assert len(found) == 1
    assert found[0].score == pytest.approx(0.95, abs=0.01), "the surer one wins"


def test_each_model_can_be_given_its_own_threshold(tmp_path):
    left = detector_for(tmp_path / "a", [(100, 100, 60, 60, "penis", 0.3)])
    right = detector_for(tmp_path / "b", [(500, 500, 60, 60, "nipple_f", 0.3)])
    left.spec = replace(left.spec, key="left")
    right.spec = replace(right.spec, key="right")

    found = detect_with([left, right], picture(),
                        thresholds={"left": 0.1, "right": 0.9})

    assert {d.label for d in found} == {detection.PENIS}


# ===== merging


def test_two_boxes_on_the_same_thing_become_one():
    a = Detection("penis", 0.9, (0.2, 0.2, 0.2, 0.2))
    b = Detection("penis", 0.5, (0.21, 0.21, 0.2, 0.2))

    assert merge([a, b]) == [a], "the more confident one survives"


def test_two_boxes_on_different_things_both_survive():
    a = Detection("penis", 0.9, (0.1, 0.1, 0.1, 0.1))
    b = Detection("nipple", 0.8, (0.7, 0.7, 0.1, 0.1))

    assert len(merge([a, b])) == 2


def test_two_kinds_in_the_same_place_both_survive():
    """A penis and a vagina can genuinely overlap."""
    same = (0.3, 0.3, 0.2, 0.2)
    assert len(merge([Detection("penis", 0.9, same), Detection("vagina", 0.8, same)])) == 2


def test_boxes_that_do_not_touch_have_no_overlap():
    assert overlap((0, 0, 0.1, 0.1), (0.5, 0.5, 0.1, 0.1)) == 0.0


def test_a_box_completely_overlaps_itself():
    assert overlap((0.1, 0.1, 0.3, 0.3), (0.1, 0.1, 0.3, 0.3)) == pytest.approx(1.0)


# ===== the model files


def test_it_says_what_is_missing_when_no_model_is_there():
    assert "No detection model" in detection.unavailable_reason()


def test_making_a_detector_without_a_model_is_refused(tmp_path):
    with pytest.raises(DetectorUnavailable, match="is not at"):
        Detector(ANIME_MODEL, tmp_path / "nothing.onnx")


def test_models_live_outside_the_project():
    """One model serves every project, and the exe folder may be read only."""
    assert detection.model_path(ANIME_MODEL).parent.name == "models"
    assert "home" in str(detection.cache_dir())


def test_a_model_with_no_published_download_is_named_not_attempted():
    """The converted one has nowhere to be fetched from yet."""
    assert ERAX_MODEL.can_be_downloaded is False
    with pytest.raises(DetectorUnavailable, match="no published download"):
        detection.download_model(ERAX_MODEL)


def test_a_model_dropped_in_by_hand_is_still_used(tmp_path):
    """Which is how the converted one works before it is hosted."""
    path = detection.model_path(ERAX_MODEL)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"pretend")

    assert detection.model_is_present(ERAX_MODEL) is True


def test_a_download_that_arrives_wrong_is_discarded(monkeypatch):
    """A truncated or tampered model must not be left looking usable."""
    monkeypatch.setattr(detection.urllib.request, "urlopen",
                        lambda *a, **k: _fake_response(b"not the model"))

    with pytest.raises(DetectorUnavailable, match="checksum"):
        detection.download_model(ANIME_MODEL)

    assert not detection.model_path(ANIME_MODEL).exists()
    assert not list(detection.model_path(ANIME_MODEL).parent.glob("*.partial"))


def test_a_download_that_fails_leaves_nothing_behind(monkeypatch):
    def boom(*a, **k):
        raise urllib.error.URLError("no network")

    monkeypatch.setattr(detection.urllib.request, "urlopen", boom)

    with pytest.raises(DetectorUnavailable, match="could not download"):
        detection.download_model(ANIME_MODEL)
    assert not detection.model_path(ANIME_MODEL).exists()


def test_a_good_download_is_kept_and_progress_reported(monkeypatch):
    payload = b"pretend model bytes"
    spec = replace(ANIME_MODEL, sha256=hashlib.sha256(payload).hexdigest(),
                   size_bytes=len(payload))
    monkeypatch.setattr(detection.urllib.request, "urlopen",
                        lambda *a, **k: _fake_response(payload))
    seen = []

    path = detection.download_model(
        spec, on_progress=lambda done, total: seen.append((done, total)))

    assert path.read_bytes() == payload
    assert detection.model_is_present(spec)
    assert seen and seen[-1][0] == len(payload)


def test_nothing_is_usable_without_the_runtime(monkeypatch):
    monkeypatch.setattr(detection, "runtime_is_present", lambda: False)

    assert detection.usable_models() == []
    assert "runtime" in detection.unavailable_reason()


class _fake_response:
    def __init__(self, payload):
        self._buffer = io.BytesIO(payload)

    def read(self, size):
        return self._buffer.read(size)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False
