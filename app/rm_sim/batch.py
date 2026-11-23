# -*- coding: utf-8 -*-
"""批处理入口（GUI 无关层）：进程级工程工作流的动作分发。

调用方式（两种等效）：
- ``main.py --batch spec.json``：spec 含 {"action": ..., "result_path": ..., 参数...}，
  执行后把结果 JSON 写入 result_path（供验收脚本的 A/B/C 三进程断言）；
- ``python -c "from rm_sim.batch import run_batch_action; ..."``。

本模块不 import PySide6（红线②）；动作引擎在 solver.project_workflow。
"""
from __future__ import annotations

import json
from pathlib import Path

from .solver import project_workflow


class BatchError(RuntimeError):
    """批处理动作失败（含明确原因）。"""


def run_batch_action(spec: dict) -> dict:  # noqa: ANN001
    """按 spec["action"] 分发执行；返回结果 dict（JSON 可序列化）。"""
    action = spec.get("action", "")
    if action == "chrono_demo":
        from .solver.chrono_adapter import run_chrono
        return run_chrono(spec["output_dir"])
    if action == "chrono_load_transfer":
        from .solver.chrono_adapter import transfer_load
        return transfer_load(spec["source_json"],spec["project_dir"],spec["step_source"],int(spec.get("sample_index",0)))
    if action == "modal_beam":
        from .solver.modal import beam_modal
        return beam_modal(spec["project_dir"],spec["step_source"],int(spec.get("modes",6)))
    if action == "assembly_new_solve":
        from .solver.assembly_workflow import new_assembly_project
        return new_assembly_project(spec["project_dir"], spec["step_source"],
            force_n=float(spec.get("force_n", 1000.0)), friction_mu=float(spec.get("friction_mu", .3)),
            mesh_size_mm=float(spec.get("mesh_size_mm", 2.0)), connection=spec.get("connection", "contact"),
            gravity=bool(spec.get("gravity", True)))
    if action == "project_new_solve":
        return project_workflow.new_solve_project(
            project_dir=spec["project_dir"],
            step_source=spec["step_source"],
            mesh_size_mm=float(spec.get("mesh_size_mm", 2.0)),
            force_n=float(spec.get("force_n", 100.0)),
            material_name=spec.get("material_name", "45钢"),
        )
    if action == "project_open_check":
        opened = project_workflow.open_and_reresolve(
            spec["bundle_path"], spec["work_dir"]
        )
        return {
            "confirmed": opened["confirmed"],
            "warnings": opened["warnings"],
            "resolved": opened["resolved"],
            "force_n": opened["force_n"],
            "material_name": opened["material_name"],
            "cases": opened["cases"],
            "parts": opened["parts"], "connections": opened["connections"],
        }
    if action == "project_modify_resolve":
        return project_workflow.modify_force_and_reresolve(
            spec["bundle_path"], spec["work_dir"], float(spec["new_force_n"]),
            friction_mu=spec.get("friction_mu"), pretension_n=spec.get("pretension_n")
        )
    if action == "project_open_readonly":
        return project_workflow.open_readonly(spec["bundle_path"], spec["work_dir"])
    if action == "project_try_solve":
        return project_workflow.try_solve_current(spec["bundle_path"], spec["work_dir"])
    raise BatchError(f"未知批处理动作: {action!r}")


def run_batch_spec(spec_path: str | Path) -> tuple[int, dict]:
    """执行 spec 文件并把结果 JSON 写入 spec["result_path"]；返回 (退出码, 结果)。"""
    sp = Path(spec_path)
    spec = json.loads(sp.read_text(encoding="utf-8"))
    result_path = spec.get("result_path")
    try:
        result = run_batch_action(spec)
        result["batch_ok"] = True
        code = 0
    except Exception as exc:  # noqa: BLE001 - 批处理进程自身不得带崩
        result = {"batch_ok": False, "error": f"{type(exc).__name__}: {exc}"}
        code = 2
    if result_path:
        rp = Path(result_path)
        rp.parent.mkdir(parents=True, exist_ok=True)
        rp.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return code, result
