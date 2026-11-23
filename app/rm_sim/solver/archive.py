# -*- coding: utf-8 -*-
"""求解产物归档与计算报告编排（GUI 无关层，solver 层）。

调研报告 §7：每次求解作为独立任务保留 输入/日志/退出状态/原始结果；
本模块在其上补齐 results.json 与 Markdown 计算报告，落点为
**工程目录 cases/case_NNN/**（与调度红线①一致：目录先建后解，
ccx 产物由调度器直接写在该目录——不做二次拷贝）。

上下文 ctx 由 build_archive_context 组装（纯 dict），报告文本由
report.markdown_report 生成（纯函数）；本模块是层间编排点：
import core / mesh.mesh_data / solver.* / report.plot_aggs（渲染纯函数，
文件产出、无 GUI），ARCHITECTURE §2 已注明。
"""
from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path
from typing import Any

import numpy as np

from ..mesh.mesh_data import boundary_faces_corners
from ..report.markdown_report import build_report_md, build_results_json
from ..report.plot_aggs import render_field_png


class ArchiveError(RuntimeError):
    """归档失败。"""


def build_archive_context(
    job_name: str,  # noqa: ANN001
    task,  # noqa: ANN001 - solver.tasks.SolveTask
    study,  # noqa: ANN001 - core.Study
    load_case,  # noqa: ANN001 - core.LoadCase
    mesh_stats,  # noqa: ANN001 - mesh.mesher.MeshStats
    fields,  # noqa: ANN001 - solver.fields.ResultFields
    results,  # noqa: ANN001 - solver.results.SolveResults（.dat 主解析）
    tip_node: int,
    geometry_file: str = "",
) -> dict[str, Any]:
    """组装报告/results.json 的全精度上下文（纯 dict，report 层零依赖）。

    tip_node：端部节点（加载面节点中距面中心最近者，由调用方按网格+NSET 计算）。
    """
    mat = study.material
    u_mag = float(np.max(np.linalg.norm(fields.u, axis=1)))
    max_mises = float(np.max(fields.s_mises))
    yield_mpa = mat.yield_strength_mpa
    tip_row = int(np.searchsorted(fields.node_ids, int(tip_node)))
    if tip_row >= len(fields.node_ids) or int(fields.node_ids[tip_row]) != int(tip_node):
        raise ArchiveError(f"tip 节点 {tip_node} 不在结果场节点表中")
    tip_uy = float(fields.u[tip_row][1])
    ctx: dict[str, Any] = {
        "job_name": job_name,
        "created": _dt.datetime.now().isoformat(timespec="milliseconds"),
        "state": task.state,
        "exit_code": task.exit_code,
        "exit_code_note": task.exit_code_note,
        "job_finished": bool(task.job_finished),
        "resource_usage": getattr(task,'resource_usage',{}),
        "solver_diagnostics": getattr(task,'diagnostics',{}),
        "geometry_file": str(geometry_file),
        "material": {
            "name": mat.name,
            "e_modulus_mpa": float(mat.e_modulus_mpa),
            "poisson": float(mat.poisson),
            "density_t_mm3": float(mat.density_t_mm3),
            "yield_strength_mpa": None if mat.yield_strength_mpa is None else float(mat.yield_strength_mpa),
        },
        "load_case": {
            "name": load_case.name,
            "constraints": [c.summary() for c in load_case.constraints],
            "loads": [ld.summary() for ld in load_case.loads],
        },
        "mesh": {
            "element_count": int(mesh_stats.element_count),
            "node_count": int(mesh_stats.node_count),
            "element_type": mesh_stats.element_type,
            "mesh_size_mm": float(mesh_stats.mesh_size_mm),
            "order": int(mesh_stats.order),
            "negative_jacobian_count": int(mesh_stats.negative_jacobian_count),
            "min_sicn": float(mesh_stats.min_sicn),
            "mean_sicn": float(mesh_stats.mean_sicn),
        },
        "results": {
            "tip_node": int(tip_node),
            "tip_uy_mm": float(tip_uy),
            "max_u_mag_mm": u_mag,
            "max_mises_mpa": max_mises,
            "rf_sum_n": [float(v) for v in fields.rf_sum],
            "rf_node_count": len(fields.rf_table),
            "u_node_count": len(results.u_block("LOADFACE")),
            "frd_node_count": int(results.frd_node_count()),
        },
        "yield_check": {
            "yield_mpa": None if yield_mpa is None else float(yield_mpa),
            "max_mises_mpa": max_mises,
            "exceeded": bool(yield_mpa is not None and max_mises > yield_mpa),
            "margin": (float(yield_mpa) / max_mises) if (yield_mpa and max_mises > 0) else None,
        },
        "files": {
            "inp": str(task.inp_path),
            "dat": str(task.dat_path),
            "frd": str(task.frd_path),
            "log": str(task.case_dir / f"{task.job_name}.log"),
        },
        "reaction_processing": fields.source.get('reaction',{}),
    }
    eq = _equilibrium(load_case, fields)
    if eq is not None:
        ctx["equilibrium"] = eq
    return ctx


