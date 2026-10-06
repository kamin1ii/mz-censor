"""RPG Maker workflow, sharing Alice Censor's gallery and region editor."""
from __future__ import annotations

from pathlib import Path
import subprocess

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (QFileDialog, QHBoxLayout, QInputDialog, QLabel, QMainWindow,
    QMessageBox, QPlainTextEdit, QPushButton, QTabWidget, QVBoxLayout, QWidget)

from alice_censor.editor.editor_dialog import RegionEditorDialog
from alice_censor.gallery.gallery_model import GalleryModel
from alice_censor.gallery.gallery_widget import GalleryWidget
from alice_censor.project import ImageStatus
from alice_censor.stickers import make_sticker_resolver
from .audit import audit_project
from .deploy import apply_project, restore_project
from .packed import import_packed
from .project import RpgProject, import_game
from .grouping import DEFAULT_FLAG_PATTERN, flag_candidates


class Job(QThread):
    output = Signal(str)
    success = Signal(object)
    failure = Signal(str)

    def __init__(self, function, parent=None):
        super().__init__(parent)
        self.function = function

    def run(self):
        try:
            result = self.function(self.output.emit)
        except Exception as error:
            self.failure.emit(str(error))
        else:
            self.success.emit(result)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('MZ Censor')
        self.resize(1280, 850)
        self.project = None
        self.worker = None
        self.summary = QLabel('Open an MV/MZ game folder, or import a packed executable into a separate playable copy.')
        self.summary.setWordWrap(True)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(3000)
        setup = QWidget()
        layout = QVBoxLayout(setup)
        layout.addWidget(self.summary)
        row = QHBoxLayout()
        self.project_buttons = []
        for title, action in [('Apply Censor Edits', self.apply), ('Restore Original Images', self.restore),
                              ('Audit All Images', self.audit), ('Launch Game', self.launch)]:
            button = QPushButton(title)
            button.clicked.connect(action)
            row.addWidget(button)
            self.project_buttons.append(button)
        layout.addLayout(row)
        layout.addWidget(self.log)
        self.gallery = GalleryWidget()
        self.gallery.auto_flag_button.setText('Flag by Filename…')
        self.gallery.auto_flag_button.setToolTip('Optional filename heuristic. It cannot prove an image is safe or find every scene.')
        self.gallery.status_changed.connect(self.save)
        self.gallery.open_requested.connect(self.edit)
        self.gallery.clear_edits_requested.connect(self.clear_edits)
        self.gallery.auto_flag_requested.connect(self.auto_flag)
        self.tabs = QTabWidget()
        self.tabs.addTab(setup, 'Game / Apply')
        self.tabs.addTab(self.gallery, 'All Images')
        self.setCentralWidget(self.tabs)
        menu = self.menuBar().addMenu('&File')
        menu.addAction('New MV/MZ Folder Project…', self.new_folder)
        menu.addAction('Import Packed EXE…', self.new_packed)
        menu.addAction('Open Project…', self.open_project)
        menu.addAction('Save Project', self.save, 'Ctrl+S')
        menu.addSeparator()
        menu.addAction('Exit', self.close)
        review = self.menuBar().addMenu('&Review')
        review.addAction('Assign Selected to Scene Group…', self.assign_group)
        review.addAction('Reset Selected Scene Groups', self.reset_groups)
        self._enable()

    def _enable(self):
        busy = self.worker is not None
        self.menuBar().setEnabled(not busy)
        self.gallery.setEnabled(not busy and self.project is not None)
        for button in self.project_buttons:
            button.setEnabled(not busy and self.project is not None)

    def run_job(self, function, success):
        if self.worker is not None:
            return
        worker = Job(function, self)
        self.worker = worker
        worker.output.connect(self.log.appendPlainText)
        worker.success.connect(success)
        worker.failure.connect(self.error)
        worker.finished.connect(self._finished)
        worker.start()
        self._enable()

    def _finished(self):
        self.worker.deleteLater()
        self.worker = None
        self._enable()

    def error(self, message):
        self.log.appendPlainText('ERROR: ' + message)
        QMessageBox.critical(self, 'Could not complete operation', message)

    def load(self, path):
        try:
            self.set_project(RpgProject.load(path))
        except Exception as error:
            self.error(str(error))

    def set_project(self, project):
        model = GalleryModel(project, project, project.groups(), project.project_file.parent / '.thumbnails',
                             self.gallery, sticker_resolver=make_sticker_resolver(project.sticker_dir))
        self.project = project
        self.gallery.set_model(model)
        self.gallery.set_auto_flag_available(True)
        self.setWindowTitle('MZ Censor — ' + project.title)
        self.refresh_summary()
        self.tabs.setCurrentIndex(1)
        self._enable()

    def refresh_summary(self):
        if self.project:
            p = self.project
            edited = sum(bool(p.enabled_layers(name)) for name in p.assets)
            unreviewed = sum(r.status == ImageStatus.UNREVIEWED for r in p.images.values())
            repaired = sum(bool(a.get('repairs')) for a in p.assets.values())
            variants = sum(name.startswith('loose-variants/') for name in p.assets)
            notes = ''
            if repaired:
                notes += f'\n{repaired} images had damaged color-profile metadata removed from the editor cache; original files are preserved.'
            if variants:
                notes += f'\n{variants} loose-variants are alternate files hidden by the original EXE; they are available for review but are not used by the game.'
            self.summary.setText(f'{p.title} · RPG Maker {p.engine}\n{len(p.assets):,} images · '
                                 f'{unreviewed:,} unreviewed · {edited:,} with enabled edits\n'
                                 f'Game: {p.game_root}\nProject: {p.project_file}\n'
                                 'Save keeps your layers. Apply writes them to the game. Restart the game after applying.' + notes)

    def save(self):
        if self.project:
            try:
                self.project.save()
                self.refresh_summary()
                return True
            except Exception as error:
                self.error(str(error))
                return False
        return True

    def open_project(self):
        if not self.save():
            return
        path, _ = QFileDialog.getOpenFileName(self, 'Open Project', filter='MZ Censor (*.rgcproj.json)')
        if path:
            self.load(path)

    def new_folder(self):
        if not self.save():
            return
        source = QFileDialog.getExistingDirectory(self, 'Select game folder or www folder')
        if not source:
            return
        workspace = QFileDialog.getExistingDirectory(self, 'Select EMPTY project folder outside game content')
        if workspace:
            self.run_job(lambda log: import_game(source, workspace, log), self.set_project)

    def new_packed(self):
        if not self.save():
            return
        source, _ = QFileDialog.getOpenFileName(self, 'Packed game EXE', filter='Game executable (*.exe)')
        if not source:
            return
        playable = QFileDialog.getExistingDirectory(self, 'Select EMPTY folder for playable game copy')
        if not playable:
            return
        workspace = QFileDialog.getExistingDirectory(self, 'Select EMPTY project folder, separate from playable copy')
        if workspace:
            self.run_job(lambda log: import_packed(source, playable, workspace, log), self.set_project)

    def edit(self, name):
        p = self.project
        if not p:
            return
        try:
            record = p.images[name]
            siblings = [n for n, r in p.images.items() if n != name and r.effective_group == record.effective_group]
            dialog = RegionEditorDialog(Path(p.extract_dir) / name, record,
                                        make_sticker_resolver(p.sticker_dir), self,
                                        project=p, current_path=name, group_members=siblings)
            dialog.batch_applied.connect(self.save)
            accepted = dialog.exec()
            changed = set(dialog.batch_applied_paths)
            if accepted:
                changed.add(name)
                self.save()
            for item in changed:
                self.gallery.model.notify_layers_changed(item)
        except Exception as error:
            self.error(str(error))

    def clear_edits(self, paths):
        if not self.project:
            return
        if QMessageBox.question(self, 'Remove Layers', f'Remove saved censor layers from {len(paths)} selected images?') == QMessageBox.Yes:
            self.gallery.model.clear_layers_for_paths(paths)
            self.save()

    def auto_flag(self):
        if not self.project:
            return
        pattern, accepted = QInputDialog.getText(self, 'Filename Pattern',
            'Regular expression (case insensitive). Only unreviewed images can be flagged:', text=DEFAULT_FLAG_PATTERN)
        if not accepted or not pattern:
            return
        try:
            names = flag_candidates(self.project, pattern)
        except Exception as error:
            self.error(str(error))
            return
        if QMessageBox.question(self, 'Filename Suggestions', f'Flag {len(names)} matching filenames? This does not detect image content; review every image.') == QMessageBox.Yes:
            self.gallery.model.set_status_for_paths(names, ImageStatus.FLAGGED)

    def assign_group(self):
        if not self.project:
            return
        paths = self.gallery._selected_paths()
        if not paths:
            QMessageBox.information(self, 'Select Images', 'Select images in the gallery first. Use Ctrl or Shift for multiple images.')
            return
        name, accepted = QInputDialog.getText(self, 'Scene Group', f'Group name for {len(paths)} selected images:')
        if accepted and name.strip():
            for path in paths:
                self.project.images[path].group_override = name.strip()
            self.save()
            self.set_project(self.project)

    def reset_groups(self):
        if not self.project:
            return
        for path in self.gallery._selected_paths():
            self.project.images[path].group_override = None
        self.save()
        self.set_project(self.project)

    def apply(self):
        if self.project and self.save():
            p = self.project
            self.run_job(lambda log: apply_project(p, log), lambda n: self.log.appendPlainText(f'Applied and verified {n} edited images. Restart the game.'))

    def restore(self):
        if self.project:
            p = self.project
            self.run_job(lambda log: restore_project(p, log), lambda _: self.log.appendPlainText('Original images restored. Saved censor layers are retained.'))

    def audit(self):
        if self.project:
            p = self.project
            self.run_job(lambda log: audit_project(p, log), self.audit_done)

    def audit_done(self, report):
        text = f"{report['images']:,} images checked. {len(report['errors'])} errors.\n\n{report['scope']}"
        if report.get('videos'):
            text += '\n\nVideos require separate review; this editor does not censor them:\n' + '\n'.join(report['videos'])
        if report['errors']:
            text += '\n\n' + '\n'.join(report['errors'][:10])
        QMessageBox.information(self, 'Image Inventory Audit', text)

    def launch(self):
        if not self.project:
            return
        root = Path(self.project.game_root)
        candidates = [root / 'Game.exe', root.parent / 'Game.exe']
        executable = next((p for p in candidates if p.is_file()), None)
        if executable is None:
            path, _ = QFileDialog.getOpenFileName(self, 'Select game launcher', str(root.parent), 'Executable (*.exe)')
            if not path:
                return
            executable = Path(path)
        try:
            subprocess.Popen([str(executable)], cwd=executable.parent)
        except OSError as error:
            self.error(str(error))

    def closeEvent(self, event):
        if self.worker is not None:
            QMessageBox.information(self, 'Operation Running', 'Wait for the current import, audit or apply operation to finish.')
            event.ignore()
            return
        if not self.save():
            event.ignore()
            return
        if self.gallery.model:
            self.gallery.model.shutdown()
        event.accept()
