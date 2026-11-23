# -*- coding: utf-8 -*-
"""工程工作流编排（GUI 无关层，批处理入口的引擎）。

全链路：导入几何 → 端面识别 → 材料/约束/载荷（面指纹持久化）→ 网格 →
求解 → 结果三场 → 归档 → 保存 .rmsimproject 束；打开 → 解包 → 几何副本
重导入 → 面指纹 re-resolve（不唯一命中 → 工程进入「需重新确认」，
is_confirmed=False，禁止求解——绝不静默贴错面，调研报告 §7）→
修改参数（标脏）→ 重算生成新 case（旧 case 产物永不覆盖）。

本模块是层间编排点（ARCHITECTURE §2）：调用 cad/mesh/solver/core 各层公开
API，不 import PySide6。批处理入口：main.py --batch <spec.json> 或
rm_sim.batch.run_batch_action。
"""
from __future__ import annotations

import datetime as _dt
from dataclasses import asdict
from pathlib import Path

import numpy as np

from ..cad.step_import import import_step
from ..config import get_ccx_path, load_config
from ..core.face_identity import fingerprint_from_face, resolve_face
from ..core.materials import find_material
from ..core.project import CaseRecord, Constraint, LoadCase, Project, Study, TractionLoad
from ..core.project_bundle import open_bundle, save_bundle
from ..mesh.mesher import generate_tet10_mesh
from .deck import build_case_inp
from .fields import build_result_fields
from .tasks import SolveScheduler


class WorkflowError(RuntimeError):
    """工程工作流失败（含拒绝求解等显式原因）。"""


def _ccx() -> str:
    return get_ccx_path(load_config())


def _end_faces(model):  # noqa: ANN001
    """识别 ±x 两块平面端面（法向沿 x 的面中，center.x 最小=固定端、最大=加载端）。"""
    axis_faces = [f for f in model.faces if abs(abs(f.normal[0]) - 1.0) <= 1e-9]
    if len(axis_faces) != 2:
        raise WorkflowError(
            f"端面识别失败：期望 2 块 x 向法向平面端面，实际 {len(axis_faces)} 块"
        )
    axis_faces.sort(key=lambda f: f.center[0])
    return axis_faces[0], axis_faces[1]  # 固定端（x≈0）、加载端（x≈L）


def _bbox_diag(model) -> float:  # noqa: ANN001
    lo, hi = model.overall_bbox
    return float(np.linalg.norm(np.asarray(hi) - np.asarray(lo)))


def part_scoped_faces(project, model, part_id: str) -> list:  # noqa: ANN001
    """(part_id) → 该部件的 occ 面清单（模块 14；装配级挂接的解析域）。

    part_id 为空 → 全模型面清单（旧版单零件语义，行为不变）；
    非空 → project.part_by_id 定位部件（solid_index → occ 实体 tag →
    FaceInfo.solid_tag 过滤）。未知 part_id 抛 KeyError、solid_index 越界抛
    WorkflowError——均显式，绝不静默回退到全模型面清单（防跨部件贴错面）。
    """
    if not part_id:
        return list(model.faces)
    part = project.part_by_id(part_id)  # 未知部件 → KeyError（显式）
    solids = list(getattr(model, "solids", []))
    if not (0 <= part.solid_index < len(solids)):
        raise WorkflowError(
            f"部件 {part_id} solid_index={part.solid_index} 超出当前模型实体数 "
            f"{len(solids)}（几何与工程不同源？）"
        )
    solid_tag = int(solids[part.solid_index].tag)
    return [f for f in model.faces if f.solid_tag == solid_tag]


