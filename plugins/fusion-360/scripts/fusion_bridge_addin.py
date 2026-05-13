"""Fusion 360 Add-In bridge for Codex modeling commands.

Polls a single command file written by submit_command.py, dispatches to
the registered operations, and writes a response file. All Fusion API
calls run on the UI thread via a CustomEvent.

Operations are organized as small, explicit handlers. Two of them are
the workhorses:

- execute_script: runs user-supplied Python with adsk.core / adsk.fusion
  preloaded. Use when no predefined op fits.
- read_design: returns structured state of the active design.
"""

from __future__ import annotations

import io
import json
import os
import threading
import time
import traceback
from contextlib import redirect_stdout

import adsk.core
import adsk.fusion


STATE_DIR = os.path.expanduser("~/.codex/fusion360")
COMMAND_FILE = os.path.join(STATE_DIR, "command.json")
RESPONSE_FILE = os.path.join(STATE_DIR, "response.json")
CUSTOM_EVENT_ID = "codexFusionBridgeCommand"
ADDIN_NAME = "CodexFusionBridge"
POLL_SECONDS = 0.25

_app = None
_ui = None
_handlers = []
_stop_event = threading.Event()
_seen_command_id = None


# ---------------------------------------------------------------------------
# Unit + geometry helpers
# ---------------------------------------------------------------------------

def mm(value) -> float:
    """Millimeters -> centimeters (Fusion's internal length unit)."""
    return float(value) / 10.0


def cm_to_mm(value) -> float:
    return float(value) * 10.0


def get_design() -> adsk.fusion.Design:
    design = adsk.fusion.Design.cast(_app.activeProduct)
    if not design:
        raise RuntimeError("Active product is not a Fusion design. Open or create a design first.")
    return design


def get_root() -> adsk.fusion.Component:
    return get_design().rootComponent


def point3d(x_mm=0.0, y_mm=0.0, z_mm=0.0) -> adsk.core.Point3D:
    return adsk.core.Point3D.create(mm(x_mm), mm(y_mm), mm(z_mm))


def value_input_mm(value_mm) -> adsk.core.ValueInput:
    return adsk.core.ValueInput.createByReal(mm(value_mm))


def value_input_real(value) -> adsk.core.ValueInput:
    return adsk.core.ValueInput.createByReal(float(value))


def find_body(name: str) -> adsk.fusion.BRepBody:
    root = get_root()
    for body in _iter_all_bodies(root):
        if body.name == name:
            return body
    raise ValueError(f"Body not found: {name}")


def _iter_all_bodies(component: adsk.fusion.Component):
    for body in component.bRepBodies:
        yield body
    for occ in component.allOccurrences:
        for body in occ.component.bRepBodies:
            yield body


def find_sketch(name: str) -> adsk.fusion.Sketch:
    for sketch in get_root().sketches:
        if sketch.name == name:
            return sketch
    raise ValueError(f"Sketch not found: {name}")


def plane_from_name(name: str):
    root = get_root()
    name = (name or "xy").lower()
    if name in ("xy", "xyplane"):
        return root.xYConstructionPlane
    if name in ("xz", "xzplane"):
        return root.xZConstructionPlane
    if name in ("yz", "yzplane"):
        return root.yZConstructionPlane
    raise ValueError(f"Unknown construction plane: {name}. Use xy, xz, or yz.")


def operation_from_name(name: str):
    name = (name or "new_body").lower()
    table = {
        "new_body": adsk.fusion.FeatureOperations.NewBodyFeatureOperation,
        "join": adsk.fusion.FeatureOperations.JoinFeatureOperation,
        "cut": adsk.fusion.FeatureOperations.CutFeatureOperation,
        "intersect": adsk.fusion.FeatureOperations.IntersectFeatureOperation,
        "new_component": adsk.fusion.FeatureOperations.NewComponentFeatureOperation,
    }
    if name not in table:
        raise ValueError(f"Unknown feature operation: {name}. Use one of: {sorted(table)}")
    return table[name]


