"""Which files the exe keeps.

Worth testing because getting it wrong produces a build that succeeds,
launches, and then fails at one thing. A rule meant for Qt's copy of
OpenSSL matched Python's too, and the only symptom was that downloading
the detection model died with "unknown url type: https".
"""

import pytest

from build_filters import keep_binary


def entry(dest):
    """A row shaped like PyInstaller's, which is what the filter sees."""
    return (dest, "/somewhere/on/disk", "BINARY")


# ===== what Python needs, which is everything not Qt's


@pytest.mark.parametrize("dest", [
    "libcrypto-3.dll",
    "libssl-3.dll",
    "_ssl.pyd",
    "_hashlib.pyd",
])
def test_pythons_own_tls_is_kept(dest):
    """Without these, ssl will not import and https does not exist."""
    assert keep_binary(entry(dest)) is True


@pytest.mark.parametrize("dest", [
    "python312.dll",
    "PIL/_imaging.cp312-win_amd64.pyd",
    "PIL/_imagingft.cp312-win_amd64.pyd",
    "onnxruntime/capi/onnxruntime_pybind11_state.pyd",
    "numpy/core/_multiarray_umath.cp312-win_amd64.pyd",
    "numpy.libs/libscipy_openblas.dll",
])
def test_everything_else_that_is_not_qt_is_kept(dest):
    assert keep_binary(entry(dest)) is True


# ===== Qt payload this app never touches


@pytest.mark.parametrize("dest", [
    "PySide6/Qt6WebEngineCore.dll",
    "PySide6/Qt6Quick.dll",
    "PySide6/Qt6Network.dll",
    "PySide6/opengl32sw.dll",
    "PySide6/avcodec-61.dll",
    "PySide6/translations/qtbase_de.qm",
])
def test_qt_payload_with_no_part_in_this_app_is_dropped(dest):
    assert keep_binary(entry(dest)) is False


def test_qts_own_openssl_is_still_dropped():
    """The rule that caused the bug is right, in the place it belongs."""
    assert keep_binary(entry("PySide6/libcrypto-3-x64.dll")) is False
    assert keep_binary(entry("PySide6/libssl-3-x64.dll")) is False


@pytest.mark.parametrize("dest", [
    "PySide6/Qt6Core.dll",
    "PySide6/Qt6Gui.dll",
    "PySide6/Qt6Widgets.dll",
    "shiboken6/shiboken6.abi3.dll",
])
def test_the_qt_this_app_actually_uses_is_kept(dest):
    assert keep_binary(entry(dest)) is True


# ===== Qt plugins, where only a few folders matter


@pytest.mark.parametrize("folder", ["platforms", "imageformats", "styles", "iconengines"])
def test_the_plugin_folders_the_app_needs_are_kept(folder):
    assert keep_binary(entry(f"PySide6/plugins/{folder}/anything.dll")) is True


@pytest.mark.parametrize("folder", ["sqldrivers", "multimedia", "position", "tls"])
def test_plugin_folders_it_does_not_are_dropped(folder):
    assert keep_binary(entry(f"PySide6/plugins/{folder}/anything.dll")) is False


def test_backslashes_are_handled_the_same_as_forward(tmp_path):
    """PyInstaller writes native separators on Windows."""
    assert keep_binary(entry("PySide6\\Qt6WebEngineCore.dll")) is False
    assert keep_binary(entry("PySide6\\plugins\\platforms\\qwindows.dll")) is True
