"""PyQt UI for the wedge submitter with structured fields.

This UI provides dedicated fields for the core configuration keys and
dynamic editors for `param_overrides` and `param_wedges`. It can save the
assembled configuration to disk and launch the submitter by passing the
assembled JSON over stdin (no need to resave).
"""

import json
import os
import subprocess
import sys
from typing import Optional, Any, Dict, List, Callable

import qdarkstyle
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QApplication,
    QFileDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
    QHBoxLayout,
    QLineEdit,
    QCheckBox,
    QComboBox,
    QGroupBox,
    QFormLayout,
    QSizePolicy,
)

try:
    # Optional import; UI falls back to raw JSON if unavailable
    from comfyman import ComfyWorkflow  # type: ignore
except Exception:  # pragma: no cover - optional at runtime
    ComfyWorkflow = None  # type: ignore


def _infer_typed_value(text: str) -> Any:
    """Best-effort conversion of a string to bool/int/float/str.

    - 'true'/'false' (case-insensitive) -> bool
    - int-like -> int
    - float-like -> float
    - otherwise -> original string
    """
    s = text.strip()
    if s.lower() in ("true", "false"):
        return s.lower() == "true"
    try:
        if s.lower().startswith("0x"):
            return int(s, 16)
        return int(s)
    except Exception:
        pass
    try:
        return float(s)
    except Exception:
        pass
    return s


class NodeComboBox(QComboBox):
    """Combo that populates with node titles when opened."""

    def __init__(self, fetch_nodes: Callable[[], List[str]], initial: str = "") -> None:
        super().__init__()
        self._fetch_nodes = fetch_nodes
        self.setEditable(True)
        self.setMinimumWidth(160)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        if initial:
            self.setEditText(initial)

    def showPopup(self) -> None:  # type: ignore[override]
        try:
            titles = self._fetch_nodes() or []
        except Exception:
            titles = []
        current = self.currentText().strip()
        self.clear()
        if titles:
            self.addItems(sorted(dict.fromkeys(titles)))
        if current and current not in [self.itemText(i) for i in range(self.count())]:
            self.insertItem(0, current)
            self.setCurrentIndex(0)
        super().showPopup()


class ParamComboBox(QComboBox):
    """Combo that populates with parameter names for a given node title."""

    def __init__(self, fetch_params_for_node: Callable[[str], List[str]], get_node_title: Callable[[], str], initial: str = "") -> None:
        super().__init__()
        self._fetch_params_for_node = fetch_params_for_node
        self._get_node_title = get_node_title
        self.setEditable(True)
        self.setMinimumWidth(160)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        if initial:
            self.setEditText(initial)

    def showPopup(self) -> None:  # type: ignore[override]
        node_title = (self._get_node_title() or "").strip()
        try:
            params = self._fetch_params_for_node(node_title) if node_title else []
        except Exception:
            params = []
        current = self.currentText().strip()
        self.clear()
        if params:
            self.addItems(sorted(dict.fromkeys(params)))
        if current and current not in [self.itemText(i) for i in range(self.count())]:
            self.insertItem(0, current)
            self.setCurrentIndex(0)
        super().showPopup()

class OverrideRow(QWidget):
    """A row to capture one param override: node, param, value."""

    def __init__(
        self,
        node_fetch: Callable[[], List[str]],
        param_fetch_for_node: Callable[[str], List[str]],
        node: str = "",
        param: str = "",
        value: Any = "",
    ) -> None:
        super().__init__()
        self.node_combo = NodeComboBox(node_fetch, initial=node)
        # Param dropdown depends on current node text
        self.param_combo = ParamComboBox(param_fetch_for_node, lambda: self.node_combo.currentText(), initial=param)
        self.value_edit = QLineEdit(json.dumps(value) if isinstance(value, (dict, list)) else str(value))
        self.remove_btn = QPushButton("Remove")
        # Clear param when node changes
        self.node_combo.currentTextChanged.connect(lambda _txt: self.param_combo.setEditText(""))

        layout = QHBoxLayout()
        layout.addWidget(QLabel("Node:"))
        layout.addWidget(self.node_combo)
        layout.addWidget(QLabel("Param:"))
        layout.addWidget(self.param_combo)
        layout.addWidget(QLabel("Value:"))
        layout.addWidget(self.value_edit)
        layout.addWidget(self.remove_btn)
        self.setLayout(layout)

    def to_entry(self) -> Optional[Dict[str, Any]]:
        node = self.node_combo.currentText().strip()
        param = self.param_combo.currentText().strip()
        raw = self.value_edit.text().strip()
        if not node or not param:
            return None
        try:
            value = json.loads(raw)
        except Exception:
            value = _infer_typed_value(raw)
        return {"node": node, "param": param, "value": value}


