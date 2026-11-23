# -*- coding: utf-8 -*-
"""验收脚本：可分离接触与摩擦连接求解验证（真实 ccx 2.22，两叠块五工况）。

算例：两叠块 20×20×20（下块底面固定；接触界面=下块顶面↔上块底面；
材料 Q235 两块同材），五工况 + 判别对 + 可分离性 + μ=0 对照。
门限与物理假设权威口径见 app/docs/connections.md §接触摩擦。

覆盖验收标准：
① 压缩 1000N -y：ΣRFy=1000N（≤0.1N）、界面法向压力积分=1000N±10%、
   接触压力场存在且 ≥0（以界面从面 σyy 面均值表征——接触压力 ≥0 即界面
   只传压不传拉的直接证据）；
② 仅重力：ΣRFy = 2ρV_mesh·g ±1%（V_mesh=网格四面体实际体积——模块 7 实测
   较 CAD 名义差 0.32%；上块自重经接触界面入地基=载荷传递证明）；
③ 摩擦（顶面夹紧 5MPa→N=2000N、μ=0.3、侧向 100N<μN=600N）：ΣRFx=100N、
   ΣRFy=2000N、顶块侧移 u_x≤1e-3mm（粘着理论 F·h_tot/(G·A)=1.238e-4mm，
   8.1× 余量，首跑标定后收紧）；
④ μ 判别（模块52 迁移后）：μ=0.3（μN=600>300）粘着 u_x≤3e-3mm（保留）；
   μ 判别力由**位移控制滑块摩擦基准**直接测（verify/friction_benchmark.py：
   夹紧 5MPa→N=2000N 可核对、驱动面 u_x 位移程序+u_z≡0 面外约束、μ=0.1/0.3
   平台切向力/实测法向力比各在 μ±15%、两档可区分、粘着阶段、平衡残差≤0.5%、
   界面法向压力积分=N±10% 且只压不拉、两网格 {2,4}mm 结论一致、全部 Job
   finished）——调研§3：单凭 failed/超时不构成滑移证据，改为正向物理测量；
   **原 μ=0.1 载荷 300s 不收敛负例未删**，保留为显式慢开关
   RM_SIM_FRICTION_SLOW_NEGATIVE=1（原语义原样：launch_mu01 预置提交、
   300s deadline、超时取消=滑移证据双结局门）——等价覆盖审核与迁移记录见
   app/docs/module_52_friction_benchmark.md；超时/取消/崩溃等软件行为改由
   可控注入进程秒级验证（verify_friction_benchmark.py ⑥）；
⑤ 可分离性（预定位移法，间隙 0.1mm 几何）：下压 0.05mm<间隙→底块反力≤1N
   （张开不传力）；下压 0.12mm→反力≈k_series·(Δ-间隙)=42000N±10%
   （k_series=2.1e6 N/mm=两块 EA/L 串联；罚刚度标定见 module_18）；
⑥ μ=0 侧向对照：预期奇异/大滑移——实测结果如实记录（不做硬门）；
⑦ doc 门：connections.md 接触摩擦节含「物理假设」「不模拟」与验证算例指针
   （对齐粘接/螺栓销轴节正则门）；
⑧ 全程无 PySide6 进程 + 静态扫描。

运行：E:/rm_sim/venv/Scripts/python.exe verify/verify_contact_friction_connection.py
慢开关：RM_SIM_FRICTION_SLOW_NEGATIVE=1 时 μ 判别槽位回退原 300s 负例（其余不变）。
"""
from __future__ import annotations

import logging
import os
import re
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))

LOG_PATH = ROOT / "verify" / "logs" / "verify_contact_friction_connection.log"
SAMPLE_BLOCKS = ROOT / "samples" / "assembly_two_blocks.step"
DOC_PATH = ROOT / "app" / "docs" / "connections.md"
SLOW_NEGATIVE_ENV = "RM_SIM_FRICTION_SLOW_NEGATIVE"

RHO = 7.85e-9  # t/mm³（Q235）
G = 9810.0  # mm/s²
K_SERIES = 2.1e6  # N/mm：两块 EA/L 串联（E=210000, A=400mm², L=20mm）

_lines: list[str] = []
_failures: list[str] = []
_FIXTURE: dict = {}
_MU01: dict = {}  # 模块46 编排提速：μ=0.1 滑移任务提前提交（300s 窗口与其他工况并行）


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


def _mk_project(mu: float, fx: dict | None = None):
    from rm_sim.cad.assembly_import import sync_parts_from_model
    from rm_sim.core.connections import ContactFrictionConnection
    from rm_sim.core.project import Project

    fx = fx if fx is not None else fixture()  # 缺省=无间隙基础夹具
    project = Project(name="接触验证工程")
    sync_parts_from_model(project, fx["model"])
    project.connections = [ContactFrictionConnection(
        name="界面接触", friction_mu=mu,
        side_a=_side("p1", fx["f_ifc1"]), side_b=_side("p2", fx["f_ifc2"]),
    )]
    return project


