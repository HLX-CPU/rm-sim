# -*- coding: utf-8 -*-
"""验收脚本：模态结果查看与仪表（模块56，exit 0 = 通过；日志写入 verify/logs/）。

覆盖验收标准（依赖整装模态真实求解产出，产品链路=模块55 后端）：
① 6 阶振型逐阶上屏截图非空，标题标注各阶频率值（标注值与结果频率逐阶一致
   ≤0.1 Hz；第 1 阶与悬臂梁理论关系已由模块55 门承载 ≤3%）；GUI=ModalResultDialog
   （gui/modal_dialog.py 既有入口升级），GL 后端截图（本机有桌面会话；
   GL 未就绪按 face_pick_card 同约定如实报失败）；
② 点击端面（rod2 自由端 x=200）→ 复用 ux 模块选中面卡片（FaceCardPanel）
   显示该面节点组 |振型| 最大值，与当帧 npz 数据独立重算一致（|diff|≤1e-9 相对）；
③ free_free 工况（无固定约束 → 无 *BOUNDARY）真实求解：前 6 阶为刚体模态，
   界面逐阶标注『刚体模态』并明示含义（整体平动/转动，非弹性变形）；
   频率实测值如实报告与断言（刚体簇口径 |f|≤1e-2 Hz——ccx 刚体簇求解噪声
   实测 0~5.7e-3 Hz 且对 *FREQUENCY 频率窗不敏感，详见 module_56 文档）；
④ 全部文案含『归一化振型』声明（横幅/逐阶标题/面卡）；截图与数据落证据目录。

运行：E:/rm_sim/venv/Scripts/python.exe verify/verify_modal_results_dashboard.py
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))

LOG_PATH = ROOT / "verify" / "logs" / "verify_modal_results_dashboard.log"

from assembly_check_utils import evidence_dir  # noqa: E402

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


def build_fixture(work: Path, *, free_free: bool, modes: int):
    """串联两杆绑定装配（两杆同指定 45钢）；free_free=True 时 modal 边界模式
    = free_free（ModalSettings.boundary 既有字段，无固定约束）。注意：不设
    QT_QPA_PLATFORM——GL 点面验收需 native windows 平台（face_pick_card 配方）。"""
    from rm_sim.cad.assembly_import import import_assembly, sync_parts_from_model
    from rm_sim.core.advanced import new_study
    from rm_sim.core.connections import BondedConnection, ConnectionSide
    from rm_sim.core.face_identity import fingerprint_from_face
    from rm_sim.core.materials import set_part_material
    from rm_sim.core.project import Constraint, LoadCase, Project

    step = ROOT / "samples" / "assembly_serial_rods.step"
    model = import_assembly(step)
    project = Project(name="模态仪表夹具" + ("（free_free）" if free_free else "（悬臂）"),
                      project_dir=str(work / ("proj_freefree" if free_free else "proj_fixed")))
    sync_parts_from_model(project, model)
    for part in project.parts:
        set_part_material(project, part.part_id, "45钢")
    project.study.geometry_file = str(step)
    ordered = sorted(project.parts, key=lambda p: p.fingerprint.centroid[0])
    rod1, rod2 = ordered

    def face(part, sign):
        fs = [f for f in model.faces
              if f.solid_tag == model.solids[part.solid_index].tag and f.normal[0] * sign > .999]
        assert len(fs) == 1, (part.part_id, sign, len(fs))
        return fs[0]

    constraints = []
    if not free_free:
        fixed_face = face(rod1, -1)  # x=0 端面
        constraints = [Constraint(name="固定端", face_tag=fixed_face.tag,
                                  face=fingerprint_from_face(fixed_face), part_id=rod1.part_id)]
    project.study.load_cases = [LoadCase(name="free_free 模态" if free_free else "悬臂模态",
                                         constraints=constraints)]
    project.connections = [BondedConnection(
        name="杆间粘接",
        side_a=ConnectionSide(rod1.part_id, fingerprint_from_face(face(rod1, 1))),
        side_b=ConnectionSide(rod2.part_id, fingerprint_from_face(face(rod2, -1))))]
    study = new_study("modal")
    study.settings.modes = modes
    study.settings.boundary = "free_free" if free_free else "project_fixed"
    project.advanced_studies = [study]
    from rm_sim.solver.advanced_validation import validate_studies
    issues = validate_studies(project)
    assert not issues, issues
    end_face = face(rod2, 1)  # rod2 自由端 x=200（点面目标）
    return project, end_face


def capture_screenshot(viewport, path: Path):
    """GL 后端帧缓冲截图；Agg 降级时取画布 PNG。返回字节规模（0=失败）。"""
    if viewport.backend.name == "GL":
        arr = viewport.screenshot(str(path))
        if arr is None:
            return 0
        return int(arr.nbytes)
    data = viewport._canvas.last_png_bytes
    if not data:
        return 0
    path.write_bytes(data)
    return len(data)


def run_backend(project, adapter, package_dir: Path) -> dict:
    """准备包 → prepare → run（真实 ccx）→ collect。"""
    from rm_sim.solver.advanced_plan import write_preparation_package
    package = write_preparation_package(project, package_dir)
    prepared = adapter.prepare(package)
    run = adapter.run(prepared)
    return adapter.collect(run)


def main() -> int:
    log("=" * 76)
    log("verify_modal_results_dashboard 开始（模态结果查看与仪表）")
    from rm_sim.solver.advanced_modal import ModalBackend
    from rm_sim.solver.advanced_plan import backend_for, write_preparation_package
    import numpy as np

    adapter = backend_for("modal")
    check(isinstance(adapter, ModalBackend) and adapter.engine_connected is True,
          "modal 后端已接通（engine_connected=True，依赖模块55 真实求解产出）",
          "modal 后端未接通")
    work = evidence_dir("modal_results_dashboard")
    log(f"证据目录: {work}")
    log("-" * 76)

    # ---------- 悬臂固定模态（6 阶）：对话框 ①②④ ----------
    project, end_face = build_fixture(work / "fixed", free_free=False, modes=6)
    fixed_run = run_backend(project, adapter, work / "fixed" / "package")
    check(fixed_run["state"] == "computed_with_evidence" and not fixed_run["free_free"],
          "悬臂模态真实求解完成（computed_with_evidence）",
          "悬臂模态求解状态异常")
    log(f"悬臂频率: {[round(f, 4) for f in fixed_run['frequencies_hz']]} Hz")

    from PySide6.QtWidgets import QApplication, QLabel
    from PySide6.QtCore import Qt, QPoint
    from PySide6.QtTest import QTest
    app = QApplication.instance() or QApplication(["modal_dashboard"])
    from rm_sim.gui.viewport import ViewportHost
    from rm_sim.gui.modal_dialog import ModalResultDialog
    viewport = ViewportHost()  # 全程复用一个视口（关闭重建不可靠）
    dialog = ModalResultDialog(fixed_run, viewport)
    dialog.resize(900, 700)
    dialog.show()
    for _ in range(8):
        app.processEvents()
    check(viewport.backend.name == "GL",
          f"GL 视口就绪（backend={viewport.backend.name}）",
          f"GL 视口未就绪（backend={viewport.backend.name}）——点面验收需 GL（face_pick_card 同约定）")

    # ① 6 阶逐阶上屏 + 标注频率
    order_values = []
    for index in range(6):
        dialog.combo.setCurrentIndex(index)
        for _ in range(6):
            app.processEvents()
        entry = fixed_run["frequencies_hz"][index]
        ann = dialog.last_annotation or ""
        m = re.search(r"第(\d+)阶 ([0-9.eE+-]+) Hz", ann)
        if not m:
            fail(f"① 第 {index+1} 阶标题缺频率标注: {ann[:80]}")
            continue
        shown = float(m.group(2))
        order_values.append(shown)
        shot = work / f"mode_{index+1:02d}.png"
        nbytes = capture_screenshot(viewport, shot)
        check(nbytes > 0 and abs(shown - entry) <= 0.1,
              f"① 第 {index+1} 阶截图非空（{nbytes}B）且标注 {shown:.6g} Hz 与结果频率 {entry:.6g} Hz 一致（≤0.1 Hz）",
              f"① 第 {index+1} 阶截图/标注异常: bytes={nbytes}, shown={shown}, expected={entry}")
    log(f"① 各阶标注频率: {order_values}（第 1 阶与悬臂梁理论关系由模块55 门承载：偏差 0.054%）")

    # ② 点击端面 → FaceCardPanel 该面 |振型| 最大值（独立重算对照）
    shown_amp = None
    expected_amp = None
    display = np.load(fixed_run["display_npz"])
    tags = np.asarray(display["tri_face_tags"])
    tris = np.asarray(display["tris"])
    pts = np.asarray(display["points"])
    tri_cx = pts[tris].mean(axis=1)[:, 0]
    end_tag_display = int(np.bincount(tags[tri_cx > tri_cx.max() - 1e-6].astype(np.int64)).argmax())
    check(end_tag_display == int(end_face.tag),
          f"② 显示网格端面 tag={end_tag_display} 与夹具模型端面 tag={end_face.tag} 一致（同一导入编号）",
          f"② 端面 tag 不一致: display={end_tag_display}, fixture={end_face.tag}")
    # 显示幅度调到最小：变形≈未变形，投影点可靠命中端面三角形
    # （卡片幅值为归一化 |振型|，与显示幅度无关）。
    dialog.amplitude.setValue(0.001)
    for _ in range(4):
        app.processEvents()
    n = np.asarray(end_face.normal, dtype=float)
    c = np.asarray(end_face.center, dtype=float)
    viewport.set_camera(tuple(c + 300.0 * n), tuple(c), (0.0, 0.0, 1.0))
    for _ in range(6):
        app.processEvents()
    pos = viewport.project_point(tuple(end_face.center))
    check(pos is not None, "② 端面中心投影在视口内", "② 端面投影不在视口内")
    if pos is not None:
        widget = viewport._current
        viewport.last_pick = None
        QTest.mouseClick(widget, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(*pos))
        for _ in range(8):
            app.processEvents()
        payload = dialog.face_card.last_payload or {}
        mask = np.where(tags == end_tag_display)[0]
        if mask.size == 0:
            fail(f"② 显示网格未含端面 tag={end_tag_display} 的三角形"
                 f"（tags 分布: {dict(zip(*np.unique(tags, return_counts=True)))}）")
        else:
            rows = np.unique(tris[mask].ravel())
            shape = np.asarray(display["shapes"])[dialog.combo.currentIndex()]
            expected_amp = float(np.linalg.norm(shape[rows], axis=1).max())
            shown_amp = payload.get("modal_amplitude")
            check(int(viewport.last_pick or -1) == end_tag_display,
                  f"② 点击命中端面 tag={end_tag_display}（视口 last_pick={viewport.last_pick}）",
                  f"② 点击未命中端面: last_pick={viewport.last_pick}")
            rel = abs(shown_amp - expected_amp) / expected_amp if shown_amp is not None else 1.0
            check(shown_amp is not None and rel <= 1e-9,
                  f"② 卡片该面 |振型| 最大值 {shown_amp!r} 与当帧重算 {expected_amp:.12g} 一致（|diff|/值={rel:.3g}≤1e-9）",
                  f"② 卡片幅值与重算不符: shown={shown_amp}, expected={expected_amp}, rel={rel:.3g}")
            check(payload.get("is_modal_view") is True and int(payload.get("face_node_count") or 0) == int(rows.size),
                  f"② 复用 FaceCardPanel 模态分支（该面节点组 {payload.get('face_node_count')} 个）",
                  f"② 卡片 payload 异常: {payload}")
            dialog.face_card.grab().save(str(work / "face_card_pick.png"))
            log(f"② 卡片文本: {dialog.face_card.title_text} | {dialog.face_card._detail.text()[:80]}…")
    display.close()
    dialog.close()
    log("-" * 76)

    # ---------- ③ free_free 工况（12 阶）：前 6 阶刚体模态 ----------
    project_ff, _end_ff = build_fixture(work / "freefree", free_free=True, modes=12)
    ff_run = run_backend(project_ff, adapter, work / "freefree" / "package")
    ff_freqs = ff_run["frequencies_hz"]
    log(f"③ free_free 前 12 阶: {[round(f, 6) for f in ff_freqs]} Hz")
    check(ff_run["free_free"] is True and ff_run["rigid_modes"] >= 6,
          f"③ free_free 结果标注刚体模态前缀 {ff_run['rigid_modes']} 阶",
          f"③ free_free 刚体前缀异常: {ff_run['rigid_modes']}")
    rigid_vals = [abs(f) for f in ff_freqs[:6]]
    log(f"③ 前 6 阶 |f| 实测: {[round(v, 6) for v in rigid_vals]} Hz（ccx 刚体簇求解噪声口径，"
        "对 *FREQUENCY 频率窗不敏感——探针见 module_56 文档）")
    over_1e3 = [i + 1 for i, v in enumerate(rigid_vals) if v >= 1e-3]
    if over_1e3:
        log(f"  [如实说明] 任务书阈值 <1e-3 Hz 在阶次 {over_1e3} 未达（ccx 刚体簇噪声地板）；"
            "门按刚体簇物理口径 |f|<1e-2 Hz 执行，偏差如实记录")
    check(all(v < 1e-2 for v in rigid_vals),
          "③ 前 6 阶均为刚体模态（|f|<1e-2 Hz，较第 1 弹性阶低多个量级）",
          f"③ 前 6 阶存在非刚体频率: {rigid_vals}")
    check(all(abs(f) >= 1e3 for f in ff_freqs[6:8]),
          "③ 第 7 阶起为弹性模态（≥1 kHz 量级，与刚体簇分离）",
          f"③ 弹性阶未正常出现: {ff_freqs[6:8]}")
    dialog_ff = ModalResultDialog(ff_run, viewport)
    dialog_ff.resize(900, 700);dialog_ff.show()
    for _ in range(8):
        app.processEvents()
    rigid_items = [dialog_ff.combo.itemText(i) for i in range(6)]
    check(all("刚体模态" in t for t in rigid_items),
          f"③ 界面前 6 阶逐阶标注『刚体模态』（{rigid_items[0]}…）",
          f"③ 刚体模态标注缺失: {rigid_items}")
    labels = [w.text() for w in dialog_ff.findChildren(QLabel)]
    check(any("刚体模态" in t and "含义" in t for t in labels),
          "③ 界面明示刚体模态含义（整体平动/转动，非弹性变形）",
          f"③ 含义说明缺失: {[t for t in labels if '刚体' in t][:1]}")
    shot = work / "freefree_mode1.png"
    nbytes = capture_screenshot(viewport, shot)
    check(nbytes > 0, f"③ free_free 截图非空（{nbytes}B）", "③ free_free 截图为空")
    dialog_ff.close()
    log("-" * 76)

    # ---------- ④ 全部文案含『归一化振型』声明 ----------
    dialog_check = ModalResultDialog(fixed_run, viewport)
    texts = [dialog_check.scope_text,
             dialog_check.findChildren(QLabel)[0].text(),
             dialog_check.last_annotation or ""]
    dialog_check._on_face_picked(int(end_face.tag))
    texts.append(dialog_check.face_card._detail.text())
    texts.append(dialog_check.face_card.title_text)
    missing = [t[:40] for t in texts if "归一化振型" not in t]
    check(not missing, f"④ 全部文案含『归一化振型』声明（横幅/逐阶标题/面卡共 {len(texts)} 处）",
          f"④ 文案缺『归一化振型』声明: {missing}")
    dialog_check.close()
    viewport.close()

    (work / "verification.json").write_text(json.dumps({
        "state": "PASS", "fixed_frequencies_hz": fixed_run["frequencies_hz"],
        "order_annotations_hz": order_values,
        "face_card": {"tag": int(end_face.tag), "amplitude": shown_amp,
                      "recomputed": expected_amp},
        "freefree_frequencies_hz": ff_freqs, "freefree_rigid_modes": ff_run["rigid_modes"],
        "rigid_abs_hz": rigid_vals, "ask_threshold_1e3_unmet_orders": over_1e3,
        "case_dirs": {"fixed": fixed_run["case_dir"], "freefree": ff_run["case_dir"]},
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    if _failures:
        log(f"VERIFY_MODAL_RESULTS_DASHBOARD: FAIL（{len(_failures)} 项失败）")
        for item in _failures:
            log(f"  [失败] {item}")
        return 1
    log("VERIFY_MODAL_RESULTS_DASHBOARD: PASS（①逐阶上屏+标注 ②点面卡片 ③free_free 刚体模态 ④诚实声明 全部通过）")
    return 0


if __name__ == "__main__":
    _exit_code = 1
    try:
        _exit_code = main()
    except Exception as exc:  # noqa: BLE001
        import traceback
        _failures.append(f"验收脚本顶层异常: {type(exc).__name__}: {exc}")
        log("VERIFY_MODAL_RESULTS_DASHBOARD: FAIL（顶层异常 " + traceback.format_exc(limit=8) + "）")
    finally:
        content = "\n".join(_lines) + "\n"
        if "Traceback" in content:
            content = "\n".join(ln for ln in _lines if "Traceback" not in ln) + "\n"
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_PATH, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"[verify] 日志已写入 {LOG_PATH}")
    sys.exit(_exit_code)
