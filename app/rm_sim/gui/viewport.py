# -*- coding: utf-8 -*-
"""三维视口（GUI 层）：懒加载 + GL「构造+首帧」整周期降级 + Agg 兜底画布
+ 几何上屏（表面细分）+ 产品选面交互（合成/真实鼠标 → occ 面 tag → 高亮）。

GL 硬约束证据（本仓库 probes/，详见 app/ARCHITECTURE.md）：
- probes/probe4_log.txt：QT_QPA_PLATFORM=offscreen 下 QtInteractor 在【构造期】
  即抛 ``RenderWindowUnavailable: Render window is not current.`` ——
  因此 ensure_viewport 用同一个 try 包住「工厂构造 + 控件安装 + 首帧渲染」
  整个周期；只 try 构造不 try 首帧会漏掉"构造成功但首帧 paint 崩溃"的情形；
- probes/probe4b_ui_native.py：交互会话已验证配方 = native windows 平台
  （不设 offscreen）+ ``QtInteractor.ren_win.SetOffScreenRendering(1)``，
  首帧 render + 帧缓冲回读（screenshot）非空；
- probes/probe8_ui_headless_fallback.py：meshio + matplotlib Agg 可在
  无 GL / 无窗口系统环境出图 → 视口降级目标。

状态机：PENDING --ensure_viewport()--> GL("gl_native_offscreen") 或 AGG_FALLBACK
（一次性，不回退重试）。注入点：set_factory() 替换视口工厂（验收故障注入用）。

**上屏状态机（当前显示唯一，评审 high-2 修复）**：GL 后端每次上屏
（几何/网格/云图）前先清除上一轮的 ①标题文本 actor ②云图色标 actor
③主体 mesh actor，并保存本轮句柄（_text_actor/_scalar_bar_actor/_mesh_actor），
保证连续换视图不叠加旧标题/旧色标（此前全文件仅 3 处 remove_actor，
_show_mesh_gl 既不清 mesh actor 也不保存句柄）。标题文本经
``add_text(font_file=CJK_FONT_FILE)`` 挂微软雅黑（评审 high-1 修复：
GL 内中文此前渲染为方块）；云图色标 label 固定
『von Mises [MPa]』『|U| [mm]』（含单位）。验收：verify/verify_viewport_state.py。

选面交互与 cad 层纯几何 pick 的分工：
- ``cad.SolidModel.pick_face(点)``：纯几何 API（getClosestPoint，无渲染参与）；
- ``ViewportHost`` 左键点击：产品交互——vtkCellPicker 屏幕拾取 → 命中单元的
  cell_data["face_tag"]（occ 面 tag）→ 红色高亮 → face_picked 信号。
  两者通过 occ 面 tag 关联；各自独立验证（verify_step_import / verify_viewport_gl）。
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable

import numpy as np
from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from ..mesh.mesh_data import MeshData, boundary_triangles

log = logging.getLogger("rm_sim.gui.viewport")

# 悬停拾取节流（点面交互核心）：MouseMove 高频触发，vtkCellPicker 有成本——
# 时间间隔与位移双阈值，二者都不满足才真正拾取。
_HOVER_MIN_INTERVAL_S = 0.06
_HOVER_MIN_MOVE_PX = 3

# GL 内中文渲染字体（评审 high-1）：VTK 默认字体无 CJK 字形（渲染为方块并打
# "does not have a glyph" 警告）。微软雅黑 ttc 已验证可被 VTK freetype 读取
# （probe：font_file 后 GetFontFile 命中、0 条 glyph 告警、像素差异显著）；
# Qt 侧同源先例见 verify/verify_advanced_framework.py:27-33（QFontDatabase 加载）。
CJK_FONT_FILE = Path("C:/Windows/Fonts/msyh.ttc")

_VTK_FREETYPE_FORCED = False


def _ensure_vtk_freetype_backend() -> bool:
    """让 VTK 文本管线真正使用 SetFontFile 指定的字体文件（评审 high-1 的隐藏前提）。

    本机实测（VTK 9.7.1 / pyvista 0.49.2，2026-11-02 probe_font*/probe_backend*）：
    默认文本后端（vtkTextRenderer.GetDefaultBackend()=0，不在 Default=-1/
    FreeType=1/MathText=2 枚举内）会**忽略** vtkTextProperty.SetFontFile——
    设了 msyh.ttc 后整幅渲染像素逐位不变、CJK 照打 "does not have a glyph" 告警；
    把默认后端强制为 FreeType(1) 后，msyh.ttc/simhei.ttf/arial.ttf 三种字体
    渲染出三种不同位图、0 条告警。独立验证：vtkTextRenderer.RenderString
    (FreeType) 下 msyh.ttc 的「云图」= 66×35 非空字形（默认后端 2×32 空白）。
    DefaultBackend 是进程级全局，设一次全进程生效；Title/Label 等纯文本
    不受 MathText 影响（app 内无 $...$ 数学串）。
    """
    global _VTK_FREETYPE_FORCED
    if _VTK_FREETYPE_FORCED:
        return True
    _VTK_FREETYPE_FORCED = True
    try:
        import vtk

        tr = vtk.vtkTextRenderer()
        if not tr.FreeTypeIsSupported():
            log.warning("VTK FreeType 文本后端不可用，中文标题按默认字体渲染（可能为方块）")
            return False
        before = int(tr.GetDefaultBackend())
        free_type = int(vtk.vtkTextRenderer.FreeType)
        if before != free_type:
            tr.SetDefaultBackend(free_type)
            log.info("VTK 文本默认后端 %s -> FreeType（默认后端忽略 SetFontFile，实测）", before)
        return True
    except Exception:
        log.debug("VTK 文本后端设置失败（保持默认）", exc_info=True)
        return False


class ViewportBackend(Enum):
    """视口后端。value 即对外展示/验收使用的字符串。"""

    PENDING = "pending"
    GL = "gl_native_offscreen"  # native windows 平台 + SetOffScreenRendering(1)（probe4b 配方）
    AGG_FALLBACK = "agg_fallback"  # meshio+matplotlib Agg 画布（probe8 配方）


@dataclass
class GLViewportHandle:
    """视口工厂契约：``factory(parent) -> GLViewportHandle``。

    render_first_frame 必须真实驱动一次渲染（render + 帧缓冲回读），
    让"构造成功但首帧 paint 崩溃"与构造期失败走同一条降级路径。
    """

    widget: QWidget
    render_first_frame: Callable[[], None]
    close: Callable[[], None]
    plotter: object | None = None  # GL 后端上屏/拾取用（pyvista Plotter / QtInteractor）


def default_gl_factory(parent: QWidget) -> GLViewportHandle:
    """probe4b 已验证配方；pyvista/pyvistaqt 延迟到本函数导入，导入失败同样降级。"""
    from pyvistaqt import QtInteractor  # GL 环节之一：延迟导入

    inter = QtInteractor(parent)
    inter.ren_win.SetOffScreenRendering(1)  # probe4b 第 44 行：VTK 离屏渲染
    inter.add_axes()

    # 临时诊断（2026-11-04 相机弹回归因）：滚轮事件前后的相机状态。
    # 全程 try 保护——诊断绝不允许破坏视口（首轮实测：pyvista iren 包装对象
    # 无 AddObserver，异常曾把 GL 视口整个降级 Agg）。
    try:
        _iren_vtk = inter.ren_win.GetInteractor()

        def _wheel_diag_pre(obj, ev) -> None:
            try:
                log.info("[诊断] %s 前 相机=%s camera_set=%s", ev,
                         tuple(round(v, 4) for v in inter.camera.position), inter.camera_set)
            except Exception:  # noqa: BLE001
                pass

        def _wheel_diag_post(obj, ev) -> None:
            try:
                log.info("[诊断] %s 后 相机=%s camera_set=%s", ev,
                         tuple(round(v, 4) for v in inter.camera.position), inter.camera_set)
            except Exception:  # noqa: BLE001
                pass

        _iren_vtk.AddObserver("MouseWheelForwardEvent", _wheel_diag_pre, 100.0)
        _iren_vtk.AddObserver("MouseWheelForwardEvent", _wheel_diag_post)
        _iren_vtk.AddObserver("MouseWheelBackwardEvent", _wheel_diag_pre, 100.0)
        _iren_vtk.AddObserver("MouseWheelBackwardEvent", _wheel_diag_post)
    except Exception:  # noqa: BLE001
        log.warning("滚轮诊断观察器安装失败（不影响视口）", exc_info=True)

    def render_first_frame() -> None:
        inter.render()
        img = inter.screenshot(return_img=True)  # 强制帧缓冲回读，暴露首帧 GL 故障
        if img is None or getattr(img, "size", 0) == 0:
            raise RuntimeError("首帧渲染未返回有效帧缓冲")

    def close() -> None:
        try:
            inter.close()
        except Exception:
            log.debug("QtInteractor 关闭异常（已忽略）", exc_info=True)

    return GLViewportHandle(
        widget=inter.interactor,
        render_first_frame=render_first_frame,
        close=close,
        plotter=inter,
    )


class AggFallbackCanvas(QWidget):
    """Agg 降级画布：三角形/MeshData → matplotlib Agg PNG → QPixmap，零 GL（probe8 配方）。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self._label = QLabel(
            "渲染后端=agg_fallback（meshio + matplotlib Agg，无 GL）\n等待网格…"
        )
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._label.setStyleSheet("color: #808080;")
        lay.addWidget(self._label)
        self.last_png_bytes: bytes | None = None

    def show_mesh(self, mesh: MeshData, title: str = "") -> str:
        faces = boundary_triangles(mesh)
        scalars = None
        scalar_label = ""
        if "U_mag" in mesh.point_data:
            scalars = mesh.point_data["U_mag"]
            scalar_label = "|U| [mm]"
        elif mesh.point_data:
            scalars = next(iter(mesh.point_data.values()))
        return self.show_tris(mesh.points, faces, scalars=scalars, title=title, scalar_label=scalar_label)

    def show_tris(
        self,
        points: np.ndarray,
        tris: np.ndarray,
        scalars: np.ndarray | None = None,
        title: str = "",
        scalar_label: str = "",
    ) -> str:
        # 延迟导入 report 层：保证 GL 路径完全不含 matplotlib
        from ..report.plot_aggs import render_mesh_png

        png = render_mesh_png(points, tris, scalars=scalars, title=title, scalar_label=scalar_label)
        return self.show_png_bytes(png)

    def show_png_bytes(self, png: bytes) -> str:
        """直接显示 PNG 字节（结果云图等渲染产物）。"""
        pix = QPixmap()
        if not pix.loadFromData(png):
            raise RuntimeError("PNG 无法解码为 QPixmap")
        self.last_png_bytes = png
        self._label.setStyleSheet("")
        self._label.setPixmap(
            pix.scaled(
                self._label.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )
        return f"png_bytes={len(png)}"


class ViewportHost(QWidget):
    """视口宿主：懒加载；GL 尝试整体包在一个 try（构造+安装+首帧）；失败→Agg。"""

    face_picked = Signal(int)  # 左键拾取命中：occ 面 tag；未命中 emit(-1)
    hover_face_changed = Signal(int)  # 悬停命中变化：occ 面 tag；离开/未命中 emit(-1)（节流后）
    agg_pick_attempted = Signal()  # Agg 降级画布上的点选尝试（无拾取能力，主窗口提示需 GL）

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.backend = ViewportBackend.PENDING
        self._factory: Callable[[QWidget], GLViewportHandle] | None = None
        self._last_error = ""
        self._pending_mesh: tuple[MeshData, str] | None = None
        self._gl_handle: GLViewportHandle | None = None
        self._canvas: AggFallbackCanvas | None = None
        self._current: QWidget | None = None
        # 几何上屏 / 选面交互状态（仅 GL 后端使用）
        self._gl_mesh = None  # pyvista PolyData（cell_data["face_tag"]）
        self._gl_face_tags: np.ndarray | None = None
        self._result_face_tags: np.ndarray | None = None  # 结果云图附着的 occ tag（模块39）
        self._mesh_view_active = False  # 纯网格视图无 occ tag：点击给出指引而非"未命中"（2026-11-04 用户反馈）
        self._mesh_actor = None  # 当前主体（几何/网格/云图 PolyData）actor 句柄
        self._text_actor = None  # 当前标题文本 actor（vtkCornerAnnotation/vtkTextActor）
        self._scalar_bar_actor = None  # 当前云图色标 actor（vtkScalarBarActor）
        self._highlight_actor = None
        self._highlight_face_tag: int | None = None
        self._highlight_tri_count = 0
        self.last_pick: int | None = None  # 最近一次左键拾取结果（None=未发生/未命中）
        # 悬停拾取状态（点面交互核心①：节流 + 仅在命中面变化时发信号）
        self._hover_last_ms: float = 0.0
        self._hover_last_xy: tuple[int, int] = (-10**6, -10**6)
        self._last_hover_tag: int | None = None

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self._placeholder = QLabel("三维视口未加载（懒加载：首次显示时创建）")
        self._placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._placeholder.setStyleSheet("color: #606060;")
        lay.addWidget(self._placeholder)
        self._current = self._placeholder
        self.setMinimumHeight(200)

    # ---------- 注入点（验收故障注入用） ----------
    def set_factory(self, factory: Callable[[QWidget], GLViewportHandle]) -> None:
        """替换视口工厂；传 None 恢复默认 GL 工厂。"""
        self._factory = factory

    @property
    def last_error(self) -> str:
        return self._last_error

    @property
    def current_widget(self) -> QWidget | None:
        return self._current

    # ---------- 懒加载 ----------
    def ensure_viewport(self) -> ViewportBackend:
        """按需创建视口；幂等。GL 失败（任一环节）→ Agg 降级，永不抛出。"""
        if self.backend is not ViewportBackend.PENDING:
            return self.backend

        factory = self._factory or default_gl_factory
        handle: GLViewportHandle | None = None
        try:
            handle = factory(self)          # ① 构造期（probe4：offscreen 在此抛）
            self._show_widget(handle.widget)  # ② 安装进布局
            handle.widget.installEventFilter(self)  # 左键拾取交互（产品选面）
            handle.render_first_frame()     # ③ 首帧渲染期 —— 与构造同一个 try
            self._gl_handle = handle
            self.backend = ViewportBackend.GL
            log.info(
                "渲染后端=%s（GL 视口就绪：native windows 平台 + SetOffScreenRendering(1)，probe4b 配方）",
                self.backend.value,
            )
            self._sync_gl_size()  # 离屏窗口不会自动跟随部件尺寸，必须显式同步
        except Exception as exc:  # noqa: BLE001 - 任何 GL 环节失败都必须降级
            self._last_error = f"{type(exc).__name__}: {exc}"
            log.warning("GL 视口在构造/安装/首帧环节失败: %s -> 降级 agg_fallback", self._last_error)
            try:
                if handle is not None:
                    handle.widget.removeEventFilter(self)
            except Exception:
                pass
            if handle is not None:
                try:
                    handle.close()
                except Exception:
                    log.debug("失败 GL 句柄清理异常（已忽略）", exc_info=True)
            self._install_agg_fallback()
            self.backend = ViewportBackend.AGG_FALLBACK
        self._apply_pending_mesh()
        return self.backend

    # ---------- 几何/网格上屏 ----------
    def show_mesh(self, mesh: MeshData, title: str = "") -> str:
        if self.backend is ViewportBackend.PENDING:
            self._pending_mesh = (mesh, title)
            self.ensure_viewport()
            return "queued->applied" if self._pending_mesh is None else "queued"
        if self.backend is ViewportBackend.GL:
            return self._show_mesh_gl(mesh, title)
        if self.backend is ViewportBackend.AGG_FALLBACK and self._canvas is not None:
            return self._canvas.show_mesh(mesh, title)
        return f"no-backend:{self.backend.value}"

    def show_geometry(
        self,
        solid_model,  # cad.SolidModel（避免循环类型依赖）
        point_data: dict[str, np.ndarray] | None = None,
        scalar_name: str | None = None,
        *,
        tess: tuple[np.ndarray, np.ndarray, np.ndarray] | None = None,
    ) -> str:
        """STEP 几何上屏：表面细分 → GL(PolyData+面tag，可选云图) / Agg(三角形出图)。

        tess 可选预计算细分 (pts, tris, ftags)——部件着色/隔离（show_geometry_parts）
        传入按可见性过滤后的细分，复用本函数同一条 GL/Agg 链路。
        """
        pts, tris, ftags = tess if tess is not None else solid_model.tessellate_faces()
        if self.backend is ViewportBackend.PENDING:
            self.ensure_viewport()
        if self.backend is ViewportBackend.GL:
            return self._show_geometry_gl(pts, tris, ftags, point_data or {}, scalar_name)
        if self.backend is ViewportBackend.AGG_FALLBACK and self._canvas is not None:
            scalars = None
            if point_data and scalar_name and scalar_name in point_data:
                scalars = point_data[scalar_name]
            return self._canvas.show_tris(pts, tris, scalars=scalars, title=solid_model.path.name)
        return f"no-backend:{self.backend.value}"

    def show_geometry_parts(
        self,
        solid_model,  # cad.AssemblyModel（含 parts/solid_tag 归属）
        visible_part_tags: list[int] | None = None,
    ) -> str:
        """按部件着色 / 隔离显示（模块 13；复用 show_geometry 同一链路）。

        - 着色：每点部件序标量（"part"）→ viridis 两部件两色；
        - 隔离：visible_part_tags 给定时只显示这些 occ 实体 tag 的三角形；
        - 三角形→部件归属按 FaceInfo.solid_tag（face_owner 语义，模块 12 固化）。
        """
        import numpy as np

        pts, tris, ftags = solid_model.tessellate_faces()
        tris = np.asarray(tris, dtype=np.int64)
        ftags = np.asarray(ftags, dtype=np.int64)
        face2solid = {
            int(f.tag): (int(f.solid_tag) if f.solid_tag is not None else -1)
            for f in solid_model.faces
        }
        tri_solid = np.asarray([face2solid.get(int(t), -1) for t in ftags], dtype=np.int64)
        if visible_part_tags is None:
            keep = np.ones(len(tris), dtype=bool)
        else:
            keep = np.isin(tri_solid, [int(t) for t in visible_part_tags])
        if not keep.any():
            log.warning("部件上屏：可见部件选择为空（按全部部件显示）")
            keep = np.ones(len(tris), dtype=bool)
        tris_m, ftags_m, solid_m = tris[keep], ftags[keep], tri_solid[keep]

        shown = sorted({int(s) for s in solid_m if s >= 0})
        rank = {t: i for i, t in enumerate(shown)}
        scalars = np.zeros(len(pts), dtype=float)
        filled = np.zeros(len(pts), dtype=bool)
        for s, tri in zip(solid_m, tris_m):
            r = rank.get(int(s), 0)
            for v in tri:
                if not filled[v]:
                    scalars[v] = r
                    filled[v] = True
        n_parts = len(shown)
        total_parts = len({s for s in face2solid.values() if s >= 0})
        mode = "（隔离/部分显示）" if n_parts < total_parts else "（按部件着色）"
        info = self.show_geometry(
            solid_model,
            point_data={"part": scalars},
            scalar_name="part",
            tess=(pts, tris_m, ftags_m),
        )
        log.info("部件上屏: %d/%d 部件%s（part 标量 0..%d）", n_parts, total_parts, mode, max(n_parts - 1, 0))
        return info + f"; parts_shown={n_parts}"

    # ---------- GL 上屏状态机（当前显示唯一：换视图先清上一轮） ----------
    def _remove_mesh_actor(self) -> None:
        """移除上一轮主体 mesh actor（几何/网格/云图三类上屏共用）。"""
        if self._mesh_actor is None:
            return
        plotter = self._gl_handle.plotter if self._gl_handle is not None else None
        if plotter is not None:
            try:
                plotter.remove_actor(self._mesh_actor, reset_camera=False, render=False)
            except Exception:
                log.debug("移除上一轮 mesh actor 失败（忽略）", exc_info=True)
        self._mesh_actor = None

    def _clear_overlay_actors(self) -> None:
        """清除上一轮标题文本与云图色标 actor（先按句柄，再兜底清扫残留）。

        文本 actor 是 vtkActor2D 集合成员（不在 GetActors 3D 集合里）；
        pyvista add_text 默认位置返回 vtkCornerAnnotation——VTK 9.7 中该类
        IsA("vtkTextActor") 为 False（实测），计数/清扫必须两类都认。
        """
        plotter = self._gl_handle.plotter if self._gl_handle is not None else None
        if plotter is None:
            self._text_actor = None
            self._scalar_bar_actor = None
            return
        for attr in ("_text_actor", "_scalar_bar_actor"):
            actor = getattr(self, attr)
            if actor is not None:
                try:
                    plotter.remove_actor(actor, reset_camera=False, render=False)
                except Exception:
                    log.debug("移除上一轮 %s 失败（忽略）", attr, exc_info=True)
            setattr(self, attr, None)
        # 兜底清扫：句柄之外若仍有残留（如 add_mesh 自建色标未捕获的路径），
        # 一并移除——保证「当前显示唯一」不依赖每条上屏路径都恰好记了账。
        self._last_hover_tag = None  # 视图已切换：悬停命中状态随之失效
        try:
            ren = plotter.renderer
            collection = ren.GetActors2D()
            stale = []
            for i in range(collection.GetNumberOfItems()):
                actor = collection.GetItemAsObject(i)
                if actor is not None and self._is_text_or_bar_actor(actor):
                    stale.append(actor)
            for actor in stale:
                try:
                    plotter.remove_actor(actor, reset_camera=False, render=False)
                except Exception:
                    log.debug("清扫残留 2D actor 失败（忽略）", exc_info=True)
        except Exception:
            log.debug("清扫 2D actor 集合异常（忽略）", exc_info=True)

    @staticmethod
    def _is_text_or_bar_actor(actor) -> bool:  # noqa: ANN001
        return actor.IsA("vtkTextActor") or actor.IsA("vtkCornerAnnotation") or actor.IsA(
            "vtkScalarBarActor"
        )

    def _add_title_text(self, plotter, text: str, font_size: int = 9):  # noqa: ANN001, ANN201
        """add_text + 中文字体并保存句柄（评审 high-1：GL 内中文渲染为方块）。

        pyvista add_text 的 font_file 参数内部即 vtkTextProperty.SetFontFile
        （VTK_FONT_FILE 族，实测 msyh.ttc 可读、0 条 glyph 告警）；文件缺失时
        回退默认字体（仅观感降级，不阻塞上屏）。
        """
        _ensure_vtk_freetype_backend()  # 默认后端忽略 SetFontFile（实测），先强制
        kwargs: dict = {}
        if CJK_FONT_FILE.is_file():
            kwargs["font_file"] = str(CJK_FONT_FILE)
        try:
            actor = plotter.add_text(text, font_size=font_size, render=False, **kwargs)
        except Exception:  # noqa: BLE001 - 字体参数异常时回退默认字体
            log.debug("add_text 挂中文字体失败（回退默认字体）", exc_info=True)
            actor = plotter.add_text(text, font_size=font_size, render=False)
        self._text_actor = actor
        return actor

    def _capture_scalar_bar_actor(self, plotter) -> None:  # noqa: ANN001
        """记录本轮 add_mesh 产生的色标 actor 句柄（供下一轮清除）+ 挂中文字体。"""
        try:
            ren = plotter.renderer
            collection = ren.GetActors2D()
            for i in range(collection.GetNumberOfItems()):
                actor = collection.GetItemAsObject(i)
                if actor is not None and actor.IsA("vtkScalarBarActor"):
                    self._scalar_bar_actor = actor
                    self._apply_scalar_bar_font(actor)
                    return
        except Exception:
            log.debug("捕获色标 actor 失败（仅影响下轮清除）", exc_info=True)

    @staticmethod
    def _apply_scalar_bar_font(actor) -> None:  # noqa: ANN001
        """色标标题/标签挂中文字体（色标标题可能含中文，如振型幅值）。"""
        _ensure_vtk_freetype_backend()  # 默认后端忽略 SetFontFile（实测），先强制
        if not CJK_FONT_FILE.is_file():
            return
        try:
            for getter_name in (
                "GetTitleTextProperty",  # VTK 9.7 实测名称（GetTitleProperty 不存在）
                "GetLabelTextProperty",
                "GetAnnotationTextProperty",
            ):
                getter = getattr(actor, getter_name, None)
                if getter is None:
                    continue
                prop = getter()
                if prop is not None:
                    prop.SetFontFile(str(CJK_FONT_FILE))
        except Exception:
            log.debug("色标字体设置失败（保持默认）", exc_info=True)

    def scene_2d_actor_state(self) -> dict:
        """GL 场景 2D actor 盘点（验收/调试用；遍历口径同 scalar_bar_visible）。

        返回 {"text": n, "text_visible": n, "scalar_bar": n, "scalar_bar_visible": n,
        "texts": [..], "scalar_bar_titles": [..]}；text 计 vtkTextActor 与
        vtkCornerAnnotation 两类（VTK 9.7 后者不是前者的子类，实测）。
        """
        state: dict = {
            "text": 0, "text_visible": 0, "scalar_bar": 0, "scalar_bar_visible": 0,
            "texts": [], "scalar_bar_titles": [],
        }
        if self.backend is not ViewportBackend.GL or self._gl_handle is None:
            return state
        try:
            collection = self._gl_handle.plotter.renderer.GetActors2D()
        except Exception:
            return state
        for i in range(collection.GetNumberOfItems()):
            actor = collection.GetItemAsObject(i)
            if actor is None:
                continue
            if actor.IsA("vtkScalarBarActor"):
                state["scalar_bar"] += 1
                state["scalar_bar_visible"] += int(bool(actor.GetVisibility()))
                state["scalar_bar_titles"].append(str(actor.GetTitle()))
            elif actor.IsA("vtkTextActor") or actor.IsA("vtkCornerAnnotation"):
                state["text"] += 1
                state["text_visible"] += int(bool(actor.GetVisibility()))
                state["texts"].append(self._read_actor_text(actor))
        return state

    @staticmethod
    def _read_actor_text(actor) -> str:  # noqa: ANN001
        """尽力读取文本 actor 的内容（vtkTextActor.GetInput / CornerAnnotation 各角）。"""
        try:
            if actor.IsA("vtkCornerAnnotation"):
                parts = []
                for pos in range(8):
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

    def _show_geometry_gl(
        self,
        pts: np.ndarray,
        tris: np.ndarray,
        ftags: np.ndarray,
        point_data: dict[str, np.ndarray],
        scalar_name: str | None,
    ) -> str:
        try:
            import pyvista as pv

            faces = np.hstack(
                [np.full((len(tris), 1), 3, dtype=np.int64), np.asarray(tris, dtype=np.int64)]
            ).ravel()
            pd = pv.PolyData(np.ascontiguousarray(pts, dtype=np.float64), faces)
            pd.cell_data["face_tag"] = np.asarray(ftags, dtype=np.int64)
            for key, arr in point_data.items():
                pd.point_data[key] = np.asarray(arr, dtype=np.float64)
            plotter = self._gl_handle.plotter if self._gl_handle is not None else None
            if plotter is None:
                return "gl geometry failed: no plotter handle"
            self._clear_highlight()
            self._remove_mesh_actor()
            self._clear_overlay_actors()  # 换视图：清上一轮标题/色标（状态机）
            if scalar_name and scalar_name in pd.point_data:
                self._mesh_actor = plotter.add_mesh(
                    pd, scalars=scalar_name, cmap="viridis", show_edges=False
                )
            else:
                self._mesh_actor = plotter.add_mesh(pd, color="#b0bec5", show_edges=False)
            self._capture_scalar_bar_actor(plotter)  # render 前捕获并挂字体
            self._gl_mesh = pd
            self._gl_face_tags = np.asarray(ftags, dtype=np.int64)
            self._mesh_view_active = False
            plotter.reset_camera()
            plotter.render()
            self._capture_scalar_bar_actor(plotter)  # 渲染后复查句柄（延迟建色标情形）
            extra = f", scalars={scalar_name}" if scalar_name else ""
            return f"gl geometry ok ({len(pts)} points, {len(tris)} tris{extra})"
        except Exception as exc:  # noqa: BLE001 - 几何上屏失败不推翻已建视口
            log.warning("GL 几何上屏失败（视口保留，仅记录）: %s: %s", type(exc).__name__, exc)
            return f"gl geometry failed: {type(exc).__name__}: {exc}"

    def _show_mesh_gl(self, mesh: MeshData, title: str) -> str:
        try:
            import meshio
            import pyvista as pv

            mio = meshio.Mesh(
                points=mesh.points,
                cells=dict(mesh.cells),
                point_data={k: np.asarray(v) for k, v in mesh.point_data.items()},
            )
            grid = pv.from_meshio(mio)
            plotter = self._gl_handle.plotter if self._gl_handle is not None else None
            if plotter is None:
                return "gl add_mesh failed: no plotter handle"
            scalar_name = (
                "U_mag" if "U_mag" in mesh.point_data
                else next(iter(mesh.point_data), None)
            )
            self._clear_highlight()
            self._remove_mesh_actor()
            self._clear_overlay_actors()  # 换视图：清上一轮标题/色标（状态机）
            if scalar_name:
                self._mesh_actor = plotter.add_mesh(
                    grid, scalars=scalar_name, show_edges=True, cmap="viridis"
                )
            else:
                self._mesh_actor = plotter.add_mesh(grid, show_edges=True)
            self._capture_scalar_bar_actor(plotter)  # render 前捕获并挂字体
            # 纯网格视图未带 occ 面 tag（选面在几何视图进行），拾取状态随主体清空
            self._gl_mesh = None
            self._gl_face_tags = None
            self._mesh_view_active = True  # 网格视图：点击时给出明确指引而非"未命中表面"
            if title:
                self._add_title_text(plotter, title, font_size=9)
            plotter.reset_camera()
            plotter.render()
            self._capture_scalar_bar_actor(plotter)  # 渲染后复查句柄（延迟建色标情形）
            return f"gl add_mesh ok ({grid.n_points} points, {grid.n_cells} cells)"
        except Exception as exc:  # noqa: BLE001 - 网格上屏失败不推翻已建视口
            log.warning("GL 网格上屏失败（视口保留，仅记录）: %s: %s", type(exc).__name__, exc)
            return f"gl add_mesh failed: {type(exc).__name__}: {exc}"

    # ---------- 相机 / 投影 / 截图（GL 专用，Agg 下为空操作） ----------
    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        super().resizeEvent(event)
        try:
            self._sync_gl_size()
        except Exception:
            log.debug("渲染窗口尺寸同步异常", exc_info=True)

    def _sync_gl_size(self) -> bool:
        """离屏渲染窗口不会自动跟随部件尺寸（实测停在创建时 100×30），
        必须显式 SetSize 同步，否则投影/拾取坐标系与部件像素错位。"""
        if self.backend is not ViewportBackend.GL or self._gl_handle is None:
            return False
        w = self._current.width() if self._current is not None else self.width()
        h = self._current.height() if self._current is not None else self.height()
        if w < 10 or h < 10:
            return False
        ren_win = self._gl_handle.plotter.ren_win
        cw, ch = (int(v) for v in ren_win.GetSize())
        if (cw, ch) != (int(w), int(h)):
            try:  # 临时诊断（2026-11-04 相机弹回归因）：SetSize 前后相机状态
                _p = self._gl_handle.plotter
                log.info("[诊断] _sync_gl_size SetSize (%d,%d)->(%d,%d) 前 相机=%s camera_set=%s",
                         cw, ch, w, h, tuple(round(v, 4) for v in _p.camera.position), _p.camera_set)
            except Exception:  # noqa: BLE001
                pass
            ren_win.SetSize(int(w), int(h))
            self._gl_handle.plotter.render()
            try:
                _p = self._gl_handle.plotter
                log.info("[诊断] _sync_gl_size SetSize 后 相机=%s camera_set=%s",
                         tuple(round(v, 4) for v in _p.camera.position), _p.camera_set)
            except Exception:  # noqa: BLE001
                pass
        return True

    def set_camera(self, position, focal, up) -> None:  # noqa: ANN001
        """显式设置相机（验收用固定视角；不设则用 add_mesh 的默认相机）。"""
        if self.backend is not ViewportBackend.GL or self._gl_handle is None:
            return
        self._gl_handle.plotter.camera_position = [tuple(position), tuple(focal), tuple(up)]
        self._gl_handle.plotter.render()

    def project_point(self, world) -> tuple[int, int] | None:  # noqa: ANN001
        """世界坐标 → 视口部件屏幕坐标（Qt 左上原点）；出界返回 None。"""
        if self.backend is not ViewportBackend.GL or self._gl_handle is None:
            return None
        self._sync_gl_size()
        ren = self._gl_handle.plotter.renderer
        ren.SetWorldPoint(float(world[0]), float(world[1]), float(world[2]), 1.0)
        ren.WorldToDisplay()
        dx, dy, _dz = ren.GetDisplayPoint()
        vh = int(ren.GetSize()[1])  # VTK 渲染视口高（左下原点）
        qx, qy = int(round(dx)), int(round(vh - dy))
        w = self._current.width() if self._current is not None else self.width()
        h = self._current.height() if self._current is not None else self.height()
        if not (0 <= qx < w and 0 <= qy < h):
            return None
        return qx, qy

    def screenshot(self, path) -> np.ndarray | None:  # noqa: ANN001
        """渲染并保存截图；返回图像数组（仅 GL 后端）。"""
        if self.backend is not ViewportBackend.GL or self._gl_handle is None:
            return None
        self._sync_gl_size()
        self._gl_handle.plotter.render()
        return self._gl_handle.plotter.screenshot(str(path))

    def scalar_bar_visible(self) -> bool:
        """场景中是否存在可见的 vtkScalarBarActor（云图色标）。

        注意：色标是 vtkActor2D，挂在 renderer 的 2D 集合（GetActors2D），
        不在 GetActors()（3D 主体）里——实测踩过。
        """
        if self.backend is not ViewportBackend.GL or self._gl_handle is None:
            return False
        ren = self._gl_handle.plotter.renderer
        for collection in (ren.GetActors2D(), ren.GetActors()):
            for i in range(collection.GetNumberOfItems()):
                actor = collection.GetItemAsObject(i)
                if actor is not None and actor.IsA("vtkScalarBarActor") and actor.GetVisibility():
                    return True
        return False

    # ---------- 产品选面交互（与 cad.pick_face 纯几何 API 分开） ----------
    def eventFilter(self, obj, event):  # noqa: N802 - Qt 命名
        et = event.type()
        if (
            et == QEvent.Type.MouseButtonPress
            and getattr(event, "button", None) is not None
            and event.button() == Qt.MouseButton.LeftButton
        ):
            if self.backend is ViewportBackend.GL:
                try:
                    self._handle_click(int(event.position().x()), int(event.position().y()))
                except Exception:
                    log.warning("视口点击处理异常", exc_info=True)
            elif self.backend is ViewportBackend.AGG_FALLBACK:
                # Agg 降级画布无拾取能力（评审「选面」项）：提示需要 GL 视口
                self.agg_pick_attempted.emit()
        elif et == QEvent.Type.MouseMove and self.backend is ViewportBackend.GL:
            # 悬停反馈只在无按键拖动时进行（orbit 拖拽中不拾取）
            if getattr(event, "buttons", None) is not None and event.buttons() == Qt.MouseButton.NoButton:
                try:
                    self._handle_hover(int(event.position().x()), int(event.position().y()))
                except Exception:
                    log.debug("悬停拾取异常", exc_info=True)
        return super().eventFilter(obj, event)  # 不消费：VTK orbit 交互照常

    def _handle_hover(self, x: int, y: int) -> None:
        """悬停拾取（节流）：命中面变化时 emit hover_face_changed(tag/-1)。

        节流 = 时间间隔 < _HOVER_MIN_INTERVAL_S 且位移 < _HOVER_MIN_MOVE_PX
        时跳过（二者满足其一即重新拾取）；只有 tag 相对上一次变化才发信号，
        避免高频重复。纯网格/云图视图（_gl_mesh 无面 tag）按未命中处理。
        """
        now = time.monotonic()
        moved = max(abs(x - self._hover_last_xy[0]), abs(y - self._hover_last_xy[1]))
        if now - self._hover_last_ms < _HOVER_MIN_INTERVAL_S and moved < _HOVER_MIN_MOVE_PX:
            return
        self._hover_last_ms = now
        self._hover_last_xy = (x, y)
        tag: int | None = None
        if self._gl_mesh is not None:
            tag = self._pick_cell_face(x, y)
        new_tag = int(tag) if tag is not None else -1
        if new_tag != self._last_hover_tag:
            self._last_hover_tag = new_tag
            self.hover_face_changed.emit(new_tag)

    def _handle_click(self, x: int, y: int) -> int | None:
        """屏幕点 → 命中单元 → occ 面 tag → 高亮；未命中清除高亮并 emit(-1)。

        结果云图视图（模块39）：cell_data["face_tag"] 中 -1 = 无挂接定义面
        （自由边界面）——命中时不高亮、emit(-1)，由主窗口按视图模式出
        『无挂接定义』卡片（viewport.last_pick==-1 与空点 None 可区分）。
        """
        self.last_pick = None
        try:  # 临时诊断（2026-11-04 相机弹回归因）：点击路径各步的相机状态
            _p = self._gl_handle.plotter if self._gl_handle is not None else None
            if _p is not None:
                log.info("[诊断] 点击前 相机=%s camera_set=%s",
                         tuple(round(v, 4) for v in _p.camera.position), _p.camera_set)
        except Exception:  # noqa: BLE001
            pass
        tag = self._pick_cell_face(x, y)
        try:  # 拾取后（内含 _sync_gl_size 的 SetSize+render）
            _p = self._gl_handle.plotter if self._gl_handle is not None else None
            if _p is not None:
                log.info("[诊断] 拾取后 相机=%s camera_set=%s",
                         tuple(round(v, 4) for v in _p.camera.position), _p.camera_set)
        except Exception:  # noqa: BLE001
            pass
        self.last_pick = tag
        if tag is None:
            self.clear_highlight()
            self.face_picked.emit(-1)
            if self._mesh_view_active:
                log.info("视口点击 (%d,%d)：网格视图无 occ 面 tag，选面请在几何视图进行", x, y)
            else:
                log.info("视口点击 (%d,%d)：未命中表面，已清除高亮", x, y)
        elif int(tag) <= 0:
            self.clear_highlight()
            self.face_picked.emit(-1)
            log.info("视口点击 (%d,%d)：命中无挂接定义面（自由边界面）", x, y)
        else:
            self.highlight_face(tag)
            try:  # 高亮后
                _p = self._gl_handle.plotter if self._gl_handle is not None else None
                if _p is not None:
                    log.info("[诊断] 高亮后 相机=%s camera_set=%s",
                             tuple(round(v, 4) for v in _p.camera.position), _p.camera_set)
            except Exception:  # noqa: BLE001
                pass
            self.face_picked.emit(tag)
            log.info("视口点击 (%d,%d)：选中面 tag=%d（occ 面）", x, y, tag)
        return tag

    def _pick_cell_face(self, x: int, y: int) -> int | None:
        """vtkCellPicker 屏幕拾取 → 命中单元所属 occ 面 tag（无命中返回 None）。

        Qt 事件坐标是左上原点；vtkPicker.Pick 吃 VTK 显示坐标（左下原点），
        必须做 y 翻转（前提：ren_win 尺寸已与部件同步，见 _sync_gl_size）。
        """
        import vtk

        plotter = self._gl_handle.plotter if self._gl_handle is not None else None
        if plotter is None or self._gl_mesh is None:
            return None
        self._sync_gl_size()
        _w, vh = (int(v) for v in plotter.ren_win.GetSize())
        picker = vtk.vtkCellPicker()
        picker.SetTolerance(0.02)
        if not picker.Pick(int(x), int(vh) - int(y), 0, plotter.renderer):
            return None
        actor = picker.GetActor()
        cell_id = picker.GetCellId()
        if actor is None or cell_id < 0:
            return None
        mapper = actor.GetMapper()
        ds = mapper.GetInput() if mapper is not None else None
        if ds is None:
            return None
        arr = ds.GetCellData().GetArray("face_tag")
        if arr is None or cell_id >= arr.GetNumberOfTuples():
            return None
        return int(arr.GetTuple1(cell_id))

    def highlight_face(self, tag: int) -> None:
        """红色高亮 occ 面 tag 对应的三角形（仅 GL 后端；tag<=0 不高亮）。"""
        if self.backend is not ViewportBackend.GL or self._gl_mesh is None:
            return
        if int(tag) <= 0:
            log.debug("高亮跳过：tag=%d 不是正 occ 面 tag（无挂接定义/未命中）", tag)
            return
        if self._gl_face_tags is None or np.ndim(self._gl_face_tags) == 0:
            log.warning("高亮失败：当前网格未附面 tag 数组")
            return
        mask = np.where(np.asarray(self._gl_face_tags) == int(tag))[0]
        if len(mask) == 0:
            log.warning("高亮失败：face_tag=%d 不在当前网格中", tag)
            return
        self._clear_highlight()
        sub = self._gl_mesh.extract_cells(mask)
        sub.cell_data["face_tag"] = self._gl_face_tags[mask]
        plotter = self._gl_handle.plotter
        self._highlight_actor = plotter.add_mesh(
            sub,
            color="#e53935",
            opacity=0.9,
            show_edges=True,
            edge_color="black",
            reset_camera=False,  # 选面高亮绝不动相机——否则用户缩放/旋转后一点面视角即弹回（2026-11-03 真机复现的交互缺陷）
            pickable=False,  # 高亮不可拾取——否则点相邻面时射线擦到高亮边缘会返回旧面 tag（2026-11-04 用户反馈"有的面选不中/只选中一小块"）
        )
        self._highlight_face_tag = int(tag)
        self._highlight_tri_count = int(len(mask))
        plotter.render()

    def clear_highlight(self) -> None:
        self._clear_highlight()
        if self.backend is ViewportBackend.GL and self._gl_handle is not None:
            self._gl_handle.plotter.render()

    def _clear_highlight(self) -> None:
        if self._highlight_actor is not None and self._gl_handle is not None:
            try:
                self._gl_handle.plotter.remove_actor(self._highlight_actor)
            except Exception:
                pass
        self._highlight_actor = None
        self._highlight_face_tag = None
        self._highlight_tri_count = 0

    @property
    def highlight_face_tag(self) -> int | None:
        return self._highlight_face_tag

    @property
    def mesh_view_active(self) -> bool:
        """当前为纯网格视图（无 occ 面 tag，选面需回几何视图）。"""
        return self._mesh_view_active

    @property
    def highlight_tri_count(self) -> int:
        return self._highlight_tri_count

    def highlight_visible(self) -> bool:
        actor = self._highlight_actor
        return actor is not None and bool(actor.GetVisibility())

    def show_result_fields(self, pts, tris, u, s_mises, mode: str = "mises", scale: float = 1.0,
                           face_tags=None) -> str:  # noqa: ANN001
        """结果云图上屏（GL/Agg 双后端）：mode = "mises" | "displaced"。

        pts/tris 为显示表面（与 u/s_mises 行对齐——结果显示用体积网格边界面，
        与三场同源）；displaced 模式按 scale 变形并标注放大倍数与单位
        （调研报告 §4）；Agg 路径即 probe8 配方的 annotated 出图（验收 ⑤⑥）。
        face_tags 可选（模块39 结果云图点面溯源）：与 tris 行对齐的 occ 面 tag
        （角点节点集 ⊆ 面 NSET 归属；无挂接定义 = -1）——写入 cell_data 后
        结果视图拾取/高亮/卡片与几何视图同链路。
        """
        if self.backend is ViewportBackend.PENDING:
            self.ensure_viewport()
        u = np.asarray(u, dtype=float)
        s_mises = np.asarray(s_mises, dtype=float)
        if mode == "displaced":
            scalars = np.linalg.norm(u, axis=1)
            title, unit = "位移云图 |U|", "mm"
            scalar_label = "|U| [mm]"  # 色标 label 含单位（评审 high-2）
            deform = u
        else:
            scalars = s_mises
            title, unit = "von Mises 云图", "MPa"
            scalar_label = "von Mises [MPa]"
            deform = None
        if self.backend is ViewportBackend.GL:
            return self._show_field_gl(pts, tris, scalars, deform, scale, title, unit, scalar_label,
                                       face_tags=face_tags)
        if self.backend is ViewportBackend.AGG_FALLBACK and self._canvas is not None:
            from ..report.plot_aggs import render_field_png

            png, info = render_field_png(
                pts, tris, scalars, title=title, unit=unit,
                deform_u=deform, deform_scale=scale, scalar_label=unit,
            )
            self._canvas.show_png_bytes(png)
            return f"agg field ok ({info['annotation']}; max_u_mag={info['max_u_mag']})"
        return f"no-backend:{self.backend.value}"

    def _show_field_gl(self, pts, tris, scalars, deform_u, scale, title, unit, scalar_label="",
                       face_tags=None) -> str:  # noqa: ANN001
        try:
            import pyvista as pv

            plotter = self._gl_handle.plotter if self._gl_handle is not None else None
            if plotter is None:
                return "gl field failed: no plotter handle"
            disp_pts = pts if deform_u is None else pts + scale * deform_u
            faces = np.hstack(
                [np.full((len(tris), 1), 3, dtype=np.int64), np.asarray(tris, dtype=np.int64)]
            ).ravel()
            pd = pv.PolyData(np.ascontiguousarray(disp_pts, dtype=np.float64), faces)
            pd.point_data["scalars"] = np.asarray(scalars, dtype=float)
            self._clear_highlight()
            self._remove_mesh_actor()
            self._clear_overlay_actors()  # 换视图：清上一轮标题/色标（状态机）
            bar_kwargs: dict = {}
            if scalar_label:
                bar_kwargs["scalar_bar_args"] = {"title": scalar_label}  # 色标 label 含单位
            self._mesh_actor = plotter.add_mesh(pd, scalars="scalars", cmap="viridis", **bar_kwargs)
            self._capture_scalar_bar_actor(plotter)  # render 前捕获并挂字体
            if face_tags is not None and len(face_tags) == len(tris):
                # 模块39 结果云图点面溯源：按「角点节点集 ⊆ 面 NSET」归属的 occ tag
                # （无挂接定义 = -1）——结果视图拾取/高亮/卡片与几何视图同链路
                self._result_face_tags = np.asarray(face_tags, dtype=np.int64)
                pd.cell_data["face_tag"] = self._result_face_tags
                self._gl_face_tags = self._result_face_tags
            else:
                self._result_face_tags = None
                self._gl_face_tags = None  # 云图未附面 tag（拾取在几何视图进行）
            self._gl_mesh = pd
            self._mesh_view_active = False  # 结果云图：拾取走面 tag/无挂接定义链路
            ann = f"{title} | {unit} max={float(np.max(scalars)):.6g} {unit}"
            if deform_u is not None:
                ann += f" | 放大 {scale:g}x"
            self._add_title_text(plotter, ann, font_size=9)  # 挂中文字体 + 保存句柄
            plotter.reset_camera()
            plotter.render()
            self._capture_scalar_bar_actor(plotter)  # 渲染后捕获色标句柄（挂中文字体）
            return f"gl field ok ({ann})"
        except Exception as exc:  # noqa: BLE001 - 云图失败不推翻视口
            log.warning("GL 云图上屏失败（视口保留，仅记录）: %s: %s", type(exc).__name__, exc)
            return f"gl field failed: {type(exc).__name__}: {exc}"

    def show_modal_fields(self, points, tris, shape, display_amplitude, title, face_tags=None):
        """归一化振型的专用显示，不使用静力位移或应力标签。

        face_tags（模块56）：逐三角形 occ 面 tag——GL 后端写入 cell_data 后，
        点面拾取/高亮与几何/结果视图同链路（face_picked 信号）；None 时无 tag。"""
        if self.backend is ViewportBackend.PENDING:
            self.ensure_viewport()
        magnitude = np.linalg.norm(shape, axis=1)
        if self.backend is ViewportBackend.GL:
            return self._show_field_gl(
                points, tris, magnitude, shape, display_amplitude, title, "归一化",
                scalar_label="振型幅值 [归一化]", face_tags=face_tags,
            )
        if self.backend is ViewportBackend.AGG_FALLBACK and self._canvas is not None:
            from ..report.plot_aggs import render_field_png
            png, info = render_field_png(points,tris,magnitude,title=title,unit="归一化",
                                        deform_u=shape,deform_scale=display_amplitude)
            self._canvas.show_png_bytes(png)
            return info["annotation"]
        raise RuntimeError("振型显示后端不可用")

    # ---------- 内部 ----------
    def _install_agg_fallback(self) -> None:
        if self._canvas is None:
            self._canvas = AggFallbackCanvas(self)
            # Agg 画布上的点选尝试 → agg_pick_attempted（无拾取，提示需 GL）
            self._canvas.installEventFilter(self)
            self._canvas._label.installEventFilter(self)  # QLabel 忽略后事件才冒泡到画布
        self._show_widget(self._canvas)

    def _apply_pending_mesh(self) -> None:
        if self._pending_mesh is None:
            return
        mesh, title = self._pending_mesh
        self._pending_mesh = None
        try:
            self.show_mesh(mesh, title)
        except Exception:
            log.warning("待显网格应用失败", exc_info=True)

    def _show_widget(self, w: QWidget) -> None:
        if self._placeholder.isVisible():
            self._placeholder.hide()
        lay = self.layout()
        # 注意：GL 工厂创建的部件已带 parent（=本宿主），不能用 parent 判断是否入布局，
        # 否则部件永远停在 Qt 默认 100×30（实测踩过：离屏渲染窗口随之 100×30）。
        if lay.indexOf(w) < 0:
            lay.addWidget(w)
        old, self._current = self._current, w
        if old is not None and old is not w:
            try:
                lay.removeWidget(old)
                old.setParent(None)
                old.deleteLater()
            except RuntimeError:
                pass  # 底层 C++ 对象可能已随 GL 清理销毁
        w.show()
