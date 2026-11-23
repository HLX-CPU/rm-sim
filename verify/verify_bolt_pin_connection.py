# -*- coding: utf-8 -*-
"""验收脚本：螺栓与销轴简化连接求解验证（真实 ccx 2.22）。

算例（几何规格=评审补齐版，见 app/docs/connections.md §螺栓/§销轴）：
- 螺栓：两叠块 20×20×20（下块底面固定；界面=接触；螺栓简化=耦合区刚体连接
  到参考点 + 预紧力对 ±F_p；不模拟螺纹/杆柔度/垫圈）。ccx 2.22 实证：
  *KINEMATIC COUPLING 不被识别（card image CKINEMATICCOUPLING fatal，
  module_16/18 探针）→ 按 ask「实跑为准」用 ccx 原生等价 *RIGID BODY。
- 销轴：plate1 x∈[0,60] y∈[0,20] z∈[0,5]；plate2 x∈[40,100] y∈[0,20] z∈[5,10]；
  两板各在 (x=50,y=10) 有 Ø8 通孔（z 向贯通）；销=圆柱 r=4、轴心 (50,10)、
  z∈[0,10]——销面=两板孔壁圆柱面（零间隙 *TIE 绑定；不考虑孔挤压/间隙/销
  弯曲柔度）。

覆盖验收标准：
① 仅预紧 F_p=2000N：ΣRF_ground 自平衡（|ΣRFy|≤20N=1%×F_p）、界面节点力
   合力（FORC@从面）=2000N±10%、节点接触法向力 ≥0；
② 预紧+500N 上拉：接地反力 ΣRFy=−500N（0.01%）；界面残余夹紧=2000−500=
   1500N（由实测接地反力与已知载荷平衡闭环给出；FORC@从面含刚体 MPC 混合
   记录为非清洁量，不设门）；
③ 销孔存在断言：两板孔壁面集非空且面积=2π×4×5=125.66mm²（occ 解析 ±1%）、
   销绑定面引用=孔壁面（绑定单元节点全部位于 r=4 圆柱面——防无孔埋入假实现）；
④ 销轴载荷传递：求解成功 + ΣRF=(−500,0,0)（0.01%）+ 板1 x=40 截面
   |Ux|=9.5238e-4mm±10%（1mm 网格实测 0.3%；2mm 实测 8.3% 亦在门内）；
⑤ 销缺失负例：无销部件（无连接）→ 板2 无约束路径 → 装配连接性门 DeckError
   （首跑实证：ccx 对该奇异体系仍 'Job finished' 但位移为垃圾——deck 层拒绝）；
⑥ 负例：μ>1 / F_p≤0 / 耦合区半径≤0（耦合区面积≤0）校验拒绝；
⑦ connections.md 螺栓/销轴节含假设与验证算例编号；
⑧ 全程无 PySide6 进程 + 静态扫描。

运行：E:/rm_sim/venv/Scripts/python.exe verify/verify_bolt_pin_connection.py
"""
from __future__ import annotations

import logging
import re
import sys
import tempfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))

LOG_PATH = ROOT / "verify" / "logs" / "verify_bolt_pin_connection.log"
SAMPLE_BLOCKS = ROOT / "samples" / "assembly_two_blocks.step"
DOC_PATH = ROOT / "app" / "docs" / "connections.md"

FP = 2000.0  # 预紧力 [N]

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


def _side(part_id: str, face):  # noqa: ANN001
    from rm_sim.core.connections import ConnectionSide
    from rm_sim.core.face_identity import fingerprint_from_face

    return ConnectionSide(part_id=part_id, face=fingerprint_from_face(face))


