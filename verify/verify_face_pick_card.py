# -*- coding: utf-8 -*-
"""验收脚本：点面交互核心——选中面卡片 / 悬停反馈 / 误点撤销（Qt Test 合成鼠标）。

配方同 verify/verify_viewport_gl.py（native windows + SetOffScreenRendering(1)，
合成鼠标事件驱动真实交互链路）。GL 视口未就绪 = FAIL（exit 1，本验收对象即
GL 交互；环境性失败如实报失败，不静默跳过）。

覆盖验收标准：
① MouseMove 至端面投影 → 悬停 QToolTip 文本含部件名与『mm²』
   （实现：viewport MouseMove 节流 vtkCellPicker → hover_face_changed →
   MainWindow._on_face_hover → QToolTip + last_face_tooltip 存档）；
② 点击 tag=2 → 选中面卡片字段与 model.face_by_tag(2) 的面积/法向一致
   （|diff|≤1e-6 相对；卡片原始数值取 properties.face_card.last_payload），
   已挂定义数=预先挂上的 1 约束/1 载荷/1 连接；
③ 先点 tag=7 再点 tag=2，执行撤销 → highlight_face_tag==2 且
   highlight_visible() 为真（选面历史栈：点中面按序入栈，撤销弹栈恢复并重新高亮；
   样例 assembly_two_blocks——梁样例仅 6 面无 tag=7）；
④ 点空白 → 卡片显示未选中（is_unselected + 标题「未选中」）；
⑤ Agg 工厂注入（set_factory 抛异常路径）→ 状态栏出现降级提示
   （『GL 不可用，已降级 Agg 画布；选面需三维视口（GL）』），
   且 Agg 画布点击尝试再次提示『选面需三维视口（GL）』；
⑥ ①-④ 每门断言 backend==gl_native_offscreen；⑤ 断言 agg_fallback；
   截图（win.grab）与日志落证据目录 E:/rm_sim/verification/face_pick_card_*。

运行：E:/rm_sim/venv/Scripts/python.exe verify/verify_face_pick_card.py
日志：verify/logs/verify_face_pick_card.log。
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

# 本验收专门验证 native windows GL 配方下的点面交互
os.environ.pop("QT_QPA_PLATFORM", None)

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "verify"))

LOG_PATH = ROOT / "verify" / "logs" / "verify_face_pick_card.log"
SAMPLE = ROOT / "samples" / "assembly_two_blocks.step"
GL_BACKEND = "gl_native_offscreen"

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
    """记录 rm_sim.* 日志（选中面/撤销选面/悬停相关日志随证据落盘）。"""

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


def rel_diff(a: float, b: float) -> float:
    return abs(float(a) - float(b)) / max(abs(float(b)), 1e-30)


def run_checks(evidence) -> None:
    global _WIN
    import numpy as np
    from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
    from PySide6.QtGui import QCursor, QMouseEvent
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QToolTip

    from rm_sim.cad.assembly_import import import_assembly
    from rm_sim.core.connections import BondedConnection, ConnectionSide
    from rm_sim.core.face_identity import fingerprint_from_face
    from rm_sim.core.project import Constraint, LoadCase, Project, TractionLoad
    from rm_sim.gui.main_window import MainWindow

    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(["verify_face_pick_card"])
    win = MainWindow()
    _WIN = win
    win.resize(1200, 800)
    win.show()
    for _ in range(20):
        app.processEvents()

    from rm_sim.gui.viewport import ViewportBackend

    backend = win.ensure_viewport()
    check(
        backend is ViewportBackend.GL,
        f"GL 视口就绪: backend={backend.value}",
        f"GL 视口未就绪: backend={backend.value}"
        f"（本验收对象为 GL 点面交互，last_error={win.viewport.last_error or '（无）'}）",
    )
    if backend is not ViewportBackend.GL:
        return

    # 模型与工程：镜像导入流程（import_assembly + 新工程 + show_assembly 同步部件）
    model = import_assembly(SAMPLE)
    win.solid_model = model
    win.project = Project(name="face_pick_card_demo")
    win.project.study.geometry_file = str(SAMPLE)
    info = win.show_assembly()
    log(f"  | show_assembly: {info}")
    log(f"  | 部件: {[p.name for p in win.project.parts]}")
    for _ in range(10):
        app.processEvents()

    f2 = model.face_by_tag(2)
    f7 = model.face_by_tag(7)
    log(
        f"  | tag2: area={f2.area_mm2:.6g} normal={tuple(round(v, 6) for v in f2.normal)}"
        f" center={tuple(round(v, 3) for v in f2.center)} solid={f2.solid_tag}"
    )
    log(
        f"  | tag7: area={f7.area_mm2:.6g} normal={tuple(round(v, 6) for v in f7.normal)}"
        f" center={tuple(round(v, 3) for v in f7.center)} solid={f7.solid_tag}"
    )

    # 预挂定义（验收②的『已挂定义数』=1/1/1）：1 约束 + 1 载荷 + 1 连接（tag=2）
    fp2 = fingerprint_from_face(f2)
    win.project.study.load_cases = [LoadCase(name="验收工况")]
    win.project.study.load_cases[0].constraints.append(
        Constraint(name="固定-验收", face_tag=2, face=fp2, part_id="p1")
    )
    win.project.study.load_cases[0].loads.append(
        TractionLoad(
            name="载荷-验收", face_tag=2, face=fp2, part_id="p1",
            total_force_n=10.0, direction=(0.0, -1.0, 0.0),
        )
    )
    win.project.connections.append(
        BondedConnection(
            name="粘接-验收",
            side_a=ConnectionSide("p1", fp2),
            side_b=ConnectionSide("p2", fp2),
        )
    )

    widget = win.viewport.current_widget
    check(widget is not None, "GL 交互部件就绪", "GL 交互部件缺失")

    def aim_camera(face) -> None:  # noqa: ANN001
        """正视目标面（沿法向外移）：保证投影命中且无遮挡歧义。"""
        n = np.asarray(face.normal, dtype=float)
        c = np.asarray(face.center, dtype=float)
        win.viewport.set_camera(tuple(c + 300.0 * n), tuple(c), (0.0, 0.0, 1.0))
        for _ in range(5):
            app.processEvents()

    def click_face(face, expect_tag: int) -> bool:  # noqa: ANN001
        aim_camera(face)
        pos = win.viewport.project_point(face.center)
        if pos is None:
            fail(f"面 tag={expect_tag} 中心投影不在视口内")
            return False
        win.viewport.last_pick = None
        QTest.mouseClick(
            widget, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(*pos)
        )
        for _ in range(5):
            app.processEvents()
        check(
            win.viewport.last_pick == expect_tag,
            f"合成点击面 tag={expect_tag}：last_pick={win.viewport.last_pick}",
            f"合成点击面 tag={expect_tag} 失败：last_pick={win.viewport.last_pick}",
        )
        return win.viewport.last_pick == expect_tag

    def hover_face(face, expect_tag: int) -> None:  # noqa: ANN001
        aim_camera(face)
        win.viewport._last_hover_tag = None  # 强制本门悬停发信号
        win.last_face_tooltip = ""
        pos = win.viewport.project_point(face.center)
        if pos is None:
            fail(f"悬停：面 tag={expect_tag} 中心投影不在视口内")
            return
        QTest.mouseMove(widget, QPoint(*pos))
        for _ in range(5):
            app.processEvents()
        if not win.last_face_tooltip:
            # 兜底：直接向交互部件投递 QMouseMoveEvent（同 QTest.mouseMove 语义）
            gpos = widget.mapToGlobal(QPoint(*pos))
            ev = QMouseEvent(
                QEvent.Type.MouseMove,
                QPointF(float(pos[0]), float(pos[1])),
                QPointF(gpos),
                Qt.MouseButton.NoButton,
                Qt.MouseButton.NoButton,
                Qt.KeyboardModifier.NoModifier,
            )
            app.sendEvent(widget, ev)
            for _ in range(5):
                app.processEvents()

    def grab(name: str) -> None:
        path = evidence / name
        try:
            win.grab().save(str(path))
            ok(f"截图已存证据目录: {path.name}（{path.stat().st_size} bytes）")
        except Exception as exc:  # noqa: BLE001
            fail(f"截图失败 {name}: {type(exc).__name__}: {exc}")

    # ---------- ① 悬停反馈：MouseMove → QToolTip 文本 ----------
    if require_gl_backend("①"):
        hover_face(f2, 2)
        tooltip = win.last_face_tooltip
        log(f"  | 悬停提示文本: {tooltip!r}")
        part_name = win._face_part_name(2)
        log(f"  | 部件名解析: {part_name!r} | QToolTip.text()={QToolTip.text()!r}")
        check(
            bool(tooltip) and "mm²" in tooltip,
            "① 悬停 QToolTip 文本含『mm²』",
            f"① 悬停文本缺『mm²』: {tooltip!r}",
        )
        check(
            bool(part_name) and part_name in tooltip,
            f"① 悬停 QToolTip 文本含部件名（{part_name}）",
            f"① 悬停文本不含部件名 {part_name!r}: {tooltip!r}",
        )
        check(
            "面 tag=2" in tooltip,
            "① 悬停 QToolTip 文本含面号「面 tag=2」",
            f"① 悬停文本缺面号: {tooltip!r}",
        )
        check(
            f2.normal[0] != 0 and f"{f2.area_mm2:.6g}" in tooltip,
            "① 悬停文本含面积数值（与 face_by_tag(2) 同源）",
            f"① 悬停文本缺面积数值 {f2.area_mm2:.6g}: {tooltip!r}",
        )
        grab("1_hover_tooltip.png")

    # ---------- ② 点击 tag=2 → 选中面卡片 ----------
    if require_gl_backend("②") and click_face(f2, 2):
        check(
            win.viewport.highlight_face_tag == 2 and win.viewport.highlight_visible(),
            "② 点击后高亮激活（tag=2）",
            f"② 高亮异常: tag={win.viewport.highlight_face_tag}",
        )
        payload = win.properties.face_card.last_payload
        check(
            payload is not None and payload["tag"] == 2,
            f"② 选中面卡片显示 tag=2（payload={payload}）",
            f"② 卡片 payload 异常: {payload}",
        )
        if payload is not None:
            face_ref = model.face_by_tag(2)
            check(
                rel_diff(payload["area_mm2"], face_ref.area_mm2) <= 1e-6,
                f"② 卡片面积与 face_by_tag(2) 一致（{payload['area_mm2']:.6g} vs "
                f"{face_ref.area_mm2:.6g} mm²，|diff|≤1e-6 相对）",
                f"② 卡片面积偏差超 1e-6: {payload['area_mm2']!r} vs {face_ref.area_mm2!r}",
            )
            n_ok = all(
                abs(float(p) - float(r)) <= 1e-6
                for p, r in zip(payload["normal"], face_ref.normal)
            )
            check(
                n_ok and len(payload["normal"]) == 3,
                "② 卡片法向与 face_by_tag(2) 一致（|diff|≤1e-6）",
                f"② 卡片法向偏差: {payload['normal']} vs {face_ref.normal}",
            )
            check(
                (payload["n_constraints"], payload["n_loads"], payload["n_connections"])
                == (1, 1, 1),
                "② 已挂定义数 = 约束 1 · 载荷 1 · 连接 1（预挂定义遍历统计）",
                "② 已挂定义数异常: "
                f"{(payload['n_constraints'], payload['n_loads'], payload['n_connections'])}",
            )
            check(
                payload["part_name"] == win._face_part_name(2)
                and payload["part_name"] in win.properties.face_card.title_text,
                f"② 卡片部件名一致（{payload['part_name']}）",
                f"② 卡片部件名异常: {payload['part_name']!r} vs "
                f"{win._face_part_name(2)!r}",
            )
        grab("2_click_card.png")

    # ---------- ③ 误点撤销：先点 tag=7 再点 tag=2 → 撤销恢复 2 ----------
    if require_gl_backend("③") and click_face(f7, 7):
        check(
            win.viewport.highlight_face_tag == 7,
            "③ 点击 tag=7 后当前高亮=7（选面历史栈=[7]）",
            f"③ tag=7 高亮异常: {win.viewport.highlight_face_tag}",
        )
        if click_face(f2, 2):
            check(
                win.viewport.highlight_face_tag == 2
                and win._face_pick_history[-2:] == [7, 2],
                f"③ 再点 tag=2：高亮=2，历史栈尾={win._face_pick_history[-2:]}"
                f"（完整栈={win._face_pick_history}，含②的首次点击）",
                f"③ 历史栈异常: {win._face_pick_history}",
            )
            win.undo_face_pick()  # 与 Ctrl+Z / 卡片按钮同一条撤销路径
            for _ in range(5):
                app.processEvents()
            check(
                win.viewport.highlight_face_tag == 2,
                f"③ 执行撤销 → highlight_face_tag==2（栈顶恢复）",
                f"③ 撤销后高亮异常: {win.viewport.highlight_face_tag}",
            )
            check(
                win.viewport.highlight_visible(),
                "③ 撤销后高亮可见（highlight_visible()）",
                "③ 撤销后高亮不可见",
            )
            check(
                win.properties.face_card.last_payload is not None
                and win.properties.face_card.last_payload["tag"] == 2,
                "③ 撤销后卡片恢复显示面 tag=2",
                f"③ 撤销后卡片异常: {win.properties.face_card.last_payload}",
            )
        grab("3_after_undo.png")

    # ---------- ④ 点空白 → 卡片未选中（并验证误点空白后撤销恢复） ----------
    if require_gl_backend("④"):
        win.viewport.last_pick = None
        QTest.mouseClick(
            widget, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(4, 4)
        )
        for _ in range(5):
            app.processEvents()
        card = win.properties.face_card
        check(
            card.is_unselected and "未选中" in card.title_text,
            f"④ 点空白 → 卡片显示未选中（标题={card.title_text!r}）",
            f"④ 点空白后卡片异常: unselected={card.is_unselected}"
            f" title={card.title_text!r}",
        )
        grab("4_empty_unselected.png")
        # 误点空白恢复故事：再点 tag=2 → 误点空白清除 → Ctrl+Z 恢复高亮
        if click_face(f2, 2):
            QTest.mouseClick(
                widget, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(4, 4)
            )
            for _ in range(5):
                app.processEvents()
            check(
                card.is_unselected,
                "④b 误点空白：选面被清除（复现真实误操作）",
                "④b 误点空白后卡片未清空",
            )
            card.undo_requested.emit()  # 『撤销选面』按钮同源信号（按钮/快捷键共同路径）
            for _ in range(5):
                app.processEvents()
            check(
                win.viewport.highlight_face_tag == 2 and win.viewport.highlight_visible(),
                "④b 点空白后撤销 → 恢复面 tag=2 高亮（栈式撤销）",
                f"④b 撤销恢复异常: highlight={win.viewport.highlight_face_tag}",
            )
        # Ctrl+Z 快捷键路径（信息性：主路径已验证，键盘事件依赖窗口焦点）
        history_before = list(win._face_pick_history)
        if history_before:
            QTest.keyClick(win, Qt.Key_Z, Qt.KeyboardModifier.ControlModifier)
            for _ in range(5):
                app.processEvents()
            log(
                f"  | [info] Ctrl+Z 快捷键尝试: 历史 {history_before} → "
                f"{win._face_pick_history}（键事件依赖平台焦点，主路径为按钮/信号/直接调用）"
            )

    # ---------- ⑤ Agg 工厂注入 → 状态栏降级提示 ----------
    try:
        win2 = MainWindow()
        win2.resize(900, 600)

        def raising_factory(parent):  # noqa: ANN001
            raise RuntimeError("注入：GL 构造失败（验收⑤ Agg 降级提示路径）")

        win2.viewport.set_factory(raising_factory)
        backend2 = win2.ensure_viewport()
        check(
            backend2.value == "agg_fallback",
            f"⑤ 注入抛异常工厂 → 降级 agg_fallback（实际 {backend2.value}）",
            f"⑤ 注入后后端异常: {backend2.value}",
        )
        status = win2.statusBar().currentMessage()
        log(f"  | 降级后状态栏: {status!r}")
        check(
            "降级" in status,
            "⑤ 状态栏出现降级提示",
            f"⑤ 状态栏缺降级提示: {status!r}",
        )
        check(
            "选面需三维视口（GL）" in status,
            "⑤ 状态栏含『选面需三维视口（GL）』",
            f"⑤ 状态栏缺选面提示: {status!r}",
        )
        # Agg 画布点击尝试 → 再次提示（无拾取能力）
        canvas = win2.viewport.current_widget
        if canvas is not None:
            QTest.mouseClick(
                canvas, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(30, 30)
            )
            for _ in range(5):
                app.processEvents()
            status2 = win2.statusBar().currentMessage()
            check(
                "选面需三维视口（GL）" in status2,
                f"⑤ Agg 画布点击 → 状态栏提示『选面需三维视口（GL）』（{status2!r}）",
                f"⑤ Agg 点击后状态栏异常: {status2!r}",
            )
        try:
            win2.grab().save(str(evidence / "5_agg_hint.png"))
            ok("⑤ Agg 降级窗口截图已存证据目录: 5_agg_hint.png")
        except Exception as exc:  # noqa: BLE001
            fail(f"⑤ 截图失败: {type(exc).__name__}: {exc}")
        win2.close()
        for _ in range(5):
            app.processEvents()
    except Exception as exc:  # noqa: BLE001
        fail(f"⑤ Agg 注入门异常: {type(exc).__name__}: {exc}")

    # 撤销选面日志行核对（历史栈路径真实发生）
    check(
        any("撤销选面：恢复面 tag=2" in r for r in CAPTURE.records),
        "⑥ 日志行「撤销选面：恢复面 tag=2」",
        f"⑥ 日志缺撤销记录: {CAPTURE.records[-8:]}",
    )

    try:
        win.close()
        for _ in range(5):
            app.processEvents()
    except Exception:
        pass


def main() -> int:
    rm_sim_logger = logging.getLogger("rm_sim")
    rm_sim_logger.setLevel(logging.INFO)
    rm_sim_logger.propagate = False
    rm_sim_logger.addHandler(CAPTURE)

    log("=" * 76)
    log("verify_face_pick_card 开始（点面交互核心：选中面卡片 / 悬停反馈 / 误点撤销）")
    log(f"python = {sys.executable}")
    check(
        "rm_sim" in sys.executable.lower() and "venv" in sys.executable.lower(),
        f"验收运行于 venv 解释器: {sys.executable}",
        f"应使用 E:/rm_sim/venv/Scripts/python.exe 运行本脚本，实际: {sys.executable}",
    )
    log(
        "QT_QPA_PLATFORM = "
        f"{os.environ.get('QT_QPA_PLATFORM', '<已卸载，native windows 平台>')}"
    )
    log("=" * 76)

    from assembly_check_utils import evidence_dir

    evidence = evidence_dir("face_pick_card")
    log(f"证据目录: {evidence}")
    run_checks(evidence)

    log("\n" + "=" * 76)
    if _failures:
        log("VERIFY_FACE_PICK_CARD: FAIL")
        for item in _failures:
            log(f"  [失败] {item}")
        return 1
    log("VERIFY_FACE_PICK_CARD: PASS（悬停提示 + 选中面卡片 + 栈式撤销 + Agg 提示）")
    return 0


if __name__ == "__main__":
    _exit_code = 1
    try:
        _exit_code = main()
    except Exception as exc:  # noqa: BLE001
        log(f"VERIFY_FACE_PICK_CARD: FAIL（顶层异常 {type(exc).__name__}: {exc}）")
        _exit_code = 1
    finally:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_PATH, "w", encoding="utf-8") as f:
            f.write("\n".join(_lines) + "\n")
        print(f"[verify] 日志已写入 {LOG_PATH}")
    sys.exit(_exit_code)