# ---------------------------------------------------------------------------
# Shape primitives
# ---------------------------------------------------------------------------

def _rect_sketch(root, name, cx_mm, cy_mm, width_mm, depth_mm, plane=None):
    sketch = root.sketches.add(plane or root.xYConstructionPlane)
    sketch.name = name
    center = point3d(cx_mm, cy_mm, 0)
    corner = point3d(cx_mm + width_mm / 2.0, cy_mm + depth_mm / 2.0, 0)
    sketch.sketchCurves.sketchLines.addCenterPointRectangle(center, corner)
    return sketch


def _circle_sketch(root, name, cx_mm, cy_mm, radius_mm, plane=None):
    sketch = root.sketches.add(plane or root.xYConstructionPlane)
    sketch.name = name
    sketch.sketchCurves.sketchCircles.addByCenterRadius(point3d(cx_mm, cy_mm, 0), mm(radius_mm))
    return sketch


def _extrude(root, profile, distance_mm, operation):
    return root.features.extrudeFeatures.addSimple(profile, value_input_mm(distance_mm), operation)


def create_box(params):
    root = get_root()
    name = params.get("name", "codex_box")
    width = params["width_mm"]
    depth = params["depth_mm"]
    height = params["height_mm"]
    sketch = _rect_sketch(root, f"{name}_profile", 0, 0, width, depth, plane_from_name(params.get("plane")))
    feat = _extrude(root, sketch.profiles.item(0), height, operation_from_name(params.get("operation")))
    body = feat.bodies.item(0)
    body.name = name
    return {"body": body.name}


def create_cylinder(params):
    root = get_root()
    name = params.get("name", "codex_cylinder")
    radius = float(params["diameter_mm"]) / 2.0
    height = params["height_mm"]
    sketch = _circle_sketch(root, f"{name}_profile", 0, 0, radius, plane_from_name(params.get("plane")))
    feat = _extrude(root, sketch.profiles.item(0), height, operation_from_name(params.get("operation")))
    body = feat.bodies.item(0)
    body.name = name
    return {"body": body.name}


def create_sphere(params):
    root = get_root()
    name = params.get("name", "codex_sphere")
    radius_mm = float(params["diameter_mm"]) / 2.0

    # Half-disk profile on XZ, revolved around Z to form a sphere.
    sketch = root.sketches.add(root.xZConstructionPlane)
    sketch.name = f"{name}_profile"
    lines = sketch.sketchCurves.sketchLines
    arcs = sketch.sketchCurves.sketchArcs
    top = adsk.core.Point3D.create(0, mm(radius_mm), 0)
    bottom = adsk.core.Point3D.create(0, mm(-radius_mm), 0)
    lines.addByTwoPoints(top, bottom)
    arcs.addByThreePoints(top, adsk.core.Point3D.create(mm(radius_mm), 0, 0), bottom)

    axis = sketch.sketchCurves.sketchLines.item(0)
    revolves = root.features.revolveFeatures
    rev_input = revolves.createInput(
        sketch.profiles.item(0),
        axis,
        operation_from_name(params.get("operation")),
    )
    rev_input.setAngleExtent(False, value_input_real(2 * 3.141592653589793))
    feat = revolves.add(rev_input)
    body = feat.bodies.item(0)
    body.name = name
    return {"body": body.name}


def create_torus(params):
    root = get_root()
    name = params.get("name", "codex_torus")
    major_mm = float(params["major_diameter_mm"]) / 2.0
    minor_mm = float(params["minor_diameter_mm"]) / 2.0

    sketch = root.sketches.add(root.xZConstructionPlane)
    sketch.name = f"{name}_profile"
    sketch.sketchCurves.sketchCircles.addByCenterRadius(
        adsk.core.Point3D.create(mm(major_mm), 0, 0), mm(minor_mm)
    )

    # Build a revolution axis along Z, away from the circle center.
    axis_sketch = root.sketches.add(root.xZConstructionPlane)
    axis_sketch.name = f"{name}_axis"
    axis_line = axis_sketch.sketchCurves.sketchLines.addByTwoPoints(
        adsk.core.Point3D.create(0, mm(-major_mm), 0),
        adsk.core.Point3D.create(0, mm(major_mm), 0),
    )

    revolves = root.features.revolveFeatures
    rev_input = revolves.createInput(
        sketch.profiles.item(0),
        axis_line,
        operation_from_name(params.get("operation")),
    )
    rev_input.setAngleExtent(False, value_input_real(2 * 3.141592653589793))
    feat = revolves.add(rev_input)
    body = feat.bodies.item(0)
    body.name = name
    return {"body": body.name}


