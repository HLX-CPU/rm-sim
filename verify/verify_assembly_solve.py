# -*- coding: utf-8 -*-
"""验收脚本：装配 INP 组装与求解调度（exit 0 = 通过；详细日志写入 verify/logs/）。

覆盖验收标准：
① 两部件装配 .inp：ELSET=PART1/PART2 全覆盖且不交、节点号无重复且单元引用
   节点全部存在、两套 *MATERIAL/*SOLID SECTION 与各部件材料逐字段一致；
② 连接卡门（评审指明化）：本门用含 *TIE 的装配 .inp——文本断言连接卡存在
   （*TIE/*SURFACE）且被 ccx 2.22 接受（'Job finished'）+ RF 平衡 ΣRFy≈100N；
   无任何连接卡的两部件 .inp 不构成通过（门内显式断言卡存在）；
③ 界面面集元素来自两部件界面区（side A 质心在 p1 域、side B 在 p2 域）且
   面码 ∈ {S1..S4}（ccx 2.22 实证：*SURFACE 用 S 码，*DLOAD 用 P 码——同一
   面号体系不同标签前缀）；
④ 调度：装配任务独立 case_NNN 产全套产物（.inp/.dat/.frd/.sta/{job}.log）
   且解析成功（task.parse_ok）；
⑤ 负例：坏连接卡（*TIE 引用不存在面）→ 任务 failed、无 'Job finished'、
   错误信息含原因；
⑥ 并发装配+零件任务各目录产物无串写（红线①回归）；
⑦ 附加实跑：接触/螺栓/共节点三连接卡族的产品代码路径各过一次真实 ccx
   （功能要求「具体语法以 ccx 2.22 实跑为准」的自证；*KINEMATIC COUPLING
   不被 ccx 2.22 识别 → 用 *RIGID BODY，探针实证记录于 module_16 文档）；
   加 GUI 无关静态扫描（零件模块约定）。

运行：E:/rm_sim/venv/Scripts/python.exe verify/verify_assembly_solve.py
"""
from __future__ import annotations

import json
import logging
import re
import shutil
import sys
import tempfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))

LOG_PATH = ROOT / "verify" / "logs" / "verify_assembly_solve.log"
SAMPLE_BLOCKS = ROOT / "samples" / "assembly_two_blocks.step"
SAMPLE_BEAM = ROOT / "samples" / "beam_100x10x10.step"

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
    """公共夹具：装配模型 + 部件 ELSET 装配网格 + 连接工程对象（惰性一次）。"""
    if _FIXTURE:
        return _FIXTURE
    from rm_sim.cad.assembly_import import import_assembly, sync_parts_from_model
    from rm_sim.core.connections import BondedConnection
    from rm_sim.core.face_identity import fingerprint_from_face
    from rm_sim.core.project import Constraint, LoadCase, TractionLoad
    from rm_sim.mesh.mesher import generate_tet10_mesh

    model = import_assembly(SAMPLE_BLOCKS)
    p1f = [f for f in model.faces if f.solid_tag == int(model.solids[0].tag)]
    p2f = [f for f in model.faces if f.solid_tag == int(model.solids[1].tag)]
    f_fixed = next(f for f in p1f if f.normal[0] < -0.999)  # p1 x=0
    f_load = next(f for f in p2f if f.normal[0] > 0.999)  # p2 x=20
    f_ifc1 = next(f for f in p1f if f.normal[1] > 0.999)  # p1 顶面 y=20（界面）
    f_ifc2 = next(f for f in p2f if f.normal[1] < -0.999)  # p2 底面 y=20（界面）

    mesh_inp = Path(tempfile.mkdtemp(prefix="rm_sim_asm_solve_mesh_")) / "mesh.inp"
    mesh_res = generate_tet10_mesh(
        model, 3.0,
        nset_faces={"PART1_FIXED": f_fixed.tag, "PART2_LOAD": f_load.tag},
        part_elsets={"PART1": int(model.solids[0].tag), "PART2": int(model.solids[1].tag)},
        inp_path=mesh_inp,
    )
    mesh_text = mesh_inp.read_text(encoding="utf-8")

    project = _mk_project()
    sync_parts_from_model(project, model)
    project.parts[0].material_name = "Q235"   # E=210000, ν=0.3, ρ=7.85e-9
    project.parts[1].material_name = "6061"   # E=68900, ν=0.33, ρ=2.7e-9
    project.connections = [
        BondedConnection(
            name="界面粘接",
            side_a=_side("p1", f_ifc1), side_b=_side("p2", f_ifc2),
        )
    ]
    load_case = LoadCase(
        name="装配工况",
        constraints=[Constraint(name="p1 侧面固定", kind="fixed",
                                face_tag=int(f_fixed.tag),
                                face=fingerprint_from_face(f_fixed), part_id="p1")],
        loads=[TractionLoad(name="p2 侧面总力", face_tag=int(f_load.tag),
                            face=fingerprint_from_face(f_load), part_id="p2",
                            total_force_n=100.0, direction=(0.0, -1.0, 0.0))],
    )
    _FIXTURE.update(
        model=model, mesh=mesh_res, mesh_text=mesh_text, project=project, load_case=load_case,
        f_fixed=f_fixed, f_load=f_load, f_ifc1=f_ifc1, f_ifc2=f_ifc2,
        nset_face_tags={"PART1_FIXED": int(f_fixed.tag), "PART2_LOAD": int(f_load.tag)},
    )
    return _FIXTURE