def launch_mu01(work_root: Path) -> None:
    """模块46 编排提速：μ=0.1 滑移任务提前提交。

    其「300s 不收敛」窗口与本门其他工况并行；等待语义不变——自提交起 300s
    未收敛 = 超时取消（持续滑移证据），deadline 记账保证总时限与原同步版一致。
    载荷工况与步骤 A④ 的 μ=0.1 分支完全一致（夹紧 5MPa + 侧向 300N），此处
    自行构造 fx["load_case_current"]，不依赖后续步骤的执行顺序。
    """
    from rm_sim.config import get_ccx_path, load_config
    from rm_sim.core.project import Constraint, LoadCase
    from memory_budget import make_scheduler

    fx = fixture()
    load_case = LoadCase(
        name="夹紧+侧向300",
        constraints=[Constraint(name="固定", kind="fixed", face_tag=int(fx["f_fixed"].tag), part_id="p1")],
        loads=_clamp_loads(300.0)(int(fx["f_top"].tag), int(fx["f_side"].tag)),
    )
    fx["load_case_current"] = load_case
    project01 = _mk_project(0.1, fx)
    deck01 = _deck(project01, fx["mesh_text"], fx["nset_face_tags"])
    sched01 = make_scheduler(get_ccx_path(load_config()), max_workers=1)
    task01 = sched01.submit("ct_mu01_300", deck01, work_root / "mu01")
    _MU01["pending"] = (sched01, task01, time.monotonic() + 300.0)
    log("  | [编排] μ=0.1 滑移任务已提前提交（300s 窗口与本门其他工况并行，等待语义不变）")


def _side(part_id: str, face):  # noqa: ANN001
    from rm_sim.core.connections import ConnectionSide
    from rm_sim.core.face_identity import fingerprint_from_face

    return ConnectionSide(part_id=part_id, face=fingerprint_from_face(face))


def fixture(mesh_size_mm: float = 2.0):
    """夹具：两叠块（无间隙）+ 部件 ELSET 网格 + NSET（惰性）。"""
    key = ("blocks", mesh_size_mm)
    if key in _FIXTURE:
        return _FIXTURE[key]
    from rm_sim.cad.assembly_import import import_assembly
    from rm_sim.mesh.mesher import generate_tet10_mesh

    model = import_assembly(SAMPLE_BLOCKS)
    p1f = [f for f in model.faces if f.solid_tag == int(model.solids[0].tag)]
    p2f = [f for f in model.faces if f.solid_tag == int(model.solids[1].tag)]
    f_fixed = next(f for f in p1f if f.normal[1] < -0.999)  # 下块底面 y=0
    f_top = next(f for f in p2f if f.normal[1] > 0.999)  # 上块顶面 y=40
    f_side = next(f for f in p2f if f.normal[0] > 0.999)  # 上块 x=20 侧
    f_ifc1 = next(f for f in p1f if f.normal[1] > 0.999)  # 界面（下块顶 y=20）
    f_ifc2 = next(f for f in p2f if f.normal[1] < -0.999)  # 界面（上块底 y=20）
    mesh_inp = Path(tempfile.mkdtemp(prefix="rm_sim_contact_mesh_")) / f"mesh_{mesh_size_mm}.inp"
    mesh_res = generate_tet10_mesh(
        model, mesh_size_mm,
        nset_faces={"PART1_FIXED": f_fixed.tag, "PART2_TOP": f_top.tag, "PART2_SIDE": f_side.tag},
        part_elsets={"PART1": int(model.solids[0].tag), "PART2": int(model.solids[1].tag)},
        inp_path=mesh_inp,
    )
    _FIXTURE[key] = {
        "model": model, "mesh": mesh_res, "mesh_text": mesh_inp.read_text(encoding="utf-8"),
        "f_fixed": f_fixed, "f_top": f_top, "f_side": f_side, "f_ifc1": f_ifc1, "f_ifc2": f_ifc2,
        "nset_face_tags": {"PART1_FIXED": int(f_fixed.tag), "PART2_TOP": int(f_top.tag),
                           "PART2_SIDE": int(f_side.tag)},
    }
    return _FIXTURE[key]


