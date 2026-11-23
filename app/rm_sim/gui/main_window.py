# -*- coding: utf-8 -*-
"""主窗口（GUI 层）：菜单 / 工况树 / 属性面板 / 状态栏 / 日志面板 / 视口。

主窗口构建本身不触碰任何 GL 资源：视口由 ViewportHost 懒加载
（首次 show 后的零延时定时器触发 ensure_viewport，或菜单「视图→创建/刷新视口」）。
"""
from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QEvent, Qt, QTimer, QProcess
from PySide6.QtWidgets import (
    QDockWidget,
    QFileDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..core.project import (
    Constraint,
    GravityLoad,
    LoadCase,
    Material,
    Project,
    Study,
    TractionLoad,
)
from ..mesh.mesh_data import MeshData
from .panels import (
    CaseTreePanel,
    EmptyStatePanel,
    LogPanel,
    PropertiesPanel,
    WorkflowBar,
    setup_gui_logging,
)
from .theme import apply_theme, chinese_button_box
from .viewport import ViewportBackend, ViewportHost

log = logging.getLogger("rm_sim.gui.main_window")


class MainWindow(QMainWindow):
    """统一主窗口框架；后续模块的入口动作（网格/求解/报告）挂到对应菜单。"""

    def __init__(self) -> None:
        super().__init__()
        from .. import __version__

        self.setWindowTitle(f"RoboMaster 仿真软件 — 零件、装配与模态 (rm_sim {__version__})")
        self.resize(1280, 800)
        self.solid_model = None  # cad.step_import.SolidModel，导入 STEP 后持有

        # 视口宿主（懒加载，不在此创建 GL 资源）
        self.viewport = ViewportHost(self)
        self.viewport.face_picked.connect(self._on_face_picked)
        self.viewport.hover_face_changed.connect(self._on_face_hover)
        self.viewport.agg_pick_attempted.connect(self._on_agg_pick_attempted)
        # 中央区 = 顶部 7 步流程条 + 页面栈（空状态占位页 ↔ 三维视口）
        self.workflow_bar = WorkflowBar(
            ["导入", "材料", "约束载荷", "网格", "求解", "结果", "报告"]
        )
        self.workflow_bar.step_clicked.connect(self._on_workflow_step_clicked)
        self.empty_state = EmptyStatePanel()
        self.empty_state.import_requested.connect(self._on_import_step)
        self.empty_state.open_example_requested.connect(self._on_open_example_project)
        self.empty_state.open_docs_requested.connect(self._on_open_usage_docs)
        self._central_stack = QStackedWidget()
        self._central_stack.addWidget(self.empty_state)  # index 0：首启空状态
        self._central_stack.addWidget(self.viewport)     # index 1：三维视口
        central = QWidget()
        central_lay = QVBoxLayout(central)
        central_lay.setContentsMargins(0, 0, 0, 0)
        central_lay.setSpacing(0)
        central_lay.addWidget(self.workflow_bar)
        central_lay.addWidget(self._central_stack)
        self.setCentralWidget(central)

        # 日志面板（先于面板构建，构建过程日志即可见）
        self.log_panel = LogPanel()
        setup_gui_logging(self.log_panel)

        # 左：装配树（工况树扩展）；右：属性面板；下：日志面板
        self.case_tree = CaseTreePanel()
        self.case_tree.part_isolate_requested.connect(self._on_isolate_part)
        self.case_tree.show_all_requested.connect(self._on_show_all_parts)
        self.case_tree.part_renamed.connect(self._on_part_renamed)
        self.tree_dock = QDockWidget("装配树", self)
        self.tree_dock.setWidget(self.case_tree)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.tree_dock)

        self.properties = PropertiesPanel()
        self.properties.import_requested.connect(self._on_import_step)
        self.props_dock = QDockWidget("属性", self)
        self.props_dock.setWidget(self.properties)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.props_dock)

        self.log_dock = QDockWidget("日志", self)
        self.log_dock.setWidget(self.log_panel)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.log_dock)

        self._build_menus()
        self._build_statusbar()
        self._build_face_interactions()

        self.project: Project = self._demo_project()
        self._refresh_project_views()
        self._viewport_ensure_scheduled = False
        self._scheduler = None          # solver.tasks.SolveScheduler（懒创建）
        self._solve_task = None         # 当前 GUI 求解任务
        self._solve_timer: QTimer | None = None  # 200ms 心跳轮询
        self._solve_cancel_button = None
        self._close_after_solve = False
        self._workflow_scheduler = None
        self._workflow_task = None
        self._workflow_timer = None
        self._workflow_project = None
        self._workflow_model = None
        self._result_surfaces = {}
        self._result_fields = None      # solver.fields.ResultFields（求解完成后回收）
        self._result_surface = None     # (pts, tris) 结果显示表面（体积网格边界面）
        self._solve_mesh_result = None  # 最近一次网格 MeshResult（归档用）
        self._solve_mesh_stats = None
        self._last_archive = None       # 最近一次归档 {case_dir, report_md, ...}
        self._last_nset_face_tags: dict[str, int] = {}  # 最近一次网格的 {NSET: occ面tag}
        self._prepared = None
        self._part_views = {}
        self._selected_face_tag = None
        self._face_pick_history: list[int] = []  # 选面历史栈（点面交互核心③：误点撤销）
        self.last_face_tooltip = ""  # 最近一次悬停提示文本（验收①断言用）
        self._setup_dock = None   # 工况设置非模态停靠面板（QDockWidget）
        self._setup_form = None   # SetupFormWidget（点面直连表单）
        self._result_view_active = False  # 当前显示是否为结果云图（点面溯源卡片模式）
        self._result_face_tags = None     # 结果表面逐三角形 occ tag（模块39 溯源）
        self._result_context = None       # 求解/历史结果的 context（仪表卡片与可信度徽章，模块42）
        self._busy_action_snapshot = {}   # busy 前各 QAction 基线使能（模块42）
        self._active_result = None
        self._native_process = None
        self._inspection_process = None
        self._inspection_cancel_button = None
        self._draft_process = None
        self._history_case_name = None
        log.info("主窗口构建完成（视口懒加载：首次显示时创建）")

    # ---------- UI 构建 ----------
    def _make_add_action(self):
        """返回带 kind 分类的 _add_action（模块42：busy 时按类禁用/开放）。"""

        def _add_action(menu, text, slot, shortcut=None, kind=None):  # noqa: ANN001
            act = menu.addAction(text)
            act.triggered.connect(slot)
            if shortcut:
                act.setShortcut(shortcut)
            if kind == "modify":
                self._act_modifying.append(act)
            elif kind == "view":
                self._act_viewing.append(act)
            return act

        return _add_action

    def _build_menus(self) -> None:
        bar = self.menuBar()
        # 模块42：QAction 分类——busy 时只禁修改类（导入/工况/网格编辑/新建打开
        # 保存工程/材料/高级），查看类（云图/历史结果/报告/视口/帮助）保持开放；
        # 求解入口自身有重复提交守卫，不参与分类。
        self._act_modifying: list = []
        self._act_viewing: list = []
        self._add_action = self._make_add_action()

        m_file = bar.addMenu("文件(&F)")
        self._add_action(m_file, "新建工程", self._on_new_project, "Ctrl+N", kind="modify")
        self._add_action(m_file, "打开工程…", self._on_open_project, "Ctrl+O", kind="modify")
        self._add_action(m_file, "保存工程", self._on_save_project, "Ctrl+S", kind="modify")
        self._add_action(m_file, "工程另存为…", self._on_save_project_as, kind="modify")
        m_file.addSeparator()
        self._add_action(m_file, "导入 STEP…", self._on_import_step, "Ctrl+I", kind="modify")
        self._add_action(m_file, "导入 SolidWorks 装配（需本机SW）…", self._on_import_native, kind="modify")
        m_examples = m_file.addMenu("打开示例工程")
        for _example in ("part_beam", "assembly_contact"):
            self._add_action(
                m_examples, _example,
                lambda checked=False, n=_example: self._on_open_example_project(n),
                kind="modify",
            )
        m_file.addSeparator()
        self._add_action(m_file, "退出", self.close, kind="view")

        m_mesh = bar.addMenu("网格(&M)")
        self._add_action(m_mesh, "全局与局部网格设置…", self._on_mesh_controls, kind="modify")
        self._add_action(m_mesh, "网格质量与差单元位置…", self._on_mesh_quality, kind="view")
        self._add_action(m_mesh, "重新运行上次失败任务…", self._on_retry_workflow, kind="modify")
        m_solve = bar.addMenu("求解(&S)")
        self._add_action(m_solve, "运行静力分析…", self._on_solve_static, "F5")
        self._add_action(m_solve, "单零件模态分析…", self._on_modal_analysis, kind="modify")
        self._add_action(m_solve, "历史结果与工况对比…", self._on_case_history, kind="view")
        m_results = bar.addMenu("结果(&T)")
        self._act_show_mises = self._add_action(m_results, "von Mises 云图", self._on_show_mises, kind="view")
        self._act_show_disp = self._add_action(m_results, "位移云图（变形缩放）…", self._on_show_displaced, kind="view")
        self._act_rf_table = self._add_action(m_results, "支反力合力表…", self._on_show_reaction_table, kind="view")
        self._act_extremes = self._add_action(m_results, "结果极值面板…", self._on_show_extremes, kind="view")
        self._act_parts = self._add_action(m_results, "装配与部件结果…", self._on_show_assembly_results, kind="view")
        self._act_parts.setEnabled(False)
        m_results.addSeparator()
        # 报告并入结果菜单（模块41 菜单重组）
        self._act_open_report = self._add_action(m_results, "打开最近计算报告", self._on_open_report, "Ctrl+P", kind="view")
        self._act_open_case_dir = self._add_action(m_results, "打开最近产物目录", self._on_open_case_dir, kind="view")
        self._project_bundle_path: str | None = None
        for act in (self._act_show_mises, self._act_show_disp, self._act_rf_table, self._act_extremes,
                    self._act_open_report, self._act_open_case_dir):
            act.setEnabled(False)  # 求解完成并归档后启用
        self._report_action = self._act_open_report  # 兼容既有使能逻辑（报告并入结果）

        m_advanced = bar.addMenu("高级(&G)")
        self._add_action(m_advanced, "高级分析工作台…", self._on_advanced_workbench, kind="modify")
        self._add_action(m_advanced, "生成网格并导出 INP…", self._on_generate_mesh, kind="modify")

        m_view = bar.addMenu("视图(&V)")
        self._add_action(m_view, "创建/刷新视口", self.ensure_viewport, kind="view")

        m_mat = bar.addMenu("材料(&A)")
        self._add_action(m_mat, "选择/管理材料…", self._on_manage_materials, kind="modify")
        self._add_action(m_mat, "按名称自动推荐材料…", self._on_auto_materials, kind="modify")
        m_case = bar.addMenu("工况(&C)")
        self._add_action(m_case, "自动识别配合面并建立连接…", self._on_auto_connections, kind="modify")
        self._add_action(m_case, "设置约束、载荷与连接…", self._on_edit_setup, kind="modify")
        self._add_action(m_case, "检查装配间隙与接触候选…", self._on_inspect_geometry, kind="modify")
        m_motion = bar.addMenu("运动(&D)")
        self._add_action(m_motion, "运行 Chrono 最小场景…", self._on_chrono_demo, kind="modify")
        self._add_action(m_motion, "运动载荷→梁静力示例…", self._on_chrono_transfer, kind="modify")

        m_help = bar.addMenu("帮助(&H)")
        self._add_action(m_help, "打开使用说明", self._on_open_usage_docs)
        self._add_action(m_help, "关于", self._on_about)

    @staticmethod
    def _add_action(menu, text, slot, shortcut: str | None = None):  # noqa: ANN001
        act = menu.addAction(text)
        act.triggered.connect(slot)
        if shortcut:
            act.setShortcut(shortcut)  # 常用动作快捷键（模块41）
        return act

    def _build_statusbar(self) -> None:
        sb = self.statusBar()
        sb.showMessage("就绪")
        self.backend_label = QLabel("渲染后端=pending")
        sb.addPermanentWidget(self.backend_label)
        self.result_label = QLabel("结果=无")
        sb.addPermanentWidget(self.result_label)

    # ---------- 视口 ----------
    def showEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        super().showEvent(event)
        if not self._viewport_ensure_scheduled:
            self._viewport_ensure_scheduled = True
            QTimer.singleShot(0, self.ensure_viewport)

    def ensure_viewport(self) -> ViewportBackend:
        """创建视口（幂等）并刷新状态栏后端标签。"""
        backend = self.viewport.ensure_viewport()
        self.backend_label.setText(f"渲染后端={backend.value}")
        if backend is ViewportBackend.GL:
            self.statusBar().showMessage("三维视口就绪（GL）", 5000)
        elif backend is ViewportBackend.AGG_FALLBACK:
            self.statusBar().showMessage(
                "GL 不可用，已降级 Agg 画布；选面需三维视口（GL）", 5000
            )
        return backend

    def show_mesh(self, mesh: MeshData, title: str = "") -> str:
        info = self.viewport.show_mesh(mesh, title)
        self._reset_result_view_state()
        self._show_viewport_page()
        self.backend_label.setText(f"渲染后端={self.viewport.backend.value}")
        log.info("网格上屏: %s", info)
        return info

    def show_geometry(
        self, solid_model, point_data=None, scalar_name=None  # noqa: ANN001
    ) -> str:
        """STEP 几何上屏（GL: PolyData+面tag；Agg: 三角形出图）。"""
        info = self.viewport.show_geometry(
            solid_model, point_data=point_data, scalar_name=scalar_name
        )
        self._reset_result_view_state()
        self._show_viewport_page()
        self.backend_label.setText(f"渲染后端={self.viewport.backend.value}")
        log.info("几何上屏: %s", info)
        return info

    def _reset_result_view_state(self) -> None:
        """离开结果云图视图：溯源卡片模式关闭，逐三角形 tag 缓存失效。"""
        self._result_view_active = False
        self._result_face_tags = None

    # ---------- 点面交互核心（选中面卡片 / 悬停反馈 / 误点撤销） ----------
    def _build_face_interactions(self) -> None:
        """选中面卡片撤销按钮 + Ctrl+Z 快捷键（同走 undo_face_pick 历史栈）。"""
        from PySide6.QtGui import QKeySequence, QShortcut

        self.properties.face_card.undo_requested.connect(self.undo_face_pick)
        self._undo_face_shortcut = QShortcut(QKeySequence("Ctrl+Z"), self)
        self._undo_face_shortcut.activated.connect(self.undo_face_pick)

    def _face_part_name(self, tag: int) -> str:
        """occ 面 tag → 所属部件名（装配按 solid_tag 匹配；单件回退『部件N』）。"""
        m = self.solid_model
        if m is None:
            return ""
        try:
            face = m.face_by_tag(tag)
        except KeyError:
            return ""
        solid_tag = getattr(face, "solid_tag", None)
        for part in getattr(self.project, "parts", []):
            if solid_tag is not None and getattr(part, "solid_tag", None) == solid_tag:
                return str(part.name)
        parts = list(getattr(self.project, "parts", []))
        if len(parts) == 1 and solid_tag is None:
            return str(parts[0].name)
        # 无工程部件数据时的可读兜底：按实体序编号（多实体/单件一致）
        if solid_tag is not None:
            for k, solid in enumerate(getattr(m, "solids", [])):
                if solid.tag == solid_tag:
                    return f"部件{k + 1}"
        return "部件1"

    def _face_definition_counts(self, tag: int) -> tuple[int, int, int]:
        """面上已挂定义数：(约束, 载荷, 连接)——遍历工程工况与连接清单。

        约束/载荷按 face_tag 计数（跨全部工况）；连接按面指纹匹配任一侧
        （face_identity.face_matches_fingerprint，同 re-resolve 口径）。
        """
        n_c = n_l = 0
        for lc in self.project.study.load_cases:
            n_c += sum(
                1 for c in lc.constraints if int(getattr(c, "face_tag", -1)) == int(tag)
            )
            n_l += sum(
                1 for ld in lc.loads if int(getattr(ld, "face_tag", -1)) == int(tag)
            )
        n_conn = 0
        m = self.solid_model
        connections = list(getattr(self.project, "connections", []) or [])
        if m is not None and connections:
            import numpy as np

            from ..core.face_identity import face_matches_fingerprint

            try:
                face = m.face_by_tag(tag)
                lo, hi = m.overall_bbox
                diag = float(np.linalg.norm(np.asarray(hi) - np.asarray(lo)))
            except Exception:
                return n_c, n_l, 0
            for conn in connections:
                for side_name in ("side_a", "side_b"):
                    side = getattr(conn, side_name, None)
                    if side is not None and face_matches_fingerprint(
                        face, side.face, diag
                    ):
                        n_conn += 1
                        break
        return n_c, n_l, n_conn

    def _face_card_payload(self, tag: int) -> dict | None:
        """选中面卡片数据（验收②对照 model.face_by_tag(tag) 的原始数值）。

        结果云图视图（模块39）附加：挂接定义名清单 + 该面局部 Mises 极值
        （与整场同源数组重算一致）。
        """
        m = self.solid_model
        if m is None:
            return None
        try:
            face = m.face_by_tag(tag)
        except KeyError:
            return None
        n_c, n_l, n_conn = self._face_definition_counts(tag)
        payload = {
            "tag": int(tag),
            "part_name": self._face_part_name(tag),
            "area_mm2": float(face.area_mm2),
            "normal": tuple(float(v) for v in face.normal),
            "n_constraints": n_c,
            "n_loads": n_l,
            "n_connections": n_conn,
        }
        if self._result_view_active:
            payload["is_result_view"] = True
            payload["definitions_text"] = self._face_definitions_text(tag)
            payload["local_max_mises"] = self._face_local_mises(tag)
        return payload

    def _face_tooltip_text(self, tag: int) -> str:
        """悬停提示：部件名 / 面号 / 面积 mm² / 法向（验收①）。"""
        info = self._face_card_payload(tag)
        if info is None:
            return ""
        nx, ny, nz = info["normal"]
        return (
            f"部件：{info['part_name']}\n"
            f"面 tag={info['tag']}\n"
            f"面积：{info['area_mm2']:.6g} mm²\n"
            f"法向：({nx:.4g}, {ny:.4g}, {nz:.4g})"
        )

    # ---------- 结果仪表卡片（模块42） ----------
    def _update_dashboard(self) -> None:
        """结果仪表卡片刷新：无求解证据（configured_not_computed/prepared_not_computed
        等一切未完成态）一律『未计算/未验证』且无数值，不得显示为已求解。"""
        data = {
            "solved": False, "badge": "未计算/未验证", "badge_level": "none",
            "max_mises": None, "max_u": None, "min_sf": None,
        }
        fields = getattr(self, "_result_fields", None)
        if fields is not None and getattr(self, "_result_context", None):
            data["solved"] = True
            data["max_mises"] = float(fields.s_mises.max())
            data["max_u"] = float(fields.max_u_mag())
            ctx = self._result_context or {}
            diagnostics = ctx.get("solver_diagnostics") or {}
            if diagnostics.get("has_solver_errors"):
                data["badge"], data["badge_level"] = "不可信（日志含错误）", "bad"
            elif diagnostics.get("requires_review"):
                data["badge"], data["badge_level"] = "待复核（连接待检查）", "warn"
            else:
                data["badge"], data["badge_level"] = "已验证", "ok"
            data["min_sf"] = self._min_safety_factor(ctx)
        self.properties.dashboard_card.set_state(data)

    def _min_safety_factor(self, ctx: dict) -> float | None:
        """最小安全系数 = min(各部件屈服强度 / 该部件最大 Mises)；无屈服数据 → None。"""
        factors: list[float] = []
        for part in (ctx.get("assembly") or {}).get("parts", []):
            material = part.get("material") or {}
            yield_mpa = material.get("yield_strength_mpa")
            max_mises = part.get("max_mises")
            if yield_mpa and max_mises:
                factors.append(float(yield_mpa) / float(max_mises))
        if not factors:
            yc = ctx.get("yield_check") or {}
            if yc.get("yield_mpa") and yc.get("max_mises_mpa"):
                factors.append(float(yc["yield_mpa"]) / float(yc["max_mises_mpa"]))
        return min(factors) if factors else None

    def _show_face_card(self, tag: int | None) -> None:
        """属性区顶部选中面卡片刷新（tag=None → 未选中占位）。"""
        payload = self._face_card_payload(int(tag)) if tag is not None else None
        if payload is None and tag is not None and self._result_view_active and int(tag) <= 0:
            # 结果云图命中的无挂接定义面（自由边界面）：卡片明示，不虚报归属
            payload = {"tag": -1, "undefined": True, "is_result_view": True}
        self.properties.face_card.set_face(payload)
        self.properties.face_card.set_undo_enabled(bool(self._face_pick_history))

    def _on_face_picked(self, tag: int) -> None:
        """视口选面信号 → 历史栈 + 选中面卡片 + 日志/状态栏（tag=-1 未命中）。"""
        tag = int(tag)
        if tag >= 0:
            self._selected_face_tag = tag
            if not self._face_pick_history or self._face_pick_history[-1] != tag:
                self._face_pick_history.append(tag)  # 连续重复点击不重复入栈
            log.info("选中面 tag=%d", tag)
            self.statusBar().showMessage(f"选中面 tag={tag}", 5000)
            self._show_face_card(tag)
        else:
            self._selected_face_tag = None
            if self._result_view_active and self.viewport.last_pick == -1:
                # 结果云图命中自由边界面（tag=-1 类）：卡片明示『无挂接定义』
                log.info("结果云图命中无挂接定义面（自由边界面）")
                self.statusBar().showMessage("该面无挂接定义（自由边界面）", 5000)
                self._show_face_card(-1)
            elif self.viewport.mesh_view_active:
                # 纯网格视图无 occ 面 tag：给明确指引而不是含糊的"未命中"（2026-11-04 用户反馈）
                self.statusBar().showMessage(
                    "网格视图暂不支持选面：约束/载荷面请回到几何视图（步骤 2）选择", 5000
                )
                self._show_face_card(None)
            else:
                self.statusBar().showMessage(
                    "视口点击：未命中表面（选面已清除，Ctrl+Z 可撤销恢复）", 3000
                )
                self._show_face_card(None)

    def _on_face_hover(self, tag: int) -> None:
        """视口悬停命中变化 → QToolTip（部件名/面号/面积/法向）。"""
        from PySide6.QtGui import QCursor
        from PySide6.QtWidgets import QToolTip

        tag = int(tag)
        if tag < 0:
            self.last_face_tooltip = ""
            QToolTip.hideText()
            return
        text = self._face_tooltip_text(tag)
        self.last_face_tooltip = text
        if text:
            QToolTip.showText(QCursor.pos(), text, self.viewport)

    def _on_agg_pick_attempted(self) -> None:
        """Agg 降级画布上的点选尝试：无拾取能力，提示需要 GL 三维视口。"""
        self.statusBar().showMessage("选面需三维视口（GL）", 5000)

    def undo_face_pick(self) -> None:
        """撤销选面（Ctrl+Z / 卡片按钮）：弹出选面历史栈顶并重新高亮。

        栈序 = 每次成功点中的面按序入栈；撤销弹出栈顶恢复——主要场景是
        误点空白清除选面后恢复（点空白不清栈），连续撤销逐条回退历史。
        """
        if not self._face_pick_history:
            self.statusBar().showMessage("没有可撤销的选面", 3000)
            return
        tag = self._face_pick_history.pop()
        try:
            self.viewport.highlight_face(tag)
        except Exception:
            log.warning("撤销选面：高亮恢复失败 tag=%d", tag, exc_info=True)
        self._selected_face_tag = tag
        self._show_face_card(tag)
        log.info("撤销选面：恢复面 tag=%d（剩余历史 %d 条）", tag, len(self._face_pick_history))
        self.statusBar().showMessage(f"已撤销选面：恢复面 tag={tag}", 5000)

    # ---------- 装配显示（模块 13） ----------
    def show_assembly(self, isolate_part_id: str | None = None) -> str:
        """装配上屏：按部件着色（每部件一色）；isolate_part_id 给定时只显示该部件。

        单实体/无模型回退 show_geometry（零件模块行为不变）。
        """
        from ..cad.assembly_import import AssemblyModel, sync_parts_from_model

        m = self.solid_model
        if not isinstance(m, AssemblyModel) or not m.parts:
            return self.show_geometry(m) if m is not None else "no model"
        if not self.project.parts:
            sync_parts_from_model(self.project, m)
            self._refresh_project_views()
        tags: list[int] | None
        if isolate_part_id is not None:
            part = self.project.part_by_id(isolate_part_id)  # 未知 part_id → KeyError 上抛
            if not (0 <= part.solid_index < len(m.solids)):
                raise IndexError(
                    f"部件 {part.part_id} solid_index={part.solid_index} "
                    f"超出当前模型实体数 {len(m.solids)}（几何与工程不同源？）"
                )
            tags = [m.solids[part.solid_index].tag]
        else:
            tags = [
                m.solids[p.solid_index].tag
                for p in self.project.parts
                if p.visible and 0 <= p.solid_index < len(m.solids)
            ] or None
        info = self.viewport.show_geometry_parts(m, visible_part_tags=tags)
        self._reset_result_view_state()
        self._show_viewport_page()
        self.backend_label.setText(f"渲染后端={self.viewport.backend.value}")
        return info

    def _on_part_renamed(self, part_id: str, new_name: str) -> None:
        """装配树 F2 重命名部件（模块41）：面引用走 part_id+几何指纹不受影响。"""
        name = (new_name or "").strip()
        try:
            part = self.project.part_by_id(part_id)
        except KeyError:
            QTimer.singleShot(0, self._refresh_project_views)  # 未知部件：回滚显示
            return
        if not name or name == part.name:
            QTimer.singleShot(0, self._refresh_project_views)  # 空名/未变：回滚显示
            return
        part.name = name
        self.project.dirty = True
        log.info("部件重命名: %s -> %s", part_id, name)
        self.statusBar().showMessage(f"已重命名部件 {part_id} 为 {name}", 5000)
        # itemChanged 信号栈内禁止同步 clear 树（shiboken 重入删除）——延迟刷新
        QTimer.singleShot(0, self._refresh_project_views)

    def _on_isolate_part(self, part_id: str) -> None:
        """装配树双击部件 → 隔离显示（只显该部件）。"""
        try:
            info = self.show_assembly(isolate_part_id=part_id)
        except (KeyError, IndexError) as exc:
            log.error("隔离显示失败: %s", exc)
            self.statusBar().showMessage(f"隔离显示失败: {exc}", 5000)
            return
        log.info("隔离显示部件 %s: %s", part_id, info)
        self.statusBar().showMessage(f"隔离显示: {part_id}", 5000)

    def _on_show_all_parts(self) -> None:
        """装配树双击装配节点 → 恢复全部部件（按部件着色）。"""
        info = self.show_assembly()
        log.info("装配显示（按部件着色）: %s", info)
        self.statusBar().showMessage("装配显示：按部件着色", 5000)

    # ---------- 工程 ----------
    def _refresh_project_views(self) -> None:
        self.case_tree.load_project(self.project)
        s = self.project.study
        rows: list[tuple[str, str]] = [
            ("工程名", self.project.name),
            ("研究", s.name),
            ("材料", "存在待指定材料" if any(not p.material_confirmed for p in self.project.parts) else s.material.name),
            ("弹性模量 [MPa]", f"{s.material.e_modulus_mpa:g}"),
            ("泊松比", f"{s.material.poisson:g}"),
            ("工况数", str(len(s.load_cases))),
            ("几何", s.geometry_file or "（未导入）"),
            ("网格", s.mesh_file or "（未生成）"),
        ]
        if self.solid_model is not None:
            m = self.solid_model
            rows += [
                ("源单位", m.source_unit.name),
                ("实体数 / 表面数", f"{len(m.solids)} / {len(m.faces)}"),
                ("部件数", str(len(getattr(self.project, "parts", [])) or len(getattr(m, "parts", [])))),
                ("总体积 [mm³]", f"{m.total_volume_mm3:.6g}"),
            ]
        self.properties.show_object("工程属性", rows)
        self.properties.set_inline_import(self.solid_model is None)
        self.refresh_workflow_bar()

    # ---------- 工作流导航（首启引导，模块40） ----------
    def refresh_workflow_bar(self) -> None:
        """由工程状态推导 7 步流程条完成态（导入→材料→约束载荷→网格→求解→结果→报告）。"""
        try:
            self.workflow_bar.set_steps(self._workflow_step_states())
        except Exception:
            log.debug("流程条刷新失败", exc_info=True)

    def _workflow_step_states(self) -> list[dict]:
        has_geo = self.solid_model is not None
        parts = list(getattr(self.project, "parts", []))
        states: list[dict] = [
            {
                "name": "导入",
                "state": "done" if has_geo else "todo",
                "hint": "" if has_geo else "缺少：导入 STEP 几何",
            }
        ]
        if not has_geo:
            states.append({"name": "材料", "state": "todo", "hint": "缺少：先导入几何"})
        else:
            missing = [
                (p.name or p.part_id)
                for p in parts
                if not getattr(p, "material_name", "") or not getattr(p, "material_confirmed", False)
            ]
            if missing:
                states.append(
                    {"name": "材料", "state": "error", "hint": "未指定材料：" + "、".join(missing[:2])}
                )
            elif parts:
                states.append({"name": "材料", "state": "done", "hint": ""})
            else:
                states.append({"name": "材料", "state": "todo", "hint": "缺少：部件清单"})
        n_defs = sum(
            1
            for lc in self.project.study.load_cases
            for x in (*lc.constraints, *lc.loads)
            if int(getattr(x, "face_tag", 0) or 0) > 0
        )
        states.append(
            {
                "name": "约束载荷",
                "state": "done" if n_defs else "todo",
                "hint": "" if n_defs else "缺少：固定面/载荷定义",
            }
        )
        import pathlib as _pathlib

        mesh_file = getattr(self.project.study, "mesh_file", "") or ""
        mesh_ok = (bool(mesh_file) and _pathlib.Path(mesh_file).exists()) or (
            getattr(self, "_solve_mesh_result", None) is not None
        )
        states.append(
            {
                "name": "网格",
                "state": "done" if mesh_ok else "todo",
                "hint": "" if mesh_ok else "缺少：生成网格并导出 INP",
            }
        )
        solved = bool(self.project.cases) or getattr(self, "_last_archive", None) is not None
        states.append(
            {
                "name": "求解",
                "state": "done" if solved else "todo",
                "hint": "" if solved else "缺少：运行静力分析",
            }
        )
        results = (
            getattr(self, "_result_fields", None) is not None
            or getattr(self, "_active_result", None) is not None
        )
        states.append(
            {
                "name": "结果",
                "state": "done" if results else "todo",
                "hint": "" if results else "缺少：求解后查看结果",
            }
        )
        archive = getattr(self, "_last_archive", None) or {}
        report_path = archive.get("report_md")
        report_ok = bool(report_path) and _pathlib.Path(str(report_path)).exists()
        states.append(
            {
                "name": "报告",
                "state": "done" if report_ok else "todo",
                "hint": "" if report_ok else "缺少：求解生成报告",
            }
        )
        return states

    def _on_workflow_step_clicked(self, index: int) -> None:
        """点击流程条步骤 → 直达对应对话框/动作（未完成步同样可点，由动作给指引）。"""
        handlers = [
            self._on_import_step,
            self._on_manage_materials,
            self._on_edit_setup,
            self._on_generate_mesh,
            self._on_solve_static,
            self._on_workflow_show_results,
            self._on_open_report,
        ]
        if 0 <= index < len(handlers):
            handlers[index]()

    def _on_workflow_show_results(self) -> None:
        if getattr(self, "_result_fields", None) is None:
            QMessageBox.information(self, "查看结果", "缺少：请先完成求解，再查看结果云图")
            return
        self._on_show_mises()

    def _show_viewport_page(self) -> None:
        """中央页面栈切到三维视口（几何/网格/结果上屏时）。"""
        stack = getattr(self, "_central_stack", None)
        if stack is not None and stack.currentWidget() is not self.viewport:
            stack.setCurrentWidget(self.viewport)

    def _on_open_example_project(self, example_name: str | None = None) -> None:
        """打开 examples/ 下示例工程束（首启引导；默认 part_beam）。"""
        name = example_name or "part_beam"
        root = Path(__file__).resolve().parents[3]
        bundle = root / "examples" / name / "project.rmsimproject"
        if not bundle.exists():
            candidates = sorted(
                p.parent.name for p in (root / "examples").glob("*/project.rmsimproject")
            )
            QMessageBox.information(
                self, "打开示例工程",
                f"示例 {name} 缺少工程束；可用示例：{'、'.join(candidates) or '（无）'}",
            )
            return
        if self._solve_task is not None and self._solve_task.state in ("pending", "running"):
            QMessageBox.information(self, "打开示例工程", "请等待当前求解结束")
            return
        self._open_bundle_path(bundle)
        self.statusBar().showMessage(f"已打开示例工程：{name}", 8000)

    def _on_open_usage_docs(self) -> None:
        """打开使用说明（仓库根 使用说明.md，系统关联程序）。"""
        import os

        root = Path(__file__).resolve().parents[3]
        doc = root / "使用说明.md"
        if not doc.exists():
            QMessageBox.information(self, "使用说明", f"未找到使用说明文件：{doc}")
            return
        try:
            os.startfile(str(doc))
            self.statusBar().showMessage("已打开使用说明", 5000)
        except Exception as exc:
            log.warning("打开使用说明失败: %s", exc)
            QMessageBox.information(self, "使用说明", f"请手动打开：{doc}")

    @staticmethod
    def _demo_project() -> Project:
        study = Study(
            name="支架-静力分析（示例）",
            material=Material(),
            load_cases=[
                LoadCase(
                    name="工况1：尖端载荷",
                    constraints=[Constraint(name="底面固定", face_tag=0)],
                    loads=[
                        TractionLoad(
                            name="端面总力 P",
                            face_tag=0,
                            total_force_n=100.0,
                            direction=(0.0, -1.0, 0.0),
                        ),
                        GravityLoad(name="重力", direction=(0.0, -1.0, 0.0), g_mm_s2=9810.0),
                    ],
                )
            ],
        )
        return Project(name="示例工程（未保存）", study=study)

    def _on_new_project(self) -> None:
        if self._solve_task is not None and self._solve_task.state in ("pending", "running"):
            QMessageBox.information(self, "新建工程", "请等待当前求解结束")
            return
        if self.solid_model is not None:
            self.solid_model.close()
        self.solid_model = None
        self._project_bundle_path = None
        self.project = Project()
        self.invalidate_results()
        self._reset_result_view_state()
        self._central_stack.setCurrentWidget(self.empty_state)  # 回首启空状态页
        self._refresh_project_views()
        log.info("已新建空白工程")

    def _on_open_project(self) -> None:
        """打开 .rmsimproject 工程束：解包 + 几何副本重导入 + 面指纹 re-resolve。"""
        path, _ = QFileDialog.getOpenFileName(
            self, "打开工程", "", "rm_sim 工程 (*.rmsimproject)"
        )
        if not path:
            return
        self._open_bundle_path(Path(path))

    def _open_bundle_path(self, path) -> None:  # noqa: ANN001
        """打开工程束本体（_on_open_project 与示例工程共用）。"""
        try:
            from ..solver.project_workflow import open_and_reresolve

            import datetime
            extract_dir = Path(path).parent / (f"opened_{Path(path).stem}_" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f"))
            if self._solve_task is not None and self._solve_task.state in ("pending", "running"):
                raise ValueError("请等待当前求解结束后再打开其他工程")
            opened = open_and_reresolve(path, extract_dir)
            if self.solid_model is not None:
                self.solid_model.close()
            self.project = opened["project"]
            self.project.project_dir = str(extract_dir)
            self._project_bundle_path = str(path)
            if opened["model"]:
                self.solid_model = opened["model"]
                warnings = opened["warnings"]
                for w in warnings:
                    log.warning("%s", w)
                if not self.project.is_confirmed:
                    QMessageBox.warning(
                        self, "需重新确认",
                        "边界/载荷面指纹未全部唯一命中（几何可能已变更）——"
                        "请重新确认固定面/加载面后再求解。\n"
                        + "\n".join(warnings[:3])
                    )
            self.invalidate_results()
            self._refresh_project_views()
            self.show_assembly()
            if self.project.cases:
                try:
                    self._load_history_case(self.project.cases[-1].name)
                except Exception as exc:
                    log.warning("工程已打开，但历史结果不能恢复: %s", exc)
                    QMessageBox.warning(self,"历史结果不可用",str(exc))
            log.info("工程已打开: %s（is_confirmed=%s）", path, self.project.is_confirmed)
            self.statusBar().showMessage(f"已打开: {path}", 8000)
        except Exception as exc:
            log.error("打开工程失败: %s: %s", type(exc).__name__, exc)
            QMessageBox.warning(self, "打开工程失败", f"{type(exc).__name__}: {exc}")

    def _on_save_project(self) -> None:
        """保存 .rmsimproject 工程束（几何副本 + case 产物）。"""
        default = self._project_bundle_path or str(
            Path(self.project.project_dir or "documents") / "project.rmsimproject"
        )
        path, _ = QFileDialog.getSaveFileName(
            self, "保存工程", default, "rm_sim 工程 (*.rmsimproject)"
        )
        if not path:
            return
        self._save_bundle_to(path)

    def _on_save_project_as(self) -> None:
        self._on_save_project()

    def _save_bundle_to(self, path: str) -> None:
        try:
            from ..core.project_bundle import save_bundle

            cases_root = Path(self.project.project_dir) / "cases"                 if self.project.project_dir and (Path(self.project.project_dir) / "cases").is_dir()                 else None
            copies = None
            if len(self.project.parts) > 1 and self.solid_model is not None:
                from ..cad.assembly_import import export_part_steps
                copies = export_part_steps(self.project, self.solid_model, Path(path).parent/"part_geometry")
            saved = save_bundle(
                path, self.project,
                geometry_source=self.project.study.geometry_file or None,
                cases_root=cases_root,
                part_geometry_sources=copies,
            )
            self._project_bundle_path = str(saved)
            self.project.dirty = False
            log.info("工程已保存: %s", saved)
            self.statusBar().showMessage(f"已保存: {saved}", 8000)
        except Exception as exc:
            log.error("保存工程失败: %s: %s", type(exc).__name__, exc)
            QMessageBox.warning(self, "保存工程失败", f"{type(exc).__name__}: {exc}")

    def _on_import_step(self) -> None:
        """导入 STEP：三检（单位/实体/清单）在 cad 层完成，源单位与清单写入日志。"""
        path, _ = QFileDialog.getOpenFileName(
            self, "导入 STEP", "", "STEP 模型 (*.step *.stp);;所有文件 (*)"
        )
        if not path:
            return
        try:
            from ..cad.assembly_import import import_assembly, sync_parts_from_model

            if self._solve_task is not None and self._solve_task.state in ("pending", "running"):
                raise ValueError("请等待当前求解结束后再导入其他几何")
            if self.solid_model is not None:
                self.solid_model.close()
            self.solid_model = import_assembly(path)
            self.project = Project(name=Path(path).stem)
            self._project_bundle_path = None
            self._close_setup_dock()  # 几何已更换：旧工况设置表单面清单失效
            self.invalidate_results()
            self.project.study.geometry_file = str(path)
            sync_note = ""
            if self.solid_model.parts:
                sync_parts_from_model(self.project, self.solid_model)
                for part in self.project.parts:
                    part.material_confirmed = False
                sync_note = f"（{len(self.solid_model.parts)} 个部件）"
            for line in self.solid_model.summary_lines():
                log.info(line)
            self._refresh_project_views()
            try:
                self.show_assembly()
            except Exception as exc:
                log.error("几何上屏失败: %s: %s", type(exc).__name__, exc)
            self.statusBar().showMessage(f"已导入 STEP，请先为部件指定材料: {path}", 10000)
            log.info("STEP 导入完成%s", sync_note)
        except Exception as exc:
            self.solid_model = None
            log.error("导入 STEP 失败: %s: %s", type(exc).__name__, exc)
            QMessageBox.warning(self, "导入 STEP 失败", f"{type(exc).__name__}: {exc}")

    def _on_import_native(self) -> None:
        if self._solve_task is not None and self._solve_task.state in ("pending", "running"):
            QMessageBox.information(self, "导入装配", "请等待当前求解结束")
            return
        source, _ = QFileDialog.getOpenFileName(self, "选择SolidWorks装配", "", "SolidWorks (*.sldasm *.SLDASM)")
        if not source:
            return
        directory = QFileDialog.getExistingDirectory(self, "选择转换副本和工程工作目录")
        if not directory:
            return
        import datetime
        from ..cad.native_import import export_command
        output = Path(directory) / ("native_" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f"))
        output.mkdir(parents=True, exist_ok=False)
        command = export_command(source, output)
        process = QProcess(self)
        self._native_process = process
        process.setProgram(command[0])
        process.setArguments(command[1:])
        process.setStandardOutputFile(str(output / "export.stdout.log"))
        process.setStandardErrorFile(str(output / "export.stderr.log"))
        self.menuBar().setEnabled(False)
        self.statusBar().showMessage("正在通过SolidWorks导出只读分析副本…")

        def finished(code, status):
            self.menuBar().setEnabled(True)
            self._native_process = None
            try:
                if code != 0 or status != QProcess.ExitStatus.NormalExit:
                    raise RuntimeError(f"SolidWorks转换失败，退出码 {code}；日志：{output}")
                from ..cad.native_import import load_native_export, project_from_export
                result = load_native_export(output)
                project = project_from_export(result, directory)
                if self.solid_model is not None:
                    self.solid_model.close()
                self.solid_model = result["model"]
                self.project = project
                self._project_bundle_path = None
                self.invalidate_results()
                self._refresh_project_views()
                self.show_assembly()
                missing = sum(not p.material_confirmed for p in project.parts)
                self.statusBar().showMessage(f"原生装配已导入：{len(project.parts)}个实体，{missing}个材料待指定", 15000)
                log.info("原生导入来源和假设: %s", project.geometry_provenance)
            except Exception as exc:
                log.exception("原生装配导入失败")
                QMessageBox.warning(self, "原生装配导入失败", str(exc))
            process.deleteLater()

        process.finished.connect(finished)
        def failed_to_start(error):
            if error == QProcess.ProcessError.FailedToStart:
                finished(-1, QProcess.ExitStatus.CrashExit)
        process.errorOccurred.connect(failed_to_start)
        process.start()

    def closeEvent(self, event) -> None:
        if self._solve_task is not None and self._solve_task.state in ('pending','running'):
            self._close_after_solve = True
            self._cancel_current_solve()
            event.ignore()
            return
        if self._inspection_process is not None:
            QMessageBox.information(self,"几何检查","请等待检查结束，或先点击状态栏的取消检查")
            event.ignore()
            return
        if self._draft_process is not None:
            QMessageBox.information(self, "自动识别配合面", "请等待识别结束，或先点击状态栏的取消识别")
            event.ignore()
            return
        if self._native_process is not None:
            QMessageBox.information(self, "装配转换", "请等待转换和源文件释放完成后退出")
            event.ignore()
            return
        super().closeEvent(event)

    def _on_inspect_geometry(self):
        if self.solid_model is None:
            QMessageBox.information(self,'几何检查','请先导入装配')
            return
        if self._solve_task is not None and self._solve_task.state in ('pending','running'):
            QMessageBox.information(self,'几何检查','请等待当前求解结束')
            return
        import datetime,json,sys
        from PySide6.QtWidgets import QPushButton
        directory=self.project.project_dir or QFileDialog.getExistingDirectory(self,'选择检查记录目录')
        if not directory:return
        work=Path(directory)/'geometry_checks'/datetime.datetime.now().strftime('%Y%m%d_%H%M%S_%f')
        work.mkdir(parents=True,exist_ok=False)
        names={str(self.solid_model.solids[p.solid_index].tag):p.name for p in self.project.parts}
        spec={'step':str(self.solid_model.path),'part_names':names,'output':str(work/'inspection.json'),'planar_only':True}
        (work/'spec.json').write_text(json.dumps(spec,ensure_ascii=False,indent=2),encoding='utf-8')
        process=QProcess(self);self._inspection_process=process
        process.setProgram(sys.executable)
        process.setArguments(['-X','utf8','-B','-m','rm_sim.cad.inspection_job',str(work/'spec.json')])
        process.setWorkingDirectory(str(Path(__file__).resolve().parents[2]))
        process.setStandardOutputFile(str(work/'stdout.log'));process.setStandardErrorFile(str(work/'stderr.log'))
        self.menuBar().setEnabled(False)
        cancel=QPushButton('取消检查',self);self.statusBar().addPermanentWidget(cancel)
        self._inspection_cancel_button=cancel
        state={'cancelled':False,'finished':False}
        def stop():
            state['cancelled']=True;cancel.setEnabled(False);process.kill()
        cancel.clicked.connect(stop)
        self.statusBar().showMessage('正在检查装配几何，原始记录将保留…')
        def finished(code,status):
            if state['finished']:return
            state['finished']=True
            self._inspection_process=None;self.menuBar().setEnabled(True)
            self._inspection_cancel_button=None
            self.statusBar().removeWidget(cancel);cancel.deleteLater()
            try:
                if state['cancelled']:
                    (work/'cancelled.json').write_text(json.dumps({'status':'cancelled','exit_code':code}),encoding='utf-8')
                    self.statusBar().showMessage(f'几何检查已取消，日志：{work}',10000)
                    return
                if code!=0 or status!=QProcess.ExitStatus.NormalExit:
                    raise RuntimeError(f'几何检查失败，退出码{code}；日志：{work}')
                import hashlib
                result=json.loads((work/'inspection.json').read_text(encoding='utf-8'))
                if hashlib.sha256(self.solid_model.path.read_bytes()).hexdigest()!=result['step_sha256']:
                    raise ValueError('几何文件在检查期间发生变化，请重新导入后检查')
                from .inspection_dialog import AssemblyInspectionDialog
                dialog=AssemblyInspectionDialog(result,self)
                dialog.faces_selected.connect(self._edit_candidate_faces)
                self.statusBar().showMessage(f'几何检查记录：{work}',15000)
                dialog.exec()
            except Exception as exc:
                log.exception('装配几何检查失败');QMessageBox.warning(self,'装配几何检查失败',str(exc))
            finally:process.deleteLater()
        process.finished.connect(finished)
        process.errorOccurred.connect(lambda error:finished(-1,QProcess.ExitStatus.CrashExit)
                                      if error==QProcess.ProcessError.FailedToStart else None)
        process.start()

    def _edit_candidate_faces(self,face_a,face_b):
        from .setup_dialog import SetupDialog
        dialog=SetupDialog(self.project,self.solid_model,face_a,self)
        for combo,tag in ((dialog.face_a,face_a),(dialog.face_b,face_b)):
            index=next((i for i in range(combo.count()) if combo.itemData(i)[1]==tag),None)
            if index is None:
                QMessageBox.warning(self,'候选面失效','当前模型中找不到候选面，请重新检查')
                return
            combo.setCurrentIndex(index)
        dialog.kind.setCurrentText('可分离接触与摩擦')
        dialog.name.setText('候选接触')
        dialog.exec()
        if dialog.changed:
            self.invalidate_results();self._refresh_project_views()

    def _on_auto_connections(self):
        """后台识别装配贴合面 → 审核对话框 → 一次写入全部连接（隔离 OCC 进程）。"""
        if self.solid_model is None:
            QMessageBox.information(self, "自动识别配合面", "请先导入装配 STEP 或 SolidWorks 装配")
            return
        if len(self.project.parts) < 2:
            QMessageBox.information(self, "自动识别配合面",
                                    "自动连接只用于多部件装配；单零件请直接设置约束与载荷")
            return
        if self._draft_process is not None:
            QMessageBox.information(self, "自动识别配合面", "上一次识别还在进行，请先等待或取消")
            return
        if self._solve_task is not None and self._solve_task.state in ("pending", "running"):
            QMessageBox.information(self, "自动识别配合面", "请等待当前求解结束")
            return
        import datetime
        import json
        import sys

        from PySide6.QtWidgets import QPushButton

        directory = self.project.project_dir or QFileDialog.getExistingDirectory(self, "选择识别记录目录")
        if not directory:
            return
        work = Path(directory) / "connection_drafts" / datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        work.mkdir(parents=True, exist_ok=False)
        names = {}
        for part in self.project.parts:
            index = int(getattr(part, "solid_index", -1))
            if 0 <= index < len(self.solid_model.solids):
                names[str(int(self.solid_model.solids[index].tag))] = part.name or part.part_id
        spec = {"step": str(self.solid_model.path), "part_names": names,
                "output": str(work / "draft.json"), "planar_only": False, "min_area_mm2": 0.05}
        (work / "spec.json").write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")
        process = QProcess(self)
        self._draft_process = process
        process.setProgram(sys.executable)
        process.setArguments(["-X", "utf8", "-B", "-m", "rm_sim.cad.draft_job", str(work / "spec.json")])
        process.setWorkingDirectory(str(Path(__file__).resolve().parents[2]))
        process.setStandardOutputFile(str(work / "stdout.log"))
        process.setStandardErrorFile(str(work / "stderr.log"))
        self.menuBar().setEnabled(False)
        cancel = QPushButton("取消识别", self)
        self.statusBar().addPermanentWidget(cancel)
        self._draft_cancel_button = cancel
        state = {"cancelled": False, "finished": False}

        def stop():
            state["cancelled"] = True
            cancel.setEnabled(False)
            process.kill()

        cancel.clicked.connect(stop)
        self.statusBar().showMessage("正在识别装配结合面（对比每个零件对的距离与公共面），请稍候…")

        def finished(code, status):
            if state["finished"]:
                return
            state["finished"] = True
            self._draft_process = None
            self.menuBar().setEnabled(True)
            self._draft_cancel_button = None
            self.statusBar().removeWidget(cancel)
            cancel.deleteLater()
            try:
                if state["cancelled"]:
                    (work / "cancelled.json").write_text(
                        json.dumps({"status": "cancelled", "exit_code": code}), encoding="utf-8")
                    self.statusBar().showMessage(f"识别已取消，记录：{work}", 10000)
                    return
                if code != 0 or status != QProcess.ExitStatus.NormalExit:
                    raise RuntimeError(f"识别失败，退出码 {code}；日志：{work}")
                import hashlib

                draft = json.loads((work / "draft.json").read_text(encoding="utf-8"))
                if hashlib.sha256(self.solid_model.path.read_bytes()).hexdigest() != draft["step_sha256"]:
                    raise ValueError("几何文件在识别期间发生变化，请重新导入后再识别")
                from .connection_draft_dialog import AssemblyConnectionDraftDialog

                dialog = AssemblyConnectionDraftDialog(draft, self, project=self.project)
                self.statusBar().showMessage(f"识别记录：{work}", 15000)
                from PySide6.QtWidgets import QDialog

                if dialog.exec() != QDialog.DialogCode.Accepted:
                    log.info("连接草稿未采用（保留记录 %s）", work)
                    return
                self._apply_connection_draft(draft, dialog.accepted, dialog.kinds)
            except Exception as exc:
                log.exception("自动识别配合面失败")
                QMessageBox.warning(self, "自动识别配合面失败", str(exc))
            finally:
                process.deleteLater()

        process.finished.connect(finished)
        process.errorOccurred.connect(
            lambda error: finished(-1, QProcess.ExitStatus.CrashExit)
            if error == QProcess.ProcessError.FailedToStart else None)
        process.start()

    def _apply_connection_draft(self, draft, accepted, kinds):
        """把审核结果写入工程连接（失败项显式列出，不静默吞掉）。"""
        from ..core.connections import apply_connection_draft

        result = apply_connection_draft(self.project, self.solid_model, draft,
                                        accepted=accepted, kinds=kinds)
        self.invalidate_results()
        self._refresh_project_views()
        summary = f"已建立 {result['added']} 条连接"
        if result["skipped"]:
            summary += f"（{result['skipped']} 条与已有连接重复，已跳过）"
        log.info("自动连接：%s", summary)
        self.statusBar().showMessage(summary, 10000)
        if result["errors"]:
            detail = "\n".join(
                f"{row.get('name_a')} ↔ {row.get('name_b')}：{row['error']}" for row in result["errors"][:5])
            QMessageBox.warning(self, "部分贴合面未建立连接",
                                summary + f"\n\n以下 {len(result['errors'])} 处失败：\n{detail}")
        else:
            QMessageBox.information(self, "连接已建立",
                                   summary + "\n\n默认按粘接近似（界面不分离、不滑移）；"
                                   "报告「模型假设」一节会写明。如某个界面实际会张开，"
                                   "可用「设置约束、载荷与连接…」改成可分离接触。")

    def _on_auto_materials(self):
        """按部件名推荐材料，用户在表格里一次性确认（材料直接影响结果，不隐式写入）。"""
        if not self.project.parts:
            QMessageBox.information(self, "自动推荐材料", "请先导入装配")
            return
        from PySide6.QtWidgets import (
            QComboBox,
            QDialog,
            QDialogButtonBox,
            QTableWidget,
            QTableWidgetItem,
            QVBoxLayout,
            QLabel,
        )

        from ..core.materials import all_materials, set_part_material, suggest_material

        names = [m.name for m in all_materials()]
        dialog = QDialog(self)
        dialog.setWindowTitle("按名称推荐材料")
        dialog.resize(820, 560)
        layout = QVBoxLayout(dialog)
        hint = QLabel("材料直接影响应力与变形。下表按部件名称给出推荐，可逐个改；"
                      "点「全部采用」才会写入工程。")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        table = QTableWidget(len(self.project.parts), 4)
        table.setHorizontalHeaderLabels(["部件", "推荐材料", "理由", "当前材料"])
        table.horizontalHeader().setStretchLastSection(True)
        combos = []
        for row, part in enumerate(self.project.parts):
            suggestion = suggest_material(part)
            label = QTableWidgetItem(f"{part.name or part.part_id}（{part.part_id}）")
            label.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            table.setItem(row, 0, label)
            combo = QComboBox()
            combo.addItem("保留当前材料" if part.material_confirmed else "待指定", None)
            for name in names:
                combo.addItem(name, name)
            if not suggestion.get("preserve") and suggestion["name"] in names:
                combo.setCurrentIndex(combo.findData(suggestion["name"]))
            combos.append(combo)
            table.setCellWidget(row, 1, combo)
            reason = QTableWidgetItem(suggestion["reason"])
            reason.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            table.setItem(row, 2, reason)
            current = QTableWidgetItem(part.material_name or "（未指定）")
            current.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            table.setItem(row, 3, current)
        table.resizeColumnsToContents()
        layout.addWidget(table)
        buttons = chinese_button_box(
            QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel),
            ok="全部采用",
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        applied = []
        for part, combo in zip(self.project.parts, combos):
            try:
                selected = combo.currentData()
                if selected is None:
                    continue
                set_part_material(self.project, part.part_id, selected)
                applied.append(f"{part.part_id}={selected}")
            except Exception as exc:  # noqa: BLE001
                QMessageBox.warning(self, "设置材料失败", f"{part.part_id}: {exc}")
        self.invalidate_results()
        self._refresh_project_views()
        log.info("自动材料已应用：%s", "，".join(applied))
        self.statusBar().showMessage(f"已为 {len(applied)} 个部件设置材料", 8000)

    def _on_generate_mesh(self) -> None:
        if self._solve_task is not None and self._solve_task.state in ("pending", "running"):
            QMessageBox.information(self,"生成网格","请等待当前求解结束")
            return
        if self.solid_model is None:
            QMessageBox.warning(self, "生成网格", "请先导入 STEP")
            return
        from PySide6.QtWidgets import QInputDialog
        size, accepted = QInputDialog.getDouble(self, "网格尺寸", "尺寸 [mm]", self.project.study.mesh_size_mm, 0.2, 50.0, 2)
        if not accepted:
            return
        out, _ = QFileDialog.getSaveFileName(self, "导出网格", "mesh.inp", "CalculiX INP (*.inp)")
        if not out:
            return
        try:
            if not self.project.project_dir:self.project.project_dir = str(Path(out).parent)
            self._start_workflow(mesh_only=True,size=size,export_path=out)
        except Exception as exc:
            log.exception("网格准备失败")
            QMessageBox.warning(self, "网格准备失败", str(exc))

    def _on_mesh_controls(self):
        if self.solid_model is None:
            QMessageBox.information(self,'局部网格','请先导入STEP');return
        from .mesh_dialogs import MeshControlsDialog
        dialog=MeshControlsDialog(self.project,self.solid_model,self)
        if dialog.exec()==dialog.DialogCode.Accepted:
            self.project.study.mesh_controls=dialog.controls
            self.project.study.mesh_size_mm=dialog.global_size.value()
            self.invalidate_results();self._refresh_project_views()

    def _on_mesh_quality(self):
        from .mesh_dialogs import MeshQualityDialog
        quality=(self._prepared or {}).get('mesh_result')
        quality=quality.quality if quality else (getattr(self,'_failed_mesh_quality',{}) or getattr(self,'_history_mesh_quality',{}))
        MeshQualityDialog(quality,self).exec()

    def _on_retry_workflow(self):
        previous=self._workflow_task
        if previous is None or previous.state!='failed':
            QMessageBox.information(self,'重试','没有可重试的失败任务');return
        if self.project is not self._workflow_project or self.solid_model is not self._workflow_model:
            QMessageBox.information(self,'重试','已切换工程或模型，请从当前工程重新提交计算');return
        from .mesh_dialogs import MeshControlsDialog
        from PySide6.QtWidgets import QInputDialog
        dialog=MeshControlsDialog(self.project,self.solid_model,self)
        if dialog.exec()!=dialog.DialogCode.Accepted:return
        choice,ok=QInputDialog.getItem(self,'重试配方','网格准备策略',
            ['当前设置','保守曲率配方（保留局部加密）'],0,False)
        if not ok:return
        self.project.study.mesh_controls=dialog.controls
        self.project.study.mesh_size_mm=dialog.global_size.value()
        kwargs=None if choice=='当前设置' else dict(curvature=8.,min_size_mm=min(.4,self.project.study.mesh_size_mm*.25),high_order_optimize=True)
        request=dict(self._last_workflow_request)
        request.update(size=self.project.study.mesh_size_mm,mesh_kwargs=kwargs)
        try:self._start_workflow(**request)
        except Exception as exc:QMessageBox.warning(self,'重试提交失败',str(exc))

    def _on_solve_static(self) -> None:
        if self._solve_task is not None and self._solve_task.state in ("pending", "running"):
            QMessageBox.information(self, "求解", "已有求解任务在运行")
            return
        if self.solid_model is None:
            QMessageBox.warning(self, "求解", "请先导入 STEP")
            return
        try:
            if not self.project.project_dir:
                directory = QFileDialog.getExistingDirectory(self, "选择工程计算目录")
                if not directory:
                    return
                self.project.project_dir = directory
            self._start_workflow()
        except Exception as exc:
            log.exception("提交求解失败")
            QMessageBox.warning(self, "提交求解失败", str(exc))
            return

    def _start_workflow(self, *, mesh_only=False,size=None,export_path=None,mesh_kwargs=None):
        from ..solver.workflow import WorkflowScheduler
        from ..config import get_ccx_path,load_config
        from ..core.face_identity import fingerprint_from_face
        self.invalidate_results()
        self._last_workflow_request=dict(mesh_only=mesh_only,size=size,export_path=export_path,mesh_kwargs=mesh_kwargs)
        self._workflow_scheduler=self._workflow_scheduler or WorkflowScheduler(get_ccx_path(load_config()))
        self._workflow_project=self.project;self._workflow_model=self.solid_model
        self._workflow_task=self._workflow_scheduler.submit(self.project,self.solid_model.path,
            mesh_only=mesh_only,size=size,export_path=export_path,mesh_kwargs=mesh_kwargs,
            face_fingerprints={f.tag:fingerprint_from_face(f) for f in self.solid_model.faces})
        self._solve_task=self._workflow_task
        self._set_solve_busy(True)
        if self._workflow_timer is None:
            self._workflow_timer=QTimer(self);self._workflow_timer.setInterval(100)
            self._workflow_timer.timeout.connect(self._poll_workflow)
        self._workflow_timer.start()
        self.statusBar().showMessage('后台任务已提交，正在准备…')

    def _poll_workflow(self):
        import time
        task=self._workflow_task
        if task is None:return
        if task.state in ('pending','running'):
            labels={'snapshotting':'保存本次模型','loading_geometry':'读取几何','validating':'检查定义',
                'meshing':'生成网格','mesh_retry':'网格加密重试','building_deck':'生成求解文件',
                'saving_preparation':'保存准备数据','solving':'求解中','loading_preparation':'读取准备数据',
                'reading_results':'读取结果','building_fields':'整理结果场','rendering_report':'生成云图和报告',
                'finalizing_results':'整理部件结果','loading_display':'核对归档结果',
                'preparing_display':'准备结果显示','loading_mesh':'读取网格'}
            label=labels.get(task.phase,'后台准备')
            if task.cancel_event.is_set():label='正在取消本次任务'
            self.statusBar().showMessage(f'{label}… 已用 {time.monotonic()-task.started_at:.0f} 秒')
            return
        self._workflow_timer.stop();self._set_solve_busy(False)
        if self._close_after_solve:self.close();return
        if task.state=='cancelled':
            self.statusBar().showMessage(f'任务已取消，输入与日志：{task.work}',15000);return
        if task.state!='completed':
            import json
            quality_path=task.work/'mesh.quality.json'
            if quality_path.is_file():
                try:self._failed_mesh_quality=json.loads(quality_path.read_text(encoding='utf-8'))
                except (ValueError,OSError):pass
            QMessageBox.warning(self,'计算未完成',f'{task.error}\n日志：{task.work}')
            self.statusBar().showMessage(f'任务失败（{task.phase}），日志：{task.work}',15000);return
        if self.project is not self._workflow_project or self.solid_model is not self._workflow_model:
            task.result=None
            self.statusBar().showMessage(f'任务已完成，当前工程已切换；原结果保留在 {task.case_dir}',15000);return
        try:
            from ..solver.project_workflow import reresolve_project
            result=task.result
            visible={p.part_id:p.visible for p in self.project.parts}
            updated=result['project']
            updated.study.geometry_file=str(self.solid_model.path)
            warnings=reresolve_project(updated,self.solid_model)
            if not updated.is_confirmed:
                raise ValueError('当前几何与本次计算不匹配；报告已保留。'+'；'.join(warnings))
            self.project=updated
            for part in self.project.parts:part.visible=visible.get(part.part_id,part.visible)
            self._prepared=result['prepared'];self._solve_mesh_result=self._prepared['mesh_result']
            self._solve_mesh_stats=self._solve_mesh_result.stats;self._last_nset_face_tags=self._prepared['nset_faces']
            self._refresh_project_views()
            if result['mesh_only']:
                self.show_mesh(self._solve_mesh_result.mesh,'有限元网格')
                self.statusBar().showMessage(f'网格已导出：{self.project.study.mesh_file}',10000);return
            data=result['display'];self._result_fields=data['fields'];self._part_views=data['views']
            self._result_context=data.get('context') or {}
            self._last_archive=data['archive'];self._history_case_name=data['case_name']
            self._result_surfaces=result['surfaces']
            diagnostics=data['context'].get('solver_diagnostics',{})
            suffix='（连接待检查）' if diagnostics.get('requires_review') else ''
            self.result_label.setText(f'计算结果={data["case_name"]}{suffix}')
            self.select_result_part('装配合成')
            for act in (self._act_show_mises,self._act_show_disp,self._act_rf_table,self._act_extremes,
                        self._act_open_report,self._act_open_case_dir,self._report_action):act.setEnabled(True)
            self._act_parts.setEnabled(bool(self._part_views))
            if diagnostics.get('requires_review'):
                from ..solver.diagnostics import diagnostic_message
                QMessageBox.warning(self,'连接需要检查',
                    f"{diagnostic_message(diagnostics)}\n请先检查报告。\n{task.case_dir}")
            self.statusBar().showMessage(f'计算和报告已完成{suffix}：{task.case_dir}',15000)
        except Exception as exc:
            log.exception('后台结果载入失败');QMessageBox.warning(self,'结果载入失败',str(exc))
        finally:
            # The window owns only the current arrays. Task history keeps paths
            # and status, rather than retaining every old mesh and result field.
            task.result=None

    def _set_solve_busy(self, busy):
        """求解忙态（模块42 重写）：按 QAction 分类——只禁修改类，查看类保持开放。

        旧实现 menuBar().setEnabled(not busy) 一刀切（禁菜单栏部件会连同全部
        子动作禁用），学生求解期间无法查看历史结果/报告。现改为：busy 时
        _act_modifying 全部禁用、_act_viewing 全部开放（进入时快照各自基线，
        结束时恢复——求解完成分支随后的结果动作启用不受影响）；求解入口自身
        有重复提交守卫，不参与分类。
        """
        if busy:
            self._busy_action_snapshot = {
                id(act): act.isEnabled() for act in (*self._act_modifying, *self._act_viewing)
            }
            for act in self._act_modifying:
                act.setEnabled(False)
            for act in self._act_viewing:
                act.setEnabled(True)
        else:
            snapshot = getattr(self, "_busy_action_snapshot", {}) or {}
            for act in (*self._act_modifying, *self._act_viewing):
                base = snapshot.get(id(act))
                if base is not None:
                    act.setEnabled(base)
            self._busy_action_snapshot = {}
        if busy and self._solve_cancel_button is None:
            from PySide6.QtWidgets import QPushButton
            self._solve_cancel_button = QPushButton('取消计算',self)
            self._solve_cancel_button.clicked.connect(self._cancel_current_solve)
            self.statusBar().addPermanentWidget(self._solve_cancel_button)
        elif not busy and self._solve_cancel_button is not None:
            self.statusBar().removeWidget(self._solve_cancel_button)
            self._solve_cancel_button.deleteLater()
            self._solve_cancel_button = None

    def _cancel_current_solve(self):
        if self._workflow_task is self._solve_task and self._workflow_task is not None:
            self._workflow_scheduler.cancel(self._workflow_task.task_id)
            if self._solve_cancel_button is not None:self._solve_cancel_button.setEnabled(False)
            self.statusBar().showMessage('正在取消当前阶段，输入和日志将保留…')
            return
        if self._scheduler is not None and self._solve_task is not None:
            self._scheduler.cancel(self._solve_task.task_id)
            if self._solve_cancel_button is not None:
                self._solve_cancel_button.setEnabled(False)
            self.statusBar().showMessage('正在停止本次计算，输入和日志将保留…')

    def _poll_solve_task(self) -> None:
        task = self._solve_task
        if task is None or task.state in ("pending", "running"):
            return
        self._solve_timer.stop()
        self._set_solve_busy(False)
        if self._close_after_solve:
            self.close()
            return
        if task.state == 'cancelled':
            self.statusBar().showMessage(f'计算已取消，输入与日志：{task.case_dir}',15000)
            return
        if task.state != "completed":
            log.error("求解失败: %s", task.summary())
            QMessageBox.warning(self, "求解失败", task.error or task.summary())
            self.statusBar().showMessage(f'计算失败，日志：{task.case_dir}',15000)
            return
        try:
            from ..solver.assembly_workflow import finish_analysis
            completed = finish_analysis(self.project, task, self._prepared)
            self._result_fields = completed["fields"]
            self._part_views = completed["views"]
            self._last_archive = completed["archive"]
            self._history_case_name = task.case_dir.name
            diagnostics = completed['summary'].get('solver_diagnostics',{})
            suffix = '（连接待检查）' if diagnostics.get('requires_review') else ''
            self.result_label.setText(f"计算结果={task.case_dir.name}{suffix}")
            self.select_result_part("装配合成")
            for act in (self._act_show_mises, self._act_show_disp, self._act_rf_table,
                        self._act_extremes, self._act_open_report, self._act_open_case_dir, self._report_action):
                act.setEnabled(True)
            self._act_parts.setEnabled(bool(self._part_views))
            self._refresh_project_views()
            if diagnostics.get('requires_review'):
                self.statusBar().showMessage(f'计算已结束，连接需检查；报告：{task.case_dir}',15000)
                from ..solver.diagnostics import diagnostic_message
                QMessageBox.warning(self,'连接需要检查',
                    f"{diagnostic_message(diagnostics)}\n"
                    f"请检查报告中的连接警告；计算结束不能据此判定连接或结构合格。\n{task.case_dir}")
            else:
                self.statusBar().showMessage(f"计算和报告已完成: {task.case_dir}", 8000)
            log.info("真实求解结果已回收并归档: %s", completed["summary"])
        except Exception as exc:
            log.exception("结果回收或归档失败")
            QMessageBox.warning(self, "结果回收失败", str(exc))

    def _recall_result_fields(self, task) -> None:
        from ..solver.fields import build_result_fields
        from ..solver.deck import parse_mesh_inp
        mesh = parse_mesh_inp(Path(self.project.study.mesh_file).read_text(encoding="utf-8"))
        fixed = self._prepared["fixed_sets"] if self._prepared else "FIXED"
        self._result_fields = build_result_fields(sorted(mesh["nodes"]), task.results, task.frd_path, fixed_set=fixed)
        self.select_result_part("装配合成")

    def _compute_result_face_tags(self, tris) -> "np.ndarray | None":  # noqa: ANN001
        """结果表面逐三角形 occ 面 tag（模块39 点面溯源）。

        归属规则 = 「边界三角形角点节点集 ⊆ 面 NSET 节点集」：产品 NSET 来自
        约束（FIXED）与非重力载荷（LOADFACE）（assembly_workflow._prepare），
        经 _last_nset_face_tags（NSET 名 → occ 面 tag）映射；未归属的其余
        边界面 = -1（无挂接定义，UI 明示，不虚报归属）。
        节点集来源优先 MeshResult.node_sets（求解会话），退回 prepared['mesh']
        的 *NSET 解析（历史结果恢复路径）。NSET 数据缺失 → None（不溯源）。
        """
        import numpy as np

        tris = np.asarray(tris, dtype=np.int64)
        active = self._active_result or {}
        node_ids = active.get("node_ids")
        nset_faces = self._last_nset_face_tags or {}
        if node_ids is None or not nset_faces:
            return None
        row_of = {int(n): i for i, n in enumerate(np.asarray(node_ids).ravel())}
        nsets_nodes: dict[str, set] = {}
        mesh_result = self._solve_mesh_result
        if mesh_result is not None and getattr(mesh_result, "node_sets", None):
            for name, gtags in mesh_result.node_sets.items():
                nsets_nodes[str(name)] = {int(g) for g in gtags}
        else:
            mesh = (self._prepared or {}).get("mesh") or {}
            for name, gtags in (mesh.get("nsets") or {}).items():
                nsets_nodes[str(name)] = {int(g) for g in gtags}
        face_rows: dict[int, set] = {}
        for name, occ_tag in nset_faces.items():
            rows = {row_of[g] for g in nsets_nodes.get(str(name), ()) if g in row_of}
            if rows:
                face_rows.setdefault(int(occ_tag), set()).update(rows)
        if not face_rows:
            return None
        tags = np.full(len(tris), -1, dtype=np.int64)
        ambiguous = 0
        for k, tri in enumerate(tris):
            corners = {int(tri[0]), int(tri[1]), int(tri[2])}
            hits = [t for t, rows in face_rows.items() if corners <= rows]
            if len(hits) == 1:
                tags[k] = hits[0]
            elif len(hits) > 1:
                tags[k] = min(hits)  # 共享边退化多归属：确定性取最小 occ tag
                ambiguous += 1
        if ambiguous:
            log.info("结果表面 tag 归属：%d 个三角形多 NSET 命中（取最小 occ tag）", ambiguous)
        n_defined = int((tags >= 0).sum())
        log.info(
            "结果表面 tag 归属：%d/%d 三角形挂接定义面（occ tags=%s），其余 %d 为无挂接定义",
            n_defined, len(tags), sorted({int(t) for t in tags if t >= 0}), len(tags) - n_defined,
        )
        return tags

    def _face_local_mises(self, tag: int) -> float | None:
        """结果云图内某 occ 面的局部 Mises 极值（与整场同源数组，节点并集取 max）。"""
        import numpy as np

        if self._active_result is None or self._result_face_tags is None:
            return None
        tags = np.asarray(self._result_face_tags)
        mask = tags == int(tag)
        if not mask.any():
            return None
        tris = np.asarray(self._active_result["tris"], dtype=np.int64)[mask]
        s_mises = np.asarray(self._active_result["s_mises"], dtype=float)
        nodes = np.unique(tris.ravel())
        return float(np.max(s_mises[nodes]))

    def _face_definitions_text(self, tag: int) -> str:
        """occ 面上挂接的约束/载荷名（跨全部工况遍历；连接面映射属可选扩展未启用）。"""
        parts: list[str] = []
        for lc in self.project.study.load_cases:
            for c in lc.constraints:
                if int(getattr(c, "face_tag", -1)) == int(tag):
                    parts.append(f"固定:{c.name}")
            for ld in lc.loads:
                if int(getattr(ld, "face_tag", -1)) == int(tag):
                    kind_label = {
                        "TractionLoad": "面力", "PressureLoad": "压力",
                        "MomentLoad": "力矩", "GravityLoad": "重力",
                    }.get(type(ld).__name__, type(ld).__name__)
                    parts.append(f"{kind_label}:{ld.name}")
        return "；".join(parts)

    def _on_show_mises(self) -> None:
        if self._active_result is None:
            return
        d = self._active_result
        self._result_view_active = True
        self._show_viewport_page()
        self.viewport.show_result_fields(
            d["points"], d["tris"], d["u"], d["s_mises"], mode="mises",
            face_tags=self._result_face_tags,
        )
        self.refresh_workflow_bar()

    def _on_show_displaced(self) -> None:
        if self._active_result is None:
            return
        from PySide6.QtWidgets import QInputDialog
        scale, accepted = QInputDialog.getDouble(self, "变形显示", "变形放大倍数", 100.0, 0.0, 100000.0, 2)
        if accepted:
            d = self._active_result
            self._result_view_active = True
            self._show_viewport_page()
            self.viewport.show_result_fields(
                d["points"], d["tris"], d["u"], d["s_mises"], mode="displaced", scale=scale,
                face_tags=self._result_face_tags,
            )
            self.refresh_workflow_bar()

    def _on_show_reaction_table(self) -> None:
        if self._result_fields is None:
            return
        from .result_dialogs import ReactionTableDialog

        ReactionTableDialog(self._result_fields, parent=self).exec()

    def _on_show_extremes(self) -> None:
        if self._result_fields is None:
            return
        from .result_dialogs import ExtremesDialog

        ExtremesDialog(self._result_fields, parent=self).exec()

    def _on_open_report(self) -> None:
        import os

        rep = (self._last_archive or {}).get("report_md")
        if not rep or not Path(rep).is_file():
            QMessageBox.warning(self, "打开报告", "尚无已归档的计算报告")
            return
        log.info("打开报告: %s", rep)
        os.startfile(str(rep))  # Windows 关联程序打开 Markdown

    def _on_open_case_dir(self) -> None:
        import os

        d = (self._last_archive or {}).get("case_dir")
        if not d or not Path(d).is_dir():
            QMessageBox.warning(self, "打开产物目录", "尚无已归档的求解产物")
            return
        log.info("打开产物目录: %s", d)
        os.startfile(str(d))  # 资源管理器打开

    def _on_manage_materials(self) -> None:
        """材料库对话框：选择材料写入数据模型（core.set_study_material）。"""
        if self._solve_task is not None and self._solve_task.state in ("pending", "running"):
            QMessageBox.information(self,"材料设置","请等待当前求解结束")
            return
        from PySide6.QtWidgets import QDialog

        from .material_dialog import MaterialDialog

        dlg = MaterialDialog(self, current_name=self.project.study.material.name)
        if dlg.exec() != QDialog.DialogCode.Accepted or dlg.selected is None:
            return
        try:
            from ..core.materials import set_study_material, set_part_material
            if len(self.project.parts) > 1:
                from PySide6.QtWidgets import QInputDialog
                labels = [f"{p.name} ({p.part_id})" for p in self.project.parts]
                chosen, accepted = QInputDialog.getItem(self, "设置材料", "选择部件", labels, 0, False)
                if not accepted:
                    return
                part = self.project.parts[labels.index(chosen)]
                m = set_part_material(self.project, part.part_id, dlg.selected.name)
            else:
                m = set_study_material(self.project, dlg.selected.name)
                if self.project.parts:
                    set_part_material(self.project, self.project.parts[0].part_id, dlg.selected.name)
            self.invalidate_results()
        except KeyError as exc:
            log.error("设置材料失败: %s", exc)
            return
        self._refresh_project_views()
        log.info(
            "零件材料已设置: %s (E=%g MPa, ν=%g, ρ=%g t/mm³)",
            m.name, m.e_modulus_mpa, m.poisson, m.density_t_mm3,
        )
        self.statusBar().showMessage(f"零件材料: {m.name}", 5000)

    def _on_about(self) -> None:
        from .. import __version__

        QMessageBox.about(
            self,
            "关于",
            f"RoboMaster 零件静力分析软件\nrm_sim {__version__}\n\n"
            "数据/网格/求解/报告层均无 GUI 依赖；\n"
            "GL 视口不可用时自动降级 matplotlib Agg 画布。",
        )

    def invalidate_results(self) -> None:
        self._result_surfaces={}
        self._result_fields = None
        self._result_surface = None
        self._active_result = None
        self._part_views = {}
        self._prepared = None
        self._history_mesh_quality = {}
        self._failed_mesh_quality = {}
        self._history_case_name = None
        self._last_archive = None
        self.result_label.setText("结果=无（当前定义需计算）")
        self._result_context = None
        self._update_dashboard()
        for name in ("_act_show_mises", "_act_show_disp", "_act_rf_table", "_act_extremes",
                     "_act_open_report", "_act_open_case_dir", "_act_parts", "_report_action"):
            getattr(self, name).setEnabled(False)
        self.project.dirty = True

    def _load_history_case(self, name):
        self._result_surfaces={}
        from ..solver.history import load_archived_case
        data=load_archived_case(self.project,name)
        self._result_fields=data["fields"]
        self._part_views=data["views"]
        self._last_archive=data["archive"]
        self._prepared={"mesh":data["mesh"]}
        self._history_mesh_quality=data['context'].get('mesh_quality',{})
        self._history_case_name=name
        self._result_context=data.get('context') or {}
        self._update_dashboard()
        diagnostics = data['context'].get('solver_diagnostics', {})
        suffix = ('；求解日志有错误，结果不接受' if diagnostics.get('has_solver_errors') else
                  '；连接待检查' if diagnostics.get('requires_review') else '')
        if data['context'].get('history_reaction_recalculated'):
            suffix += '；反力已重算，原报告未改写'
        self.result_label.setText(f"历史结果={name}（与当前定义独立{suffix}）")
        self.select_result_part("装配合成")
        for act in (self._act_show_mises,self._act_show_disp,self._act_rf_table,self._act_extremes,
                    self._act_open_report,self._act_open_case_dir,self._report_action):
            act.setEnabled(True)
        self._act_parts.setEnabled(bool(self._part_views))


    def _on_case_history(self):
        if self._solve_task is not None and self._solve_task.state in ("pending","running"):
            QMessageBox.information(self,"历史结果","请等待当前计算结束")
            return
        if not self.project.cases:
            QMessageBox.information(self,"历史结果","当前工程没有已完成计算")
            return
        from .result_dialogs import CaseHistoryDialog
        dialog=CaseHistoryDialog(self.project,self)
        def choose(name):
            try:self._load_history_case(name)
            except Exception as exc:
                log.exception("历史结果恢复失败")
                QMessageBox.warning(self,"历史结果恢复失败",str(exc))
        dialog.case_selected.connect(choose)
        dialog.exec()

    def select_result_part(self, name: str) -> None:
        if self._result_fields is None:
            return
        if name in self._result_surfaces:
            self._active_result=self._result_surfaces[name]
            self._result_surface=(self._active_result['points'],self._active_result['tris'])
            self._result_face_tags=self._compute_result_face_tags(self._active_result['tris'])
            self._on_show_mises()
            self.statusBar().showMessage(f'当前结果: {name}',5000)
            return
        from ..solver.fields import field_surface
        from ..solver.deck import parse_mesh_inp
        mesh = self._prepared["mesh"] if self._prepared else parse_mesh_inp(Path(self.project.study.mesh_file).read_text(encoding="utf-8"))
        elements = None if name == "装配合成" else mesh["elsets"].get(name)
        if name != "装配合成" and not elements:
            raise ValueError(f"结果中不存在部件 {name}")
        self._active_result = self._result_surfaces.get(name) or field_surface(mesh, self._result_fields, elements)
        self._result_surface = (self._active_result["points"], self._active_result["tris"])
        self._result_face_tags = self._compute_result_face_tags(self._active_result["tris"])
        self._on_show_mises()
        self._update_dashboard()
        self.statusBar().showMessage(f"当前结果: {name}", 5000)

    def _on_show_assembly_results(self) -> None:
        if not self._part_views:
            return
        from .result_dialogs import AssemblyResultDialog
        parts = {name: {"nodes": len(v.node_ids), "max_u": v.max_u_mag(), "max_mises": v.max_mises()}
                 for name, v in self._part_views.items()}
        composite = {"nodes": len(self._result_fields.node_ids), "max_u": self._result_fields.max_u_mag(),
                     "max_mises": float(self._result_fields.s_mises.max())}
        import json
        ctx = json.loads(Path(self._last_archive["results_json"]).read_text(encoding="utf-8"))
        # 模块49：质量（体积×密度，与计算报告同源）与材料名随卡片展示
        parts_meta: dict[str, dict] = {}
        for k, entry in enumerate(ctx.get("assembly", {}).get("parts") or [], start=1):
            material = entry.get("material") or {}
            density = float(material.get("density_t_mm3") or 0.0)
            volume = float(entry.get("volume_mm3") or 0.0)
            parts_meta[f"PART{k}"] = {"mass_kg": volume * density * 1000.0,
                                      "material": material.get("name") or "—"}
        composite_meta = {"mass_kg": sum(m["mass_kg"] for m in parts_meta.values()),
                          "material": "全部部件（明细见各部件卡片）"}
        dialog = AssemblyResultDialog(parts, composite=composite, parent=self,
            interfaces=ctx.get("assembly",{}).get("interface_pressure",[]),
            parts_meta={"装配合成": composite_meta, **parts_meta},
            report_path=str(self._last_archive.get("report_md") or "") or None)
        dialog.part_selected.connect(self.select_result_part)
        dialog.exec()

    def _on_edit_setup(self) -> None:
        """工况设置：非模态停靠面板（模块38，对标属性管理器）——打开期间可继续点视口选面。"""
        if self.solid_model is None:
            QMessageBox.information(self, "工况设置", "请先导入 STEP")
            return
        if self._solve_task is not None and self._solve_task.state in ("pending", "running"):
            QMessageBox.information(self, "工况设置", "请等待当前求解结束")
            return
        # 工程/几何已更换 → 旧表单失效，先收掉再重建（面清单与部件可能不同）
        if self._setup_dock is not None and (
            self._setup_form is None
            or self._setup_form.project is not self.project
            or self._setup_form.model is not self.solid_model
        ):
            self._close_setup_dock()
        if self._setup_dock is not None:
            self._setup_dock.show()
            self._setup_dock.raise_()
            self.statusBar().showMessage("工况设置面板已打开：点三维视口选面写入 A/B", 5000)
            return
        from .setup_dialog import SetupFormWidget

        form = SetupFormWidget(self.project, self.solid_model, self._selected_face_tag)
        dock = QDockWidget("工况设置（点三维视口选面）", self)
        dock.setObjectName("setup_dock")
        dock.setWidget(form)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dock)
        self.tabifyDockWidget(self.props_dock, dock)
        dock.show()
        dock.raise_()
        self._setup_dock, self._setup_form = dock, form

        def _pick_to_form(tag) -> None:  # noqa: ANN001
            if dock.isVisible() and form.isVisible():
                form.on_viewport_face_picked(int(tag))

        def _highlight_from_combo(combo) -> None:  # noqa: ANN001
            data = combo.currentData()
            if data and int(data[1]) > 0:
                try:
                    self.viewport.highlight_face(int(data[1]))  # 下拉变更 → 反向高亮 3D
                except Exception:
                    log.debug("表单反向高亮失败", exc_info=True)

        self.viewport.face_picked.connect(_pick_to_form)
        form.face_a.currentIndexChanged.connect(lambda _=0: _highlight_from_combo(form.face_a))
        form.face_b.currentIndexChanged.connect(lambda _=0: _highlight_from_combo(form.face_b))
        dock.installEventFilter(self)  # 关闭（X/完成）→ 断开信号 + 应用变更
        log.info("工况设置面板已打开（非模态停靠，点面直连 A/B）")

    def _close_setup_dock(self) -> None:
        """关闭工况设置停靠面板并断开视口接线；有变更则失效结果缓存并刷新视图。"""
        dock, form = self._setup_dock, self._setup_form
        self._setup_dock = self._setup_form = None
        if dock is None:
            return
        try:
            dock.removeEventFilter(self)
        except RuntimeError:
            pass
        if form is not None and form.changed:
            self.invalidate_results()
            self._refresh_project_views()
        try:
            dock.close()
            dock.deleteLater()
        except RuntimeError:
            pass  # 底层 C++ 对象可能已销毁

    def eventFilter(self, obj, event):  # noqa: N802 - Qt 命名
        """工况设置停靠面板关闭检测（visibilityChanged 会被 tab 切换误触发）。"""
        if obj is self._setup_dock and event.type() == QEvent.Type.Close:
            self._close_setup_dock()
        return super().eventFilter(obj, event)

    def _on_advanced_workbench(self):
        from .advanced_dialog import AdvancedWorkbench
        dialog=AdvancedWorkbench(self.project,self)
        if dialog.exec()==dialog.DialogCode.Accepted and dialog.changed:
            self.project.advanced_studies=dialog.project.advanced_studies
            self.project.dirty=True
            self.statusBar().showMessage('高级研究已应用；请保存工程。尚未运行高级求解。',10000)
        dialog.deleteLater()

    def _on_modal_analysis(self) -> None:
        if self.solid_model is None:
            QMessageBox.information(self,"模态分析","请先导入单零件并设置固定面")
            return
        if self._solve_task is not None and self._solve_task.state in ("pending","running"):
            QMessageBox.information(self,"模态分析","请等待当前静力求解结束")
            return
        from PySide6.QtWidgets import QInputDialog
        modes,accepted=QInputDialog.getInt(self,"模态分析","求解阶数（无预应力）",6,1,30)
        if not accepted:return
        if not self.project.project_dir:
            directory=QFileDialog.getExistingDirectory(self,"选择计算目录")
            if not directory:return
            self.project.project_dir=directory
        try:
            from ..solver.modal import run_modal
            from .modal_dialog import ModalResultDialog
            result=run_modal(self.project,self.solid_model,modes)
            self.statusBar().showMessage("模态完成；归一化振型不代表实际振幅",8000)
            ModalResultDialog(result,self.viewport,self).exec()
        except Exception as exc:
            log.exception("模态分析失败")
            QMessageBox.warning(self,"模态分析失败",str(exc))

    def _on_chrono_demo(self) -> None:
        directory=QFileDialog.getExistingDirectory(self,"选择动力学结果目录")
        if not directory:return
        try:
            from ..solver.chrono_adapter import run_chrono
            import datetime
            work=Path(directory)/("chrono_"+datetime.datetime.now().strftime("%Y%m%d_%H%M%S"))
            output=run_chrono(work)
            self._chrono_source=output["result_path"]
            self.show_chrono_results(output)
        except Exception as exc:
            log.exception("Chrono 运行失败")
            QMessageBox.warning(self,"Chrono 运行失败",str(exc))

    def show_chrono_results(self,output) -> None:
        from PySide6.QtWidgets import QDialog,QVBoxLayout,QLabel
        from PySide6.QtGui import QPixmap
        dialog=QDialog(self);dialog.setWindowTitle("Chrono 关节、电机、弹簧与碰撞最小示例")
        layout=QVBoxLayout(dialog)
        text=QLabel("单位 m-kg-s-N。刚体场景不输出碰撞应力或塑性。\n结果: "+output["result_path"])
        text.setWordWrap(True);layout.addWidget(text)
        image=QLabel();pixmap=QPixmap(output["history_png"])
        image.setPixmap(pixmap.scaledToHeight(650,Qt.TransformationMode.SmoothTransformation))
        layout.addWidget(image);dialog.exec()

    def _on_chrono_transfer(self) -> None:
        source,_=QFileDialog.getOpenFileName(self,"选择 Chrono 结果",getattr(self,"_chrono_source",""),"JSON (*.json)")
        if not source:return
        directory=QFileDialog.getExistingDirectory(self,"选择标准梁载荷传递的计算目录")
        if not directory:return
        try:
            from ..solver.chrono_adapter import transfer_load
            import datetime
            work=Path(directory)/("transfer_"+datetime.datetime.now().strftime("%Y%m%d_%H%M%S"))
            result=transfer_load(source,work,Path(__file__).resolve().parents[3]/"samples/beam_100x10x10.step")
            QMessageBox.information(self,"载荷传递完成",f"单时刻、单向准静态载荷已计算。\n结构报告: {result['structural']['solve']['report_md']}\n映射记录: {work/'load_transfer.json'}")
        except Exception as exc:
            log.exception("运动载荷传递失败")
            QMessageBox.warning(self,"载荷传递失败",str(exc))
