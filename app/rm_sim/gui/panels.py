# -*- coding: utf-8 -*-
"""主窗口面板（GUI 层）：工况树 / 属性面板 / 日志面板 + 日志桥接。"""
from __future__ import annotations

import logging
import sys

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)


class CaseTreePanel(QTreeWidget):
    """装配树（原工况树扩展，模块 13）：工程 → 装配（部件/材料/可见性）
    → 研究 → 材料/工况 → 约束/载荷。

    双击部件 → part_isolate_requested(part_id)（视口隔离显示该部件）；
    双击装配节点 → show_all_requested()（恢复全部部件按部件着色）。
    """

    part_isolate_requested = Signal(str)
    show_all_requested = Signal()
    part_renamed = Signal(str, str)  # (part_id, 新名)：F2 重命名（模块41）

    _ROLE_KIND = Qt.ItemDataRole.UserRole + 0  # ("assembly"|"part"|"study", 载荷值)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setHeaderLabels(["对象", "类型 / 摘要"])
        self.setAlternatingRowColors(True)
        self.setColumnWidth(0, 190)
        self.setMinimumWidth(220)
        # F2 重命名（模块41）：仅部件名条目可编辑（load_project 时置 flags）
        from PySide6.QtWidgets import QAbstractItemView

        self.setEditTriggers(QAbstractItemView.EditTrigger.EditKeyPressed)
        self._loading = False
        self.itemChanged.connect(self._on_item_changed)

    def _on_item_changed(self, item) -> None:  # noqa: ANN001
        if self._loading:
            return
        data = item.data(0, self._ROLE_KIND)
        if isinstance(data, tuple) and len(data) == 2 and data[0] == "part" and data[1]:
            self.part_renamed.emit(str(data[1]), item.text(0))

    def load_project(self, project) -> None:  # noqa: ANN001 - core.Project（避免循环类型依赖）
        self._loading = True
        try:
            self._load_project_inner(project)
        finally:
            self._loading = False

    def _load_project_inner(self, project) -> None:  # noqa: ANN001
        self.clear()
        root = QTreeWidgetItem([project.name, "工程"])
        self.addTopLevelItem(root)

        # 装配节点（模块 13）：部件/材料/可见性
        parts = list(getattr(project, "parts", []))
        asm_item = QTreeWidgetItem([f"装配（{len(parts)} 部件）", "双击恢复全部显示"])
        asm_item.setData(0, self._ROLE_KIND, ("assembly", None))
        root.addChild(asm_item)
        for p in parts:
            try:
                from ..core.materials import part_material

                mat = part_material(project, p).name
            except KeyError:
                mat = f"未知:{p.material_name}" if p.material_name else project.study.material.name
            item = QTreeWidgetItem(
                [
                    p.name or p.part_id,
                    f"{p.part_id} · 材料={mat if p.material_confirmed else '待指定'} · "
                    + ("可见" if p.visible else "隐藏"),
                ]
            )
            item.setData(0, self._ROLE_KIND, ("part", p.part_id))
            # 部件名可编辑（F2 重命名，模块41）；面引用走 part_id+几何指纹不受影响
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
            asm_item.addChild(item)
        asm_item.setExpanded(True)

        study = project.study
        study_item = QTreeWidgetItem([study.name, "研究"])
        study_item.setData(0, self._ROLE_KIND, ("study", None))
        root.addChild(study_item)
        study_item.addChild(
            QTreeWidgetItem(
                [study.material.name, f"材料 E={study.material.e_modulus_mpa:g} MPa"]
            )
        )
        for lc in study.load_cases:
            case_item = QTreeWidgetItem([lc.name, "工况"])
            study_item.addChild(case_item)
            for c in lc.constraints:
                case_item.addChild(QTreeWidgetItem([c.name, c.summary()]))
            for ld in lc.loads:
                case_item.addChild(QTreeWidgetItem([ld.name, ld.summary()]))
        for conn in project.connections:
            asm_item.addChild(QTreeWidgetItem([conn.name, conn.summary()]))
        root.setExpanded(True)
        study_item.setExpanded(True)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        item = self.itemAt(event.position().toPoint())
        if item is not None:
            data = item.data(0, self._ROLE_KIND)
            if isinstance(data, tuple) and len(data) == 2:
                kind, value = data
                if kind == "part" and value:
                    self.part_isolate_requested.emit(str(value))
                    return
                if kind == "assembly":
                    self.show_all_requested.emit()
                    return
        super().mouseDoubleClickEvent(event)


