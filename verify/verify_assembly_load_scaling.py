# -*- coding: utf-8 -*-
"""验收脚本：整装外载工况补账（模块48）。exit 0 = 通过；日志 → verify/logs/。

背景：快拆整装（20 实体，133810 单元）此前仅自重工况（总重 2.177 N——模型
自重；任务书材料）。本验收补两组外载工况并在同一网格上验证载荷线性律与总
平衡，结果写入历史并可被 history_restore 机制读回：
- ① 同一整装模型 1g/3g/5g 重力倍率（同一 prepare 网格，仅换 GravityLoad g）；
- ② ≈2 kg 部件外载假设工况：100 N（≈2kg×5g，任务书口径）沿 -X 施加于
  NEW快拆壳体侧面 + 1g 自重——矢量叠加。

模型配方与 probes/quick_release_full_assembly_static.py 一致（续作）：
NEW壳体底面 -Y 理想固定（假设安装面）、6061 参考弹性数据/销钉轴承钢假设、
连接草稿按公共面积 ≥1mm² 过滤（39 补丁/22 部件对，拓扑覆盖 20/20）。

覆盖验收标准：
① 1g/3g/5g 最大位移比 = 1:3:5（两两相对偏差 ≤1%，同网格线性静力）；
   接地反力合力（修正口径）= 总重 × 倍率（残差 ≤0.5%）；
② 100 N 工况：接地反力合力 = 外载向量 + 自重向量（残差 ≤0.5%）；
③ 四个工况 case 目录、报告 md、历史记录（project.cases 索引）存在，
   load_archived_case（verify_history_restore 机制）逐工况读回且 max|U| 与
   归档记录一致；束保存/重开后 5g 工况仍可读回（持久化）；
④ 全部门数值写入 verification.json 落证据目录。
诚实边界：连接复核——1g 求解后诊断把出现未绑定节点的连接回写 needs_review
（模块47）；本验收不代替决策，重解通过直接复用同一 deck 网格（不经
prepare），工程保存后这些连接保持待决策状态（下次 prepare 会被拦截——按设计）。
"""
from __future__ import annotations

import datetime
import json
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "verify"))
LOG_PATH = ROOT / "verify" / "logs" / "verify_assembly_load_scaling.log"

STEP = Path("E:/rm_sim/verification/quick_release_assembly_20261102_1020/assembly_thread_partition.step")
PARTITION = STEP.parent / "partition.json"
DRAFT = Path("E:/rm_sim/verification/draft_qr_asm_named_20261102_120005/draft.json")
G0 = 9810.0

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


