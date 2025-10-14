"""CLI entry point for running wedge parameter sweeps through ComfyUI."""

import argparse
import asyncio
import logging
import math
from copy import deepcopy
from datetime import timedelta
from itertools import product
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple

from comfyman import ComfyAPIClient, ComfyWorkflow

try:
    from wedge_tool.wedge_config_manager import WedgeConfig
except ImportError:  # pragma: no cover - convenience when running as a package
    from .wedge_config_manager import WedgeConfig  # type: ignore[attr-defined]

logger = logging.getLogger("wedge_submitter")


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments for the wedge submitter.

    Returns:
        argparse.Namespace: Parsed options including `workflow_filename`, `config_filename`, `output_node`,
            `limit`, `dry_run`, `print_combinations`, `stream`,
            `confirm`, `no_confirm`, and `log_level`.
    """
    parser = argparse.ArgumentParser(
        description="Run wedge parameter sweeps against a ComfyUI workflow."
    )
    # --json-folder deprecated: paths are resolved from --workflow-filename
    parser.add_argument(
        "--workflow-filename",
        required=True,
        help=(
            "Workflow JSON path. Must exist. An adjacent wedge_config.json will be used by default."
        ),
    )
    parser.add_argument(
        "--config-filename",
        default=None,
        help=(
            "Optional config filename or path. If omitted, a 'wedge_config.json' next to the workflow is used. "
            "If no adjacent config exists, this argument is required."
        ),
    )
    parser.add_argument(
        "--config-stdin",
        action="store_true",
        help=(
            "Read wedge configuration JSON from STDIN instead of a file. Used in UI mode."
            "When set, --config-filename is ignored."
        ),
    )
    parser.add_argument(
        "--output-node",
        default="OUT_image",
        help="Preferred SaveImage node title to update (default: %(default)s)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Limit the number of combinations to submit.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List the combinations that would run without submitting to ComfyUI.",
    )
    parser.add_argument(
        "--print-combinations",
        action="store_true",
        help="Print each combination before submitting.",
    )
    parser.add_argument(
        "--stream",
        action="store_true",
        help="Stream WebSocket events from ComfyUI for each run.",
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Always ask for confirmation before submitting regardless of config.",
    )
    parser.add_argument(
        "--no-confirm",
        action="store_true",
        help="Skip confirmation prompt even if the config requests it.",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"],
        help="Logging level (default: %(default)s)",
    )

    args = parser.parse_args()
    if args.limit is not None and args.limit <= 0:
        parser.error("--limit must be a positive integer.")
    if args.confirm and args.no_confirm:
        parser.error("Use either --confirm or --no-confirm, not both.")
    return args


def normalize_base_url(url: str) -> str:
    """Ensure the ComfyUI URL includes an HTTP/HTTPS scheme.

    Args:
        url: Base URL or host:port string.

    Returns:
        str: URL prefixed with `http://` if no scheme is present.
    """
    if url.startswith("http://") or url.startswith("https://"):
        return url
    return f"http://{url}"


def _coerce_numeric(value: Any) -> Any:
    """Round floats that are effectively integers while preserving other types.

    Args:
        value: A value possibly of type float/int/bool/other.

    Returns:
        Any: If `value` is a float close to an integer, returns an int;
        otherwise returns the original (or rounded float).
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        rounded = round(value, 10)
        if math.isclose(rounded, round(rounded)):
            return int(round(rounded))
        return rounded
    return value


def expand_wedge_values(spec: Iterable[Any], wedge_type: str) -> List[Any]:
    """Expand a wedge definition into an explicit list of values.

    Args:
        spec: Either a list of explicit values, or `[min, max, step]` for
            `minmax` wedges.
        wedge_type: One of `"explicit"` or `"minmax"`.

    Returns:
        list[Any]: Concrete list of values.

    Raises:
        ValueError: If arguments are malformed or unsupported.
    """
    if wedge_type == "explicit":
        values = list(spec)
        if not values:
            raise ValueError("Explicit wedge must contain at least one value.")
        return values

    if wedge_type == "minmax":
        values = list(spec)
        if len(values) != 3:
            raise ValueError("Minmax wedge expects [min, max, step].")
        start, stop, step = values
        if step == 0:
            raise ValueError("Minmax wedge step must be non-zero.")

        # Ensure numeric types are floats for calculation
        start_f = float(start)
        stop_f = float(stop)
        step_f = float(step)
        direction = 1 if step_f > 0 else -1

        result: List[Any] = []
        current = start_f
        max_iterations = 100000
        iterations = 0
        tolerance = abs(step_f) * 1e-9

        while True:
            if direction > 0 and current > stop_f + tolerance:
                break
            if direction < 0 and current < stop_f - tolerance:
                break
            result.append(_coerce_numeric(current))
            current += step_f
            iterations += 1
            if iterations > max_iterations:
                raise ValueError("Exceeded maximum iterations expanding minmax wedge.")

        if not result:
            raise ValueError("No values produced when expanding minmax wedge.")

        return result

    raise ValueError(f"Unsupported wedge type: {wedge_type}")


