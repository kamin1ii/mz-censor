from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QFileDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..afa_repack import repack_afa_in_place, verify_afa
from ..alice_tools import AliceTools, AliceToolsOutdated
from ..editor.editor_dialog import RegionEditorDialog
from ..export import render_export
from ..extraction import extract_archive
from ..gallery.gallery_model import GalleryModel
from ..gallery.gallery_widget import GalleryWidget
from ..grouping import find_explicit_by_naming
from ..manifest import Manifest, ManifestFormat, parse_manifest
from ..paths import resolve_fs_path
from ..project import ImageRecord, ImageStatus, ProjectState
from ..ald_repack import find_sibling_volumes, repack_ald_in_place, verify_ald
from ..scanning import scan_and_sync
from ..session import OpenProject
from ..share import BundleError, apply_bundle, export_bundle, read_bundle
from ..stickers import make_sticker_resolver
from ..verify import VerifyResult, verify_archive_contents
from .icon import ICON_PATH
from .new_project_dialog import NewProjectDialog
from .settings import native_formats_enabled, set_native_formats_enabled
from .workers import CommandWorker


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Alice Censor")
        self.setWindowIcon(QIcon(str(ICON_PATH)))
        self.resize(1100, 750)

        # None until a project is open. See session.OpenProject for why the
        # project, its manifest and the tools travel as one value.
        self.session: OpenProject | None = None
        self._worker: CommandWorker | None = None
        # Set once per project so the repack note, whether unavailable or
        # experimental, lands in the log once rather than on every summary
        # refresh.
        self._warned_repack_notice = False
        # Set when Open Shared Project was chosen with nothing open, so
        # it can be applied once New Project finishes extracting.
        self._pending_bundle: str | None = None
        # Tracks whether the open project has changes not yet persisted to
        # its project_file. _autosave sets this before writing and clears
        # it only once the write actually succeeds, so a failed save from a
        # full disk or a permissions problem correctly stays set instead of
        # losing track of the unsaved change.
        self._dirty = False

        # ===== "Extract / Repack" tab
        self.summary_label = QLabel("No project open.")
        self.summary_label.setWordWrap(True)

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)

        self.extract_button = QPushButton("Extract")
        self.extract_button.clicked.connect(self.run_extract)
        self.extract_button.setEnabled(False)

        self.repack_button = QPushButton("Repack")
        self.repack_button.clicked.connect(self.run_repack)
        self.repack_button.setEnabled(False)

        extract_tab = QWidget()
        extract_layout = QVBoxLayout(extract_tab)
        extract_layout.addWidget(self.summary_label)
        extract_layout.addWidget(self.extract_button)
        extract_layout.addWidget(self.repack_button)
        extract_layout.addWidget(self.log_view, stretch=1)

        # ===== "Gallery" tab
        self.gallery_widget = GalleryWidget()
        self.gallery_widget.status_changed.connect(self._on_gallery_status_changed)
        self.gallery_widget.open_requested.connect(self._on_gallery_open_requested)
        self.gallery_widget.auto_flag_requested.connect(self._on_auto_flag_requested)
        self.gallery_widget.clear_edits_requested.connect(self._on_clear_edits_requested)

        self.tabs = QTabWidget()
        self.tabs.addTab(extract_tab, "Extract / Repack")
        self.tabs.addTab(self.gallery_widget, "Gallery")
        self.setCentralWidget(self.tabs)

        self._build_menu()

    def _build_menu(self) -> None:
        file_menu = self.menuBar().addMenu("&File")
        file_menu.addAction("&New Project…", self.new_project)
        file_menu.addAction("&Open Project…", self.open_project)
        self._save_action = file_menu.addAction("&Save Project", self.save_project)
        self._save_action.setEnabled(False)
        self._save_as_action = file_menu.addAction("Save Project &As…", self.save_project_as)
        self._save_as_action.setEnabled(False)
        file_menu.addSeparator()
        self._export_action = file_menu.addAction("&Share Project…", self.export_bundle)
        self._export_action.setEnabled(False)
        # Left enabled with no project open. Being handed a bundle is
        # exactly the case where you have not extracted anything yet, and
        # a greyed out menu item explains none of that.
        self._import_action = file_menu.addAction("&Open Shared Project…", self.import_bundle)
        file_menu.addSeparator()
        file_menu.addAction("E&xit", self.close)

        advanced_menu = self.menuBar().addMenu("&Advanced")
        self._native_action = advanced_menu.addAction(
            "Use &built in format support (experimental)"
        )
        self._native_action.setCheckable(True)
        self._native_action.setChecked(native_formats_enabled())
        self._native_action.setToolTip(
            "Repack .afa archives without alice.exe, copying every image you "
            "did not edit as the bytes it already was."
        )
        self._native_action.toggled.connect(self._on_native_formats_toggled)

    def _on_native_formats_toggled(self, enabled: bool) -> None:
        set_native_formats_enabled(enabled)
        if enabled:
            self.log(
                "Built in format support on. Repacking an .afa will rebuild it "
                "here rather than through alice.exe ar pack."
            )
        else:
            self.log("Built in format support off. Repacking uses alice.exe ar pack.")
        # The switch is reachable with nothing open, which is a reasonable
        # time to set it, and there is no summary to refresh in that case.
        if self.session is not None:
            self._refresh_summary()

    def _use_native_formats(self) -> bool:
        """Whether this build should read and write archives itself.

        On by default. Turned off, extract and repack go back through
        alice.exe. The .ald repack ignores it either way, since alice-tools
        cannot write that format at all.
        """
        return bool(self._native_action.isChecked())

    def _alice_exe_usable(self, tools: AliceTools) -> bool:
        """Report a missing or too old alice.exe, once, in one way."""
        try:
            tools.check_available()
            tools.check_supported()
        except FileNotFoundError as e:
            QMessageBox.critical(self, "alice.exe not found", str(e))
            return False
        except AliceToolsOutdated as e:
            QMessageBox.critical(self, "alice.exe is too old", str(e))
            return False
        return True

    # ===== logging

    def log(self, line: str) -> None:
        self.log_view.appendPlainText(line)

    # ===== close / dirty tracking

    def _save_project(self, path: str | Path | None = None) -> bool:
        """Persist the open project, clearing the dirty flag only if the
        write actually succeeds. Returns whether it succeeded.

        The single place the project file is written. Everything that
        changes project state routes through here or through _autosave, so
        a failed write is reported once, in one way, and the dirty flag
        cannot drift away from what is actually on disk.
        """
        assert self.session is not None
        try:
            self.session.project.save(path)
        except OSError as e:
            QMessageBox.critical(self, "Failed to save project", str(e))
            return False
        self._dirty = False
        return True

    def _autosave(self, path: str | Path | None = None) -> bool:
        """Record that the project changed, then immediately persist it.

        Marking before writing is the point. If the write fails, the flag
        stays set and the close prompt still knows there is unsaved work,
        which is why this is one operation and not two calls a caller could
        get out of order.
        """
        self._dirty = True
        return self._save_project(path)

    def closeEvent(self, event) -> None:
        if not self._dirty:
            self._stop_background_work()
            event.accept()
            return
        reply = QMessageBox.question(
            self,
            "Unsaved changes",
            "This project has unsaved changes. Save before closing?",
            QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
            QMessageBox.Save,
        )
        if reply == QMessageBox.Cancel:
            event.ignore()
            return
        if reply == QMessageBox.Save and not self._save_project():
            event.ignore()
            return
        self._stop_background_work()
        event.accept()

    def _stop_background_work(self) -> None:
        """Let the gallery finish what it is doing before the window goes.

        Only reached once closing is certain, never on a cancelled close,
        so a user who backs out of the prompt keeps their thumbnails.
        """
        if self.gallery_widget.model is not None:
            self.gallery_widget.model.shutdown()

    # ===== project lifecycle

    def new_project(self) -> None:
        native = self._use_native_formats()
        dialog = NewProjectDialog(self, needs_alice_exe=not native)
        if not dialog.exec():
            return
        archive_path, alice_exe, output_dir, project_file = dialog.values()

        tools = AliceTools(alice_exe)
        if not native and not self._alice_exe_usable(tools):
            return

        manifest_path = Path(output_dir) / "manifest.txt"
        self.log(f"Extracting {archive_path!r} -> {output_dir!r} ...")
        self.extract_button.setEnabled(False)

        if native:
            def job(on_output):
                return extract_archive(
                    archive_path,
                    output_dir,
                    manifest_path,
                    on_progress=lambda path: on_output(f"extracting {path}"),
                )
        else:
            def job(on_output):
                return tools.extract(
                    archive_path, output_dir, manifest_path=manifest_path, on_output=on_output
                )

        # `tools` stays a local until the extract succeeds. Nothing is
        # written to self until there is a whole project to write.
        self._run_worker(
            job,
            on_success=lambda result: self._on_initial_extract_done(
                archive_path, alice_exe, output_dir, manifest_path, Path(project_file), tools
            ),
        )

    def _on_initial_extract_done(
        self,
        archive_path: str,
        alice_exe: str,
        output_dir: str,
        manifest_path: Path,
        project_file: Path,
        tools: AliceTools,
    ) -> None:
        self.log("Extract finished. Parsing manifest...")
        try:
            manifest = parse_manifest(manifest_path)
        except Exception as e:  # noqa: BLE001 - surface any parse failure to the user
            QMessageBox.critical(self, "Failed to parse manifest", str(e))
            return

        project = ProjectState(
            archive_path=str(Path(archive_path).resolve()),
            manifest_path=str(manifest_path.resolve()),
            alice_exe_path=str(Path(alice_exe).resolve()),
            extract_dir=str(Path(output_dir).resolve()),
            output_dir=str((Path(output_dir) / "censored_out").resolve()),
            sticker_dir=str((Path(output_dir) / "stickers").resolve()),
            archive_format=manifest.archive_format.value,
        )
        self.session = OpenProject(project=project, manifest=manifest, tools=tools)
        self._warned_repack_notice = False
        result = scan_and_sync(project, manifest)
        self._autosave(project_file)

        self.log(
            f"Project created: {len(manifest.entries)} files, "
            f"{len(result.groups)} suggested scene group(s)."
        )
        self._refresh_summary()
        self._refresh_gallery(result)

        pending, self._pending_bundle = self._pending_bundle, None
        if pending:
            self._apply_bundle_file(pending)

    def open_project(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Project", filter="Alice Censor projects (*.acproj.json)"
        )
        if not path:
            return
        # Load into a local first. A project whose manifest has gone missing
        # since it was saved is a normal thing to hit after a game update,
        # and assigning the parts one at a time used to leave the window
        # holding the new project alongside the previous one's manifest.
        try:
            session = OpenProject.load(path)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Failed to open project", str(e))
            return
        self.session = session
        self._warned_repack_notice = False
        self._dirty = False  # freshly loaded from disk, nothing unsaved yet

        result = scan_and_sync(session.project, session.manifest)
        if result.new_paths or result.changed_paths or result.missing_paths:
            self._autosave()

        self.log(
            f"Opened project: {path}  "
            f"({len(result.new_paths)} new, {len(result.changed_paths)} changed, "
            f"{len(result.missing_paths)} missing since last open)"
        )
        self._refresh_summary()
        self._refresh_gallery(result)

    def save_project(self) -> None:
        if self.session is None:
            return
        if self._save_project():
            self.log(f"Saved project: {self.session.project.project_file}")

    def save_project_as(self) -> None:
        if self.session is None:
            return
        project = self.session.project
        start = str(project.project_file) if project.project_file else ""
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Project As", start, filter="Alice Censor projects (*.acproj.json)"
        )
        if not path:
            return
        if self._save_project(path):
            self.log(f"Saved project: {project.project_file}")

    def export_bundle(self) -> None:
        """Package this project so someone else can apply it.

        Carries statuses, layers and the stickers those layers use, with
        every local path stripped out. Not the images, which the recipient
        already has in their own copy of the archive.
        """
        if self.session is None:
            return
        project = self.session.project
        suggested = f"{Path(project.archive_path).stem}-censor.zip"
        start = str(Path(project.project_file).parent / suggested) if project.project_file else suggested
        path, _ = QFileDialog.getSaveFileName(
            self, "Share Project", start, filter="Shared Alice Censor projects (*.zip)"
        )
        if not path:
            return
        try:
            included = export_bundle(project, path)
        except OSError as e:
            QMessageBox.critical(self, "Could not share the project", str(e))
            return

        edited = sum(1 for rec in project.images.values() if rec.layers)
        layers = sum(len(rec.layers) for rec in project.images.values())
        message = (
            f"Exported {layers} layer(s) across {edited} image(s), "
            f"with {len(included)} sticker(s).\n\n{path}\n\n"
            f"Whoever opens this needs their own project made from the same "
            f"archive. It carries no images and no paths."
        )
        self.log(message)
        QMessageBox.information(self, "Project shared", message)

    def import_bundle(self) -> None:
        """Apply a shared project on top of this one.

        Matched by the path each image has inside the archive, so both
        sides have to come from the same archive for anything to line up.
        A mismatch shows as unmatched entries rather than as an error.
        """
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Shared Project", "", filter="Shared Alice Censor projects (*.zip)"
        )
        if not path:
            return

        if self.session is None:
            self._offer_project_for_bundle(path)
            return
        self._apply_bundle_file(path)

    def _apply_bundle_file(self, path: str) -> None:
        """Confirm and apply a shared project to the open one.

        Split out because it can arrive two ways, chosen against an open
        project or chosen with nothing open and applied once New Project
        has finished extracting.
        """
        assert self.session is not None
        project = self.session.project
        try:
            bundle = read_bundle(path)
        except (BundleError, OSError) as e:
            QMessageBox.critical(self, "Not a shared project", str(e))
            return

        already = sum(1 for rec in project.images.values() if rec.layers)
        warning = ""
        if bundle.archive_name and bundle.archive_name != Path(project.archive_path).name:
            warning = (
                f"\n\nIt was made from {bundle.archive_name}, and this project is "
                f"{Path(project.archive_path).name}. Anything that does not line up "
                f"will be skipped."
            )
        reply = QMessageBox.question(
            self,
            "Open shared project",
            f"Apply {bundle.layer_count} layer(s) across {bundle.edited_count} image(s), "
            f"plus {len(bundle.stickers)} sticker(s)?\n\n"
            f"This replaces the layers on any image it covers. "
            f"{already} image(s) here already have layers.{warning}",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        try:
            result = apply_bundle(path, project)
        except (BundleError, OSError) as e:
            QMessageBox.critical(self, "Could not open the shared project", str(e))
            return

        self._autosave()
        lines = [
            f"Imported {len(result.applied)} image(s) from {Path(path).name}, "
            f"copying {len(result.stickers_copied)} new sticker(s)."
        ]
        if result.unmatched:
            lines.append(
                f"{len(result.unmatched)} image(s) in it are not in this project, "
                f"so they were skipped. First few: " + ", ".join(result.unmatched[:5])
            )
        if result.missing_stickers:
            lines.append(
                f"{len(result.missing_stickers)} sticker(s) a layer needs were not in the "
                f"shared project, so those layers will not render until you supply them: "
                + ", ".join(result.missing_stickers)
            )
        message = "\n\n".join(lines)
        self.log(message)

        if self.gallery_widget.model is not None:
            for changed in result.applied:
                self.gallery_widget.model.notify_layers_changed(changed)
        QMessageBox.information(self, "Shared project applied", message)

    def _offer_project_for_bundle(self, bundle_path: str) -> None:
        """Explain what a shared project needs, and offer to set it up.

        It carries the review work, not the images, so it can only be
        applied to images you already have. Being sent one is the common
        way to arrive here with nothing open, so rather than refusing,
        this walks into New Project and applies it once the archive has
        been extracted.
        """
        try:
            bundle = read_bundle(bundle_path)
        except (BundleError, OSError) as e:
            QMessageBox.critical(self, "Not a shared project", str(e))
            return

        from_archive = f" It was made from {bundle.archive_name}." if bundle.archive_name else ""
        reply = QMessageBox.question(
            self,
            "Extract the archive first",
            f"This shared project has {bundle.layer_count} layer(s) across "
            f"{bundle.edited_count} image(s) and {len(bundle.stickers)} sticker(s), "
            f"but no images.{from_archive}\n\n"
            "It applies on top of a project of your own made from the same archive, "
            "so that has to be extracted first. Set one up now and apply the "
            "shared project when it finishes?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if reply != QMessageBox.Yes:
            return
        self._pending_bundle = bundle_path
        self.new_project()

    def _set_project_actions_enabled(self, enabled: bool) -> None:
        """Enable or disable everything that only makes sense with a
        project open. Kept apart from _refresh_summary so that redrawing
        the summary text and re-enabling the buttons after a failed run
        stay separate concerns.
        """
        self.extract_button.setEnabled(enabled)
        self.repack_button.setEnabled(enabled and self._repack_blocked_reason() is None)
        self._save_action.setEnabled(enabled)
        self._save_as_action.setEnabled(enabled)
        self._export_action.setEnabled(enabled)

    def _tools_usable(self, session: OpenProject) -> bool:
        """Check the alice.exe a saved project points at before using it.

        A project can be opened with any alice.exe, including one swapped
        out since it was created, and reviewing images works fine either
        way. Only the operations that actually shell out need this, so the
        check lives here rather than blocking the project from opening.
        """
        return self._alice_exe_usable(session.tools)

    def _repack_blocked_reason(self) -> str | None:
        """Why this project cannot be repacked, or None if it can.

        alice-tools cannot write .ald at all, so those archives are rebuilt
        by ald_repack instead of by `ar pack`. That path handles a single
        volume. A set split across fooA.ald and fooB.ald is extracted as one
        archive but would have to be rebuilt as several, which is not
        implemented, so it is refused here rather than part way through.
        """
        if self.session is None:
            return None
        if self.session.manifest.archive_format == ManifestFormat.AFA:
            return None
        volumes = find_sibling_volumes(self.session.manifest.resolved_archive_path())
        if len(volumes) > 1:
            names = ", ".join(v.name for v in volumes)
            return (
                f"This .ald archive is split across {len(volumes)} volumes ({names}).\n\n"
                "alice-tools extracts the whole set as one archive, so rebuilding "
                "just one volume would drop every file that lives in the others. "
                "Rebuilding split archives is not supported."
            )
        return None

    def _experimental_notice(self) -> str | None:
        """Said out loud for .ald projects, because that path is new.

        The AFA path has been used against real archives for a while. The
        ALD writer has not. It reproduces a real archive byte for byte and
        edited images come back out pixel for pixel, and a rebuilt archive
        has been played in Rance 02 far enough to see a few edited images
        render, which does at least prove the re-encoded entries work and
        not merely that the container parses.

        Worth naming rather than leaving implied. Those images were
        interface art, so no edited scene CG has been seen in game.
        """
        if self.session is None:
            return None
        if self.session.manifest.archive_format == ManifestFormat.AFA:
            return None
        return (
            "Rebuilding .ald archives is EXPERIMENTAL and lightly tested.\n\n"
            "alice-tools cannot write this format, so Alice Censor writes it itself. "
            "It has been checked against a real archive, which it reproduces byte for "
            "byte, and edited images come back out of it pixel for pixel. A rebuilt "
            "archive has also been played in Rance 02, where a few edited images on "
            "the save and load menu and the splash screens displayed correctly.\n\n"
            "That is the whole of the testing. No other game, no other archive, and "
            "only a handful of interface images rather than scene CGs. Expect the "
            "possibility of a missing or broken image somewhere nobody has looked "
            "yet.\n\n"
            "Keep the .orig-backup file. It is how you put things back, and it is also "
            "the source every rebuild reads from."
        )

    def _refresh_summary(self) -> None:
        assert self.session is not None
        project, manifest = self.session.project, self.session.manifest
        n_flagged = sum(1 for rec in project.images.values() if rec.status == ImageStatus.FLAGGED)
        lines = [
            f"Archive: {project.archive_path}",
            f"Format: {project.archive_format.upper()}  |  "
            f"Files: {len(manifest.entries)}  |  Flagged: {n_flagged}",
            f"Working folder: {project.extract_dir}",
        ]
        blocked = self._repack_blocked_reason()
        notice = blocked or self._experimental_notice()
        if blocked:
            lines.append("Repack unavailable for this archive format. See the log.")
        elif notice:
            lines.append("Rebuilding .ald is experimental. See the log.")
        self.summary_label.setText("\n".join(lines))
        self._set_project_actions_enabled(True)
        if notice and not self._warned_repack_notice:
            # Once per project, at the top of the log, so it is read before
            # hours of review rather than after. The log is easy to scroll
            # past though, and this decides whether the work about to be
            # done can be used at all, so it also interrupts.
            self.repack_button.setToolTip(notice)
            self.log(f"NOTE: {notice}")
            # Set before the modal opens. A modal runs its own event loop,
            # so a refresh triggered while it is up would otherwise stack a
            # second identical dialog behind the first.
            self._warned_repack_notice = True
            QMessageBox.warning(
                self,
                "Repack unavailable" if blocked else "Experimental .ald support",
                notice,
            )

    # ===== gallery

    def _refresh_gallery(self, result) -> None:
        assert self.session is not None
        project, manifest = self.session.project, self.session.manifest
        thumb_dir = Path(project.project_file).parent / ".thumbnails"
        model = GalleryModel(
            project,
            manifest,
            result.groups,
            thumb_dir,
            self.gallery_widget,
            sticker_resolver=make_sticker_resolver(project.sticker_dir),
        )
        self.gallery_widget.set_model(model)
        self.gallery_widget.set_auto_flag_available(
            manifest.archive_format == ManifestFormat.AFA
        )

    def _on_gallery_status_changed(self) -> None:
        if self.session is not None:
            self._autosave()

    def _on_auto_flag_requested(self) -> None:
        if self.session is None or not self.gallery_widget.model:
            return
        if self.session.manifest.archive_format != ManifestFormat.AFA:
            QMessageBox.information(
                self,
                "Not available",
                "Auto-flagging by naming pattern only works for .afa-style archives "
                "with descriptive names. .ald archives have no naming signal to "
                "detect explicit scenes from. Use the gallery to review manually.",
            )
            return

        images = self.session.project.images
        candidates = [
            path
            for path in find_explicit_by_naming(self.session.manifest)
            if path in images and images[path].status == ImageStatus.UNREVIEWED
        ]
        if not candidates:
            QMessageBox.information(
                self,
                "Auto-Flag Explicit Scenes",
                "No unreviewed images match the explicit-scene naming pattern.",
            )
            return

        reply = QMessageBox.question(
            self,
            "Auto-Flag Explicit Scenes",
            f"Found {len(candidates)} unreviewed image(s) matching the explicit-scene "
            f"naming pattern (H01-H13, 挿入/射精, etc.). Flag them all for censor?\n\n"
            f"Images you've already reviewed (clean / needs-edit / flagged) are left "
            f"untouched. This only affects unreviewed ones.",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        self.gallery_widget.model.set_status_for_paths(candidates, ImageStatus.FLAGGED)
        self.log(f"Auto-flagged {len(candidates)} image(s) matching the explicit-scene naming pattern.")

    def _on_clear_edits_requested(self, paths: list[str]) -> None:
        """Delete the censor layers on every selected image.

        Destructive and not undoable, since the app keeps no history, so
        it counts what would actually go and says so before doing it. The
        extracted images are never modified, so the cost of getting this
        wrong is redrawing regions rather than losing artwork.
        """
        model = self.gallery_widget.model
        if self.session is None or model is None:
            return

        with_layers = model.paths_with_layers(paths)
        if not with_layers:
            QMessageBox.information(
                self,
                "Nothing to remove",
                f"None of the {len(paths)} selected image(s) have any censor layers.",
            )
            return

        n_layers = sum(len(self.session.project.images[p].layers) for p in with_layers)
        reply = QMessageBox.question(
            self,
            "Remove edits",
            f"Delete {n_layers} censor layer(s) from {len(with_layers)} image(s)?\n\n"
            "This cannot be undone. Review status is left alone, and the extracted "
            "images are not touched, so anything removed has to be drawn again.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        cleared = model.clear_layers_for_paths(with_layers)
        self.log(f"Removed all censor layers from {cleared} image(s).")

    def _group_members(self, path: str) -> list[str]:
        """Every other image sharing `path`'s scene group. Uses each
        image's own effective_group (group override if set, else the
        suggested group_key) rather than the static GroupInfo.members list,
        so this stays correct if manual group merge/split is ever wired up."""
        if self.session is None:
            return []
        images = self.session.project.images
        record = images.get(path)
        group_key = record.effective_group if record else None
        if not group_key:
            return []
        return [
            other_path
            for other_path, other_record in images.items()
            if other_path != path and other_record.effective_group == group_key
        ]

    def _on_gallery_open_requested(self, path: str) -> None:
        if self.session is None:
            return
        project = self.session.project
        fs_path = resolve_fs_path(self.session.manifest.resolved_src_dir(), path)
        if not fs_path.exists():
            QMessageBox.warning(self, "Image not found", f"File missing on disk:\n{fs_path}")
            return

        record = project.images.setdefault(path, ImageRecord())
        dialog = RegionEditorDialog(
            fs_path,
            record,
            sticker_resolver=make_sticker_resolver(project.sticker_dir),
            parent=self,
            project=project,
            current_path=path,
            group_members=self._group_members(path),
        )
        # Batch apply commits as soon as it is confirmed, whether or not
        # THIS image's own edit is later saved or cancelled, so it persists
        # through here the moment it happens rather than waiting for the
        # dialog to close.
        dialog.batch_applied.connect(self._autosave)
        accepted = dialog.exec()
        # Those batch-applied images need refreshing either way.
        affected = set(dialog.batch_applied_paths)
        if accepted:
            self._autosave()
            affected.add(path)
            self.log(f"Saved {len(record.layers)} layer(s) for {path}")
        if dialog.batch_applied_paths:
            self.log(
                f"Batch-applied to {len(dialog.batch_applied_paths)} other image(s) "
                f"in the scene group."
            )
        if self.gallery_widget.model is not None:
            for changed_path in affected:
                self.gallery_widget.model.notify_layers_changed(changed_path)

    # ===== commands

    def run_extract(self) -> None:
        if self.session is None:
            return
        session = self.session
        native = self._use_native_formats()
        if not native and not self._tools_usable(session):
            return
        self.log("Re-extracting...")

        if native:
            def job(on_output):
                return extract_archive(
                    session.project.archive_path,
                    session.project.extract_dir,
                    Path(session.project.manifest_path),
                    on_progress=lambda path: on_output(f"extracting {path}"),
                )
        else:
            def job(on_output):
                return session.tools.extract(
                    session.project.archive_path,
                    session.project.extract_dir,
                    manifest_path=Path(session.project.manifest_path),
                    on_output=on_output,
                )

        self._run_worker(job, on_success=self._on_reextract_done)

    def _on_reextract_done(self, result) -> None:
        assert self.session is not None
        # Re-extract rewrites the manifest under an otherwise unchanged
        # project, so swap in a session carrying the new one.
        self.session = self.session.reloaded_manifest()
        scan = scan_and_sync(self.session.project, self.session.manifest)
        self._autosave()
        self.log(
            f"Re-extract complete. {len(scan.new_paths)} new file(s), "
            f"{len(scan.changed_paths)} changed, {len(scan.missing_paths)} missing."
        )
        self._refresh_summary()
        self._refresh_gallery(scan)

    def run_repack(self) -> None:
        if self.session is None:
            return
        # Belt and braces. The button is already disabled for these, but
        # rendering an export and copying the original archive are both
        # expensive and neither should start for a project that can never
        # reach `ar pack`.
        blocked = self._repack_blocked_reason()
        if blocked:
            QMessageBox.warning(self, "Repack not possible", blocked)
            return
        session = self.session
        native = self._use_native_formats()
        if not native and not self._tools_usable(session):
            return
        self.repack_button.setEnabled(False)

        if session.manifest.archive_format != ManifestFormat.AFA:
            self._run_ald_repack(session)
            return

        if self._use_native_formats():
            self._run_native_afa_repack(session)
            return

        self.log("Rendering censored images for export...")

        def job(on_output):
            return render_export(
                session.project,
                session.manifest,
                sticker_resolver=make_sticker_resolver(session.project.sticker_dir),
                on_progress=lambda path: on_output(f"rendering {path}"),
            )

        self._run_worker(job, on_success=self._on_export_rendered)

    def _run_native_afa_repack(self, session: OpenProject) -> None:
        """Rebuild an .afa here rather than through `ar pack`.

        Same shape as the .ald path. There is no export folder and no
        second subprocess stage, and every image without layers is copied
        out of the pristine backup as the bytes it already was.
        """
        reply = QMessageBox.question(
            self,
            "Rebuild .afa archive?",
            "Built in format support is on, so this rebuilds the archive here "
            "instead of running alice.exe ar pack.\n\n"
            "Rebuilding an untouched archive this way reproduces it byte for byte, "
            "and images you did not edit are copied without being decoded. Edited "
            "ones are written back as .qnt.\n\n"
            f"Overwrite {session.manifest.resolved_archive_path()} now?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            self.log("Rebuild cancelled.")
            self.repack_button.setEnabled(True)
            return

        self.log("Rebuilding .afa archive with built in format support...")

        def job(on_output):
            return repack_afa_in_place(
                session.project,
                session.manifest,
                extract_dir=session.project.extract_dir,
                sticker_resolver=make_sticker_resolver(session.project.sticker_dir),
                on_progress=lambda path: on_output(f"reading {path}"),
            )

        self._run_worker(job, on_success=self._on_native_afa_repack_done)

    def _on_native_afa_repack_done(self, result) -> None:
        self.repack_button.setEnabled(True)
        lines = [
            f"Rebuilt {result.archive_path}",
            f"{len(result.rebuilt_paths)} image(s) censored and re-encoded, "
            f"{result.copied_count} copied unchanged, byte for byte.",
        ]
        if result.converted_formats:
            lines.append(
                f"{len(result.converted_formats)} edited image(s) were written back "
                f"as .qnt because this build has no encoder for the format they "
                f"arrived in. The archive grows as a result."
            )
        for path, message in result.errors.items():
            self.log(f"  ERROR {path}: {message}")

        problems = verify_afa(result.archive_path, result)
        if result.errors or problems:
            lines.extend(problems)
            if result.errors:
                lines.append(f"{len(result.errors)} image(s) failed, listed above.")
            self.log("\n".join(lines))
            QMessageBox.warning(self, "Rebuilt with problems", "\n\n".join(lines))
        else:
            lines.append("Read back and verified.")
            self.log("\n".join(lines))
            QMessageBox.information(self, "Repack complete", "\n\n".join(lines))

    def _run_ald_repack(self, session: OpenProject) -> None:
        """Rebuild an .ald in place, since `ar pack` cannot write one.

        Unlike the AFA path there is no export folder and no second
        subprocess stage. Untouched entries are copied straight out of the
        pristine backup, so this is one pass over the archive that only
        decodes the images which actually have layers.
        """
        # Asked here rather than only logged at open time, because this is
        # the point where the game's own archive gets overwritten and the
        # experimental part stops being hypothetical.
        reply = QMessageBox.question(
            self,
            "Rebuild .ald archive?",
            f"{self._experimental_notice()}\n\n"
            f"Overwrite {self.session.manifest.resolved_archive_path()} now?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            self.log("Rebuild cancelled.")
            self.repack_button.setEnabled(True)
            return

        self.log("Rebuilding .ald archive (alice-tools cannot pack this format)...")

        def job(on_output):
            return repack_ald_in_place(
                session.project,
                session.manifest,
                session.tools,
                sticker_resolver=make_sticker_resolver(session.project.sticker_dir),
                on_progress=lambda path: on_output(f"reading {path}"),
            )

        self._run_worker(job, on_success=self._on_ald_repack_done)

    def _on_ald_repack_done(self, result) -> None:
        self.repack_button.setEnabled(True)
        archive_path = result.archive_path
        lines = [
            f"Rebuilt {archive_path}",
            f"{len(result.rebuilt_paths)} image(s) censored and re-encoded, "
            f"{result.copied_count} copied unchanged, byte for byte.",
        ]
        if result.converted_formats:
            changed = ", ".join(
                f"{path} was .{ext}" for path, ext in list(result.converted_formats.items())[:10]
            )
            lines.append(
                f"{len(result.converted_formats)} edited image(s) changed format to .qnt "
                f"because alice-tools has no encoder for the original, and their entry "
                f"was renamed to match. Worth checking these in game. {changed}"
            )
        for path, message in result.errors.items():
            self.log(f"  ERROR {path}: {message}")

        problems = verify_ald(archive_path, result)
        if result.errors or problems:
            lines.extend(problems)
            if result.errors:
                lines.append(f"{len(result.errors)} image(s) failed.")
            message = "\n\n".join(lines)
            self.log(message)
            QMessageBox.warning(self, "Repack finished with problems", message)
            return

        backup_path = archive_path.with_name(archive_path.name + ".orig-backup")
        if backup_path.exists():
            lines.append(f"(Pristine original backed up at:\n{backup_path})")
        lines.append(
            "Verified: every file number in the rebuilt archive is present and readable."
        )
        message = "\n\n".join(lines)
        self.log(message)
        QMessageBox.information(self, "Repack complete", message)

    def _on_export_rendered(self, export_result) -> None:
        assert self.session is not None
        tools = self.session.tools
        self.log(
            f"Export: {len(export_result.rendered_paths)} censored, "
            f"{len(export_result.copied_paths)} copied unchanged "
            f"({len(export_result.preserved_paths)} of them keeping their original bytes), "
            f"{len(export_result.errors)} error(s)."
        )
        if export_result.flattened_paths:
            self.log(
                f"{len(export_result.flattened_paths)} edited image(s) were stored as a "
                f"difference against another image and are written out whole instead. "
                f"Re-encoding a difference makes the game render it black."
            )
        for path, message in export_result.errors.items():
            self.log(f"  ERROR rendering {path}: {message}")

        try:
            export_manifest = parse_manifest(export_result.manifest_path)
        except Exception as e:  # noqa: BLE001
            self.repack_button.setEnabled(True)
            QMessageBox.critical(self, "Failed to prepare export manifest", str(e))
            return

        self.log("Repacking from the rendered export...")

        def job(on_output):
            # render_export curated this cache, seeding original bytes for
            # untouched files and removing the entry for everything it
            # re-rendered, so clearing it would throw that away.
            return tools.repack(export_manifest, clear_cache=False, on_output=on_output)

        self._run_worker(job, on_success=lambda result: self._on_repack_done(export_manifest))

    def _on_repack_done(self, export_manifest: Manifest) -> None:
        assert self.session is not None
        tools = self.session.tools
        # A clean exit code from `ar pack` is not actually proof the
        # archive is correct. A real alice-tools bug could, and for some
        # users did, silently corrupt a filename to "?" while still
        # exiting 0. Read the freshly-packed archive back and confirm
        # every expected file is really there under its expected name
        # before declaring success.
        self.log("Verifying repacked archive contents...")

        def job(on_output):
            return verify_archive_contents(
                tools, export_manifest.resolved_archive_path(), export_manifest
            )

        self._run_worker(job, on_success=lambda result: self._on_verify_done(result, export_manifest))

    def _on_verify_done(self, verify_result: VerifyResult, export_manifest: Manifest) -> None:
        self.repack_button.setEnabled(True)
        archive_path = export_manifest.resolved_archive_path()
        backup_path = archive_path.with_name(archive_path.name + ".orig-backup")

        lines = [f"Repack finished. Archive written to:\n{archive_path}"]
        if backup_path.exists():
            lines.append(f"(Pristine original backed up at:\n{backup_path})")

        if verify_result.ok:
            lines.append(
                f"Verified: all {verify_result.expected_count} file(s) present in the "
                f"repacked archive under their expected names. No corruption detected."
            )
            message = "\n\n".join(lines)
            self.log(message)
            QMessageBox.information(self, "Repack complete", message)
            return

        lines.append(
            f"⚠ VERIFICATION FAILED: expected {verify_result.expected_count} file(s), "
            f"found {verify_result.actual_count} in the repacked archive."
        )
        if verify_result.suspicious:
            lines.append(
                f"{len(verify_result.suspicious)} filename(s) contain '?', the signature of "
                f"a known alice-tools bug (github.com/nunuhara/alice-tools/issues/92) that can "
                f"silently corrupt certain characters while packing, even though 'ar pack' "
                f"reports success. Try updating alice.exe and re-extracting (not just "
                f"repacking) before distributing this archive.\nAffected: "
                + ", ".join(verify_result.suspicious[:10])
            )
        if verify_result.missing:
            lines.append(
                f"{len(verify_result.missing)} expected file(s) missing from the archive:\n"
                + ", ".join(verify_result.missing[:10])
            )
        message = "\n\n".join(lines)
        self.log(message)
        QMessageBox.warning(self, "Repack verification failed", message)

    # ===== worker plumbing

    def _run_worker(self, job, *, on_success) -> None:
        worker = CommandWorker(job, self)
        worker.output_line.connect(self.log)
        worker.finished_err.connect(lambda msg: self._on_worker_error(msg))
        worker.finished_ok.connect(on_success)

        # Guard against clobbering a newer worker. on_success (above) may
        # itself synchronously start a follow-up worker (run_repack chains
        # export -> repack this way) and reassign self._worker *before*
        # this cleanup slot for the *old* worker fires, since Qt delivers a
        # signal's connected slots in connection order. Only clear the
        # reference if it's still pointing at this same worker.
        def clear_if_current(_result=None, *, _worker=worker) -> None:
            if self._worker is _worker:
                self._worker = None

        worker.finished_ok.connect(clear_if_current)
        worker.finished_err.connect(clear_if_current)
        self._worker = worker  # keep a reference so it isn't GC'd mid-run
        worker.start()

    def _on_worker_error(self, message: str) -> None:
        self.log(f"ERROR: {message}")
        if self.session is not None:
            # A failed stage leaves whichever button started it disabled.
            self._set_project_actions_enabled(True)
        QMessageBox.critical(self, "alice-tools error", message)
