"""Which bundled binaries the exe keeps, and which it throws away.

Lives here rather than inside the spec so it can be tested. Getting this
wrong produces an exe that builds, launches and then fails at one specific
thing, which is exactly what happened when a rule meant for Qt's copy of
OpenSSL also matched Python's and left the app unable to open an https URL.
"""

from __future__ import annotations

# Qt payload with no part in a QtWidgets app, matched on filename. Kept as
# substrings because the version suffixes move between releases.
#
# Every one of these is Qt's, which is why they are only ever applied to
# files coming out of the Qt folders. A bare name like libcrypto matches
# Python's own OpenSSL just as happily, and Python needs it.
EXCLUDED_QT_PARTS = (
    "qt6webengine", "qt6quick", "qt6qml", "qt6multimedia", "qt63d",
    "qt6charts", "qt6datavisualization", "qt6graphs", "qt6pdf",
    "qt6designer", "qt6test", "qt6sql", "qt6bluetooth", "qt6nfc",
    "qt6positioning", "qt6location", "qt6sensors", "qt6serialport",
    "qt6remoteobjects", "qt6scxml", "qt6statemachine", "qt6websockets",
    "qt6webchannel", "qt6texttospeech", "qt6spatialaudio", "qt6shadertools",
    # Bundled ffmpeg, only ever used by QtMultimedia.
    "avcodec", "avformat", "avutil", "swresample", "swscale",
    # A 20 MB software OpenGL fallback. The widgets this app uses render
    # through the raster engine.
    "opengl32sw",
    # Qt's networking and the TLS stack behind it. Nothing here opens a
    # socket through Qt, and its libcrypto alone is 5 MB. Python's own
    # OpenSSL is a different file in a different place and is kept, because
    # downloading the detection model needs https.
    "qt6network", "libcrypto", "libssl",
    # An on-screen keyboard for touch devices.
    "qt6virtualkeyboard",
    # Qt's own translations, 60 MB of .qm for languages the app has none of.
    "translations",
)

# Qt plugin folders. Only the platform integration, image formats and
# styles matter here.
KEPT_PLUGIN_DIRS = ("platforms", "imageformats", "styles", "iconengines")

# Where Qt's own files land in the bundle.
QT_ROOTS = ("pyside6", "shiboken6")


def keep_binary(entry) -> bool:
    """Whether one bundled file survives into the exe.

    `entry` is a PyInstaller table row, whose first element is where the
    file will sit inside the bundle.
    """
    dest = entry[0].replace("\\", "/").lower()
    if not _is_qt(dest):
        # Anything that is not Qt's is Python's or ours, and both are
        # needed. Pruning here is what broke https.
        return True
    if any(part in dest for part in EXCLUDED_QT_PARTS):
        return False
    if "plugins/" in dest:
        folder = dest.split("plugins/", 1)[1].split("/", 1)[0]
        return folder in KEPT_PLUGIN_DIRS
    return True


def _is_qt(dest: str) -> bool:
    return any(root in dest.split("/") or dest.startswith(root + "/") for root in QT_ROOTS)
