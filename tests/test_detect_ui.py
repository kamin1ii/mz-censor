"""The Advanced menu action that runs a detection pass.

No model is loaded. What matters here is the wiring: that it refuses
politely when it cannot run, that it asks before downloading tens of
megabytes, that scope and settings reach the scan, and that nothing is
applied without being agreed to.
"""

from dataclasses import replace

import pytest
from PySide6.QtWidgets import QDialog, QMessageBox

from alice_censor import detection
from alice_censor.autocensor import ScanResult, layer_for
from alice_censor.detection import ANIME_MODEL, ERAX_MODEL, Detection
from alice_censor.gui.detect_dialog import DetectDialog
from alice_censor.gui.main_window import MainWindow
from alice_censor.manifest import Manifest, ManifestEntry, ManifestFormat, ManifestOptions
from alice_censor.project import ImageRecord, ImageStatus, LayerType, ProjectState
from alice_censor.session import OpenProject

BOTH = [ANIME_MODEL, replace(ERAX_MODEL, sha256="x" * 64, url="https://example/m.onnx",
                             size_bytes=1000)]


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


def window_with(tmp_path, statuses=None):
    window = MainWindow()
    window.session = session_for(tmp_path, statuses or {"a.png": ImageStatus.UNREVIEWED})
    return window


def ready(monkeypatch, models=None):
    monkeypatch.setattr(detection, "runtime_is_present", lambda: True)
    monkeypatch.setattr(detection, "usable_models", lambda: list(models or [ANIME_MODEL]))
    monkeypatch.setattr(detection, "missing_models", lambda: [])


# ===== refusing politely


def test_the_action_is_off_with_no_project_open(qapp):
    assert MainWindow()._detect_action.isEnabled() is False


def test_a_build_with_no_runtime_says_so_rather_than_failing(qapp, tmp_path, monkeypatch):
    window = window_with(tmp_path)
    monkeypatch.setattr(detection, "runtime_is_present", lambda: False)
    told = []
    monkeypatch.setattr(QMessageBox, "information",
                        staticmethod(lambda *a, **k: told.append(a)))
    started = []
    monkeypatch.setattr(MainWindow, "_run_worker", lambda self, *a, **k: started.append(a))

    window.detect_regions()

    assert told and "does not include" in told[0][2]
    assert not started


def test_it_asks_before_downloading_a_model(qapp, tmp_path, monkeypatch):
    window = window_with(tmp_path)
    monkeypatch.setattr(detection, "runtime_is_present", lambda: True)
    monkeypatch.setattr(detection, "usable_models", lambda: [])
    monkeypatch.setattr(detection, "missing_models", lambda: [ANIME_MODEL])
    asked = []
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: asked.append(a) or QMessageBox.No))
    fetched = []
    monkeypatch.setattr(detection, "download_model",
                        lambda *a, **k: fetched.append(1))

    window.detect_regions()

    assert asked, "tens of megabytes is not something to fetch silently"
    assert "MB" in asked[0][2]
    assert not fetched, "declining means not downloading"


def test_the_licence_of_each_model_is_shown_before_fetching(qapp, tmp_path, monkeypatch):
    window = window_with(tmp_path)
    monkeypatch.setattr(detection, "runtime_is_present", lambda: True)
    monkeypatch.setattr(detection, "usable_models", lambda: [])
    monkeypatch.setattr(detection, "missing_models", lambda: [ANIME_MODEL])
    asked = []
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: asked.append(a) or QMessageBox.No))

    window.detect_regions()

    assert "MIT" in asked[0][2]


def test_a_failed_download_is_reported(qapp, tmp_path, monkeypatch):
    window = window_with(tmp_path)
    monkeypatch.setattr(detection, "runtime_is_present", lambda: True)
    monkeypatch.setattr(detection, "usable_models", lambda: [])
    monkeypatch.setattr(detection, "missing_models", lambda: [ANIME_MODEL])
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))

    def boom(*a, **k):
        raise detection.DetectorUnavailable("no network")

    monkeypatch.setattr(detection, "download_model", boom)
    told = []
    monkeypatch.setattr(QMessageBox, "critical", staticmethod(lambda *a, **k: told.append(a)))
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))

    window.detect_regions()

    assert told and "no network" in told[0][2]


# ===== what reaches the scan