def revolve_sketch(params):
    root = get_root()
    sketch_name = params["sketch_name"]
    sketch = find_sketch(sketch_name)
    profile_index = int(params.get("profile_index", 0))
    profile = sketch.profiles.item(profile_index)

    angle_deg = float(params.get("angle_deg", 360.0))
    operation = operation_from_name(params.get("operation"))

    axis_kind = params.get("axis", "z").lower()
    if axis_kind == "x":
        axis = root.xConstructionAxis
    elif axis_kind == "y":
        axis = root.yConstructionAxis
    elif axis_kind == "z":
        axis = root.zConstructionAxis
    else:
        raise ValueError("axis must be one of: x, y, z")

    revolves = root.features.revolveFeatures
    rev_input = revolves.createInput(profile, axis, operation)
    rev_input.setAngleExtent(False, value_input_real(angle_deg * 3.141592653589793 / 180.0))
    feat = revolves.add(rev_input)
    bodies = [b.name for b in feat.bodies]
    return {"bodies": bodies}


def extrude_sketch(params):
    root = get_root()
    sketch = find_sketch(params["sketch_name"])
    profile = sketch.profiles.item(int(params.get("profile_index", 0)))
    feat = _extrude(root, profile, params["distance_mm"], operation_from_name(params.get("operation")))
    new_name = params.get("name")
    bodies = []
    for body in feat.bodies:
        if new_name and feat.bodies.count == 1:
            body.name = new_name
        bodies.append(body.name)
    return {"bodies": bodies}


def fillet_body(params):
    root = get_root()
    body = find_body(params["body_name"])
    radius_mm = float(params["radius_mm"])
    edges_filter = params.get("edges", "all").lower()  # all | convex | concave

    edges = adsk.core.ObjectCollection.create()
    for edge in body.edges:
        if edges_filter == "all":
            edges.add(edge)
        elif edges_filter == "convex" and edge.isConvex:
            edges.add(edge)
        elif edges_filter == "concave" and not edge.isConvex:
            edges.add(edge)

    if edges.count == 0:
        raise RuntimeError(f"No edges matched filter '{edges_filter}' on body {body.name}.")

    fillets = root.features.filletFeatures
    fillet_input = fillets.createInput()
    fillet_input.addConstantRadiusEdgeSet(edges, value_input_mm(radius_mm), True)
    fillets.add(fillet_input)
    return {"body": body.name, "edges_filleted": edges.count}


def chamfer_body(params):
    root = get_root()
    body = find_body(params["body_name"])
    distance_mm = float(params["distance_mm"])
    edges_filter = params.get("edges", "all").lower()

    edges = adsk.core.ObjectCollection.create()
    for edge in body.edges:
        if edges_filter == "all":
            edges.add(edge)
        elif edges_filter == "convex" and edge.isConvex:
            edges.add(edge)
        elif edges_filter == "concave" and not edge.isConvex:
            edges.add(edge)
    if edges.count == 0:
        raise RuntimeError(f"No edges matched filter '{edges_filter}' on body {body.name}.")

    chamfers = root.features.chamferFeatures
    chamfer_input = chamfers.createInput(edges, True)
    chamfer_input.setToEqualDistance(value_input_mm(distance_mm))
    chamfers.add(chamfer_input)
    return {"body": body.name, "edges_chamfered": edges.count}


