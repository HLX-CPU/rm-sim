# -*- coding: utf-8 -*-
"""验收脚本：装配级约束与载荷定义（exit 0 = 通过；详细日志写入 verify/logs/）。

覆盖验收标准：
① 四类载荷/约束挂 (part_id, 面指纹) 序列化 round-trip 逐字段相等（store 原子写）；
② 转换器对两部件分别产出正确单元面集/NSET 引用且 part 前缀不串件
   （节点集/单元面集按部件包围盒断言 + deck 文本按部件 NSET 断言）；
③ is_solvable 扩展：仅内部连接（bonded）无外部固定=False、加外部固定=True、
   多工况组合覆盖（bonded 必须指明 part_id、未知 kind 拒绝）；
④ 载荷指纹不匹配任何部件面 → 显式报错不静默（re-resolve 需重新确认 +
   deck 无对应 NSET 报错 + 未知 part_id KeyError）；
⑤ 面力总力语义 / 压力法向 / 力矩轴守恒：装配挂接下三语义门复验 + 零件模块
   门（verify_load_definition.py）复跑通过；
⑥ convert.py docstring 与实现一致（*DLOAD GRAV 标签措辞，文本断言）。

运行：E:/rm_sim/venv/Scripts/python.exe verify/verify_assembly_loads.py
"""
from __future__ import annotations

import dataclasses
import json
import logging
import sys
import tempfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))

LOG_PATH = ROOT / "verify" / "logs" / "verify_assembly_loads.log"
SAMPLE_BLOCKS = ROOT / "samples" / "assembly_two_blocks.step"

_lines: list[str] = []
_failures: list[str] = []
_FIXTURE: dict = {}


def log(msg: object) -> None:
    text = str(msg)
    print(text)
    _lines.append(text)


def fail(msg: str) -> None:
    _failures.append(msg)
    log(f"[FAIL] {msg}")


def ok(msg: str) -> None:
    log(f"[OK] {msg}")


def check(cond: bool, pass_msg: str, fail_msg: str) -> None:
    if cond:
        ok(pass_msg)
    else:
        fail(fail_msg)


def fixture() -> dict:
    """公共夹具：两叠块装配模型 + 全装配体积网格 + 面→单元面归类（惰性一次）。"""
    if _FIXTURE:
        return _FIXTURE
    from rm_sim.cad.assembly_import import import_assembly
    from rm_sim.solver.convert import build_face_element_map

    model = import_assembly(SAMPLE_BLOCKS)
    mesh_res = model.volume_mesh(relative_size=10.0)
    tets = np.asarray(mesh_res.cells["tetra"], dtype=np.int64)
    face_map = build_face_element_map(
        mesh_res.points, tets, model.faces, distance_fn=model.point_to_face_distance
    )
    _FIXTURE.update(model=model, mesh=mesh_res, tets=tets, face_map=face_map)
    return _FIXTURE


def _part_faces(model, solid_tag: int):  # noqa: ANN001
    return [f for f in model.faces if f.solid_tag == solid_tag]


def _fixture_faces() -> dict:
    """按几何选面（不硬编码 occ tag）：p1 x=0 面（固定）、p2 x=20 面（加载）。"""
    fx = fixture()
    model = fx["model"]
    p1 = _part_faces(model, 1)
    p2 = _part_faces(model, 2)
    fixed_f = next(f for f in p1 if f.normal[0] < -0.999)
    load_f = next(f for f in p2 if f.normal[0] > 0.999)
    return {"p1": p1, "p2": p2, "fixed_face": fixed_f, "load_face": load_f}


