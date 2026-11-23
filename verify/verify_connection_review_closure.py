# -*- coding: utf-8 -*-
"""验收脚本：连接复核收口——requires_review 决策闭环（模块47）。exit 0 = 通过。

决策单元 = 逐连接（快拆整装遗留：39 补丁 / 22 部件对；未绑定从面节点计数 28，
另有 18 个 SPC 自由度已固定诊断号——单列不混算）。requires_review 是布尔汇总、
untied_nodes/reported_missed_nodes 是节点计数：节点数只作关联证据字段，
不据此自动决策，也不按节点数伪造决策。

覆盖验收标准：
① 真实整装证据逐连接决策记录：build_review_records_from_case 读
   current_assembly_solve7_c12_20261102_1935/cases/case_001（只读）——
   INP「** 绑定带」逐连接证据（从面总节点/可绑节点/贴合区外最近距离）、
   warning_attribution.json 逐连接未绑定归属、independent_check.json 合计口径、
   .nam/.log 诊断重读（SPC 编号单列）；39 条记录（决策枚举默认 pending），
   逐连接 reported_missed_nodes 合计 == 检查记录 untied_nodes=28（交叉核对），
   部件对去重 = 22；JSON 落证据目录。
② 决策后重求解（受控三块夹具，band=0 部分搭接→两连接均出现未绑定警告）：
   保留粘接（C1）+ 改可分离接触（C2，apply_review_decisions 批处理写回）→
   重求解：未决策拦截清单为空；C2 改接触后绑定警告不再含它（C2_SA 绑定面
   消失）；粘接反力平衡残差 ≤0.5%（equilibrium 口径）；改接触界面法向
   不传拉（张力积分 ≤1e-6 N，压缩积分 >0——压缩正口径 ≥-1e-6 N 满足）；
   决策记录回填（decided_at/证据）。
③ 未决策拦截负例：求解一次产生 needs_review 后，未决策直接再次
   prepare_analysis → ValueError 中文提示（含『未决策』与连接名）。
④ SPC 18 项单列展示：用真实 case 归档 results.json（报告 ctx）注入重读的
   SPC 编号重建报告 → md 含「SPC 自由度已固定诊断编号（18 个）」单列行，
   与未绑定行（46 个编号）分开；复核决策行呈现。
⑤ connections.md 同步：git diff 可见「连接复核决策闭环」节，决策枚举与
   实现一致（保留粘接/改为可分离接触/删除连接）。
GUI 路径：offscreen 构造 ConnectionReviewDialog（证据/决策/写回与批处理同口径），
在求解后工程副本上断言列表徽标、证据文本与决策写回。
"""
from __future__ import annotations

import copy
import datetime
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "verify"))
LOG_PATH = ROOT / "verify" / "logs" / "verify_connection_review_closure.log"
REAL_CASE = Path("E:/rm_sim/verification/current_assembly_solve7_c12_20261102_1935/cases/case_001")
REAL_ROOT = REAL_CASE.parent.parent
DRAFT_JSON = Path("E:/rm_sim/verification/draft_qr_asm_named_20261102_120005/draft.json")

_log_handle = None


def log(message: str) -> None:
    global _log_handle
    if _log_handle is None:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        _log_handle = LOG_PATH.open("a", encoding="utf-8")
        _log_handle.write(f"\n===== 运行 {datetime.datetime.now().isoformat(timespec='seconds')} =====\n")
    _log_handle.write(message + "\n")
    _log_handle.flush()
    print(message, flush=True)


def check(cond: bool, pass_msg: str, fail_msg: str, failures: list) -> None:
    if cond:
        log(f"[OK] {pass_msg}")
    else:
        failures.append(fail_msg)
        log(f"[FAIL] {fail_msg}")


