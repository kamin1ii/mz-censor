# PyInstaller build for Alice Censor.
#
# Build with:  .venv\Scripts\pyinstaller alice-censor.spec
#
# PySide6 ships around 640 MB, nearly all of which this app never touches.
# It uses QtCore, QtGui and QtWidgets and nothing else, so the two lists
# below throw the rest away. That is the whole difference between a 500 MB
# folder and a small exe, so both lists are deliberate rather than
# cargo-culted. Anything removed here that turns out to be needed shows up
# as a missing DLL on launch, not as a subtle bug.

import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

# Which bundled files survive. Kept in its own module so the rule can
# be tested, after one of its patterns quietly matched Python's copy
# of OpenSSL as well as Qt's.
from build_filters import keep_binary

# Two builds come out of this one file. The plain one is small and has no
# automatic detection in it. Setting ALICE_CENSOR_DETECT=1 keeps onnxruntime
# and numpy, which roughly triples the size, and produces a separately named
# exe so the two never overwrite each other.
WITH_DETECTION = os.environ.get("ALICE_CENSOR_DETECT") == "1"

APP_NAME = "AliceCensorDetect" if WITH_DETECTION else "AliceCensor"
ICON = Path("alice_censor/assets/icon.ico")

# Qt modules with no part in a QtWidgets app. QtWebEngineCore alone is a
# whole embedded Chromium at roughly 195 MB.
EXCLUDED_QT_MODULES = [
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick",
    "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtQuick3D", "PySide6.QtQuickWidgets",
    "PySide6.QtQuickControls2", "PySide6.QtQuickTest",
    "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets", "PySide6.QtSpatialAudio",
    "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.Qt3DInput", "PySide6.Qt3DLogic",
    "PySide6.Qt3DAnimation", "PySide6.Qt3DExtras",
    "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtGraphs",
    "PySide6.QtBluetooth", "PySide6.QtNfc", "PySide6.QtPositioning", "PySide6.QtLocation",
    "PySide6.QtSerialPort", "PySide6.QtSerialBus", "PySide6.QtSensors",
    "PySide6.QtRemoteObjects", "PySide6.QtScxml", "PySide6.QtStateMachine",
    "PySide6.QtWebSockets", "PySide6.QtWebChannel", "PySide6.QtHttpServer",
    "PySide6.QtDesigner", "PySide6.QtUiTools", "PySide6.QtHelp", "PySide6.QtTest",
    "PySide6.QtSql", "PySide6.QtPdf", "PySide6.QtPdfWidgets", "PySide6.QtTextToSpeech",
    "PySide6.QtOpenGL", "PySide6.QtOpenGLWidgets", "PySide6.QtDBus",
    "PySide6.QtNetworkAuth", "PySide6.QtConcurrent",
    "PySide6.scripts",
]

EXCLUDES = EXCLUDED_QT_MODULES + [
    # Pulled in by Pillow's optional plugins and by setuptools, never by us.
    "tkinter", "unittest", "pydoc", "doctest",
    "matplotlib", "scipy", "pandas",
    "pytest", "_pytest", "pygments",
    # Pillow's AVIF codec is 7.5 MB on its own. The sticker library and the
    # archives this reads are png, jpg, webp, bmp, gif and qnt, never avif.
    "PIL._avif", "PIL.AvifImagePlugin",
    # numpy and onnxruntime are only wanted by the detection feature, which
    # reports itself unavailable without them rather than breaking. Together
    # they are most of 100 MB, so the plain build leaves them out.
    *([] if WITH_DETECTION else ["numpy", "onnxruntime", "onnx"]),
    # Repacking encodes images on a thread pool, and importing
    # concurrent.futures drags in the process pool with it. Nothing here
    # ever touches ProcessPoolExecutor, and the package only imports it
    # when something asks for it by name, so it never loads at runtime.
    "multiprocessing", "concurrent.futures.process",
]

a = Analysis(
    ["main.py"],
    pathex=["."],
    binaries=[],
    # The window and taskbar icon are loaded from this path at runtime by
    # gui/icon.py, so it has to exist inside the bundle too.
    datas=[("alice_censor/assets/icon.ico", "alice_censor/assets"),
           ("alice_censor/assets/icon.png", "alice_censor/assets")],
    hiddenimports=collect_submodules("alice_censor"),
    excludes=EXCLUDES,
    noarchive=False,
)

a.binaries = [b for b in a.binaries if keep_binary(b)]
a.datas = [d for d in a.datas if keep_binary(d)]

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    # No console window behind the GUI.
    console=False,
    icon=str(ICON),
)
