# -*- coding: utf-8 -*-
"""验收脚本：装配连接草稿（自动识别配合面 → 连接对象 → 真实求解）。

覆盖（exit 0 = 全部通过；日志写 verify/logs/verify_connection_draft.log）：

① 草稿生成：串联双杆（assembly_serial_rods.step）两实体之间的贴合界面被自动
   识别为候选，公共面积 = 杆截面面积（OCC 求交，不是包围盒重叠）；
② 方向正确：界面两侧法向反向贴合，推荐类型为粘接（不含销/轴承关键词），
   slave 侧 = 面积较小的一侧；
③ 应用：connections_from_draft 生成 1 条 BondedConnection，validate 通过；
   重复应用 → 去重（added=0、skipped≥1），不会写出重复 *TIE；
④ 进 deck：prepare_analysis 通过与 face 指纹 re-resolve，deck 含 *TIE 卡；
⑤ 真求解：端部 Ux 与串联双材料理论 δ 误差 ≤2%（证明草稿连接真的传力）；
⑥ 判别力：同几何去掉全部连接 → deck 层连接性门显式拒绝（不允许把悬浮件
   交给求解器）。
"""
from __future__ import annotations

import logging
import sys
import tempfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))

LOG_PATH = ROOT / "verify" / "logs" / "verify_connection_draft.log"
SAMPLE = ROOT / "samples" / "assembly_serial_rods.step"

E_STEEL = 210000.0  # Q235
E_AL = 68900.0  # 6061

_lines: list[str] = []
_failures: list[str] = []


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


def _rod_geometry(model) -> list[dict]:
    """每个杆的 bbox 长度与截面积（从 OCC 实体量，不硬编码尺寸）。"""
    import gmsh

    rows = []
    for solid in model.solids:
        bb = gmsh.model.getBoundingBox(3, int(solid.tag))
        length = float(bb[3] - bb[0])
        area = float(solid.volume_mm3) / length if length > 0 else 0.0
        rows.append({"tag": int(solid.tag), "x0": float(bb[0]), "x1": float(bb[3]),
                     "length_mm": length, "area_mm2": area, "volume_mm3": float(solid.volume_mm3)})
    return rows


def step_a_draft(model, project) -> dict:
    """① 草稿生成 + ② 方向/推荐类型。"""
    from rm_sim.cad.connection_draft import choose_side, draft_assembly_connections

    rods = _rod_geometry(model)
    check(len(rods) == 2, f"① 样例为两实体（{len(rods)}）", f"① 样例实体数异常: {len(rods)}")
    draft = draft_assembly_connections(model, project, planar_only=False, min_area_mm2=0.01)
    log(f"  | 草稿: bodies={draft['body_count']} pairs={draft['pairs_considered']} "
        f"joints={draft['joint_pairs']} candidates={len(draft['candidates'])}")
    check(len(draft["candidates"]) >= 1,
          f"① 贴合界面被识别为连接候选（{len(draft['candidates'])} 条）",
          "① 未识别出任何配合面候选")
    if not draft["candidates"]:
        return draft

    top = max(draft["candidates"], key=lambda r: r["common_area_mm2"])
    iface_area = float(min(r["area_mm2"] for r in rods))
    err = abs(top["common_area_mm2"] - iface_area) / iface_area
    log(f"  | 最大候选公共面积 = {top['common_area_mm2']:.6f} mm² vs 杆截面 {iface_area:.6f} mm²"
        f"（差 {err*100:.4f}%）")
    check(err <= 0.01,
          f"① 公共面积 = 杆截面面积（OCC 求交，误差 {err*100:.4f}% ≤1%）",
          f"① 公共面积异常: {top['common_area_mm2']} vs {iface_area}")

    dotted = float(np.dot(np.asarray(top["normal_a"]), np.asarray(top["normal_b"])))
    norm = float(np.linalg.norm(np.asarray(top["normal_a"])) * np.linalg.norm(np.asarray(top["normal_b"])))
    cos = dotted / norm if norm else 0.0
    log(f"  | 界面法向: a={top['normal_a']} b={top['normal_b']} cos={cos:.6f}")
    check(abs(abs(cos) - 1.0) <= 1e-6,
          "② 界面两面法向平行反向（贴合面）",
          f"② 界面法向异常 cos={cos}")

    expected_slave, _ = choose_side(top["name_a"], top["name_b"],
                                    top["face_area_a_mm2"], top["face_area_b_mm2"])
    check(top["recommended"] == "bonded",
          "② 推荐类型 = 粘接（部件名不含销/轴承关键词）",
          f"② 推荐类型异常: {top['recommended']}")
    check(top["slave_side"] == expected_slave,
          f"② slave 侧取面积较小侧（{top['slave_side']}）",
          f"② slave 侧异常: {top['slave_side']} vs {expected_slave}")
    return draft