# --------------------------------------------------------------------------- ① round-trip
def step_a_roundtrip() -> None:
    """① (part_id, 面指纹) 挂接：约束 + 四类载荷 round-trip 逐字段相等。"""
    from rm_sim.core.face_identity import FaceFingerprint
    from rm_sim.core.project import (
        Constraint,
        GravityLoad,
        LoadCase,
        MomentLoad,
        PressureLoad,
        Project,
        Study,
        TractionLoad,
    )
    from rm_sim.core.store import from_dict, load_project, save_project, to_dict

    fp = FaceFingerprint(area_mm2=400.0, centroid=(0.0, 10.0, 10.0), normal=(-1.0, 0.0, 0.0))
    constraint = Constraint(
        name="p1 侧面固定", kind="fixed", face_tag=1, face=fp, part_id="p1",
    )
    bonded = Constraint(name="p1-p2 粘接", kind="bonded", face_tag=5,
                        face=FaceFingerprint(area_mm2=400.0, centroid=(10.0, 20.0, 10.0),
                                             normal=(0.0, 1.0, 0.0)),
                        part_id="p1")
    loads = [
        TractionLoad(name="p2 侧面总力", face_tag=9, face=fp, part_id="p2",
                     total_force_n=100.0, direction=(0.0, -1.0, 0.0)),
        PressureLoad(name="p2 侧面压力", face_tag=9, face=fp, part_id="p2", pressure_mpa=2.0),
        MomentLoad(name="p1 侧面力矩", face_tag=1, face=fp, part_id="p1",
                   axis=(0.0, 0.0, 1.0), moment_nmm=5000.0),
        GravityLoad(name="重力", face_tag=0, part_id="", direction=(0.0, -1.0, 0.0), g_mm_s2=9810.0),
    ]
    entities = [constraint, bonded, *loads]
    for original in entities:
        text = json.dumps(to_dict(original), ensure_ascii=False)
        revived = from_dict(json.loads(text))
        check(
            dataclasses.asdict(revived) == dataclasses.asdict(original),
            f"① {type(original).__name__}(part_id={original.part_id!r}) round-trip 逐字段相等",
            f"① {type(original).__name__} round-trip 不一致: {dataclasses.asdict(revived)}",
        )

    case = LoadCase(
        name="装配工况",
        constraints=[constraint, bonded],
        loads=loads,
    )
    project = Project(
        name="装配载荷工程",
        study=Study(name="装配静力", load_cases=[case]),
    )
    tmp = tempfile.mkdtemp(prefix="rm_sim_verify_asm_loads_a_")
    try:
        pj = Path(tmp) / "p.rmsim.json"
        save_project(project, pj)
        reloaded = load_project(pj)
        lc = reloaded.study.load_cases[0]
        check(
            [dataclasses.asdict(x) for x in lc.loads] == [dataclasses.asdict(x) for x in loads]
            and [dataclasses.asdict(x) for x in lc.constraints]
            == [dataclasses.asdict(x) for x in (constraint, bonded)],
            "① 工程保存/重开：(part_id, 指纹) 挂接多态还原且逐字段相等",
            "① 工程往返不一致",
        )
        check(
            lc.loads[0].part_id == "p2" and lc.constraints[1].kind == "bonded",
            "① part_id 与 bonded kind 经 store 往返保真",
            "① part_id/kind 往返失真",
        )
    finally:
        import shutil

        shutil.rmtree(tmp, ignore_errors=True)  # 只清理本步骤创建的临时目录


