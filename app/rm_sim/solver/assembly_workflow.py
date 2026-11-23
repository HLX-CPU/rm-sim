# -*- coding: utf-8 -*-
"""统一的零件/装配准备、结果回收及装配示例工作流，无 GUI 依赖。"""
from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
import datetime
import json
import logging
import re

import numpy as np

from ..core.project import CaseRecord
from ..core.materials import part_material
from ..mesh.mesher import generate_tet10_mesh
from .deck import build_case_inp, build_assembly_case_inp, parse_mesh_inp
from .bond_band import BOND_BAND_DEFAULT_MM
from .fields import build_result_fields, part_field_views, field_surface


log = logging.getLogger("rm_sim.solver.assembly_workflow")

# 自动网格升级参数：首次仍按原配方（min=max=size），负 Jacobian/质量无效时改用
# 曲率自适应 + 高阶优化重试（整装 20 实体探针实测：均匀 3mm 有 2520 个负单元，
# 2mm+curvature24+HighOrderElastic/HighOrder 后归零）。
AUTO_MESH_CURVATURE = 24.0
AUTO_MESH_MIN_SIZE_RATIO = 0.075
AUTO_MESH_MIN_SIZE_CAP = 0.15


def prepare_analysis(project, model, mesh_path, size=None, auto_mesh=False, mesh_kwargs=None, progress=None,
                     mesh_only=False, bond_band_mm=None):
    """所有边界/加载面分别建集；装配按保存的部件顺序建立 PARTn。

    mesh_kwargs：显式网格配方（curvature / min_size_mm / high_order_optimize），
    作为首次尝试的参数。用于「已知某档尺寸在默认升级配方下会超出内存」的场景——
    例如整装 4 mm 若按 auto_mesh 的 curvature=24/min=0.15 升级会得到 74 万节点，
    必须先按更粗的曲率配方（curvature=8, min=0.4 → 12.9 万节点）网格化。
    auto_mesh 仍保留：显式配方失败时继续走自动升级。
    """
    from .project_workflow import reresolve_project
    if progress: progress('validating')

    warnings = reresolve_project(project, model)
    if not project.is_confirmed:
        raise ValueError("工程需重新确认：" + "；".join(warnings))
    missing = [p.name for p in project.parts if not p.material_confirmed]
    if missing and not mesh_only:
        raise ValueError("请先指定缺失材料的部件：" + "；".join(missing))
    if not mesh_only and not project.study.load_cases:
        raise ValueError('请先建立工况并设置约束和载荷')
    case = project.study.load_cases[0] if project.study.load_cases else None
    if not mesh_only:case.validate()
    if not mesh_only and not case.is_solvable():
        raise ValueError("请设置至少一个外部固定面")
    if not mesh_only:
        # 连接复核决策闭环（模块47）：上次求解报告过未绑定从面节点的连接
        # （needs_review）必须逐条决策后才允许再求解；不静默通过。
        from ..core.connections import undecided_block_message
        block = undecided_block_message(project)
        if block:
            raise ValueError(block)
    assembly = len(project.parts) > 1
    if len(project.parts) == 1 and not mesh_only:
        project.study.material = part_material(project, project.parts[0])
    nsets = {}
    fixed = []
    owners = [] if mesh_only else [(x, "FIXED") for x in case.constraints if x.kind == "fixed"] + [
        (x, "LOADFACE") for x in case.loads if type(x).__name__ != "GravityLoad"
    ]
    for owner, purpose in owners:
        tag = int(owner.face_tag)
        if tag <= 0:
            raise ValueError(f"{owner.name} 尚未挂接几何面")
        if assembly:
            if not owner.part_id:
                raise ValueError(f"装配的{owner.name}必须明确作用部件")
            part = project.part_by_id(owner.part_id)
            if model.face_by_tag(tag).solid_tag != model.solids[part.solid_index].tag:
                raise ValueError(f"{owner.name}的面与部件{owner.part_id}不匹配")
        existing = next((k for k, v in nsets.items() if v == tag), None)
        name = existing or (purpose if not assembly and purpose not in nsets else f"{purpose}_FACE_{tag}")
        nsets[name] = tag
        if purpose == "FIXED" and name not in fixed:
            fixed.append(name)
    size = float(project.study.mesh_size_mm if size is None else size)
    from ..cad.mesh_targets import resolve_controls
    local_controls=resolve_controls(project,model,size)
    part_sets = {f"PART{k}": int(model.solids[p.solid_index].tag)
                 for k, p in enumerate(project.parts, 1)} if assembly else None
    merge = [getattr(c, "mode", "tie") == "merge" for c in project.connections]
    if any(merge) and not all(merge):
        raise ValueError("共节点模式会合并所有重合节点，不能与接触/其他连接混用；请改用 tie")
    def _mesh(**kwargs):
        return generate_tet10_mesh(model, size, nset_faces=nsets, part_elsets=part_sets,
                                   remove_duplicate_nodes=bool(merge and all(merge)),
                                   local_controls=local_controls,
                                   inp_path=mesh_path, **kwargs)

    attempts = [dict(mesh_kwargs or {})]
    if auto_mesh:
        attempts.append(dict(
            curvature=AUTO_MESH_CURVATURE,
            min_size_mm=min(AUTO_MESH_MIN_SIZE_CAP, float(size) * AUTO_MESH_MIN_SIZE_RATIO),
            high_order_optimize=True,
        ))
    result = None
    for index, kwargs in enumerate(attempts):
        if progress: progress('meshing' if index==0 else 'mesh_retry')
        result = _mesh(**kwargs)
        ok = (not result.stats.negative_jacobian_count
              and np.isfinite(result.stats.min_sicn) and result.stats.min_sicn > 0)
        if ok:
            if index:
                log.info("自动网格升级成功：curvature=%g, min=%g, 高阶优化=True, 单元=%d, min SICN=%.4g",
                         AUTO_MESH_CURVATURE, kwargs["min_size_mm"], result.stats.element_count,
                         result.stats.min_sicn)
            break
        if index + 1 < len(attempts):
            log.warning("网格质量不合格（负 Jacobian %d, min SICN %.4g），改用曲率加密+高阶优化重试",
                        result.stats.negative_jacobian_count, result.stats.min_sicn)
    if result.stats.negative_jacobian_count:
        raise ValueError(f"网格存在 {result.stats.negative_jacobian_count} 个负Jacobian单元，禁止求解；请细化网格或修复几何。网格文件保留：{mesh_path}")
    if not np.isfinite(result.stats.min_sicn) or result.stats.min_sicn <= 0:
        raise ValueError(f"网格质量无效（min SICN={result.stats.min_sicn}），禁止求解")
    project.study.mesh_size_mm = size
    project.study.mesh_file = str(mesh_path)
    if mesh_only:
        return {'mesh_result':result,'mesh':None,'deck':'','nset_faces':{},'fixed_sets':[],
                'assembly':assembly,'bond_band_mm':None}
    # 仍保留旧流水线的保守内存闸门；当前解析和准备复测远低于旧估计，
    # 但CCX峰值未标定，暂不据此放宽整个求解流程的预算。
    from .scale_guard import PREP_REFUSE_SHARE, available_memory_gb, python_peak_gb

    nodes = int(result.stats.node_count)
    peak_gb = python_peak_gb(nodes)
    available_gb = available_memory_gb()
    if available_gb is not None and peak_gb > PREP_REFUSE_SHARE * available_gb:
        raise ValueError(
            f"网格规模超出本机内存：{nodes} 个节点（{int(result.stats.element_count)} 单元），"
            f"沿用旧流水线的保守预算为 {peak_gb:.1f} GiB，本机当前可用 {available_gb:.1f} GiB。"
            f"继续会占满内存导致系统卡死——请把网格尺寸放大后重试（例如当前 {size:g} mm 改大 1.5~2 倍）。"
            f"网格文件保留：{mesh_path}"
        )
    text = Path(mesh_path).read_text(encoding="utf-8")
    if progress: progress('building_deck')
    mesh = parse_mesh_inp(text)
    deck = (build_assembly_case_inp(text, project, case, model, nset_face_tags=nsets, mesh=mesh,
                                    bond_band_mm=bond_band_mm)
            if assembly else build_case_inp(text, project.study, case, geom=model, nset_face_tags=nsets, mesh=mesh))
    return {"mesh_result": result, "mesh": mesh, "deck": deck,
            "nset_faces": nsets, "fixed_sets": fixed, "assembly": assembly,
            # 生效的绑定带宽（mm）：0 表示旧行为（整张 CAD 面 + 求解器默认搜索
            # 半径）。报告用它说明本次 *TIE 是否按实测几何裁过面。
            "bond_band_mm": (float(bond_band_mm) if bond_band_mm is not None else BOND_BAND_DEFAULT_MM)
                            if assembly else None}