def build_model(work: Path):
    """整装模型 + 工程 + 39 补丁连接 + NEW壳体固定 + 外载面选择（原探针配方）。"""
    from dataclasses import replace

    from rm_sim.cad.assembly_import import import_assembly, sync_parts_from_model
    from rm_sim.config import get_ccx_path, load_config
    from rm_sim.core.connections import apply_connection_draft
    from rm_sim.core.face_identity import fingerprint_from_face
    from rm_sim.core.materials import part_material
    from rm_sim.core.project import Constraint, Project

    sha_before = None
    model = import_assembly(STEP)
    if PARTITION.is_file():
        data = json.loads(PARTITION.read_text(encoding="utf-8"))
        names = {int(row["tag"]): str(row["name"]) for row in data.get("part_mapping", [])}
        model.parts = tuple(
            replace(part, name=names.get(int(part.tag), part.name)) for part in model.parts)
    project = Project(name="整装外载工况补账", project_dir=str(work))
    sync_parts_from_model(project, model)
    solids = list(model.solids)
    for part in project.parts:
        part.name = model.parts[int(part.solid_index)].name
    materials = {}
    from rm_sim.core.project import Material
    for part in project.parts:
        steel = any(word in (part.name or "") for word in ("销钉", "轴承"))
        material = (Material(name="钢（假设弹性数据）", e_modulus_mpa=210000.0, poisson=0.3,
                             density_t_mm3=7.85e-9)
                    if steel else
                    Material(name="6061（参考弹性数据）", e_modulus_mpa=68900.0, poisson=0.33,
                             density_t_mm3=2.7e-9))
        part.material_name = material.name
        part.material_snapshot = material  # 快照材料不在库内（原探针配方：参考/假设数据）
        part.material_confirmed = True
        materials[part.part_id] = part.material_name
    draft = json.loads(DRAFT.read_text(encoding="utf-8"))
    kept = [c for c in draft["candidates"] if float(c["common_area_mm2"]) >= 1.0]
    draft["candidates"] = kept
    touched = {p for c in kept for p in (c["part_a"], c["part_b"])}
    applied = apply_connection_draft(project, model, draft)
    housing_ids = [p.part_id for p in project.parts if "NEW快拆" in (p.name or "")]
    constraints = []
    for part in project.parts:
        if part.part_id not in housing_ids:
            continue
        solid_tag = int(solids[int(part.solid_index)].tag)
        faces = [f for f in model.faces
                 if f.solid_tag == solid_tag and f.normal[1] < -0.999999]
        bottom = min(float(f.center[1]) for f in faces)
        for face in (f for f in faces if abs(float(f.center[1]) - bottom) < 1e-5):
            constraints.append(
                Constraint(name=f"壳体底面理想固定（假设）-{part.name}", kind="fixed",
                           face_tag=int(face.tag), face=fingerprint_from_face(face),
                           part_id=part.part_id))
    # 外载面：NEW壳体部件上 |nx| 最大且面积 ≥100mm² 的侧面（假设工况作用面）
    candidates = []
    for part in project.parts:
        if part.part_id not in housing_ids:
            continue
        solid_tag = int(solids[int(part.solid_index)].tag)
        for face in model.faces:
            if face.solid_tag != solid_tag or abs(float(face.normal[0])) < 0.999:
                continue
            if float(face.area_mm2) >= 100.0:
                candidates.append((float(face.area_mm2), part, face))
    candidates.sort(key=lambda row: -row[0])
    ext_area, ext_part, ext_face = candidates[0]
    ext_face_fp = fingerprint_from_face(ext_face)

    expected_weight = 0.0
    for part in project.parts:
        solid = solids[int(part.solid_index)]
        density = float(part_material(project, part).density_t_mm3)
        expected_weight += float(solid.volume_mm3) * density * G0
    return {"model": model, "project": project, "materials": materials,
            "connections": applied, "constraints": constraints,
            "expected_weight_n": expected_weight, "ext_face": ext_face,
            "ext_face_fp": ext_face_fp, "ext_part": ext_part,
            "ext_area_mm2": ext_area}


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    import os
    os.environ.setdefault("OMP_NUM_THREADS", "2")
    failures: list[str] = []
    log("整装外载工况补账验收开始（模块48）")
    work = Path("E:/rm_sim/verification") / (
        "assembly_load_scaling_" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f"))
    work.mkdir(parents=True, exist_ok=False)
    log(f"证据目录：{work}")

    from rm_sim.cad.assembly_import import import_assembly
    from rm_sim.config import get_ccx_path, load_config
    from rm_sim.core.connections import undecided_block_message
    from rm_sim.core.face_identity import fingerprint_from_face
    from rm_sim.core.project import GravityLoad, LoadCase, TractionLoad
    from rm_sim.solver.assembly_workflow import finish_analysis, prepare_analysis
    from memory_budget import make_scheduler

    fx = build_model(work)
    project, model = fx["project"], fx["model"]
    check(len(project.connections) == 39,
          f"整装连接 39 补丁（面积 ≥1mm² 过滤，实测 {len(project.connections)}）",
          f"整装连接数 {len(project.connections)} ≠ 39", failures)
    log(f"材料：{fx['materials']}")
    log(f"总重（CAD 体积×密度×1g）= {fx['expected_weight_n']:.6f} N")

    # 首次 prepare 需要有效工况（Study 默认带一个空 LoadCase → 固定面校验失败）：
    # 预置 1g 自重 + 100N 外载面工况（原探针配方 + 本脚本首跑实错修正）——网格 NSET
    # 只为 prepare 时工况里的约束/非重力载荷面生成，ext100 工况建 deck 时
    # _nset_for_tag（deck.py:139）会拒绝无 NSET 的挂接面 occ452（首跑 DeckError 实证）。
    # 后续倍率工况在循环中按同一约束换载重建 deck，1g/3g/5g 的 deck 不引用该 NSET
    # （多定义一个未引用 NSET 对 ccx 无害）。
    project.study.load_cases = [LoadCase(name="整装准备工况（1g+100N 外载面）",
                                         constraints=fx["constraints"],
                                         loads=[GravityLoad(name="自重", direction=(0.0, -1.0, 0.0),
                                                            g_mm_s2=G0),
                                                TractionLoad(name="外载100N（≈2kg×5g 假设）",
                                                             face_tag=int(fx["ext_face"].tag),
                                                             part_id=fx["ext_part"].part_id,
                                                             face=fx["ext_face_fp"], total_force_n=100.0,
                                                             direction=(-1.0, 0.0, 0.0))])]
    prepared = prepare_analysis(project, model, work / "mesh.inp", size=4.0,
                                auto_mesh=True,
                                mesh_kwargs=dict(curvature=8.0, min_size_mm=0.4,
                                                 high_order_optimize=True))
    mesh_text = Path(project.study.mesh_file).read_text(encoding="utf-8")
    stats = prepared["mesh_result"].stats
    log(f"网格：{stats.element_count} 单元 / {stats.node_count} 节点，"
        f"负 Jacobian={stats.negative_jacobian_count}，minSICN={stats.min_sicn:.4g}")
    check(stats.negative_jacobian_count == 0, "网格 0 负 Jacobian",
          "网格出现负 Jacobian", failures)

    constraints = fx["constraints"]
    cases = [
        {"tag": "1g", "loads": [GravityLoad(name="自重", direction=(0.0, -1.0, 0.0), g_mm_s2=G0)],
         "external": None},
        {"tag": "3g", "loads": [GravityLoad(name="自重", direction=(0.0, -1.0, 0.0), g_mm_s2=3 * G0)],
         "external": None},
        {"tag": "5g", "loads": [GravityLoad(name="自重", direction=(0.0, -1.0, 0.0), g_mm_s2=5 * G0)],
         "external": None},
        {"tag": "ext100", "loads": [GravityLoad(name="自重", direction=(0.0, -1.0, 0.0), g_mm_s2=G0),
                                    TractionLoad(name="外载100N（≈2kg×5g 假设）",
                                                 face_tag=int(fx["ext_face"].tag), part_id=fx["ext_part"].part_id,
                                                 face=fx["ext_face_fp"], total_force_n=100.0,
                                                 direction=(-1.0, 0.0, 0.0))],
         "external": {"vector_n": [-100.0, 0.0, 0.0], "face": int(fx["ext_face"].tag),
                      "part": fx["ext_part"].name, "area_mm2": fx["ext_area_mm2"]}},
    ]
    scheduler = make_scheduler(get_ccx_path(load_config()), max_workers=1, tier="large")
    records = []
    for case in cases:
        load_case = LoadCase(name=f"整装工况 {case['tag']}", constraints=constraints,
                             loads=case["loads"])
        project.study.load_cases = [load_case]
        from rm_sim.solver.deck import build_assembly_case_inp
        deck = build_assembly_case_inp(mesh_text, project, load_case, model,
                                       nset_face_tags=prepared["nset_faces"],
                                       mesh=prepared["mesh"],
                                       bond_band_mm=prepared["bond_band_mm"])
        task = scheduler.submit(f"assembly_{case['tag']}", deck, work / "cases")
        started = time.monotonic()
        task = scheduler.wait_task(task.task_id, timeout=1200)
        if task.state != "completed":
            failures.append(f"工况 {case['tag']} 求解失败: {task.summary()}")
            records.append({"tag": case["tag"], "state": task.state, "error": task.error})
            continue
        completed = finish_analysis(project, task, prepared)
        ctx = json.loads(Path(completed["archive"]["results_json"]).read_text(encoding="utf-8"))
        equilibrium = ctx.get("equilibrium") or {}
        summary = completed["summary"]
        rf = np.asarray(equilibrium.get("rf_sum_n", summary["rf_sum_n"]), dtype=float)
        applied = np.asarray(equilibrium.get("applied_n", [0, 0, 0]), dtype=float)
        residual = float(equilibrium.get("residual", float("inf")))
        applied_mag = float(np.linalg.norm(applied))
        record = {"tag": case["tag"], "job": task.job_name,
                  "case_dir": str(task.case_dir), "case_name": task.case_dir.name,
                  "max_u_mag_mm": float(summary["max_u_mag_mm"]),
                  "max_mises_mpa": float(summary["max_mises_mpa"]),
                  "rf_sum_n": rf.tolist(), "applied_n": applied.tolist(),
                  "applied_mag_n": applied_mag, "residual_n": residual,
                  "residual_ratio": residual / applied_mag if applied_mag else None,
                  "reaction_corrected": bool(equilibrium.get("reaction_corrected")),
                  "external": case["external"], "seconds": round(time.monotonic() - started, 1),
                  "report_md": summary.get("report_md")}
        records.append(record)
        log(f"[工况 {case['tag']}] max|U|={record['max_u_mag_mm']:.6g}mm "
            f"ΣRF(修正)={np.round(rf, 6).tolist()} N 残差比={record['residual_ratio']:.5f}")

    by_tag = {r["tag"]: r for r in records if "max_u_mag_mm" in r}
    # ---- ① 线性律 1:3:5 ----
    u1, u3, u5 = (by_tag[t]["max_u_mag_mm"] for t in ("1g", "3g", "5g"))
    dev35 = abs(u3 / u1 - 3.0) / 3.0
    dev51 = abs(u5 / u1 - 5.0) / 5.0
    dev53 = abs(u5 / u3 - 5.0 / 3.0) / (5.0 / 3.0)
    check(max(dev35, dev51, dev53) <= 0.01,
          f"① 最大位移比 1:3:5（同网格线性）：u={u1:.6g}/{u3:.6g}/{u5:.6g} mm，"
          f"两两偏差 {dev35:.2e}/{dev51:.2e}/{dev53:.2e} ≤1%",
          f"① 位移线性律偏差超 1%: {dev35:.3e}/{dev51:.3e}/{dev53:.3e}", failures)
    weight = fx["expected_weight_n"]
    for tag, k in (("1g", 1), ("3g", 3), ("5g", 5)):
        rf_y = by_tag[tag]["rf_sum_n"][1]
        ratio = abs(rf_y - weight * k) / (weight * k)
        check(ratio <= 0.005,
              f"① {tag} 接地反力合力 = 总重×{k}: ΣRFy={rf_y:.6f} N vs {weight * k:.6f} N"
              f"（残差 {ratio * 100:.4f}% ≤0.5%）",
              f"① {tag} 反力残差 {ratio * 100:.3f}% 超 0.5%", failures)
    # ---- ② 100N 矢量叠加 ----
    ext = np.asarray(by_tag["ext100"]["external"]["vector_n"], dtype=float)
    # 反力 = −(外载 + 自重)：外载 -X 100N → 反力 +X；自重 -Y → 反力 +Y。
    # （脚本首版把 X 分量写成 −100 是符号错误——已按平衡方程修正。）
    expected_total = -ext + np.asarray([0.0, weight, 0.0])
    rf_vec = np.asarray(by_tag["ext100"]["rf_sum_n"], dtype=float)
    vec_residual = float(np.linalg.norm(rf_vec - expected_total))
    vec_ratio = vec_residual / float(np.linalg.norm(expected_total))
    check(vec_ratio <= 0.005,
          f"② 100N 工况接地反力合力 = −(外载向量+自重向量): ΣRF={np.round(rf_vec, 6).tolist()} "
          f"vs 期望 {np.round(expected_total, 6).tolist()} N（残差 {vec_ratio * 100:.4f}% ≤0.5%）",
          f"② 100N 矢量平衡残差 {vec_ratio * 100:.3f}% 超 0.5%", failures)
    check(by_tag["ext100"]["residual_ratio"] is not None and by_tag["ext100"]["residual_ratio"] <= 0.005,
          f"② 100N 工况产品平衡残差 ≤0.5%: {by_tag['ext100']['residual_ratio'] * 100:.4f}%"
          "（equilibrium 修正口径）",
          f"② 100N 产品平衡残差超 0.5%: {by_tag['ext100']['residual_ratio']}", failures)
    check(by_tag["ext100"]["reaction_corrected"] is True,
          "② 反力为修正口径（原始 RF − 固定节点已施加载荷）",
          "② 反力口径未修正", failures)
    # ---- ③ 历史读回（verify_history_restore 机制） ----
    from rm_sim.solver.history import load_archived_case
    for record in records:
        raw = load_archived_case(project, record["case_name"])
        restored_u = float(raw["fields"].max_u_mag())
        check(abs(restored_u - record["max_u_mag_mm"]) <= 1e-9 + 1e-6 * abs(record["max_u_mag_mm"]),
              f"③ {record['tag']} 历史读回：load_archived_case max|U|={restored_u:.6g} "
              f"== 归档 {record['max_u_mag_mm']:.6g}",
              f"③ {record['tag']} 历史读回位移不一致", failures)
        case_dir = Path(record["case_dir"])
        check((case_dir / "report.md").is_file() and (case_dir / "results.json").is_file(),
              f"③ {record['tag']} case 目录/报告 md/结果 JSON 存在",
              f"③ {record['tag']} 归档文件缺失", failures)
    # 持久化：束保存/重开后 5g 仍可读回
    from rm_sim.core.project_bundle import open_bundle, save_bundle
    bundle = save_bundle(work / "assembly_loads.rmsimproject", project,
                         geometry_source=STEP, cases_root=work / "cases")
    opened = open_bundle(bundle, work / "reopened")
    reopened = opened["project"]
    raw5 = load_archived_case(reopened, by_tag["5g"]["case_name"])
    check(abs(float(raw5["fields"].max_u_mag()) - by_tag["5g"]["max_u_mag_mm"])
          <= 1e-9 + 1e-6 * abs(by_tag["5g"]["max_u_mag_mm"]),
          "③ 束保存/重开后 5g 工况仍可由 history 机制读回（持久化）",
          "③ 重开工程 5g 读回失败", failures)
    # ---- 连接复核状态（模块47 回写；不代替决策） ----
    # 自一致性口径：needs_review 连接集合与关联节点合计必须等于「本次运行自身」
    # 最终工况诊断的逐连接归属（同节点属多条连接时按归属逐连接计数）。
    # 不得硬编码 28——那是 DS c12 网格（235908 节点）的检查记录值；本脚本网格为
    # size=4.0/curvature=8（节点数不同→绑定带/未绑定集合不同），首跑实测 12 条
    # 连接/逐连接合计 54（唯一节点 42，SPC 18 仍单列）——硬编码 28 即为对错网格
    # 的过拟合（首跑 FAIL 实证），故改为与本运行 diagnostics 交叉核对。
    last_results = Path(by_tag["ext100"]["case_dir"]) / "results.json"
    diag = (json.loads(last_results.read_text(encoding="utf-8")).get("solver_diagnostics") or {})
    coverage = {item.get("name"): int(item.get("reported_missed_nodes") or 0)
                for item in diag.get("connections") or []}
    flagged = [c.name for c in project.connections if c.needs_review]
    flagged_sum = sum((c.review_evidence.get("last_solve") or {}).get("reported_missed_nodes", 0)
                      for c in project.connections if c.needs_review)
    flagged_from_diag = sorted(name for name, count in coverage.items() if count > 0)
    check(sorted(flagged) == flagged_from_diag,
          f"连接复核回写自一致：needs_review 连接集合 == 本次诊断 reported_missed_nodes>0 集合"
          f"（{len(flagged)} 条）",
          f"连接复核回写集合不一致: needs_review={sorted(flagged)} vs 诊断={flagged_from_diag}",
          failures)
    check(flagged_sum == sum(coverage.values()),
          f"连接复核回写：{len(flagged)} 条连接关联未绑定节点合计 {flagged_sum}"
          f" == 本次诊断逐连接归属合计 {sum(coverage.values())}"
          f"（唯一节点 {diag.get('untied_nodes')}、报告编号 {diag.get('reported_missed_nodes')}、"
          f"SPC {diag.get('dof_inactive_nodes')} 单列；同一节点属多条连接按归属逐连接计数）",
          f"连接复核回写合计不一致: 回写 {flagged_sum} vs 诊断 {sum(coverage.values())}", failures)
    check(undecided_block_message(project) is not None,
          "保存的工程含未决策复核项（产品按设计将在下次求解前拦截——本验收不代替决策）",
          "保存工程未出现待决策状态", failures)

    (work / "verification.json").write_text(json.dumps(
        {"created": datetime.datetime.now().isoformat(timespec="seconds"),
         "step": str(STEP), "mesh": {"elements": int(stats.element_count),
                                     "nodes": int(stats.node_count),
                                     "min_sicn": float(stats.min_sicn),
                                     "negative_jacobian": int(stats.negative_jacobian_count)},
         "expected_weight_n": weight,
         "cases": records,
         "linearity": {"u1": u1, "u3": u3, "u5": u5,
                       "dev_3g_vs_1g": dev35, "dev_5g_vs_1g": dev51, "dev_5g_vs_3g": dev53},
         "review_state": {"flagged_connections": flagged, "flagged_untied_sum": flagged_sum,
                          "last_case_untied_nodes": diag.get("untied_nodes"),
                          "last_case_reported_missed_nodes": diag.get("reported_missed_nodes"),
                          "last_case_dof_inactive_nodes": diag.get("dof_inactive_nodes"),
                          "note": "模块47 回写；决策留给使用者，后续 prepare 将拦截。"
                                  "未绑定节点数随网格不同而不同（DS c12 网格为 28，本网格见上），"
                                  "以本运行诊断自一致口径为准"},
         "failures": failures}, ensure_ascii=False, indent=2), encoding="utf-8")

    log("\n==== 摘要 ====")
    if failures:
        log(f"ASSEMBLY_LOAD_SCALING: FAIL（{len(failures)} 项）")
        for item in failures:
            log(f"  - {item}")
        return 1
    log("① 1g/3g/5g 位移比 1:3:5（≤1%）+ 反力=总重×倍率（≤0.5%）")
    log("② 100N 外载+自重矢量平衡 ≤0.5%（修正口径）")
    log("③ 四工况 case 目录/报告/历史存在，history 机制逐工况读回 + 束持久化读回")
    log("④ 全部门数值已落 verification.json")
    log("ASSEMBLY_LOAD_SCALING: PASS")
    return 0


if __name__ == "__main__":
    _exit = 1
    try:
        _exit = main()
    except Exception as exc:  # noqa: BLE001
        log(f"ASSEMBLY_LOAD_SCALING: FAIL（顶层异常 {type(exc).__name__}: {exc}）")
        raise
    raise SystemExit(_exit)