Axis = Tuple[Tuple[str, str], List[Any]]


def build_axes(config: WedgeConfig) -> List[Axis]:
    """Build Cartesian axes from the wedge configuration.

    Args:
        config: Loaded `WedgeConfig` containing `param_wedges`.

    Returns:
        list[Axis]: A list of `((node, param), values)` axes.
    """
    axes: List[Axis] = []
    for node_name, wedges in config.param_wedges.items():
        for wedge in wedges:
            param, values_spec, wedge_type = wedge
            expanded = expand_wedge_values(values_spec, wedge_type)
            axes.append(((node_name, param), expanded))
    return axes


def iter_combinations(axes: List[Axis]) -> Iterator[Dict[str, Dict[str, Any]]]:
    """Yield dictionaries describing every combination from the provided axes.

    Args:
        axes: List of `((node, param), values)` axes.

    Yields:
        dict[str, dict[str, Any]]: Mapping of node → {param → value}.
    """
    if not axes:
        yield {}
        return

    keys = [axis[0] for axis in axes]
    value_lists = [axis[1] for axis in axes]

    for combo_values in product(*value_lists):
        combination: Dict[str, Dict[str, Any]] = {}
        for (node_name, param), value in zip(keys, combo_values):
            combination.setdefault(node_name, {})[param] = value
        yield combination


def format_combination(combination: Dict[str, Dict[str, Any]]) -> str:
    """Format a combination dictionary into a readable string.

    Args:
        combination: Node/param/value mapping as produced by `iter_combinations`.

    Returns:
        str: Readable summary for logs.
    """
    if not combination:
        return "<no wedges>"
    parts: List[str] = []
    for node_name in sorted(combination):
        for param_name in sorted(combination[node_name]):
            value = combination[node_name][param_name]
            parts.append(f"{node_name}.{param_name}={value}")
    return ", ".join(parts)


def stringify_value(value: Any) -> str:
    """Convert a value into a string, trimming float noise where possible.

    Args:
        value: Value to stringify.

    Returns:
        str: Short, filename-friendly representation.
    """
    if isinstance(value, float):
        return f"{value:.10g}".rstrip(".")
    return str(value)


def sanitize_value(value: Any) -> str:
    """Convert a value into a filename-safe fragment.

    Args:
        value: Arbitrary value.

    Returns:
        str: Safe token with spaces and separators replaced.
    """
    text = stringify_value(value)
    for ch in (" ", "/", "\\", ":", ",", ";", "\"", "'"):
        text = text.replace(ch, "_")
    return text


def build_filename(prefix: str, combination: Dict[str, Dict[str, Any]]) -> str:
    """Produce a filename prefix that encodes the wedge values.

    Args:
        prefix: Project/file prefix.
        combination: Mapping of node → {param → value}.

    Returns:
        str: Filename stem without numeric suffix or extension.
    """
    parts = [prefix]
    for node_name in sorted(combination):
        for param_name in sorted(combination[node_name]):
            value = combination[node_name][param_name]
            parts.append(f"{node_name}-{param_name}-{sanitize_value(value)}")
    return "__".join(parts)


def apply_param_overrides(workflow: ComfyWorkflow, config: WedgeConfig) -> None:
    """Apply static parameter overrides from the config onto the workflow.

    Args:
        workflow: `ComfyWorkflow` to mutate.
        config: Configuration providing override entries.

    Raises:
        RuntimeError: If a target node/parameter is not found.
    """
    for node_name, overrides in config.param_overrides.items():
        for param, value in overrides:
            if not workflow.set_param_value(node_name, param, value):
                raise RuntimeError(
                    f"Failed to set override {node_name}.{param} = {value!r}. "
                    "Check that the node title and parameter exist in the workflow."
                )


def apply_combination(workflow: ComfyWorkflow, combination: Dict[str, Dict[str, Any]]) -> None:
    """Apply a single wedge combination onto the workflow inputs.

    Args:
        workflow: `ComfyWorkflow` to mutate.
        combination: Mapping of node → {param → selected value}.

    Raises:
        RuntimeError: If a target node/parameter is not found.
    """
    for node_name, params in combination.items():
        for param, value in params.items():
            if not workflow.set_param_value(node_name, param, value):
                raise RuntimeError(
                    f"Failed to set wedge parameter {node_name}.{param} = {value!r}. "
                    "Verify that the workflow contains the expected node and input."
                )