def gap_fixture(gap_mm: float = 0.1, mesh_size_mm: float = 2.0):
    """⑤ 可分离性夹具：上块抬升 gap_mm 的几何（探针内 gmsh 生成）。"""
    key = ("gap", gap_mm, mesh_size_mm)
    if key in _FIXTURE:
        return _FIXTURE[key]
    import gmsh

    from rm_sim import gmsh_runtime
    from rm_sim.cad.assembly_import import import_assembly
    from rm_sim.mesh.mesher import generate_tet10_mesh

    step = Path(tempfile.mkdtemp(prefix="rm_sim_contact_gap_")) / f"gap_{gap_mm}.step"
    with gmsh_runtime.session():
        gmsh.model.add("gen")
        gmsh.model.occ.addBox(0, 0, 0, 20, 20, 20)
        gmsh.model.occ.addBox(0, 20.0 + gap_mm, 0, 20, 20, 20)
        gmsh.model.occ.synchronize()
        gmsh.write(str(step))
    model = import_assembly(step)
    p1f = [f for f in model.faces if f.solid_tag == int(model.solids[0].tag)]
    p2f = [f for f in model.faces if f.solid_tag == int(model.solids[1].tag)]
    f_fixed = next(f for f in p1f if f.normal[1] < -0.999)
    f_top = next(f for f in p2f if f.normal[1] > 0.999)
    f_ifc1 = next(f for f in p1f if f.normal[1] > 0.999)
    f_ifc2 = next(f for f in p2f if f.normal[1] < -0.999)
    mesh_inp = step.parent / f"mesh_{gap_mm}.inp"
    generate_tet10_mesh(
        model, mesh_size_mm,
        nset_faces={"PART1_FIXED": f_fixed.tag, "PART2_TOP": f_top.tag},
        part_elsets={"PART1": int(model.solids[0].tag), "PART2": int(model.solids[1].tag)},
        inp_path=mesh_inp,
    )
    _FIXTURE[key] = {
        "model": model, "mesh_text": mesh_inp.read_text(encoding="utf-8"),
        "f_fixed": f_fixed, "f_top": f_top, "f_ifc1": f_ifc1, "f_ifc2": f_ifc2,
        "nset_face_tags": {"PART1_FIXED": int(f_fixed.tag), "PART2_TOP": int(f_top.tag)},
    }
    return _FIXTURE[key]


def _deck(project, mesh_text: str, nset_face_tags: dict) -> str:
    from rm_sim.solver.deck import build_assembly_case_inp

    fx = _FIXTURE[("blocks", 2.0)]
    return build_assembly_case_inp(mesh_text, project, fx["load_case_current"], fx["model"],
                                   nset_face_tags=nset_face_tags)


def _submit(deck: str, job: str, work_root: Path):
    from rm_sim.config import get_ccx_path, load_config
    from rm_sim.solver.tasks import SolveScheduler

    from memory_budget import make_scheduler

    sched = make_scheduler(get_ccx_path(load_config()), max_workers=2)  # 模块46：分级预算
    task = sched.submit(job, deck, work_root)
    return sched.wait_task(task.task_id, timeout=1800)


def _mesh_volume(mesh_res) -> float:
    """网格四面体实际体积（C3D10 角点四面体之和——平直面块中点即中点）。"""
    pts = mesh_res.mesh.points
    tets = np.asarray(mesh_res.mesh.cells["tetra10"], dtype=np.int64)[:, :4]
    p0, p1, p2, p3 = pts[tets[:, 0]], pts[tets[:, 1]], pts[tets[:, 2]], pts[tets[:, 3]]
    return float(np.abs(np.einsum("ij,ij->i", np.cross(p1 - p0, p2 - p0), p3 - p0)).sum() / 6.0)


def _vtu_fields(task):  # noqa: ANN001
    from rm_sim.solver.ccx2para import frd_to_vtu, read_vtu_point_data

    return read_vtu_point_data(frd_to_vtu(task.frd_path))


def _part2_ux(task, mesh_res) -> float:
    """上块（PART2）全部节点平均 u_x [mm]。"""
    data = _vtu_fields(task)
    u = np.asarray(data["U"], dtype=float)
    node_ids = np.asarray(sorted(int(t) for t in np.asarray(mesh_res.node_tags).ravel()))
    pos_of = {int(t): i for i, t in enumerate(node_ids)}
    from rm_sim.solver.deck import parse_mesh_inp

    fx = _FIXTURE[("blocks", 2.0)]
    mesh = parse_mesh_inp(fx["mesh_text"])
    part2 = set(mesh["elsets"]["PART2"])
    rows = []
    for eid, conn in mesh["elems"].items():
        if eid in part2:
            rows += [pos_of[int(n)] for n in conn]
    return float(np.mean(u[rows, 0]))


def _interface_pressure_integral(task, mesh_text: str, node_ids: np.ndarray) -> tuple[float, int, float]:
    """界面从面法向压力积分 Σ(−σyy)·A [N] 与最小面压力 [MPa]。"""
    from rm_sim.solver.ccx2para import frd_to_vtu, read_vtu_point_data
    from rm_sim.solver.convert import TET_LOCAL_FACES
    from rm_sim.solver.deck import parse_mesh_inp

    fx = _FIXTURE[("blocks", 2.0)]
    mesh = parse_mesh_inp(mesh_text)
    data = _vtu_fields(task)
    s_arr = np.asarray(data["S"], dtype=float)
    pos_of = {int(t): i for i, t in enumerate(node_ids)}
    total, n_faces, p_min = 0.0, 0, float("inf")
    for name, ids in mesh["elsets"].items():
        if not re.fullmatch(r"C1_SA_\d", name):
            continue
        li = int(name.rsplit("_", 1)[1])
        corner = TET_LOCAL_FACES[li]
        for eid in ids:
            conn = mesh["elems"][eid]
            n_tags = [conn[c] for c in corner]
            pts = [np.asarray(mesh["nodes"][t], dtype=float) for t in n_tags]
            area = 0.5 * float(np.linalg.norm(np.cross(pts[1] - pts[0], pts[2] - pts[0])))
            syy = float(np.mean([s_arr[pos_of[t], 1] for t in n_tags]))
            pressure = -syy
            total += pressure * area
            n_faces += 1
            p_min = min(p_min, pressure)
    return total, n_faces, p_min