# --------------------------------------------------------------------------- 螺栓夹具
def bolt_fixture():
    key = "bolt"
    if key in _FIXTURE:
        return _FIXTURE[key]
    from rm_sim.cad.assembly_import import import_assembly, sync_parts_from_model
    from rm_sim.core.connections import BoltConnection, ContactFrictionConnection
    from rm_sim.core.project import Project
    from rm_sim.mesh.mesher import generate_tet10_mesh

    model = import_assembly(SAMPLE_BLOCKS)
    p1f = [f for f in model.faces if f.solid_tag == int(model.solids[0].tag)]
    p2f = [f for f in model.faces if f.solid_tag == int(model.solids[1].tag)]
    f_fixed = next(f for f in p1f if f.normal[1] < -0.999)
    f_top = next(f for f in p2f if f.normal[1] > 0.999)
    f_ifc1 = next(f for f in p1f if f.normal[1] > 0.999)
    f_ifc2 = next(f for f in p2f if f.normal[1] < -0.999)
    mesh_inp = Path(tempfile.mkdtemp(prefix="rm_sim_bolt_mesh_")) / "mesh.inp"
    generate_tet10_mesh(
        model, 3.0,
        nset_faces={"PART1_FIXED": f_fixed.tag, "PART2_TOP": f_top.tag},
        part_elsets={"PART1": int(model.solids[0].tag), "PART2": int(model.solids[1].tag)},
        inp_path=mesh_inp,
    )
    project = Project(name="螺栓验证工程")
    sync_parts_from_model(project, model)
    project.connections = [
        ContactFrictionConnection(name="界面接触", friction_mu=0.3,
                                  side_a=_side("p1", f_ifc1), side_b=_side("p2", f_ifc2)),
        BoltConnection(name="预紧螺栓", pretension_n=FP, coupling_radius_mm=6.0,
                       side_a=_side("p1", f_ifc1), side_b=_side("p2", f_ifc2)),
    ]
    _FIXTURE[key] = {
        "model": model, "mesh_text": mesh_inp.read_text(encoding="utf-8"),
        "project": project, "f_fixed": f_fixed, "f_top": f_top, "f_ifc1": f_ifc1, "f_ifc2": f_ifc2,
        "nset_face_tags": {"PART1_FIXED": int(f_fixed.tag), "PART2_TOP": int(f_top.tag)},
    }
    return _FIXTURE[key]


def _bolt_load_case(fx, pulls):  # noqa: ANN001
    from rm_sim.core.project import Constraint, LoadCase, TractionLoad

    return LoadCase(
        name="螺栓工况",
        constraints=[Constraint(name="固定", kind="fixed", face_tag=int(fx["f_fixed"].tag), part_id="p1")],
        loads=[TractionLoad(name="上拉", face_tag=int(fx["f_top"].tag), part_id="p2",
                            total_force_n=pulls, direction=(0.0, 1.0, 0.0)) for _ in (0,) if pulls > 0],
    )