def ensure_output_dir(output_folder: str) -> Path:
    """Create the output directory if it does not already exist.

    Args:
        output_folder: Base folder; images are written under `<output_folder>/images`.

    Returns:
        pathlib.Path: Absolute/relative path to the images directory.
    """
    output_dir = Path(output_folder) / "images"
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def set_output_prefix(
    workflow: ComfyWorkflow,
    filename_prefix: str,
    preferred_node_title: str,
) -> None:
    """Assign the generated filename prefix to the appropriate SaveImage node.

    Args:
        workflow: Workflow to update.
        filename_prefix: Full path (without suffix) to set on the SaveImage node.
        preferred_node_title: Node title to try first (e.g., `OUT_image`).

    Raises:
        RuntimeError: If no suitable SaveImage node can be determined.
    """
    if workflow.set_param_value(preferred_node_title, "filename_prefix", filename_prefix):
        return

    save_nodes: List[Tuple[str, Dict[str, Any]]] = [
        (node_id, node)
        for node_id, node in workflow.items()
        if node.get("class_type") == "SaveImage"
    ]

    if len(save_nodes) == 1:
        node_title = save_nodes[0][1].get("_meta", {}).get("title")
        if node_title and workflow.set_param_value(node_title, "filename_prefix", filename_prefix):
            return

    raise RuntimeError(
        "Unable to determine output node. "
        "Rename the desired SaveImage node to match --output-node (default 'OUT_image') "
        "or ensure only one SaveImage node exists."
    )


def prompt_confirmation(total_runs: int, output_dir: Path) -> bool:
    """Ask the operator for confirmation before submitting prompts.

    Args:
        total_runs: Number of runs to be submitted.
        output_dir: Directory where images will be written.

    Returns:
        bool: True if the user consented, False otherwise.
    """
    plural = "s" if total_runs != 1 else ""
    prompt = f"Submit {total_runs} run{plural} and write outputs under '{output_dir}'? [y/N]: "
    response = input(prompt).strip().lower()
    return response in {"y", "yes"}


def extract_last_image_path(history: Dict[str, Any], prompt_id: str) -> Optional[str]:
    """Return the final image path recorded in a workflow history, if present.

    Args:
        history: Execution history as returned by the Comfy API.
        prompt_id: The prompt identifier for the run of interest.

    Returns:
        Optional[str]: Relative path `subfolder/filename` if available; else None.
    """
    prompt_history = history.get(prompt_id)
    if not prompt_history:
        return None

    outputs = prompt_history.get("outputs", {})
    images: List[Dict[str, Any]] = []
    for node_output in outputs.values():
        images.extend(node_output.get("images", []))

    if not images:
        return None

    last_image = images[-1]
    filename = last_image.get("filename")
    if not filename:
        return None

    subfolder = last_image.get("subfolder")
    if subfolder:
        return str(Path(subfolder) / filename)
    return filename


def format_elapsed(elapsed: Dict[str, Any]) -> str:
    """Return a human-friendly representation of elapsed time.

    Args:
        elapsed: Dict with keys `hours`, `minutes`, `seconds`.

    Returns:
        str: Formatted string like `0h 1m 3.2s`.
    """
    return f"{elapsed['hours']}h {elapsed['minutes']}m {elapsed['seconds']}s"


def estimate_eta(seconds_list: List[float], remaining: int) -> Optional[timedelta]:
    """Estimate remaining wall-clock time based on recorded durations.

    Args:
        seconds_list: List of previous run durations in seconds.
        remaining: Number of runs remaining.

    Returns:
        Optional[datetime.timedelta]: Estimated time to completion, or None.
    """
    if not seconds_list or remaining <= 0:
        return None
    average = sum(seconds_list) / len(seconds_list)
    return timedelta(seconds=average * remaining)