class FaceCardPanel(QFrame):
    """选中面卡片（点面交互核心）：把『点面』从 tag 数字升级为可见语义。

    - 点击视口面后显示：部件名 / 面积 mm² / 法向 / 已挂定义数（约束/载荷/连接）；
    - 点空白或未选中时显示「未选中」占位（验收④）；
    - 内置『撤销选面』按钮（Ctrl+Z 同源）：undo_requested 信号 → 主窗口历史栈回退。
    """

    undo_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 4, 6, 4)
        self._title = QLabel("选中面：未选中")
        bold = QFont()
        bold.setBold(True)
        self._title.setFont(bold)
        self._detail = QLabel("在三维视口中点击模型表面选择面")
        self._detail.setWordWrap(True)
        self._detail.setStyleSheet("color: #606060;")
        self._undo_btn = QPushButton("撤销选面")
        self._undo_btn.setToolTip("恢复选面历史栈中的上一面（Ctrl+Z）")
        self._undo_btn.clicked.connect(self.undo_requested.emit)
        self._undo_btn.setEnabled(False)
        lay.addWidget(self._title)
        lay.addWidget(self._detail)
        lay.addWidget(self._undo_btn)
        self.last_payload: dict | None = None  # 最近一次 set_face 的原始数据（验收对照用）

    def set_face(self, info: dict | None) -> None:
        """info 键：tag/part_name/area_mm2/normal/n_constraints/n_loads/n_connections；
        结果云图视图（模块39）附加：is_result_view/definitions_text/local_max_mises，
        以及 undefined=True（tag=-1 无挂接定义面——如实明示，不虚报归属）。"""
        self.last_payload = info
        if not info:
            self._title.setText("选中面：未选中")
            self._detail.setText("在三维视口中点击模型表面选择面")
            self._detail.setStyleSheet("color: #606060;")
            return
        if info.get("undefined"):
            self._title.setText("选中面：无挂接定义")
            self._detail.setText(
                "该面无约束/载荷挂接定义（自由边界面）。\n"
                "产品 NSET 仅覆盖约束与非重力载荷面；连接贴合面映射见后续扩展。"
            )
            self._detail.setStyleSheet("color: #8d6e63;")
            return
        if info.get("is_modal_view"):
            # 模态结果仪表（模块56）：点面 → 该面节点组 |振型| 最大值。
            # 归一化振型声明必须随数值出现（不虚报实际振幅）。
            amp = info.get("modal_amplitude")
            freq = info.get("modal_frequency_hz")
            order = info.get("modal_order")
            nodes = info.get("face_node_count")
            self._title.setText(f"选中面：面 {info['tag']}（模态 · 归一化振型）")
            lines = [
                f"该面 |振型| 最大值：{amp:.6g}" if amp is not None else "该面 |振型| 最大值：无数据",
                f"所在阶次：第 {order} 阶 · {freq:.6g} Hz" if freq is not None else "所在阶次：—",
                f"该面节点组：{nodes} 个节点" if nodes is not None else "该面节点组：—",
                "归一化振型不代表实际振幅（显示幅度仅用于观察形状）。",
            ]
            self._detail.setText("\n".join(lines))
            self._detail.setStyleSheet("")
            return
        nx, ny, nz = (float(v) for v in info["normal"])
        self._title.setText(f"选中面：{info['part_name']} · 面 {info['tag']}")
        lines = [
            f"部件：{info['part_name']}",
            f"面积：{float(info['area_mm2']):.6g} mm²",
            f"法向：({nx:.4g}, {ny:.4g}, {nz:.4g})",
            f"已挂定义：约束 {info['n_constraints']} · 载荷 {info['n_loads']}"
            f" · 连接 {info['n_connections']}",
        ]
        if info.get("is_result_view"):
            defs = info.get("definitions_text") or ""
            lines.append(f"挂接定义：{defs}" if defs else "挂接定义：无")
            local = info.get("local_max_mises")
            lines.append(
                f"面内 Mises 极值：{local:.6g} MPa" if local is not None
                else "面内 Mises 极值：无数据"
            )
        self._detail.setText("\n".join(lines))
        self._detail.setStyleSheet("")

    def set_undo_enabled(self, enabled: bool) -> None:
        self._undo_btn.setEnabled(bool(enabled))

    @property
    def title_text(self) -> str:
        return self._title.text()

    @property
    def is_unselected(self) -> bool:
        return self.last_payload is None