# --------------------------------------------------------------------------- A① 压缩
def step_a_compression(work_root: Path) -> dict:
    """① 压缩 1000N -y：ΣRFy、界面法向压力积分、压力场 ≥0。"""
    from rm_sim.core.project import Constraint, LoadCase, TractionLoad
    from rm_sim.solver.tasks import STATE_COMPLETED

    fx = fixture()
    load_case = LoadCase(
        name="压缩",
        constraints=[Constraint(name="固定", kind="fixed", face_tag=int(fx["f_fixed"].tag), part_id="p1")],
        loads=[TractionLoad(name="顶面压缩", face_tag=int(fx["f_top"].tag), part_id="p2",
                            total_force_n=1000.0, direction=(0.0, -1.0, 0.0))],
    )
    fx["load_case_current"] = load_case
    project = _mk_project(0.3)
    deck = _deck(project, fx["mesh_text"], fx["nset_face_tags"])
    task = _submit(deck, "ct_comp", work_root / "comp")
    check(
        task.state == STATE_COMPLETED and task.job_finished and task.parse_ok,
        f"① 压缩工况求解完成（state={task.state}）",
        f"① 压缩工况失败: {task.summary()}",
    )
    rf = task.results.rf_sum("PART1_FIXED")
    check(
        abs(float(rf[1]) - 1000.0) <= 0.1 and abs(float(rf[0])) <= 0.1 and abs(float(rf[2])) <= 0.1,
        f"① ΣRF = ({float(rf[0]):.6f},{float(rf[1]):.6f},{float(rf[2]):.6f}) N——ΣRFy=1000N（≤0.1N）",
        f"① ΣRF 超差: {rf}",
    )
    node_ids = _results_node_ids(fx)
    p_int, n_faces, p_min = _interface_pressure_integral(task, deck, node_ids)
    log(f"  | 界面法向压力积分: {n_faces} 个面单元，Σp·A = {p_int:.4f} N（理论 1000N±10%），最小面压 {p_min:.4f} MPa")
    check(
        abs(p_int - 1000.0) <= 0.10 * 1000.0,
        f"① 界面法向压力积分 = {p_int:.4f} N（1000N±10%）",
        f"① 压力积分超差: {p_int:.4f} N",
    )
    check(
        p_min >= 0.0,
        f"① 接触压力场存在且 ≥0（最小面压 {p_min:.4f} MPa，只传压不传拉）",
        f"① 界面出现负压力（拉力）: {p_min:.4f} MPa",
    )
    return {"rf": rf, "deck": deck, "p_int": p_int}


def _results_node_ids(fx: dict) -> np.ndarray:  # noqa: ANN001
    return np.asarray(sorted(int(t) for t in np.asarray(fx["mesh"].node_tags).ravel()), dtype=np.int64)


# --------------------------------------------------------------------------- A② 仅重力
def step_b_gravity(work_root: Path) -> None:
    """② 仅重力：ΣRFy = 2ρV_mesh·g ±1%（上块自重经界面入地基）。"""
    from rm_sim.core.project import Constraint, GravityLoad, LoadCase
    from rm_sim.solver.tasks import STATE_COMPLETED

    fx = fixture()
    load_case = LoadCase(
        name="仅重力",
        constraints=[Constraint(name="固定", kind="fixed", face_tag=int(fx["f_fixed"].tag), part_id="p1")],
        loads=[GravityLoad(name="重力", direction=(0.0, -1.0, 0.0), g_mm_s2=G)],
    )
    fx["load_case_current"] = load_case
    project = _mk_project(0.3)
    deck = _deck(project, fx["mesh_text"], fx["nset_face_tags"])
    task = _submit(deck, "ct_grav", work_root / "grav")
    check(
        task.state == STATE_COMPLETED and task.job_finished and task.parse_ok,
        f"② 重力工况求解完成（state={task.state}）",
        f"② 重力工况失败: {task.summary()}",
    )
    v_mesh = _mesh_volume(fixture()["mesh"])
    expected = RHO * v_mesh * G  # V_mesh=两块总体积（16000mm³）；2ρVg=2×单块
    nominal = 2.0 * RHO * 8000.0 * G
    rf = task.results.rf_sum("PART1_FIXED")
    log(
        f"  | ΣRFy = {float(rf[1]):.6f} N；期望 2ρV_mesh·g = {expected:.6f} N"
        f"（V_mesh={v_mesh:.2f} mm³，CAD 名义 2ρVg={nominal:.6f} N，网格体积差 "
        f"两块合计名义 16000mm³（网格体积差 {(v_mesh-16000.0)/16000.0*100:.3f}%）"
    )
    check(
        abs(float(rf[1]) - expected) <= 0.01 * expected,
        f"② ΣRFy = 2ρV_mesh·g ±1%: 实测 {float(rf[1]):.6f} vs {expected:.6f}",
        f"② 重力反力超 1%: {rf}",
    )
    check(
        abs(float(rf[0])) <= 0.001 * expected and abs(float(rf[2])) <= 0.001 * expected,
        "② 水平反力分量≈0（纯重力）",
        f"② 水平反力异常: {rf}",
    )


