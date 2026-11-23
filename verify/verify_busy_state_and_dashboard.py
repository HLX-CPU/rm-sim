# -*- coding: utf-8 -*-
"""验收脚本：求解期间可用性与结果仪表卡片（模块42）。

覆盖验收标准：
① busy 态分类断言：查看类 QAction isEnabled()==True、导入/工况/网格等修改类
   ==False（_set_solve_busy 按类禁用/开放，不再 menuBar 一刀切）；取消按钮
   存在且点击进入取消流程（复用 _cancel_current_solve 现有链路，真实取消
   运行中的工作流任务）；busy 结束后全部 QAction 恢复基线使能；
② 求解前（含取消后/已配置未计算/已准备未计算）仪表卡片含『未计算/未验证』
   且无任何数值（红线：configured_not_computed/prepared_not_computed 不得
   显示为已求解）；
③ 真实求解完成后卡片数值与极值面板同源一致（同为 _result_fields：
   max_mises=s_mises.max()、max_u=max_u_mag()，|diff|≤1e-9 相对），最小安全
   系数=屈服/最大 Mises 手算一致（Q235 屈服 235 MPa，按部件取最小）；
④ 无屈服材料（yield_strength_mpa=None）时安全系数显示『—』；
⑤ 回归保护：verify_solver_gui_lifecycle.py 以修订后语义复跑 exit 0（B1 修订
   在门内以『模块42 B1 修订』注释标记，随本功能提交可 diff 追溯）；
   全部状态截图落证据目录。

运行：E:/rm_sim/venv/Scripts/python.exe verify/verify_busy_state_and_dashboard.py
日志：verify/logs/verify_busy_state_and_dashboard.log。
"""
from __future__ import annotations

import copy
import os
import subprocess
import sys
import time
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"  # 无 GL 拾取依赖

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "verify"))

LOG_PATH = ROOT / "verify" / "logs" / "verify_busy_state_and_dashboard.log"
SAMPLE = ROOT / "samples" / "assembly_two_blocks.step"

_lines: list[str] = []
_failures: list[str] = []


def log(msg: object) -> None:
    text = str(msg)
    print(text, flush=True)
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