def shell_body(params):
    root = get_root()
    body = find_body(params["body_name"])
    thickness_mm = float(params["thickness_mm"])
    face_indices = params.get("open_face_indices")

    faces = adsk.core.ObjectCollection.create()
    if face_indices:
        for idx in face_indices:
            faces.add(body.faces.item(int(idx)))
    # else: solid shell with no openings.

    shells = root.features.shellFeatures
    shell_input = shells.createInput(faces if faces.count else adsk.core.ObjectCollection.create(), False)
    shell_input.insideThickness = value_input_mm(thickness_mm)
    shells.add(shell_input)
    return {"body": body.name, "thickness_mm": thickness_mm}


def loft_profiles(params):
    """Loft through a list of sketch profiles by name."""
    root = get_root()
    sketches = params["sketches"]  # list of {"sketch_name": "...", "profile_index": 0}
    if len(sketches) < 2:
        raise ValueError("loft requires at least two sketch profiles.")
    operation = operation_from_name(params.get("operation"))

    lofts = root.features.loftFeatures
    loft_input = lofts.createInput(operation)
    for entry in sketches:
        sketch = find_sketch(entry["sketch_name"])
        profile = sketch.profiles.item(int(entry.get("profile_index", 0)))
        loft_input.loftSections.add(profile)
    loft_input.isSolid = True
    feat = lofts.add(loft_input)
    bodies = [b.name for b in feat.bodies]
    new_name = params.get("name")
    if new_name and len(bodies) == 1:
        feat.bodies.item(0).name = new_name
        bodies = [new_name]
    return {"bodies": bodies}


def sweep_along_path(params):
    """Sweep a profile sketch along a path sketch."""
    root = get_root()
    profile_sketch = find_sketch(params["profile_sketch"])
    profile = profile_sketch.profiles.item(int(params.get("profile_index", 0)))
    path_sketch = find_sketch(params["path_sketch"])

    path = root.features.createPath(path_sketch.sketchCurves.item(0))
    sweeps = root.features.sweepFeatures
    sweep_input = sweeps.createInput(profile, path, operation_from_name(params.get("operation")))
    feat = sweeps.add(sweep_input)
    bodies = [b.name for b in feat.bodies]
    new_name = params.get("name")
    if new_name and len(bodies) == 1:
        feat.bodies.item(0).name = new_name
        bodies = [new_name]
    return {"bodies": bodies}


# ---------------------------------------------------------------------------
# Sketch construction
# ---------------------------------------------------------------------------

def create_sketch(params):
    root = get_root()
    name = params.get("name", "codex_sketch")
    plane = plane_from_name(params.get("plane", "xy"))
    sketch = root.sketches.add(plane)
    sketch.name = name

    for circle in params.get("circles") or []:
        sketch.sketchCurves.sketchCircles.addByCenterRadius(
            point3d(circle.get("cx_mm", 0.0), circle.get("cy_mm", 0.0), 0),
            mm(float(circle["radius_mm"])),
        )
    for rect in params.get("rectangles") or []:
        cx = rect.get("cx_mm", 0.0)
        cy = rect.get("cy_mm", 0.0)
        w = rect["width_mm"]
        d = rect["depth_mm"]
        sketch.sketchCurves.sketchLines.addCenterPointRectangle(
            point3d(cx, cy, 0),
            point3d(cx + w / 2.0, cy + d / 2.0, 0),
        )
    for line in params.get("lines") or []:
        sketch.sketchCurves.sketchLines.addByTwoPoints(
            point3d(line["x1_mm"], line["y1_mm"], 0),
            point3d(line["x2_mm"], line["y2_mm"], 0),
        )

    return {
        "sketch": sketch.name,
        "plane": params.get("plane", "xy"),
        "profile_count": sketch.profiles.count,
    }


# ---------------------------------------------------------------------------
# Reading state
# ---------------------------------------------------------------------------

def _bbox_summary(body):
    try:
        bbox = body.boundingBox
        return {
            "min_mm": [cm_to_mm(bbox.minPoint.x), cm_to_mm(bbox.minPoint.y), cm_to_mm(bbox.minPoint.z)],
            "max_mm": [cm_to_mm(bbox.maxPoint.x), cm_to_mm(bbox.maxPoint.y), cm_to_mm(bbox.maxPoint.z)],
        }
    except Exception:
        return None


