# -*- coding: utf-8 -*-
"""验收脚本：装配与部件结果仪表收口（模块49）。exit 0 = 通过；日志 → verify/logs/。

功能：『装配与部件结果』对话框升级为部件仪表卡片——每部件 max Mises [MPa] /
max |U| [mm] / 质量 [kg] / 材料名 + 整装合成卡片，数值与 solver.fields 的
part_field_views 同源；开发者术语出清（人话口径说明 + 『打开诊断』按钮）。
API 兼容（B4）：保留 AssemblyResultDialog._table/_combo，行1列1 仍为
max |U| 数值——verify_gui_assembly.py:84 门语义不变。

覆盖验收标准：
① 两块装配真实求解产物载入后，每部件卡片/明细表数值与 part_field_views
   同源数组重算一致（|diff|≤1e-9 相对）；
② 卡片单位齐全（MPa/mm/kg）且质量=ρ×体积手算对照 ≤0.5%；
③ 无结果时显示『未计算』且不出现任何数值（红线）；
④ 对话框全部可见文本不含开发者术语（正则断言：ELSET/solver.fields/
   part_field_views/.py/盘符路径），并含『口径见计算报告』说明；
⑤ 截图落证据目录；
⑥ 回归保护：verify_gui_assembly.py 复跑 exit 0（原语义，B4 未修订）。
"""
from __future__ import annotations

import datetime
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "verify"))
sys.path.insert(0, str(ROOT / "app"))
os.environ.setdefault("OMP_NUM_THREADS", "2")

LOG_PATH = ROOT / "verify" / "logs" / "verify_assembly_dashboard.log"
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