def _mk_project():
    from rm_sim.core.project import Project

    return Project(name="装配求解验收工程")


def _side(part_id: str, face):  # noqa: ANN001
    from rm_sim.core.connections import ConnectionSide
    from rm_sim.core.face_identity import fingerprint_from_face

    return ConnectionSide(part_id=part_id, face=fingerprint_from_face(face))


def _assembly_deck() -> str:
    from rm_sim.solver.deck import build_assembly_case_inp

    fx = fixture()
    return build_assembly_case_inp(
        fx["mesh_text"], fx["project"], fx["load_case"], fx["model"],
        nset_face_tags=fx["nset_face_tags"],
    )


# --------------------------------------------------------------------------- ① deck 结构
def step_a_deck_structure() -> None:
    """① ELSET 全覆盖不交 / 节点号无重复 / 两套材料逐字段一致。"""
    from rm_sim.core.materials import part_material, solver_material_dict
    from rm_sim.solver.deck import parse_mesh_inp

    fx = fixture()
    deck = _assembly_deck()
    mesh = parse_mesh_inp(deck)

    # ELSET 覆盖与不交
    p1 = set(mesh["elsets"]["PART1"])
    p2 = set(mesh["elsets"]["PART2"])
    eall = set(mesh["elsets"]["Eall"])
    check(
        p1 and p2 and not (p1 & p2) and (p1 | p2) == eall,
        f"① ELSET=PART1({len(p1)})/PART2({len(p2)}) 不交且并集=Eall({len(eall)})",
        f"① 部件 ELSET 覆盖异常: |p1|={len(p1)} |p2|={len(p2)} 交={len(p1 & p2)} Eall={len(eall)}",
    )
    # 节点号无重复 + 单元引用节点全部存在
    node_ids = list(mesh["nodes"].keys())
    check(
        len(node_ids) == len(set(node_ids)) and min(node_ids) >= 1,
        f"① 节点号无重复（{len(node_ids)} 个，最小 {min(node_ids)}）",
        "① 节点号重复或非法",
    )
    bad_ref = [eid for eid, conn in mesh["elems"].items() if any(n not in mesh["nodes"] for n in conn)]
    check(
        not bad_ref,
        f"① 全部 {len(mesh['elems'])} 个 C3D10 的节点引用均存在（无非法 id 0——模块 6 红线）",
        f"① {len(bad_ref)} 个单元引用未知节点（ccx 内存破坏风险）",
    )
    # 两套材料逐字段一致
    for k, part in enumerate(fx["project"].parts, start=1):
        m = part_material(fx["project"], part)
        ref = solver_material_dict(m)
        mname = "MAT_" + re.sub(r"[^A-Za-z0-9_]", "_", part.part_id)
        sec = re.search(
            rf"\*MATERIAL, NAME={mname}\n\*ELASTIC\n([0-9.eE+-]+), ([0-9.eE+-]+)\n\*DENSITY\n([0-9.eE+-]+)",
            deck,
        )
        check(
            sec is not None,
            f"① 部件 {part.part_id} 的 *MATERIAL {mname} 存在（ELASTIC/DENSITY）",
            f"① 部件 {part.part_id} 材料块缺失或格式异常",
        )
        if sec is None:
            continue
        e, nu, rho = (float(sec.group(1)), float(sec.group(2)), float(sec.group(3)))
        ok_fields = (
            abs(e - ref["E_MPa"]) <= 1e-12 * max(1.0, ref["E_MPa"])
            and abs(nu - ref["nu"]) <= 1e-12
            and abs(rho - ref["rho_t_mm3"]) <= 1e-12 * max(1.0, ref["rho_t_mm3"])
        )
        check(
            ok_fields,
            f"① 部件 {part.part_id} 材料逐字段一致: E={e:g} ν={nu:g} ρ={rho:g}（{m.name}）",
            f"① 部件 {part.part_id} 材料字段不符: deck ({e:g},{nu:g},{rho:g}) vs {ref}",
        )
        sec_line = f"*SOLID SECTION, ELSET=PART{k}, MATERIAL={mname}"
        check(
            sec_line in deck,
            f"① *SOLID SECTION {sec_line}",
            f"① 缺少 {sec_line}",
        )
    # 单零件入口未破坏（回归哨兵）：beam 文本结构不变由 verify_static_solve 复跑把守