def read_design(params):
    design = get_design()
    root = design.rootComponent

    include = set(params.get("include") or ["bodies", "sketches", "parameters", "components"])

    result = {
        "document_name": _app.activeDocument.name if _app.activeDocument else None,
        "unit": design.unitsManager.defaultLengthUnits,
        "design_type": "DirectDesignType"
        if design.designType == adsk.fusion.DesignTypes.DirectDesignType
        else "ParametricDesignType",
        "root_component": root.name,
    }

    if "bodies" in include:
        bodies = []
        for body in _iter_all_bodies(root):
            bodies.append(
                {
                    "name": body.name,
                    "visible": body.isVisible,
                    "volume_mm3": body.volume * 1000.0,  # cm^3 -> mm^3
                    "area_mm2": body.area * 100.0,  # cm^2 -> mm^2
                    "bounding_box": _bbox_summary(body),
                }
            )
        result["bodies"] = bodies

    if "sketches" in include:
        result["sketches"] = [
            {"name": s.name, "profile_count": s.profiles.count, "is_visible": s.isVisible}
            for s in root.sketches
        ]

    if "parameters" in include:
        result["user_parameters"] = [
            {
                "name": p.name,
                "expression": p.expression,
                "value": p.value,
                "unit": p.unit,
                "comment": p.comment or "",
            }
            for p in design.userParameters
        ]

    if "components" in include:
        result["components"] = [
            {"name": c.name, "id": c.id, "body_count": c.bRepBodies.count}
            for c in design.allComponents
        ]

    if "occurrences" in include:
        result["occurrences"] = [
            {"name": occ.name, "component": occ.component.name}
            for occ in root.allOccurrences
        ]

    return result


# ---------------------------------------------------------------------------
# Parameters
# ---------------------------------------------------------------------------

def set_parameter(params):
    design = get_design()
    name = params["name"]
    user_param = design.userParameters.itemByName(name)
    if not user_param:
        raise ValueError(f"User parameter not found: {name}")
    user_param.expression = str(params["expression"])
    return {"name": name, "expression": user_param.expression, "value": user_param.value}


def create_parameter(params):
    design = get_design()
    name = params["name"]
    unit = params.get("unit", "mm")
    expression = str(params["expression"])
    comment = params.get("comment", "")

    if design.userParameters.itemByName(name):
        raise ValueError(f"User parameter already exists: {name}")

    value_input = adsk.core.ValueInput.createByString(expression)
    user_param = design.userParameters.add(name, value_input, unit, comment)
    return {
        "name": user_param.name,
        "expression": user_param.expression,
        "value": user_param.value,
        "unit": user_param.unit,
    }


# ---------------------------------------------------------------------------
# Documents + export
# ---------------------------------------------------------------------------

def new_document(params):
    documents = _app.documents
    doc_type = adsk.core.DocumentTypes.FusionDesignDocumentType
    doc = documents.add(doc_type)
    name = params.get("name")
    if name:
        # Naming a new untitled doc requires saving; defer until save_as is called.
        pass
    return {"document": doc.name, "is_saved": doc.isSaved}


def save_document(params):
    doc = _app.activeDocument
    if not doc.isSaved:
        raise RuntimeError("Document has never been saved. Use save_as first.")
    doc.save(params.get("description", ""))
    return {"document": doc.name, "is_saved": True}


def close_document(params):
    doc = _app.activeDocument
    save_changes = bool(params.get("save_changes", False))
    name = doc.name
    doc.close(save_changes)
    return {"closed": name, "saved": save_changes}