def mesh_part_volumes(mesh):
    volumes = {}
    for name, elements in mesh["elsets"].items():
        if not re.fullmatch(r"PART\d+", name):
            continue
        total = 0.0
        for eid in elements:
            pts = np.asarray([mesh["nodes"][n] for n in mesh["elems"][eid][:4]])
            total += abs(float(np.linalg.det(pts[1:] - pts[0]))) / 6.0
        volumes[name] = total
    return volumes


def connection_pressure(task, mesh, fields):
    """从面节点应力外推的法向压应力积分；明确区别于求解器接触压力。"""
    from .ccx2para import frd_to_vtu, read_vtu_point_data
    from .convert import TET_LOCAL_FACES

    data = read_vtu_point_data(frd_to_vtu(task.frd_path))
    stress = data.get("S")
    if stress is None:
        return []
    pos = {int(n): i for i, n in enumerate(fields.node_ids)}
    output = {}
    for name, elements in mesh["elsets"].items():
        match = re.fullmatch(r"C(\d+)_(SA|SB)_(\d)", name)
        if not match:
            continue
        key = (match[1], match[2])
        entry = output.setdefault(key, {"connection": int(match[1]), "side": match[2], "normal_force_n": 0.0,
                                            "force_n": [0.0, 0.0, 0.0],
                                            "min_pressure_mpa": float("inf"), "faces": 0,
                                            "method": "从面外推应力 n·σ·n 面积分；非直接接触压力输出"})
        combo = TET_LOCAL_FACES[int(match[3])]
        for eid in elements:
            conn = mesh["elems"][eid]
            ns = [conn[k] for k in combo]
            pts = np.asarray([mesh["nodes"][n] for n in ns])
            cross = np.cross(pts[1]-pts[0], pts[2]-pts[0])
            area = float(np.linalg.norm(cross)) / 2.0
            normal = cross / (2.0 * area)
            other = next(i for i in range(4) if i not in combo)
            if np.dot(normal, np.asarray(mesh["nodes"][conn[other]])-pts.mean(axis=0)) > 0:
                normal = -normal
            s = np.mean([stress[pos[n]] for n in ns], axis=0)
            tensor = np.array([[s[0], s[3], s[5]], [s[3], s[1], s[4]], [s[5], s[4], s[2]]])
            pressure = -float(normal @ tensor @ normal)
            entry["force_n"] = (np.asarray(entry["force_n"]) + tensor @ normal * area).tolist()
            entry["normal_force_n"] += pressure * area
            entry["min_pressure_mpa"] = min(entry["min_pressure_mpa"], pressure)
            entry["faces"] += 1
    return list(output.values())