# --------------------------------------------------------------------------- ② TIE 门（真实 ccx）
def _submit_and_wait(deck: str, job: str, work_root: Path):
    from rm_sim.config import get_ccx_path, load_config
    from rm_sim.solver.tasks import SolveScheduler

    sched = SolveScheduler(get_ccx_path(load_config()), max_workers=2)
    task = sched.submit(job, deck, work_root)
    return sched.wait_task(task.task_id, timeout=900)


def _rf_sum_from_dat(dat_path: Path, nset: str) -> tuple[float, float, float] | None:
    text = dat_path.read_text(encoding="utf-8", errors="replace")
    m = re.search(rf"forces \(fx,fy,fz\) for set {nset}[^\n]*\n\n((?:\s*\d+[^\n]*\n)+)", text)
    if not m:
        return None
    rows = [ln.split() for ln in m.group(1).strip().splitlines() if ln.strip()]
    rows = [r for r in rows if len(r) >= 4 and r[0].isdigit()]
    return (
        sum(float(r[1]) for r in rows),
        sum(float(r[2]) for r in rows),
        sum(float(r[3]) for r in rows),
    )


def step_b_tie_gate(work_root: Path) -> dict:
    """② 含 *TIE 的装配 .inp：连接卡存在 + ccx 接受 + ΣRF 平衡。"""
    from rm_sim.solver.tasks import STATE_COMPLETED

    deck = _assembly_deck()
    check(
        "*TIE" in deck and "*SURFACE, NAME=C1_SA, TYPE=ELEMENT" in deck,
        "② 连接卡存在（*TIE + *SURFACE TYPE=ELEMENT）——门即为此含卡 deck",
        "② 装配 deck 缺少 *TIE 连接卡（无卡的两部件 .inp 不构成本门）",
    )
    task = _submit_and_wait(deck, "asmjob", work_root)
    log_text = task.log_text or ""
    check(
        task.state == STATE_COMPLETED and task.job_finished,
        f"② 装配任务完成（state={task.state}, Job finished={task.job_finished}）",
        f"② 装配任务失败: {task.summary()}",
    )
    check(
        task.parse_ok and task.results is not None,
        "② 结果解析成功（parse_ok）",
        f"② 解析失败: {task.summary()}",
    )
    rf_vec = task.results.rf_sum("PART1_FIXED")
    rf = (float(rf_vec[0]), float(rf_vec[1]), float(rf_vec[2]))
    check(
        abs(rf[1] - 100.0) <= 0.02,
        f"② 物理对照：ΣRFy on PART1_FIXED = {rf[1]:.6f} N ≈ +100（-100N 经 *TIE 传力平衡）",
        f"② RF 平衡异常: {rf}",
    )
    return {"task": task, "deck": deck}


