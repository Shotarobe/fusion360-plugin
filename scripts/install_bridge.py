#!/usr/bin/env python3
"""Install the Codex Fusion 360 bridge Add-In into the user's Fusion API folder.

This installs a single Add-In named ``CodexFusionBridge``. If the older
``CodexFusionBridgeModern`` directory is still present it is removed, since
the bridge now uses one channel.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SOURCE = PLUGIN_ROOT / "scripts" / "fusion_bridge_addin.py"

if sys.platform == "darwin":
    FUSION_API_ROOT = (
        Path.home()
        / "Library"
        / "Application Support"
        / "Autodesk"
        / "Autodesk Fusion 360"
        / "API"
    )
elif sys.platform.startswith("win"):
    FUSION_API_ROOT = Path.home() / "AppData" / "Roaming" / "Autodesk" / "Autodesk Fusion 360" / "API"
else:
    raise SystemExit(f"Unsupported platform for Fusion 360: {sys.platform}")


ADDIN = {
    "name": "CodexFusionBridge",
    "version": "0.3.0",
    "description": "Codex Fusion 360 modeling bridge (execute_script, read_design, exports).",
}

# Older names that should be cleaned out if present.
LEGACY_NAMES = ("CodexFusionBridgeModern",)


def install_addin() -> Path:
    name = ADDIN["name"]
    addin_dir = FUSION_API_ROOT / "AddIns" / name
    target = addin_dir / f"{name}.py"
    manifest_path = addin_dir / f"{name}.manifest"

    addin_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(SOURCE, target)

    manifest = {
        "autodeskProduct": "Fusion360",
        "type": "addin",
        "id": name,
        "author": "shota",
        "description": {"": ADDIN["description"]},
        "version": ADDIN["version"],
        "runOnStartup": False,
        "supportedOS": "mac|windows",
        "editEnabled": True,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return addin_dir


def remove_legacy_addins() -> list[Path]:
    removed = []
    for legacy in LEGACY_NAMES:
        legacy_dir = FUSION_API_ROOT / "AddIns" / legacy
        if legacy_dir.exists():
            shutil.rmtree(legacy_dir)
            removed.append(legacy_dir)
    return removed


def main() -> None:
    if not SOURCE.exists():
        raise SystemExit(f"Bridge source not found: {SOURCE}")

    addin_dir = install_addin()
    print(f"Installed Fusion 360 Add-In: {addin_dir}")

    for removed in remove_legacy_addins():
        print(f"Removed legacy Add-In: {removed}")

    print(
        "\nNext: open Fusion 360 -> Utilities -> Scripts and Add-Ins -> "
        "Add-Ins tab -> CodexFusionBridge -> Run.\n"
        "Enable 'Run on Startup' if you want commands to keep working after relaunch."
    )


if __name__ == "__main__":
    main()
