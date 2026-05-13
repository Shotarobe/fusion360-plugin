#!/usr/bin/env python3
"""Write a Codex Fusion 360 bridge command and optionally wait for the response.

Usage examples:
  python3 submit_command.py get_status --wait
  python3 submit_command.py create_box --name base --width-mm 80 --depth-mm 40 --height-mm 20 --wait
  python3 submit_command.py read_design --include bodies,sketches,parameters --wait
  python3 submit_command.py execute_script --code "result = root.bRepBodies.count" --wait
  python3 submit_command.py execute_script --file path/to/script.py --wait
  python3 submit_command.py export_stl --filename pendant.stl --body-name pendant_1 --wait
  python3 submit_command.py raw path/to/command.json --wait

Every operation accepts --wait and --timeout.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from pathlib import Path
from typing import Any


STATE_DIR = Path.home() / ".codex" / "fusion360"
COMMAND_FILE = STATE_DIR / "command.json"
RESPONSE_FILE = STATE_DIR / "response.json"


def positive_float(value: str) -> float:
    parsed = float(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be positive")
    return parsed


def csv_list(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def int_csv(value: str) -> list[int]:
    return [int(item.strip()) for item in value.split(",") if item.strip()]


def add_common(subparser: argparse.ArgumentParser) -> None:
    subparser.add_argument("--wait", action="store_true", help="Wait for Fusion to respond.")
    subparser.add_argument("--timeout", type=float, default=30.0, help="Seconds to wait when --wait is set.")
    subparser.add_argument(
        "--operation-kind",
        choices=("new_body", "join", "cut", "intersect", "new_component"),
        default=None,
        help="Combine operation (only meaningful for shape creators).",
    )


def add_plane(subparser: argparse.ArgumentParser) -> None:
    subparser.add_argument(
        "--plane",
        choices=("xy", "xz", "yz"),
        default=None,
        help="Construction plane for the sketch (defaults to xy).",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Send a command to the Fusion 360 Codex bridge.")
    subparsers = parser.add_subparsers(dest="operation", required=True)

    # --- shape primitives ---------------------------------------------------
    box = subparsers.add_parser("create_box", help="Rectangular solid.")
    add_common(box); add_plane(box)
    box.add_argument("--name", default="codex_box")
    box.add_argument("--width-mm", type=positive_float, required=True)
    box.add_argument("--depth-mm", type=positive_float, required=True)
    box.add_argument("--height-mm", type=positive_float, required=True)

    cyl = subparsers.add_parser("create_cylinder", help="Cylinder by diameter and height.")
    add_common(cyl); add_plane(cyl)
    cyl.add_argument("--name", default="codex_cylinder")
    cyl.add_argument("--diameter-mm", type=positive_float, required=True)
    cyl.add_argument("--height-mm", type=positive_float, required=True)

    sph = subparsers.add_parser("create_sphere", help="Sphere by diameter.")
    add_common(sph)
    sph.add_argument("--name", default="codex_sphere")
    sph.add_argument("--diameter-mm", type=positive_float, required=True)

    tor = subparsers.add_parser("create_torus", help="Torus by major/minor diameter.")
    add_common(tor)
    tor.add_argument("--name", default="codex_torus")
    tor.add_argument("--major-diameter-mm", type=positive_float, required=True)
    tor.add_argument("--minor-diameter-mm", type=positive_float, required=True)

    # --- sketches + features -----------------------------------------------
    sketch = subparsers.add_parser("create_sketch", help="Create a sketch via JSON-described primitives.")
    add_common(sketch); add_plane(sketch)
    sketch.add_argument("--name", default="codex_sketch")
    sketch.add_argument(
        "--json",
        help="Path to JSON file or inline JSON with circles/rectangles/lines arrays.",
    )

    ext = subparsers.add_parser("extrude_sketch", help="Extrude an existing sketch profile.")
    add_common(ext)
    ext.add_argument("--sketch-name", required=True)
    ext.add_argument("--distance-mm", type=float, required=True, help="Negative values extrude downward.")
    ext.add_argument("--profile-index", type=int, default=0)
    ext.add_argument("--name", default=None)

    rev = subparsers.add_parser("revolve_sketch", help="Revolve a sketch profile around an axis.")
    add_common(rev)
    rev.add_argument("--sketch-name", required=True)
    rev.add_argument("--axis", choices=("x", "y", "z"), default="z")
    rev.add_argument("--angle-deg", type=float, default=360.0)
    rev.add_argument("--profile-index", type=int, default=0)

    loft = subparsers.add_parser("loft_profiles", help="Loft through two or more sketch profiles.")
    add_common(loft)
    loft.add_argument("--sketches", required=True, help="Comma-separated sketch names.")
    loft.add_argument("--name", default=None)

    sweep = subparsers.add_parser("sweep_along_path", help="Sweep a profile along a path sketch.")
    add_common(sweep)
    sweep.add_argument("--profile-sketch", required=True)
    sweep.add_argument("--path-sketch", required=True)
    sweep.add_argument("--profile-index", type=int, default=0)
    sweep.add_argument("--name", default=None)

    fil = subparsers.add_parser("fillet_body", help="Fillet edges of a body.")
    add_common(fil)
    fil.add_argument("--body-name", required=True)
    fil.add_argument("--radius-mm", type=positive_float, required=True)
    fil.add_argument("--edges", choices=("all", "convex", "concave"), default="all")

    cha = subparsers.add_parser("chamfer_body", help="Chamfer edges of a body.")
    add_common(cha)
    cha.add_argument("--body-name", required=True)
    cha.add_argument("--distance-mm", type=positive_float, required=True)
    cha.add_argument("--edges", choices=("all", "convex", "concave"), default="all")

    shell = subparsers.add_parser("shell_body", help="Shell a body. Pass face indices to leave open.")
    add_common(shell)
    shell.add_argument("--body-name", required=True)
    shell.add_argument("--thickness-mm", type=positive_float, required=True)
    shell.add_argument("--open-face-indices", type=int_csv, default=None,
                       help="Comma-separated face indices to leave open.")

    delete = subparsers.add_parser("delete_body", help="Delete a body by name.")
    add_common(delete)
    delete.add_argument("--body-name", required=True)

    rename = subparsers.add_parser("rename_body", help="Rename a body.")
    add_common(rename)
    rename.add_argument("--body-name", required=True)
    rename.add_argument("--new-name", required=True)

    # --- parameters --------------------------------------------------------
    new_param = subparsers.add_parser("create_parameter", help="Create a user parameter.")
    add_common(new_param)
    new_param.add_argument("--name", required=True)
    new_param.add_argument("--expression", required=True)
    new_param.add_argument("--unit", default="mm")
    new_param.add_argument("--comment", default="")

    set_param = subparsers.add_parser("set_parameter", help="Set an existing user parameter expression.")
    add_common(set_param)
    set_param.add_argument("--name", required=True)
    set_param.add_argument("--expression", required=True)

    # --- documents + export ------------------------------------------------
    new_doc = subparsers.add_parser("new_document", help="Create a new untitled design document.")
    add_common(new_doc)

    save_doc = subparsers.add_parser("save_document", help="Save the active document.")
    add_common(save_doc)
    save_doc.add_argument("--description", default="")

    close_doc = subparsers.add_parser("close_document", help="Close the active document.")
    add_common(close_doc)
    close_doc.add_argument("--save-changes", action="store_true")

    stl = subparsers.add_parser("export_stl", help="Export STL (whole design or a single body).")
    add_common(stl)
    stl.add_argument("--filename", required=True)
    stl.add_argument("--body-name", default=None)
    stl.add_argument("--quality", choices=("low", "medium", "high"), default="medium")

    step = subparsers.add_parser("export_step", help="Export STEP of the entire design.")
    add_common(step)
    step.add_argument("--filename", required=True)

    iges = subparsers.add_parser("export_iges", help="Export IGES of the entire design.")
    add_common(iges)
    iges.add_argument("--filename", required=True)

    f3d = subparsers.add_parser("export_f3d", help="Export a Fusion Archive (.f3d).")
    add_common(f3d)
    f3d.add_argument("--filename", required=True)

    # --- introspection + power tools --------------------------------------
    read = subparsers.add_parser("read_design", help="Inspect the active design's state.")
    add_common(read)
    read.add_argument(
        "--include",
        type=csv_list,
        default=["bodies", "sketches", "parameters", "components"],
        help="Comma-separated: bodies, sketches, parameters, components, occurrences.",
    )

    script = subparsers.add_parser("execute_script", help="Run arbitrary Fusion Python.")
    add_common(script)
    script_src = script.add_mutually_exclusive_group(required=True)
    script_src.add_argument("--code", help="Inline Python source.")
    script_src.add_argument("--file", type=Path, help="Path to a .py file.")
    script.add_argument(
        "--globals-json",
        default=None,
        help="JSON object injected into the script namespace.",
    )

    status = subparsers.add_parser("get_status", help="Return bridge status and operations.")
    add_common(status)

    raw = subparsers.add_parser("raw", help="Send a raw command JSON file.")
    add_common(raw)
    raw.add_argument("json_file", type=Path)

    return parser


# ---------------------------------------------------------------------------
# Build command payload
# ---------------------------------------------------------------------------

# Args that should never appear in the command's params dict.
_META_KEYS = {"operation", "wait", "timeout", "operation_kind"}


def _kebab_params(args: argparse.Namespace) -> dict[str, Any]:
    params: dict[str, Any] = {}
    for key, value in vars(args).items():
        if key in _META_KEYS:
            continue
        if value is None:
            continue
        params[key] = value
    return params


def command_for_create_sketch(args: argparse.Namespace) -> dict[str, Any]:
    params: dict[str, Any] = {"name": args.name}
    if args.plane:
        params["plane"] = args.plane

    if args.json:
        text = Path(args.json).read_text(encoding="utf-8") if Path(args.json).exists() else args.json
        sketch_spec = json.loads(text)
        for key in ("circles", "rectangles", "lines"):
            if key in sketch_spec:
                params[key] = sketch_spec[key]
    return params


def command_for_loft(args: argparse.Namespace) -> dict[str, Any]:
    sketches = [{"sketch_name": name} for name in csv_list(args.sketches)]
    params: dict[str, Any] = {"sketches": sketches}
    if args.name:
        params["name"] = args.name
    return params


def command_for_execute_script(args: argparse.Namespace) -> dict[str, Any]:
    if args.file:
        code = args.file.read_text(encoding="utf-8")
    else:
        code = args.code
    params: dict[str, Any] = {"code": code}
    if args.globals_json:
        params["globals"] = json.loads(args.globals_json)
    return params


def command_from_args(args: argparse.Namespace) -> dict[str, Any]:
    command_id = str(uuid.uuid4())

    if args.operation == "raw":
        command = json.loads(args.json_file.read_text(encoding="utf-8"))
        command.setdefault("command_id", command_id)
        return command

    if args.operation == "create_sketch":
        params = command_for_create_sketch(args)
    elif args.operation == "loft_profiles":
        params = command_for_loft(args)
    elif args.operation == "execute_script":
        params = command_for_execute_script(args)
    else:
        params = _kebab_params(args)

    # operation_kind is exposed only on shape creators that accept it.
    if getattr(args, "operation_kind", None):
        params["operation"] = args.operation_kind

    return {"command_id": command_id, "operation": args.operation, "params": params}


# ---------------------------------------------------------------------------
# Wait for response
# ---------------------------------------------------------------------------

def wait_for_response(command_id: str, timeout: float) -> dict[str, Any]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if RESPONSE_FILE.exists():
            try:
                response = json.loads(RESPONSE_FILE.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                response = {}
            if response.get("command_id") == command_id:
                return response
        time.sleep(0.2)
    raise TimeoutError(
        f"No response from Fusion 360 bridge for command_id={command_id}. "
        "Check that CodexFusionBridge is running."
    )


def main() -> None:
    args = build_parser().parse_args()
    STATE_DIR.mkdir(parents=True, exist_ok=True)

    command = command_from_args(args)
    COMMAND_FILE.write_text(json.dumps(command, indent=2), encoding="utf-8")
    print(json.dumps({"wrote": str(COMMAND_FILE), "command": command}, indent=2))

    if getattr(args, "wait", False):
        response = wait_for_response(command["command_id"], args.timeout)
        print(json.dumps({"response": response}, indent=2, default=str))
        if response.get("status") != "ok":
            sys.exit(1)


if __name__ == "__main__":
    main()