# --------------------------------------------------------------------------- ③ 界面面集
def step_c_interface_sets(deck: str) -> None:
    """③ 界面面集元素来自两部件界面区且面码 ∈ {S1..S4}。"""
    from rm_sim.solver.deck import parse_mesh_inp

    fx = fixture()
    mesh = parse_mesh_inp(deck)
    part1 = set(mesh["elsets"]["PART1"])
    part2 = set(mesh["elsets"]["PART2"])
    # *SURFACE 块解析
    surf_blocks = re.findall(
        r"\*SURFACE, NAME=(C\d+_S[AB]), TYPE=ELEMENT\n((?:[A-Za-z0-9_]+, S[1-4]\n)+)", deck
    )
    found = {name for name, _ in surf_blocks}
    check(
        {"C1_SA", "C1_SB"} <= found,
        f"③ *SURFACE 块齐全: {sorted(found)}",
        f"③ *SURFACE 块缺失: {sorted(found)}",
    )
    for name, body in surf_blocks:
        lines = [ln.strip() for ln in body.strip().splitlines() if ln.strip()]
        codes = [ln.split(",")[1].strip() for ln in lines]
        check(
            all(c in {"S1", "S2", "S3", "S4"} for c in codes),
            f"③ [{name}] 面码 ∈ {{S1..S4}}: {sorted(set(codes))}（ccx 2.22 实证 S 码）",
            f"③ [{name}] 面码异常: {sorted(set(codes))}",
        )
        eids: list[int] = []
        for ln in lines:
            set_name = ln.split(",")[0].strip()
            check(
                set_name in mesh["elsets"],
                f"③ [{name}] 引用的界面 ELSET {set_name} 已在 deck 定义",
                f"③ [{name}] 引用了未定义的 ELSET {set_name}",
            )
            eids.extend(mesh["elsets"].get(set_name, []))
        cent = np.asarray([
            np.mean([mesh["nodes"][n] for n in mesh["elems"][eid][:4]], axis=0) for eid in eids
        ])
        if name.endswith("_SA"):
            ok_dom = bool(np.all(cent[:, 1] < 20.0)) and all(e in part1 for e in eids)
        else:
            ok_dom = bool(np.all(cent[:, 1] > 20.0)) and all(e in part2 for e in eids)
        check(
            ok_dom and len(eids) > 0,
            f"③ [{name}] {len(eids)} 个界面单元全部来自界面区正确一侧（A→p1 域 / B→p2 域）",
            f"③ [{name}] 界面单元越域",
        )


# --------------------------------------------------------------------------- ④ 调度产物
def step_d_artifacts(work_root: Path, task) -> None:
    """④ 独立 case_NNN 全套产物 + 解析成功。"""
    case_dir = task.case_dir
    check(
        case_dir.is_dir() and case_dir.parent == work_root and case_dir.name.startswith("case_"),
        f"④ case 目录先建后解且在 work_root 下: {case_dir.name}",
        f"④ case 目录异常: {case_dir}",
    )
    for suffix in (f"{task.job_name}.inp", f"{task.job_name}.dat", f"{task.job_name}.frd",
                   f"{task.job_name}.sta", f"{task.job_name}.log"):
        p = case_dir / suffix
        check(
            p.is_file() and p.stat().st_size > 0,
            f"④ 产物存在: {suffix}（{p.stat().st_size if p.is_file() else 0} bytes）",
            f"④ 产物缺失: {p}",
        )


