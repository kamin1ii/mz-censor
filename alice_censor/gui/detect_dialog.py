"""Settings for a detection pass.

Deliberately a dialog rather than a menu item that just runs. A scan takes
minutes over a whole archive, it decides what gets drawn over a few hundred
pictures, and how heavy each part should be is a matter of taste. All of
that is worth a moment's thought before it starts.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QLabel,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .. import detection
from ..autocensor import DEFAULT_PADDING, DEFAULT_STYLES
from ..project import LayerType

# What each kind is called in front of a person.
KIND_NAMES = {
    detection.NIPPLE: "Nipples",
    detection.PENIS: "Penis",
    detection.VAGINA: "Vagina",
    detection.ANUS: "Anus",
}

# The layer types worth putting over a detection. A sticker needs choosing
# per image, so it has no place in a bulk pass.
OFFERED = [
    (LayerType.PIXELATE, "Pixelate"),
    (LayerType.BLUR, "Blur"),
    (LayerType.SOLID, "Solid color"),
]

SCOPES = [
    ("all", "Every image in the project"),
    ("unreviewed", "Only images not yet reviewed"),
    ("flagged", "Only images flagged for censoring"),
]

# Which parameter each layer type calls its strength, and a sane range.
STRENGTHS = {
    LayerType.PIXELATE: ("block_size", 4, 64),
    LayerType.BLUR: ("radius", 2, 100),
}


class DetectDialog(QDialog):
    """Collects the scope, which models to use, and a style per kind."""

    def __init__(self, parent=None, *, image_count: int = 0, models=None):
        super().__init__(parent)
        self.setWindowTitle("Detect Censor Regions")
        self.setMinimumWidth(500)
        self._models = list(models if models is not None else detection.usable_models())

        self.scope_combo = QComboBox()
        for key, text in SCOPES:
            self.scope_combo.addItem(text, key)

        self.padding_spin = QSpinBox()
        self.padding_spin.setRange(0, 100)
        self.padding_spin.setSuffix(" %")
        self.padding_spin.setValue(round(DEFAULT_PADDING * 100))
        self.padding_spin.setToolTip(
            "How much bigger than the detected box to make the censor region."
        )

        self.deep_checkbox = QCheckBox("Look harder, four times slower")
        self.deep_checkbox.setToolTip(
            "Also scans the image in overlapping quarters. Finds roughly half "
            "again of what a plain scan misses, and roughly triples the number "
            "of wrong boxes, so it earns its keep on a small selection rather "
            "than a whole archive."
        )

        self.group_checkbox = QCheckBox("Give every image in a scene group the same regions")
        self.group_checkbox.setChecked(True)
        self.group_checkbox.setToolTip(
            "A scene group is one CG in its variations. Read on their own, the "
            "models can find something in one frame and miss it in the next, "
            "which leaves a scene censored in patches. This pools whatever was "
            "found anywhere in the group and gives it to all of them."
        )

        top = QFormLayout()
        top.addRow("Scan:", self.scope_combo)
        top.addRow("Grow regions by:", self.padding_spin)
        top.addRow(self.group_checkbox)
        top.addRow(self.deep_checkbox)

        models_box = QGroupBox("Models to use")
        models_grid = QGridLayout(models_box)
        models_grid.setVerticalSpacing(4)
        self._model_rows = {}
        if not self._models:
            models_grid.addWidget(QLabel("None downloaded yet."), 0, 0, 1, 3)
        for row, spec in enumerate(self._models):
            enabled = QCheckBox(spec.title)
            enabled.setChecked(True)
            enabled.setToolTip(f"{spec.note}\n{spec.licence}")
            threshold = QDoubleSpinBox()
            threshold.setRange(0.05, 0.95)
            threshold.setSingleStep(0.01)
            # Three places, because a model's own tuned figure can be
            # something like 0.238 and rounding it here would quietly
            # change the setting it publishes as its best.
            threshold.setDecimals(3)
            threshold.setValue(spec.threshold)
            threshold.setToolTip(
                "Lower finds more and gets more wrong. This is where the "
                "model's own accuracy peaks."
            )
            models_grid.addWidget(enabled, row, 0)
            models_grid.addWidget(QLabel("confidence"), row, 1)
            models_grid.addWidget(threshold, row, 2)
            self._model_rows[spec.key] = (enabled, threshold)

        self._kind_rows = {}
        kinds = QGroupBox("What to draw over each thing found")
        kind_grid = QGridLayout(kinds)
        kind_grid.setVerticalSpacing(4)
        for row, kind in enumerate(detection.KINDS):
            enabled = QCheckBox(KIND_NAMES.get(kind, kind))
            enabled.setChecked(kind in DEFAULT_STYLES)
            kind_combo = QComboBox()
            for layer_type, text in OFFERED:
                kind_combo.addItem(text, layer_type)
            strength = QSpinBox()
            strength.setRange(1, 100)

            default_type, default_params = DEFAULT_STYLES.get(
                kind, (LayerType.PIXELATE, {"block_size": 12})
            )
            kind_combo.setCurrentIndex(kind_combo.findData(default_type))
            self._kind_rows[kind] = (enabled, kind_combo, strength)
            self._sync_strength(kind, default_params)
            kind_combo.currentIndexChanged.connect(
                lambda _i, name=kind: self._sync_strength(name, None)
            )

            kind_grid.addWidget(enabled, row, 0)
            kind_grid.addWidget(kind_combo, row, 1)
            kind_grid.addWidget(QLabel("strength"), row, 2)
            kind_grid.addWidget(strength, row, 3)

        note = QLabel(
            f"{image_count} image(s) in scope. Nothing is applied on its own: "
            "every region found is added as a layer that starts switched off, "
            "for you to look at and enable."
        )
        note.setWordWrap(True)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Scan")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        inner_widget = QWidget()
        inner = QVBoxLayout(inner_widget)
        inner.setContentsMargins(0, 0, 0, 0)
        inner.addLayout(top)
        inner.addWidget(models_box)
        inner.addWidget(kinds)
        inner.addWidget(note)
        inner.addStretch(1)

        # Scrolled, because this has grown enough rows to run off a short
        # screen, and a dialog whose buttons are below the bottom of the
        # monitor cannot be used at all. The buttons stay outside it so they
        # are always reachable.
        scroll = QScrollArea()
        scroll.setWidget(inner_widget)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)

        layout = QVBoxLayout(self)
        layout.addWidget(scroll, stretch=1)
        layout.addWidget(buttons)
        self._fit_to_screen(inner_widget)

    def _fit_to_screen(self, contents) -> None:
        """Open big enough to show everything, but never taller than the screen.

        Qt will happily size a dialog past the bottom of the monitor, where
        the buttons cannot be reached and there is nothing to scroll.
        """
        wanted = contents.sizeHint().height() + 90
        available = self.screen().availableGeometry().height() if self.screen() else 900
        self.resize(self.width(), min(wanted, int(available * 0.85)))

    def _sync_strength(self, kind: str, params: dict | None) -> None:
        """Point the strength box at whatever the chosen layer type calls it."""
        _, kind_combo, strength = self._kind_rows[kind]
        layer_type = kind_combo.currentData()
        key, low, high = STRENGTHS.get(layer_type, (None, 1, 100))
        strength.setEnabled(key is not None)
        if key is None:
            return
        strength.setRange(low, high)
        default = DEFAULT_STYLES.get(kind, (None, {}))[1]
        strength.setValue(int((params or default).get(key, low * 3)))

    def chosen_models(self) -> list:
        return [spec for spec in self._models
                if self._model_rows[spec.key][0].isChecked()]

    def thresholds(self) -> dict:
        return {key: threshold.value()
                for key, (enabled, threshold) in self._model_rows.items()
                if enabled.isChecked()}

    def styles(self) -> dict:
        """The per kind choice, in the shape autocensor wants."""
        out = {}
        for kind, (enabled, kind_combo, strength) in self._kind_rows.items():
            if not enabled.isChecked():
                continue
            layer_type = kind_combo.currentData()
            key = STRENGTHS.get(layer_type, (None,))[0]
            params = {key: strength.value()} if key else {"color": "#000000"}
            out[kind] = (layer_type, params)
        return out

    def settings(self) -> dict:
        return {
            "scope": self.scope_combo.currentData(),
            "models": self.chosen_models(),
            "thresholds": self.thresholds(),
            "padding": self.padding_spin.value() / 100,
            "deep": self.deep_checkbox.isChecked(),
            "keep_groups_consistent": self.group_checkbox.isChecked(),
            "styles": self.styles(),
        }