def reresolve_project(project, model) -> list[str]:  # noqa: ANN001
    """面指纹 re-resolve（模块 14 装配感知）：命中回写 face_tag；未命中 →
    is_confirmed=False + 警告。

    挂接目标 = (part_id, 面指纹)：part_id 非空时指纹只在**该部件**的面清单内
    解析（每部件复用 core.face_identity.resolve_face）；部件无法定位（未知
    part_id / solid_index 越界 / 非装配模型）同样按未命中处理并显式警告，
    绝不静默贴到其他部件的面。
    """
    diag = _bbox_diag(model)
    warnings: list[str] = []

    def _resolve_one(owner: str, part_id: str, fp, is_load: bool) -> int:
        """(part_id, 指纹) → occ tag；任何失败回 0 并置 is_confirmed=False。"""
        try:
            faces = part_scoped_faces(project, model, part_id)
        except (KeyError, WorkflowError) as exc:
            project.is_confirmed = False
            warnings.append(f"{owner}挂接部件 {part_id or '（空）'} 无法定位: {exc}——需重新确认")
            return 0
        tag, status = resolve_face(faces, fp, diag)
        if status == "ok":
            return int(tag)
        project.is_confirmed = False
        scope = f"部件 {part_id}" if part_id else "模型"
        warnings.append(
            f"{owner}面指纹在{scope}面清单中未唯一命中（{status}，面积 "
            f"{fp.area_mm2:.6g} mm²）——需重新确认"
        )
        return 0

    project.is_confirmed = True
    # 身份先匹配，禁止将旧 solid_index 当作持久化身份。
    if len(project.parts) > 1:
        from ..core.part_identity import part_matches_fingerprint
        claimed = set()
        if len(project.parts) != len(model.solids):
            project.is_confirmed = False
            warnings.append("部件数量变化——需重新确认")
        for part in project.parts:
            hits = [i for i, solid in enumerate(model.solids)
                    if part.fingerprint is not None and part_matches_fingerprint(solid, part.fingerprint, diag)]
            if len(hits) != 1 or hits[0] in claimed:
                project.is_confirmed = False
                part.solid_tag = 0
                warnings.append(f"部件 {part.part_id} 几何指纹未唯一命中——需重新确认")
                continue
            part.solid_index = hits[0]
            part.solid_tag = int(model.solids[hits[0]].tag)
            part.source = str(model.path)
            claimed.add(hits[0])
    for case in project.study.load_cases:
        for c in case.constraints:
            if c.face is None:
                continue
            c.face_tag = _resolve_one(f"约束「{c.name}」", c.part_id, c.face, is_load=False)
        for ld in case.loads:
            fp = getattr(ld, "face", None)
            if fp is None:
                continue
            ld.face_tag = _resolve_one(f"载荷「{ld.name}」", getattr(ld, "part_id", ""), fp, is_load=True)
    for conn in project.connections:
        for side, label in ((conn.side_a, "A"), (conn.side_b, "B")):
            _resolve_one(f"连接「{conn.name}」{label}侧", side.part_id, side.face, is_load=False)
    if project.is_confirmed and any(c.enabled for c in project.study.mesh_controls):
        from ..cad.mesh_targets import resolve_controls
        try:resolve_controls(project,model,project.study.mesh_size_mm)
        except ValueError as exc:
            project.is_confirmed=False;warnings.append(str(exc))
    return warnings


def _mesh_and_solve(project, model, mesh_size_mm: float | None = None) -> dict:
    """Share GUI/batch preparation, all face sets and material checks."""
    from .assembly_workflow import solve_assembly
    if mesh_size_mm is not None:
        project.study.mesh_size_mm = float(mesh_size_mm)
    return solve_assembly(project, model)


