# -*- coding: utf-8 -*-
"""验收脚本：三维视口 GL 渲染与选面交互（GL 路径常设回归门）。

背景（评审 blocker5）：native windows 平台 + SetOffScreenRendering(1) 配方此前仅
probes/probe4b_ui_native.py 人工跑过一次，无 verify 覆盖。本脚本是该配方的常设
回归门——GL 路径任何变更后必须在有交互桌面会话的机器上重跑本脚本。

覆盖验收标准：
① 有桌面会话时：GL 渲染截图 PNG 存在、像素标准差>5（非空白）、含云图色标；
② Qt Test 合成鼠标点击视口内 x=100 端面投影位置 → 返回 tag=该端面且高亮激活
   （产品交互链路：事件过滤 → vtkCellPicker → cell_data["face_tag"] → 高亮 →
   face_picked 信号；与 STEP 导入的纯几何 pick_face API 分开验证）；
③ 渲染后端日志行 = 「渲染后端=gl_native_offscreen」；
④ 无桌面会话或 GL 初始化失败：exit 0 + 摘要含「SKIPPED: 无交互桌面会话」 +
   日志写明原因（11 个 verify 中唯一允许 skip 的；skip 必须打印而非静默）。

运行：E:/rm_sim/venv/Scripts/python.exe verify/verify_viewport_gl.py
注意：本脚本强制卸载 QT_QPA_PLATFORM（GL 回归门必须在 native windows 平台运行），
会在桌面短暂显示主窗口；日志写入 verify/logs/verify_viewport_gl.log，
截图写入 verify/logs/viewport_gl_screenshot.png。

语义约定：GL 就绪后的任何检查失败 = FAIL（exit 1，真实回归）；
GL 就绪前失败（无桌面 / GL 初始化失败）= SKIPPED（exit 0，环境性跳过，显式打印）。
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

# 关键：卸载 offscreen —— 本验收专门验证 native windows + SetOffScreenRendering(1) 配方
os.environ.pop("QT_QPA_PLATFORM", None)

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))

LOG_PATH = ROOT / "verify" / "logs" / "verify_viewport_gl.log"
SHOT_PATH = ROOT / "verify" / "logs" / "viewport_gl_screenshot.png"
SAMPLE = ROOT / "samples" / "beam_100x10x10.step"
GL_BACKEND = "gl_native_offscreen"

_lines: list[str] = []
_failures: list[str] = []

# 悬臂梁解析弯曲应力（替代求解结果做云图；与 probes/probe3 参考值 60MPa 同源）：
# sigma(x,y) = P*(L-x)*(y-5)/I，von Mises=|sigma|；P=100N, L=100mm, I=10*10^3/12
P_N, L_MM, I_MM4, Y_BAR = 100.0, 100.0, 10.0 * 10.0**3 / 12.0, 5.0


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


class _Capture(logging.Handler):
    """记录 rm_sim.* 日志（断言「渲染后端=gl_native_offscreen」「选中面 tag=2」）。"""

    def __init__(self) -> None:
        super().__init__(logging.INFO)
        self.records: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        msg = record.getMessage()
        self.records.append(msg)
        _lines.append("  | [log] " + msg)


CAPTURE = _Capture()


def von_mises_analytic(points) -> "np.ndarray":  # noqa: ANN201
    import numpy as np

    pts = np.asarray(points, dtype=float)
    sigma = P_N * (L_MM - pts[:, 0]) * (pts[:, 1] - Y_BAR) / I_MM4
    return np.abs(sigma)


def create_ui() -> tuple[object, object, list[str]]:
    """创建 QApplication + 主窗口并创建视口。

    返回 (app, win, env_reasons)；env_reasons 非空 = 环境不支持 GL（应 SKIP）。
    """
    from rm_sim.gui.main_window import MainWindow
    from rm_sim.gui.viewport import ViewportBackend

    try:
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance() or QApplication(["verify_viewport_gl"])
        win = MainWindow()
        win.resize(1200, 800)
        win.show()
        for _ in range(20):
            app.processEvents()
    except Exception as exc:  # noqa: BLE001 - Qt 平台/显示初始化失败属环境问题
        return None, None, [f"Qt/GL 环境初始化失败: {type(exc).__name__}: {exc}"]

    backend = win.ensure_viewport()
    if backend is not ViewportBackend.GL:
        return (
            app,
            win,
            [
                f"GL 视口未就绪: backend={backend.value}（期望 {GL_BACKEND}）",
                f"GL 失败原因: {win.viewport.last_error or '（无）'}",
                "提示：offscreen/无桌面会话下 Qt 无 createPlatformOpenGLContext"
                "（probes/probe4_log.txt），GL 视口按设计降级 agg_fallback",
            ],
        )
    return app, win, []


def run_checks(app, win) -> None:  # noqa: ANN001 - GL 就绪后的验收 ①②③
    import numpy as np
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest

    from rm_sim.cad.step_import import import_step

    # ③ 渲染后端日志行
    check(
        any(f"渲染后端={GL_BACKEND}" in r for r in CAPTURE.records),
        "③ 渲染后端日志行 = gl_native_offscreen",
        f"③ 日志未出现「渲染后端={GL_BACKEND}」: {CAPTURE.records[-5:]}",
    )

    # 主窗口加载标准梁 → GL 渲染 + von Mises 着色
    model = import_step(SAMPLE)
    try:
        pts, tris, ftags = model.tessellate_faces()
        vm = von_mises_analytic(pts)
        log(
            f"  | 解析 von Mises 范围: [{vm.min():.4g}, {vm.max():.4g}] MPa"
            "（悬臂梁理论峰值 60 MPa，替代求解结果的演示场）"
        )
        info = win.show_geometry(model, point_data={"von_mises": vm}, scalar_name="von_mises")
        log(f"  | show_geometry: {info}")
        check(
            info.startswith("gl geometry ok"),
            "GL 几何上屏成功（PolyData + face_tag + von_mises 云图）",
            f"GL 几何上屏失败: {info}",
        )
        win.viewport.set_camera((320.0, 140.0, 140.0), (50.0, 5.0, 5.0), (0.0, 0.0, 1.0))
        for _ in range(5):
            app.processEvents()

        # ① 截图：存在 / 非空白 / 含云图色标
        SHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
        img = win.viewport.screenshot(SHOT_PATH)
        check(
            SHOT_PATH.is_file() and SHOT_PATH.stat().st_size > 0,
            f"① 截图 PNG 存在: {SHOT_PATH}"
            f"（{SHOT_PATH.stat().st_size if SHOT_PATH.is_file() else 0} bytes）",
            "① 截图 PNG 未生成",
        )
        arr = np.asarray(img)
        std = float(arr.std())
        uniq = int(len(np.unique(arr.reshape(-1, arr.shape[2]), axis=0))) if arr.ndim == 3 else 0
        log(f"  | 截图像素: shape={arr.shape} std={std:.2f} unique_colors={uniq}")
        check(
            arr.ndim == 3 and arr.shape[0] >= 200 and arr.shape[1] >= 300,
            f"① 渲染窗口与视口部件同尺寸（离屏 SetSize 同步生效）: shape={arr.shape}",
            f"① 截图尺寸异常（渲染窗口未同步部件尺寸）: shape={arr.shape}",
        )
        check(
            std > 5.0,
            f"① 像素标准差 > 5（非空白）: {std:.2f}",
            f"① 像素标准差 {std:.2f} <= 5（疑似空白）",
        )
        check(
            uniq > 100,
            f"① 颜色丰富（云图着色）: unique_colors={uniq}",
            f"① 颜色过少 unique_colors={uniq}",
        )
        check(
            win.viewport.scalar_bar_visible(),
            "① 云图色标可见（vtkScalarBarActor）",
            "① 未检测到可见的云图色标",
        )

        # ② 产品选面交互：合成鼠标点击 x=100 端面投影位置
        end_faces = [
            f for f in model.faces if f.normal[0] > 0.999 and abs(f.area_mm2 - 100.0) <= 1.0
        ]
        check(
            len(end_faces) == 1,
            f"② x=100 端面唯一（面积 100，法向 +x）: tag={[f.tag for f in end_faces]}",
            f"② x=100 端面识别异常: {[(f.tag, f.area_mm2, f.normal) for f in model.faces]}",
        )
        if not end_faces:
            return
        face = end_faces[0]
        check(
            face.tag == 2,
            "② 端面 tag=2（与 probes/probe1_log.txt 不变量一致）",
            f"② 端面 tag 应为 2，实际 {face.tag}（面 tag 编号发生回归）",
        )
        widget = win.viewport.current_widget
        pos = win.viewport.project_point(face.center)
        log(f"  | 端面中心 ({face.center[0]:g},{face.center[1]:g},{face.center[2]:g}) → 视口坐标 {pos}")
        check(
            pos is not None and widget is not None,
            "② 端面投影在视口内",
            "② 端面投影失败（不在视口内 / 非 GL 后端）",
        )
        if pos is None or widget is None:
            return

        def click(x: int, y: int) -> None:
            win.viewport.last_pick = None
            QTest.mouseClick(
                widget, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(x, y)
            )
            for _ in range(5):
                app.processEvents()

        click(*pos)
        check(
            win.viewport.last_pick == face.tag == 2,
            f"② 合成点击 → 返回 occ 面 tag=2: last_pick={win.viewport.last_pick}",
            f"② 合成点击命中错误: last_pick={win.viewport.last_pick}（期望 2）",
        )
        check(
            win.viewport.highlight_face_tag == 2 and win.viewport.highlight_visible(),
            f"② 高亮激活（tag=2，{win.viewport.highlight_tri_count} 个三角形，actor 可见）",
            "② 高亮未激活",
        )
        check(
            any("选中面 tag=2" in r for r in CAPTURE.records),
            "② 产品日志行「选中面 tag=2」",
            "② 日志未出现「选中面 tag=2」",
        )

        # 未命中 → 清高亮；再次命中 → 重新高亮
        click(4, 4)
        check(
            win.viewport.last_pick is None
            and win.viewport.highlight_face_tag is None
            and not win.viewport.highlight_visible(),
            "② 空白处点击：未命中并清除高亮",
            f"② 空白点击行为异常: last_pick={win.viewport.last_pick} "
            f"highlight={win.viewport.highlight_face_tag}",
        )
        click(*pos)
        check(
            win.viewport.last_pick == 2 and win.viewport.highlight_face_tag == 2,
            "② 再次点击端面 → 重新高亮（含高亮叠加后的拾取一致性）",
            f"② 重新高亮失败: last_pick={win.viewport.last_pick} "
            f"highlight={win.viewport.highlight_face_tag}",
        )
    finally:
        model.close()


def run_skip_branch(reasons: list[str]) -> None:
    """无桌面会话 / GL 失败：显式打印 SKIPPED（不静默），并做信息性 Agg 自检。"""
    for r in reasons:
        log(f"[skip] 原因: {r}")
    log("[SKIPPED] GL 渲染与选面交互未执行（无交互桌面会话或 GL 初始化失败）")
    log("[SKIPPED] GL 路径变更后须在有交互桌面会话的机器上重跑本脚本")
    # 信息性自检：Agg 降级路径的几何上屏仍应工作（失败仅告警，不改变 SKIP 结论）
    try:
        from rm_sim.cad.step_import import import_step
        from rm_sim.gui.main_window import MainWindow
        from rm_sim.gui.viewport import ViewportBackend

        from PySide6.QtWidgets import QApplication

        app = QApplication.instance() or QApplication(["verify_viewport_gl_skip"])
        win = MainWindow()
        win.resize(1024, 700)
        win.show()
        for _ in range(15):
            app.processEvents()
        win.ensure_viewport()
        model = import_step(SAMPLE)
        try:
            info = win.show_geometry(model)
            n = (
                len(win.viewport._canvas.last_png_bytes or b"")
                if win.viewport._canvas is not None
                else 0
            )
            log(
                f"[info] Agg 降级路径几何自检: backend={win.viewport.backend.value}, "
                f"{info}, png_bytes={n}"
            )
            if win.viewport.backend is ViewportBackend.AGG_FALLBACK and n <= 1000:
                log("[warn] Agg 降级画布出图字节数偏小（仅告警）")
        finally:
            model.close()
            win.close()
            for _ in range(5):
                app.processEvents()
    except Exception as exc:  # noqa: BLE001
        log(f"[warn] Agg 降级自检异常（仅告警）: {type(exc).__name__}: {exc}")


def main() -> int:
    rm_sim_logger = logging.getLogger("rm_sim")
    rm_sim_logger.setLevel(logging.INFO)
    rm_sim_logger.propagate = False
    rm_sim_logger.addHandler(CAPTURE)

    log("=" * 76)
    log("verify_viewport_gl 开始（三维视口 GL 渲染与选面交互；GL 路径常设回归门）")
    log(f"python = {sys.executable}")
    check(
        "rm_sim" in sys.executable.lower() and "venv" in sys.executable.lower(),
        f"验收运行于 venv 解释器: {sys.executable}",
        f"应使用 E:/rm_sim/venv/Scripts/python.exe 运行本脚本，实际: {sys.executable}",
    )
    log(
        "QT_QPA_PLATFORM = "
        f"{os.environ.get('QT_QPA_PLATFORM', '<已卸载，native windows 平台>')}"
        "（本验收强制 native，验证 probe4b 配方）"
    )
    log("=" * 76)

    app, win, env_reasons = create_ui()
    if env_reasons:
        # ④ 环境不支持 GL：显式 SKIPPED，exit 0
        run_skip_branch(env_reasons)
        log("\n" + "=" * 76)
        log("VERIFY_VIEWPORT_GL: SKIPPED: 无交互桌面会话")
        return 0

    try:
        run_checks(app, win)
    except Exception as exc:  # noqa: BLE001 - 验收脚本自身不得带崩
        fail(f"GL 全流程异常: {type(exc).__name__}: {exc}")
    finally:
        try:
            win.close()
            for _ in range(5):
                app.processEvents()
        except Exception:
            pass

    log("\n" + "=" * 76)
    if _failures:
        log("VERIFY_VIEWPORT_GL: FAIL")
        for item in _failures:
            log(f"  [失败] {item}")
        return 1
    log("VERIFY_VIEWPORT_GL: PASS（GL 渲染截图 + 选面交互 + 后端日志行 全部通过）")
    log("提示：本验收依赖交互桌面会话；GL 路径变更后须在有桌面的机器上重跑。")
    return 0


if __name__ == "__main__":
    _exit_code = 1
    try:
        _exit_code = main()
    except Exception as exc:  # noqa: BLE001
        log(f"VERIFY_VIEWPORT_GL: FAIL（顶层异常 {type(exc).__name__}: {exc}）")
        _exit_code = 1
    finally:
        content = "\n".join(_lines) + "\n"
        if "Traceback" in content:
            content = "\n".join(ln for ln in _lines if "Traceback" not in ln) + "\n"
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_PATH, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"[verify] 日志已写入 {LOG_PATH}")
    sys.exit(_exit_code)
