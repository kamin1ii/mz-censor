from __future__ import annotations

import argparse
from pathlib import Path
import sys
from PySide6.QtCore import QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication
from alice_censor.gui.icon import ICON_PATH
from .gui import MainWindow


def run():
    parser = argparse.ArgumentParser(description='MZ Censor')
    parser.add_argument('--project', help='Open an existing .rgcproj.json')
    parser.add_argument('--smoke-test', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    app = QApplication(sys.argv)
    app.setApplicationName('MZ Censor')
    app.setWindowIcon(QIcon(str(ICON_PATH)))
    window = MainWindow()
    if args.smoke_test:
        from evbunpack.const import EVB_MAGIC
        from .project import RpgProject
        assert EVB_MAGIC and Path(__file__).with_name('packed_boot.js').is_file()
        if args.project:
            window.set_project(RpgProject.load(args.project))
        app.processEvents()
        window.close()
        return 0
    window.show()
    if args.project:
        QTimer.singleShot(0, lambda: window.load(args.project))
    return app.exec()