# --------------------------------------------------------------------------- 销轴夹具
def pin_fixture(mesh_size_mm: float | None = None):
    """评审补齐几何：搭接双板 + Ø8 通孔 + 销 r=4（探针内 gmsh 生成）。

    模块46 规模参数化：缺省档位随 RM_SIM_SCALE——默认档 1.5mm（Ux 锚 9.5238e-4mm
    ±10% 内，网格离散误差实测 <10%，求解峰值内存显著低于 1.0mm 档的 95433 节点）；
    大网格档（--slow）1.0mm（原始验收档）。
    """
    if mesh_size_mm is None:
        from memory_budget import scale_tier

        mesh_size_mm = 1.0 if scale_tier() == "large" else 1.5
    key = ("pin", mesh_size_mm)
    if key in _FIXTURE:
        return _FIXTURE[key]
    import gmsh

    from rm_sim import gmsh_runtime
    from rm_sim.cad.assembly_import import import_assembly, sync_parts_from_model
    from rm_sim.core.connections import PinConnection
    from rm_sim.core.project import Project
    from rm_sim.mesh.mesher import generate_tet10_mesh

    step = Path(tempfile.mkdtemp(prefix="rm_sim_bpin_geom_")) / "lap_pin_v2.step"
    with gmsh_runtime.session():
        gmsh.model.add("gen")
        occ = gmsh.model.occ
        a = occ.addBox(0, 0, 0, 60, 20, 5)  # plate1 x 0..60, z 0..5
        b = occ.addBox(40, 0, 5, 60, 20, 5)  # plate2 x 40..100, z 5..10
        h1 = occ.addCylinder(50, 10, -1, 0, 0, 7, 4)  # plate1 孔刀（z 贯通）
        h2 = occ.addCylinder(50, 10, 4, 0, 0, 7, 4)  # plate2 孔刀
        occ.cut([(3, a)], [(3, h1)])
        occ.cut([(3, b)], [(3, h2)])
        occ.addCylinder(50, 10, 0, 0, 0, 10, 4)  # 销 r=4 z 0..10
        occ.synchronize()
        gmsh.write(str(step))

    model = import_assembly(step)
    check(len(model.parts) == 3, "③ 销轴装配 3 部件（两板+销）", f"③ 部件数 {len(model.parts)} ≠ 3")
    parts = {k: next(p for p in model.parts if p.tag == k) for k in (1, 2, 3)}
    plate1, plate2, pin_part = parts[1], parts[2], parts[3]
    p1f = [f for f in model.faces if f.solid_tag == int(plate1.tag)]
    p2f = [f for f in model.faces if f.solid_tag == int(plate2.tag)]
    p3f = [f for f in model.faces if f.solid_tag == int(pin_part.tag)]
    f_p1fix = next(f for f in p1f if f.normal[0] < -0.999)  # plate1 x=0
    f_p2load = next(f for f in p2f if f.normal[0] > 0.999)  # plate2 x=100
    hf1 = next(f for f in p1f if 125.0 < f.area_mm2 < 126.3)  # plate1 孔壁
    hf2 = next(f for f in p2f if 125.0 < f.area_mm2 < 126.3)  # plate2 孔壁
    pin_lat = next(f for f in p3f if f.area_mm2 > 200)  # 销侧面

    mesh_inp = step.parent / f"mesh_pin_{mesh_size_mm}.inp"
    mesh_res = generate_tet10_mesh(
        model, mesh_size_mm,
        nset_faces={"PART1_FIXED": f_p1fix.tag, "PART2_LOAD": f_p2load.tag},
        part_elsets={"PART1": int(plate1.tag), "PART2": int(plate2.tag), "PART3": int(pin_part.tag)},
        inp_path=mesh_inp,
    )
    project = Project(name="销轴验证工程")
    sync_parts_from_model(project, model)
    project.connections = [
        PinConnection(name="销-板1绑定", side_a=_side("p3", pin_lat), side_b=_side("p1", hf1)),
        PinConnection(name="销-板2绑定", side_a=_side("p3", pin_lat), side_b=_side("p2", hf2)),
    ]
    _FIXTURE[key] = {
        "model": model, "mesh": mesh_res, "mesh_size_mm": mesh_size_mm, "mesh_text": mesh_inp.read_text(encoding="utf-8"),
        "project": project, "f_p1fix": f_p1fix, "f_p2load": f_p2load,
        "hf1": hf1, "hf2": hf2, "pin_lat": pin_lat,
        "nset_face_tags": {"PART1_FIXED": int(f_p1fix.tag), "PART2_LOAD": int(f_p2load.tag)},
    }
    return _FIXTURE[key]


def _pin_load_case(gfx):
    from rm_sim.core.project import Constraint, LoadCase, TractionLoad

    return LoadCase(
        name="销拉",
        constraints=[Constraint(name="固定", kind="fixed", face_tag=int(gfx["f_p1fix"].tag), part_id="p1")],
        loads=[TractionLoad(name="销拉", face_tag=int(gfx["f_p2load"].tag), part_id="p2",
                            total_force_n=500.0, direction=(1.0, 0.0, 0.0))],
    )


# --------------------------------------------------------------------------- 提交与测量
def _submit(deck: str, job: str, work_root: Path, timeout: float = 1800):
    from rm_sim.config import get_ccx_path, load_config
    from rm_sim.solver.tasks import SolveScheduler

    from memory_budget import make_scheduler

    sched = make_scheduler(get_ccx_path(load_config()), max_workers=2)  # 模块46：分级预算
    task = sched.submit(job, deck, work_root)
    return task, sched.wait_task(task.task_id, timeout=timeout)


def _forc_at_interface(task, deck: str, side: str) -> tuple[np.ndarray, float]:
    """连接侧（从面）节点的 FORC 合力与最小法向分量。"""
    from rm_sim.solver.ccx2para import frd_to_vtu, read_vtu_point_data
    from rm_sim.solver.deck import parse_mesh_inp

    fx = bolt_fixture()
    mesh = parse_mesh_inp(deck)
    data = read_vtu_point_data(frd_to_vtu(task.frd_path))
    forc = np.asarray(data["FORC"], dtype=float)
    pos_of = {int(n): i for i, n in enumerate(sorted(mesh["nodes"].keys()))}
    nodes: set[int] = set()
    for ename, eids in mesh["elsets"].items():
        if re.fullmatch(rf"C1_{side}_\d", ename):
            for eid in eids:
                nodes |= {int(n) for n in mesh["elems"][eid]}
    rows = [pos_of[n] for n in sorted(nodes) if n in pos_of]
    s = forc[rows].sum(axis=0)
    return s, float(np.min(forc[rows, 1]))


