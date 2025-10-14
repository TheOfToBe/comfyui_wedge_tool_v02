# ComfyUI Wedge Tool

Run parameter sweeps (“wedges”) against a ComfyUI workflow, with a simple CLI and a helpful PyQt UI. The tool expands your wedge definitions into a Cartesian product, updates your workflow inputs, and submits each combination to a running ComfyUI instance while tracking progress and outputs.


## Features

- CLI submitter that reads a workflow JSON and a wedge configuration
- PyQt UI to build and export configs, then launch runs (no need to save first)
- Config format supports explicit value lists and min/max/step sequences
- Output naming encodes wedge values; images land under `<output_folder>/images`
- Auto-selects a SaveImage node by title or when only one is present
- Optional WebSocket streaming and ETA estimation during runs
- Separate wedge viewer to explore results interactively


## Repository Layout

- `wedge_tool/wedge_submitter.py` — CLI submitter
- `wedge_tool/wedge_submitter_ui.py` — PyQt UI for creating configs and launching runs
- `wedge_tool/view_wedges.py` — PyQt wedge viewer for generated images
- `wedge_tool/wedge_config_manager.py` — Config schema and validation helpers
- `templates/` — Example or helper files (if present)
- `_RUN_submit_wedges.bat`, `_RUN_view_wedges.bat` — Convenience launchers (Windows)


## Requirements

- Python 3.10+
- A running ComfyUI instance reachable over HTTP/WebSocket
- Python packages listed in `requirements.txt`

Install using requirements.txt:

```
python -m venv .venv
.venv\Scripts\activate  # Windows
# source .venv/bin/activate  # macOS/Linux

pip install -r requirements.txt
```


## Quick Start

1) Install dependencies (one time)

```
python -m venv .venv
.venv\Scripts\activate  # Windows
# source .venv/bin/activate  # macOS/Linux
pip install -r requirements.txt
```

2) Launch the UI (recommended)

```
python wedge_tool/wedge_submitter_ui.py
```

- Click “Select Workflow JSON” and choose your ComfyUI `workflow_api.json`.
- If a `wedge_config.json` is next to it, the UI loads it automatically.
- Fill or edit settings, then click “Submit Wedges”.
- To save your config, click “Export Config”. If you only specify a filename, it is saved next to the workflow.

Tip (Windows): you can also double‑click `_RUN_submit_wedges.bat`.

3) Or use the CLI directly

```
# Use adjacent wedge_config.json if present
python wedge_tool/wedge_submitter.py --workflow-filename C:\path\to\workflow_api.json

# Or point to a separate config file
python wedge_tool/wedge_submitter.py \
  --workflow-filename C:\path\to\workflow_api.json \
  --config-filename    C:\path\to\wedge_config.json
```

4) View results interactively (optional)

```
python wedge_tool/view_wedges.py
```


## Configuration Format

The submitter reads a JSON config describing output paths, server URL, static parameter overrides, and wedge axes. Example:

```json
{
  "output_folder": "WORK/my_project",
  "filename_prefix": "exp01",
  "url": "127.0.0.1:8188",
  "param_overrides": {
    "KSampler": [["seed", 12345]]
  },
  "param_wedges": {
    "KSampler": [
      ["steps", [10, 20, 30], "explicit"],
      ["cfg", [3, 9, 3], "minmax"]
    ],
    "CLIPTextEncode": [
      ["text", ["a sunset", "a sunrise"], "explicit"]
    ]
  }
}
```

Notes:

- `url` may omit the scheme; the tool will add `http://` when needed.
- `param_overrides` are applied to the workflow before wedges.
- `param_wedges` entries are `[
  param_name,
  values_spec,
  wedge_type
]` where:
  - `wedge_type = "explicit"` → `values_spec` is a list of explicit values
  - `wedge_type = "minmax"` → `values_spec` is `[min, max, step]`


## Workflow + Config Resolution

- The CLI requires a `--workflow-filename` that points to an existing JSON file.
- If a `wedge_config.json` exists next to that workflow file, it is used automatically.
- Otherwise, pass a config path via `--config-filename` (absolute or relative to the workflow’s folder).
- The UI can also pass the config over STDIN, so you can run without saving first.


## CLI Usage

Basic run using an adjacent config:

```
python wedge_tool/wedge_submitter.py \
  --workflow-filename C:\path\to\workflow_api.json
```

Specify a separate config file:

```
python wedge_tool/wedge_submitter.py \
  --workflow-filename C:\path\to\workflow_api.json \
  --config-filename    C:\path\to\wedge_config.json
```

Other useful options:

- `--output-node OUT_image` — SaveImage node title to set (auto-detected when possible)
- `--limit N` — Cap how many combinations are submitted
- `--dry-run` — Print combinations but do not submit
- `--print-combinations` — Log each combination submitted
- `--stream` — Stream events from ComfyUI during each run
- `--confirm` / `--no-confirm` — Control the confirmation prompt
- `--log-level INFO|DEBUG|…` — Adjust logging verbosity

Output paths:

- Images are written under `<output_folder>/images` using a filename that encodes wedge values, e.g.:
  `exp01__KSampler-steps-20__KSampler-cfg-6.png`


## UI Usage

Launch the UI:

```
python wedge_tool/wedge_submitter_ui.py
```

Workflow

- Click “Select Workflow JSON” and choose your workflow file.
- If a `wedge_config.json` lives next to it, the UI loads it automatically.
- If not, the UI leaves existing values alone so you can fill them in.

Config

- Fill out `output_folder`, `filename_prefix`, and `url` (defaults to `127.0.0.1:8188`).
- Use “+ Add Override” and “+ Add Wedge” to build your configuration.
- “Select Config JSON” lets you load an arbitrary config file.
- “Export Config” writes your current config:
  - If “Config Filename” is a bare filename, it is saved next to the workflow file.
  - Absolute/relative paths are respected; parent folders are created when needed.

Submitting

- Click “Submit Wedges” to run; the UI sends the config via STDIN to the CLI.
- Confirmation is handled in the UI. When disabled, the CLI is invoked with `--no-confirm`.

Advanced Settings

- Hidden by default behind “Show Advanced Settings”
- Controls: Output Node, Limit, Confirm submission, Dry run, Print combinations, Stream, and --log-level


## Wedge Viewer

The viewer can load a PNG result, read embedded metadata, and provide controls to navigate across wedge axes, updating the displayed image by filename convention.

Run:

```
python wedge_tool/view_wedges.py
```


## Tips & Troubleshooting

- “Workflow file not found” — Check the path you passed to `--workflow-filename`.
- “A wedge_config.json is required …” — Save a config next to the workflow or provide `--config-filename`.
- Output node not found — Rename your SaveImage node to the title you pass via `--output-node` (default `OUT_image`) or ensure only one SaveImage node exists.
- URL issues — You can omit the scheme (`127.0.0.1:8188`); the CLI normalizes to `http://127.0.0.1:8188`.
- Large min/max/step ranges — The CLI guards against runaway iteration; ensure sensible steps.


## License

MIT — see `LICENSE` for details.