def step_b_apply(model, project, draft) -> None:
    """③ 应用 + 去重。"""
    from rm_sim.core.connections import apply_connection_draft, connections_from_draft

    built = connections_from_draft(project, model, draft)
    check(len(built["connections"]) >= 1 and not built["errors"],
          f"③ connections_from_draft 生成 {len(built['connections'])} 条连接，无错误",
          f"③ 连接构造失败: {len(built['connections'])} 条，errors={built['errors'][:2]}")
    for conn in built["connections"]:
        check(conn.KIND == "bonded",
              f"③ 连接类型 = 粘接（{conn.name}）",
              f"③ 连接类型异常: {conn.KIND}")

    first = apply_connection_draft(project, model, draft)
    check(first["added"] >= 1 and not first["errors"],
          f"③ 应用到工程：新增 {first['added']} 条，无错误",
          f"③ 应用失败: {first}")
    second = apply_connection_draft(project, model, draft)
    check(second["added"] == 0 and second["skipped"] >= 1,
          f"③ 重复应用去重：新增 {second['added']}、跳过 {second['skipped']}（不写重复 *TIE）",
          f"③ 去重失效: 新增 {second['added']}、跳过 {second['skipped']}")


def _setup_case(project, model) -> None:
    """约束 x=0 端面、载荷 x=L 端面 +x 1000N；两端面按几何法向识别。"""
    from rm_sim.core.face_identity import fingerprint_from_face
    from rm_sim.core.project import Constraint, LoadCase, TractionLoad

    p1 = project.parts[0]
    p2 = project.parts[1]
    f1 = [f for f in model.faces if f.solid_tag == int(model.solids[p1.solid_index].tag)]
    f2 = [f for f in model.faces if f.solid_tag == int(model.solids[p2.solid_index].tag)]
    fixed = min((f for f in f1 if abs(abs(f.normal[0]) - 1.0) <= 1e-9), key=lambda f: f.center[0])
    loaded = max((f for f in f2 if abs(abs(f.normal[0]) - 1.0) <= 1e-9), key=lambda f: f.center[0])
    project.study.load_cases = [LoadCase(
        name="拉伸",
        constraints=[Constraint(name="固定", kind="fixed", face_tag=int(fixed.tag),
                                face=fingerprint_from_face(fixed), part_id=p1.part_id)],
        loads=[TractionLoad(name="端拉", face_tag=int(loaded.tag),
                            face=fingerprint_from_face(loaded), part_id=p2.part_id,
                            total_force_n=1000.0, direction=(1.0, 0.0, 0.0))],
    )]


def step_c_solve(project, model, work: Path) -> None:
    """④ 进 deck + ⑤ 真实求解对 δ。"""
    from rm_sim.config import get_ccx_path, load_config
    from rm_sim.solver.assembly_workflow import prepare_analysis
    from rm_sim.solver.fields import build_result_fields
    from rm_sim.solver.tasks import STATE_COMPLETED, SolveScheduler

    _setup_case(project, model)
    project.parts[0].material_name = "Q235"
    project.parts[1].material_name = "6061"
    for part in project.parts:
        part.material_confirmed = True
    project.project_dir = str(work)
    prepared = prepare_analysis(project, model, work / "mesh.inp", size=2.0)
    deck = prepared["deck"]
    check("*TIE, NAME=C1_TIE" in deck,
          "④ deck 含草稿生成的 *TIE 卡",
          "④ deck 缺少 *TIE 卡")
    check(prepared["assembly"] and len(project.connections) >= 1,
          f"④ 工程连接数 = {len(project.connections)}（草稿应用生效）",
          "④ 工程连接列表为空")

    rods = _rod_geometry(model)
    delta = sum(1000.0 * r["length_mm"] / (E * r["area_mm2"])
                for r, E in zip(sorted(rods, key=lambda r: r["x0"]), (E_STEEL, E_AL)))
    log(f"  | 串联双材料理论 δ = {delta:.10f} mm（L={[round(r['length_mm'],4) for r in rods]}，"
        f"A={[round(r['area_mm2'],4) for r in rods]}）")

    scheduler = SolveScheduler(get_ccx_path(load_config()), max_workers=1)
    task = scheduler.submit("draft_gate", deck, work / "cases")
    task = scheduler.wait_task(task.task_id, timeout=1800)
    check(task.state == STATE_COMPLETED and task.job_finished and task.parse_ok,
          f"⑤ 求解完成（state={task.state}, parse_ok={task.parse_ok}）",
          f"⑤ 求解失败: {task.summary()}")

    fields = build_result_fields(sorted(prepared["mesh"]["nodes"]), task.results,
                                 task.frd_path, fixed_set=prepared["fixed_sets"])
    load_names = [k for k in prepared["nset_faces"] if k not in prepared["fixed_sets"]]
    node_ids = prepared["mesh"]["nsets"][load_names[0]]
    pos = {int(n): i for i, n in enumerate(fields.node_ids)}
    ux = float(np.mean([fields.u[pos[int(n)], 0] for n in node_ids]))
    err = abs(ux - delta) / delta
    log(f"  | 端部平均 Ux = {ux:.10f} mm vs 理论 {delta:.10f} mm（误差 {err*100:.4f}%）")
    check(err <= 0.02,
          f"⑤ 端部 Ux vs 串联双材料理论误差 ≤2%: 实测 {err*100:.4f}%",
          f"⑤ 端部 Ux 超差: {ux:.10f} vs {delta:.10f}")