class SolveDashboardCard(QFrame):
    """结果仪表卡片（模块42）：最大 von Mises / 最大 |U| / 最小安全系数 + 可信度徽章。

    红线：无求解证据（含 configured_not_computed / prepared_not_computed 阶段）
    一律显示『未计算/未验证』且不出现任何数值，不得显示为已求解。
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 4, 6, 4)
        head = QHBoxLayout()
        self._title = QLabel("结果仪表")
        bold = QFont()
        bold.setBold(True)
        self._title.setFont(bold)
        self.badge = QLabel("未计算/未验证")
        self.badge.setStyleSheet("color: #78909C;")
        head.addWidget(self._title)
        head.addStretch(1)
        head.addWidget(self.badge)
        lay.addLayout(head)
        self.lbl_mises = self._metric_row("最大 von Mises [MPa]")
        self.lbl_u = self._metric_row("最大 |U| [mm]")
        self.lbl_sf = self._metric_row("最小安全系数（屈服/σmax）")
        self.last_payload: dict | None = None

    def _metric_row(self, label: str) -> QLabel:
        row = QHBoxLayout()
        cap = QLabel(label)
        cap.setStyleSheet("color: #606060;")
        value = QLabel("—")
        row.addWidget(cap)
        row.addStretch(1)
        row.addWidget(value)
        self.layout().addLayout(row)
        return value

    def set_state(self, data: dict | None) -> None:
        """data: {solved, badge, badge_level, max_mises, max_u, min_sf}。"""
        self.last_payload = dict(data) if data else None
        if not data or not data.get("solved"):
            self.badge.setText("未计算/未验证")
            self.badge.setStyleSheet("color: #78909C;")
            self.lbl_mises.setText("—")
            self.lbl_u.setText("—")
            self.lbl_sf.setText("—")
            return
        level = data.get("badge_level", "ok")
        colors = {"ok": "#1b5e20", "warn": "#e65100", "bad": "#b71c1c"}
        self.badge.setText(str(data.get("badge") or "已求解"))
        self.badge.setStyleSheet(f"color: {colors.get(level, '#1b5e20')}; font-weight: bold;")
        mises = data.get("max_mises")
        u = data.get("max_u")
        sf = data.get("min_sf")
        self.lbl_mises.setText(f"{float(mises):.6g} MPa" if mises is not None else "—")
        self.lbl_u.setText(f"{float(u):.6g} mm" if u is not None else "—")
        self.lbl_sf.setText(f"{float(sf):.4g}" if sf is not None else "—")


class PropertiesPanel(QWidget):
    """属性面板：选中面卡片（顶部）+ 当前对象键值表（只读）+ 几何行内导入按钮。"""

    import_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        lay = QVBoxLayout(self)
        self.dashboard_card = SolveDashboardCard()  # 属性区顶部：结果仪表卡片（模块42）
        self.face_card = FaceCardPanel()  # 选中面卡片（点面交互核心）
        self._title = QLabel("属性")
        self._table = QTableWidget(0, 2)
        self._table.setHorizontalHeaderLabels(["属性", "值"])
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.verticalHeader().setVisible(False)
        self._table.setMinimumWidth(240)
        lay.addWidget(self.dashboard_card)
        lay.addWidget(self.face_card)
        lay.addWidget(self._title)
        lay.addWidget(self._table)

    def set_inline_import(self, visible: bool) -> None:
        """几何未导入时在『几何』行内提供导入按钮（首启引导③）。"""
        row = next(
            (
                r
                for r in range(self._table.rowCount())
                if self._table.item(r, 0) is not None
                and self._table.item(r, 0).text() == "几何"
            ),
            None,
        )
        if row is None:
            return
        if visible:
            btn = QPushButton("导入 STEP…")
            btn.setToolTip("导入 STEP 几何以开始分析")
            btn.clicked.connect(self.import_requested.emit)
            self._table.setCellWidget(row, 1, btn)
        else:
            self._table.removeCellWidget(row, 1)

    def show_object(self, title: str, rows: list[tuple[str, str]]) -> None:
        self._title.setText(title)
        self._table.setRowCount(len(rows))
        for i, (key, value) in enumerate(rows):
            k = QTableWidgetItem(key)
            v = QTableWidgetItem(value)
            flags = Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled
            k.setFlags(flags)
            v.setFlags(flags)
            self._table.setItem(i, 0, k)
            self._table.setItem(i, 1, v)


class EmptyStatePanel(QWidget):
    """首启空状态占位页（首启引导，评审 high：首屏空视口劝退）。

    三个入口：导入 STEP / 打开示例工程（examples/ 束）/ 打开使用说明；
    替代空视口+空树的『无从下手』首屏。
    """

    import_requested = Signal()
    open_example_requested = Signal()
    open_docs_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title = QLabel("欢迎使用 RoboMaster 仿真软件")
        bold = QFont()
        bold.setBold(True)
        bold.setPointSize(16)
        title.setFont(bold)
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub = QLabel("从下面任一入口开始你的第一个分析：")
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.btn_import = QPushButton("导入 STEP…")
        self.btn_example = QPushButton("打开示例工程")
        self.btn_docs = QPushButton("打开使用说明")
        for btn in (self.btn_import, self.btn_example, self.btn_docs):
            btn.setMinimumWidth(180)
            lay.addWidget(btn, 0, Qt.AlignmentFlag.AlignHCenter)
        from .theme import ACCENT, ACCENT_DARK

        self.btn_import.setStyleSheet(
            f"QPushButton{{background:{ACCENT};color:white;border:none;font-weight:bold;}}"
            f"QPushButton:hover{{background:{ACCENT_DARK};}}"
        )
        self.btn_import.clicked.connect(self.import_requested.emit)
        self.btn_example.clicked.connect(self.open_example_requested.emit)
        self.btn_docs.clicked.connect(self.open_docs_requested.emit)
        hint = QLabel("提示：顶部流程条随时显示当前进度与每一步缺少的输入")
        hint.setStyleSheet("color: #606060;")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(title)
        lay.addWidget(sub)
        lay.addSpacing(12)
        lay.addWidget(hint)


class WorkflowBar(QWidget):
    """主窗口顶部 7 步流程条（导入→材料→约束载荷→网格→求解→结果→报告）。

    每步完成态由工程状态推导（set_steps 注入：done/todo/error + 缺少输入
    提示）；未完成步的提示文字直接显示在按钮上，点击发 step_clicked(序号)
    由主窗口直达对应对话框/动作。
    """

    step_clicked = Signal(int)

    def __init__(self, steps: list[str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        from PySide6.QtWidgets import QHBoxLayout

        self._names = list(steps)
        self.states: list[dict] = []
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 4, 8, 4)
        lay.setSpacing(6)
        self._buttons: list[QPushButton] = []
        for i, name in enumerate(self._names):
            btn = QPushButton(f"{i + 1} {name}")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda checked=False, idx=i: self.step_clicked.emit(idx))
            lay.addWidget(btn)
            self._buttons.append(btn)
        lay.addStretch(1)

    def set_steps(self, states: list[dict]) -> None:
        """states: [{name, state: done|todo|error, hint}]；hint=缺少的输入。"""
        self.states = list(states)
        for i, btn in enumerate(self._buttons):
            st = self.states[i] if i < len(self.states) else {}
            name = st.get("name") or (self._names[i] if i < len(self._names) else f"步骤{i + 1}")
            state = st.get("state", "todo")
            hint = (st.get("hint") or "").strip()
            if state == "done":
                btn.setText(f"✓ {name}")
                btn.setStyleSheet("QPushButton{color:#1b5e20;font-weight:bold;}")
                btn.setToolTip(f"{name}：已完成")
            elif state == "error":
                btn.setText(f"{i + 1} {name}\n{hint}" if hint else f"{i + 1} {name}")
                btn.setStyleSheet(
                    "QPushButton{background:#ffcdd2;color:#b71c1c;font-weight:bold;}"
                )
                btn.setToolTip(f"{name}：{hint}")
            else:
                btn.setText(f"{i + 1} {name}\n{hint}" if hint else f"{i + 1} {name}")
                btn.setStyleSheet("QPushButton{color:#616161;}")
                btn.setToolTip(f"{name}：{hint or '未完成'}")


class LogPanel(QPlainTextEdit):
    """日志面板（只读，自动滚动，限制块数防膨胀）。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setReadOnly(True)
        self.setMaximumBlockCount(2000)
        self.setMinimumHeight(120)
        self.setPlaceholderText("日志输出…")