class WedgeRow(QWidget):
    """A row to capture one param wedge: node, param, type, values.

    Supports types:
      - explicit: comma-separated list field
      - minmax: three fields for min, max, step
    """

    def __init__(
        self,
        node_fetch: Callable[[], List[str]],
        param_fetch_for_node: Callable[[str], List[str]],
        node: str = "",
        param: str = "",
        wedge_type: str = "minmax",
        values: Any = None,
    ) -> None:
        super().__init__()
        values = values or []
        self.node_combo = NodeComboBox(node_fetch, initial=node)
        self.param_combo = ParamComboBox(param_fetch_for_node, lambda: self.node_combo.currentText(), initial=param)
        self.type_combo = QComboBox()
        self.type_combo.addItems(["minmax", "explicit"])
        if wedge_type in ("explicit", "minmax"):
            self.type_combo.setCurrentText(wedge_type)
        # Clear param when node changes
        self.node_combo.currentTextChanged.connect(lambda _txt: self.param_combo.setEditText(""))

        self.explicit_values_edit = QLineEdit(
            ", ".join(str(v) for v in values) if wedge_type == "explicit" and isinstance(values, list) else ""
        )
        mm_min = mm_max = mm_step = ""
        if wedge_type == "minmax" and isinstance(values, list) and len(values) == 3:
            mm_min, mm_max, mm_step = [str(x) for x in values]
        self.min_edit = QLineEdit(mm_min)
        self.max_edit = QLineEdit(mm_max)
        self.step_edit = QLineEdit(mm_step)

        self.remove_btn = QPushButton("Remove")

        layout = QHBoxLayout()
        layout.addWidget(QLabel("Node:"))
        layout.addWidget(self.node_combo)
        layout.addWidget(QLabel("Param:"))
        layout.addWidget(self.param_combo)
        layout.addWidget(QLabel("Type:"))
        layout.addWidget(self.type_combo)

        self.explicit_label = QLabel("Values (comma-separated):")
        layout.addWidget(self.explicit_label)
        layout.addWidget(self.explicit_values_edit)

        self.min_label = QLabel("Min:")
        self.max_label = QLabel("Max:")
        self.step_label = QLabel("Step:")
        layout.addWidget(self.min_label)
        layout.addWidget(self.min_edit)
        layout.addWidget(self.max_label)
        layout.addWidget(self.max_edit)
        layout.addWidget(self.step_label)
        layout.addWidget(self.step_edit)

        layout.addWidget(self.remove_btn)
        self.setLayout(layout)

        self.type_combo.currentTextChanged.connect(self._refresh_type_visibility)
        self._refresh_type_visibility()

    def _refresh_type_visibility(self) -> None:
        is_explicit = self.type_combo.currentText() == "explicit"
        self.explicit_label.setVisible(is_explicit)
        self.explicit_values_edit.setVisible(is_explicit)
        self.min_label.setVisible(not is_explicit)
        self.min_edit.setVisible(not is_explicit)
        self.max_label.setVisible(not is_explicit)
        self.max_edit.setVisible(not is_explicit)
        self.step_label.setVisible(not is_explicit)
        self.step_edit.setVisible(not is_explicit)

    def to_entry(self) -> Optional[Dict[str, Any]]:
        node = self.node_combo.currentText().strip()
        param = self.param_combo.currentText().strip()
        wtype = self.type_combo.currentText().strip()
        if not node or not param or wtype not in ("explicit", "minmax"):
            return None
        if wtype == "explicit":
            raw = self.explicit_values_edit.text().strip()
            if not raw:
                return None
            values: List[Any] = []
            for token in [t.strip() for t in raw.split(",") if t.strip() != ""]:
                try:
                    values.append(json.loads(token))
                except Exception:
                    values.append(_infer_typed_value(token))
            if not values:
                return None
            return {"node": node, "param": param, "type": "explicit", "values": values}
        else:
            a = self.min_edit.text().strip()
            b = self.max_edit.text().strip()
            c = self.step_edit.text().strip()
            if not a or not b or not c:
                return None
            try:
                min_v = float(a)
                max_v = float(b)
                step_v = float(c)
            except Exception:
                return None
            if step_v == 0:
                return None
            return {"node": node, "param": param, "type": "minmax", "values": [min_v, max_v, step_v]}


