"""Settings for a detection pass.

Deliberately a dialog rather than a menu item that just runs. A scan takes
minutes over a whole archive, it decides what gets drawn over a few hundred
pictures, and how heavy each class should be is a matter of taste. All of
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
    QGroupBox,
    QLabel,
    QSpinBox,
    QVBoxLayout,
)

from ..autocensor import DEFAULT_PADDING, DEFAULT_STYLES
from ..detection import DEFAULT_THRESHOLD, LABELS
from ..project import LayerType

# What the three classes are called in front of a person.
LABEL_NAMES = {
    "nipple_f": "Nipples",
    "penis": "Penis",
    "pussy": "Vagina",
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

STRENGTHS = {
    LayerType.PIXELATE: ("block_size", 4, 64),
    LayerType.BLUR: ("radius", 2, 100),
}


class DetectDialog(QDialog):
    """Collects the scope, the threshold and a style per class."""

    def __init__(self, parent=None, *, image_count: int = 0):
        super().__init__(parent)
        self.setWindowTitle("Detect Censor Regions")
        self.setMinimumWidth(460)

        self.scope_combo = QComboBox()
        for key, text in SCOPES:
            self.scope_combo.addItem(text, key)

        self.threshold_spin = QDoubleSpinBox()
        self.threshold_spin.setRange(0.05, 0.95)
        self.threshold_spin.setSingleStep(0.05)
        self.threshold_spin.setValue(DEFAULT_THRESHOLD)
        self.threshold_spin.setToolTip(
            "Lower finds more and gets more wrong. Measured on a real project, "
            "0.25 found four fifths of the images that needed work and put a box "
            "on about one in forty that did not."
        )

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

        top = QFormLayout()
        top.addRow("Scan:", self.scope_combo)
        top.addRow("Confidence:", self.threshold_spin)
        top.addRow("Grow regions by:", self.padding_spin)
        top.addRow(self.deep_checkbox)

        self._class_rows = {}
        classes = QGroupBox("What to draw over each thing found")
        class_form = QFormLayout(classes)
        for label in LABELS:
            enabled = QCheckBox()
            enabled.setChecked(label in DEFAULT_STYLES)
            kind = QComboBox()
            for layer_type, text in OFFERED:
                kind.addItem(text, layer_type)
            strength = QSpinBox()
            strength.setRange(1, 100)

            default_type, default_params = DEFAULT_STYLES.get(
                label, (LayerType.PIXELATE, {"block_size": 12})
            )
            kind.setCurrentIndex(kind.findData(default_type))
            self._class_rows[label] = (enabled, kind, strength)
            self._sync_strength(label, default_params)
            kind.currentIndexChanged.connect(
                lambda _i, name=label: self._sync_strength(name, None)
            )

            enabled.setText("Include")
            holder = QGroupBox(LABEL_NAMES.get(label, label))
            inner = QFormLayout(holder)
            inner.addRow(enabled)
            inner.addRow("Censor with:", kind)
            inner.addRow("Strength:", strength)
            class_form.addRow(holder)

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

        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(classes)
        layout.addWidget(note)
        layout.addWidget(buttons)

    def _sync_strength(self, label: str, params: dict | None) -> None:
        """Point the strength box at whatever the chosen layer type calls it."""
        _, kind, strength = self._class_rows[label]
        layer_type = kind.currentData()
        key, low, high = STRENGTHS.get(layer_type, (None, 1, 100))
        strength.setEnabled(key is not None)
        if key is None:
            return
        strength.setRange(low, high)
        default = DEFAULT_STYLES.get(label, (None, {}))[1]
        strength.setValue(int((params or default).get(key, low * 3)))

    def styles(self) -> dict:
        """The per class choice, in the shape autocensor wants."""
        out = {}
        for label, (enabled, kind, strength) in self._class_rows.items():
            if not enabled.isChecked():
                continue
            layer_type = kind.currentData()
            key = STRENGTHS.get(layer_type, (None,))[0]
            params = {key: strength.value()} if key else {"color": "#000000"}
            out[label] = (layer_type, params)
        return out

    def settings(self) -> dict:
        return {
            "scope": self.scope_combo.currentData(),
            "threshold": self.threshold_spin.value(),
            "padding": self.padding_spin.value() / 100,
            "deep": self.deep_checkbox.isChecked(),
            "styles": self.styles(),
        }