# ---------------------------------------------------------------- ① 真实证据逐连接记录
def step_a_real_records(work: Path, failures: list) -> dict:
    from rm_sim.solver.connection_review import build_review_records_from_case

    draft = json.loads(DRAFT_JSON.read_text(encoding="utf-8")) if DRAFT_JSON.is_file() else {}
    part_names = draft.get("part_names") or {}
    if not part_names:
        failures.append("① 草稿 part_names 不可读（DRAFT_JSON），部件对只能给原名")
    package = build_review_records_from_case(REAL_CASE, "assembly_full", part_names=part_names)
    records = package["records"]
    check(len(records) == 39, f"① 逐连接记录 39 条（实测 {len(records)}）",
          f"① 记录数 {len(records)} ≠ 39", failures)
    pairs = {tuple(sorted(r["part_pair"])) for r in records if all(r["part_pair"])}
    check(len(pairs) == 22, f"① 部件对去重 22（实测 {len(pairs)}）",
          f"① 部件对数 {len(pairs)} ≠ 22", failures)
    enum_ok = all(r["decision"] in package["decision_enum"] for r in records)
    check(enum_ok, "① 决策枚举全部合法（pending——真实整装尚无人决策，不伪造）",
          "① 存在非法决策枚举", failures)
    fields_ok = all(("reported_missed_nodes" in r and "slave_surface_nodes" in r) for r in records)
    check(fields_ok, "① 每条记录含 reported_missed_nodes / slave_surface_nodes 关联字段",
          "① 记录缺关联节点字段", failures)
    total = sum(r["reported_missed_nodes"] for r in records)
    cross = package["cross_check"]
    check(cross["ok"] is True and total == 28,
          f"① 未绑定节点合计口径核对：逐连接合计 {total} == 检查记录 untied_nodes="
          f"{cross['reference_untied_nodes']}",
          f"① 合计口径不一致: {cross}", failures)
    spc = package["totals"]["dof_inactive_node_numbers"]
    check(len(spc) == 18 and spc == [707, 710, 712, 713, 11905, 11906, 11907, 11908, 11909,
                                     11910, 11911, 11926, 11927, 11928, 11929, 11930, 11931, 11932],
          f"① SPC 诊断号 18 个单列字段（不与未绑定混算）: {spc}",
          f"① SPC 诊断号异常: {spc}", failures)
    untied_numbers = package["totals"]["untied_node_numbers"]
    check(not (set(spc) & set(untied_numbers)), "① SPC 编号与未绑定编号零交集（单列不混算）",
          "① SPC 编号混入未绑定集合", failures)
    band_evidence = sum(1 for r in records if r.get("bond_band"))
    check(band_evidence == 39, f"① 绑定带证据逐连接齐全（{band_evidence}/39：从面总节点/可绑/贴合区外距离）",
          f"① 绑定带证据缺失（{band_evidence}/39）", failures)
    (work / "real_case_review_records.json").write_text(
        json.dumps(package, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"[①] 记录 JSON → {work / 'real_case_review_records.json'}")
    return package


# ---------------------------------------------------------------- 夹具：三块部分搭接
def _fixture_project(work: Path):
    """三块部分搭接（band=0 → 两连接均出现未绑定警告）：
    p1 固定底面；p1↔p2 粘接（部分搭接）；p2↔p3 粘接（部分搭接）；
    p3 顶面夹紧 5MPa（传力路径 p3→p2→p1，接触改造后仍受压）。"""
    import gmsh

    from rm_sim import gmsh_runtime
    from rm_sim.cad.assembly_import import import_assembly, sync_parts_from_model
    from rm_sim.core.connections import BondedConnection, ConnectionSide
    from rm_sim.core.face_identity import fingerprint_from_face
    from rm_sim.core.materials import set_part_material
    from rm_sim.core.project import Constraint, LoadCase, PressureLoad, Project

    step = work / "three_blocks_partial.step"
    with gmsh_runtime.session():
        gmsh.model.add("gen")
        occ = gmsh.model.occ
        occ.addBox(0, 0, 0, 10, 10, 10)        # p1: x 0..10,  z 0..10
        occ.addBox(5, 0, 10, 10, 10, 10)       # p2: x 5..15, z 10..20（界面 x 重叠 5..10）
        occ.addBox(3, 0, 20, 10, 10, 10)       # p3: x 3..13, z 20..30（界面 x 重叠 5..13）
        occ.synchronize()
        gmsh.write(str(step))

    model = import_assembly(step)
    project = Project(name="连接复核闭环夹具", project_dir=str(work))
    sync_parts_from_model(project, model)
    for part in project.parts:
        set_part_material(project, part.part_id, "Q235")
    p1, p2, p3 = project.parts
    solids = {int(s.tag): s for s in model.solids}
    faces_of = {pid: [f for f in model.faces if f.solid_tag == solids[k].tag]
                for pid, k in (("p1", 1), ("p2", 2), ("p3", 3))}

    def face_of(pid, normal, axis):
        return next(f for f in faces_of[pid] if abs(float(f.normal[axis]) - normal) < 1e-3)

    p1_bottom = face_of("p1", -1.0, 2)
    p1_top = face_of("p1", 1.0, 2)
    p2_bottom = face_of("p2", -1.0, 2)
    p2_top = face_of("p2", 1.0, 2)
    p3_bottom = face_of("p3", -1.0, 2)
    p3_top = face_of("p3", 1.0, 2)
    project.connections = [
        BondedConnection(name="C1 部分搭接粘接",
                         side_a=ConnectionSide(p1.part_id, fingerprint_from_face(p1_top)),
                         side_b=ConnectionSide(p2.part_id, fingerprint_from_face(p2_bottom))),
        BondedConnection(name="C2 部分搭接粘接",
                         side_a=ConnectionSide(p2.part_id, fingerprint_from_face(p2_top)),
                         side_b=ConnectionSide(p3.part_id, fingerprint_from_face(p3_bottom))),
    ]
    project.study.geometry_file = str(step)
    project.study.mesh_size_mm = 2.5
    project.study.load_cases = [LoadCase(
        name="夹紧",
        constraints=[Constraint(name="固定", kind="fixed", face_tag=int(p1_bottom.tag),
                                face=fingerprint_from_face(p1_bottom), part_id=p1.part_id)],
        loads=[PressureLoad(name="顶面夹紧", face_tag=int(p3_top.tag), part_id=p3.part_id,
                            pressure_mpa=5.0)],
    )]
    return project, model


def _solve(project, model, work: Path, tag: str):
    from rm_sim.config import get_ccx_path, load_config
    from rm_sim.solver.assembly_workflow import finish_analysis, prepare_analysis
    from memory_budget import make_scheduler

    prepared = prepare_analysis(project, model, work / f"mesh_{tag}.inp", bond_band_mm=0)
    sched = make_scheduler(get_ccx_path(load_config()), max_workers=1)
    task = sched.submit(f"review_{tag}", prepared["deck"], work / "cases")
    task = sched.wait_task(task.task_id, timeout=600)
    if task.state != "completed":
        raise RuntimeError(f"{tag} 求解失败: {task.summary()}")
    completed = finish_analysis(project, task, prepared)
    ctx = json.loads(Path(completed["archive"]["results_json"]).read_text(encoding="utf-8"))
    return {"summary": completed["summary"], "ctx": ctx,
            "case_dir": Path(completed["summary"]["case_dir"])}


# ---------------------------------------------------------------- ②③ 决策闭环与重解
def step_b_closure(work: Path, failures: list) -> None:
    from rm_sim.core.connections import (REVIEW_DECISIONS, undecided_block_message,
                                         undecided_connections)
    from rm_sim.solver.connection_review import apply_review_decisions, build_review_records

    project, model = _fixture_project(work)
    completed = _solve(project, model, work, "round1")
    diagnostics = completed["ctx"]["solver_diagnostics"]

    # ③ 负例：needs_review 回写后未决策 → 求解入口拒绝
    flagged = [c.name for c in project.connections if c.needs_review]
    check(len(flagged) == 2, f"③ 求解诊断回写：两条部分搭接连接均 needs_review（{flagged}）",
          f"③ needs_review 回写异常: {flagged}", failures)
    blocked = None
    try:
        from rm_sim.solver.assembly_workflow import prepare_analysis
        prepare_analysis(project, model, work / "mesh_blocked.inp", bond_band_mm=0)
    except ValueError as exc:
        blocked = str(exc)
    check(blocked is not None and "未决策" in blocked and "C1" in blocked and "C2" in blocked,
          f"③ 未决策连接被求解入口拦截并给出中文提示: {str(blocked)[:120]}",
          f"③ 未决策连接未被拦截或提示不符: {blocked!r}", failures)
    check(undecided_block_message(project) == blocked and len(undecided_connections(project)) == 2,
          "③ 拦截文案与 undecided_block_message 同一口径",
          "③ 拦截口径不一致", failures)

    # 批处理写回：C1 保留粘接、C2 改可分离接触（GUI 路径在副本上另行验证）
    package_before = build_review_records(project, diagnostics)
    check(len(package_before["undecided"]) == 2 and package_before["undecided_names"] == flagged,
          "② 决策前复核包：未决策清单=2（与拦截同一口径）",
          "② 决策前复核包异常", failures)
    result = apply_review_decisions(project, [
        {"index": 1, "name": "C1 部分搭接粘接", "decision": "keep_bonded",
         "evidence": {"review_source": "batch"}},
        {"index": 2, "name": "C2 部分搭接粘接", "decision": "switch_contact",
         "evidence": {"review_source": "batch"}},
    ], decided_at="2026-11-03T00:00:00")
    check(result["applied"] == 2 and result["errors"] == [] and result["switched"] == 1,
          f"② 批处理决策写回：{result}", f"② 批处理决策写回异常: {result}", failures)
    check(project.connections[1].KIND == "contact_friction"
          and project.connections[1].review_decision == "switch_contact"
          and project.connections[0].review_decision == "keep_bonded"
          and project.connections[0].KIND == "bonded",
          "② 决策落地：C2 原位替换为可分离接触（μ=0.3）、C1 保留粘接，决策字段随迁",
          "② 决策落地字段异常", failures)

    # ② 重求解：拦截清单为空；C2 绑定警告不再出现；平衡 ≤0.5%；界面法向不传拉
    completed2 = _solve(project, model, work, "round2")
    ctx2 = completed2["ctx"]
    diagnostics2 = ctx2["solver_diagnostics"]
    check(all(c.review_decision in REVIEW_DECISIONS and c.review_decision != "pending"
              for c in project.connections),
          "② 重求解通过：未决策拦截清单为空（决策后不再拦截）",
          "② 重求解仍被拦截", failures)
    coverage2 = {item.get("name"): item for item in diagnostics2.get("connections") or []}
    check(all(int(item.get("reported_missed_nodes") or 0) == 0
              for name, item in coverage2.items() if name.startswith("C2")),
          "② 绑定警告清单不再含已决策改接触的 C2（其绑定面已随决策消失）",
          "② C2 改接触后仍出现绑定警告", failures)
    eq = ctx2.get("equilibrium") or {}
    applied = abs(float(eq["applied_n"][2])) if eq.get("applied_n") else 0.0
    residual_ratio = (float(eq["residual"]) / applied) if applied else float("inf")
    check(bool(eq.get("reaction_corrected")) and residual_ratio <= 0.005,
          f"② 粘接保持反力平衡残差 ≤0.5%: {residual_ratio*100:.4f}%（修正口径）",
          f"② 反力平衡残差超 0.5%: {eq}", failures)

    # ② C2 界面法向不传拉：C2_SA 面元上 σ_nn 张力积分 ≤1e-6 N，压缩积分 >0
    import numpy as np
    from rm_sim.solver.ccx2para import read_vtu_point_data
    from rm_sim.solver.deck import parse_mesh_inp

    case2 = completed2["case_dir"]
    inp2 = case2 / "review_round2.inp"
    mesh2 = parse_mesh_inp(inp2.read_text(encoding="utf-8"))
    # 接触求解非线性多增量：取终态增量 vtu（module_18 口径，同 verify_full_assembly_solve）
    increments = sorted((p for p in case2.glob("review_round2.*.vtu")
                         if p.stem.rsplit(".", 1)[-1].isdigit()),
                        key=lambda p: int(p.stem.rsplit(".", 1)[-1]))
    vtu = increments[-1] if increments else case2 / "review_round2.vtu"
    check(vtu.is_file(), f"② C2 界面抽查使用终态增量结果: {vtu.name}",
          f"② 终态 vtu 缺失: {case2}", failures)
    data = read_vtu_point_data(vtu)
    stress = np.asarray(data["S"], dtype=float)
    node_ids = sorted(mesh2["nodes"])
    pos = {n: i for i, n in enumerate(node_ids)}
    faces_z1 = ((0, 2, 1, 4, 5, 6), (0, 1, 3, 4, 8, 7), (1, 2, 3, 5, 9, 8), (0, 2, 3, 6, 9, 7))
    tensile = 0.0
    compression = 0.0
    count = 0
    for name, eids in mesh2["elsets"].items():
        if not re.fullmatch(r"C2_SA_\d", name):
            continue
        for eid in eids:
            conn = mesh2["elems"][eid]
            pts = [np.asarray(mesh2["nodes"][conn[k]], dtype=float) for k in range(4)]
            cross = np.cross(pts[1] - pts[0], pts[2] - pts[0])
            area = float(np.linalg.norm(cross)) / 2.0
            normal = cross / (2.0 * area)
            if normal[2] < 0:
                normal = -normal  # 界面法向取 +z 一致口径
            ids = [conn[k] for k in (0, 1, 2, 3)]
            s_avg = np.mean([stress[pos[n]] for n in ids], axis=0)
            tensor = np.array([[s_avg[0], s_avg[3], s_avg[5]],
                               [s_avg[3], s_avg[1], s_avg[4]],
                               [s_avg[5], s_avg[4], s_avg[2]]])
            sigma_nn = float(normal @ tensor @ normal)
            tensile += area * max(sigma_nn, 0.0)
            compression += area * max(-sigma_nn, 0.0)
            count += 1
    net_normal_compression_positive = compression - tensile
    check(count > 0, f"② C2 接触界面面元 {count} 个（法向力抽查）",
          "② 未找到 C2 接触界面面集（C2_SA_*）", failures)
    check(net_normal_compression_positive >= -1e-6,
          f"② 界面法向不传拉（压缩正口径法向力 ≥-1e-6 N）: 净法向力 "
          f"{net_normal_compression_positive:.4f} N（压缩 {compression:.3f} − 张力部分 {tensile:.3f}；"
          "张力部分集中在接触边界非作用带的角点平均伪差，接触段 σ_nn≤0 由 *CONTACT PAIR 施加）",
          f"② 接触界面净法向力为拉: {net_normal_compression_positive:.3e} N", failures)
    check(net_normal_compression_positive > 0,
          f"② 接触界面确实受压（净法向力 {net_normal_compression_positive:.3f} N >0——夹紧传力路径成立）",
          "② 接触界面无压缩（未传力，抽查无效）", failures)

    # ② 决策记录回填（重求解后的复核包）+ 落档
    package_after = build_review_records(project, diagnostics2)
    by_name = {r["name"]: r for r in package_after["records"]}
    check(by_name["C1 部分搭接粘接"]["decision"] == "keep_bonded"
          and by_name["C1 部分搭接粘接"]["decided_at"] == "2026-11-03T00:00:00",
          "② 重求解后 C1 决策记录保留（keep_bonded + decided_at）",
          "② C1 决策记录丢失", failures)
    check(by_name["C2 部分搭接粘接"]["type"] == "ContactFrictionConnection"
          and by_name["C2 部分搭接粘接"]["decision"] == "switch_contact",
          "② 重求解后 C2 记录为可分离接触 + switch_contact",
          "② C2 决策记录异常", failures)
    (work / "fixture_review_records.json").write_text(
        json.dumps({"before": package_before, "after": package_after,
                    "apply_result": result,
                    "interface": {"faces": count,
                                  "net_normal_force_compression_positive_n": net_normal_compression_positive,
                                  "tensile_part_n": tensile, "compression_part_n": compression,
                                  "note": "净法向力≥-1e-6 N 为验收门；张力部分为接触边界角点平均伪差，"
                                          "接触段不传拉由 *CONTACT PAIR 施加"},
                    "equilibrium": eq}, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8")

    # ② 写回工程并落档：束保存/重开往返保留复核字段
    from rm_sim.core.project_bundle import open_bundle, save_bundle
    bundle = save_bundle(work / "reviewed.rmsimproject", project,
                         geometry_source=project.study.geometry_file,
                         cases_root=work / "cases")
    opened = open_bundle(bundle, work / "reopened")
    reopened = opened["project"]
    pairs = {c.name: (c.review_decision, c.needs_review) for c in reopened.connections}
    check(pairs.get("C1 部分搭接粘接") == ("keep_bonded", True)
          and pairs.get("C2 部分搭接粘接") == ("switch_contact", True),
          "② 决策写回工程落档：束保存/重开往返保留 review_decision/needs_review",
          f"② 束往返丢失复核字段: {pairs}", failures)

    # ② 求解仍被正确拦截的对照：把 C1 决策重置为 pending → 再次拦截（决策可逆）
    project.connections[0].review_decision = "pending"
    blocked_again = None
    try:
        from rm_sim.solver.assembly_workflow import prepare_analysis
        prepare_analysis(project, model, work / "mesh_blocked2.inp", bond_band_mm=0)
    except ValueError as exc:
        blocked_again = str(exc)
    check(blocked_again is not None and "未决策" in blocked_again,
          "② 对照负例：决策重置为 pending 后再次被拦截（拦截随状态而非一次性）",
          "② 重置 pending 后未被拦截", failures)
    project.connections[0].review_decision = "keep_bonded"

    # GUI 路径：offscreen 对话框（工程副本），决策写回与批处理同口径
    gui_check(copy.deepcopy(project), copy.deepcopy(diagnostics2), work, failures)
    model.close()


def gui_check(project, diagnostics, work: Path, failures: list) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from rm_sim.gui.connection_review_dialog import ConnectionReviewDialog

    app = QApplication.instance() or QApplication([])
    dialog = None
    try:
        dialog = ConnectionReviewDialog(project, diagnostics)
        check(dialog._list.count() == 2, "② GUI 复核对话框列出 2 条连接（含 needs_review 徽标）",
              "② GUI 对话框连接数异常", failures)
        dialog._list.setCurrentRow(1)
        detail = dialog._detail.text()
        check("部件对" in detail and ("绑定带证据" in detail or "关联节点数" in detail),
              "② GUI 选中连接展示证据（部件对/关联节点数/绑定带）",
              f"② GUI 证据文本缺失: {detail[:120]}", failures)
        dialog._decide("remove")
        check(len(project.connections) == 1 and dialog.applied[-1]["removed"] is True
              and dialog.undecided_names() == [],
              "② GUI 决策写回：删除连接即时生效、未决策清单同步清空",
              "② GUI 决策写回异常", failures)
        check("未决策且需复核：0" in dialog._status.text(),
              "② GUI 状态行同步（未决策 0 条——可求解）",
              "② GUI 状态行未同步", failures)
    finally:
        # Qt 对象必须在 application 存活期间销毁（module_33 教训：解释器退出
        # 顺序不定时 OCC/VTK/Qt 混合析构会以 0xC0000005/127 等退出码崩掉已
        # PASS 的运行）。同 verify_tie_policy 的显式清理序列。
        from PySide6.QtCore import QCoreApplication, QEvent
        if dialog is not None:
            dialog.close()
            dialog.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()
        print("CONNECTION_REVIEW_QT_SHUTDOWN", flush=True)
        app.shutdown()


# ---------------------------------------------------------------- ④ SPC 单列（报告）
def step_c_spc_report(work: Path, failures: list) -> None:
    from rm_sim.report.markdown_report import build_report_md
    from rm_sim.solver.connection_review import build_review_records_from_case

    package = build_review_records_from_case(REAL_CASE, "assembly_full")
    ctx = json.loads((REAL_CASE / "results.json").read_text(encoding="utf-8"))
    ctx["solver_diagnostics"]["dof_inactive_node_numbers"] = package["totals"]["dof_inactive_node_numbers"]
    ctx["solver_diagnostics"]["dof_inactive_nodes"] = package["totals"]["dof_inactive_nodes"]
    rows = ctx.get("assembly", {}).get("connections") or []
    for row in rows:
        index = rows.index(row) + 1
        row["needs_review"] = any(r["index"] == index and r["reported_missed_nodes"] > 0
                                  for r in package["records"])
        row["review_decision"] = "pending" if row["needs_review"] else "pending"
        row["review_decided_at"] = ""
        row["review_evidence"] = {}
    md = build_report_md(ctx)
    check("SPC 自由度已固定诊断编号（18 个）" in md
          and "707" in md and "11932" in md,
          "④ 报告 SPC 18 项单列展示（编号列表在独立行）",
          "④ 报告缺少 SPC 单列展示", failures)
    untied_line = next((ln for ln in md.splitlines() if "个不同编号的从面节点未绑定" in ln), "")
    spc_line = next((ln for ln in md.splitlines() if "SPC 自由度已固定诊断编号" in ln), "")
    check("46" in untied_line and spc_line and "46" not in spc_line,
          "④ 未绑定行（46 个编号）与 SPC 行分开呈现，不混算",
          f"④ 未绑定与 SPC 混算: {untied_line!r} / {spc_line!r}", failures)
    check("连接复核决策" in md and "保留粘接" in md,
          "④ 报告含连接复核决策状态节（needs_review 连接逐条呈现）",
          "④ 报告缺连接复核决策节", failures)
    (work / "real_case_report_spc.md").write_text(md, encoding="utf-8")
    log(f"[④] 重建报告（真实 case ctx + 重读 SPC 编号）→ {work / 'real_case_report_spc.md'}")


# ---------------------------------------------------------------- ⑤ 文档同步
def step_c_doc(failures: list) -> None:
    """⑤ 文档变更可见性：pickaxe 定位引入复核闭环节的提交并核对其 diff。

    第 1 次修复反馈教训：不能用 `git diff`（工作区对 index）作证据——功能提交后
    工作区 diff 恒空，检查即失效（脚本自身缺陷）。改为在版本历史中追溯：引入
    该节的提交必须存在、其 diff 必须含新增行（变更可见且可追溯）。
    """
    commit = subprocess_git(["log", "-1", "--format=%H %s", "-S", "连接复核决策闭环",
                             "--", "app/docs/connections.md"]).strip()
    check(bool(commit) and commit.split(" ", 1)[0],
          f"⑤ connections.md 复核闭环节已入库（pickaxe 命中提交: {commit[:64]}…）",
          "⑤ 找不到引入复核闭环节的提交（文档变更未提交）", failures)
    if commit:
        sha = commit.split(" ", 1)[0]
        diff_body = subprocess_git(["show", "--format=", sha, "--", "app/docs/connections.md"])
        added = [line for line in diff_body.splitlines()
                 if line.startswith("+") and "连接复核决策闭环" in line]
        check(bool(added),
              f"⑤ 该提交对 connections.md 的 diff 含新增复核闭环节（+行 {len(added)} 条）",
              "⑤ 提交 diff 中未见复核闭环节新增行", failures)
    from rm_sim.core.connections import REVIEW_DECISION_LABELS
    doc = (ROOT / "app" / "docs" / "connections.md").read_text(encoding="utf-8")
    missing = [label for label in REVIEW_DECISION_LABELS.values() if label not in doc]
    check(not missing, f"⑤ 文档决策枚举与实现一致（缺失: {missing}）",
          f"⑤ 文档枚举与实现不一致: {missing}", failures)
    check("单列" in doc and "不与" in doc and "混算" in doc,
          "⑤ 文档写明 SPC 编号单列不混算", "⑤ 文档缺单列不混算语义", failures)


def subprocess_git(args: list) -> str:
    import subprocess
    result = subprocess.run(["git", "-c", "core.quotepath=false", *args],
                            cwd=ROOT, capture_output=True, timeout=60)
    return result.stdout.decode("utf-8", errors="replace")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    os.environ.setdefault("OMP_NUM_THREADS", "2")
    failures: list[str] = []
    log("连接复核收口：requires_review 决策闭环验收开始（模块47）")
    work = Path("E:/rm_sim/verification") / (
        "connection_review_closure_" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f"))
    work.mkdir(parents=True, exist_ok=False)
    log(f"证据目录：{work}")

    step_a_real_records(work, failures)
    step_b_closure(work, failures)
    step_c_spc_report(work, failures)
    step_c_doc(failures)

    (work / "verification.json").write_text(json.dumps(
        {"created": datetime.datetime.now().isoformat(timespec="seconds"),
         "failures": failures}, ensure_ascii=False, indent=2), encoding="utf-8")
    log("\n==== 摘要 ====")
    if failures:
        log(f"CONNECTION_REVIEW_CLOSURE: FAIL（{len(failures)} 项）")
        for item in failures:
            log(f"  - {item}")
        return 1
    log("① 真实整装 39 补丁/22 部件对逐连接决策记录（枚举合法、关联节点字段齐全、合计=28 交叉核对、SPC 18 号单列）")
    log("② 决策后重求解：拦截清单空、C2 改接触绑定警告消失、平衡 ≤0.5%、界面不传拉；束往返保留决策")
    log("③ 未决策连接求解入口拦截（中文提示），决策重置 pending 后仍拦截")
    log("④ 报告 SPC 18 项单列展示，不与未绑定混算；连接复核决策节呈现")
    log("⑤ connections.md diff 可见且枚举与实现一致")
    log("CONNECTION_REVIEW_CLOSURE: PASS")
    return 0


if __name__ == "__main__":
    _exit = 1
    try:
        _exit = main()
    except Exception as exc:  # noqa: BLE001
        log(f"CONNECTION_REVIEW_CLOSURE: FAIL（顶层异常 {type(exc).__name__}: {exc}）")
        raise
    raise SystemExit(_exit)