def main() -> None:
    """Execute the CLI workflow for submitting wedge combinations.

    Parses CLI args, loads the workflow and wedge config, prepares the
    combination space, and submits runs to ComfyUI while logging progress
    and estimating completion time.
    """
    args = parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper()),
        format="%(asctime)s - %(levelname)s - %(message)s",
    )

    # Resolve workflow path from the provided argument only
    workflow_path = Path(args.workflow_filename).expanduser()

    if not workflow_path.exists():
        raise FileNotFoundError(f"Workflow file not found: {workflow_path}")

    if args.config_stdin:
        import sys, json as _json  # local, to avoid top-level shadowing
        raw = sys.stdin.read()
        try:
            data = _json.loads(raw)
        except Exception as e:
            raise ValueError(f"Failed to parse config JSON from STDIN: {e}")
        config = WedgeConfig.from_dict(data)
        logger.info("Loaded config from STDIN override.")
    else:
        # Prefer a wedge_config.json next to the workflow, if present
        adjacent_cfg = workflow_path.parent / "wedge_config.json"
        config_path: Optional[Path] = None
        if adjacent_cfg.exists():
            config_path = adjacent_cfg
        else:
            # If provided, resolve config path; try as-is, else relative to the workflow folder
            if args.config_filename:
                cfg_arg = Path(str(args.config_filename)).expanduser()
                if cfg_arg.exists():
                    config_path = cfg_arg
                else:
                    candidate = workflow_path.parent / str(args.config_filename)
                    if candidate.exists():
                        config_path = candidate
            if config_path is None:
                raise FileNotFoundError(
                    "A wedge_config.json is required (either next to the workflow file "
                    "or provided via --config-filename)."
                )

        config = WedgeConfig.load_from_file(config_path)
    logger.info(
        "Loaded config for output '%s' with %d override node(s) and %d wedge node(s).",
        config.output_folder,
        len(config.param_overrides),
        len(config.param_wedges),
    )

    axes = build_axes(config)
    total_runs = 1
    if axes:
        total_runs = math.prod(len(axis[1]) for axis in axes)

    max_runs = total_runs
    if args.limit is not None:
        max_runs = min(max_runs, args.limit)

    if max_runs <= 0:
        logger.info("No combinations to run. Exiting.")
        return

    combinations_iter = iter_combinations(axes)

    if args.print_combinations or args.dry_run:
        logger.info("Listing up to %d combination(s):", max_runs)
        for idx, combination in enumerate(combinations_iter, 1):
            logger.info("[%d/%d] %s", idx, total_runs, format_combination(combination))
            if idx >= max_runs:
                break
        if args.dry_run:
            logger.info("Dry run complete. No prompts submitted.")
            return
        combinations_iter = iter_combinations(axes)

    require_confirmation = (args.confirm and not args.no_confirm)

    # output_dir = ensure_output_dir(config.output_folder)
    output_dir = Path(config.output_folder) / "images"
    if require_confirmation and not prompt_confirmation(max_runs, output_dir):
        logger.info("Submission cancelled by user.")
        return

    logger.info(
        "Preparing to submit %d combination(s) (total possible: %d).",
        max_runs,
        total_runs,
    )

    base_workflow = ComfyWorkflow(workflow_path, add_metadata=True)
    base_workflow.update_adhoc_metadata("wedge_config", config.to_dict())

    client = ComfyAPIClient(url=normalize_base_url(config.url))
    logger.info("Using ComfyUI at %s (client_id=%s).", client.url, client.client_id)

    elapsed_seconds: List[float] = []

    combinations_iter = iter_combinations(axes)
    for idx, combination in enumerate(combinations_iter, 1):
        if idx > max_runs:
            break

        run_workflow: ComfyWorkflow = deepcopy(base_workflow)
        apply_param_overrides(run_workflow, config)
        apply_combination(run_workflow, combination)
        run_workflow.update_adhoc_metadata(
            "wedge_iteration",
            {
                "index": idx,
                "of": max_runs,
                "combination": combination,
            },
        )

        filename = build_filename(config.filename_prefix, combination)
        output_prefix = output_dir / filename
        set_output_prefix(run_workflow, str(output_prefix), args.output_node)

        logger.info(
            "[%d/%d] Submitting combination → %s",
            idx,
            max_runs,
            format_combination(combination),
        )

        job = client.queue_prompt(run_workflow, client_id=client.client_id)
        prompt_id = job.get("prompt_id")
        if not prompt_id:
            raise RuntimeError("ComfyUI did not return a prompt_id.")

        history = asyncio.run(
            client.stream_until_done(
                prompt_id,
                client_id=client.client_id,
                print_stream=args.stream,
            )
        )

        elapsed = client.get_elapsed_time(prompt_id, history)
        elapsed_seconds.append(elapsed["seconds_total"])
        out_path = extract_last_image_path(history, prompt_id)

        logger.info(
            "[%d/%d] Completed prompt_id=%s (%s). Output: %s",
            idx,
            max_runs,
            prompt_id,
            format_elapsed(elapsed),
            out_path or "<no image recorded>",
        )

        remaining = max_runs - idx
        eta = estimate_eta(elapsed_seconds, remaining)
        if eta:
            logger.info(
                "Estimated time remaining: %s",
                str(eta).split(".")[0],
            )

    logger.info("All requested combinations have been processed.")


if __name__ == "__main__":
    main()