class _GuiLogBridge(QObject):
    message = Signal(str)


class GuiLogHandler(logging.Handler):
    """logging → Qt 信号（跨线程投递到 GUI 线程追加到日志面板）。"""

    def __init__(self) -> None:
        super().__init__(logging.INFO)
        self.bridge = _GuiLogBridge()
        self.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s", "%H:%M:%S")
        )

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
        except Exception:
            return
        self.bridge.message.emit(msg)


_SHARED_HANDLER: GuiLogHandler | None = None
_LOGGER = logging.getLogger("rm_sim")


def setup_gui_logging(panel: LogPanel) -> None:
    """把 rm_sim logger 接到日志面板（多窗口共享同一 handler）+ stdout。"""
    global _SHARED_HANDLER
    if _SHARED_HANDLER is None:
        _SHARED_HANDLER = GuiLogHandler()
        _LOGGER.setLevel(logging.INFO)
        _LOGGER.addHandler(_SHARED_HANDLER)
        stdout_handler = logging.StreamHandler(sys.stdout)
        stdout_handler.setFormatter(logging.Formatter("[log] %(message)s"))
        _LOGGER.addHandler(stdout_handler)
        _LOGGER.propagate = False
    _SHARED_HANDLER.bridge.message.connect(panel.appendPlainText)