def new_solve_project(
    project_dir: str | Path,
    step_source: str | Path,
    mesh_size_mm: float = 2.0,
    force_n: float = 100.0,
    material_name: str = "45钢",
    direction: tuple = (0.0, -1.0, 0.0),
) -> dict:
    """进程 A：新建工程 → 导入 → 材料/约束/载荷 → 网格 → 求解 → 归档 → 保存束。"""
    project_dir = Path(project_dir)
    project_dir.mkdir(parents=True, exist_ok=True)
    model = import_step(step_source)
    fixed_face, load_face = _end_faces(model)

    project = Project(
        name=Path(step_source).stem,
        project_dir=str(project_dir),
        is_confirmed=True,
    )
    project.study = Study(
        name="静力分析",
        material=find_material(material_name),
        mesh_size_mm=float(mesh_size_mm),
        geometry_file=str(Path(step_source).resolve()),
        load_cases=[
            LoadCase(
                name="工况1：端部载荷",
                constraints=[
                    Constraint(name="固定端", face_tag=int(fixed_face.tag),
                               face=fingerprint_from_face(fixed_face))
                ],
                loads=[
                    TractionLoad(
                        name="端面总力", face_tag=int(load_face.tag),
                        face=fingerprint_from_face(load_face),
                            total_force_n=float(force_n), direction=tuple(direction),
                    )
                ],
            )
        ],
    )
    solve = _mesh_and_solve(project, model, mesh_size_mm)
    bundle = save_bundle(
        project_dir / "project.rmsimproject", project,
        geometry_source=step_source, cases_root=project_dir / "cases",
    )
    return {
        "project_dir": str(project_dir),
        "bundle": str(bundle),
        "material_name": project.study.material.name,
        "force_n": float(force_n),
        "mesh_size_mm": float(mesh_size_mm),
        "fixed_face": {"area_mm2": fixed_face.area_mm2,
                       "centroid": list(fixed_face.center), "normal": list(fixed_face.normal)},
        "load_face": {"area_mm2": load_face.area_mm2,
                      "centroid": list(load_face.center), "normal": list(load_face.normal)},
        "solve": solve,
    }


def open_and_reresolve(bundle_path: str | Path, work_dir: str | Path) -> dict:
    """进程 打开：解包 + 几何副本重导入 + 面指纹 re-resolve（不求解）。"""
    opened = open_bundle(bundle_path, work_dir)
    project = opened["project"]
    if not opened["geometry_path"]:
        raise WorkflowError(f"工程束内没有几何副本: {bundle_path}")
    project.project_dir = str(Path(work_dir))
    if opened["geometry_path"]:
        project.study.geometry_file = str(opened["geometry_path"])
    from ..cad.assembly_import import import_assembly
    model = (import_assembly(opened["geometry_path"]) if len(project.parts) > 1
             else import_step(opened["geometry_path"]))
    warnings = reresolve_project(project, model)
    resolved = {}
    case = project.study.load_cases[0]
    for c in case.constraints:
        if c.face is not None and c.face_tag > 0:
            fi = model.face_by_tag(c.face_tag)
            resolved.setdefault("fixed_face", {
                "area_mm2": fi.area_mm2, "centroid": list(fi.center),
                "normal": list(fi.normal), "saved_area_mm2": c.face.area_mm2,
                "saved_centroid": list(c.face.centroid), "saved_normal": list(c.face.normal),
            })
    for ld in case.loads:
        fp = getattr(ld, "face", None)
        if fp is not None and getattr(ld, "face_tag", 0) > 0:
            fi = model.face_by_tag(ld.face_tag)
            resolved.setdefault("load_face", {
                "area_mm2": fi.area_mm2, "centroid": list(fi.center),
                "normal": list(fi.normal), "saved_area_mm2": fp.area_mm2,
                "saved_centroid": list(fp.centroid), "saved_normal": list(fp.normal),
            })
    return {
        "project": project,
        "model": model,
        "confirmed": bool(project.is_confirmed),
        "warnings": warnings,
        "resolved": resolved,
        "cases_dir": str(opened["cases_dir"]) if opened["cases_dir"] else None,
        "cases": [
            {"name": r.name, "job_name": r.job_name, "load_summary": r.load_summary,
             "tip_uy_mm": r.tip_uy_mm, "max_mises_mpa": r.max_mises_mpa}
            for r in project.cases
        ],
        "force_n": next(
            (float(ld.total_force_n) for case in project.study.load_cases
             for ld in case.loads if type(ld).__name__ == "TractionLoad"),
            None,
        ),
        "mesh_size_mm": None,  # 由调用方从 deck/mesh.inp 或工程记录读取
        "material_name": project.study.material.name,  # 最终验收补充：project_open_check 消费（batch.py）
        "parts": [asdict(p) for p in project.parts],
        "connections": [asdict(c) for c in project.connections],
    }


