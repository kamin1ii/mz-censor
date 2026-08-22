"""The Advanced menu action that runs a detection pass.

No model is loaded. What matters here is the wiring: that it refuses
politely when it cannot run, that it asks before downloading 44 MB, that
scope and settings reach the scan, and that nothing is applied without
being agreed to.
"""

import pytest
from PySide6.QtWidgets import QDialog, QMessageBox

from alice_censor import detection
from alice_censor.autocensor import ScanResult, layer_for
from alice_censor.detection import Detection
from alice_censor.gui.detect_dialog import DetectDialog
from alice_censor.gui.main_window import MainWindow
from alice_censor.manifest import Manifest, ManifestEntry, ManifestFormat, ManifestOptions
from alice_censor.project import ImageRecord, ImageStatus, LayerType, ProjectState
from alice_censor.session import OpenProject


def found(label="penis", rect=(0.4, 0.4, 0.1, 0.1)):
    return Detection(label=label, score=0.8, rect=rect)


def session_for(tmp_path, statuses):
    manifest = Manifest(
        manifest_path=tmp_path / "manifest.txt",
        magic="#ALICEPACK",
        options=ManifestOptions(src_dir=str(tmp_path)),
        archive_line=str(tmp_path / "Game.afa"),
        archive_format=ManifestFormat.AFA,
        entries=[ManifestEntry(path=name, dst_format="qnt") for name in statuses],
    )
    project = ProjectState(extract_dir=str(tmp_path / "extract"),
                           project_file=str(tmp_path / "p.acproj.json"))
    for name, status in statuses.items():
        project.images[name] = ImageRecord(status=status)
    return OpenProject(project=project, manifest=manifest, tools=None)


def window_with(qapp, tmp_path, statuses=None, monkeypatch=None):
    statuses = statuses or {"a.png": ImageStatus.UNREVIEWED}
    window = MainWindow()
    window.session = session_for(tmp_path, statuses)
    return window


# ===== refusing politely


def test_the_action_is_off_with_no_project_open(qapp):
    assert MainWindow()._detect_action.isEnabled() is False


def test_a_build_with_no_runtime_says_so_rather_than_failing(qapp, tmp_path, monkeypatch):
    window = window_with(qapp, tmp_path)
    monkeypatch.setattr(detection, "runtime_is_present", lambda: False)
    told = []
    monkeypatch.setattr(QMessageBox, "information",
                        staticmethod(lambda *a, **k: told.append(a)))
    started = []
    monkeypatch.setattr(MainWindow, "_run_worker", lambda self, *a, **k: started.append(a))

    window.detect_regions()

    assert told and "does not include" in told[0][2]
    assert not started


def test_it_asks_before_downloading_the_model(qapp, tmp_path, monkeypatch):
    window = window_with(qapp, tmp_path)
    monkeypatch.setattr(detection, "runtime_is_present", lambda: True)
    monkeypatch.setattr(detection, "model_is_present", lambda: False)
    asked = []
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: asked.append(a) or QMessageBox.No))
    fetched = []
    monkeypatch.setattr(detection, "download_model", lambda *a, **k: fetched.append(1))

    window.detect_regions()

    assert asked, "44 MB is not something to fetch silently"
    assert "44 MB" in asked[0][2]
    assert not fetched, "declining means not downloading"


def test_a_failed_download_is_reported(qapp, tmp_path, monkeypatch):
    window = window_with(qapp, tmp_path)
    monkeypatch.setattr(detection, "runtime_is_present", lambda: True)
    monkeypatch.setattr(detection, "model_is_present", lambda: False)
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))

    def boom(*a, **k):
        raise detection.DetectorUnavailable("no network")

    monkeypatch.setattr(detection, "download_model", boom)
    told = []
    monkeypatch.setattr(QMessageBox, "critical", staticmethod(lambda *a, **k: told.append(a)))

    window.detect_regions()

    assert told and "no network" in told[0][2]


# ===== what reaches the scan


def _ready(monkeypatch):
    monkeypatch.setattr(detection, "runtime_is_present", lambda: True)
    monkeypatch.setattr(detection, "model_is_present", lambda: True)


def test_the_scope_chosen_decides_which_images_are_looked_at(qapp, tmp_path, monkeypatch):
    _ready(monkeypatch)
    window = window_with(qapp, tmp_path, {
        "new.png": ImageStatus.UNREVIEWED,
        "flagged.png": ImageStatus.FLAGGED,
        "done.png": ImageStatus.CLEAN,
    })

    every = window._paths_in_scope(["new.png", "flagged.png", "done.png"], "all")
    unreviewed = window._paths_in_scope(["new.png", "flagged.png", "done.png"], "unreviewed")
    flagged = window._paths_in_scope(["new.png", "flagged.png", "done.png"], "flagged")

    assert len(every) == 3
    assert unreviewed == ["new.png"]
    assert flagged == ["flagged.png"]


