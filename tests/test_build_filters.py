"""Which files the exe keeps.

Worth testing because getting it wrong produces a build that succeeds,
launches, and then fails at one thing, or one that succeeds and is quietly
several megabytes too big. Both have happened here, in opposite directions,
over the same handful of OpenSSL files.

The second one got past this file. A test asserted Qt's OpenSSL was dropped
from `PySide6/libcrypto-3-x64.dll`, a path it never actually has, so the
test passed and the exe shipped it anyway. Paths here are the ones seen in
a real build.
"""

import pytest

from build_filters import keep_binary


def entry(dest):
    """A row shaped like PyInstaller's, which is what the filter sees."""
    return (dest, "/somewhere/on/disk", "BINARY")


# ===== the TLS stack, which nothing in this app has a use for


@pytest.mark.parametrize("dest", [
    "libcrypto-3.dll",       # Python's, from its DLLs folder
    "libssl-3.dll",
    "libcrypto-3-x64.dll",   # Qt's, which lands at the root of the bundle
    "libssl-3-x64.dll",
])
def test_openssl_is_dropped_wherever_it_comes_from(dest):
    """4.6 MB for something no code here calls. Qt's copies are the ones
    that got through before, because they are not in a Qt folder."""
    assert keep_binary(entry(dest)) is False


@pytest.mark.parametrize("dest", ["_ssl.pyd", "_hashlib.pyd"])
def test_the_stdlib_modules_themselves_are_left_alone(dest):
    """Tiny, and hashlib falls back to Python's own sha1 without OpenSSL.
    This is how the exe shipped for every release up to v0.7.0."""
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


def test_a_qt_name_is_only_matched_inside_qt():
    """The narrowing that fixed https. These names are Qt's alone, so they
    must not reach a file of Python's that happens to be called the same."""
    assert keep_binary(entry("PySide6/Qt6Network.dll")) is False
    assert keep_binary(entry("translations/messages.mo")) is True


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