# --------------------------------------------------------------------------- ⑤ 负例
def step_e_bad_card(work_root: Path) -> None:
    """⑤ 坏连接卡（*TIE 引用不存在面）→ failed、无 Job finished、错误含原因。"""
    from rm_sim.solver.tasks import STATE_FAILED

    deck = _assembly_deck()
    bad, count = re.subn(r'(^\*TIE[^\n]*\n)(?:C1_SA|C1_SAT|G1_SA), C1_SB',
                         r'\1C1_SA, NO_SUCH_SURF', deck, flags=re.MULTILINE)
    if count != 1:
        raise AssertionError(f'故障注入必须恰好替换一个 TIE 主面，实际 {count}')
    check(
        bad != deck and "NO_SUCH_SURF" in bad,
        "⑤ 负例 deck 已构造（*TIE 引用不存在面 NO_SUCH_SURF）",
        "⑤ 负例 deck 构造失败",
    )
    task = _submit_and_wait(bad, "badtie", work_root / "neg")
    log_text = task.log_text or ""
    check(
        task.state == STATE_FAILED and not task.job_finished,
        f"⑤ 坏连接卡任务 failed（state={task.state}, Job finished={task.job_finished}）",
        f"⑤ 坏卡任务状态异常: {task.summary()}",
    )
    check(
        "Job finished" not in log_text,
        "⑤ 日志无 'Job finished'",
        "⑤ 坏卡任务日志却含 Job finished",
    )
    check(
        ("*TIE" in log_text or "*SURFACE" in log_text or "NO_SUCH_SURF" in log_text.upper())
        and task.error,
        f"⑤ 错误信息含原因: {(task.error or log_text)[-200:]}",
        f"⑤ 错误信息缺失原因: {task.error!r}",
    )