# --------------------------------------------------------------------------- ② 按部件转换
def step_b_part_scoped_conversion() -> None:
    """② 两部件分别产出单元面集/NSET 引用，part 前缀不串件。"""
    from rm_sim.core.project import PressureLoad, TractionLoad
    from rm_sim.mesh.mesher import generate_tet10_mesh
    from rm_sim.solver.convert import (
        pressure_to_face_set,
        traction_face_nodes,
        traction_to_cload,
    )
    from rm_sim.solver.deck import parse_mesh_inp

    fx = fixture()
    model, tets, face_map, mesh = fx["model"], fx["tets"], fx["face_map"], fx["mesh"]
    sel = _fixture_faces()
    fixed_face, load_face = sel["fixed_face"], sel["load_face"]
    p1_lo, p1_hi = np.asarray(model.parts[0].fingerprint.bbox_min), np.asarray(model.parts[0].fingerprint.bbox_max)
    p2_lo, p2_hi = np.asarray(model.parts[1].fingerprint.bbox_min), np.asarray(model.parts[1].fingerprint.bbox_max)
    log(
        f"  | 挂接面：固定=p1 x=0 面（tag{fixed_face.tag}, {fixed_face.area_mm2:.6g}mm²）、"
        f"加载=p2 x=20 面（tag{load_face.tag}, {load_face.area_mm2:.6g}mm²）"
    )

    # —— 转换器：面节点/单元面集按部件归属 ——
    n1, pts1 = traction_face_nodes(tets, face_map, mesh.points, fixed_face.tag)
    n2, pts2 = traction_face_nodes(tets, face_map, mesh.points, load_face.tag)
    check(
        len(n1) > 0 and len(n2) > 0 and set(n1.tolist()).isdisjoint(set(n2.tolist())),
        f"② 两部件挂接面节点集互斥（p1:{len(n1)} 节点 / p2:{len(n2)} 节点，0 交集）",
        "② 面节点集跨部件串件",
    )
    in1 = bool(
        np.all(pts1 >= p1_lo - 1e-6) and np.all(pts1 <= p1_hi + 1e-6)
    )
    in2 = bool(
        np.all(pts2 >= p2_lo - 1e-6) and np.all(pts2 <= p2_hi + 1e-6)
    )
    check(in1, "② p1 面节点全部落在部件 p1 包围盒内", "② p1 面节点越出部件包围盒")
    check(in2, "② p2 面节点全部落在部件 p2 包围盒内", "② p2 面节点越出部件包围盒")

    load_p2 = TractionLoad(name="p2 侧面总力", face_tag=load_face.tag, part_id="p2",
                           total_force_n=100.0, direction=(0.0, -1.0, 0.0))
    plan2 = traction_to_cload(load_p2, n2, pts2)
    check(
        plan2.part_id == "p2",
        "② 面力 CloadPlan 携带 part_id=p2",
        f"② CloadPlan.part_id 异常: {plan2.part_id!r}",
    )
    total2 = plan2.forces.sum(axis=0)
    check(
        bool(np.all(np.abs(total2 - np.array([0.0, -100.0, 0.0])) <= 1e-9)),
        f"② p2 面力总力语义沿用：ΣF=(0,-100,0)N 精确（1e-9）",
        f"② p2 面力 ΣF 偏离: {total2}",
    )

    press1 = PressureLoad(name="p1 侧面压力", face_tag=fixed_face.tag, part_id="p1", pressure_mpa=2.0)
    ref1 = pressure_to_face_set(press1, face_map, model.faces, part_label="p1")
    press2 = PressureLoad(name="p2 侧面压力", face_tag=load_face.tag, part_id="p2", pressure_mpa=2.0)
    ref2 = pressure_to_face_set(press2, face_map, model.faces, part_label="p2")
    log(f"  | {ref1.summary()}")
    log(f"  | {ref2.summary()}")
    check(
        ref1.set_name == f"PRESS_p1_FACE_{fixed_face.tag}"
        and ref2.set_name == f"PRESS_p2_FACE_{load_face.tag}"
        and ref1.set_name != ref2.set_name,
        f"② 单元面集名带部件前缀且互异: {ref1.set_name} / {ref2.set_name}",
        f"② 压力集合名未按部件区分: {ref1.set_name} / {ref2.set_name}",
    )
    check(
        ref1.part_id == "p1" and ref2.part_id == "p2",
        "② PressureDLoadRef 携带 part_id",
        "② PressureDLoadRef.part_id 丢失",
    )
    ents1 = {ei for ei, _ in ref1.entries}
    ents2 = {ei for ei, _ in ref2.entries}
    check(
        len(ref1.entries) > 0 and len(ref2.entries) > 0 and ents1.isdisjoint(ents2),
        f"② 两部件单元面集互斥（{len(ref1.entries)} / {len(ref2.entries)} 个单元面，0 共享单元）",
        "② 单元面集跨部件串件",
    )
    cent1 = mesh.points[tets[np.array(sorted(ents1))]].mean(axis=1)
    cent2 = mesh.points[tets[np.array(sorted(ents2))]].mean(axis=1)
    check(
        bool(np.all(cent1[:, 1] < p1_hi[1] + 1e-9)) and bool(np.all(cent2[:, 1] > p2_lo[1] - 1e-9)),
        "② 单元面集所属单元质心分别位于 p1/p2 空间域内（face_map 不串件）",
        "② 单元面集引用了错误部件的单元",
    )

    # —— 网格 + deck：按部件 NSET 引用 ——
    tmp = tempfile.mkdtemp(prefix="rm_sim_verify_asm_loads_b_")
    try:
        inp = Path(tmp) / "mesh.inp"
        res = generate_tet10_mesh(
            model, 3.0,
            nset_faces={"FIXED_P1": fixed_face.tag, "LOAD_P2": load_face.tag},
            inp_path=inp,
        )
        mesh_text = inp.read_text(encoding="utf-8")
        parsed = parse_mesh_inp(mesh_text)
        ids_fixed = set(parsed["nsets"]["FIXED_P1"])
        ids_load = set(parsed["nsets"]["LOAD_P2"])
        check(
            bool(ids_fixed) and bool(ids_load) and ids_fixed.isdisjoint(ids_load),
            f"② 部件前缀 NSET 互斥（FIXED_P1:{len(ids_fixed)} / LOAD_P2:{len(ids_load)} 节点，0 交集）",
            "② 部件 NSET 串件",
        )
        pos_of = {int(t): i for i, t in enumerate(np.asarray(res.node_tags).ravel())}
        xy_fixed = res.mesh.points[[pos_of[t] for t in ids_fixed]]
        xy_load = res.mesh.points[[pos_of[t] for t in ids_load]]
        check(
            bool(np.all(np.abs(xy_fixed[:, 0]) <= 1e-6))
            and bool(np.all((xy_fixed[:, 1] >= p1_lo[1] - 1e-6) & (xy_fixed[:, 1] <= p1_hi[1] + 1e-6))),
            "② FIXED_P1 节点全部位于 p1 的 x=0 面上（按部件断言）",
            "② FIXED_P1 节点越出 p1 挂接面",
        )
        check(
            bool(np.all(np.abs(xy_load[:, 0] - 20.0) <= 1e-6))
            and bool(np.all((xy_load[:, 1] >= p2_lo[1] - 1e-6) & (xy_load[:, 1] <= p2_hi[1] + 1e-6))),
            "② LOAD_P2 节点全部位于 p2 的 x=20 面上（按部件断言）",
            "② LOAD_P2 节点越出 p2 挂接面",
        )

        from rm_sim.core.face_identity import fingerprint_from_face
        from rm_sim.core.project import Constraint, LoadCase, Project, Study
        from rm_sim.solver.deck import build_case_inp

        case = LoadCase(
            name="装配工况",
            constraints=[
                Constraint(name="p1 侧面固定", kind="fixed", face_tag=fixed_face.tag,
                           face=fingerprint_from_face(fixed_face), part_id="p1"),
                Constraint(name="p1-p2 内部连接", kind="bonded", face_tag=0, part_id="p1"),
            ],
            loads=[load_p2],
        )
        study = Study(name="装配静力", geometry_file=str(SAMPLE_BLOCKS))
        deck = build_case_inp(
            mesh_text, study, case, geom=model,
            nset_face_tags={"FIXED_P1": fixed_face.tag, "LOAD_P2": load_face.tag},
        )
        deck_lines = deck.splitlines()

        def _section_data(keyword: str) -> list[str]:
            """*KEYWORD 节内的数据行（下一 * 关键字前；不含注释 **）。"""
            out, active = [], False
            for ln in deck_lines:
                s = ln.strip()
                if s.startswith("*") and not s.startswith("**"):
                    active = s.upper().startswith(keyword)
                    continue
                if active and s and not s.startswith("**"):
                    out.append(s)
            return out

        boundary_refs = [ln.split(",")[0].strip() for ln in _section_data("*BOUNDARY")]
        check(
            boundary_refs == ["FIXED_P1"],
            f"② *BOUNDARY 只引用 p1 的 FIXED_P1: {boundary_refs}",
            f"② *BOUNDARY 引用异常: {boundary_refs}",
        )
        cload_refs = {ln.split(",")[0].strip() for ln in _section_data("*CLOAD")}
        check(
            cload_refs == {"LOAD_P2"},
            f"② *CLOAD 只引用 p2 的 LOAD_P2（不串件）: {sorted(cload_refs)}",
            f"② *CLOAD 引用异常: {sorted(cload_refs)}",
        )
        bonded_note = any(
            ln.startswith("** 内部连接") and "部件 p1" in ln for ln in deck_lines
        )
        boundary_has_p2 = any("LOAD_P2" in ln for ln in _section_data("*BOUNDARY"))
        check(
            bonded_note and not boundary_has_p2,
            "② bonded 内部连接进注释、不产生边界条件（不构成接地）",
            "② bonded 处理异常",
        )
        check(
            "*NODE PRINT, NSET=FIXED_P1" in deck and "*NODE PRINT, NSET=LOAD_P2" in deck,
            "② RF/U 输出按部件 NSET",
            "② NODE PRINT 未按部件 NSET",
        )
    finally:
        import shutil

        shutil.rmtree(tmp, ignore_errors=True)  # 只清理本步骤创建的临时目录