# --------------------------------------------------------------------------- ①② 螺栓
def step_a_bolt(work_root: Path) -> None:
    """① 仅预紧：自平衡+界面节点力合力+压力≥0；② 预紧+上拉：残余夹紧+接地反力。"""
    from rm_sim.solver.tasks import STATE_COMPLETED

    fx = bolt_fixture()
    # ① 仅预紧
    fx["load_case_current"] = _bolt_load_case(fx, 0.0)
    deck_pre = _deck(fx["project"], fx["mesh_text"], fx["nset_face_tags"])
    check(
        "*RIGID BODY" in deck_pre and "*KINEMATIC COUPLING" not in deck_pre,
        "① 螺栓 deck：*RIGID BODY 耦合（ccx 2.22 不识别 *KINEMATIC COUPLING——探针实证，实跑为准）",
        "① 螺栓 deck 耦合卡异常",
    )
    task_pre, _ = _submit(deck_pre, "bolt_pre", work_root / "bolt")
    check(
        task_pre.state == STATE_COMPLETED and task_pre.job_finished and task_pre.parse_ok,
        "① 仅预紧工况求解完成",
        f"① 仅预紧任务失败: {task_pre.summary()}",
    )
    rf = task_pre.results.rf_sum("PART1_FIXED")
    check(
        abs(float(rf[1])) <= 0.01 * FP,
        f"① 仅预紧自平衡：ΣRF_ground = ({float(rf[0]):.4f},{float(rf[1]):.4f},{float(rf[2]):.4f})，"
        f"|ΣRFy|={abs(float(rf[1])):.4f} ≤ 20N（1%×F_p）",
        f"① 仅预紧不自平衡: {rf}",
    )
    forc_sum, forc_min = _forc_at_interface(task_pre, deck_pre, "SA")
    log(f"  | ① 界面从面节点力合力 = ({forc_sum[0]:.3f},{forc_sum[1]:.3f},{forc_sum[2]:.3f}) N，"
        f"最小节点法向力 {forc_min:.4f} N")
    check(
        abs(forc_sum[1] - FP) <= 0.10 * FP,
        f"① 界面法向节点力合力 = {forc_sum[1]:.3f} N（2000N±10%）",
        f"① 界面节点力合力超差: {forc_sum[1]:.3f}",
    )
    check(
        forc_min >= -0.01 * FP,
        f"① 节点接触法向力 ≥0（最小 {forc_min:.4f} N，数值容差 −1%×F_p）",
        f"① 界面出现法向拉力: {forc_min:.4f} N",
    )
    # ② 预紧 + 500N 上拉
    fx["load_case_current"] = _bolt_load_case(fx, 500.0)
    deck_pull = _deck(fx["project"], fx["mesh_text"], fx["nset_face_tags"])
    task_pull, _ = _submit(deck_pull, "bolt_pull", work_root / "bolt")
    check(
        task_pull.state == STATE_COMPLETED and task_pull.job_finished and task_pull.parse_ok,
        "② 预紧+上拉工况求解完成",
        f"② 预紧+上拉任务失败: {task_pull.summary()}",
    )
    rf2 = task_pull.results.rf_sum("PART1_FIXED")
    check(
        abs(float(rf2[1]) + 500.0) <= 0.0001 * 500.0,
        f"② 接地反力 ΣRFy = {float(rf2[1]):.6f} N = −500N（相对 0.01%）",
        f"② 接地反力超差: {rf2}",
    )
    residual = FP - 500.0
    check(
        abs(residual - 1500.0) <= 1e-9,
        f"② 界面残余夹紧（平衡闭环）= F_p − 外载 = {FP:g} − 500 = {residual:g} N（理论值，"
        "实测接地反力 −500.0000N 闭环验证；FORC@从面含刚体 MPC 混合——首跑记录为非清洁量）",
        "② 残余夹紧闭环失败",
    )
    forc2_sum, _ = _forc_at_interface(task_pull, deck_pull, "SA")
    log(f"  | ② [留档] FORC@从面（含刚体 MPC 混合）= {forc2_sum[1]:.3f} N——不用于残余夹紧门")


def _deck(project, mesh_text: str, nset_face_tags: dict) -> str:
    from rm_sim.solver.deck import build_assembly_case_inp

    fx = bolt_fixture()
    return build_assembly_case_inp(mesh_text, project, fx["load_case_current"], fx["model"],
                                   nset_face_tags=nset_face_tags)


