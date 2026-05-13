# Fusion 360 Codex Plugin

A local Codex plugin that lets Codex prepare and send modeling commands to
Autodesk Fusion 360 through the official Python API.

The bridge is intentionally local and file-based:

- Codex writes a command JSON to `~/.codex/fusion360/command.json`.
- The `CodexFusionBridge` Add-In polls that file and runs Fusion's Python API.
- The Add-In writes the outcome to `~/.codex/fusion360/response.json`.

Everything runs through Fusion's supported Python API; no menu clicking.

## Setup

```bash
cd <plugin install path>
python3 scripts/install_bridge.py
```

Then in Fusion 360: `Utilities` -> `Scripts and Add-Ins` -> `Add-Ins` tab ->
`CodexFusionBridge` -> `Run`. Enable `Run on Startup` to persist across
relaunches.

## Quick start

```bash
python3 scripts/submit_command.py get_status --wait
python3 scripts/submit_command.py create_box --name base --width-mm 80 --depth-mm 40 --height-mm 20 --wait
python3 scripts/submit_command.py create_cylinder --name post --diameter-mm 30 --height-mm 60 --wait
python3 scripts/submit_command.py fillet_body --body-name base --radius-mm 3 --wait
python3 scripts/submit_command.py read_design --include bodies,parameters --wait
python3 scripts/submit_command.py export_stl --filename pendant.stl --body-name base --quality high --wait
```

## Operations

| Group | Operations |
|---|---|
| Creation | `create_box`, `create_cylinder`, `create_sphere`, `create_torus`, `create_sketch`, `extrude_sketch`, `revolve_sketch`, `loft_profiles`, `sweep_along_path` |
| Modification | `fillet_body`, `chamfer_body`, `shell_body`, `delete_body`, `rename_body` |
| Parameters | `create_parameter`, `set_parameter` |
| Documents + export | `new_document`, `save_document`, `close_document`, `export_stl`, `export_step`, `export_iges`, `export_f3d` |
| Introspection / power tools | `read_design`, `execute_script`, `get_status` |

Run any operation with `--help` for full flags:

```bash
python3 scripts/submit_command.py create_box --help
python3 scripts/submit_command.py execute_script --help
```

## execute_script — arbitrary Fusion Python

When no predefined op fits, hand Fusion a Python script directly. Names
preloaded into the script: `adsk`, `app`, `ui`, `design`, `root`, and a
`helpers` dict (`mm`, `cm_to_mm`, `point3d`, `value_input_mm`,
`value_input_real`, `find_body`, `find_sketch`, `plane_from_name`,
`operation_from_name`). Assign `result` to return a JSON value; stdout is
captured.

```bash
python3 scripts/submit_command.py execute_script --wait --code '
sketch = root.sketches.add(root.xYConstructionPlane)
sketch.sketchCurves.sketchCircles.addByCenterRadius(helpers["point3d"](0,0,0), helpers["mm"](15))
feat = root.features.extrudeFeatures.addSimple(
    sketch.profiles.item(0),
    helpers["value_input_mm"](5),
    adsk.fusion.FeatureOperations.NewBodyFeatureOperation,
)
result = feat.bodies.item(0).name
'
```

For larger scripts, pass `--file path/to/script.py`.

## File locations

| Path | Purpose |
|---|---|
| `~/.codex/fusion360/command.json` | Pending command for the Add-In to consume. |
| `~/.codex/fusion360/response.json` | Latest response written by the Add-In. |
| `~/.codex/fusion360/exports/` | Default folder for relative export filenames. |

## Troubleshooting

- **Bridge not responding:** Open Fusion -> `Scripts and Add-Ins` -> verify
  `CodexFusionBridge` is running. Use `get_status --wait` to confirm.
- **"Unsupported operation" after code changes:** Stop and re-run the
  Add-In. Fusion caches loaded Python modules across edits.
- **"Active product is not a Fusion design":** Open or create a Fusion
  design document before sending modeling commands.
- **Legacy `CodexFusionBridgeModern` still present:** Re-run
  `python3 scripts/install_bridge.py`. The installer removes it.
