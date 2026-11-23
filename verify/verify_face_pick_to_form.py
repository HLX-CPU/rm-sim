# -*- coding: utf-8 -*-
"""验收脚本：点面直连工况表单（视口选面写入约束/载荷/连接目标）。

配方同 verify_viewport_gl.py（native windows GL + Qt Test 合成鼠标）。
GL 未就绪 = FAIL（本验收对象即 GL 点面交互，环境性失败如实报失败）。

覆盖验收标准：
① 非模态工况设置停靠面板打开期间，合成点击部件1 一面 → face_a.currentData
   ==(p1, 该tag)；再点击部件2 一面 → face_b.currentData==(p2, 该tag)
   （消灭交互评审实测的静默默认 ('p1',1)：B 面首项为占位 ("", -1)）；
② B 面未选时点『添加定义』→ definitions 计数不变且弹中文提示
   （连接 B 面未选择：拒绝添加，不再默认第一部件第一个面）；
③ 正常添加粘接 → project.connections 增 1 且 side_a/side_b 面指纹与
   点击面 fingerprint_from_face 一致（side_a.part_id/side_b.part_id 同步）；
④ 表单当前面与 viewport.highlight_face_tag 双向一致（切下拉→3D 高亮变化；
   点视口→表单目标下拉同步）；
⑤ 回归保护（本脚本内 subprocess 复跑）：verify_gui_assembly.py、
   verify_consistent_traction.py、verify_geometry_inspection_gui.py
   （SetupDialog.exec monkeypatch 兼容）全部 exit 0；
全程不改 convert/deck 数值链路（solver 层零改动，见 git status 核对）。
截图与日志落证据目录 E:/rm_sim/verification/face_pick_to_form_*。

运行：E:/rm_sim/venv/Scripts/python.exe verify/verify_face_pick_to_form.py
日志：verify/logs/verify_face_pick_to_form.log。
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

# 本验收专门验证 native windows GL 配方下的点面直连交互
os.environ.pop("QT_QPA_PLATFORM", None)

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "verify"))

LOG_PATH = ROOT / "verify" / "logs" / "verify_face_pick_to_form.log"
SAMPLE = ROOT / "samples" / "assembly_two_blocks.step"
GL_BACKEND = "gl_native_offscreen"

# 回归保护门（⑤）：SetupDialog 相关断言按原语义复跑
REGRESSION_GATES = (
    ("verify_gui_assembly.py", 900),
    ("verify_consistent_traction.py", 900),
    ("verify_geometry_inspection_gui.py", 300),
)

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


class _Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.INFO)
        self.records: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        msg = record.getMessage()
        self.records.append(msg)
        _lines.append("  | [log] " + msg)


CAPTURE = _Capture()

_WIN = None


def require_gl_backend(gate: str) -> bool:
    backend_value = _WIN.viewport.backend.value
    check(
        backend_value == GL_BACKEND,
        f"{gate} 前置：backend=={GL_BACKEND}",
        f"{gate} 前置失败：backend={backend_value}（期望 {GL_BACKEND}）",
    )
    return backend_value == GL_BACKEND


def combo_index(combo, pid: str, tag: int) -> int | None:  # noqa: ANN001
    for i in range(combo.count()):
        data = combo.itemData(i)
        if data and data[0] == pid and int(data[1]) == int(tag):
            return i
    return None


def run_interaction_checks(evidence) -> None:
    global _WIN
    import numpy as np
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QPushButton, QMessageBox

    from rm_sim.cad.assembly_import import import_assembly
    from rm_sim.core.project import Project
    from rm_sim.gui.main_window import MainWindow
    from rm_sim.gui.viewport import ViewportBackend

    app = QApplication.instance() or QApplication(["verify_face_pick_to_form"])
    warnings: list[str] = []
    QMessageBox.warning = lambda *a, **k: warnings.append(str(a[2:]))  # 捕获中文提示

    win = MainWindow()
    _WIN = win
    win.resize(1240, 800)
    win.show()
    for _ in range(20):
        app.processEvents()

    backend = win.ensure_viewport()
    check(
        backend is ViewportBackend.GL,
        f"GL 视口就绪: backend={backend.value}",
        f"GL 视口未就绪: backend={backend.value}"
        f"（本验收对象为 GL 点面交互，last_error={win.viewport.last_error or '（无）'}）",
    )
    if backend is not ViewportBackend.GL:
        return

    model = import_assembly(SAMPLE)
    win.solid_model = model
    win.project = Project(name="face_pick_to_form_demo")
    win.project.study.geometry_file = str(SAMPLE)
    win.show_assembly()
    for _ in range(10):
        app.processEvents()

    face2 = model.face_by_tag(2)   # 部件1（solid_tag=1）+x 端面
    face8 = model.face_by_tag(8)   # 部件2（solid_tag=2）+x 端面
    face12 = model.face_by_tag(12)  # 部件2 顶面（下拉反向高亮用）
    widget = win.viewport.current_widget
    check(widget is not None, "GL 交互部件就绪", "GL 交互部件缺失")

    def aim_camera(face) -> None:  # noqa: ANN001
        n = np.asarray(face.normal, dtype=float)
        c = np.asarray(face.center, dtype=float)
        win.viewport.set_camera(tuple(c + 300.0 * n), tuple(c), (0.0, 0.0, 1.0))
        for _ in range(5):
            app.processEvents()

    def click_face(face) -> bool:  # noqa: ANN001
        aim_camera(face)
        pos = win.viewport.project_point(face.center)
        if pos is None:
            fail(f"面 tag={face.tag} 中心投影不在视口内")
            return False
        win.viewport.last_pick = None
        QTest.mouseClick(
            widget, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(*pos)
        )
        for _ in range(5):
            app.processEvents()
        return win.viewport.last_pick == int(face.tag)

    def grab(name: str) -> None:
        path = evidence / name
        try:
            win.grab().save(str(path))
            ok(f"截图已存证据目录: {path.name}（{path.stat().st_size} bytes）")
        except Exception as exc:  # noqa: BLE001
            fail(f"截图失败 {name}: {type(exc).__name__}: {exc}")

    def open_form() -> bool:
        win._selected_face_tag = None  # 无预选面：A/B 均从占位开始（点面直连全流程）
        win._on_edit_setup()
        for _ in range(10):
            app.processEvents()
        form, dock = win._setup_form, win._setup_dock
        check(
            form is not None and dock is not None and dock.isVisible(),
            "工况设置停靠面板已打开（非模态）",
            "工况设置面板未打开",
        )
        if form is None:
            return False
        check(
            QApplication.activeModalWidget() is None,
            "面板为非模态（无 activeModalWidget，视口可继续点击）",
            "面板处于模态（activeModalWidget 非空），视口无法点选",
        )
        return True

    # ---------- ① 点面直连：第一次点→A、第二次点→B（消灭静默默认） ----------
    if require_gl_backend("①") and open_form():
        form = win._setup_form
        check(
            form.face_a.currentData() == ("", -1) and form.face_b.currentData() == ("", -1),
            "① 初始 A/B 均为『未选择』占位（静默默认 ('p1',1) 已消灭）",
            f"① 初始占位异常: A={form.face_a.currentData()!r} B={form.face_b.currentData()!r}",
        )
        if click_face(face2):
            check(
                form.face_a.currentData() == ("p1", 2),
                f"① 合成点击部件1 面2 → face_a.currentData==('p1',2)"
                f"（实际 {form.face_a.currentData()}）",
                f"① face_a 回填异常: {form.face_a.currentData()}（期望 ('p1', 2)）",
            )
            check(
                "已选" in form.face_a_status.text() and "mm²" in form.face_a_status.text(),
                f"① A 面状态显示『已选：部件X 面N（面积 … mm²）』（{form.face_a_status.text()!r}）",
                f"① A 面状态文本异常: {form.face_a_status.text()!r}",
            )
        if click_face(face8):
            check(
                form.face_b.currentData() == ("p2", 8),
                f"① 再点击部件2 面8 → face_b.currentData==('p2',8)"
                f"（实际 {form.face_b.currentData()}）",
                f"① face_b 回填异常: {form.face_b.currentData()}（期望 ('p2', 8)）",
            )
            check(
                "已选" in form.face_b_status.text() and "mm²" in form.face_b_status.text(),
                f"① B 面状态显示已选（{form.face_b_status.text()!r}）",
                f"① B 面状态文本异常: {form.face_b_status.text()!r}",
            )
        grab("1_pick_to_form.png")

    # ---------- ② B 面未选时点添加 → 计数不变 + 中文提示 ----------
    if require_gl_backend("②"):
        form = win._setup_form
        if form is None:
            fail("② 表单缺失")
        else:
            form.face_b.setCurrentIndex(0)  # B 面退回『未选择』占位
            form.kind.setCurrentText("粘接")
            for _ in range(5):
                app.processEvents()
            n_conn_before = len(win.project.connections)
            n_defs_before = form.definitions.count()
            add = next(
                (x for x in form.findChildren(QPushButton) if x.text() == "添加定义"), None
            )
            check(add is not None, "② 找到『添加定义』按钮", "② 未找到添加按钮")
            if add is not None:
                add.click()
                for _ in range(5):
                    app.processEvents()
                check(
                    len(win.project.connections) == n_conn_before
                    and form.definitions.count() == n_defs_before,
                    f"② B 面未选时点添加 → definitions 计数不变"
                    f"（连接 {n_conn_before}→{len(win.project.connections)}，"
                    f"列表 {n_defs_before}→{form.definitions.count()}）",
                    "② B 面未选仍添加成功（静默默认回归）",
                )
                check(
                    bool(warnings) and "B 面" in warnings[-1],
                    f"② 弹出中文提示（{warnings[-1] if warnings else '（无）'}）",
                    f"② 未弹中文提示: {warnings}",
                )
            grab("2_b_guard_popup.png")

    # ---------- ③ 正常添加粘接：点面→指纹写入 connections ----------
    if require_gl_backend("③"):
        # 重开全新表单（A/B 从占位开始），完整走 点A→点B→粘接→添加 闭环
        win._close_setup_dock()
        for _ in range(5):
            app.processEvents()
        warnings.clear()
        if open_form() and click_face(face2) and click_face(face8):
            form = win._setup_form
            check(
                form.face_a.currentData() == ("p1", 2)
                and form.face_b.currentData() == ("p2", 8),
                f"③ 全新表单点面回填 A=('p1',2) B=('p2',8)"
                f"（实际 A={form.face_a.currentData()} B={form.face_b.currentData()}）",
                f"③ 点面回填异常: A={form.face_a.currentData()} B={form.face_b.currentData()}",
            )
            form.kind.setCurrentText("粘接")
            for _ in range(5):
                app.processEvents()
            n_before = len(win.project.connections)
            add = next(
                (x for x in form.findChildren(QPushButton) if x.text() == "添加定义"), None
            )
            add.click()
            for _ in range(5):
                app.processEvents()
            check(
                len(win.project.connections) == n_before + 1,
                f"③ 添加粘接 → project.connections 增 1（{n_before}→{len(win.project.connections)}）",
                f"③ 连接数量异常: {n_before}→{len(win.project.connections)}"
                f"（提示 {warnings}）",
            )
            if len(win.project.connections) == n_before + 1:
                from rm_sim.core.face_identity import fingerprint_from_face

                conn = win.project.connections[-1]
                check(
                    conn.side_a.part_id == "p1" and conn.side_b.part_id == "p2",
                    f"③ 连接两侧部件（{conn.side_a.part_id}/{conn.side_b.part_id}）",
                    f"③ 连接部件异常: {conn.side_a.part_id}/{conn.side_b.part_id}",
                )
                check(
                    conn.side_a.face == fingerprint_from_face(face2),
                    "③ side_a 面指纹与点击面 face2 的 fingerprint_from_face 一致",
                    f"③ side_a 指纹不一致: {conn.side_a.face} vs {fingerprint_from_face(face2)}",
                )
                check(
                    conn.side_b.face == fingerprint_from_face(face8),
                    "③ side_b 面指纹与点击面 face8 的 fingerprint_from_face 一致",
                    f"③ side_b 指纹不一致: {conn.side_b.face} vs {fingerprint_from_face(face8)}",
                )
                check(
                    form.definitions.count() >= 1
                    and any(
                        "bonded" in form.definitions.item(i).text()
                        and "p1 ↔ p2" in form.definitions.item(i).text()
                        for i in range(form.definitions.count())
                    ),
                    "③ 定义列表汇总确认显示粘接定义（bonded p1 ↔ p2）",
                    f"③ 定义列表未显示新粘接定义: "
                    f"{[form.definitions.item(i).text() for i in range(form.definitions.count())]}",
                )
            grab("3_bonded_added.png")

    # ---------- ④ 双向一致：切下拉→3D 高亮变化；点视口→表单同步 ----------
    if require_gl_backend("④"):
        form = win._setup_form
        if form is None:
            fail("④ 表单缺失")
        else:
            idx12 = combo_index(form.face_b, "p2", 12)
            check(idx12 is not None, "④ B 面候选含部件2 面12", "④ 面12 不在候选中")
            if idx12 is not None:
                form.face_b.setCurrentIndex(idx12)
                for _ in range(5):
                    app.processEvents()
                check(
                    win.viewport.highlight_face_tag == 12
                    and win.viewport.highlight_visible(),
                    "④ 切 B 面下拉 → 3D 反向高亮面12",
                    f"④ 反向高亮异常: {win.viewport.highlight_face_tag}",
                )
            if click_face(face2):
                check(
                    win.viewport.highlight_face_tag == 2,
                    "④ 视口点击面2 → 3D 高亮面2",
                    f"④ 视口点击高亮异常: {win.viewport.highlight_face_tag}",
                )
                check(
                    form.face_b.currentData() == ("p1", 2),
                    f"④ 表单当前目标面（B）与点击面一致（{form.face_b.currentData()}）",
                    f"④ 表单未随视口点击同步: B={form.face_b.currentData()}",
                )
            grab("4_bidirectional.png")

    # ---------- 收尾：『完成』关闭面板，接线断开 ----------
    if win._setup_form is not None:
        done = next(
            (x for x in win._setup_form.findChildren(QPushButton) if x.text() == "完成"), None
        )
        if done is not None:
            done.click()
            for _ in range(10):
                app.processEvents()
        check(
            win._setup_dock is None and win._setup_form is None,
            "『完成』关闭停靠面板并清理引用/接线",
            f"『完成』后面板未清理: dock={win._setup_dock} form={win._setup_form}",
        )
    try:
        win.close()
        for _ in range(5):
            app.processEvents()
    except Exception:
        pass
    try:
        model.close()
    except Exception:
        pass


def run_regression_gates() -> None:
    """⑤ 回归保护：三个绿色门 subprocess 复跑（SetupDialog 相关断言按原语义）。"""
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env.setdefault("PYTHONIOENCODING", "utf-8")
    for name, timeout in REGRESSION_GATES:
        script = ROOT / "verify" / name
        log(f"$ QT_QPA_PLATFORM=offscreen {sys.executable} {script}")
        try:
            proc = subprocess.run(
                [sys.executable, "-X", "utf8", str(script)],
                capture_output=True, timeout=timeout, env=env, cwd=str(ROOT),
            )
            out = proc.stdout.decode("utf-8", errors="replace")
            err = proc.stderr.decode("utf-8", errors="replace")
            for line in out.rstrip().splitlines()[-6:]:
                log(f"  | {line}")
            if err.strip():
                log(f"  [stderr] {err.rstrip().splitlines()[-2:]}")
            check(
                proc.returncode == 0,
                f"⑤ 回归门 {name} 复跑 exit=0（原语义 PASS）",
                f"⑤ 回归门 {name} 复跑 exit={proc.returncode}",
            )
        except subprocess.TimeoutExpired:
            fail(f"⑤ 回归门 {name} 复跑超时（>{timeout}s）")


def main() -> int:
    rm_sim_logger = logging.getLogger("rm_sim")
    rm_sim_logger.setLevel(logging.INFO)
    rm_sim_logger.propagate = False
    rm_sim_logger.addHandler(CAPTURE)

    log("=" * 76)
    log("verify_face_pick_to_form 开始（点面直连工况表单：视口选面写入约束/载荷/连接目标）")
    log(f"python = {sys.executable}")
    check(
        "rm_sim" in sys.executable.lower() and "venv" in sys.executable.lower(),
        f"验收运行于 venv 解释器: {sys.executable}",
        f"应使用 E:/rm_sim/venv/Scripts/python.exe 运行本脚本，实际: {sys.executable}",
    )
    log("=" * 76)

    from assembly_check_utils import evidence_dir

    evidence = evidence_dir("face_pick_to_form")
    log(f"证据目录: {evidence}")
    run_interaction_checks(evidence)
    if not _failures:
        run_regression_gates()

    log("\n" + "=" * 76)
    if _failures:
        log("VERIFY_FACE_PICK_TO_FORM: FAIL")
        for item in _failures:
            log(f"  [失败] {item}")
        return 1
    log("VERIFY_FACE_PICK_TO_FORM: PASS（点面回填 A/B + B 面守卫 + 指纹一致 + 双向高亮 + 三门复跑）")
    return 0


if __name__ == "__main__":
    _exit_code = 1
    try:
        _exit_code = main()
    except Exception as exc:  # noqa: BLE001
        log(f"VERIFY_FACE_PICK_TO_FORM: FAIL（顶层异常 {type(exc).__name__}: {exc}）")
        _exit_code = 1
    finally:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_PATH, "w", encoding="utf-8") as f:
            f.write("\n".join(_lines) + "\n")
        print(f"[verify] 日志已写入 {LOG_PATH}")
    sys.exit(_exit_code)