def main() -> int:
    log("=" * 76)
    log("verify_busy_state_and_dashboard 开始（求解期间可用性与结果仪表卡片）")
    log(f"python = {sys.executable}")
    check(
        "rm_sim" in sys.executable.lower() and "venv" in sys.executable.lower(),
        f"验收运行于 venv 解释器: {sys.executable}",
        f"应使用 E:/rm_sim/venv/Scripts/python.exe 运行本脚本，实际: {sys.executable}",
    )
    log("=" * 76)

    from assembly_check_utils import evidence_dir

    evidence = evidence_dir("busy_state_and_dashboard")
    log(f"证据目录: {evidence}")

    from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton

    import rm_sim.gui.result_dialogs as result_dialogs_module
    from rm_sim.cad.assembly_import import import_assembly
    from rm_sim.core.connections import BondedConnection, ConnectionSide
    from rm_sim.core.face_identity import fingerprint_from_face
    from rm_sim.core.materials import set_part_material
    from rm_sim.core.project import Constraint, LoadCase, Project, TractionLoad
    from rm_sim.gui.main_window import MainWindow

    app = QApplication.instance() or QApplication(["verify_busy_state_and_dashboard"])
    warnings: list[str] = []
    QMessageBox.warning = lambda *a, **k: warnings.append(str(a[2:]))
    QMessageBox.information = lambda *a, **k: warnings.append(str(a[2:]))

    win = MainWindow()
    win.resize(1280, 820)
    win.show()
    for _ in range(20):
        app.processEvents()

    # 工程装配：two_blocks + 材料 + 定义 + 界面粘接（连接性门要求 p2 接地）
    win.solid_model = import_assembly(SAMPLE)
    win.project = Project(name="busy_dashboard_demo", project_dir=str(evidence / "run"))
    win.project.study.geometry_file = str(SAMPLE)
    win.show_assembly()
    set_part_material(win.project, "p1", "Q235")
    set_part_material(win.project, "p2", "Q235")
    face3 = win.solid_model.face_by_tag(3)
    face10 = win.solid_model.face_by_tag(10)
    face4 = win.solid_model.face_by_tag(4)
    face9 = win.solid_model.face_by_tag(9)
    win.project.study.load_cases = [
        LoadCase(
            name="工况1",
            constraints=[
                Constraint(name="底面固定", face_tag=3, face=fingerprint_from_face(face3), part_id="p1")
            ],
            loads=[
                TractionLoad(name="顶面总力", face_tag=10, face=fingerprint_from_face(face10),
                             part_id="p2", total_force_n=1000.0, direction=(0.0, -1.0, 0.0))
            ],
        )
    ]
    win.project.connections.append(
        BondedConnection(
            name="界面粘接",
            side_a=ConnectionSide("p1", fingerprint_from_face(face4)),
            side_b=ConnectionSide("p2", fingerprint_from_face(face9)),
        )
    )
    win._refresh_project_views()
    for _ in range(5):
        app.processEvents()

    def grab(name: str) -> None:
        path = evidence / name
        try:
            win.grab().save(str(path))
            ok(f"截图已存证据目录: {path.name}（{path.stat().st_size} bytes）")
        except Exception as exc:  # noqa: BLE001
            fail(f"截图失败 {name}: {type(exc).__name__}: {exc}")

    def dashboard_text() -> str:  # noqa: ANN001
        card = win.properties.dashboard_card
        labels = card.findChildren(type(card.badge))
        return " ".join(l.text() for l in labels if hasattr(l, "text"))

    # ---------- ① busy 态分类断言 + 取消流程 ----------
    win._on_solve_static()
    for _ in range(100):
        app.processEvents()
        if win._solve_cancel_button is not None and win._solve_task is not None:
            break
        time.sleep(0.05)
    busy_on = win._solve_cancel_button is not None
    check(
        busy_on,
        "① 提交求解 → busy 态（取消按钮出现）",
        "① 提交求解后未进入 busy 态",
    )
    if busy_on:
        modifying_disabled = all(not a.isEnabled() for a in win._act_modifying)
        viewing_enabled = all(a.isEnabled() for a in win._act_viewing)
        check(
            modifying_disabled and len(win._act_modifying) >= 10,
            f"① busy：修改类 QAction 全部禁用（{len(win._act_modifying)} 个）",
            f"① busy：修改类未全部禁用: "
            f"{[a.text() for a in win._act_modifying if a.isEnabled()]}",
        )
        check(
            viewing_enabled and len(win._act_viewing) >= 5,
            f"① busy：查看类 QAction 全部开放（{len(win._act_viewing)} 个）",
            f"① busy：查看类未全部开放: "
            f"{[a.text() for a in win._act_viewing if not a.isEnabled()]}",
        )
        cancel_btn = win._solve_cancel_button
        check(
            isinstance(cancel_btn, QPushButton) and "取消" in cancel_btn.text(),
            f"① 取消按钮存在（{cancel_btn.text() if cancel_btn else '（无）'}）",
            "① 取消按钮缺失",
        )
        grab("1_busy_state.png")
        cancel_btn.click()  # 复用 _cancel_current_solve 现有链路
        for _ in range(600):
            app.processEvents()
            if win._solve_task is None or win._solve_task.state == "cancelled":
                break
            time.sleep(0.1)
        check(
            win._solve_task is not None and win._solve_task.state == "cancelled",
            f"① 点击取消 → 任务进入取消流程（state={getattr(win._solve_task, 'state', None)}）",
            f"① 取消流程异常: state={getattr(win._solve_task, 'state', None)}",
        )
        for _ in range(20):
            app.processEvents()
        restored = all(a.isEnabled() for a in win._act_modifying)
        check(
            restored and win._solve_cancel_button is None,
            "① busy 结束 → 修改类 QAction 全部恢复 enabled、取消按钮移除",
            f"① 忙态结束后未恢复: "
            f"{[a.text() for a in win._act_modifying if not a.isEnabled()]}",
        )

    # ---------- ② 无求解证据：卡片『未计算/未验证』且无数值 ----------
    payload = win.properties.dashboard_card.last_payload
    check(
        payload is not None and not payload.get("solved"),
        f"② 取消后仪表 payload solved=False（{payload}）",
        f"② 取消后仪表状态异常: {payload}",
    )
    card_text = dashboard_text()
    check(
        "未计算" in card_text and "未验证" in card_text,
        f"② 卡片含『未计算/未验证』字样（{card_text!r}）",
        f"② 卡片缺未计算字样: {card_text!r}",
    )
    import re as _re

    digits = _re.findall(r"\d", card_text)
    check(
        not digits,
        "② 卡片无任何数值（红线：configured/prepared_not_computed 不显示为已求解）",
        f"② 未计算态出现数值: {digits}",
    )
    grab("2_dashboard_unsolved.png")

    # ---------- ③ 真实求解 → 卡片数值与极值面板同源一致 + 安全系数手算 ----------
    win._on_solve_static()
    deadline = time.monotonic() + 900
    while win._last_archive is None and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.02)
    check(
        win._last_archive is not None and win._solve_task is not None
        and win._solve_task.state == "completed",
        f"③ 真实求解完成（state={getattr(win._solve_task, 'state', None)}）",
        f"③ 求解超时/失败: {getattr(win._solve_task, 'state', None)} warnings={warnings[:2]}",
    )
    if not (win._last_archive and getattr(win._solve_task, "state", "") == "completed"):
        _finish()
        return _exit_code()
    win.select_result_part("装配合成")
    win._update_dashboard()
    for _ in range(5):
        app.processEvents()
    payload = win.properties.dashboard_card.last_payload
    fields = win._result_fields
    check(
        payload is not None and payload.get("solved"),
        f"③ 求解后仪表 solved=True（badge={payload and payload.get('badge')}）",
        f"③ 求解后仪表状态异常: {payload}",
    )
    if payload and fields is not None:
        card_mises = float(payload["max_mises"])
        src_mises = float(fields.s_mises.max())
        card_u = float(payload["max_u"])
        src_u = float(fields.max_u_mag())
        check(
            abs(card_mises - src_mises) <= 1e-9 * max(abs(src_mises), 1e-30),
            f"③ 最大 von Mises 与极值面板同源一致（卡片 {card_mises:.10g} vs "
            f"s_mises.max() {src_mises:.10g}，|diff|≤1e-9 相对）",
            f"③ max_mises 不一致: {card_mises!r} vs {src_mises!r}",
        )
        check(
            abs(card_u - src_u) <= 1e-9 * max(abs(src_u), 1e-30),
            f"③ 最大 |U| 与极值面板同源一致（卡片 {card_u:.10g} vs max_u_mag {src_u:.10g}）",
            f"③ max_u 不一致: {card_u!r} vs {src_u!r}",
        )
        # 极值面板同源性：_on_show_extremes 传给 ExtremesDialog 的就是 _result_fields
        captured: list = []

        class _RecorderExtremes:
            def __init__(self, fields, parent=None):  # noqa: ANN001
                captured.append(fields)

            def exec(self):
                return 0

        real_dialog = result_dialogs_module.ExtremesDialog
        result_dialogs_module.ExtremesDialog = _RecorderExtremes
        try:
            win._on_show_extremes()
        finally:
            result_dialogs_module.ExtremesDialog = real_dialog
        check(
            bool(captured) and captured[0] is fields,
            "③ 极值面板与仪表卡片同源（ExtremesDialog 收到同一 _result_fields）",
            "③ 极值面板数据源与仪表不一致",
        )
        # 安全系数手算：min(各部件屈服 / 该部件最大 Mises)，Q235 屈服 235 MPa
        ctx = win._result_context or {}
        parts = (ctx.get("assembly") or {}).get("parts", [])
        expected_sfs = [
            float(p["material"]["yield_strength_mpa"]) / float(p["max_mises"])
            for p in parts
            if p.get("material", {}).get("yield_strength_mpa") and p.get("max_mises")
        ]
        check(
            bool(expected_sfs),
            f"③ ctx 部件材料含屈服（{len(parts)} 部件，Q235 yield=235）",
            "③ ctx 缺屈服数据",
        )
        if expected_sfs:
            expected_min = min(expected_sfs)
            card_sf = float(payload["min_sf"])
            diff = abs(card_sf - expected_min) / max(abs(expected_min), 1e-30)
            check(
                diff <= 1e-9,
                f"③ 最小安全系数=屈服/最大Mises 手算一致（卡片 {card_sf:.10g} vs "
                f"手算 {expected_min:.10g}，|diff|={diff:.3g}≤1e-9 相对）",
                f"③ 安全系数不一致: 卡片 {card_sf!r} vs 手算 {expected_min!r}",
            )
            log(f"  | [info] 手算明细: {[round(v, 6) for v in expected_sfs]} → min={expected_min:.6g}")
        grab("3_dashboard_solved.png")

    # ---------- ④ 无屈服材料 → 安全系数『—』 ----------
    ctx2 = copy.deepcopy(win._result_context or {})
    for p in (ctx2.get("assembly") or {}).get("parts", []):
        if p.get("material"):
            p["material"]["yield_strength_mpa"] = None
    win._result_context = ctx2
    win._update_dashboard()
    payload = win.properties.dashboard_card.last_payload
    check(
        payload is not None and payload.get("min_sf") is None and payload.get("solved"),
        "④ 无屈服材料 → 最小安全系数 None（其余指标保留）",
        f"④ 无屈服态 payload 异常: {payload}",
    )
    check(
        win.properties.dashboard_card.lbl_sf.text() == "—",
        f"④ 安全系数显示『—』（{win.properties.dashboard_card.lbl_sf.text()!r}）",
        f"④ 安全系数显示异常: {win.properties.dashboard_card.lbl_sf.text()!r}",
    )
    grab("4_dashboard_no_yield.png")
    win._result_context = copy.deepcopy(ctx2)
    win._result_context.update({})
    # 还原：重建原 context（深拷贝回写后刷新）
    win._result_context = ctx = copy.deepcopy(win._result_context)
    win._update_dashboard()

    # ---------- ⑤ 绿色门修订版复跑 ----------
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env.setdefault("PYTHONIOENCODING", "utf-8")
    log("$ verify_solver_gui_lifecycle.py（B1 修订语义）复跑")
    proc = subprocess.run(
        [sys.executable, str(ROOT / "verify" / "verify_solver_gui_lifecycle.py")],
        capture_output=True, timeout=600, env=env, cwd=str(ROOT),
    )
    out = proc.stdout.decode("utf-8", errors="replace")
    for line in out.rstrip().splitlines()[-4:]:
        log(f"  | {line}")
    check(
        proc.returncode == 0,
        "⑤ verify_solver_gui_lifecycle.py 复跑 exit=0（修订后语义）",
        f"⑤ lifecycle 门复跑 exit={proc.returncode}",
    )
    check(
        "B1 修订" in (ROOT / "verify" / "verify_solver_gui_lifecycle.py").read_text(encoding="utf-8"),
        "⑤ 修订内容在门内以『模块42 B1 修订』注释标记（随提交可追溯）",
        "⑤ 修订标记缺失",
    )

    try:
        win.close()
        for _ in range(5):
            app.processEvents()
    except Exception:
        pass
    _finish()
    return _exit_code()


def _finish() -> None:
    log("\n" + "=" * 76)
    if _failures:
        log("VERIFY_BUSY_STATE_AND_DASHBOARD: FAIL")
        for item in _failures:
            log(f"  [失败] {item}")
    else:
        log("VERIFY_BUSY_STATE_AND_DASHBOARD: PASS（busy 分类 + 取消链路 + 仪表卡片红线/同源/安全系数）")


def _exit_code() -> int:
    return 1 if _failures else 0


if __name__ == "__main__":
    try:
        _exit = main()
    except Exception as exc:  # noqa: BLE001
        fail(f"VERIFY_BUSY_STATE_AND_DASHBOARD: FAIL（顶层异常 {type(exc).__name__}: {exc}）")
        _exit = 1
    finally:
        _finish()
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_PATH, "w", encoding="utf-8") as f:
            f.write("\n".join(_lines) + "\n")
        print(f"[verify] 日志已写入 {LOG_PATH}")
    sys.exit(_exit)