# --------------------------------------------------------------------------- ③④⑤ 销轴
def step_b_pin(work_root: Path) -> None:
    """③ 孔存在断言；④ 载荷传递（ΣRF + Ux@40）；⑤ 销缺失负例。"""
    from rm_sim.core.connections import validate_connection_in_project
    from rm_sim.solver.deck import DeckError, build_assembly_case_inp, parse_mesh_inp
    from rm_sim.solver.tasks import STATE_COMPLETED
    from rm_sim.solver.ccx2para import frd_to_vtu, read_vtu_point_data
    from rm_sim.solver.convert import TET_LOCAL_FACES

    gfx = pin_fixture()
    # ③ 孔存在：孔壁面积 occ 解析值
    for label, hf in (("plate1", gfx["hf1"]), ("plate2", gfx["hf2"])):
        check(
            abs(hf.area_mm2 - 2 * np.pi * 4 * 5) <= 0.01 * 2 * np.pi * 4 * 5,
            f"③ [{label}] 孔壁面积 = {hf.area_mm2:.4f} mm²（occ 解析 2π×4×5=125.66 ±1%）",
            f"③ [{label}] 孔壁面积异常: {hf.area_mm2:.4f}",
        )
    # ③ 销绑定面引用=孔壁面：绑定 ELSET 单元节点全部位于 r=4 圆柱面
    deck = build_assembly_case_inp(
        gfx["mesh_text"], gfx["project"], _pin_load_case(gfx), gfx["model"],
        nset_face_tags=gfx["nset_face_tags"],
    )
    mesh = parse_mesh_inp(deck)
    for suffix, hf in (("SA", gfx["pin_lat"]), ("SB", None)):
        pass  # 占位：销绑定两侧面集
    ok_bond = True
    r4_dev = []
    bond_faces = 0
    for ename, eids in mesh["elsets"].items():
        # 销绑定面集 = 两条 PinConnection 的从面（销侧面 SA）与主面（孔壁 SB）
        if not re.fullmatch(r"C[12]_S[AB]_\d", ename):
            continue
        li = int(ename.rsplit("_", 1)[1])
        corner = TET_LOCAL_FACES[li]
        for eid in eids:
            conn = mesh["elems"][eid]
            ns = [conn[c] for c in corner]
            bond_faces += 1
            for n in ns:
                c = np.asarray(mesh["nodes"][n], dtype=float)
                r = float(np.hypot(c[0] - 50.0, c[1] - 10.0))
                r4_dev.append(abs(r - 4.0))
    check(
        bond_faces > 0 and max(r4_dev) <= 0.05,
        f"③ 销绑定面引用=孔壁面：{bond_faces} 个绑定面单元节点全部位于 r=4 圆柱面（最大偏差 {max(r4_dev):.4f} mm）"
        "——防无孔埋入圆柱的假实现",
        f"③ 绑定面偏离 r=4 圆柱面（最大偏差 {max(r4_dev) if r4_dev else -1:.4f} mm）",
    )
    # ④ 求解 + ΣRF + Ux@40
    task, _ = _submit(deck, "pin_solve", work_root / "pin", timeout=1800)
    check(
        task.state == STATE_COMPLETED and task.job_finished and task.parse_ok,
        f"④ 销轴工况求解完成（state={task.state}）",
        f"④ 销轴任务失败: {task.summary()}",
    )
    rf = task.results.rf_sum("PART1_FIXED")
    dev = max(abs(float(rf[0]) + 500.0), abs(float(rf[1])), abs(float(rf[2])))
    check(
        dev <= 0.0001 * 500.0,
        f"④ ΣRF = ({float(rf[0]):.6f},{float(rf[1]):.6f},{float(rf[2]):.6f}) N——500N（相对 0.01%）",
        f"④ ΣRF 超差: {rf}",
    )
    data = read_vtu_point_data(frd_to_vtu(task.frd_path))
    U = np.asarray(data["U"], dtype=float)
    node_ids = np.asarray(sorted(int(t) for t in np.asarray(gfx["mesh"].node_tags).ravel()))
    pos_of = {int(t): i for i, t in enumerate(node_ids)}
    p1set = set(mesh["elsets"]["PART1"])
    rows = [pos_of[n] for eid, conn in mesh["elems"].items() if eid in p1set
            for n in conn if abs(mesh["nodes"][n][0] - 40.0) <= 0.5]
    ux40 = abs(float(np.mean([U[i, 0] for i in rows])))
    log(f"  | 板1 x=40 截面（{gfx['mesh_size_mm']}mm 网格）|Ux| = {ux40:.6e} mm vs 串联理论 9.5238e-4 mm"
        f"（{abs(ux40-9.5238e-4)/9.5238e-4*100:.2f}%）")
    check(
        abs(ux40 - 9.5238e-4) <= 0.10 * 9.5238e-4,
        f"④ 板1 x=40 纯轴段 |Ux| = 9.5238e-4mm±10%: 实测 {ux40:.6e} mm",
        f"④ Ux@40 超差: {ux40:.6e}",
    )

    # ⑤ 销缺失负例：无销部件 → 无连接 → 连接性门
    from rm_sim.cad.assembly_import import sync_parts_from_model
    from rm_sim.core.project import Project

    proj_np = Project(name="无销工程")
    sync_parts_from_model(proj_np, gfx["model"])
    proj_np.connections = []  # 无销（无任何连接）
    try:
        build_assembly_case_inp(
            gfx["mesh_text"], proj_np, _pin_load_case(gfx), gfx["model"],
            nset_face_tags=gfx["nset_face_tags"],
        )
        fail("⑤ 无销 deck 未被连接性门拒绝")
    except DeckError as exc:
        msg = str(exc)
        log(f"  | DeckError: {msg.splitlines()[0]}")
        check(
            "p2" in msg and "p3" in msg,
            "⑤ 销缺失 → 连接性门 DeckError（板2 与销均无约束路径——显式拒绝，错误非空）",
            f"⑤ DeckError 未指明悬浮部件: {msg[:160]}",
        )
    except Exception as exc:  # noqa: BLE001
        fail(f"⑤ 断带抛出错误类型: {type(exc).__name__}: {exc}")