def _resolve_export_path(filename: str) -> str:
    path = os.path.expanduser(filename)
    if not os.path.isabs(path):
        path = os.path.join(STATE_DIR, "exports", path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return path


def _export_geometry(params, kind: str):
    design = get_design()
    export_mgr = design.exportManager
    path = _resolve_export_path(params["filename"])

    if kind == "stl":
        body_name = params.get("body_name")
        if body_name:
            body = find_body(body_name)
            options = export_mgr.createSTLExportOptions(body, path)
        else:
            options = export_mgr.createSTLExportOptions(get_root(), path)
        quality_map = {
            "low": adsk.fusion.MeshRefinementSettings.MeshRefinementLow,
            "medium": adsk.fusion.MeshRefinementSettings.MeshRefinementMedium,
            "high": adsk.fusion.MeshRefinementSettings.MeshRefinementHigh,
        }
        options.meshRefinement = quality_map.get(
            params.get("quality", "medium").lower(),
            adsk.fusion.MeshRefinementSettings.MeshRefinementMedium,
        )
    elif kind == "step":
        options = export_mgr.createSTEPExportOptions(path, get_root())
    elif kind == "iges":
        options = export_mgr.createIGESExportOptions(path, get_root())
    elif kind == "f3d":
        options = export_mgr.createFusionArchiveExportOptions(path, get_root())
    else:
        raise ValueError(f"Unknown export kind: {kind}")

    export_mgr.execute(options)
    return {"path": path, "kind": kind}


def export_stl(params):
    return _export_geometry(params, "stl")


def export_step(params):
    return _export_geometry(params, "step")


def export_iges(params):
    return _export_geometry(params, "iges")


def export_f3d(params):
    return _export_geometry(params, "f3d")


# ---------------------------------------------------------------------------
# Selection / deletion helpers
# ---------------------------------------------------------------------------

def delete_body(params):
    body = find_body(params["body_name"])
    body.deleteMe()
    return {"deleted": params["body_name"]}


def rename_body(params):
    body = find_body(params["body_name"])
    new_name = params["new_name"]
    body.name = new_name
    return {"renamed_to": new_name}


# ---------------------------------------------------------------------------
# Arbitrary script execution
# ---------------------------------------------------------------------------

def execute_script(params):
    """Run user-supplied Python inside the Fusion API context.

    Available names: adsk, adsk.core, adsk.fusion, app, ui, design, root,
    helpers (a dict of small helpers from this module). The script may
    assign `result` to return a JSON-serializable value. stdout is captured.
    """
    code = params.get("code")
    if not code:
        raise ValueError("execute_script requires 'code'.")

    design = adsk.fusion.Design.cast(_app.activeProduct)
    root = design.rootComponent if design else None

    helpers = {
        "mm": mm,
        "cm_to_mm": cm_to_mm,
        "point3d": point3d,
        "value_input_mm": value_input_mm,
        "value_input_real": value_input_real,
        "find_body": find_body,
        "find_sketch": find_sketch,
        "plane_from_name": plane_from_name,
        "operation_from_name": operation_from_name,
    }

    namespace = {
        "adsk": adsk,
        "app": _app,
        "ui": _ui,
        "design": design,
        "root": root,
        "helpers": helpers,
        "result": None,
    }
    namespace.update(params.get("globals") or {})

    buffer = io.StringIO()
    with redirect_stdout(buffer):
        exec(compile(code, "<codex_script>", "exec"), namespace)

    result_value = namespace.get("result")
    return {
        "result": _coerce_jsonable(result_value),
        "stdout": buffer.getvalue(),
    }


def _coerce_jsonable(value):
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (list, tuple)):
        return [_coerce_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(k): _coerce_jsonable(v) for k, v in value.items()}
    return repr(value)


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

def get_status(params):
    return {
        "addin": ADDIN_NAME,
        "command_file": COMMAND_FILE,
        "response_file": RESPONSE_FILE,
        "operations": sorted(OPERATIONS.keys()),
        "active_document": _app.activeDocument.name if _app and _app.activeDocument else None,
    }


# ---------------------------------------------------------------------------
# Dispatch table
# ---------------------------------------------------------------------------