def modify_force_and_reresolve(
    bundle_path: str | Path, work_dir: str | Path, new_force_n: float,
    friction_mu: float | None = None, pretension_n: float | None = None,
) -> dict:
    """进程 B 全流程：重开校验 → 改载荷（标脏）→ 重算新 case → 归档 → 回存束。"""
    opened = open_and_reresolve(bundle_path, work_dir)
    project, model = opened["project"], opened["model"]
    if not project.is_confirmed:
        return {
            "refused": True, "confirmed": False, "warnings": opened["warnings"],
            "reason": "面指纹 re-resolve 未全部唯一命中，工程处于「需重新确认」状态",
        }
    traction = next(
        (ld for case in project.study.load_cases for ld in case.loads
         if type(ld).__name__ == "TractionLoad"),
        None,
    )
    if traction is None:
        raise WorkflowError("工程中没有面力载荷可修改")
    old_force = float(traction.total_force_n)
    traction.total_force_n = float(new_force_n)
    project.dirty = True  # 修改载荷 → 标脏（重算生成新 case 后清除）
    for conn in project.connections:
        if friction_mu is not None and hasattr(conn, "friction_mu"):
            conn.friction_mu = float(friction_mu)
        if pretension_n is not None and hasattr(conn, "pretension_n"):
            conn.pretension_n = float(pretension_n)
        conn.validate([p.part_id for p in project.parts])
    solve = _mesh_and_solve(project, model)  # 网格参数取工程记录（MeshSize 不变）
    copies = None
    if len(project.parts) > 1:
        from ..cad.assembly_import import export_part_steps
        copies = export_part_steps(project, model, Path(project.project_dir)/"part_geometry")
    bundle = save_bundle(
        bundle_path, project,
        geometry_source=project.study.geometry_file,
        cases_root=Path(project.project_dir) / "cases",
        part_geometry_sources=copies,
    )
    return {
        "refused": False,
        "confirmed": True,
        "warnings": opened["warnings"],
        "reopened": {
            "material_name": project.study.material.name,
            "old_force_n": old_force,
            "new_force_n": float(traction.total_force_n),
            "mesh_size_mm": float(project.study.mesh_size_mm),
            "resolved": opened["resolved"],
            "cases_dir": opened["cases_dir"],
            "parts": opened["parts"], "connections": opened["connections"],
        },
        "modified": {"dirty_set": True, "new_force_n": float(new_force_n)},
        "solve": solve,
        "cases": [
            {"name": r.name, "load_summary": r.load_summary, "tip_uy_mm": r.tip_uy_mm}
            for r in project.cases
        ],
        "bundle": str(bundle),
    }


def open_readonly(bundle_path: str | Path, work_dir: str | Path) -> dict:
    """进程 C：再重开（只读）：当前工况 + 历史 case 可读。"""
    opened = open_and_reresolve(bundle_path, work_dir)
    project = opened["project"]
    cases_readable = []
    cases_root = opened.get("cases_dir")
    for r in project.cases:
        rj = Path(cases_root) / r.name / "results.json" if cases_root else None
        cases_readable.append({
            "name": r.name,
            "load_summary": r.load_summary,
            "tip_uy_mm": r.tip_uy_mm,
            "results_json_readable": bool(rj and rj.is_file()),
        })
    return {
        "confirmed": opened["confirmed"],
        "warnings": opened["warnings"], "parts": opened["parts"], "connections": opened["connections"],
        "force_n": opened["force_n"],
        "material_name": project.study.material.name,
        "cases": cases_readable,
        "n_cases": len(project.cases),
    }


def try_solve_current(bundle_path: str | Path, work_dir: str | Path) -> dict:
    """⑤ 指纹篡改负例：尝试求解 → 未确认则拒绝（不抛出，返回拒绝报告）。"""
    opened = open_and_reresolve(bundle_path, work_dir)
    if not opened["confirmed"]:
        return {
            "refused": True,
            "confirmed": False,
            "warnings": opened["warnings"],
            "reason": "；".join(opened["warnings"]) or "面指纹未唯一命中",
        }
    try:
        solve = _mesh_and_solve(opened["project"], opened["model"], mesh_size_mm=2.0)
    except WorkflowError as exc:
        return {"refused": True, "confirmed": opened["confirmed"], "reason": str(exc)}
    return {"refused": False, "confirmed": True, "solve": solve}