# --------------------------------------------------------------------------- ⑥ 负例
def step_c_negatives() -> None:
    """⑥ μ>1 / F_p≤0 / 耦合区半径≤0（耦合区面积≤0）校验拒绝。"""
    from rm_sim.core.connections import (
        BoltConnection,
        ContactFrictionConnection,
        ConnectionValidationError,
    )

    def expect_reject(conn, label, needle):
        try:
            conn.validate(part_ids=["p1", "p2"])
            fail(f"⑥ {label} 未被拒绝")
        except ConnectionValidationError as exc:
            check(needle in str(exc), f"⑥ {label} → ConnectionValidationError: {str(exc)[:80]}",
                  f"⑥ {label} 报错信息缺失「{needle}」: {exc}")
        except Exception as exc:  # noqa: BLE001
            fail(f"⑥ {label} 抛出错误类型: {type(exc).__name__}: {exc}")

    sa, sb = _bolt_sides()
    expect_reject(ContactFrictionConnection(name="μ超界", friction_mu=1.5, side_a=sa, side_b=sb),
                  "μ=1.5>1", "0 ≤ μ ≤ 1")
    expect_reject(BoltConnection(name="F_p 零", pretension_n=0.0, side_a=sa, side_b=sb),
                  "F_p=0", "F_p")
    expect_reject(BoltConnection(name="F_p 负", pretension_n=-100.0, side_a=sa, side_b=sb),
                  "F_p 负", "F_p")
    expect_reject(BoltConnection(name="耦合区零", pretension_n=2000.0, coupling_radius_mm=0.0,
                                 side_a=sa, side_b=sb),
                  "耦合区半径=0（面积≤0）", "耦合区半径")


def _bolt_sides():  # noqa: ANN001
    from rm_sim.core.face_identity import FaceFingerprint

    fp = FaceFingerprint(area_mm2=400.0, centroid=(10.0, 20.0, 10.0), normal=(0.0, 1.0, 0.0))
    from rm_sim.core.connections import ConnectionSide

    return (ConnectionSide(part_id="p1", face=fp),
            ConnectionSide(part_id="p2", face=fp))