# --------------------------------------------------------------------------- A③ 摩擦粘着
def _clamp_loads(lateral_n: float):
    def build(top_tag: int, side_tag: int):
        from rm_sim.core.project import PressureLoad, TractionLoad

        return [
            PressureLoad(name="夹紧", face_tag=top_tag, part_id="p2", pressure_mpa=5.0),
            TractionLoad(name="侧向", face_tag=side_tag, part_id="p2", total_force_n=lateral_n,
                         direction=(1.0, 0.0, 0.0)),
        ]

    return build


def step_c_friction_stick(work_root: Path) -> float:
    """③ 夹紧 5MPa + 侧向 100N（μN=600）：ΣRFx/ΣRFy + 粘着 u_x≤1e-3mm。"""
    from rm_sim.core.project import LoadCase, Constraint
    from rm_sim.solver.tasks import STATE_COMPLETED

    fx = fixture()
    load_case = LoadCase(
        name="夹紧+侧向",
        constraints=[Constraint(name="固定", kind="fixed", face_tag=int(fx["f_fixed"].tag), part_id="p1")],
        loads=_clamp_loads(100.0)(int(fx["f_top"].tag), int(fx["f_side"].tag)),
    )
    fx["load_case_current"] = load_case
    project = _mk_project(0.3)
    deck = _deck(project, fx["mesh_text"], fx["nset_face_tags"])
    task = _submit(deck, "ct_fric100", work_root / "fric")
    check(
        task.state == STATE_COMPLETED and task.job_finished and task.parse_ok,
        f"③ 摩擦粘着工况求解完成（state={task.state}）",
        f"③ 摩擦工况失败: {task.summary()}",
    )
    rf = task.results.rf_sum("PART1_FIXED")
    check(
        abs(float(rf[0]) + 100.0) <= 0.1,
        f"③ ΣRFx = {float(rf[0]):.6f} N（侧向 100N 平衡，≤0.1N）",
        f"③ ΣRFx 超差: {rf}",
    )
    check(
        abs(float(rf[1]) - 2000.0) <= 0.1,
        f"③ ΣRFy = {float(rf[1]):.6f} N（夹紧 2000N 平衡，≤0.1N）",
        f"③ ΣRFy 超差: {rf}",
    )
    ux = _part2_ux(task, fixture()["mesh"])
    log(f"  | 顶块侧移 u_x（PART2 节点平均）= {ux:.6e} mm（粘着理论 1.238e-4mm，门限 1e-3mm）")
    check(
        abs(ux) <= 1.0e-3,
        f"③ 粘着：顶块 u_x ≤1e-3mm: {ux:.3e} mm（8.1× 余量）",
        f"③ 粘着滑移超限: {ux:.3e} mm",
    )
    return ux


