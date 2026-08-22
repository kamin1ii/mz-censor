"""The detector, its model file, and how it fetches one.

The real model is 44 MB and lives on the internet, so neither is used here.
A tiny ONNX graph shaped like the real one stands in for it, which is
enough to check the preprocessing, the decoding of what comes back and the
merging, all of which are ours rather than the model's.
"""

import hashlib
import io
import urllib.error

import pytest
from PIL import Image

from alice_censor import detection
from alice_censor.detection import (
    LABELS,
    Detection,
    Detector,
    DetectorUnavailable,
    _merge,
    _overlap,
    _tiles,
)

onnx = pytest.importorskip("onnx", reason="building a stand in model needs onnx")
np = pytest.importorskip("numpy")


@pytest.fixture(autouse=True)
def scratch_home(tmp_path, monkeypatch):
    """Never touch the real model cache, in either direction."""
    monkeypatch.setenv("ALICE_CENSOR_HOME", str(tmp_path / "home"))


def make_model(path, boxes):
    """A graph that ignores its input and returns fixed boxes.

    Shaped exactly like the real one, 4 + 3 rows by however many candidate
    boxes, so everything downstream of the model is exercised for real.
    """
    from onnx import TensorProto, helper, numpy_helper

    rows = 4 + len(LABELS)
    data = np.zeros((1, rows, max(1, len(boxes))), dtype=np.float32)
    for i, (cx, cy, w, h, label, score) in enumerate(boxes):
        data[0, 0:4, i] = (cx, cy, w, h)
        data[0, 4 + LABELS.index(label), i] = score
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


# ===== decoding what the model says


def test_a_box_comes_back_as_fractions_of_the_image(tmp_path):
    # Centred, a quarter of the frame, in the model's 640 space.
    model = make_model(tmp_path / "m.onnx", [(320, 320, 160, 160, "penis", 0.9)])

    found = Detector(model).detect(picture())

    assert len(found) == 1
    assert found[0].label == "penis"
    assert found[0].score == pytest.approx(0.9, abs=0.01)
    x, y, w, h = found[0].rect
    assert (x, y) == pytest.approx((0.375, 0.375), abs=0.01)
    assert (w, h) == pytest.approx((0.25, 0.25), abs=0.01)


def test_rects_are_plain_floats_so_a_project_can_be_saved(tmp_path):
    model = make_model(tmp_path / "m.onnx", [(320, 320, 100, 100, "pussy", 0.7)])

    found = Detector(model).detect(picture())

    assert all(type(v) is float for v in found[0].rect)
    assert type(found[0].score) is float


def test_the_highest_scoring_class_wins(tmp_path):
    model = make_model(tmp_path / "m.onnx", [(320, 320, 100, 100, "nipple_f", 0.9)])

    assert Detector(model).detect(picture())[0].label == "nipple_f"


def test_anything_under_the_threshold_is_dropped(tmp_path):
    model = make_model(tmp_path / "m.onnx", [(320, 320, 100, 100, "penis", 0.3)])
    detector = Detector(model)

    assert detector.detect(picture(), threshold=0.2)
    assert detector.detect(picture(), threshold=0.5) == []


def test_a_non_square_image_is_padded_not_stretched(tmp_path):
    """A box in the model's frame has to land in the same place either way."""
    model = make_model(tmp_path / "m.onnx", [(160, 160, 80, 80, "penis", 0.9)])
    detector = Detector(model)

    square = detector.detect(picture((640, 640)))[0].rect
    wide = detector.detect(picture((640, 320)))[0].rect

    # Same fraction across, and twice the fraction down, because the image
    # is half as tall while the padding makes up the rest.
    assert wide[0] == pytest.approx(square[0], abs=0.01)
    assert wide[1] == pytest.approx(square[1] * 2, abs=0.02)


def test_an_image_with_no_area_finds_nothing(tmp_path):
    model = make_model(tmp_path / "m.onnx", [(320, 320, 100, 100, "penis", 0.9)])

    assert Detector(model).detect(Image.new("RGB", (0, 0))) == []


def test_a_greyscale_image_is_converted_rather_than_refused(tmp_path):
    model = make_model(tmp_path / "m.onnx", [(320, 320, 100, 100, "penis", 0.9)])

    assert Detector(model).detect(Image.new("L", (640, 640), 128))


