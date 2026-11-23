# -*- coding: utf-8 -*-
"""验收脚本：视口上屏状态机（当前显示唯一）+ GL 中文标题字体（评审 high-1/high-2）。

覆盖验收标准（功能「修复：视口结果视图残留叠加」）：
① GL 后端连续上屏 几何→网格→Mises云图→位移云图 4 次；
② 每次上屏后独立遍历 renderer GetActors2D 计数（口径同 viewport.scalar_bar_visible，
   不依赖被测实现 viewport.scene_2d_actor_state——后者仅做交叉核对）：
   文本 actor（vtkTextActor + vtkCornerAnnotation，VTK 9.7 实测后者
   IsA("vtkTextActor")=False 不是子类，必须两类都计）数量 ≤1；
   最终 vtkScalarBarActor 恰 1 且标题含单位（MPa/mm）；
③ 每次切换后上一张图的标题文本串在 actor 中不复现（无残留叠加）；
④ 截图 4 张落入证据目录（evidence_dir 约定 E:/rm_sim/verification，
   见 verify/assembly_check_utils.py:11-15）供人工目检；
⑤ stdout 打印 actor 计数摘要；exit 0 = 通过；
⑥ --selfcheck 复跑 main.py --selfcheck（offscreen 子进程）exit 0 且
   stdout 含「窗口构建成功」。

运行：
  E:/rm_sim/venv/Scripts/python.exe verify/verify_viewport_state.py          # 全量
  E:/rm_sim/venv/Scripts/python.exe verify/verify_viewport_state.py --selfcheck  # 仅 ⑥ 快门
日志：verify/logs/verify_viewport_state.log。

语义约定：本验收的对象是【GL 后端的状态机】本身——GL 视口未就绪时按 FAIL
（exit 1）处理而非 SKIPPED（verify_viewport_gl 已单另覆盖 GL 配方门并带
skip 语义；环境无 GL 时本门无法验收，如实报失败）。

字体事实（2026-11-02 probe_font*/probe_backend* 实测记录）：
VTK 9.7.1 默认文本后端（GetDefaultBackend()=0）忽略 SetFontFile，需先把
默认后端强制为 FreeType——该修复已落 viewport._ensure_vtk_freetype_backend；
本脚本断言当前标题 actor 的 GetFontFile() 指向 msyh.ttc。
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
from pathlib import Path

# 本验收专门验证 native windows + SetOffScreenRendering(1) 配方下的 GL 状态机
os.environ.pop("QT_QPA_PLATFORM", None)

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "verify"))

LOG_PATH = ROOT / "verify" / "logs" / "verify_viewport_state.log"
SAMPLE = ROOT / "samples" / "beam_100x10x10.step"
GL_BACKEND = "gl_native_offscreen"
CJK_FONT = "C:/Windows/Fonts/msyh.ttc"

# 悬臂梁解析场（替代求解结果；与 verify_viewport_gl 同源配方）
P_N, L_MM, I_MM4, Y_BAR = 100.0, 100.0, 10.0 * 10.0**3 / 12.0, 5.0

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


# ---------- 独立的 2D actor 计数（不依赖被测实现；遍历 GetActors2D） ----------

def _read_text(actor) -> str:  # noqa: ANN001
    """读文本 actor 内容：vtkCornerAnnotation 各角 GetText / vtkTextActor.GetInput。"""
    try:
        if actor.IsA("vtkCornerAnnotation"):
            parts = []
            for pos in range(4):  # LowerLeft=0 LowerRight=1 UpperLeft=2 UpperRight=3
                try:
                    t = actor.GetText(pos)
                except Exception:
                    break
                if t:
                    parts.append(str(t))
            return "\n".join(parts)
        getter = getattr(actor, "GetInput", None)
        return str(getter()) if getter is not None else ""
    except Exception:
        return ""


def count_2d_actors(viewport) -> dict:  # noqa: ANN001
    """遍历 GetActors2D（参考 viewport.scalar_bar_visible 的集合口径）。"""
    plotter = viewport._gl_handle.plotter
    ren = plotter.renderer
    coll = ren.GetActors2D()
    state = {
        "text": 0,
        "scalar_bar": 0,
        "texts": [],
        "scalar_bar_titles": [],
    }
    for i in range(coll.GetNumberOfItems()):
        actor = coll.GetItemAsObject(i)
        if actor is None:
            continue
        if actor.IsA("vtkScalarBarActor"):
            state["scalar_bar"] += 1
            state["scalar_bar_titles"].append(str(actor.GetTitle()))
        elif actor.IsA("vtkTextActor") or actor.IsA("vtkCornerAnnotation"):
            state["text"] += 1
            state["texts"].append(_read_text(actor))
    return state


def cross_check_with_impl(step_name: str, indep: dict) -> None:
    """被测实现 viewport.scene_2d_actor_state 与独立计数交叉核对（应一致）。"""
    try:
        impl = viewport_state_snapshot()
    except Exception as exc:  # noqa: BLE001
        fail(f"{step_name} 被测状态接口异常: {type(exc).__name__}: {exc}")
        return
    check(
        impl["text"] == indep["text"] and impl["scalar_bar"] == indep["scalar_bar"],
        f"{step_name} 交叉核对一致（独立={indep['text']}/{indep['scalar_bar']}"
        f" 实现={impl['text']}/{impl['scalar_bar']}）",
        f"{step_name} 交叉核对不一致: 独立计数 text={indep['text']} bar={indep['scalar_bar']}"
        f" vs 实现计数 text={impl['text']} bar={impl['scalar_bar']}",
    )


_VIEWPORT = None


def viewport_state_snapshot() -> dict:  # noqa: ANN201
    return _VIEWPORT.scene_2d_actor_state()


# ---------- GL 4 步上屏序列 ----------

def run_gl_sequence(evidence) -> None:
    global _VIEWPORT
    from rm_sim.cad.step_import import import_step
    from rm_sim.gui.main_window import MainWindow
    from rm_sim.gui.viewport import ViewportBackend
    from rm_sim.mesh.mesh_data import boundary_faces_corners, tetra_corners

    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(["verify_viewport_state"])
    win = MainWindow()
    win.resize(1100, 760)
    win.show()
    for _ in range(20):
        app.processEvents()

    backend = win.ensure_viewport()
    check(
        backend is ViewportBackend.GL,
        f"GL 视口就绪: backend={backend.value}",
        f"GL 视口未就绪: backend={backend.value}"
        f"（本验收对象为 GL 状态机，last_error={win.viewport.last_error or '（无）'}）",
    )
    if backend is not ViewportBackend.GL:
        return
    _VIEWPORT = win.viewport

    model = import_step(SAMPLE)
    try:
        # 演示数据：网格（无点数据 → 无色标）+ 解析 von Mises/位移场
        mesh = model.volume_mesh()
        pts = mesh.points
        tris = boundary_faces_corners(tetra_corners(mesh))
        n = len(pts)
        s_mises = abs(P_N * (L_MM - pts[:, 0]) * (pts[:, 1] - Y_BAR) / I_MM4)
        u = np_zeros((n, 3))
        u[:, 1] = -1e-3 * (L_MM - pts[:, 0]) / L_MM

        # (步骤, 上屏动作, 期望文本数, 期望色标数, 本步标题关键词, 色标标题, 上一张标题关键词们)
        steps = [
            (
                "1_geometry",
                lambda: win.show_geometry(model),
                0, 0, None, None, [],
                "step_1_geometry.png",
            ),
            (
                "2_mesh",
                lambda: win.viewport.show_mesh(mesh, "有限元网格"),
                1, 0, "有限元网格", None, [],
                "step_2_mesh.png",
            ),
            (
                "3_mises",
                lambda: win.viewport.show_result_fields(
                    pts, tris, u, s_mises, mode="mises"
                ),
                1, 1, "von Mises", "von Mises [MPa]", ["有限元网格"],
                "step_3_mises.png",
            ),
            (
                "4_displaced",
                lambda: win.viewport.show_result_fields(
                    pts, tris, u, s_mises, mode="displaced", scale=50
                ),
                1, 1, "位移云图", "|U| [mm]", ["有限元网格", "von Mises"],
                "step_4_displaced.png",
            ),
        ]

        for name, action, exp_text, exp_bar, title_kw, bar_title, prev_kws, shot in steps:
            info = action()
            state = count_2d_actors(win.viewport)
            log(
                f"[SUMMARY] {name}: info={info} | 文本actor={state['text']}"
                f" 色标actor={state['scalar_bar']}"
                f" 文本={state['texts']} 色标标题={state['scalar_bar_titles']}"
            )

            expect_prefix = {
                "1_geometry": "gl geometry ok",
                "2_mesh": "gl add_mesh ok",
                "3_mises": "gl field ok",
                "4_displaced": "gl field ok",
            }[name]
            check(
                str(info).startswith(expect_prefix),
                f"{name} 上屏成功（{info}）",
                f"{name} 上屏失败（期望 {expect_prefix}，实际 {info}）",
            )
            check(
                state["text"] <= 1,
                f"{name} 文本 actor 数量 ≤1（实际 {state['text']}）",
                f"{name} 文本 actor 数量 {state['text']} > 1（标题叠加残留）",
            )
            check(
                state["scalar_bar"] == exp_bar,
                f"{name} 色标 actor 数量 = {exp_bar}",
                f"{name} 色标 actor 数量 {state['scalar_bar']} != 期望 {exp_bar}",
            )
            if exp_text is not None and title_kw:
                check(
                    any(title_kw in t for t in state["texts"]),
                    f"{name} 当前标题含「{title_kw}」",
                    f"{name} 未找到当前标题「{title_kw}」: {state['texts']}",
                )
            if bar_title is not None:
                check(
                    bar_title in state["scalar_bar_titles"],
                    f"{name} 色标标题 = 「{bar_title}」（含单位）",
                    f"{name} 色标标题异常: {state['scalar_bar_titles']}"
                    f"（期望含「{bar_title}」）",
                )
            for prev in prev_kws:
                check(
                    not any(prev in t for t in state["texts"]),
                    f"{name} 上一张标题「{prev}」已不复现",
                    f"{name} 上一张标题「{prev}」仍在文本 actor 中（叠加残留）",
                )
                check(
                    not any(prev in t for t in state["scalar_bar_titles"]),
                    f"{name} 上一张色标标题「{prev}」已不复现",
                    f"{name} 上一张色标标题「{prev}」仍在（叠加残留）",
                )
            cross_check_with_impl(name, state)

            # 截图证据（人工目检：中文标题非方块、单色标、单标题）
            img = win.viewport.screenshot(evidence / shot)
            check(
                (evidence / shot).is_file() and (evidence / shot).stat().st_size > 0,
                f"{name} 截图已存证据目录: {evidence / shot}"
                f"（{(evidence / shot).stat().st_size if (evidence / shot).is_file() else 0} bytes）",
                f"{name} 截图未生成: {evidence / shot}",
            )
            if img is not None:
                import numpy as np

                arr = np.asarray(img)
                log(f"  | {name} 截图像素 shape={arr.shape} std={float(arr.std()):.2f}")

        # 字体句柄断言（评审 high-1 的实现载体；视觉目检看截图）
        text_actor = win.viewport._text_actor
        try:
            font_file = str(text_actor.GetTextProperty().GetFontFile())
            check(
                font_file.replace("\\", "/").endswith("msyh.ttc"),
                f"当前标题 actor 字体 = {font_file}",
                f"当前标题 actor 字体异常: {font_file}（期望 {CJK_FONT}）",
            )
        except Exception as exc:  # noqa: BLE001
            fail(f"读取标题 actor 字体失败: {type(exc).__name__}: {exc}")
        try:
            bar_actor = win.viewport._scalar_bar_actor
            bar_font = str(bar_actor.GetTitleTextProperty().GetFontFile())
            check(
                bar_font.replace("\\", "/").endswith("msyh.ttc"),
                f"色标标题字体 = {bar_font}",
                f"色标标题字体异常: {bar_font}（期望 {CJK_FONT}）",
            )
        except Exception as exc:  # noqa: BLE001
            fail(f"读取色标标题字体失败: {type(exc).__name__}: {exc}")

        # 最终状态落盘（actor 摘要 JSON 随截图供人工核对）
        final = count_2d_actors(win.viewport)
        (evidence / "actor_state.json").write_text(
            json.dumps(final, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        log(
            f"[SUMMARY] FINAL: 文本actor={final['text']}（≤1）"
            f" 色标actor={final['scalar_bar']}（=1）"
            f" 色标标题={final['scalar_bar_titles']}"
        )
        check(
            final["text"] <= 1 and final["scalar_bar"] == 1
            and any(("MPa" in t or "mm" in t) for t in final["scalar_bar_titles"]),
            "④ 4 次上屏后终态：文本 ≤1、色标恰 1 且标题含单位 MPa/mm",
            f"④ 终态不满足验收: text={final['text']} bar={final['scalar_bar']}"
            f" titles={final['scalar_bar_titles']}",
        )

        try:
            win.close()
            for _ in range(5):
                app.processEvents()
        except Exception:
            pass
    finally:
        try:
            model.close()
        except Exception:
            pass


def np_zeros(shape):  # noqa: ANN001, ANN201
    import numpy as np

    return np.zeros(shape)


# ---------- ⑥ main.py --selfcheck 复跑门 ----------

def run_selfcheck_gate() -> None:
    cmd = [sys.executable, str(ROOT / "main.py"), "--selfcheck"]
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env.setdefault("PYTHONIOENCODING", "utf-8")
    log(f"$ QT_QPA_PLATFORM=offscreen {' '.join(cmd)}")
    proc = subprocess.run(cmd, capture_output=True, timeout=300, env=env, cwd=str(ROOT))
    out = proc.stdout.decode("utf-8", errors="replace")
    for line in out.rstrip().splitlines():
        log(f"  | {line}")
    err = proc.stderr.decode("utf-8", errors="replace")
    if err.strip():
        log("  [stderr] " + " / ".join(err.rstrip().splitlines()[:5]))
    check(
        proc.returncode == 0,
        "⑥ main.py --selfcheck 复跑 exit=0",
        f"⑥ main.py --selfcheck 复跑 exit={proc.returncode}（期望 0）",
    )
    check(
        "窗口构建成功" in out,
        "⑥ selfcheck stdout 含「窗口构建成功」",
        "⑥ selfcheck stdout 缺少「窗口构建成功」",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--selfcheck",
        action="store_true",
        help="仅跑 main.py --selfcheck 复跑门（快门，不做 GL 4 步序列）",
    )
    args = parser.parse_args()

    log("=" * 76)
    log("verify_viewport_state 开始（视口上屏状态机 + GL 中文标题字体）")
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

    if args.selfcheck:
        run_selfcheck_gate()
    else:
        from assembly_check_utils import evidence_dir

        evidence = evidence_dir("viewport_state")
        log(f"证据目录: {evidence}")
        run_gl_sequence(evidence)
        if not _failures:
            run_selfcheck_gate()

    log("\n" + "=" * 76)
    if _failures:
        log("VERIFY_VIEWPORT_STATE: FAIL")
        for item in _failures:
            log(f"  [失败] {item}")
        return 1
    log("VERIFY_VIEWPORT_STATE: PASS（状态机唯一显示 + 中文标题字体 + selfcheck 复跑）")
    return 0


if __name__ == "__main__":
    _exit_code = 1
    try:
        _exit_code = main()
    except Exception as exc:  # noqa: BLE001
        log(f"VERIFY_VIEWPORT_STATE: FAIL（顶层异常 {type(exc).__name__}: {exc}）")
        _lines.append(f"Traceback: {exc}")
        _exit_code = 1
    finally:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_PATH, "w", encoding="utf-8") as f:
            f.write("\n".join(_lines) + "\n")
        print(f"[verify] 日志已写入 {LOG_PATH}")
    sys.exit(_exit_code)
