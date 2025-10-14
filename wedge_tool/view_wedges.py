"""Interactive wedge viewer.

Loads a PNG, extracts the embedded wedge_config metadata (new adhoc format or
legacy WEDGE_string), builds controls for each wedge parameter, and switches the
displayed image dynamically as selections change. Filenames are constructed to
match the current submitter's naming scheme.
"""

import os
import sys
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QPixmap
from PyQt5.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QLabel,
    QMainWindow,
    QPushButton,
    QScrollArea,
    QSlider,
    QVBoxLayout,
    QWidget,
)
import qdarkstyle


def stringify_value(value: Any) -> str:
    """Return a readable string for a value, trimming float noise.

    Floats are formatted with up to 10 significant digits and any trailing
    decimal dot is removed to better match filenames produced by the submitter.
    """
    if isinstance(value, float):
        s = f"{value:.10g}".rstrip(".")
        return s
    return str(value)


def sanitize_value(value: Any) -> str:
    """Convert a value into a filename-safe token for image lookup."""
    text = stringify_value(value)
    for ch in (" ", "/", "\\", ":", ",", ";", '"', "'"):
        text = text.replace(ch, "_")
    return text


def expand_minmax(min_val: float, max_val: float, step: float) -> List[Any]:
    """Generate a numeric sequence for a min/max/step wedge.

    Coerces values that are effectively integers to int to mirror the
    submitter’s filename formatting.
    """
    if step == 0:
        return []
    values: List[Any] = []
    current = float(min_val)
    stop = float(max_val)
    stepf = float(step)
    # Protect against infinite loops
    guard = 0
    while (current <= stop + abs(stepf) * 1e-9) if stepf > 0 else (current >= stop - abs(stepf) * 1e-9):
        # Coerce near-integers to int to match submitter naming
        rounded = round(current, 10)
        if abs(rounded - round(rounded)) < 1e-10:
            values.append(int(round(rounded)))
        else:
            values.append(rounded)
        current += stepf
        guard += 1
        if guard > 100000:
            break
    return values


@dataclass
class WedgeEntry:
    """Description of a single wedge axis extracted from metadata.

    Attributes:
        node: Node title where the parameter exists.
        param: Parameter name within the node.
        values: Expanded list of values for this axis.
        type: Wedge type label ("minmax" or "explicit").
    """
    node: str
    param: str
    values: List[Any]
    type: str  # "minmax" | "explicit"