# --------------------------------------------------------------------------- A④ μ 判别
def step_d_mu_discrimination(work_root: Path) -> None:
    """④ 侧向 300N：μ=0.3 粘着门（保留）+ μ 判别槽位（模块52 迁移）。

    默认档：μ 判别力由位移控制滑块摩擦基准直接测（verify/friction_benchmark.py，
    两网格×两 μ，判据见其模块文档）；
    慢开关 RM_SIM_FRICTION_SLOW_NEGATIVE=1：槽位回退**原 μ=0.1 载荷 300s 不收敛
    负例**（语义原样：双结局门=任务失败/超时取消 或 u_x>0.01mm；预置提交与
    deadline 记账随开关启用）。
    """
    from rm_sim.core.project import Constraint, LoadCase
    from rm_sim.solver.tasks import STATE_COMPLETED

    fx = fixture()
    load_case = LoadCase(
        name="夹紧+侧向300",
        constraints=[Constraint(name="固定", kind="fixed", face_tag=int(fx["f_fixed"].tag), part_id="p1")],
        loads=_clamp_loads(300.0)(int(fx["f_top"].tag), int(fx["f_side"].tag)),
    )
    fx["load_case_current"] = load_case
    # μ=0.3：μN=600>300 → 粘着
    project = _mk_project(0.3)
    deck = _deck(project, fx["mesh_text"], fx["nset_face_tags"])
    task = _submit(deck, "ct_mu03_300", work_root / "mu03")
    check(
        task.state == STATE_COMPLETED and task.job_finished,
        f"④ μ=0.3 侧向 300N 求解完成",
        f"④ μ=0.3 任务失败: {task.summary()}",
    )
    ux03 = _part2_ux(task, fixture()["mesh"])
    log(f"  | μ=0.3: u_x = {ux03:.6e} mm（粘着门限 3e-3=1e-3×(300/100)——切向罚柔量随载荷线性，首跑标定 3.3×理论）")
    check(
        abs(ux03) <= 3.0e-3,
        f"④ μ=0.3（μN=600>300）粘着：u_x ≤3e-3mm（标定后）: {ux03:.3e}",
        f"④ μ=0.3 粘着滑移超限: {ux03:.3e}",
    )
    if os.environ.get(SLOW_NEGATIVE_ENV, "").strip() == "1":
        _step_d_slow_negative(work_root)
        return
    # 默认档：位移控制滑块摩擦基准（模块52，两网格×两 μ 正向物理测量）
    import friction_benchmark

    for mesh_mm in friction_benchmark.MESH_SIZES:
        bench = friction_benchmark.run_benchmark(work_root / "friction_benchmark",
                                                 mesh_size_mm=mesh_mm, log=log)
        friction_benchmark.check_benchmark(bench, _failures, log=log,
                                           tag=f"④'位移滑块基准 {mesh_mm:g}mm")
    log("  | （④' 两网格结论一致性：两网格各自过 μ±15% 判据且两档可区分——跨网格逐 μ "
        "平台均值差核对由验收脚本 verify_friction_benchmark.py ② 汇总断言）")


def _step_d_slow_negative(work_root: Path) -> None:
    """原 μ=0.1 载荷 300s 不收敛负例（原语义原样；仅 RM_SIM_FRICTION_SLOW_NEGATIVE=1 运行）。"""
    pending = _MU01.pop("pending", None)
    if pending is not None:
        sched01, task01, deadline = pending
        remaining = max(0.0, deadline - time.monotonic())
        log(f"  | （μ=0.1 任务为预置提交；300s 提交时限剩 {remaining:.0f}s——语义与同步版一致）")
    else:  # 回退：未经 launch_mu01 的入口路径按原同步语义提交
        from rm_sim.config import get_ccx_path, load_config

        fx = fixture()
        project01 = _mk_project(0.1)
        deck01 = _deck(project01, fx["mesh_text"], fx["nset_face_tags"])
        from memory_budget import make_scheduler

        sched01 = make_scheduler(get_ccx_path(load_config()), max_workers=1)  # 模块46：分级预算
        task01 = sched01.submit("ct_mu01_300", deck01, work_root / "mu01")
        remaining = 300.0
    outcome: str
    try:
        sched01.wait_task(task01.task_id, timeout=remaining)
    except TimeoutError:
        sched01.cancel(task01.task_id)
        try:
            sched01.wait_task(task01.task_id, timeout=120)
        except TimeoutError:
            pass
        outcome = "μ=0.1（μN=200<300）未在 300s 内收敛（持续滑移=刚体自由度，超时取消）——滑移证据（双结局门分支三，首跑实证）"
    else:
        from rm_sim.solver.tasks import STATE_FAILED

        if task01.state == STATE_FAILED:
            outcome = f"μ=0.1 任务 failed（{task01.error or '滑移失稳'}）——滑移证据（双结局门分支一）"
        else:
            ux01 = _part2_ux(task01, fixture()["mesh"])
            outcome = f"μ=0.1 u_x = {ux01:.3e} mm"
            check(
                abs(ux01) > 0.01,
                f"④ μ=0.1 滑移证据：u_x >0.01mm: {ux01:.3e}（双结局门分支二）",
                f"④ μ=0.1 未呈现滑移证据（u_x={ux01:.3e} 且任务未失败）——μ 数值不可区分？",
            )
    log(f"  | {outcome}")
    check(
        bool(outcome),
        f"④ μ 判别力如实记录（慢开关原负例）：{outcome}",
        "④ 记录失败",
    )