def collect_visible_texts(dialog) -> list[str]:
    """对话框全部可见文本：labels/buttons/表格项/下拉项/标题。"""
    texts = [dialog.windowTitle()]
    from PySide6.QtWidgets import QLabel, QPushButton
    for label in dialog.findChildren(QLabel):
        if label.isVisible() or True:  # offscreen 下 isVisible 可能为 False，文本一并纳入
            texts.append(label.text())
    for button in dialog.findChildren(QPushButton):
        texts.append(button.text())
    table = dialog._table
    for row in range(table.rowCount()):
        for col in range(table.columnCount()):
            item = table.item(row, col)
            if item is not None:
                texts.append(item.text())
    for index in range(dialog._combo.count()):
        texts.append(dialog._combo.itemText(index))
    for name, card in getattr(dialog, "_cards", {}).items():
        texts.append(name)
        for label in card.values():
            texts.append(label.text())
    return texts


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    failures: list[str] = []
    log("装配与部件结果仪表收口验收开始（模块49）")

    from assembly_check_utils import evidence_dir
    work = evidence_dir("assembly_dashboard")
    log(f"证据目录：{work}")

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])

    # ---- 真实求解两块装配（产品链路）----
    import gmsh

    from rm_sim import gmsh_runtime
    from rm_sim.cad.assembly_import import import_assembly, sync_parts_from_model
    from rm_sim.config import get_ccx_path, load_config
    from rm_sim.core.connections import BondedConnection, ConnectionSide
    from rm_sim.core.face_identity import fingerprint_from_face
    from rm_sim.core.materials import set_part_material
    from rm_sim.core.project import Constraint, LoadCase, Project, TractionLoad
    from rm_sim.mesh.mesher import generate_tet10_mesh
    from rm_sim.solver.assembly_workflow import finish_analysis, prepare_analysis
    from rm_sim.solver.fields import part_field_views
    from rm_sim.solver.tasks import SolveScheduler

    step = work / "two_blocks.step"
    with gmsh_runtime.session():
        gmsh.model.add("dash")
        gmsh.model.occ.addBox(0, 0, 0, 20, 20, 20)
        gmsh.model.occ.addBox(0, 20, 0, 20, 20, 20)
        gmsh.model.occ.synchronize()
        gmsh.write(str(step))
        gmsh.model.remove()
    model = import_assembly(step)
    project = Project(name="仪表收口夹具", project_dir=str(work / "project"))
    sync_parts_from_model(project, model)
    for part in project.parts:
        set_part_material(project, part.part_id, "Q235")
    p1f = [f for f in model.faces if f.solid_tag == int(model.solids[0].tag)]
    p2f = [f for f in model.faces if f.solid_tag == int(model.solids[1].tag)]
    f_fixed = next(f for f in p1f if f.normal[1] < -0.999)
    f_top1 = next(f for f in p1f if f.normal[1] > 0.999)
    f_bottom2 = next(f for f in p2f if f.normal[1] < -0.999)
    f_load = next(f for f in p2f if f.normal[1] > 0.999)
    project.connections = [BondedConnection(
        name="界面粘接", side_a=ConnectionSide("p1", fingerprint_from_face(f_top1)),
        side_b=ConnectionSide("p2", fingerprint_from_face(f_bottom2)))]
    project.study.mesh_size_mm = 2.0
    project.study.load_cases = [LoadCase(
        name="100N", constraints=[Constraint(name="固定", kind="fixed", face_tag=int(f_fixed.tag),
                                             face=fingerprint_from_face(f_fixed), part_id="p1")],
        loads=[TractionLoad(name="面力", face_tag=int(f_load.tag),
                            face=fingerprint_from_face(f_load), part_id="p2",
                            total_force_n=100.0, direction=(0.0, -1.0, 0.0))])]
    prepared = prepare_analysis(project, model, work / "project" / "mesh.inp")
    sched = SolveScheduler(get_ccx_path(load_config()), max_workers=1)
    task = sched.submit("dash_assembly", prepared["deck"], work / "project" / "cases")
    task = sched.wait_task(task.task_id, timeout=300)
    if task.state != "completed":
        failures.append(f"夹具求解失败: {task.summary()}")
        log(f"ASSEMBLY_DASHBOARD: FAIL（夹具求解失败）")
        return 1
    completed = finish_analysis(project, task, prepared)
    fields = completed["fields"]
    ctx = json.loads(Path(completed["archive"]["results_json"]).read_text(encoding="utf-8"))

    # ---- 构造对话框数据（与 main_window._on_show_assembly_results 同一口径）----
    part_elsets = {name: set(int(x) for x in ids)
                   for name, ids in prepared["mesh_result"].element_sets.items()
                   if name.startswith("PART")}
    part_views = part_field_views(fields, part_elsets,
                                  prepared["mesh"].get("elems") or {})
    parts = {name: {"nodes": len(v.node_ids), "max_u": v.max_u_mag(), "max_mises": v.max_mises()}
             for name, v in part_views.items()}
    composite = {"nodes": len(fields.node_ids), "max_u": fields.max_u_mag(),
                 "max_mises": float(fields.s_mises.max())}
    parts_meta: dict[str, dict] = {}
    for k, entry in enumerate(ctx.get("assembly", {}).get("parts") or [], start=1):
        material = entry.get("material") or {}
        density = float(material.get("density_t_mm3") or 0.0)
        volume = float(entry.get("volume_mm3") or 0.0)
        parts_meta[f"PART{k}"] = {"mass_kg": volume * density * 1000.0,
                                  "material": material.get("name") or "—"}
    composite_meta = {"mass_kg": sum(m["mass_kg"] for m in parts_meta.values()),
                      "material": "全部部件（明细见各部件卡片）"}

    from rm_sim.gui.result_dialogs import AssemblyResultDialog

    dialog = AssemblyResultDialog(
        parts, composite=composite,
        interfaces=ctx.get("assembly", {}).get("interface_pressure", []),
        parts_meta={"装配合成": composite_meta, **parts_meta},
        report_path=completed["archive"]["report_md"])
    dialog.resize(760, 520)
    dialog.show()
    app.processEvents()

    # ---- ① 卡片/明细表数值 vs part_field_views 同源数组重算 ----
    for name, view in part_views.items():
        mises_view = float(view.s_mises.max())
        u_view = float(view.max_u_mag())
        card = dialog._cards.get(name) or {}
        if not card:
            failures.append(f"① 缺部件卡片: {name}")
            continue
        # 卡片数值 = 渲染所依据的同源浮点（Qt property，模块42 卡片口径）；文本 .6g 仅展示
        card_mises = float(card["mises"].property("value"))
        card_u = float(card["u"].property("value"))
        for label, got, expect in (("卡片 Mises", card_mises, mises_view),
                                   ("卡片 |U|", card_u, u_view)):
            diff = abs(got - expect)
            ok = diff <= 1e-9 * max(abs(expect), 1e-30)
            if not ok:
                failures.append(f"① {name} {label} 与同源重算不一致: {got} vs {expect}")
        text_mises = float(re.search(r":\s*([-+0-9.eE]+)", card["mises"].text()).group(1))
        if abs(text_mises - mises_view) > 1e-5 * max(abs(mises_view), 1e-30):
            failures.append(f"① {name} 卡片文本与数值脱节: {text_mises} vs {mises_view}")
        # 明细表（B4 门同位 行1列1）：切到该部件后读文本（.6g 展示精度 → 半字-relative ≤5e-6）
        dialog._combo.setCurrentText(name)
        app.processEvents()
        table_u = float(dialog._table.item(1, 1).text().split()[0])
        if abs(table_u - u_view) > 5e-6 * max(abs(u_view), 1e-30):
            failures.append(f"① {name} 明细表 |U| 与同源重算不一致: {table_u} vs {u_view}")
        log(f"[①] {name}: 卡片 Mises={card_mises:.9g} MPa、|U|={card_u:.9g} mm 与 "
            f"part_field_views 重算一致（|diff|≤1e-9 相对）；明细表 |U|={table_u:.6g}；节点 {len(view.node_ids)}")
    composite_view_u = float(fields.max_u_mag())
    composite_view_mises = float(fields.s_mises.max())
    card = dialog._cards.get("装合成成") or dialog._cards.get("装配合成")
    if card is None:
        failures.append("① 缺整装合成卡片")
    else:
        ok_u = abs(float(card["u"].property("value")) - composite_view_u) \
            <= 1e-9 * max(composite_view_u, 1e-30)
        ok_m = abs(float(card["mises"].property("value")) - composite_view_mises) \
            <= 1e-9 * max(composite_view_mises, 1e-30)
        if not (ok_u and ok_m):
            failures.append("① 整装合成卡片与全场景不一致")
        else:
            log("[①] 装配合成卡片与整场 max 同源一致")

    # ---- ② 单位齐全 + 质量手算对照 ----
    texts = collect_visible_texts(dialog)
    for unit in ("MPa", "mm", "kg"):
        if not any(unit in text for text in texts):
            failures.append(f"② 可见文本缺单位 {unit}")
    log("[②] 单位 MPa/mm/kg 齐全（卡片与明细表）")
    for name, entry in parts_meta.items():
        if name not in dialog._cards:
            continue
        material = (ctx.get("assembly", {}).get("parts") or [{}])[int(name[4:]) - 1].get("material") or {}
        density = float(material.get("density_t_mm3") or 0.0)
        volume = float((ctx.get("assembly", {}).get("parts") or [{}])[int(name[4:]) - 1].get("volume_mm3") or 0.0)
        hand = volume * density * 1000.0  # t/mm³ × mm³ = t → ×1000 = kg
        shown = float(dialog._cards[name]["mass"].property("value"))
        dev = abs(shown - hand) / hand if hand else float("inf")
        if dev > 0.005:
            failures.append(f"② {name} 质量=ρ×体积手算对照偏差 {dev * 100:.3f}% > 0.5%")
        else:
            log(f"[②] {name} 质量 {shown:.6g} kg vs 手算 ρ×V {hand:.6g} kg"
                f"（V={volume:.6g} mm³，ρ={density:.3g} t/mm³）偏差 {dev * 100:.4f}% ≤0.5%")

    # ---- ④ 术语正则（全部可见文本）----
    texts = collect_visible_texts(dialog)
    jargon = re.compile(r"ELSET|solver\.fields|part_field_views|ResultFields|\.py|[A-Za-z]:\\")
    hits = [text for text in texts if jargon.search(text)]
    if hits:
        failures.append(f"④ 可见文本含开发者术语: {hits[:5]}")
    else:
        log(f"[④] PASS 全部 {len(texts)} 条可见文本无开发者术语（ELSET/solver.fields/"
            f"part_field_views/.py/盘符路径）")
    if not any("口径见计算报告" in text for text in texts):
        failures.append("④ 可见文本缺『口径见计算报告』说明")
    else:
        log("[④] PASS 含『口径见计算报告』说明")
    if not any("打开诊断" in text for text in texts):
        failures.append("④ 缺『打开诊断』按钮")
    else:
        log("[④] PASS 含『打开诊断』按钮（计算报告）")

    # ---- ⑤ 截图 ----
    screenshot = work / "assembly_dashboard.png"
    dialog.grab().save(str(screenshot))
    if not screenshot.is_file() or screenshot.stat().st_size == 0:
        failures.append("⑤ 截图未落盘")
    else:
        log(f"[⑤] 截图 → {screenshot}（{screenshot.stat().st_size} bytes）")
    dialog.close()

    # ---- ③ 无结果状态：未计算、无数值 ----
    empty = AssemblyResultDialog({}, composite=None)
    empty.show()
    app.processEvents()
    empty_texts = collect_visible_texts(empty)
    if not any("未计算" in text for text in empty_texts):
        failures.append("③ 无结果时未显示『未计算』")
    numeric = [text for text in empty_texts if re.search(r"\d+\.\d+", text)]
    if numeric:
        failures.append(f"③ 无结果状态出现数值: {numeric[:5]}")
    else:
        log(f"[③] PASS 空结果对话框显示『未计算』且无任何数值（{len(empty_texts)} 条文本）")
    empty.grab().save(str(work / "assembly_dashboard_empty.png"))
    empty.close()

    # ---- ⑥ 回归保护：verify_gui_assembly 复跑（B4 原语义）----
    log("[⑥] 复跑 verify_gui_assembly.py（B4 门原语义：_table.item(1,1)=|U|、_combo 切部件）")
    env = dict(os.environ, PYTHONUTF8="1", OMP_NUM_THREADS="2",
               TEMP="E:/rm_sim/temp", TMP="E:/rm_sim/temp", QT_QPA_PLATFORM="offscreen")
    proc = subprocess.run([sys.executable, "-X", "utf8", "-B",
                           str(ROOT / "verify" / "verify_gui_assembly.py")],
                          cwd=ROOT, env=env, capture_output=True, timeout=900)
    stdout = proc.stdout.decode("utf-8", errors="replace")
    if proc.returncode != 0:
        failures.append(f"⑥ verify_gui_assembly 复跑 exit={proc.returncode}: "
                        f"{stdout[-400:] or proc.stderr.decode('utf-8', errors='replace')[-400:]}")
    else:
        log(f"[⑥] PASS verify_gui_assembly.py 复跑 exit 0（B4 原语义，未修订）")

    # ---- selfcheck 契约回归（对话框可构建 + 输出标记）----
    proc = subprocess.run([sys.executable, "-X", "utf8", "-B", str(ROOT / "main.py"), "--selfcheck"],
                          cwd=ROOT, env=env, capture_output=True, timeout=300)
    stdout = proc.stdout.decode("utf-8", errors="replace")
    if proc.returncode != 0 or "装配结果对话框构建成功" not in stdout:
        failures.append(f"selfcheck 契约回归失败 exit={proc.returncode}")
    else:
        log("[回归] PASS main.py --selfcheck exit 0（含『装配结果对话框构建成功』）")

    (work / "verification.json").write_text(json.dumps(
        {"created": datetime.datetime.now().isoformat(timespec="seconds"),
         "cases": {name: {"max_u": parts[name]["max_u"], "max_mises": parts[name]["max_mises"],
                          "mass_kg": parts_meta.get(name, {}).get("mass_kg"),
                          "material": parts_meta.get(name, {}).get("material")}
                   for name in part_views},
         "composite": composite_meta, "failures": failures},
        ensure_ascii=False, indent=2), encoding="utf-8")

    log("\n==== 摘要 ====")
    if failures:
        log(f"ASSEMBLY_DASHBOARD: FAIL（{len(failures)} 项）")
        for item in failures:
            log(f"  - {item}")
        return 1
    log("① 每部件卡片/明细表与 part_field_views 同源重算一致（≤1e-9 相对）")
    log("② 单位 MPa/mm/kg 齐全，质量=ρ×体积手算对照 ≤0.5%")
    log("③ 无结果显示『未计算』无数值；④ 无开发者术语且含『口径见计算报告』与『打开诊断』")
    log("⑤ 截图落证据目录；⑥ gui_assembly 复跑 + selfcheck 契约全过")
    log("ASSEMBLY_DASHBOARD: PASS")
    return 0


if __name__ == "__main__":
    _exit = 1
    try:
        _exit = main()
    except Exception as exc:  # noqa: BLE001
        log(f"ASSEMBLY_DASHBOARD: FAIL（顶层异常 {type(exc).__name__}: {exc}）")
        raise
    raise SystemExit(_exit)