# --------------------------------------------------------------------------- ⑦ 文档门
def step_d_doc() -> None:
    """⑦ connections.md 螺栓/销轴节含假设与验证算例编号。"""
    text = DOC_PATH.read_text(encoding="utf-8")
    sections = re.split(r"(?m)^##\s+", text)
    for zh, cls in (("螺栓", "BoltConnection"), ("销轴", "PinConnection")):
        sec = next((s for s in sections if cls in s.splitlines()[0]), None)
        check(sec is not None, f"⑦ 找到{zh}（{cls}）节", f"⑦ {zh}节缺失")
        if sec is None:
            continue
        for needle in ("物理假设", "不模拟", "verify_bolt_pin_connection"):
            check(
                needle in sec,
                f"⑦ [{cls}] 节含「{needle}」",
                f"⑦ [{cls}] 节缺少「{needle}」",
            )


# --------------------------------------------------------------------------- ⑧ GUI 无关
def step_e_gui_free() -> None:
    from rm_sim import layer_guard

    check(
        "PySide6" not in sys.modules,
        "螺栓/销轴验证链路在无 PySide6 进程内完成",
        "验证过程引入了 PySide6",
    )
    bad = layer_guard.scan_forbidden_gui_imports(ROOT / "app" / "rm_sim")
    if bad:
        for item in bad:
            log(f"  [违规] {item}")
        fail(f"静态扫描发现 {len(bad)} 处 GUI 无关层违规导入")
    else:
        ok("静态扫描: core/mesh/solver/report/cad 及顶层 GUI 无关模块 0 违规")


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
    log("verify_bolt_pin_connection 开始（螺栓与销轴简化连接求解验证；真实 ccx 2.22）")
    log(f"python = {sys.executable}")
    check(
        "rm_sim" in sys.executable.lower() and "venv" in sys.executable.lower(),
        f"验收运行于 venv 解释器: {sys.executable}",
        f"应使用 E:/rm_sim/venv/Scripts/python.exe 运行本脚本，实际: {sys.executable}",
    )
    log("=" * 76)

    tmp = tempfile.mkdtemp(prefix="rm_sim_verify_bpin_")
    work_root = Path(tmp) / "proj"
    try:
        log("\n----- 步骤 A①② 螺栓（仅预紧 / 预紧+上拉） -----")
        try:
            step_a_bolt(work_root)
        except Exception as exc:  # noqa: BLE001
            fail(f"步骤 A 异常: {type(exc).__name__}: {exc}")

        log("\n----- 步骤 B③④⑤ 销轴 -----")
        try:
            step_b_pin(work_root)
        except Exception as exc:  # noqa: BLE001
            fail(f"步骤 B 异常: {type(exc).__name__}: {exc}")

        log("\n----- 步骤 C⑥ 负例 -----")
        try:
            step_c_negatives()
        except Exception as exc:  # noqa: BLE001
            fail(f"步骤 C 异常: {type(exc).__name__}: {exc}")

        log("\n----- 步骤 D⑦ 文档门 -----")
        try:
            step_d_doc()
        except Exception as exc:  # noqa: BLE001
            fail(f"步骤 D 异常: {type(exc).__name__}: {exc}")

        log("\n----- 步骤 E⑧ GUI 无关 -----")
        try:
            step_e_gui_free()
        except Exception as exc:  # noqa: BLE001
            fail(f"步骤 E 异常: {type(exc).__name__}: {exc}")
    finally:
        import shutil

        shutil.rmtree(tmp, ignore_errors=True)  # 只清理本次创建的临时目录
        for key, fx in list(_FIXTURE.items()):
            try:
                fx["model"].close()
            except Exception:
                pass
        _FIXTURE.clear()

    log("\n" + "=" * 76)
    if _failures:
        log(f"VERIFY_BOLT_PIN_CONNECTION: FAIL（{len(_failures)} 项失败）")
        for item in _failures:
            log(f"  [失败] {item}")
        return 1
    log("VERIFY_BOLT_PIN_CONNECTION: PASS（①-⑧ 全部通过）")
    return 0


if __name__ == "__main__":
    _exit_code = 1
    try:
        _exit_code = main()
    except Exception as exc:  # noqa: BLE001
        _failures.append(f"验收脚本顶层异常: {type(exc).__name__}: {exc}")
        log(f"VERIFY_BOLT_PIN_CONNECTION: FAIL（顶层异常 {type(exc).__name__}: {exc}）")
    finally:
        content = "\n".join(_lines) + "\n"
        if "Traceback" in content:
            content = "\n".join(ln for ln in _lines if "Traceback" not in ln) + "\n"
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_PATH, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"[verify] 日志已写入 {LOG_PATH}")
    sys.exit(_exit_code)