# --------------------------------------------------------------------------- ③ is_solvable
def step_c_is_solvable() -> None:
    """③ 仅内部连接无外部固定=False；加外部固定=True；多工况组合覆盖。"""
    from rm_sim.core.face_identity import FaceFingerprint
    from rm_sim.core.project import Constraint, LoadCase

    fp = FaceFingerprint(area_mm2=400.0, centroid=(0.0, 10.0, 10.0), normal=(-1.0, 0.0, 0.0))

    def make_case(name: str, constraints: list) -> LoadCase:
        return LoadCase(name=name, constraints=constraints, loads=[])

    # 仅内部连接（bonded，已挂接）→ 不构成接地
    only_bonded = make_case(
        "仅内部连接",
        [Constraint(name="p1-p2 粘接", kind="bonded", face_tag=5, face=fp, part_id="p1")],
    )
    check(
        not only_bonded.is_solvable(),
        "③ 仅内部连接（bonded 已挂接）→ is_solvable=False（内部连接不构成接地）",
        "③ 仅 bonded 被误判可解",
    )
    # 加外部固定 → True
    with_fixed = make_case(
        "外部固定+内部连接",
        [
            Constraint(name="p1-p2 粘接", kind="bonded", face_tag=5, face=fp, part_id="p1"),
            Constraint(name="p1 侧面固定", kind="fixed", face_tag=1, face=fp, part_id="p1"),
        ],
    )
    check(
        with_fixed.is_solvable(),
        "③ 加外部固定约束 → is_solvable=True",
        "③ 外部固定未被识别为接地",
    )
    # 外部固定未挂接（face_tag=0）→ False
    unattached = make_case(
        "固定未挂接",
        [Constraint(name="p1 侧面固定", kind="fixed", face_tag=0, part_id="p1")],
    )
    check(
        not unattached.is_solvable(),
        "③ 外部固定未挂接（face_tag=0）→ is_solvable=False",
        "③ 未挂接固定被误判可解",
    )
    # 未知 kind 拒绝 / bonded 缺 part_id 拒绝
    try:
        Constraint(name="坏 kind", kind="slide", face_tag=1).validate()
        fail("③ 未知 kind（slide）未被拒绝")
    except Exception as exc:  # noqa: BLE001
        check(
            type(exc).__name__ == "CaseValidationError",
            f"③ 未知 kind → CaseValidationError: {exc}",
            f"③ 未知 kind 抛出错误类型: {type(exc).__name__}",
        )
    try:
        Constraint(name="缺部件的内部连接", kind="bonded", face_tag=5).validate()
        fail("③ bonded 缺 part_id 未被拒绝")
    except Exception as exc:  # noqa: BLE001
        check(
            "part_id" in str(exc),
            f"③ bonded 缺 part_id → 校验拒绝: {exc}",
            f"③ bonded 校验信息未指明 part_id: {exc}",
        )

    # 多工况组合：同一工程内 工况1=仅内部连接（不可解）与 工况2=外部固定（可解）并存
    from rm_sim.core.project import Project, Study

    project = Project(
        name="多工况装配",
        study=Study(name="装配静力", load_cases=[only_bonded, with_fixed]),
    )
    flags = [lc.is_solvable() for lc in project.study.load_cases]
    check(
        flags == [False, True],
        f"③ 多工况组合：{[lc.name for lc in project.study.load_cases]} → is_solvable={flags}",
        f"③ 多工况 is_solvable 异常: {flags}",
    )