def test_the_deep_pass_looks_at_more_of_the_image(tmp_path):
    """Five inferences rather than one, so the same fixed box comes back
    from each crop and gets merged down to the ones in different places."""
    model = make_model(tmp_path / "m.onnx", [(320, 320, 40, 40, "penis", 0.9)])
    detector = Detector(model)

    shallow = detector.detect(picture(), deep=False)
    deep = detector.detect(picture(), deep=True)

    assert len(shallow) == 1
    assert len(deep) > 1, "each crop contributes its own box"


# ===== merging


def test_two_boxes_on_the_same_thing_become_one():
    a = Detection("penis", 0.9, (0.2, 0.2, 0.2, 0.2))
    b = Detection("penis", 0.5, (0.21, 0.21, 0.2, 0.2))

    merged = _merge([a, b])

    assert merged == [a], "the more confident one survives"


def test_two_boxes_on_different_things_both_survive():
    a = Detection("penis", 0.9, (0.1, 0.1, 0.1, 0.1))
    b = Detection("nipple_f", 0.8, (0.7, 0.7, 0.1, 0.1))

    assert len(_merge([a, b])) == 2


def test_boxes_that_do_not_touch_have_no_overlap():
    assert _overlap((0, 0, 0.1, 0.1), (0.5, 0.5, 0.1, 0.1)) == 0.0


def test_a_box_completely_overlaps_itself():
    assert _overlap((0.1, 0.1, 0.3, 0.3), (0.1, 0.1, 0.3, 0.3)) == pytest.approx(1.0)


def test_tiles_cover_the_whole_image_and_overlap():
    boxes = list(_tiles(100, 100))

    assert len(boxes) == 4
    assert min(b[0] for b in boxes) == 0 and min(b[1] for b in boxes) == 0
    assert max(b[2] for b in boxes) == 100 and max(b[3] for b in boxes) == 100
    assert boxes[0][2] > 50, "wider than a quarter, so the seam is covered"


# ===== the model file


def test_it_says_what_is_missing_when_the_model_is_not_there():
    assert "not been downloaded" in detection.unavailable_reason()


def test_making_a_detector_without_a_model_is_refused(tmp_path):
    with pytest.raises(DetectorUnavailable, match="not at"):
        Detector(tmp_path / "nothing.onnx")


def test_the_model_lives_outside_the_project(tmp_path):
    """One model serves every project, and the exe folder may be read only."""
    assert detection.model_path().parent.name == "models"
    assert "home" in str(detection.cache_dir())


def test_a_download_that_arrives_wrong_is_discarded(tmp_path, monkeypatch):
    """A truncated or tampered model must not be left looking usable."""
    monkeypatch.setattr(detection.urllib.request, "urlopen",
                        lambda *a, **k: _fake_response(b"not the model"))

    with pytest.raises(DetectorUnavailable, match="checksum"):
        detection.download_model()

    assert not detection.model_path().exists()
    assert not list(detection.model_path().parent.glob("*.partial"))


def test_a_download_that_fails_leaves_nothing_behind(monkeypatch):
    def boom(*a, **k):
        raise urllib.error.URLError("no network")

    monkeypatch.setattr(detection.urllib.request, "urlopen", boom)

    with pytest.raises(DetectorUnavailable, match="could not download"):
        detection.download_model()
    assert not detection.model_path().exists()


def test_a_good_download_is_kept_and_progress_reported(monkeypatch):
    payload = b"pretend model bytes"
    monkeypatch.setattr(detection, "MODEL_SHA256", hashlib.sha256(payload).hexdigest())
    monkeypatch.setattr(detection, "MODEL_BYTES", len(payload))
    monkeypatch.setattr(detection.urllib.request, "urlopen",
                        lambda *a, **k: _fake_response(payload))
    seen = []

    path = detection.download_model(on_progress=lambda done, total: seen.append((done, total)))

    assert path.read_bytes() == payload
    assert detection.model_is_present()
    assert seen and seen[-1][0] == len(payload)


class _fake_response:
    def __init__(self, payload):
        self._buffer = io.BytesIO(payload)

    def read(self, size):
        return self._buffer.read(size)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False