def applied_force_from_deck(project, text, *, mesh=None):
    """从最终INP积分外力；材料密度和二次几何均取该工况输入。"""
    from .nodal_loads import evaluate_nodal_loads
    if mesh is None:
        mesh = parse_mesh_inp(text)
    return np.asarray(evaluate_nodal_loads(text,mesh,())['applied_total_n'])


def finish_analysis(project, task, prepared, *, progress=None):
    """GUI 与 batch 共用真实结果回收、反力汇总、分部件场、报告和 case 索引。"""
    from .archive import archive_case
    from ..report.markdown_report import build_results_json, build_report_md

    if task.state != "completed" or not task.parse_ok:
        raise RuntimeError(task.summary())
    mesh = prepared["mesh"]
    deck_text = task.inp_path.read_text(encoding='utf-8')
    solved_mesh = parse_mesh_inp(deck_text)
    if progress: progress('building_fields')
    fields = build_result_fields(sorted(mesh["nodes"]), task.results, task.frd_path,
                                 fixed_set=prepared["fixed_sets"],inp_text=deck_text,mesh=solved_mesh)
    load_names = [k for k in prepared["nset_faces"] if k not in prepared["fixed_sets"]]
    nodes = mesh["nsets"][load_names[0]] if load_names else sorted(mesh["nodes"])
    center = np.mean([mesh["nodes"][n] for n in nodes], axis=0)
    tip = min(nodes, key=lambda n: np.linalg.norm(np.asarray(mesh["nodes"][n])-center))
    if progress: progress('rendering_report')
    arch = archive_case(project.project_dir, job_name=task.job_name, task=task,
                        study=project.study, load_case=project.study.load_cases[0],
                        mesh_result=prepared["mesh_result"], mesh_stats=prepared["mesh_result"].stats,
                        fields=fields, results=task.results, tip_node=tip,
                        geometry_file=project.study.geometry_file)
    sets = {n: set(e) for n, e in mesh["elsets"].items() if re.fullmatch(r"PART\d+", n)}
    if progress: progress('finalizing_results')
    views = part_field_views(fields, sets, mesh["elems"]) if sets else {}
    volumes = mesh_part_volumes(mesh)
    pressure = connection_pressure(task, solved_mesh, fields) if prepared["assembly"] else []
    parts = []
    for k, part in enumerate(project.parts, 1):
        name = f"PART{k}"
        view = views.get(name)
        parts.append({"part_id": part.part_id, "name": part.name, "material": asdict(part_material(project, part)),
                      "volume_mm3": volumes.get(name), "nodes": len(view.node_ids) if view else len(fields.node_ids),
                      "max_u": view.max_u_mag() if view else fields.max_u_mag(),
                      "max_mises": view.max_mises() if view else float(fields.s_mises.max())})
    assembly_info = {"parts": parts, "connections": [dict(type=type(c).__name__, **asdict(c)) for c in project.connections],
                     "interface_pressure": pressure,
                     "bond_band_mm": prepared.get("bond_band_mm"),
                     "assumptions": "粘接/销轴为绑定；接触可分离且服从库仑摩擦；螺栓为一步等效预紧。详见 app/docs/connections.md。"}
    ctx = json.loads(arch["results_json"].read_text(encoding="utf-8"))
    from .diagnostics import collect_tie_diagnostics
    ctx['solver_diagnostics'] = collect_tie_diagnostics(task.case_dir,task.job_name,
                                                       mesh=solved_mesh,connections=project.connections)
    # 连接复核决策闭环（模块47）：把逐连接覆盖诊断回写为 needs_review 标记——
    # 该连接的从面出现未绑定节点（reported_missed_nodes>0）→ 下次求解前必须
    # 逐条决策（保留粘接/改可分离接触/删除）。节点计数只是关联证据。
    for index, item in enumerate(ctx['solver_diagnostics'].get('connections') or [], start=1):
        if index <= len(project.connections) and int(item.get('reported_missed_nodes') or 0) > 0:
            conn = project.connections[index - 1]
            conn.needs_review = True
            conn.review_evidence = {**(conn.review_evidence or {}),
                                    'last_solve': {'case_dir': str(task.case_dir),
                                                   'job': task.job_name,
                                                   'slave_surface_nodes': item.get('slave_surface_nodes'),
                                                   'reported_missed_nodes': item.get('reported_missed_nodes'),
                                                   'dof_inactive_on_surface': item.get('dof_inactive_on_surface')}}
    # 复核标记回写发生在 assembly_info 快照之后：重建连接列表，报告/结果 JSON
    # 携带的是回写后的复核状态（needs_review/决策字段）。
    assembly_info["connections"] = [dict(type=type(c).__name__, **asdict(c)) for c in project.connections]
    ctx['resource_usage'] = getattr(task,'resource_usage',{})
    if project.geometry_provenance:
        ctx["model_provenance"] = project.geometry_provenance
    if prepared["assembly"]:
        ctx["assembly"] = assembly_info
        ctx["yield_check"] = {"yield_mpa": None, "max_mises_mpa": float(fields.s_mises.max()),
                              "exceeded": False, "margin": None}
    # 所有工况从最终INP核对外力，不遗漏压力、自重或内部预紧力对。
    reaction = fields.source['reaction']
    if reaction.get('supported'):
        applied = np.asarray(reaction['applied_total_n'])
        moment_rf = sum((np.cross(solved_mesh['nodes'][n],vec) for n,vec in fields.rf_table.items()),np.zeros(3))
        ctx["equilibrium"] = {"applied_desc":"最终INP一致节点载荷积分（含二次单元中间节点）",
            "applied_n":applied.tolist(),"rf_sum_n":fields.rf_sum.tolist(),
            "residual":float(np.linalg.norm(applied+fields.rf_sum)),
            "moment_residual_origin_nmm":(moment_rf+reaction['applied_moment_origin_nmm']).tolist(),
            "reaction_corrected":True}
    else:
        ctx.pop('equilibrium',None)
    arch["results_json"].write_text(build_results_json(ctx),encoding="utf-8")
    arch["report_md"].write_text(build_report_md(ctx),encoding="utf-8")
    tip_row = list(fields.node_ids).index(tip)
    record = CaseRecord(name=task.case_dir.name, job_name=task.job_name,
                        created=datetime.datetime.now().isoformat(timespec="seconds"),
                        load_summary="；".join(x.summary() for x in project.study.load_cases[0].loads),
                        tip_uy_mm=float(fields.u[tip_row, 1]), max_u_mag_mm=fields.max_u_mag(),
                        max_mises_mpa=float(fields.s_mises.max()), rf_sum_n=tuple(float(x) for x in fields.rf_sum))
    project.cases.append(record)
    project.dirty = False
    return {"fields": fields, "views": views, "archive": arch, "assembly": assembly_info,
            "summary": {"case_dir": str(task.case_dir), "case_name": task.case_dir.name, "report_md": str(arch["report_md"]),
                        "tip_node": tip, "tip_uy_mm": record.tip_uy_mm, "max_u_mag_mm": record.max_u_mag_mm,
                        "max_mises_mpa": record.max_mises_mpa, "rf_sum_n": list(record.rf_sum_n),
                        "solver_diagnostics":ctx['solver_diagnostics'],
                        "assembly": assembly_info, "mesh": asdict(prepared["mesh_result"].stats)}}


