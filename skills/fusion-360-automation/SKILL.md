---
name: fusion-360-automation
description: Use when the user asks Codex to control Autodesk Fusion 360, create or modify CAD geometry, query the active design, generate Fusion 360 Python API scripts, export STL/STEP/IGES/F3D, or install the local Fusion bridge Add-In.
---

# Fusion 360 Automation

Use this skill to convert user requests into Fusion 360 modeling actions via a
local file-based bridge.

## Architecture

1. Codex writes a command JSON to `~/.codex/fusion360/command.json`.
2. The `CodexFusionBridge` Add-In (installed by `scripts/install_bridge.py`)
   polls that file every 0.25s and fires a custom UI-thread event.
3. The Add-In dispatches to one of the registered operations and runs the
   Fusion 360 Python API.
4. The Add-In writes the outcome to `~/.codex/fusion360/response.json`.

All CAD work happens inside Fusion's supported Python API — no GUI clicking.

## First-time setup

```bash
python3 scripts/install_bridge.py
```

Then in Fusion 360:

1. `Utilities` -> `Scripts and Add-Ins` -> `Add-Ins` tab.
2. Select `CodexFusionBridge` -> `Run`.
3. Enable `Run on Startup` for sessions to persist across relaunches.

If a `CodexFusionBridgeModern` Add-In from a previous version is still
listed, the installer removes it; restart Fusion to refresh the list.

## Two workhorse operations

Most user requests should be routed through one of these.

### `execute_script` — arbitrary Fusion Python

When no predefined op fits, generate a Fusion 360 Python API script and run
it directly. The script runs on Fusion's UI thread with these names
preloaded: `adsk`, `app`, `ui`, `design`, `root`, and `helpers`
(`mm`, `cm_to_mm`, `point3d`, `value_input_mm`, `value_input_real`,
`find_body`, `find_sketch`, `plane_from_name`, `operation_from_name`).
Assign `result` to return a JSON-serializable value; stdout is captured.

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

For larger scripts, write to a file and pass `--file`:

```bash
python3 scripts/submit_command.py execute_script --file my_script.py --wait
```

### `read_design` — inspect the active design

```bash
python3 scripts/submit_command.py read_design --wait
python3 scripts/submit_command.py read_design --include bodies,parameters --wait
```

Includes: `bodies`, `sketches`, `parameters`, `components`, `occurrences`.

## Registered operations

Creation: `create_box`, `create_cylinder`, `create_sphere`, `create_torus`,
`create_sketch`, `extrude_sketch`, `revolve_sketch`, `loft_profiles`,
`sweep_along_path`.

Modification: `fillet_body`, `chamfer_body`, `shell_body`, `delete_body`,
`rename_body`.

Parameters: `create_parameter`, `set_parameter`.

Documents + export: `new_document`, `save_document`, `close_document`,
`export_stl`, `export_step`, `export_iges`, `export_f3d`.

Introspection: `read_design`, `execute_script`, `get_status`.

Run `python3 scripts/submit_command.py --help` for the full CLI surface,
or `<subcommand> --help` for each operation.

## Send a command

```bash
python3 scripts/submit_command.py get_status --wait
python3 scripts/submit_command.py create_box --name base --width-mm 80 --depth-mm 40 --height-mm 20 --wait
python3 scripts/submit_command.py create_cylinder --name post --diameter-mm 30 --height-mm 60 --wait
python3 scripts/submit_command.py fillet_body --body-name base --radius-mm 3 --edges all --wait
python3 scripts/submit_command.py shell_body --body-name base --thickness-mm 2 --open-face-indices 1 --wait
python3 scripts/submit_command.py export_stl --filename pendant.stl --body-name post --quality high --wait
```

Combine operations with `--operation-kind` (`new_body` default, or `join`,
`cut`, `intersect`, `new_component`) on shape creators that support it.

## Choosing the right operation

- Simple parametric primitive (box, cylinder, sphere, torus): use the
  dedicated `create_*` operation.
- Sketch-based feature with custom geometry: build with `create_sketch`,
  then `extrude_sketch` / `revolve_sketch` / `loft_profiles` / `sweep_along_path`.
- Modifying an existing body: use `fillet_body`, `chamfer_body`,
  `shell_body`, `delete_body`, or `rename_body`.
- Anything else (patterns, joints, materials, mirroring, complex
  selection): generate a Fusion Python script and run via `execute_script`.

## Command JSON shape

The CLI writes JSON like:

```json
{
  "command_id": "uuid",
  "operation": "create_box",
  "params": {
    "name": "base",
    "width_mm": 80,
    "depth_mm": 40,
    "height_mm": 20,
    "plane": "xy",
    "operation": "new_body"
  }
}
```

For `execute_script`:

```json
{
  "command_id": "uuid",
  "operation": "execute_script",
  "params": {
    "code": "result = root.bRepBodies.count",
    "globals": {"my_radius_mm": 12.5}
  }
}
```

## Operating rules

- Always prefer registered operations or `execute_script` over UI clicking
  instructions.
- Use millimeters in user-facing commands; the bridge converts to Fusion's
  internal centimeters.
- After sending a command with `--wait`, inspect the response. On
  `status: ok` proceed; on `status: error` read `error` and `traceback`
  and either fix the script or report the failure clearly.
- Before any destructive operation (delete, overwrite document), confirm
  with the user unless they already asked to keep working in the active
  document.
- If Fusion reports `Unsupported operation` after a code change, restart
  the `CodexFusionBridge` Add-In — Fusion caches Python modules.
- If the bridge does not respond (timeout), check `get_status`. If Fusion
  is not running or the Add-In is stopped, instruct the user how to start
  it.
- For exports, files default to `~/.codex/fusion360/exports/<filename>`
  when a relative path is given.