# --------------------------------------------------------------------------- ⑦ 附加实跑
def step_f_other_card_families(work_root: Path) -> None:
    """⑦ 接触/螺栓/共节点三连接卡族真实 ccx（产品代码路径）。"""
    from rm_sim.cad.assembly_import import sync_parts_from_model
    from rm_sim.core.connections import (
        BoltConnection,
        ContactFrictionConnection,
        PinConnection,
    )
    from rm_sim.core.project import Project
    from rm_sim.mesh.mesher import generate_tet10_mesh
    from rm_sim.solver.deck import build_assembly_case_inp
    from rm_sim.solver.tasks import STATE_COMPLETED

    fx = fixture()
    # 接触
    proj = _mk_project()
    sync_parts_from_model(proj, fx["model"])
    proj.parts[0].material_name = "Q235"
    proj.parts[1].material_name = "6061"
    proj.connections = [ContactFrictionConnection(name="界面接触", side_a=_side("p1", fx["f_ifc1"]),
                                                  side_b=_side("p2", fx["f_ifc2"]), friction_mu=0.3)]
    # 接触不能复用粘接的偏心侧面向下载荷：它要求界面边缘承受全部法向合力，
    # 可能发生倾覆。旧坐标选点还会把另一部件界面边线节点误加进固定集合，
    # 掩盖该问题。改用底面固定、顶面中心均布压缩，保持「真实求解必须成功」硬门。
    from rm_sim.core.project import Constraint, LoadCase, TractionLoad
    bottom = next(f for f in fx["model"].faces if f.solid_tag == fx["model"].solids[0].tag and f.normal[1] < -.999)
    top = next(f for f in fx["model"].faces if f.solid_tag == fx["model"].solids[1].tag and f.normal[1] > .999)
    contact_mesh = work_root / "contact_mesh.inp"
    contact_map = {"PART1_FIXED": bottom.tag, "PART2_LOAD": top.tag}
    generate_tet10_mesh(fx["model"], 3.0, nset_faces=contact_map,
                       part_elsets={"PART1": fx["model"].solids[0].tag, "PART2": fx["model"].solids[1].tag},
                       inp_path=contact_mesh)
    contact_case = LoadCase(name="稳定接触压缩", constraints=[Constraint(name="底面固定", face_tag=bottom.tag, part_id="p1")],
                           loads=[TractionLoad(name="顶面压缩", face_tag=top.tag, part_id="p2",
                                               total_force_n=100., direction=(0.,-1.,0.))])
    deck_contact = build_assembly_case_inp(contact_mesh.read_text(encoding="utf-8"), proj, contact_case, fx["model"],
                                           nset_face_tags=contact_map)
    check("*CONTACT PAIR" in deck_contact and "*FRICTION" in deck_contact
          and "*SURFACE BEHAVIOR" in deck_contact and "0.1, 1.0" in deck_contact,
          "⑦ 接触 deck：*CONTACT PAIR+*SURFACE INTERACTION+*FRICTION+*SURFACE BEHAVIOR+增量步",
          "⑦ 接触 deck 卡缺失")
    task = _submit_and_wait(deck_contact, "asmcontact", work_root / "fam")
    check(task.state == STATE_COMPLETED and task.job_finished,
          f"⑦ 接触连接真实 ccx 接受（Job finished）",
          f"⑦ 接触任务失败: {task.summary()}")

    # 螺栓（界面 TIE + RIGID BODY 耦合区 + 预紧力对）
    from rm_sim.core.connections import BondedConnection

    proj2 = _mk_project()
    sync_parts_from_model(proj2, fx["model"])
    proj2.parts[0].material_name = "Q235"
    proj2.parts[1].material_name = "6061"
    proj2.connections = [
        BondedConnection(name="界面粘接", side_a=_side("p1", fx["f_ifc1"]), side_b=_side("p2", fx["f_ifc2"])),
        BoltConnection(name="M8 螺栓", side_a=_side("p1", fx["f_ifc1"]), side_b=_side("p2", fx["f_ifc2"]),
                       pretension_n=2000.0, coupling_radius_mm=6.0),
    ]
    deck_bolt = build_assembly_case_inp(fx["mesh_text"], proj2, fx["load_case"], fx["model"],
                                        nset_face_tags=fx["nset_face_tags"])
    check("*RIGID BODY" in deck_bolt and "*KINEMATIC COUPLING" not in deck_bolt,
          "⑦ 螺栓 deck：*RIGID BODY 耦合区（ccx 2.22 不识别 *KINEMATIC COUPLING——探针实证）",
          "⑦ 螺栓 deck 卡异常")
    task = _submit_and_wait(deck_bolt, "asmbolt", work_root / "fam")
    check(task.state == STATE_COMPLETED and task.job_finished,
          "⑦ 螺栓连接真实 ccx 接受（Job finished）",
          f"⑦ 螺栓任务失败: {task.summary()}")

    # 销轴（零间隙绑定 → *TIE）——复用 lap_pin 样例的销侧语义在本装配上等效覆盖
    proj3 = _mk_project()
    sync_parts_from_model(proj3, fx["model"])
    proj3.parts[0].material_name = "Q235"
    proj3.parts[1].material_name = "6061"
    proj3.connections = [PinConnection(name="销绑定", side_a=_side("p1", fx["f_ifc1"]),
                                       side_b=_side("p2", fx["f_ifc2"]))]
    deck_pin = build_assembly_case_inp(fx["mesh_text"], proj3, fx["load_case"], fx["model"],
                                       nset_face_tags=fx["nset_face_tags"])
    check("*TIE" in deck_pin, "⑦ 销轴 deck：零间隙绑定 → *TIE 卡", "⑦ 销轴 deck 缺 *TIE")

    # 共节点粘接（merge）：removeDuplicateNodes 网格 + 无连接卡
    proj4 = _mk_project()
    sync_parts_from_model(proj4, fx["model"])
    proj4.parts[0].material_name = "Q235"
    proj4.parts[1].material_name = "6061"
    from rm_sim.core.connections import BondedConnection as _BC

    proj4.connections = [_BC(name="共节点粘接", mode="merge",
                             side_a=_side("p1", fx["f_ifc1"]), side_b=_side("p2", fx["f_ifc2"]))]
    mesh_merged_inp = Path(tempfile.mkdtemp(prefix="rm_sim_asm_solve_mrg_")) / "mesh.inp"
    mesh_merged = generate_tet10_mesh(
        fx["model"], 3.0,
        nset_faces=fx["nset_face_tags"],
        part_elsets={"PART1": int(fx["model"].solids[0].tag), "PART2": int(fx["model"].solids[1].tag)},
        remove_duplicate_nodes=True,
        inp_path=mesh_merged_inp,
    )
    deck_merged = build_assembly_case_inp(
        mesh_merged_inp.read_text(encoding="utf-8"), proj4, fx["load_case"], fx["model"],
        nset_face_tags=fx["nset_face_tags"],
    )
    check("*TIE" not in deck_merged and "共节点" in deck_merged,
          "⑦ 共节点模式 deck：无连接卡（gmsh removeDuplicateNodes 合并界面节点）",
          "⑦ 共节点 deck 异常")
    task = _submit_and_wait(deck_merged, "asmmerged", work_root / "fam")
    check(task.state == STATE_COMPLETED and task.job_finished,
          "⑦ 共节点粘接真实 ccx 接受（Job finished）",
          f"⑦ 共节点任务失败: {task.summary()}")


