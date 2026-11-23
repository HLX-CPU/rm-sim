# -*- coding: utf-8 -*-
"""报告/出图层（GUI 无关层）：matplotlib Agg 渲染，零 GL / 零窗口系统 / 零 Qt。

沿用 probes/probe8_ui_headless_fallback.py 已验证配方：
- MPLBACKEND=Agg 必须在 pyplot 导入之前设置（probe8 第 18 行）；
- Poly3DCollection + set_array(每三角标量) 出云图，savefig 到内存字节流。
"""
from __future__ import annotations

import io
import os

os.environ.setdefault("MPLBACKEND", "Agg")  # 必须先于 pyplot 导入

import matplotlib

matplotlib.use("Agg")  # 双保险：即使 MPLBACKEND 被外部改写也强制 Agg
# Windows 自带中文字体，避免标题/标签 CJK 字形缺失警告
matplotlib.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
matplotlib.rcParams["axes.unicode_minus"] = False

import numpy as np


def render_mesh_png(
    points: np.ndarray,
    tri_faces: np.ndarray,
    scalars: np.ndarray | None = None,
    title: str = "",
    scalar_label: str = "",
    figsize: tuple[float, float] = (10.24, 7.68),
    dpi: float = 100.0,
    view: tuple[float, float] = (18.0, -60.0),
) -> bytes:
    """边界三角网格 → PNG 字节流（Agg，无 GL，会话无关）。

    points: (n,3) 节点坐标；tri_faces: (k,3) 三角形节点索引；
    scalars: (n,) 点标量（按三角取均值上色），None 时不着色。
    """
    import matplotlib.pyplot as plt  # 延迟导入，确保 use("Agg") 已生效
    from mpl_toolkits.mplot3d.art3d import Poly3DCollection

    pts = np.asarray(points, dtype=np.float64)
    tris = np.asarray(tri_faces, dtype=np.int64)
    if tris.ndim != 2 or tris.shape[1] != 3:
        raise ValueError(f"tri_faces 必须是 (k,3)，实际 {tris.shape}")
    tri_pts = pts[tris]
    if scalars is None:
        tri_val = np.zeros(len(tris), dtype=np.float64)
    else:
        tri_val = np.asarray(scalars, dtype=np.float64)[tris].mean(axis=1)

    fig = plt.figure(figsize=figsize, dpi=dpi)
    try:
        ax = fig.add_subplot(111, projection="3d")
        pc = Poly3DCollection(tri_pts, cmap="viridis")
        pc.set_array(tri_val)
        ax.add_collection3d(pc)
        center = pts.mean(axis=0)
        radius = float(np.max(pts.max(axis=0) - pts.min(axis=0))) / 2.0
        if radius <= 0.0:
            radius = 1.0
        ax.set_xlim(center[0] - radius, center[0] + radius)
        ax.set_ylim(center[1] - radius, center[1] + radius)
        ax.set_zlim(center[2] - radius, center[2] + radius)
        ax.set_box_aspect((1, 1, 1))
        ax.set_xlabel("x")
        ax.set_ylabel("y")
        ax.set_zlabel("z")
        if title:
            ax.set_title(title)
        fig.colorbar(pc, ax=ax, shrink=0.6, label=scalar_label or None)
        ax.view_init(elev=view[0], azim=view[1])
        buf = io.BytesIO()
        fig.savefig(buf, format="png")
    finally:
        plt.close(fig)
    return buf.getvalue()


def render_field_png(
    points: np.ndarray,
    tri_faces: np.ndarray,
    scalars: np.ndarray,
    *,
    title: str,
    unit: str,
    deform_u: np.ndarray | None = None,
    deform_scale: float = 1.0,
    scalar_label: str = "",
    figsize: tuple[float, float] = (10.24, 7.68),
    dpi: float = 100.0,
    view: tuple[float, float] = (18.0, -60.0),
) -> tuple[bytes, dict]:
    """云图出图（可选变形缩放），返回 (PNG 字节, 信息)。

    info: {"png_bytes", "annotation", "max_scalar", "max_u_mag"|None}——
    annotation 含标题/单位/标量极值，变形模式下含放大倍数标注
    （调研报告 §4：变形缩放倍数可调并标注）。纯 Agg，零 GL。
    """
    import matplotlib.pyplot as plt
    from mpl_toolkits.mplot3d.art3d import Poly3DCollection

    pts = np.asarray(points, dtype=np.float64)
    tris = np.asarray(tri_faces, dtype=np.int64)
    vals = np.asarray(scalars, dtype=float).reshape(-1)
    if deform_u is not None and abs(deform_scale) > 0.0:
        pts = pts + deform_scale * np.asarray(deform_u, dtype=float)
    tri_pts = pts[tris]
    tri_val = vals[tris].mean(axis=1)

    fig = plt.figure(figsize=figsize, dpi=dpi)
    try:
        ax = fig.add_subplot(111, projection="3d")
        pc = Poly3DCollection(tri_pts, cmap="viridis")
        pc.set_array(tri_val)
        ax.add_collection3d(pc)
        center = pts.mean(axis=0)
        radius = float(np.max(pts.max(axis=0) - pts.min(axis=0))) / 2.0
        if radius <= 0.0:
            radius = 1.0
        ax.set_xlim(center[0] - radius, center[0] + radius)
        ax.set_ylim(center[1] - radius, center[1] + radius)
        ax.set_zlim(center[2] - radius, center[2] + radius)
        ax.set_box_aspect((1, 1, 1))
        ax.set_xlabel("x [mm]")
        ax.set_ylabel("y [mm]")
        ax.set_zlabel("z [mm]")
        annotation = f"{title} | 单位: {unit} | max = {float(vals.max()):.6g} {unit}"
        if deform_u is not None:
            annotation += f" | 放大 {deform_scale:g}×"
        ax.set_title(annotation, fontsize=10)
        fig.colorbar(pc, ax=ax, shrink=0.6, label=scalar_label or None)
        ax.view_init(elev=view[0], azim=view[1])
        buf = io.BytesIO()
        fig.savefig(buf, format="png")
    finally:
        plt.close(fig)
    info = {
        "png_bytes": buf.getvalue(),
        "annotation": annotation,
        "max_scalar": float(vals.max()),
        "max_u_mag": (
            float(np.max(np.linalg.norm(np.asarray(deform_u, dtype=float), axis=1)))
            if deform_u is not None
            else None
        ),
    }
    return info["png_bytes"], info