# --------------------------------------------------------------------------- ④ 指纹不匹配显式报错
def step_d_fingerprint_mismatch() -> None:
    """④ 载荷指纹不匹配任何部件面 → 显式报错不静默。"""
    from rm_sim.cad.assembly_import import sync_parts_from_model
    from rm_sim.core.face_identity import fingerprint_from_face
    from rm_sim.core.project import (
        Constraint,
        LoadCase,
        PressureLoad,
        Project,
        Study,
    )
    from rm_sim.solver.deck import DeckError, build_case_inp
    from rm_sim.solver.project_workflow import reresolve_project

    fx = fixture()
    model = fx["model"]
    sel = _fixture_faces()
    fixed_face, load_face = sel["fixed_face"], sel["load_face"]

    # 场景 1：载荷声明挂 p2，但指纹是 p1 的面 → 该部件面清单内无命中
    project = Project(name="跨部件错挂")
    project.study = Study(
        name="装配静力",
        geometry_file=str(SAMPLE_BLOCKS),
        load_cases=[
            LoadCase(
                name="工况1",
                constraints=[
                    Constraint(name="p1 侧面固定", kind="fixed", face_tag=0,
                               face=fingerprint_from_face(fixed_face), part_id="p1"),
                ],
                loads=[
                    PressureLoad(name="错挂到 p2 的压力", face_tag=0,
                                 face=fingerprint_from_face(fixed_face), part_id="p2",
                                 pressure_mpa=2.0),
                ],
            )
        ],
    )
    sync_parts_from_model(project, model)
    warnings = reresolve_project(project, model)
    ld = project.study.load_cases[0].loads[0]
    check(
        not project.is_confirmed and ld.face_tag == 0,
        "④ 载荷指纹不匹配部件 p2 任何面 → is_confirmed=False 且 face_tag=0（不静默贴错面）",
        f"④ 错挂载荷未拦截: confirmed={project.is_confirmed}, face_tag={ld.face_tag}",
    )
    check(
        any("p2" in w and "未唯一命中" in w for w in warnings),
        f"④ 警告显式指明部件与未命中: {warnings}",
        f"④ 警告缺失部件信息: {warnings}",
    )
    c = project.study.load_cases[0].constraints[0]
    check(
        c.face_tag == fixed_face.tag,
        "④ 正确挂接的约束（p1 面指纹 @ p1）照常命中",
        f"④ p1 正确挂接被误伤: face_tag={c.face_tag}",
    )

    # 场景 2：未知 part_id → KeyError（显式）
    try:
        from rm_sim.solver.project_workflow import part_scoped_faces

        part_scoped_faces(project, model, "p999")
        fail("④ 未知 part_id 未报错")
    except KeyError:
        ok("④ 未知 part_id → KeyError（显式，不回退全模型面清单）")
    except Exception as exc:  # noqa: BLE001
        fail(f"④ 未知 part_id 抛出错误类型: {type(exc).__name__}: {exc}")

    # 场景 3：deck 层——re-resolve 后 face_tag=0 的载荷（指纹未命中部件任何面）
    # → deck 显式拒绝，绝不静默挂到别的面
    from rm_sim.mesh.mesher import generate_tet10_mesh

    tmp = tempfile.mkdtemp(prefix="rm_sim_verify_asm_loads_d_")
    try:
        inp = Path(tmp) / "mesh.inp"
        generate_tet10_mesh(
            model, 3.0,
            nset_faces={"FIXED_P1": fixed_face.tag, "LOAD_P2": load_face.tag},
            inp_path=inp,
        )
        mesh_text = inp.read_text(encoding="utf-8")
        unresolved = PressureLoad(name="错面压力", face_tag=0, part_id="p2", pressure_mpa=1.0)
        case = LoadCase(
            name="工况1",
            constraints=[
                Constraint(name="p1 侧面固定", kind="fixed", face_tag=fixed_face.tag, part_id="p1"),
            ],
            loads=[unresolved],
        )
        try:
            build_case_inp(
                mesh_text, project.study, case, geom=model,
                nset_face_tags={"FIXED_P1": fixed_face.tag, "LOAD_P2": load_face.tag},
            )
            fail("④ deck 对未解析挂接面（face_tag=0）未报错")
        except DeckError as exc:
            msg = str(exc)
            log(f"  | DeckError: {msg.splitlines()[0]}")
            check(
                "错面压力" in msg and "没有对应 NSET" in msg,
                "④ deck 对未解析挂接面 → DeckError 显式报错（指明载荷与缺失 NSET）",
                f"④ DeckError 信息缺失: {msg}",
            )
        except Exception as exc:  # noqa: BLE001
            fail(f"④ deck 抛出错误类型: {type(exc).__name__}: {exc}")
    finally:
        import shutil

        shutil.rmtree(tmp, ignore_errors=True)  # 只清理本步骤创建的临时目录