def step_d_broken(project, model, work: Path) -> None:
    """⑥ 判别力：去掉全部连接 → 连接性门显式拒绝。"""
    from rm_sim.core.project import Project
    from rm_sim.cad.assembly_import import sync_parts_from_model
    from rm_sim.solver.assembly_workflow import prepare_analysis
    from rm_sim.solver.deck import DeckError

    empty = Project(name="断带负例", project_dir=str(work / "broken"))
    (work / "broken").mkdir(parents=True, exist_ok=True)
    sync_parts_from_model(empty, model)
    empty.parts[0].material_name = "Q235"
    empty.parts[1].material_name = "6061"
    _setup_case(empty, model)
    try:
        prepare_analysis(empty, model, work / "broken" / "mesh.inp", size=2.0)
        fail("⑥ 断带工程未被连接性门拒绝")
    except DeckError as exc:
        msg = str(exc)
        check("p2" in msg and ("连接性" in msg or "奇异" in msg),
              "⑥ 无连接 → DeckError 显式拒绝（指明悬浮部件与奇异原因）",
              f"⑥ DeckError 信息缺失: {msg[:160]}")
    except Exception as exc:  # noqa: BLE001
        fail(f"⑥ 断带抛出错误类型: {type(exc).__name__}: {exc}")


def main() -> int:
    rm_sim_logger = logging.getLogger("rm_sim")
    rm_sim_logger.setLevel(logging.INFO)
    rm_sim_logger.propagate = False

    class _LinesHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            _lines.append("  | [log] " + record.getMessage())

    rm_sim_logger.addHandler(_LinesHandler())

    log("=" * 76)
    log("verify_connection_draft 开始（连接草稿 → 连接对象 → 真实 ccx 求解）")
    log(f"python = {sys.executable}")
    check("rm_sim" in sys.executable.lower() and "venv" in sys.executable.lower(),
          f"验收运行于 venv 解释器: {sys.executable}",
          f"应使用 E:/rm_sim/venv/Scripts/python.exe 运行，实际: {sys.executable}")
    log("=" * 76)

    from rm_sim.cad.assembly_import import import_assembly, sync_parts_from_model
    from rm_sim.core.project import Project

    tmp = Path(tempfile.mkdtemp(prefix="rm_sim_verify_draft_"))
    model = None
    try:
        model = import_assembly(SAMPLE)
        project = Project(name="连接草稿验证", project_dir=str(tmp / "proj"))
        (tmp / "proj").mkdir(parents=True, exist_ok=True)
        sync_parts_from_model(project, model)

        log("\n----- 步骤 A①② 草稿生成 -----")
        draft: dict = {}
        try:
            draft = step_a_draft(model, project)
        except Exception as exc:  # noqa: BLE001
            fail(f"步骤 A 异常: {type(exc).__name__}: {exc}")

        log("\n----- 步骤 B③ 应用与去重 -----")
        try:
            if draft:
                step_b_apply(model, project, draft)
            else:
                fail("步骤 B 跳过（草稿为空）")
        except Exception as exc:  # noqa: BLE001
            fail(f"步骤 B 异常: {type(exc).__name__}: {exc}")

        log("\n----- 步骤 C④⑤ deck 与真实求解 -----")
        try:
            if draft:
                step_c_solve(project, model, tmp / "proj")
            else:
                fail("步骤 C 跳过（草稿为空）")
        except Exception as exc:  # noqa: BLE001
            fail(f"步骤 C 异常: {type(exc).__name__}: {exc}")

        log("\n----- 步骤 D⑥ 断带等效负例 -----")
        try:
            step_d_broken(project, model, tmp)
        except Exception as exc:  # noqa: BLE001
            fail(f"步骤 D 异常: {type(exc).__name__}: {exc}")
    finally:
        import shutil

        for obj in (model,):
            try:
                if obj is not None:
                    obj.close()
            except Exception:  # noqa: BLE001
                pass
        shutil.rmtree(tmp, ignore_errors=True)  # 只清理本次创建的临时目录

    log("\n" + "=" * 76)
    if _failures:
        log(f"VERIFY_CONNECTION_DRAFT: FAIL（{len(_failures)} 项失败）")
        for item in _failures:
            log(f"  [失败] {item}")
        return 1
    log("VERIFY_CONNECTION_DRAFT: PASS")
    return 0


if __name__ == "__main__":
    _exit_code = 1
    try:
        _exit_code = main()
    except Exception as exc:  # noqa: BLE001
        _failures.append(f"验收脚本顶层异常: {type(exc).__name__}: {exc}")
        log(f"VERIFY_CONNECTION_DRAFT: FAIL（顶层异常 {type(exc).__name__}: {exc}）")
    finally:
        content = "\n".join(_lines) + "\n"
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_PATH, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"[verify] 日志已写入 {LOG_PATH}")
    sys.exit(_exit_code)
