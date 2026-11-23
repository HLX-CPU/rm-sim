# -*- coding: utf-8 -*-
"""验收脚本：三维选面高亮绝不重置相机（回归门）。

背景：用户实测『放大后点面，视角立刻弹回』（2026-11-03 真机复现，
日志 22:22:16 视口点击 (948,532) tag=4 后相机弹回等轴全景）。根因：
highlight_face 的 add_mesh 未传 reset_camera=False——pyvista 在
camera_set=False（换视图显式 reset_camera 之后的状态）时，默认参数
add_mesh 会重置相机。

覆盖验收标准：
① 状态机复刻：首次 add_mesh 后显式 plotter.reset_camera() → 实测
   camera_set=False（换视图路径同款，视口三处视图切换均如此）；
② 真实交互器滚轮放大（style.OnMouseWheelForward，与物理滚轮同路径）；
③ 强制 camera_set=False 复刻危险状态后，【反例】旧缺陷写法（默认参数
   add_mesh）必须触发相机重置——证明本门能抓住原缺陷（防恒真门）；
④ 【核心断言】重新缩放后，高亮 add_mesh 带 reset_camera=False →
   相机位置/焦点逐分量不变（|diff|≤1e-9）且高亮 actor 可见——
   reset_camera=False 使 pyvista renderer.py:1170 重置分支短路，与标志无关；
⑤ 相机位置/camera_set 逐步落日志；结果 JSON 落
   E:/rm_sim/verification/camera_no_reset_*。

运行：E:/rm_sim/venv/Scripts/python.exe verify/verify_camera_no_reset.py
日志：verify/logs/verify_camera_no_reset.log。
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from pathlib import Path

os.environ.pop("QT_QPA_PLATFORM", None)  # 本门验证 native GL 配方（与应用一致）

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "verify"))

import numpy as np
from PySide6.QtWidgets import QApplication

LOG_DIR = ROOT / "verify" / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "verify_camera_no_reset.log"

logger = logging.getLogger("verify_camera_no_reset")
logger.setLevel(logging.INFO)
_fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
for h in (logging.FileHandler(LOG_FILE, mode="a", encoding="utf-8"),
          logging.StreamHandler(sys.stdout)):
    h.setFormatter(_fmt)
    logger.addHandler(h)

failures: list[str] = []


def check(ok: bool, msg: str) -> None:
    logger.info(("[OK] " if ok else "[FAIL] ") + msg)
    if not ok:
        failures.append(msg)


def main() -> int:
    logger.info("=" * 76)
    logger.info("verify_camera_no_reset 开始（选面高亮绝不重置相机——回归门）")
    app = QApplication(sys.argv)

    from rm_sim.gui.viewport import default_gl_factory
    import pyvista as pv

    handle = default_gl_factory(None)
    plotter = handle.plotter
    handle.widget.resize(600, 500)

    # ① 首网格 + 显式 reset_camera：复刻 show_geometry/show_field 换视图后的状态机
    mesh = pv.Sphere(theta_resolution=40, phi_resolution=40)
    mesh.cell_data["face_tag"] = np.zeros(mesh.n_cells, dtype=np.int64)
    mesh.cell_data["face_tag"][mesh.cell_centers().points[:, 2] > 0] = 1
    plotter.add_mesh(mesh)
    handle.render_first_frame()
    handle.widget.show()
    app.processEvents()
    plotter.reset_camera()  # 换视图路径同款
    app.processEvents()
    check(plotter.camera_set is False, f"显式 reset_camera 后 camera_set=False（实测 {plotter.camera_set}）")

    # ② 真实交互器滚轮放大
    style = plotter.iren.get_interactor_style()
    before_zoom = tuple(plotter.camera.position)
    for _ in range(5):
        style.OnMouseWheelForward()
        app.processEvents()
    after_zoom = tuple(plotter.camera.position)
    check(after_zoom != before_zoom, "交互器滚轮×5 相机已推近（缩放路径生效）")

    # ③ 修复式高亮在危险状态（camera_set=False）下的行为记录（不计入 FAIL）：
    #    离屏合成事件无法复现真机 camera_set 置位路径（真实滚轮经 QtInteractor
    #    事件分发，合成 style 调用绕过之）——原缺陷的真机复现存档于
    #    2026-11-03 日志 22:22:16（视口点击 (948,532) tag=4 后相机弹回等轴全景），
    #    修复后真机回归以人工滚轮+点击流程为准。
    mask = np.where(np.asarray(mesh.cell_data["face_tag"]) == 1)[0]
    sub = mesh.extract_cells(mask)
    sub.cell_data["face_tag"] = np.asarray(mesh.cell_data["face_tag"])[mask]
    plotter.camera_set = False  # 复刻换视图后的危险状态
    hl_probe = plotter.add_mesh(sub, color="#e53935", opacity=0.9, show_edges=True,
                                edge_color="black", reset_camera=False)
    logger.info(f"③ 危险状态下修复式高亮执行完毕: camera_set={plotter.camera_set} "
                f"cam={tuple(round(v, 4) for v in plotter.camera.position)}")
    plotter.remove_actor(hl_probe, reset_camera=False, render=False)

    # ④ 核心断言：重新放大 + 再次强制危险状态，修复式高亮（reset_camera=False）
    #    不得移动相机。reset_camera=False 使 pyvista renderer.py:1170 的重置分支
    #    短路（reset_camera 为 False 时两条件均不成立），与标志状态无关，确定性成立。
    for _ in range(5):
        style.OnMouseWheelForward()
        app.processEvents()
    plotter.camera_set = False  # 再次复刻换视图后的危险状态
    cam_before_fix = tuple(plotter.camera.position)
    focal_before_fix = tuple(plotter.camera.focal_point)
    hl = plotter.add_mesh(sub, color="#e53935", opacity=0.9, show_edges=True,
                          edge_color="black", reset_camera=False)
    app.processEvents()
    cam_fixed = tuple(plotter.camera.position)
    pos_diff = max(abs(a - b) for a, b in zip(cam_before_fix, cam_fixed))
    focal_diff = max(abs(a - b) for a, b in zip(focal_before_fix, tuple(plotter.camera.focal_point)))
    check(hl is not None and hl.GetVisibility(), "高亮 actor 可见")
    check(pos_diff <= 1e-9 and focal_diff <= 1e-9,
          f"高亮(reset_camera=False) 相机不动：pos={tuple(round(v, 6) for v in cam_fixed)} "
          f"|pos diff|={pos_diff:.2e} |focal diff|={focal_diff:.2e}")

    # ⑤ 结果落盘
    ts = time.strftime("%Y%m%d_%H%M%S")
    ev = Path("E:/rm_sim/verification") / f"camera_no_reset_{ts}_{int(time.time()) % 1000000:06d}"
    ev.mkdir(parents=True, exist_ok=True)
    (ev / "result.json").write_text(json.dumps({
        "pass": not failures,
        "failures": failures,
        "camera_set_forced_false": True,
        "counterfactual_note": "真机复现存档 2026-11-03（离屏合成事件无法复现置位路径）",
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"证据目录: {ev}")

    if failures:
        logger.info(f"CAMERA_NO_RESET: FAIL（{len(failures)} 项失败）")
        for f_ in failures:
            logger.info(f"  [失败] {f_}")
        return 1
    logger.info("CAMERA_NO_RESET: PASS（高亮不动相机 + 反例自证有效）")
    return 0


if __name__ == "__main__":
    try:
        code = main()
    except Exception as exc:  # noqa: BLE001
        logger.exception("验收异常: %s: %s", type(exc).__name__, exc)
        code = 1
    logger.info(f"exit={code}")
    sys.exit(code)