class WedgeSubmitterUI(QWidget):
    """UI wrapper for selecting, editing, and submitting wedge configs.

    Workflow:
    - Pick a workflow JSON file.
    - If a `wedge_config.json` sits next to the workflow, it is preferred.
    - Otherwise, specify a config filename/path or create one via the UI.
    - Optionally save changes to `wedge_config.json`.
    - Launch the submitter in a background process (sends config over stdin).
    """

    def __init__(self) -> None:
        super().__init__()
        self.selected_workflow: Optional[str] = None
        self.wedge_config_path: Optional[str] = None
        self.override_rows: List[OverrideRow] = []
        self.wedge_rows: List[WedgeRow] = []
        self._init_ui()

    def _init_ui(self) -> None:
        self.setWindowTitle("Wedge Submitter UI")
        self.setGeometry(120, 120, 900, 800)

        layout = QVBoxLayout()

        # Workflow selector is embedded next to the field below

        # --- Argument controls ---
        # json-folder deprecated: paths are resolved from workflow path

        # workflow-filename
        row_workflow = QHBoxLayout()
        row_workflow.addWidget(QLabel("Workflow Filename:"))
        self.workflow_edit = QLineEdit("")
        self.workflow_edit.setToolTip(
            "Full path to the workflow JSON file."
        )
        row_workflow.addWidget(self.workflow_edit)
        self.workflow_browse_btn = QPushButton("Select Workflow JSON")
        self.workflow_browse_btn.clicked.connect(self._pick_workflow)
        row_workflow.addWidget(self.workflow_browse_btn)
        layout.addLayout(row_workflow)

        # config-filename
        row_config = QHBoxLayout()
        row_config.addWidget(QLabel("Config Filename:"))
        self.config_edit = QLineEdit("wedge_config.json")
        self.config_edit.setToolTip(
            "Config filename or path. If a wedge_config.json exists next to the workflow, it will be used instead."
        )
        row_config.addWidget(self.config_edit)
        self.config_browse_btn = QPushButton("Select Config JSON")
        self.config_browse_btn.clicked.connect(self._pick_config)
        row_config.addWidget(self.config_browse_btn)
        layout.addLayout(row_config)

        # --- Config (top-level + overrides + wedges) ---
        config_group = QGroupBox("Config")
        config_layout = QVBoxLayout()
        # Tighten spacing/margins to reduce gap before overrides
        config_layout.setSpacing(4)
        config_layout.setContentsMargins(8, 8, 8, 8)

        top_form = QFormLayout()
        top_form.setHorizontalSpacing(8)
        top_form.setVerticalSpacing(4)
        top_form.setContentsMargins(0, 0, 0, 0)
        self.output_folder_edit = QLineEdit("")
        self.filename_prefix_edit = QLineEdit("")
        self.url_edit = QLineEdit("127.0.0.1:8188")
        self.url_edit.setPlaceholderText("127.0.0.1:8188")
        top_form.addRow(QLabel("output_folder"), self.output_folder_edit)
        top_form.addRow(QLabel("filename_prefix"), self.filename_prefix_edit)
        top_form.addRow(QLabel("url"), self.url_edit)
        config_layout.addLayout(top_form)

        # --- Overrides section ---
        over_group = QGroupBox("param_overrides")
        over_layout = QVBoxLayout()
        over_layout.setSpacing(4)
        over_layout.setContentsMargins(6, 6, 6, 6)
        self.overrides_container = QVBoxLayout()
        over_layout.addLayout(self.overrides_container)
        self.add_override_btn = QPushButton("+ Add Override")
        self.add_override_btn.clicked.connect(self._add_override_row)
        over_layout.addWidget(self.add_override_btn)
        over_group.setLayout(over_layout)
        config_layout.addWidget(over_group)

        # --- Wedges section ---
        wedge_group = QGroupBox("param_wedges")
        wedge_layout = QVBoxLayout()
        wedge_layout.setSpacing(4)
        wedge_layout.setContentsMargins(6, 6, 6, 6)
        self.wedges_container = QVBoxLayout()
        wedge_layout.addLayout(self.wedges_container)
        self.add_wedge_btn = QPushButton("+ Add Wedge")
        self.add_wedge_btn.clicked.connect(self._add_wedge_row)
        wedge_layout.addWidget(self.add_wedge_btn)
        wedge_group.setLayout(wedge_layout)
        config_layout.addWidget(wedge_group)

        # Export button (inside the Config section)
        self.export_button = QPushButton("Export Config", self)
        self.export_button.clicked.connect(self._export_config)
        self.export_button.setEnabled(True)
        config_layout.addWidget(self.export_button)

        config_group.setLayout(config_layout)
        layout.addWidget(config_group)

        # Advanced settings toggle and group (hidden by default)
        self.advanced_toggle = QPushButton("Show Advanced Settings")
        self.advanced_toggle.setCheckable(True)
        self.advanced_toggle.setChecked(False)
        self.advanced_toggle.clicked.connect(self._toggle_advanced)
        layout.addWidget(self.advanced_toggle)

        self.advanced_group = QGroupBox("Advanced Settings")
        advanced_layout = QVBoxLayout()

        # output-node
        row_outnode = QHBoxLayout()
        row_outnode.addWidget(QLabel("Output Node:"))
        self.output_node_edit = QLineEdit("")
        self.output_node_edit.setPlaceholderText("(AUTO)")
        row_outnode.addWidget(self.output_node_edit)
        advanced_layout.addLayout(row_outnode)

        # limit
        row_limit = QHBoxLayout()
        row_limit.addWidget(QLabel("Limit:"))
        self.limit_edit = QLineEdit("")
        self.limit_edit.setPlaceholderText("(NO LIMIT)")
        row_limit.addWidget(self.limit_edit)
        advanced_layout.addLayout(row_limit)

        # flags (stacked)
        flags_col = QVBoxLayout()
        self.confirm_cb = QCheckBox("Confirm submission")
        self.confirm_cb.setChecked(True)
        self.dry_run_cb = QCheckBox("Dry run")
        self.print_cb = QCheckBox("Print combinations")
        self.stream_cb = QCheckBox("Stream")
        flags_col.addWidget(self.confirm_cb)
        flags_col.addWidget(self.dry_run_cb)
        flags_col.addWidget(self.print_cb)
        flags_col.addWidget(self.stream_cb)
        advanced_layout.addLayout(flags_col)

        # log level
        row_log = QHBoxLayout()
        row_log.addWidget(QLabel("--log-level:"))
        self.log_level_combo = QComboBox()
        self.log_level_combo.addItems(["CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"])
        self.log_level_combo.setCurrentText("INFO")
        row_log.addWidget(self.log_level_combo)
        advanced_layout.addLayout(row_log)

        self.advanced_group.setLayout(advanced_layout)
        self.advanced_group.setVisible(False)
        layout.addWidget(self.advanced_group)

        self.run_button = QPushButton("Submit Wedges", self)
        self.run_button.clicked.connect(self._run_submitter)
        self.run_button.setEnabled(False)
        layout.addWidget(self.run_button)

        self.setLayout(layout)

    # -----------------------------
    # Interactions
    # -----------------------------
    def _toggle_advanced(self) -> None:
        visible = self.advanced_toggle.isChecked()
        self.advanced_group.setVisible(visible)
        self.advanced_toggle.setText("Hide Advanced Settings" if visible else "Show Advanced Settings")

    # -----------------------------
    # Interactions
    # -----------------------------
    def _pick_workflow(self) -> None:
        """Ask user for a workflow JSON and load its adjacent config if present."""
        path, _ = QFileDialog.getOpenFileName(self, "Select workflow JSON", "", "JSON Files (*.json);;All Files (*)")
        if not path:
            return

        self.selected_workflow = path
        # Populate controls
        self.workflow_edit.setText(path)
        if not self.config_edit.text().strip():
            self.config_edit.setText("wedge_config.json")

        folder = os.path.dirname(path)
        workflow_path = path
        wedge_config_path = os.path.join(folder, self.config_edit.text().strip())

        try:
            # Prefer adjacent wedge_config.json next to the workflow
            default_adjacent = os.path.join(folder, "wedge_config.json")
            if os.path.exists(default_adjacent):
                # Only clear/populate when a config file is actually found
                self._clear_override_rows()
                self._clear_wedge_rows()
                with open(default_adjacent, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                self.wedge_config_path = default_adjacent
                # Top-level
                self.output_folder_edit.setText(str(cfg.get("output_folder", "")))
                self.filename_prefix_edit.setText(str(cfg.get("filename_prefix", "")))
                self.url_edit.setText(str(cfg.get("url", "")))
                # Overrides
                pov = cfg.get("param_overrides", {}) or {}
                if isinstance(pov, dict):
                    for node, entries in pov.items():
                        if not isinstance(entries, list):
                            continue
                        for entry in entries:
                            if isinstance(entry, list) and len(entry) == 2:
                                self._add_override_row(node=node, param=str(entry[0]), value=entry[1])
                # Wedges
                pw = cfg.get("param_wedges", {}) or {}
                if isinstance(pw, dict):
                    for node, entries in pw.items():
                        if not isinstance(entries, list):
                            continue
                        for entry in entries:
                            if isinstance(entry, list) and len(entry) == 3:
                                param, vals, wtype = entry
                                self._add_wedge_row(node=node, param=str(param), wedge_type=str(wtype), values=vals)
            elif os.path.exists(wedge_config_path):
                # Only clear/populate when a config file is actually found
                self._clear_override_rows()
                self._clear_wedge_rows()
                with open(wedge_config_path, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                self.wedge_config_path = wedge_config_path
                # Top-level
                self.output_folder_edit.setText(str(cfg.get("output_folder", "")))
                self.filename_prefix_edit.setText(str(cfg.get("filename_prefix", "")))
                self.url_edit.setText(str(cfg.get("url", "")))
                # Overrides
                pov = cfg.get("param_overrides", {}) or {}
                if isinstance(pov, dict):
                    for node, entries in pov.items():
                        if not isinstance(entries, list):
                            continue
                        for entry in entries:
                            if isinstance(entry, list) and len(entry) == 2:
                                self._add_override_row(node=node, param=str(entry[0]), value=entry[1])
                # Wedges
                pw = cfg.get("param_wedges", {}) or {}
                if isinstance(pw, dict):
                    for node, entries in pw.items():
                        if not isinstance(entries, list):
                            continue
                        for entry in entries:
                            if isinstance(entry, list) and len(entry) == 3:
                                param, vals, wtype = entry
                                self._add_wedge_row(node=node, param=str(param), wedge_type=str(wtype), values=vals)
            else:
                # No config discovered; leave current UI values untouched
                pass

            self.run_button.setEnabled(True)
            self.export_button.setEnabled(True)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Error loading config: {str(e)}")
            self.run_button.setEnabled(False)
            self.export_button.setEnabled(False)

    def _export_config(self) -> None:
        """Export current config to the path indicated by Config Filename.

        If only a filename (no directory) is provided, derive the folder from the
        Workflow Filename path. Creates/overwrites the target JSON file.
        """
        try:
            cfg = self._assemble_config_dict()
        except ValueError as e:
            QMessageBox.critical(self, "Invalid Config", str(e))
            return

        # Determine target path
        target = (self.config_edit.text() or "wedge_config.json").strip()
        wf_path = self._get_workflow_path()
        try:
            if not target:
                raise ValueError("Please provide a Config Filename.")
            if not os.path.isabs(target):
                # If no directory component, derive from workflow path
                if os.path.dirname(target):
                    target_path = os.path.abspath(target)
                else:
                    if not wf_path:
                        raise ValueError(
                            "Cannot derive save location: select a Workflow Filename or provide an absolute Config Filename."
                        )
                    wf_dir = os.path.dirname(wf_path)
                    target_path = os.path.join(wf_dir, target)
            else:
                target_path = target

            os.makedirs(os.path.dirname(target_path), exist_ok=True)
            with open(target_path, "w", encoding="utf-8") as f:
                json.dump(cfg, f, indent=4)
            self.wedge_config_path = target_path
            self.config_edit.setText(target_path)
            QMessageBox.information(self, "Exported", f"Config written to:\n{target_path}")
        except Exception as e:
            QMessageBox.critical(self, "Write Error", f"Could not write file:\n{e}")

    def _pick_config(self) -> None:
        """Select a config JSON and load it into the UI."""
        path_tuple = QFileDialog.getOpenFileName(self, "Select config JSON")
        path = path_tuple[0] if isinstance(path_tuple, tuple) else path_tuple
        if not path:
            return
        try:
            # Clear dynamic rows
            self._clear_override_rows()
            self._clear_wedge_rows()

            with open(path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            self.wedge_config_path = path
            # Show chosen path in the field
            self.config_edit.setText(path)
            # Top-level
            self.output_folder_edit.setText(str(cfg.get("output_folder", "")))
            self.filename_prefix_edit.setText(str(cfg.get("filename_prefix", "")))
            self.url_edit.setText(str(cfg.get("url", self.url_edit.text())))
            # Overrides
            pov = cfg.get("param_overrides", {}) or {}
            if isinstance(pov, dict):
                for node, entries in pov.items():
                    if not isinstance(entries, list):
                        continue
                    for entry in entries:
                        if isinstance(entry, list) and len(entry) == 2:
                            self._add_override_row(node=node, param=str(entry[0]), value=entry[1])
            # Wedges
            pw = cfg.get("param_wedges", {}) or {}
            if isinstance(pw, dict):
                for node, entries in pw.items():
                    if not isinstance(entries, list):
                        continue
                    for entry in entries:
                        if isinstance(entry, list) and len(entry) == 3:
                            param, vals, wtype = entry
                            self._add_wedge_row(node=node, param=str(param), wedge_type=str(wtype), values=vals)
            self.run_button.setEnabled(True)
            self.export_button.setEnabled(True)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Error loading config: {str(e)}")
            self.run_button.setEnabled(False)
            self.export_button.setEnabled(False)

    def _run_submitter(self) -> None:
        """Launch the submitter script in a separate process with the folder."""
        if not self._get_workflow_path():
            QMessageBox.warning(self, "No Workflow Selected", "Please select a workflow JSON first.")
            return

        try:
            # Locate the submitter next to this UI file
            script_dir = os.path.dirname(os.path.abspath(__file__))
            wedge_submitter_path = os.path.join(script_dir, "wedge_submitter.py")

            if not os.path.exists(wedge_submitter_path):
                QMessageBox.critical(
                    self,
                    "Script Not Found",
                    f"Could not find wedge_submitter.py at:\n{wedge_submitter_path}",
                )
                return

            # Determine if we should confirm and estimate total
            show_confirm, est_total = self._read_confirm_and_estimate()

            # Pop up confirmation inside the UI instead of terminal input
            if show_confirm:
                msg = f"Submit wedge runs now?"
                if est_total is not None:
                    msg = f"Submit {est_total} run(s) now?"
                response = QMessageBox.question(
                    self,
                    "Confirm Submission",
                    msg,
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No,
                )
                if response != QMessageBox.Yes:
                    return

            # Build command from UI inputs
            cmd = self._build_command(wedge_submitter_path)
            # If not confirming in UI, bypass in-script prompt
            if not show_confirm and "--no-confirm" not in cmd:
                cmd.append("--no-confirm")

            # Assemble current config and pass via stdin
            cfg = self._assemble_config_dict()
            cmd.append("--config-stdin")
            proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
            assert proc.stdin is not None
            proc.stdin.write(json.dumps(cfg).encode("utf-8"))
            proc.stdin.close()
            QMessageBox.information(self, "Submitted", "Wedge submission started!")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Could not run the script:\n{str(e)}")

    # -----------------------------
    # Helpers
    # -----------------------------
    def _read_confirm_and_estimate(self) -> tuple[bool, Optional[int]]:
        """Decide whether to confirm (from UI) and estimate total runs."""
        show_confirm = self.confirm_cb.isChecked()
        try:
            cfg = self._assemble_config_dict()
            total = self._estimate_total_runs(cfg.get("param_wedges", {}))
        except Exception:
            total = None
        return (show_confirm, total)

    def _estimate_total_runs(self, param_wedges: dict) -> Optional[int]:
        """Estimate the Cartesian product size from param_wedges.

        Supports the current format: node -> [[param, values, type], ...].
        """
        if not isinstance(param_wedges, dict):
            return None
        axis_sizes: list[int] = []
        for _node, entries in param_wedges.items():
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if not isinstance(entry, list) or len(entry) != 3:
                    continue
                _param, values_spec, wtype = entry
                if wtype == "explicit" and isinstance(values_spec, list):
                    size = len(values_spec)
                elif wtype == "minmax" and isinstance(values_spec, list) and len(values_spec) == 3:
                    try:
                        vmin, vmax, step = values_spec
                        if step == 0:
                            continue
                        size = int(round((float(vmax) - float(vmin)) / float(step))) + 1
                    except Exception:
                        continue
                else:
                    continue
                axis_sizes.append(max(1, size))

        if not axis_sizes:
            return 1

        total = 1
        for s in axis_sizes:
            total *= s
        return total

    def _build_command(self, submitter_path: str) -> list[str]:
        """Build a wedge_submitter.py command line from UI state."""
        workflow_path = self._get_workflow_path() or ""
        config_file = self.config_edit.text().strip() or "wedge_config.json"
        output_node = self.output_node_edit.text().strip() or "OUT_image"
        log_level = self.log_level_combo.currentText().strip() or "INFO"
        limit = self.limit_edit.text().strip()

        cmd = [
            sys.executable,
            submitter_path,
            "--workflow-filename", workflow_path,
            "--output-node", output_node,
            "--log-level", log_level,
        ]
        # Prefer adjacent config if present; otherwise pass config-filename
        try:
            wf_dir = os.path.dirname(workflow_path)
            adjacent = os.path.join(wf_dir, "wedge_config.json") if workflow_path else ""
            if not (adjacent and os.path.exists(adjacent)):
                cfg_path = config_file if os.path.isabs(config_file) else os.path.join(wf_dir, config_file)
                cmd.extend(["--config-filename", cfg_path])
        except Exception:
            # Fallback: include whatever the user typed
            if config_file:
                cmd.extend(["--config-filename", config_file])
        if self.dry_run_cb.isChecked():
            cmd.append("--dry-run")
        if self.print_cb.isChecked():
            cmd.append("--print-combinations")
        if self.stream_cb.isChecked():
            cmd.append("--stream")
        if limit:
            cmd.extend(["--limit", limit])
        return cmd

    # -----------------------------
    # Dynamic rows management
    # -----------------------------
    def _clear_layout(self, layout: QVBoxLayout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)

    def _clear_override_rows(self) -> None:
        self.override_rows.clear()
        self._clear_layout(self.overrides_container)

    def _clear_wedge_rows(self) -> None:
        self.wedge_rows.clear()
        self._clear_layout(self.wedges_container)

    def _add_override_row(self, *, node: str = "", param: str = "", value: Any = "") -> None:
        row = OverrideRow(
            self._get_available_node_titles,
            self._get_available_params_for_node,
            node=node,
            param=param,
            value=value,
        )
        row.remove_btn.clicked.connect(lambda: self._remove_override_row(row))
        self.override_rows.append(row)
        self.overrides_container.addWidget(row)

    def _remove_override_row(self, row: OverrideRow) -> None:
        if row in self.override_rows:
            self.override_rows.remove(row)
            row.setParent(None)

    def _add_wedge_row(self, *, node: str = "", param: str = "", wedge_type: str = "minmax", values: Any = None) -> None:
        row = WedgeRow(
            self._get_available_node_titles,
            self._get_available_params_for_node,
            node=node,
            param=param,
            wedge_type=wedge_type,
            values=values,
        )
        row.remove_btn.clicked.connect(lambda: self._remove_wedge_row(row))
        self.wedge_rows.append(row)
        self.wedges_container.addWidget(row)

    def _remove_wedge_row(self, row: WedgeRow) -> None:
        if row in self.wedge_rows:
            self.wedge_rows.remove(row)
            row.setParent(None)

    # -----------------------------
    # Config assembly
    # -----------------------------
    def _assemble_config_dict(self) -> Dict[str, Any]:
        output_folder = self.output_folder_edit.text().strip()
        filename_prefix = self.filename_prefix_edit.text().strip()
        url = self.url_edit.text().strip()
        if not output_folder:
            raise ValueError("'output_folder' is required.")
        if not filename_prefix:
            raise ValueError("'filename_prefix' is required.")
        if not url:
            raise ValueError("'url' is required.")

        pov_dict: Dict[str, List[List[Any]]] = {}
        for row in self.override_rows:
            entry = row.to_entry()
            if not entry:
                continue
            pov_dict.setdefault(entry["node"], []).append([entry["param"], entry["value"]])

        pw_dict: Dict[str, List[List[Any]]] = {}
        for row in self.wedge_rows:
            entry = row.to_entry()
            if not entry:
                continue
            pw_dict.setdefault(entry["node"], []).append([entry["param"], entry["values"], entry["type"]])

        return {
            "output_folder": output_folder,
            "filename_prefix": filename_prefix,
            "url": url,
            "param_overrides": pov_dict,
            "param_wedges": pw_dict,
        }

    # -----------------------------
    # Workflow helpers
    # -----------------------------
    def _get_workflow_path(self) -> Optional[str]:
        candidate = self.workflow_edit.text().strip()
        if candidate and os.path.exists(candidate):
            return candidate
        if self.selected_workflow and os.path.exists(self.selected_workflow):
            return self.selected_workflow
        return None

    def _get_available_node_titles(self) -> List[str]:
        path = self._get_workflow_path()
        if not path:
            return []
        # Prefer ComfyWorkflow if available
        try:
            if ComfyWorkflow is not None:
                wf = ComfyWorkflow(path)
                titles: List[str] = []
                for _node_id, node in wf.items():  # type: ignore[attr-defined]
                    title = None
                    meta = node.get("_meta") if isinstance(node, dict) else None
                    if isinstance(meta, dict):
                        title = meta.get("title")
                    if isinstance(title, str) and title:
                        titles.append(title)
                return sorted(dict.fromkeys(titles))
        except Exception:
            pass

        # Fallback: load raw JSON and read _meta.title
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            titles: List[str] = []
            if isinstance(data, dict):
                for node in data.values():
                    if isinstance(node, dict):
                        meta = node.get("_meta")
                        if isinstance(meta, dict):
                            title = meta.get("title")
                            if isinstance(title, str) and title:
                                titles.append(title)
            return sorted(dict.fromkeys(titles))
        except Exception:
            return []

    def _get_available_params_for_node(self, node_title: str) -> List[str]:
        path = self._get_workflow_path()
        if not path or not node_title:
            return []
        # Try via ComfyWorkflow
        try:
            if ComfyWorkflow is not None:
                wf = ComfyWorkflow(path)
                params: List[str] = []
                for _node_id, node in wf.items():  # type: ignore[attr-defined]
                    if not isinstance(node, dict):
                        continue
                    meta = node.get("_meta")
                    title = meta.get("title") if isinstance(meta, dict) else None
                    if title != node_title:
                        continue
                    inputs = node.get("inputs")
                    if isinstance(inputs, dict):
                        params.extend([str(k) for k in inputs.keys()])
                return sorted(dict.fromkeys(params))
        except Exception:
            pass

        # Fallback: raw JSON
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            params: List[str] = []
            if isinstance(data, dict):
                for node in data.values():
                    if not isinstance(node, dict):
                        continue
                    meta = node.get("_meta")
                    title = meta.get("title") if isinstance(meta, dict) else None
                    if title != node_title:
                        continue
                    inputs = node.get("inputs")
                    if isinstance(inputs, dict):
                        params.extend([str(k) for k in inputs.keys()])
            return sorted(dict.fromkeys(params))
        except Exception:
            return []


def main() -> None:
    app = QApplication(sys.argv)
    app.setStyleSheet(qdarkstyle.load_stylesheet_pyqt5())
    window = WedgeSubmitterUI()
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