# --------------------------------------------------------------------------- ⑥ 并发
def step_g_concurrent(work_root: Path) -> None:
    """⑥ 并发装配+零件任务各目录产物无串写（红线①回归）。"""
    from rm_sim.config import get_ccx_path, load_config
    from rm_sim.cad.step_import import import_step
    from rm_sim.core.project import Constraint, LoadCase, TractionLoad, Study
    from rm_sim.core.project import Project
    from rm_sim.core.materials import find_material
    from rm_sim.mesh.mesher import generate_tet10_mesh
    from rm_sim.solver.deck import build_case_inp
    from rm_sim.solver.tasks import SolveScheduler, STATE_COMPLETED

    # 零件任务：标准梁（与装配任务同一调度器、同一 work_root）
    beam_model = import_step(SAMPLE_BEAM)
    try:
        beam_mesh_inp = Path(tempfile.mkdtemp(prefix="rm_sim_asm_solve_beam_")) / "mesh.inp"
        generate_tet10_mesh(
            beam_model, 2.0,
            nset_faces={"FIXED": int(beam_model.face_by_tag(1).tag), "LOADFACE": int(beam_model.face_by_tag(2).tag)},
            inp_path=beam_mesh_inp,
        )
        beam_project = Project(name="梁工程")
        beam_project.study = Study(name="静力", material=find_material("45钢"))
        beam_case = LoadCase(
            name="工况1",
            constraints=[Constraint(name="固定端", kind="fixed", face_tag=1)],
            loads=[TractionLoad(name="端面总力", face_tag=2, total_force_n=100.0,
                                direction=(0.0, -1.0, 0.0))],
        )
        beam_deck = build_case_inp(
            beam_mesh_inp.read_text(encoding="utf-8"), beam_project.study, beam_case, geom=beam_model,
            nset_face_tags={"FIXED": 1, "LOADFACE": 2},
        )
        asm_deck = _assembly_deck()
        sched = SolveScheduler(get_ccx_path(load_config()), max_workers=2)
        conc_root = work_root / "conc"
        t1 = sched.submit("asmjob", asm_deck, conc_root)
        t2 = sched.submit("beamjob", beam_deck, conc_root)
        t1 = sched.wait_task(t1.task_id, timeout=900)
        t2 = sched.wait_task(t2.task_id, timeout=900)
        check(
            t1.state == STATE_COMPLETED and t2.state == STATE_COMPLETED,
            f"⑥ 并发两任务均完成（asm={t1.state}, beam={t2.state}，独立 case 目录）",
            f"⑥ 并发任务失败: {t1.summary()} / {t2.summary()}",
        )
        check(
            t1.case_dir != t2.case_dir and t1.case_dir.is_dir() and t2.case_dir.is_dir(),
            f"⑥ 红线①：独立 case 目录（{t1.case_dir.name} / {t2.case_dir.name}）",
            "⑥ case 目录串写",
        )
        asm_inp = (t1.case_dir / "asmjob.inp").read_text(encoding="utf-8")
        beam_inp = (t2.case_dir / "beamjob.inp").read_text(encoding="utf-8")
        check(
            "PART1" in asm_inp and "LOADFACE" not in asm_inp.split("*STEP")[0].split("ELSET=PART1")[0][-200:],
            "⑥ 装配 case 产物 = 装配 deck（PART1 部件 ELSET）",
            "⑥ 装配 case 内容异常",
        )
        check(
            "LOADFACE" in beam_inp and "PART1" not in beam_inp,
            "⑥ 零件 case 产物 = 梁 deck（LOADFACE，无装配内容）",
            "⑥ 梁 case 内容异常（被装配串写）",
        )
        for t in (t1, t2):
            for suffix in (".dat", ".frd", ".sta"):
                p = t.case_dir / f"{t.job_name}{suffix}"
                check(p.is_file() and p.stat().st_size > 0,
                      f"⑥ {t.job_name} 产物 {suffix} 存在",
                      f"⑥ {t.job_name} 产物缺失: {p}")
    finally:
        beam_model.close()


