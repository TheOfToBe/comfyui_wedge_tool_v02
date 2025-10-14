"""Configuration helpers for describing wedge sweeps.

Defines a `WedgeConfig` dataclass for loading, validating, mutating, and
serialising wedge configuration files. The loader supports both the modern
schema and a legacy schema and normalises inputs to a single internal shape.
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Literal, Optional

ParamOverride = List[Any]
ParamWedge = List[Any]
WedgeType = Literal["minmax", "explicit"]


@dataclass
class WedgeConfig:
    """In-memory representation of a wedge configuration file.

    Attributes:
        project_name: Logical project name; used for output directory.
        filename_prefix: Prefix used in generated filenames.
        for_testing: If true, limit submissions to a single combination.
        show_confirmation: If true, prompt for confirmation before submit.
        url: Base URL for the ComfyUI server (with or without scheme).
        param_overrides: Dict of node → list[[param, value]] static overrides.
        param_wedges: Dict of node → list[[param, values_spec, wedge_type]].
    """
    output_folder: str
    filename_prefix: str
    url: str
    param_overrides: Dict[str, List[ParamOverride]]
    param_wedges: Dict[str, List[ParamWedge]]

    @staticmethod
    def _normalize_overrides(raw: Any) -> Dict[str, List[ParamOverride]]:
        """Coerce overrides from either dict or legacy list form into a dict.

        Args:
            raw: Either a dict mapping node → [[param, value], ...] or a
                legacy list of [node, param, value] entries.

        Returns:
            dict[str, list[list[Any]]]: Normalised overrides keyed by node.

        Raises:
            ValueError: If the structure does not match either supported form.
        """
        if raw in (None, {}):
            return {}
        if isinstance(raw, dict):
            normalized: Dict[str, List[ParamOverride]] = {}
            for node, entries in raw.items():
                if not isinstance(node, str):
                    raise ValueError("param_overrides keys must be node titles.")
                if not isinstance(entries, list):
                    raise ValueError(f"param_overrides[{node!r}] must be a list.")
                converted: List[ParamOverride] = []
                for entry in entries:
                    if not isinstance(entry, list) or len(entry) != 2:
                        raise ValueError(
                            f"param_overrides[{node!r}] entries must be [param, value] lists."
                        )
                    converted.append(entry)
                normalized[node] = converted
            return normalized
        if isinstance(raw, list):
            normalized = {}
            for entry in raw:
                if not isinstance(entry, list) or len(entry) != 3:
                    raise ValueError(
                        "List-form param_overrides entries must be [node, param, value]."
                    )
                node, param, value = entry
                if not isinstance(node, str):
                    raise ValueError("param_overrides node names must be strings.")
                normalized.setdefault(node, []).append([param, value])
            return normalized
        raise ValueError("param_overrides must be a dict or list.")

    @staticmethod
    def _normalize_wedges(raw: Any) -> Dict[str, List[ParamWedge]]:
        """Coerce wedge definitions from legacy and modern formats into dict form.

        Args:
            raw: Either a modern dict of node → [[param, values, mode], ...]
                or a legacy dict of param → [node, values, mode].

        Returns:
            dict[str, list[list[Any]]]: Normalised wedges keyed by node.

        Raises:
            ValueError: If entries are malformed or of an unsupported type.
        """
        if raw in (None, {}):
            return {}
        if not isinstance(raw, dict):
            raise ValueError("param_wedges must be a dictionary.")

        normalized: Dict[str, List[ParamWedge]] = {}
        for key, value in raw.items():
            # Modern format: node_name -> [ [param, values, mode], ... ]
            if isinstance(value, list) and value and isinstance(value[0], list):
                node_name = key
                if not isinstance(node_name, str):
                    raise ValueError("param_wedges keys must be node titles.")
                converted: List[ParamWedge] = []
                for entry in value:
                    if not isinstance(entry, list) or len(entry) != 3:
                        raise ValueError(
                            f"param_wedges[{node_name!r}] entries must be [param, values, mode] lists."
                        )
                    converted.append(entry)
                normalized[node_name] = converted
                continue

            # Legacy format: param_name -> [node_name, values, mode]
            if isinstance(value, list) and len(value) == 3 and isinstance(key, str):
                node_name, values_config, mode = value
                if not isinstance(node_name, str):
                    raise ValueError("Legacy param_wedges entries must include a node title string.")
                normalized.setdefault(node_name, []).append([key, values_config, mode])
                continue

            raise ValueError(
                f"Unrecognized param_wedges entry for key '{key}'. Expected legacy or new format."
            )

        return normalized

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "WedgeConfig":
        """Construct a config instance from a dictionary, normalising fields.

        Args:
            data: Raw configuration dictionary.

        Returns:
            WedgeConfig: Validated configuration instance.

        Raises:
            ValueError: If validation fails.
        """
        # Prefer new key; fall back to legacy `project_name` for compatibility
        output_folder = data.get("output_folder") or data.get("project_name", "")

        config = cls(
            output_folder=output_folder,
            filename_prefix=data.get("filename_prefix", ""),
            url=data.get("url", ""),
            param_overrides=cls._normalize_overrides(data.get("param_overrides", {})),
            param_wedges=cls._normalize_wedges(data.get("param_wedges", {})),
        )
        config.vet()
        return config

    @classmethod
    def load_from_file(cls, filepath: str | Path) -> "WedgeConfig":
        """Load a WedgeConfig from a JSON file and validate it.

        Args:
            filepath: Path to a JSON configuration file.

        Returns:
            WedgeConfig: Validated configuration instance.

        Raises:
            FileNotFoundError: If `filepath` does not exist.
            ValueError: If validation fails.
        """
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
        return cls.from_dict(data)

    def save_to_file(self, filepath: str | Path, indent: int = 4) -> None:
        """Persist the configuration to disk.

        Args:
            filepath: Destination file path.
            indent: JSON indentation level.
        """
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=indent)

    def vet(self) -> None:
        """Validate core configuration fields.

        Raises:
            ValueError: If any required field is missing or of wrong type.
        """
        if not self.output_folder or not isinstance(self.output_folder, str):
            raise ValueError("output_folder must be a non-empty string.")
        if not self.filename_prefix or not isinstance(self.filename_prefix, str):
            raise ValueError("filename_prefix must be a non-empty string.")
        if not self.url or not isinstance(self.url, str):
            raise ValueError("url must be a non-empty string.")
        if not isinstance(self.param_overrides, dict):
            raise ValueError("param_overrides must be a dictionary keyed by node name.")
        if not isinstance(self.param_wedges, dict):
            raise ValueError("param_wedges must be a dictionary keyed by node name.")

        for node, overrides in self.param_overrides.items():
            if not isinstance(node, str):
                raise ValueError("param_overrides node keys must be strings.")
            if not isinstance(overrides, list):
                raise ValueError(f"param_overrides[{node!r}] must be a list.")
            for item in overrides:
                if not isinstance(item, list) or len(item) != 2:
                    raise ValueError(
                        f"param_overrides[{node!r}] entries must be [param, value] lists."
                    )

        for node, wedges in self.param_wedges.items():
            if not isinstance(node, str):
                raise ValueError("param_wedges node keys must be strings.")
            if not isinstance(wedges, list):
                raise ValueError(f"param_wedges[{node!r}] must be a list.")
            for wedge in wedges:
                if not isinstance(wedge, list) or len(wedge) != 3:
                    raise ValueError(
                        f"param_wedges[{node!r}] entries must be [param, values, wedge_type] lists."
                    )
                wedge_type = wedge[2]
                if wedge_type not in ("minmax", "explicit"):
                    raise ValueError(
                        f"Unsupported wedge type '{wedge_type}' in param_wedges[{node!r}]."
                    )

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-serialisable representation of the config.

        Returns:
            dict[str, Any]: Configuration suitable for `json.dump`.
        """
        return {
            "output_folder": self.output_folder,
            "filename_prefix": self.filename_prefix,
            "url": self.url,
            "param_overrides": self.param_overrides,
            "param_wedges": self.param_wedges,
        }

    def to_json(self, indent: int = 4) -> str:
        """Return the config as formatted JSON.

        Args:
            indent: Indentation level for pretty printing.

        Returns:
            str: JSON string of the configuration.
        """
        return json.dumps(self.to_dict(), indent=indent)

    # ------------------------------------------------------------------
    # Mutators
    # ------------------------------------------------------------------
    def set_param_override(self, node_name: str, param: str, value: Any) -> None:
        """Add or replace a static override for a given node parameter.

        Args:
            node_name: Target node title.
            param: Input parameter name.
            value: Value to assign.
        """
        overrides = self.param_overrides.setdefault(node_name, [])
        overrides[:] = [entry for entry in overrides if entry[0] != param]
        overrides.append([param, value])

    def add_param_override(self, node_name: str, param: str, value: Any) -> None:
        """Alias for `set_param_override` for backward compatibility.

        Args:
            node_name: Target node title.
            param: Input parameter name.
            value: Value to assign.
        """
        self.set_param_override(node_name, param, value)

    def remove_param_override(self, node_name: str, param: str) -> bool:
        """Remove a static override, returning True if something was deleted.

        Args:
            node_name: Target node title.
            param: Input parameter name.

        Returns:
            bool: True if an override was removed; False otherwise.
        """
        overrides = self.param_overrides.get(node_name)
        if not overrides:
            return False
        original_len = len(overrides)
        overrides[:] = [entry for entry in overrides if entry[0] != param]
        if not overrides:
            self.param_overrides.pop(node_name, None)
        return len(overrides) != original_len

    def set_param_wedge(self, node_name: str, param: str, wedge_values: Iterable[Any], wedge_type: WedgeType) -> None:
        """Add or replace a wedge definition for the supplied node parameter.

        Args:
            node_name: Target node title.
            param: Input parameter name.
            wedge_values: Iterable of values or `[min, max, step]`.
            wedge_type: Either `"minmax"` or `"explicit"`.

        Raises:
            ValueError: If `wedge_type` is unsupported.
        """
        if wedge_type not in ("minmax", "explicit"):
            raise ValueError("wedge_type must be 'minmax' or 'explicit'.")
        wedges = self.param_wedges.setdefault(node_name, [])
        wedges[:] = [entry for entry in wedges if entry[0] != param]
        wedges.append([param, list(wedge_values), wedge_type])

    def remove_param_wedge(self, node_name: str, param: str) -> bool:
        """Remove a wedge entry, returning True if the node retained changes.

        Args:
            node_name: Target node title.
            param: Input parameter name.

        Returns:
            bool: True if a wedge entry was removed; False otherwise.
        """
        wedges = self.param_wedges.get(node_name)
        if not wedges:
            return False
        original_len = len(wedges)
        wedges[:] = [entry for entry in wedges if entry[0] != param]
        if not wedges:
            self.param_wedges.pop(node_name, None)
        return len(wedges) != original_len

    def get_param_override(self, node_name: str, param: str) -> Optional[ParamOverride]:
        """Return the override entry for a node parameter if present.

        Args:
            node_name: Target node title.
            param: Input parameter name.

        Returns:
            Optional[list[Any]]: `[param, value]` if present; else None.
        """
        overrides = self.param_overrides.get(node_name, [])
        for entry in overrides:
            if entry[0] == param:
                return entry
        return None

    def get_param_wedge(self, node_name: str, param: str) -> Optional[ParamWedge]:
        """Return the wedge entry for a node parameter if present.

        Args:
            node_name: Target node title.
            param: Input parameter name.

        Returns:
            Optional[list[Any]]: `[param, values, type]` if present; else None.
        """
        wedges = self.param_wedges.get(node_name, [])
        for entry in wedges:
            if entry[0] == param:
                return entry
        return None


if __name__ == "__main__":

    # --- For testing
    example_path = Path("templates/example_wedge_config.json")
    config = WedgeConfig.load_from_file(example_path)
    print(config.to_json())