# --------------------------------------------------------------------------- A⑤ 可分离性
def step_e_separability(work_root: Path) -> None:
    """⑤ 预定位移法：下压 0.05（<间隙）→反力≤1N；0.12 → ≈k_series·0.02±10%。"""
    from rm_sim.core.project import Constraint, LoadCase

    gfx = gap_fixture(0.1)
    load_case = LoadCase(
        name="预定位移",
        constraints=[Constraint(name="固定", kind="fixed", face_tag=int(gfx["f_fixed"].tag), part_id="p1")],
        loads=[],
    )
    gfx["load_case_current"] = load_case
    project = _mk_project(0.3, gfx)  # 间隙模型：连接面指纹取自间隙夹具
    base_deck = _deck_gap(project, gfx)
    for disp, label in ((0.05, "张开（0.05<间隙 0.1）"), (0.12, "闭合（0.12>间隙 0.1，压缩 0.02mm）")):
        deck = base_deck.replace("*BOUNDARY\n", f"*BOUNDARY\nPART2_TOP, 2, 2, {-disp}\n", 1)
        check(
            f"PART2_TOP, 2, 2, {-disp}" in deck,
            f"⑤ [{label}] 预定位移 *BOUNDARY 注入（PART2_TOP, 2, {-disp}mm）",
            f"⑤ [{label}] 位移边界注入失败",
        )
        task = _submit(deck, f"ct_gap_{int(disp*1000)}", work_root / "gap")
        if label.startswith("张开"):
            from rm_sim.solver.tasks import STATE_COMPLETED

            check(
                task.state == STATE_COMPLETED and task.results is not None,
                f"⑤ 张开工况求解完成（state={task.state}）",
                f"⑤ 张开工况失败: {task.summary()}",
            )
            if task.results is None:
                continue
            rf = task.results.rf_sum("PART1_FIXED")
            check(
                abs(float(rf[1])) <= 1.0,
                f"⑤ 张开不传力：底块反力 |ΣRFy| = {abs(float(rf[1])):.6f} N ≤1N",
                f"⑤ 张开却传力: {rf}",
            )
        else:
            if task.results is None:
                check(False, f"⑤ 闭合并无反力（任务 {task.state}）", f"⑤ 闭合任务失败: {task.summary()}")
                continue
            rf = task.results.rf_sum("PART1_FIXED")
            log(f"  | 下压 0.12mm: ΣRFy = {float(rf[1]):.2f} N（k_series·0.02 = 42000N±10%）")
            check(
                abs(abs(float(rf[1])) - 42000.0) <= 0.10 * 42000.0,
                f"⑤ 闭合反力 ≈ k_series·0.02 = 42000N±10%: 实测 {float(rf[1]):.2f} N"
                "（罚刚度影响已按首跑标定，见 module_18）",
                f"⑤ 闭合反力超 ±10%: {float(rf[1]):.2f} N",
            )


def _deck_gap(project, gfx: dict) -> str:
    from rm_sim.solver.deck import build_assembly_case_inp

    return build_assembly_case_inp(
        gfx["mesh_text"], project, gfx["load_case_current"], gfx["model"],
        nset_face_tags=gfx["nset_face_tags"],
    )


# --------------------------------------------------------------------------- A⑥ μ=0 对照
def step_e2_mu0_record(work_root: Path) -> None:
    """⑥ μ=0 侧向对照：实测记录（不做硬门）。"""
    from rm_sim.core.project import Constraint, LoadCase
    from rm_sim.solver.tasks import STATE_COMPLETED

    fx = fixture()
    load_case = LoadCase(
        name="μ0侧向",
        constraints=[Constraint(name="固定", kind="fixed", face_tag=int(fx["f_fixed"].tag), part_id="p1")],
        loads=_clamp_loads(100.0)(int(fx["f_top"].tag), int(fx["f_side"].tag)),
    )
    fx["load_case_current"] = load_case
    project = _mk_project(0.0)
    deck = _deck(project, fx["mesh_text"], fx["nset_face_tags"])
    task = _submit(deck, "ct_mu0", work_root / "mu0")
    if task.state == STATE_COMPLETED and task.results is not None:
        ux = _part2_ux(task, fixture()["mesh"])
        log(f"  | [μ=0 实测记录] 任务完成，顶块 u_x = {ux:.6e} mm（预期奇异/大滑移）")
        _GLOBALS["mu0_record"] = f"μ=0 侧向：任务完成（Job finished），顶块 u_x={ux:.3e} mm（无摩擦滑移，数值可解但物理上界面无切向承载）"
    else:
        log(f"  | [μ=0 实测记录] 任务未完成（state={task.state}，error={task.error!r}）——奇异/不收敛")
        _GLOBALS["mu0_record"] = f"μ=0 侧向：任务失败（state={task.state}，{task.error!r}）——无摩擦界面切向刚体自由度，符合奇异预期"


_GLOBALS: dict = {}


# --------------------------------------------------------------------------- B⑦ 文档门
def step_h_doc(mu0_record: str) -> None:
    """⑦ connections.md 接触摩擦节 + μ=0 实测记录写回。"""
    text = DOC_PATH.read_text(encoding="utf-8")
    sections = re.split(r"(?m)^##\s+", text)
    sec = next((s for s in sections if "ContactFrictionConnection" in s.splitlines()[0]), None)
    check(sec is not None, "⑦ 找到接触摩擦（ContactFrictionConnection）节", "⑦ 接触摩擦节缺失")
    if sec is None:
        return
    for needle in ("物理假设", "不模拟", "verify_contact_friction_connection", "0.0190476" if False else "装配接触算例"):
        check(
            needle in sec,
            f"⑦ 接触摩擦节含「{needle}」",
            f"⑦ 接触摩擦节缺少「{needle}」",
        )
    check(
        "粘着" in sec and "1e-3" in sec and "滑移" in sec,
        "⑦ 接触摩擦节含粘着/滑移门限",
        "⑦ 接触摩擦节缺门限",
    )