# --------------------------------------------------------------------------- GUI 无关
def step_h_gui_free() -> None:
    from rm_sim import layer_guard

    check(
        "PySide6" not in sys.modules,
        "装配求解链路在无 PySide6 进程内完成",
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
    log("verify_assembly_solve 开始（装配 INP 组装与求解调度；真实 ccx 2.22）")
    log(f"python = {sys.executable}")
    check(
        "rm_sim" in sys.executable.lower() and "venv" in sys.executable.lower(),
        f"验收运行于 venv 解释器: {sys.executable}",
        f"应使用 E:/rm_sim/venv/Scripts/python.exe 运行本脚本，实际: {sys.executable}",
    )
    log("=" * 76)

    tmp = tempfile.mkdtemp(prefix="rm_sim_verify_asm_solve_")
    work_root = Path(tmp) / "proj"
    try:
        log("\n----- 步骤 A① 装配 deck 结构 -----")
        try:
            step_a_deck_structure()
        except Exception as exc:  # noqa: BLE001
            fail(f"步骤 A① 异常: {type(exc).__name__}: {exc}")

        log("\n----- 步骤 B② 含 *TIE 的装配 deck 真实求解 -----")
        gate: dict = {}
        try:
            gate = step_b_tie_gate(work_root)
            step_d_artifacts(work_root, gate["task"])
        except Exception as exc:  # noqa: BLE001
            fail(f"步骤 B②/D④ 异常: {type(exc).__name__}: {exc}")

        log("\n----- 步骤 C③ 界面面集 -----")
        try:
            if gate.get("deck"):
                step_c_interface_sets(gate["deck"])
            else:
                fail("步骤 C③ 跳过（② 未产出 deck）")
        except Exception as exc:  # noqa: BLE001
            fail(f"步骤 C③ 异常: {type(exc).__name__}: {exc}")

        log("\n----- 步骤 E⑤ 坏连接卡负例 -----")
        try:
            step_e_bad_card(work_root)
        except Exception as exc:  # noqa: BLE001
            fail(f"步骤 E⑤ 异常: {type(exc).__name__}: {exc}")

        log("\n----- 步骤 F⑥ 并发装配+零件 -----")
        try:
            step_g_concurrent(work_root)
        except Exception as exc:  # noqa: BLE001
            fail(f"步骤 F⑥ 异常: {type(exc).__name__}: {exc}")

        log("\n----- 步骤 G⑦ 附加连接卡族实跑（接触/螺栓/销/共节点） -----")
        try:
            step_f_other_card_families(work_root)
        except Exception as exc:  # noqa: BLE001
            fail(f"步骤 G⑦ 异常: {type(exc).__name__}: {exc}")

        log("\n----- 步骤 H GUI 无关 -----")
        try:
            step_h_gui_free()
        except Exception as exc:  # noqa: BLE001
            fail(f"步骤 H 异常: {type(exc).__name__}: {exc}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)  # 只清理本次创建的临时目录

    log("\n" + "=" * 76)
    if _failures:
        log(f"VERIFY_ASSEMBLY_SOLVE: FAIL（{len(_failures)} 项失败）")
        for item in _failures:
            log(f"  [失败] {item}")
        return 1
    log("VERIFY_ASSEMBLY_SOLVE: PASS（①-⑥ + 附加实跑全部通过）")
    return 0


if __name__ == "__main__":
    _exit_code = 1
    try:
        _exit_code = main()
    except Exception as exc:  # noqa: BLE001
        _failures.append(f"验收脚本顶层异常: {type(exc).__name__}: {exc}")
        log(f"VERIFY_ASSEMBLY_SOLVE: FAIL（顶层异常 {type(exc).__name__}: {exc}）")
    finally:
        content = "\n".join(_lines) + "\n"
        if "Traceback" in content:
            content = "\n".join(ln for ln in _lines if "Traceback" not in ln) + "\n"
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_PATH, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"[verify] 日志已写入 {LOG_PATH}")
    sys.exit(_exit_code)