def test_unticking_every_class_stops_before_scanning(qapp, tmp_path, monkeypatch):
    _ready(monkeypatch)
    window = window_with(qapp, tmp_path)
    monkeypatch.setattr(DetectDialog, "exec", lambda self: QDialog.Accepted)
    monkeypatch.setattr(DetectDialog, "settings", lambda self: {
        "scope": "all", "threshold": 0.25, "padding": 0.15, "deep": False, "styles": {}})
    told = []
    monkeypatch.setattr(QMessageBox, "information",
                        staticmethod(lambda *a, **k: told.append(a)))
    started = []
    monkeypatch.setattr(MainWindow, "_run_worker", lambda self, *a, **k: started.append(a))

    window.detect_regions()

    assert told and "nothing to detect" in told[0][2]
    assert not started


def test_cancelling_the_dialog_scans_nothing(qapp, tmp_path, monkeypatch):
    _ready(monkeypatch)
    window = window_with(qapp, tmp_path)
    monkeypatch.setattr(DetectDialog, "exec", lambda self: 0)
    started = []
    monkeypatch.setattr(MainWindow, "_run_worker", lambda self, *a, **k: started.append(a))

    window.detect_regions()

    assert not started


# ===== what happens with the results


def test_nothing_found_says_so_and_changes_nothing(qapp, tmp_path, monkeypatch):
    window = window_with(qapp, tmp_path)
    told = []
    monkeypatch.setattr(QMessageBox, "information",
                        staticmethod(lambda *a, **k: told.append(a)))

    window._on_detect_done(ScanResult(skipped=["a.png"]))

    assert told and "found nothing" in told[0][2]
    assert window.session.project.images["a.png"].layers == []


def test_the_results_are_not_applied_without_agreement(qapp, tmp_path, monkeypatch):
    window = window_with(qapp, tmp_path)
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.No))
    result = ScanResult(proposed={"a.png": [layer_for(found())]})

    window._on_detect_done(result)

    assert window.session.project.images["a.png"].layers == []


def test_agreeing_adds_them_switched_off(qapp, tmp_path, monkeypatch):
    window = window_with(qapp, tmp_path)
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(MainWindow, "_autosave", lambda self, *a, **k: True)
    monkeypatch.setattr(MainWindow, "_refresh_summary", lambda self: None)
    monkeypatch.setattr(MainWindow, "_refresh_gallery", lambda self, result: None)
    result = ScanResult(proposed={"a.png": [layer_for(found()), layer_for(found("pussy"))]})

    window._on_detect_done(result)

    layers = window.session.project.images["a.png"].layers
    assert len(layers) == 2
    assert all(not layer.enabled for layer in layers), "proposed, not applied"
    assert window.session.project.images["a.png"].status == ImageStatus.FLAGGED


def test_the_action_comes_back_after_a_run(qapp, tmp_path, monkeypatch):
    """Otherwise a second scan is impossible without reopening the project."""
    window = window_with(qapp, tmp_path)
    window._detect_action.setEnabled(False)
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))

    window._on_detect_done(ScanResult(skipped=["a.png"]))

    assert window._detect_action.isEnabled()


# ===== the dialog itself


def test_the_dialog_offers_every_class_and_defaults_them_on(qapp):
    dialog = DetectDialog(image_count=10)

    assert set(dialog.styles()) == {"nipple_f", "penis", "pussy"}


def test_unticking_a_class_removes_it(qapp):
    dialog = DetectDialog(image_count=10)

    dialog._class_rows["nipple_f"][0].setChecked(False)

    assert "nipple_f" not in dialog.styles()


def test_the_strength_field_follows_the_censor_type(qapp):
    """Pixelate and blur do not measure strength in the same units."""
    dialog = DetectDialog(image_count=10)
    kind = dialog._class_rows["penis"][1]

    kind.setCurrentIndex(kind.findData(LayerType.PIXELATE))
    assert "block_size" in dialog.styles()["penis"][1]

    kind.setCurrentIndex(kind.findData(LayerType.BLUR))
    assert "radius" in dialog.styles()["penis"][1]

    kind.setCurrentIndex(kind.findData(LayerType.SOLID))
    assert "color" in dialog.styles()["penis"][1]


def test_the_dialog_reports_what_was_chosen(qapp):
    dialog = DetectDialog(image_count=10)
    dialog.threshold_spin.setValue(0.4)
    dialog.padding_spin.setValue(30)
    dialog.deep_checkbox.setChecked(True)

    settings = dialog.settings()

    assert settings["threshold"] == pytest.approx(0.4)
    assert settings["padding"] == pytest.approx(0.3)
    assert settings["deep"] is True