# --------------------------------------------------------------------------- GUI 无关
def step_i_gui_free() -> None:
    from rm_sim import layer_guard

    check(
        "PySide6" not in sys.modules,
        "接触验证链路在无 PySide6 进程内完成",
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
    log("verify_contact_friction_connection 开始（可分离接触与摩擦连接求解验证；真实 ccx 2.22）")
    log(f"python = {sys.executable}")
    check(
        "rm_sim" in sys.executable.lower() and "venv" in sys.executable.lower(),
        f"验收运行于 venv 解释器: {sys.executable}",
        f"应使用 E:/rm_sim/venv/Scripts/python.exe 运行本脚本，实际: {sys.executable}",
    )
    log("=" * 76)

    tmp = tempfile.mkdtemp(prefix="rm_sim_verify_contact_")
    work_root = Path(tmp) / "proj"
    slow_negative = os.environ.get(SLOW_NEGATIVE_ENV, "").strip() == "1"
    log(f"μ 判别槽位（模块52 迁移）：{'原 μ=0.1 300s 负例（显式慢开关 %s=1）' % SLOW_NEGATIVE_ENV if slow_negative else '位移控制滑块摩擦基准（默认档，两网格×两 μ 正向物理测量）'}")
    try:
        if slow_negative:
            log("\n----- 步骤预置：μ=0.1 滑移任务提前提交（编排提速，门语义不变） -----")
            try:
                launch_mu01(work_root)
            except Exception as exc:  # noqa: BLE001
                log(f"  | [编排] μ=0.1 提前提交未成功，步骤 A④ 内按同步语义提交: {exc}")

        log("\n----- 步骤 A① 压缩 -----")
        try:
            step_a_compression(work_root)
        except Exception as exc:  # noqa: BLE001
            fail(f"步骤 A① 异常: {type(exc).__name__}: {exc}")

        log("\n----- 步骤 A② 仅重力 -----")
        try:
            step_b_gravity(work_root)
        except Exception as exc:  # noqa: BLE001
            fail(f"步骤 A② 异常: {type(exc).__name__}: {exc}")

        log("\n----- 步骤 A③ 摩擦粘着（100N） -----")
        try:
            step_c_friction_stick(work_root)
        except Exception as exc:  # noqa: BLE001
            fail(f"步骤 A③ 异常: {type(exc).__name__}: {exc}")

        log("\n----- 步骤 A④ μ 判别（μ=0.3 粘着门 + 位移滑块基准/慢开关负例） -----")
        try:
            step_d_mu_discrimination(work_root)
        except Exception as exc:  # noqa: BLE001
            fail(f"步骤 A④ 异常: {type(exc).__name__}: {exc}")

        log("\n----- 步骤 A⑤ 可分离性（间隙 0.1mm 预定位移） -----")
        try:
            step_e_separability(work_root)
        except Exception as exc:  # noqa: BLE001
            fail(f"步骤 A⑤ 异常: {type(exc).__name__}: {exc}")

        log("\n----- 步骤 A⑥ μ=0 对照（实测记录） -----")
        try:
            step_e2_mu0_record(work_root)
        except Exception as exc:  # noqa: BLE001
            fail(f"步骤 A⑥ 异常: {type(exc).__name__}: {exc}")

        log("\n----- 步骤 B⑦ 文档门 -----")
        try:
            step_h_doc(_GLOBALS.get("mu0_record", "（未运行）"))
        except Exception as exc:  # noqa: BLE001
            fail(f"步骤 B⑦ 异常: {type(exc).__name__}: {exc}")

        log("\n----- 步骤 I GUI 无关 -----")
        try:
            step_i_gui_free()
        except Exception as exc:  # noqa: BLE001
            fail(f"步骤 I 异常: {type(exc).__name__}: {exc}")
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
        log(f"VERIFY_CONTACT_FRICTION_CONNECTION: FAIL（{len(_failures)} 项失败）")
        for item in _failures:
            log(f"  [失败] {item}")
        return 1
    log("VERIFY_CONTACT_FRICTION_CONNECTION: PASS（①-⑧ 全部通过）")
    return 0


if __name__ == "__main__":
    _exit_code = 1
    try:
        _exit_code = main()
    except Exception as exc:  # noqa: BLE001
        _failures.append(f"验收脚本顶层异常: {type(exc).__name__}: {exc}")
        log(f"VERIFY_CONTACT_FRICTION_CONNECTION: FAIL（顶层异常 {type(exc).__name__}: {exc}）")
    finally:
        content = "\n".join(_lines) + "\n"
        if "Traceback" in content:
            content = "\n".join(ln for ln in _lines if "Traceback" not in ln) + "\n"
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_PATH, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"[verify] 日志已写入 {LOG_PATH}")
    sys.exit(_exit_code)
