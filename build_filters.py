"""Which bundled binaries the exe keeps, and which it throws away.

Lives here rather than inside the spec so it can be tested. Getting this
wrong produces an exe that builds, launches and then fails at one specific
thing, which is exactly what happened when a rule meant for Qt's copy of
OpenSSL also matched Python's and left the app unable to open an https URL.

Getting it wrong the other way is quieter and was shipped once. Narrowing
that rule to Qt's folders stopped it catching Qt's copy as well, because
Qt's OpenSSL lands at the root of the bundle rather than beside the rest of
Qt, and v0.7.0 went out carrying 2.6 MB of it.
"""

from __future__ import annotations

# Dropped wherever they come from, Qt's copy and Python's alike.
#
# Nothing in this app opens a socket. The only reason https was ever wanted
# was to download a detection model, and that feature now lives on the
# autodetect branch, which needs Python's pair put back. Together they are
# 4.6 MB, which is most of the difference between a 28 MB exe and a 32 MB
# one. Qt's copy is worse than unused, it is a dependency of Qt6Network,
# which is itself thrown away below.
#
# The one thing this leaves behind is _ssl.pyd, which imports and then
# cannot do anything. That is how the exe shipped for its whole life up to
# v0.7.0. Hashing is unaffected, since sha1 and friends are built into
# Python rather than coming from OpenSSL.
EXCLUDED_ANYWHERE = ("libcrypto", "libssl")

# Qt payload with no part in a QtWidgets app, matched on filename. Kept as
# substrings because the version suffixes move between releases.
#
# Every one of these is Qt's, which is why they are only ever applied to
# files coming out of the Qt folders. A bare name would match Python's
# equivalents just as happily, and Python needs those.
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
    # Qt's networking. The TLS stack it wants is handled above, since it
    # does not land in a Qt folder.
    "qt6network",
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
    if any(part in dest for part in EXCLUDED_ANYWHERE):
        return False
    if not _is_qt(dest):
        # Anything else that is not Qt's is Python's or ours, and both are
        # needed. Pruning more than the list above is what broke https.
        return True
    if any(part in dest for part in EXCLUDED_QT_PARTS):
        return False
    if "plugins/" in dest:
        folder = dest.split("plugins/", 1)[1].split("/", 1)[0]
        return folder in KEPT_PLUGIN_DIRS
    return True


def _is_qt(dest: str) -> bool:
    return any(root in dest.split("/") or dest.startswith(root + "/") for root in QT_ROOTS)