def _equilibrium(load_case, fields):  # noqa: ANN001
    """对已支持的单步静力载荷检查修正后支反力与总外力的平衡。"""
    reaction = fields.source.get('reaction',{})
    if not reaction.get('supported'): return None
    applied = np.asarray(reaction['applied_total_n'])
    rf = np.asarray(fields.rf_sum, dtype=float)
    residual = float(np.linalg.norm(rf + applied))
    return {
        "applied_desc": "最终INP节点力、自重及压力的一致节点载荷积分",
        "applied_n": [float(v) for v in applied],
        "rf_sum_n": [float(v) for v in rf],
        "residual": residual,
        "reaction_corrected":True,
    }


def archive_case(
    project_dir: str | Path,
    *,
    job_name: str,
    task,  # noqa: ANN001
    study,  # noqa: ANN001
    load_case,  # noqa: ANN001
    mesh_result,  # noqa: ANN001 - mesh.MeshResult（边界面 + 节点 tag）
    mesh_stats,  # noqa: ANN001
    fields,  # noqa: ANN001
    results,  # noqa: ANN001
    geometry_file: str = "",
    tip_node: int = 0,
) -> dict[str, Path]:
    """把一次成功求解归档进 工程目录/cases/case_NNN/：
    results.json、report.md、mises.png、displaced.png（ccx 产物与 .inp/.log
    已由调度器写入同目录）。

    tip_node：端部节点（加载面节点中距面中心最近者，调用方按网格+NSET 计算）。
    校验：task.case_dir 必须位于 project_dir/cases 下（与调度红线①一致）。
    返回 {"case_dir", "results_json", "report_md", "mises_png", "displaced_png"}。
    """
    case_dir = Path(task.case_dir)
    cases_root = (Path(project_dir) / "cases").resolve()
    if case_dir.resolve().parent != cases_root:
        raise ArchiveError(
            f"case 目录 {case_dir} 不在工程归档区 {cases_root} 下"
            "（调度 work_root 必须为 project_dir/cases）"
        )
    if task.state != "completed":
        raise ArchiveError(f"仅归档 completed 任务，当前 {task.state}: {task.summary()}")

    ctx = build_archive_context(job_name, task, study, load_case, mesh_stats, fields,
                                results, tip_node=int(tip_node), geometry_file=geometry_file)
    # 文件路径与云图名先定全量 ctx，再一次性落盘——保证附录与 results.json 严格同文
    results_json_path = case_dir / "results.json"
    ctx['mesh_quality']=mesh_result.quality
    report_md_path = case_dir / "report.md"
    ctx["files"]["results_json"] = str(results_json_path)
    ctx["files"]["report_md"] = str(report_md_path)
    ctx["png"] = {"mises": "mises.png", "displaced": "displaced.png"}

    # 云图：结果显示表面 = 体积网格边界面（与三场行同源对齐，模块 8 教训）
    tris = boundary_faces_corners(mesh_result.mesh.cells["tetra10"][:, :4])
    u_mag_rows = np.linalg.norm(fields.u, axis=1)
    _, info_m = render_field_png(
        mesh_result.mesh.points, tris, fields.s_mises,
        title="von Mises 云图", unit="MPa", scalar_label="σ_vM [MPa]",
    )
    _, info_d = render_field_png(
        mesh_result.mesh.points, tris, u_mag_rows,
        title="位移云图 |U|", unit="mm", scalar_label="|U| [mm]",
        deform_u=fields.u, deform_scale=100.0,
    )
    mises_png = case_dir / "mises.png"
    displaced_png = case_dir / "displaced.png"
    mises_png.write_bytes(info_m["png_bytes"])
    displaced_png.write_bytes(info_d["png_bytes"])

    results_json_path.write_text(build_results_json(ctx), encoding="utf-8")
    report_md_path.write_text(build_report_md(ctx), encoding="utf-8")

    return {
        "case_dir": case_dir,
        "results_json": results_json_path,
        "report_md": report_md_path,
        "mises_png": mises_png,
        "displaced_png": displaced_png,
    }