OPERATIONS = {
    # creation
    "create_box": create_box,
    "create_cylinder": create_cylinder,
    "create_sphere": create_sphere,
    "create_torus": create_torus,
    "create_sketch": create_sketch,
    "extrude_sketch": extrude_sketch,
    "revolve_sketch": revolve_sketch,
    "loft_profiles": loft_profiles,
    "sweep_along_path": sweep_along_path,
    # modify
    "fillet_body": fillet_body,
    "chamfer_body": chamfer_body,
    "shell_body": shell_body,
    "delete_body": delete_body,
    "rename_body": rename_body,
    # parameters
    "create_parameter": create_parameter,
    "set_parameter": set_parameter,
    # documents + export
    "new_document": new_document,
    "save_document": save_document,
    "close_document": close_document,
    "export_stl": export_stl,
    "export_step": export_step,
    "export_iges": export_iges,
    "export_f3d": export_f3d,
    # introspection + power tools
    "read_design": read_design,
    "execute_script": execute_script,
    "get_status": get_status,
}


# ---------------------------------------------------------------------------
# Command loop
# ---------------------------------------------------------------------------

def write_response(payload):
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp_path = RESPONSE_FILE + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, default=str)
    os.replace(tmp_path, RESPONSE_FILE)


def read_command():
    with open(COMMAND_FILE, "r", encoding="utf-8") as handle:
        return json.load(handle)


def execute_current_command():
    global _seen_command_id
    command = read_command()
    command_id = command.get("command_id")
    if not command_id or command_id == _seen_command_id:
        return

    operation = command.get("operation")
    params = command.get("params") or {}

    started = time.time()
    try:
        if operation not in OPERATIONS:
            supported = ", ".join(sorted(OPERATIONS.keys()))
            raise ValueError(f"Unsupported operation: {operation}. Supported: {supported}")
        result = OPERATIONS[operation](params)
        _seen_command_id = command_id
        write_response(
            {
                "command_id": command_id,
                "operation": operation,
                "status": "ok",
                "result": result,
                "duration_seconds": round(time.time() - started, 4),
                "time": time.time(),
            }
        )
    except Exception as exc:
        _seen_command_id = command_id
        write_response(
            {
                "command_id": command_id,
                "operation": operation,
                "status": "error",
                "error": str(exc),
                "traceback": traceback.format_exc(),
                "duration_seconds": round(time.time() - started, 4),
                "time": time.time(),
            }
        )


class CodexCommandHandler(adsk.core.CustomEventHandler):
    def notify(self, args):
        try:
            execute_current_command()
        except Exception as exc:
            write_response(
                {
                    "status": "error",
                    "error": f"Top-level handler failure: {exc}",
                    "traceback": traceback.format_exc(),
                    "time": time.time(),
                }
            )


def poll_loop():
    last_mtime = None
    while not _stop_event.is_set():
        try:
            if os.path.exists(COMMAND_FILE):
                mtime = os.path.getmtime(COMMAND_FILE)
                if mtime != last_mtime:
                    last_mtime = mtime
                    _app.fireCustomEvent(CUSTOM_EVENT_ID, "")
        except Exception:
            pass
        time.sleep(POLL_SECONDS)


def run(context):
    global _app, _ui
    _app = adsk.core.Application.get()
    _ui = _app.userInterface
    os.makedirs(STATE_DIR, exist_ok=True)

    event = _app.registerCustomEvent(CUSTOM_EVENT_ID)
    handler = CodexCommandHandler()
    event.add(handler)
    _handlers.append(handler)

    _stop_event.clear()
    thread = threading.Thread(target=poll_loop, daemon=True)
    thread.start()

    write_response(
        {
            "status": "ready",
            "addin": ADDIN_NAME,
            "command_file": COMMAND_FILE,
            "response_file": RESPONSE_FILE,
            "operations": sorted(OPERATIONS.keys()),
            "time": time.time(),
        }
    )


def stop(context):
    _stop_event.set()
    try:
        _app.unregisterCustomEvent(CUSTOM_EVENT_ID)
    except Exception:
        pass
    write_response(
        {
            "status": "stopped",
            "addin": ADDIN_NAME,
            "time": time.time(),
        }
    )