def test_the_scope_chosen_decides_which_images_are_looked_at(qapp, tmp_path, monkeypatch):
    ready(monkeypatch)
    window = window_with(tmp_path, {
        "new.png": ImageStatus.UNREVIEWED,
        "flagged.png": ImageStatus.FLAGGED,
        "done.png": ImageStatus.CLEAN,
    })
    every = ["new.png", "flagged.png", "done.png"]

    assert len(window._paths_in_scope(every, "all")) == 3
    assert window._paths_in_scope(every, "unreviewed") == ["new.png"]
    assert window._paths_in_scope(every, "flagged") == ["flagged.png"]


def _accept_with(monkeypatch, **overrides):
    settings = {"scope": "all", "models": [ANIME_MODEL], "thresholds": {"anime": 0.25},
                "padding": 0.15, "deep": False, "keep_groups_consistent": False,
                "styles": {"penis": (LayerType.PIXELATE, {"block_size": 12})}}
    settings.update(overrides)
    monkeypatch.setattr(DetectDialog, "exec", lambda self: QDialog.Accepted)
    monkeypatch.setattr(DetectDialog, "settings", lambda self: settings)
    return settings


def test_unticking_every_kind_stops_before_scanning(qapp, tmp_path, monkeypatch):
    ready(monkeypatch)
    window = window_with(tmp_path)
    _accept_with(monkeypatch, styles={})
    told = []
    monkeypatch.setattr(QMessageBox, "information",
                        staticmethod(lambda *a, **k: told.append(a)))
    started = []
    monkeypatch.setattr(MainWindow, "_run_worker", lambda self, *a, **k: started.append(a))

    window.detect_regions()

    assert told and "nothing to detect" in told[0][2]
    assert not started


def test_unticking_every_model_stops_before_scanning(qapp, tmp_path, monkeypatch):
    ready(monkeypatch)
    window = window_with(tmp_path)
    _accept_with(monkeypatch, models=[])
    told = []
    monkeypatch.setattr(QMessageBox, "information",
                        staticmethod(lambda *a, **k: told.append(a)))
    started = []
    monkeypatch.setattr(MainWindow, "_run_worker", lambda self, *a, **k: started.append(a))

    window.detect_regions()

    assert told and "nothing to detect with" in told[0][2]
    assert not started


def test_cancelling_the_dialog_scans_nothing(qapp, tmp_path, monkeypatch):
    ready(monkeypatch)
    window = window_with(tmp_path)
    monkeypatch.setattr(DetectDialog, "exec", lambda self: 0)
    started = []
    monkeypatch.setattr(MainWindow, "_run_worker", lambda self, *a, **k: started.append(a))

    window.detect_regions()

    assert not started


# ===== what happens with the results


def test_nothing_found_says_so_and_changes_nothing(qapp, tmp_path, monkeypatch):
    window = window_with(tmp_path)
    told = []
    monkeypatch.setattr(QMessageBox, "information",
                        staticmethod(lambda *a, **k: told.append(a)))

    window._on_detect_done(ScanResult(skipped=["a.png"]))

    assert told and "found nothing" in told[0][2]
    assert window.session.project.images["a.png"].layers == []


def test_the_results_are_not_applied_without_agreement(qapp, tmp_path, monkeypatch):
    window = window_with(tmp_path)
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.No))

    window._on_detect_done(ScanResult(proposed={"a.png": [layer_for(found())]}))

    assert window.session.project.images["a.png"].layers == []


def test_agreeing_adds_them_switched_off(qapp, tmp_path, monkeypatch):
    window = window_with(tmp_path)
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(MainWindow, "_autosave", lambda self, *a, **k: True)
    monkeypatch.setattr(MainWindow, "_refresh_summary", lambda self: None)
    monkeypatch.setattr(MainWindow, "_refresh_gallery", lambda self, result: None)
    result = ScanResult(proposed={"a.png": [layer_for(found()), layer_for(found("vagina"))]})

    window._on_detect_done(result)

    layers = window.session.project.images["a.png"].layers
    assert len(layers) == 2
    assert all(not layer.enabled for layer in layers), "proposed, not applied"
    assert window.session.project.images["a.png"].status == ImageStatus.FLAGGED


def test_the_action_comes_back_after_a_run(qapp, tmp_path, monkeypatch):
    """Otherwise a second scan is impossible without reopening the project."""
    window = window_with(tmp_path)
    window._detect_action.setEnabled(False)
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))

    window._on_detect_done(ScanResult(skipped=["a.png"]))

    assert window._detect_action.isEnabled()


