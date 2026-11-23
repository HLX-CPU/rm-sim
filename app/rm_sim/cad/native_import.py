"""Optional SolidWorks assembly conversion; source files are opened read-only.

The manifest keeps native configurations/material labels separate from confirmed
analysis inputs. Component/body names are matched in assembly coordinates, never
by assuming STEP entity order equals native component order.
"""
from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path
import subprocess

import numpy as np


def identify_native_bodies(model, record):
    from .step_import import import_step

    candidates = []
    for item in record["Parts"]:
        local = import_step(item["Output"])
        try:
            for instance in record["Instances"]:
                if instance["Suppressed"] or instance["Source"].casefold() != item["Source"].casefold():
                    continue
                if instance["Configuration"] != item["Configuration"]:
                    continue
                transform = np.asarray(instance["Transform"], dtype=float)
                if transform.shape != (16,):
                    raise ValueError("Native component has no complete transform: " + instance["Name"])
                for index, solid in enumerate(local.solids, 1):
                    center = (np.asarray(solid.center_of_mass) @ transform[:9].reshape(3, 3)
                              * transform[12] + transform[9:12] * 1000.)
                    candidates.append({"name": f"{instance['Name']} / body{index}",
                                       "source": item["Source"], "configuration": item["Configuration"],
                                       "material_label": item["Material"], "center": center,
                                       "volume": solid.volume_mm3 * abs(transform[12])**3})
        finally:
            local.close()
    if len(candidates) != len(model.solids):
        raise ValueError(f"Native/STEP body counts disagree: {len(candidates)} / {len(model.solids)}")
    result, used = [], set()
    for solid in model.solids:
        matches = [(i, c) for i, c in enumerate(candidates)
                   if np.linalg.norm(c["center"] - solid.center_of_mass) < .02
                   and abs(c["volume"] - solid.volume_mm3) < max(.01, solid.volume_mm3 * 1e-5)]
        if len(matches) != 1 or matches[0][0] in used:
            raise ValueError(f"Cannot uniquely match native body to STEP solid {solid.tag}")
        index, candidate = matches[0]
        used.add(index)
        result.append({"solid_tag": solid.tag, **{k: v for k, v in candidate.items() if k != "center"},
                       "center": list(solid.center_of_mass), "volume": solid.volume_mm3})
    model.parts = tuple(replace(part, name=row["name"]) for part, row in zip(model.parts, result))
    return result


def load_native_export(directory):
    """Validate and read an existing export, including a geometry volume check."""
    from .assembly_import import import_assembly

    directory = Path(directory)
    record = json.loads((directory / "native_export.json").read_text(encoding="utf-8-sig"))
    if not record["Success"] or record["HashBefore"] != record["HashAfter"]:
        raise ValueError("Native export failed or modified its source")
    model = import_assembly(record["AssemblyStep"])
    try:
        expected = record["VolumeM3"] * 1e9
        if expected <= 0 or abs(model.total_volume_mm3 - expected) / expected > 1e-4:
            raise ValueError("Native/STEP volume mismatch exceeds 0.01%")
        mapping = identify_native_bodies(model, record)
        return {"model": model, "record": record, "body_mapping": mapping,
                "step_sha256": hashlib.sha256(model.path.read_bytes()).hexdigest()}
    except Exception:
        model.close()
        raise


def export_native_assembly(source, output_directory):
    """Call the installed SolidWorks API; export logs and all evidence persist."""
    output = Path(output_directory).resolve()
    if output.exists() and any(output.iterdir()):
        raise ValueError("Please choose an empty conversion directory")
    output.mkdir(parents=True, exist_ok=True)
    process = subprocess.run(export_command(source, output),
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             creationflags=subprocess.CREATE_NO_WINDOW)
    (output / "export.stdout.log").write_bytes(process.stdout)
    (output / "export.stderr.log").write_bytes(process.stderr)
    if process.returncode != 0:
        raise RuntimeError(f"SolidWorks export failed (exit {process.returncode}); see {output}")
    result = load_native_export(output)
    (output / "body_mapping.json").write_text(json.dumps(result["body_mapping"], ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def export_command(source, output):
    script = Path(__file__).resolve().parents[3] / "tools" / "export_native_assembly.ps1"
    return ["powershell.exe", "-NoLogo", "-NoProfile", "-STA", "-ExecutionPolicy", "Bypass",
            "-File", str(script), "-Source", str(Path(source).resolve()),
            "-OutputDirectory", str(Path(output).resolve())]


def project_from_export(result, directory):
    """Make CAD provenance explicit; unknown materials cannot inherit steel silently."""
    from .assembly_import import sync_parts_from_model
    from ..core.project import Project, Material

    project = Project(name=Path(result["record"]["Source"]).stem, project_dir=str(directory))
    model = result["model"]
    sync_parts_from_model(project, model)
    project.study.geometry_file = str(model.path)
    for part, row in zip(project.parts, result["body_mapping"]):
        if "6061" in row["material_label"]:
            name = row["material_label"] + "（CAD标签；线弹性参考）"
            part.material_name = name
            part.material_snapshot = Material(name=name, e_modulus_mpa=68900., poisson=.33,
                                                density_t_mm3=2.7e-9, yield_strength_mpa=None)
        else:
            part.material_confirmed = False
    project.geometry_provenance = {"source": result["record"]["Source"],
        "source_sha256": result["record"]["HashBefore"], "step_sha256": result["step_sha256"],
        "native_configuration": result["record"]["Configuration"], "body_mapping": result["body_mapping"],
        "open_warning_bits": result["record"]["OpenWarnings"],
        "material_note": "CAD material labels are not certificates; 6061 E/nu/rho are reference assumptions, yield unknown."}
    project.dirty = True
    return project