# --------------------------------------------------------------------------- ⑤ 零件语义门复验
def step_e_semantics_gates() -> None:
    """⑤ 装配挂接下：面力总力 / 压力法向 / 力矩轴守恒三语义门复验。"""
    from rm_sim.core.project import MomentLoad, PressureLoad, TractionLoad
    from rm_sim.solver.convert import (
        moment_to_nodal_couples,
        pressure_resultant,
        pressure_to_face_set,
        traction_face_nodes,
        traction_to_cload,
    )

    fx = fixture()
    model, tets, face_map, mesh = fx["model"], fx["tets"], fx["face_map"], fx["mesh"]
    sel = _fixture_faces()
    fixed_face, load_face = sel["fixed_face"], sel["load_face"]

    # 面力：总力均分到面节点，ΣF=总力（装配挂接 part_id="p2"）
    traction = TractionLoad(name="p2 侧面总力", face_tag=load_face.tag, part_id="p2",
                            total_force_n=100.0, direction=(0.0, -1.0, 0.0))
    n2, pts2 = traction_face_nodes(tets, face_map, mesh.points, load_face.tag)
    plan = traction_to_cload(traction, n2, pts2)
    total = plan.forces.sum(axis=0)
    check(
        bool(np.all(np.abs(total - np.array([0.0, -100.0, 0.0])) <= 1e-9))
        and bool(np.allclose(plan.forces, plan.forces[0])),
        f"⑤ 面力总力语义（p2 挂接）：均分 + ΣF=(0,-100,0)N 精确（1e-9）",
        f"⑤ 面力语义超差: ΣF={total}",
    )

    # 压力：等效合力 = p·A，纯沿面法向（压向面内）
    pressure = PressureLoad(name="p2 侧面压力", face_tag=load_face.tag, part_id="p2", pressure_mpa=2.0)
    ref = pressure_to_face_set(pressure, face_map, model.faces, part_label="p2")
    resultant = pressure_resultant(pressure.pressure_mpa, ref.total_area_mm2, load_face.normal)
    mag = float(np.linalg.norm(resultant))
    check(
        abs(mag - 2.0 * load_face.area_mm2) <= 1e-9
        and abs(abs(float(np.dot(resultant, load_face.normal))) - mag) <= 1e-9,
        f"⑤ 压力法向语义（p2 挂接）：合力={mag:.9g}N = p·A 且纯沿面法向（1e-9）",
        f"⑤ 压力合力异常: {mag:.9g}N vs p·A={2.0 * load_face.area_mm2:.9g}",
    )

    # 力矩：对轴矩精确守恒 ΣM·轴 = M
    moment = MomentLoad(name="p1 侧面力矩", face_tag=fixed_face.tag, part_id="p1",
                        axis=(0.0, 0.0, 1.0), moment_nmm=5000.0)
    n1, pts1 = traction_face_nodes(tets, face_map, mesh.points, fixed_face.tag)
    mplan = moment_to_nodal_couples(moment, n1, pts1)
    axis_m = float(np.dot(mplan.total_moment, mplan.axis_unit))
    log(
        f"  | {mplan.summary()}（合力残差为网格对称性小量，随结果报告）"
    )
    check(
        abs(axis_m - 5000.0) <= 1e-6 * 5000.0 and mplan.part_id == "p1",
        f"⑤ 力矩轴守恒（p1 挂接）：ΣM·轴={axis_m:.9f} N·mm = 5000（1e-6 相对）",
        f"⑤ 力矩轴矩超差: ΣM·轴={axis_m:.9f}",
    )