def solve_assembly(project, model):
    from .tasks import SolveScheduler
    from ..config import get_ccx_path, load_config
    work = Path(project.project_dir)
    work.mkdir(parents=True, exist_ok=True)
    prepared = prepare_analysis(project, model, work / "mesh.inp")
    scheduler = SolveScheduler(get_ccx_path(load_config()), max_workers=1)
    task = scheduler.submit("assembly" if prepared["assembly"] else "solve", prepared["deck"], work / "cases")
    try:
        task = scheduler.wait_task(task.task_id, timeout=900)
    except Exception:
        scheduler.cancel(task.task_id)
        raise
    return finish_analysis(project, task, prepared)["summary"]


def new_assembly_project(project_dir, step_source, force_n=1000.0, friction_mu=0.3,
                         mesh_size_mm=2.0, connection="contact", gravity=True):
    """标准双叠块示例；任意用户装配通过 GUI 显式选面配置。"""
    from ..cad.assembly_import import import_assembly, sync_parts_from_model
    from ..core.project import Project, Constraint, LoadCase, TractionLoad, GravityLoad
    from ..core.connections import ConnectionSide, ContactFrictionConnection, BondedConnection
    from ..core.face_identity import fingerprint_from_face
    from ..core.materials import set_part_material
    from ..core.project_bundle import save_bundle

    model = import_assembly(step_source)
    try:
        if len(model.solids) != 2:
            raise ValueError("标准叠块示例需要两个实体；一般装配请使用界面配置")
        project = Project(name="两件式装配示例", project_dir=str(Path(project_dir).resolve()))
        sync_parts_from_model(project, model)
        for p in project.parts:
            set_part_material(project, p.part_id, "Q235")
        project.study.material = part_material(project, project.parts[0])
        project.study.geometry_file = str(Path(step_source).resolve())
        project.study.mesh_size_mm = mesh_size_mm
        ordered = sorted(project.parts, key=lambda p: p.fingerprint.centroid[1])
        lower, upper = ordered
        def face(part, sign):
            fs = [f for f in model.faces if f.solid_tag == model.solids[part.solid_index].tag and f.normal[1]*sign > .999]
            if len(fs) != 1:
                raise ValueError("示例要求平面叠块；无法唯一识别 y 向端面")
            return fs[0]
        bottom, top = face(lower, -1), face(upper, 1)
        a, b = face(lower, 1), face(upper, -1)
        project.study.load_cases = [LoadCase(name="叠块压缩与重力", constraints=[
            Constraint(name="底面固定", face_tag=bottom.tag, face=fingerprint_from_face(bottom), part_id=lower.part_id)],
            loads=[TractionLoad(name="顶面总力", face_tag=top.tag, face=fingerprint_from_face(top),
                                part_id=upper.part_id, total_force_n=force_n, direction=(0., -1., 0.))])]
        if gravity:
            project.study.load_cases[0].loads.append(GravityLoad(name="自重"))
        sides = dict(name="叠块界面", side_a=ConnectionSide(lower.part_id, fingerprint_from_face(a)),
                     side_b=ConnectionSide(upper.part_id, fingerprint_from_face(b)))
        if connection not in ("contact", "bonded"):
            raise ValueError("connection 只能是 contact 或 bonded")
        project.connections = [ContactFrictionConnection(**sides, friction_mu=friction_mu)
                               if connection == "contact" else BondedConnection(**sides)]
        result = solve_assembly(project, model)
        from ..cad.assembly_import import export_part_steps
        copies = export_part_steps(project, model, Path(project.project_dir)/"part_geometry")
        bundle = save_bundle(Path(project.project_dir)/"project.rmsimproject", project,
                             geometry_source=project.study.geometry_file, cases_root=Path(project.project_dir)/"cases",
                             part_geometry_sources=copies)
        return {"solve": result, "bundle": str(bundle), "parts": len(project.parts), "force_n": force_n,
                "connections": [asdict(c) for c in project.connections]}
    finally:
        model.close()