# ===== the dialog itself


def test_the_dialog_offers_every_kind_and_defaults_them_on(qapp):
    dialog = DetectDialog(image_count=10, models=[ANIME_MODEL])

    assert set(dialog.styles()) == set(detection.KINDS)


def test_unticking_a_kind_removes_it(qapp):
    dialog = DetectDialog(image_count=10, models=[ANIME_MODEL])

    dialog._kind_rows["nipple"][0].setChecked(False)

    assert "nipple" not in dialog.styles()


def test_every_model_offered_is_on_by_default(qapp):
    dialog = DetectDialog(image_count=10, models=BOTH)

    assert len(dialog.chosen_models()) == 2
    assert set(dialog.thresholds()) == {"anime", "erax"}


def test_a_model_can_be_unticked(qapp):
    dialog = DetectDialog(image_count=10, models=BOTH)

    dialog._model_rows["anime"][0].setChecked(False)

    assert [spec.key for spec in dialog.chosen_models()] == ["erax"]
    assert "anime" not in dialog.thresholds()


def test_each_model_starts_at_its_own_threshold(qapp):
    dialog = DetectDialog(image_count=10, models=BOTH)

    assert dialog.thresholds()["anime"] == pytest.approx(ANIME_MODEL.threshold)
    assert dialog.thresholds()["erax"] == pytest.approx(ERAX_MODEL.threshold)


def test_the_strength_field_follows_the_censor_type(qapp):
    """Pixelate and blur do not measure strength in the same units."""
    dialog = DetectDialog(image_count=10, models=[ANIME_MODEL])
    kind = dialog._kind_rows["penis"][1]

    kind.setCurrentIndex(kind.findData(LayerType.PIXELATE))
    assert "block_size" in dialog.styles()["penis"][1]

    kind.setCurrentIndex(kind.findData(LayerType.BLUR))
    assert "radius" in dialog.styles()["penis"][1]

    kind.setCurrentIndex(kind.findData(LayerType.SOLID))
    assert "color" in dialog.styles()["penis"][1]


def test_scene_group_consistency_is_on_by_default(qapp):
    """A scene censored in patches looks worse than one not censored."""
    dialog = DetectDialog(image_count=10, models=[ANIME_MODEL])

    assert dialog.settings()["keep_groups_consistent"] is True


def test_the_dialog_reports_what_was_chosen(qapp):
    dialog = DetectDialog(image_count=10, models=[ANIME_MODEL])
    dialog.padding_spin.setValue(30)
    dialog.deep_checkbox.setChecked(True)
    dialog.group_checkbox.setChecked(False)

    settings = dialog.settings()

    assert settings["padding"] == pytest.approx(0.3)
    assert settings["deep"] is True
    assert settings["keep_groups_consistent"] is False


def test_a_dialog_with_no_models_still_opens(qapp):
    """Rather than failing on the way to explaining the problem."""
    dialog = DetectDialog(image_count=0, models=[])

    assert dialog.chosen_models() == []


# ===== fitting on a screen
#
# This dialog grew a row per model and a row per kind, and Qt will happily
# size one past the bottom of a monitor, where the buttons cannot be
# reached and there is nothing to scroll.


def test_the_dialog_is_never_taller_than_the_screen(qapp):
    from alice_censor.detection import ANIME_MODEL, ERAX_MODEL

    dialog = DetectDialog(image_count=3683, models=[ANIME_MODEL, ERAX_MODEL])
    dialog.show()

    available = dialog.screen().availableGeometry().height()
    assert dialog.height() <= available, "it would hang off the bottom"


def test_the_settings_area_scrolls(qapp):
    from PySide6.QtWidgets import QScrollArea

    dialog = DetectDialog(image_count=10, models=BOTH)

    assert dialog.findChild(QScrollArea) is not None


def test_the_buttons_are_outside_the_scrolling_part(qapp):
    """Scrolling to reach Scan would be worse than not scrolling at all."""
    from PySide6.QtWidgets import QDialogButtonBox, QScrollArea

    dialog = DetectDialog(image_count=10, models=BOTH)
    buttons = dialog.findChild(QDialogButtonBox)
    scroll = dialog.findChild(QScrollArea)

    assert buttons is not None and scroll is not None
    assert not scroll.isAncestorOf(buttons)