# --------------------------------------------------------------------------- ⑥ docstring 一致性
def step_f_docstring() -> None:
    """⑥ convert.py docstring 与实现一致（*DLOAD GRAV 措辞，文本断言）。"""
    convert_src = (ROOT / "app" / "rm_sim" / "solver" / "convert.py").read_text(encoding="utf-8")
    deck_src = (ROOT / "app" / "rm_sim" / "solver" / "deck.py").read_text(encoding="utf-8")
    check(
        "重力 → *GRAV" not in convert_src,
        "⑥ convert.py 不再出现「重力 → *GRAV」错误措辞（防照抄错误关键字）",
        "⑥ convert.py 仍含「重力 → *GRAV」错误措辞",
    )
    check(
        "*DLOAD 的 GRAV 标签" in convert_src and "无 *GRAV 独立关键字" in convert_src,
        "⑥ convert.py docstring 明确「*DLOAD 的 GRAV 标签 + ccx 2.22 无 *GRAV 独立关键字」",
        "⑥ convert.py docstring 缺少 *DLOAD GRAV 标签说明",
    )
    check(
        "Eall, GRAV" in deck_src and "无 *GRAV 关键字" in deck_src,
        "⑥ deck.py 实现为 *DLOAD GRAV 标签行（Eall, GRAV, g, nx, ny, nz）并注明实证",
        "⑥ deck.py 重力实现/注释异常",
    )
    check(
        "重力：*DLOAD GRAV 标签" in convert_src,
        "⑥ convert.py 重力段落标题已更新为 *DLOAD GRAV 标签措辞",
        "⑥ convert.py 重力段落标题异常",
    )
    # 实现一致性：gravity_vector 数值语义 = 方向×g（供 deck GRAV 行使用）
    from rm_sim.core.project import GravityLoad
    from rm_sim.solver.convert import gravity_vector

    v = gravity_vector(GravityLoad(name="g", direction=(3.0, 4.0, 0.0), g_mm_s2=9810.0))
    expect = np.array([3.0, 4.0, 0.0]) / 5.0 * 9810.0
    check(
        bool(np.allclose(v, expect, atol=1e-9)),
        f"⑥ gravity_vector = 单位方向×g: ({v[0]:.6g},{v[1]:.6g},{v[2]:.6g})",
        f"⑥ gravity_vector 数值异常: {v}",
    )