class WedgeViewer(QMainWindow):
    def __init__(self) -> None:
        """Initialize the viewer UI and state containers."""
        super().__init__()
        self.setWindowTitle("Wedge Viewer")

        self.image_label = QLabel("Load a PNG to start", alignment=Qt.AlignCenter)
        self.image_label.setObjectName("imageLabel")
        self.image_label.setScaledContents(False)

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setWidget(self.image_label)

        self.load_button = QPushButton("Load Image")
        self.load_button.clicked.connect(self.load_image)

        self.slider_container = QWidget()
        self.slider_layout = QVBoxLayout()
        self.slider_container.setLayout(self.slider_layout)

        # Keyed by (node, param) → control data
        self.controls: Dict[Tuple[str, str], Dict[str, Any]] = {}
        self.filename_prefix: str = ""
        self.folder_path: str = ""
        self.current_pixmap: Optional[QPixmap] = None

        central_layout = QVBoxLayout()
        central_layout.addWidget(self.load_button)
        central_layout.addWidget(self.scroll_area)
        central_layout.addWidget(self.slider_container)

        central_widget = QWidget()
        central_widget.setLayout(central_layout)
        self.setCentralWidget(central_widget)

    # -----------------------------
    # Loading and metadata parsing
    # -----------------------------
    def load_image(self) -> None:
        """Open a file picker, read the image, extract wedge config, build UI."""
        default_directory = "D:/AI/ComfyUI/output"
        if not os.path.isdir(default_directory):
            default_directory = ""

        file_path, _ = QFileDialog.getOpenFileName(
            self, "Open Image", default_directory, "PNG Images (*.png)"
        )
        if not file_path:
            return

        self.folder_path = os.path.dirname(file_path)

        try:
            with Image.open(file_path) as img:
                prompt_json = img.info.get("prompt")
                if not prompt_json:
                    raise ValueError("No 'prompt' metadata found in image.")
                workflow = json.loads(prompt_json)
        except Exception as e:
            self.image_label.setText(f"Failed to read image: {e}")
            return

        wedge_config = self._extract_wedge_config(workflow)
        if wedge_config is None:
            self.image_label.setText("No wedge metadata found in image.")
            return

        self.filename_prefix = wedge_config.get("filename_prefix", "image")
        entries = self._flatten_wedges(wedge_config.get("param_wedges", {}))

        # Clear old UI elements
        for i in reversed(range(self.slider_layout.count())):
            w = self.slider_layout.itemAt(i).widget()
            if w is not None:
                w.setParent(None)
        self.controls.clear()

        # Build controls from entries
        for entry in entries:
            label_text = f"{entry.node}.{entry.param}"
            values = entry.values

            if entry.type == "explicit" and all(isinstance(v, str) for v in values):
                label = QLabel(f"{label_text}: {values[0] if values else ''}")
                dropdown = QComboBox()
                dropdown.addItems(values)
                dropdown.currentIndexChanged.connect(
                    self._make_dropdown_callback(entry.node, entry.param, dropdown, values, label)
                )
                self.slider_layout.addWidget(label)
                self.slider_layout.addWidget(dropdown)
                self.controls[(entry.node, entry.param)] = {
                    "type": "dropdown",
                    "widget": dropdown,
                    "label": label,
                    "values": values,
                }
                continue

            # Slider path (numeric explicit or minmax)
            slider = QSlider(Qt.Horizontal)
            slider.setMinimum(0)
            slider.setMaximum(max(0, len(values) - 1))
            slider.setTickInterval(1)
            slider.setValue(0)
            slider.setSingleStep(1)

            label = QLabel(f"{label_text}: {values[0] if values else ''}")
            slider.valueChanged.connect(
                self._make_slider_callback(entry.node, entry.param, slider, label, values)
            )

            self.slider_layout.addWidget(label)
            self.slider_layout.addWidget(slider)
            self.controls[(entry.node, entry.param)] = {
                "type": "slider",
                "widget": slider,
                "label": label,
                "values": values,
            }

        self.update_image_display()

    def _extract_wedge_config(self, workflow: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Return the wedge_config dict from workflow metadata if present.

        Searches in order:
        1) New adhoc metadata: _NULL_ADHOC_METADATA.inputs._adhoc_metadata.wedge_config
        2) Legacy WEDGE_string node: JSON string stored at inputs.value
        """
        try:
            # New: _NULL_ADHOC_METADATA with inputs._adhoc_metadata.wedge_config
            for node in workflow.values():
                meta = node.get("_meta", {})
                if meta.get("title") == "_NULL_ADHOC_METADATA":
                    inputs = node.get("inputs", {})
                    adhoc = inputs.get("_adhoc_metadata", {})
                    wc = adhoc.get("wedge_config")
                    if isinstance(wc, dict):
                        return wc
        except Exception:
            pass

        try:
            # Legacy: PrimitiveString titled WEDGE_string with JSON string in inputs.value
            for node in workflow.values():
                meta = node.get("_meta", {})
                if meta.get("title") == "WEDGE_string":
                    inputs = node.get("inputs", {})
                    value = inputs.get("value")
                    if isinstance(value, str):
                        return json.loads(value)
        except Exception:
            pass

        return None

    def _flatten_wedges(self, param_wedges: Dict[str, List[List[Any]]]) -> List[WedgeEntry]:
        """Transform param_wedges dict into a flat list of WedgeEntry objects."""
        entries: List[WedgeEntry] = []
        for node, items in (param_wedges or {}).items():
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, list) or len(item) != 3:
                    continue
                param, values_spec, wtype = item
                if wtype == "explicit":
                    values = list(values_spec)
                elif wtype == "minmax":
                    try:
                        min_v, max_v, step = values_spec
                        values = expand_minmax(min_v, max_v, step)
                    except Exception:
                        values = []
                else:
                    values = []
                entries.append(WedgeEntry(node=node, param=param, values=values, type=wtype))
        return entries

    # -----------------------------
    # Control callbacks
    # -----------------------------
    def _make_slider_callback(self, node: str, param: str, slider: QSlider, label: QLabel, values: List[Any]):
        """Return a slot that updates label and image when a slider changes."""
        def callback(_index: int) -> None:
            idx = slider.value()
            val = values[idx] if 0 <= idx < len(values) else None
            label.setText(f"{node}.{param}: {val}")
            self.update_image_display()
        return callback

    def _make_dropdown_callback(self, node: str, param: str, dropdown: QComboBox, values: List[Any], label: QLabel):
        """Return a slot that updates label and image when a dropdown changes."""
        def callback(_index: int) -> None:
            idx = dropdown.currentIndex()
            val = values[idx] if 0 <= idx < len(values) else None
            label.setText(f"{node}.{param}: {val}")
            self.update_image_display()
        return callback

    # -----------------------------
    # Image swapping
    # -----------------------------
    def update_image_display(self) -> None:
        """Build the expected filename from current controls and display the image."""
        if not self.folder_path:
            return

        # Build combination dict for deterministic ordering (node, param sorted)
        combo: Dict[str, Dict[str, Any]] = {}
        for (node, param), data in self.controls.items():
            values = data.get("values", [])
            if not values:
                continue
            if data.get("type") == "slider":
                idx = data["widget"].value()
            else:
                idx = data["widget"].currentIndex()
            idx = max(0, min(idx, len(values) - 1))
            combo.setdefault(node, {})[param] = values[idx]

        parts = [self.filename_prefix]
        for node in sorted(combo.keys()):
            for param in sorted(combo[node].keys()):
                value = combo[node][param]
                parts.append(f"{node}-{param}-{sanitize_value(value)}")

        base = "__".join(parts)
        candidate = f"{base}_00001_.png"
        full_path = os.path.join(self.folder_path, candidate)

        if not os.path.exists(full_path):
            # Fallback: try any sequential suffix
            stem = base + "_"
            for i in range(1, 100):
                alt = f"{stem}{i:05d}_.png"
                alt_path = os.path.join(self.folder_path, alt)
                if os.path.exists(alt_path):
                    full_path = alt_path
                    break

        if os.path.exists(full_path):
            self.current_pixmap = QPixmap(full_path)
            self.resize_image_to_fit()
        else:
            self.image_label.setPixmap(QPixmap())
            self.image_label.setText(f"Image not found:\n{os.path.basename(full_path)}")

    def resize_image_to_fit(self) -> None:
        """Scale the current pixmap to fit within the scroll viewport."""
        if not self.current_pixmap or self.current_pixmap.isNull():
            return
        scroll_size = self.scroll_area.viewport().size()
        scaled_pixmap = self.current_pixmap.scaled(
            scroll_size, Qt.KeepAspectRatio, Qt.SmoothTransformation
        )
        self.image_label.setPixmap(scaled_pixmap)

    def resizeEvent(self, event):  # type: ignore[override]
        """Ensure the image resizes responsively with the window."""
        super().resizeEvent(event)
        self.resize_image_to_fit()


def main() -> None:
    """Run the PyQt application with dark styling."""
    app = QApplication(sys.argv)
    app.setStyleSheet(qdarkstyle.load_stylesheet_pyqt5())
    viewer = WedgeViewer()
    viewer.resize(1000, 700)
    viewer.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
