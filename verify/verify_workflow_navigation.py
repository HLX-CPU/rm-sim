# -*- coding: utf-8 -*-
"""验收脚本：工作流导航与首启引导（模块40）。

覆盖验收标准：
① 新建空工程 → 首启占位页含 3 个按钮（导入 STEP / 打开示例工程 / 打开使用
   说明）+ 属性面板『几何』行内导入按钮；截图落证据目录；
② 点『打开示例工程』→ project.parts≥1 且流程条第 1 步变完成态（中央区切到
   三维视口页）；
③ 删除材料（material_name 清空 + 未确认）→ 材料步标红（error）且提示文本
   含『未指定材料』；
④ 点击未完成步骤触发对应对话框：空工程点『约束载荷』→ 工况设置停靠面板；
   材料标红态点『材料』→ MaterialDialog（exec 打桩捕获）；
⑤ 导入 two_blocks + 真实求解后全部 7 步逐一点亮（done）；
⑥ main.py --smoke 复跑 exit 0（不崩，主窗口已显示 + 渲染后端标记）。
全程 offscreen（本验收无 GL 拾取依赖）；日志写入 verify/logs/。

运行：E:/rm_sim/venv/Scripts/python.exe verify/verify_workflow_navigation.py
日志：verify/logs/verify_workflow_navigation.log。
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"  # 本验收无 GL 拾取依赖

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "verify"))

LOG_PATH = ROOT / "verify" / "logs" / "verify_workflow_navigation.log"
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


def grab(win, evidence, name: str) -> None:  # noqa: ANN001
    path = evidence / name
    try:
        win.grab().save(str(path))
        ok(f"截图已存证据目录: {path.name}（{path.stat().st_size} bytes）")
    except Exception as exc:  # noqa: BLE001
        fail(f"截图失败 {name}: {type(exc).__name__}: {exc}")


def geometry_row_widget(win):  # noqa: ANN001
    table = win.properties._table
    for r in range(table.rowCount()):
        if table.item(r, 0) is not None and table.item(r, 0).text() == "几何":
            return table.cellWidget(r, 1)
    return None


def main() -> int:
    log("=" * 76)
    log("verify_workflow_navigation 开始（工作流导航与首启引导）")
    log(f"python = {sys.executable}")
    check(
        "rm_sim" in sys.executable.lower() and "venv" in sys.executable.lower(),
        f"验收运行于 venv 解释器: {sys.executable}",
        f"应使用 E:/rm_sim/venv/Scripts/python.exe 运行本脚本，实际: {sys.executable}",
    )
    log("=" * 76)

    from assembly_check_utils import evidence_dir

    evidence = evidence_dir("workflow_navigation")
    log(f"证据目录: {evidence}")

    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton

    import rm_sim.gui.main_window as main_window_module
    from rm_sim.cad.assembly_import import import_assembly
    from rm_sim.core.connections import BondedConnection, ConnectionSide
    from rm_sim.core.face_identity import fingerprint_from_face
    from rm_sim.core.materials import set_part_material
    from rm_sim.core.project import Constraint, LoadCase, Project, TractionLoad
    from rm_sim.gui.material_dialog import MaterialDialog
    from rm_sim.gui.main_window import MainWindow

    app = QApplication.instance() or QApplication(["verify_workflow_navigation"])
    warnings: list[str] = []
    QMessageBox.warning = lambda *a, **k: warnings.append(str(a[2:]))
    QMessageBox.information = lambda *a, **k: warnings.append(str(a[2:]))

    # ---------- ① 新建空工程 → 占位页 3 按钮 + 流程条 7 步全未完成 ----------
    win = MainWindow()
    win.resize(1280, 820)
    win.show()
    for _ in range(20):
        app.processEvents()
    win._on_new_project()  # 显式新建空工程（等价首启空态）
    for _ in range(10):
        app.processEvents()

    check(
        win._central_stack.currentWidget() is win.empty_state,
        "① 新建空工程 → 中央区为首启占位页",
        "① 新建空工程后未显示占位页",
    )
    btn_texts = [b.text() for b in win.empty_state.findChildren(QPushButton)]
    check(
        any("导入 STEP" in t for t in btn_texts)
        and any("打开示例工程" in t for t in btn_texts)
        and any("打开使用说明" in t for t in btn_texts),
        f"① 占位页含 3 个入口按钮（{btn_texts}）",
        f"① 占位页按钮缺失: {btn_texts}",
    )
    check(
        len(win.workflow_bar._buttons) == 7 and len(win.workflow_bar.states) == 7,
        f"① 流程条 7 步（{[s.get('name') for s in win.workflow_bar.states]}）",
        f"① 流程条步数异常: {len(win.workflow_bar.states)}",
    )
    check(
        all(s.get("state") == "todo" for s in win.workflow_bar.states),
        "① 空工程 7 步全部未完成态",
        f"① 空工程流程态异常: {[(s.get('name'), s.get('state')) for s in win.workflow_bar.states]}",
    )
    check(
        any("缺少" in (s.get("hint") or "") for s in win.workflow_bar.states),
        "① 未完成步显示缺少的输入（hint 含『缺少』）",
        "① 未完成步未显示缺少的输入",
    )
    inline_btn = geometry_row_widget(win)
    check(
        isinstance(inline_btn, QPushButton) and "导入" in inline_btn.text(),
        "① 属性面板『几何』行内导入按钮存在",
        f"① 几何行内导入按钮缺失: {inline_btn}",
    )
    grab(win, evidence, "1_empty_state.png")

    # ---------- ④a 空工程点『约束载荷』→ 指引对话框（缺少：导入几何） ----------
    win.workflow_bar._buttons[2].click()  # 第 3 步：约束载荷（无几何）
    for _ in range(10):
        app.processEvents()
    check(
        bool(warnings) and "请先导入 STEP" in warnings[-1],
        f"④ 空工程点击『约束载荷』→ 指引对话框（{warnings[-1] if warnings else '（无）'}）",
        "④ 空工程点击约束载荷步骤无指引",
    )
    warnings.clear()

    # ---------- ② 点『打开示例工程』→ parts≥1 且第 1 步完成 ----------
    win.empty_state.btn_example.click()
    for _ in range(30):
        app.processEvents()
        if win.solid_model is not None:
            break
        time.sleep(0.05)
    check(
        len(win.project.parts) >= 1,
        f"② 打开示例工程 → project.parts={len(win.project.parts)}≥1",
        f"② 示例工程部件数异常: {len(win.project.parts)}",
    )
    check(
        win.workflow_bar.states[0].get("state") == "done",
        f"② 流程条第 1 步（导入）变完成态（{win.workflow_bar.states[0]}）",
        f"② 第 1 步未完成: {win.workflow_bar.states[0]}",
    )
    check(
        win._central_stack.currentWidget() is win.viewport,
        "② 中央区切到三维视口页",
        "② 中央区仍在占位页",
    )
    check(
        geometry_row_widget(win) is None,
        "② 几何已导入 → 几何行内导入按钮消失",
        "② 几何行内导入按钮未消失",
    )
    grab(win, evidence, "2_example_opened.png")

    # ---------- ④c 几何就绪后点『约束载荷』→ 工况设置停靠面板 ----------
    win.workflow_bar._buttons[2].click()  # 第 3 步：约束载荷（几何已导入）
    for _ in range(10):
        app.processEvents()
    check(
        win._setup_form is not None and win._setup_dock is not None,
        "④ 点击『约束载荷』步骤 → 工况设置面板打开",
        "④ 点击约束载荷步骤未打开工况设置面板",
    )
    win._close_setup_dock()
    for _ in range(5):
        app.processEvents()

    # ---------- ③ 删除材料 → 材料步标红 + 『未指定材料』 ----------
    for part in win.project.parts:
        part.material_name = ""
        part.material_confirmed = False
    win._refresh_project_views()
    for _ in range(5):
        app.processEvents()
    material_state = win.workflow_bar.states[1]
    check(
        material_state.get("state") == "error",
        f"③ 删除材料 → 材料步标红（state={material_state.get('state')}）",
        f"③ 材料步未标红: {material_state}",
    )
    check(
        "未指定材料" in (material_state.get("hint") or ""),
        f"③ 提示文本含『未指定材料』（{material_state.get('hint')!r}）",
        f"③ 提示文本缺『未指定材料』: {material_state.get('hint')!r}",
    )
    grab(win, evidence, "3_material_error.png")

    # ---------- ④b 材料标红态点『材料』→ MaterialDialog（exec 打桩） ----------
    material_dialog_calls: list[str] = []
    MaterialDialog.exec = lambda self: material_dialog_calls.append("exec") or 0
    win.workflow_bar._buttons[1].click()
    for _ in range(10):
        app.processEvents()
    check(
        bool(material_dialog_calls),
        "④ 点击『材料』步骤 → 触发材料对话框（exec 打桩捕获）",
        "④ 点击材料步骤未触发对话框",
    )

    # ---------- 使用说明按钮（os.startfile 打桩） ----------
    startfile_calls: list[str] = []
    real_startfile = getattr(os, "startfile", None)

    def _fake_startfile(path):
        startfile_calls.append(str(path))
        return None

    os.startfile = _fake_startfile
    try:
        win.empty_state.btn_docs.click()
        for _ in range(5):
            app.processEvents()
        check(
            bool(startfile_calls) and "使用说明" in startfile_calls[-1],
            f"『打开使用说明』→ os.startfile（{startfile_calls[-1] if startfile_calls else '（无）'}）",
            f"使用说明按钮未触发: {startfile_calls}",
        )
    finally:
        if real_startfile is not None:
            os.startfile = real_startfile

    # ---------- ⑤ 导入 two_blocks + 真实求解 → 7 步全部点亮 ----------
    win._on_new_project()  # 回空工程（占位页），再走完整流程
    for _ in range(5):
        app.processEvents()
    win.solid_model = import_assembly(SAMPLE)
    win.project = Project(name="workflow_nav_demo", project_dir=str(evidence / "two_blocks_run"))
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
                Constraint(name="底面固定", face_tag=3,
                           face=fingerprint_from_face(face3), part_id="p1")
            ],
            loads=[
                TractionLoad(name="顶面总力", face_tag=10,
                             face=fingerprint_from_face(face10), part_id="p2",
                             total_force_n=1000.0, direction=(0.0, -1.0, 0.0))
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
    warnings.clear()  # 求解阶段单独核对：不再有前置指引类提示
    check(
        require_steps_before_solve(win),
        "⑤ 求解前：导入/材料/约束载荷 3 步完成态",
        "⑤ 求解前前三步未点亮: "
        f"{[(s.get('name'), s.get('state')) for s in win.workflow_bar.states[:3]]}",
    )
    win._on_solve_static()
    deadline = time.monotonic() + 900
    while win._last_archive is None and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.02)
    check(
        win._last_archive is not None and win._solve_task is not None
        and win._solve_task.state == "completed",
        f"⑤ 真实求解完成（state={getattr(win._solve_task, 'state', None)}）",
        f"⑤ 求解超时/失败: {getattr(win._solve_task, 'state', None)} warnings={warnings[:2]}",
    )
    if win._last_archive is None:
        _finish()
        return _exit_code()
    win.select_result_part("装配合成")
    win.refresh_workflow_bar()
    for _ in range(5):
        app.processEvents()
    states = win.workflow_bar.states
    names = [s.get("name") for s in states]
    not_done = [(s.get("name"), s.get("state"), s.get("hint")) for s in states if s.get("state") != "done"]
    check(
        all(s.get("state") == "done" for s in states) and len(states) == 7,
        f"⑤ 全部 7 步点亮（{list(zip(names, [s.get('state') for s in states]))}）",
        f"⑤ 未点亮步骤: {not_done}",
    )
    grab(win, evidence, "4_all_done.png")

    # ---------- ⑥ main.py --smoke 复跑 exit 0 ----------
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env.setdefault("PYTHONIOENCODING", "utf-8")
    log("$ QT_QPA_PLATFORM=offscreen main.py --smoke")
    proc = subprocess.run(
        [sys.executable, str(ROOT / "main.py"), "--smoke"],
        capture_output=True, timeout=300, env=env, cwd=str(ROOT),
    )
    out = proc.stdout.decode("utf-8", errors="replace")
    check(
        proc.returncode == 0,
        f"⑥ main.py --smoke 复跑 exit=0",
        f"⑥ --smoke exit={proc.returncode}",
    )
    check(
        "主窗口已显示" in out,
        "⑥ --smoke stdout 含『主窗口已显示』",
        "⑥ --smoke stdout 缺『主窗口已显示』",
    )
    check(
        not warnings,
        "⑥ 求解阶段无警告弹窗（QMessageBox 打桩为空）",
        f"⑥ 求解阶段出现警告: {warnings[:3]}",
    )

    try:
        win.close()
        for _ in range(5):
            app.processEvents()
    except Exception:
        pass
    _finish()
    return _exit_code()


def require_steps_before_solve(win) -> bool:  # noqa: ANN001
    states = win.workflow_bar.states
    return len(states) == 7 and all(s.get("state") == "done" for s in states[:3])


def _finish() -> None:
    log("\n" + "=" * 76)
    if _failures:
        log("VERIFY_WORKFLOW_NAVIGATION: FAIL")
        for item in _failures:
            log(f"  [失败] {item}")
    else:
        log("VERIFY_WORKFLOW_NAVIGATION: PASS（占位页三入口 + 7 步流程条全点亮 + --smoke 复跑）")


def _exit_code() -> int:
    return 1 if _failures else 0


if __name__ == "__main__":
    _exit = 1
    try:
        _exit = main()
    except Exception as exc:  # noqa: BLE001
        fail(f"VERIFY_WORKFLOW_NAVIGATION: FAIL（顶层异常 {type(exc).__name__}: {exc}）")
        _exit = 1
    finally:
        _finish()
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_PATH, "w", encoding="utf-8") as f:
            f.write("\n".join(_lines) + "\n")
        print(f"[verify] 日志已写入 {LOG_PATH}")
    sys.exit(_exit)