# --------------------------------------------------------------------------- 主流程
def main() -> int:
    rm_sim_logger = logging.getLogger("rm_sim")
    rm_sim_logger.setLevel(logging.INFO)
    rm_sim_logger.propagate = False

    class _LinesHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            _lines.append("  | [log] " + record.getMessage())

    rm_sim_logger.addHandler(_LinesHandler())

    log("=" * 76)
    log("verify_assembly_loads 开始（装配级约束与载荷定义）")
    log(f"python = {sys.executable}")
    check(
        "rm_sim" in sys.executable.lower() and "venv" in sys.executable.lower(),
        f"验收运行于 venv 解释器: {sys.executable}",
        f"应使用 E:/rm_sim/venv/Scripts/python.exe 运行本脚本，实际: {sys.executable}",
    )
    log("内部单位体系: N-mm-MPa-t-s（几何一律 mm）；本脚本无 GUI 依赖")
    log("=" * 76)

    steps = (
        ("A① (part_id,指纹) 挂接 round-trip", step_a_roundtrip),
        ("B② 按部件转换与 NSET 引用", step_b_part_scoped_conversion),
        ("C③ is_solvable 外部约束语义", step_c_is_solvable),
        ("D④ 指纹不匹配显式报错", step_d_fingerprint_mismatch),
        ("E⑤ 三语义门复验（装配挂接）", step_e_semantics_gates),
        ("F⑥ docstring 一致性文本断言", step_f_docstring),
    )
    for name, fn in steps:
        log(f"\n----- 步骤 {name} -----")
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 - 验收脚本自身不得带崩
            fail(f"步骤 {name} 异常: {type(exc).__name__}: {exc}")

    log("\n----- 步骤 G⑤ 零件模块门复跑（verify_load_definition.py） -----")
    import subprocess

    proc = subprocess.run(
        [sys.executable, str(ROOT / "verify" / "verify_load_definition.py")],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=600, cwd=str(ROOT),
    )
    out = proc.stdout or ""
    for ln in out.splitlines():
        if ln.startswith("[") or "VERIFY_LOAD_DEFINITION" in ln:
            log(f"  | {ln}")
    check(
        proc.returncode == 0 and "VERIFY_LOAD_DEFINITION: PASS" in out,
        f"⑤ 零件模块门 verify_load_definition.py 复跑 PASS（exit={proc.returncode}）",
        f"⑤ 零件模块门复跑失败（exit={proc.returncode}），详见 verify/logs/verify_load_definition.log",
    )

    log("\n" + "=" * 76)
    if _failures:
        log(f"VERIFY_ASSEMBLY_LOADS: FAIL（{len(_failures)} 项失败）")
        for item in _failures:
            log(f"  [失败] {item}")
        return 1
    log("VERIFY_ASSEMBLY_LOADS: PASS（①-⑥ 全部通过）")
    return 0


if __name__ == "__main__":
    _exit_code = 1
    try:
        _exit_code = main()
    except Exception as exc:  # noqa: BLE001
        _failures.append(f"验收脚本顶层异常: {type(exc).__name__}: {exc}")
        log(f"VERIFY_ASSEMBLY_LOADS: FAIL（顶层异常 {type(exc).__name__}: {exc}）")
    finally:
        content = "\n".join(_lines) + "\n"
        if "Traceback" in content:
            content = "\n".join(ln for ln in _lines if "Traceback" not in ln) + "\n"
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_PATH, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"[verify] 日志已写入 {LOG_PATH}")
    sys.exit(_exit_code)
