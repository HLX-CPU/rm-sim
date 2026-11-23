# -*- coding: utf-8 -*-
"""结果查看对话框（GUI 层）：支反力合力表 + 极值面板 + 装配结果（部件选择）。

数据来自 solver.fields.ResultFields / PartFieldView（GUI 无关层构建）；
对话框只做展示。
"""
from __future__ import annotations
import os
from pathlib import Path

from PySide6.QtCore import Signal, Qt

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)


def _readonly_table(rows: list[tuple[str, str]], headers: list[str]) -> QTableWidget:
    table = QTableWidget(len(rows), len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.horizontalHeader().setStretchLastSection(True)
    table.verticalHeader().setVisible(False)
    table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    for i, row in enumerate(rows):
        for j, v in enumerate(row):
            table.setItem(i, j, QTableWidgetItem(str(v)))
    return table


class ReactionTableDialog(QDialog):
    """支反力及原始RF并列展示，标明扣除已施加载荷的处理口径。"""

    def __init__(self, fields, parent=None) -> None:  # noqa: ANN001
        super().__init__(parent)
        info = fields.source.get('reaction',{})
        label = '支反力' if info.get('supported') else '节点外力（反力修正未验证）'
        self.setWindowTitle(label+"（N，约束节点逐节点）")
        self.resize(900, 520)
        lay = QVBoxLayout(self)
        s = fields.rf_sum
        lay.addWidget(QLabel(
            f"{label}合力 ΣF = ({s[0]:.6f}, {s[1]:.6f}, {s[2]:.6f}) N"
            "（= 各约束节点逐节点求和，可复算）"
        ))
        note = ('支反力 = DAT原始RF − 固定节点已施加载荷；自由节点不在此表内。' if info.get('supported')
                else info.get('reason','缺少反力处理口径，当前按原始节点外力展示。'))
        if info.get('historical_recalculation'):
            note += ' 历史文件未改写；原报告保留原口径，本窗口已按原始输入重算。'
        lay.addWidget(QLabel(note))
        raw = fields.raw_rf_table or fields.rf_table
        raw_sum = [sum(v[i] for v in raw.values()) for i in range(3)]
        rows = [(str(int(nid)),*(f'{v[i]:.6g}' for i in range(3)),
                 *(f'{raw[nid][i]:.6g}' for i in range(3))) for nid,v in sorted(fields.rf_table.items())]
        rows.append(('ΣF（合力）',*(f'{v:.6f}' for v in s),*(f'{v:.6f}' for v in raw_sum)))
        lay.addWidget(_readonly_table(rows,['节点','Fx [N]','Fy [N]','Fz [N]',
                                            '原始RFx [N]','原始RFy [N]','原始RFz [N]']))


class ExtremesDialog(QDialog):
    """极值面板：位移 / von Mises / 反力 极值与合力。"""

    def __init__(self, fields, parent=None) -> None:  # noqa: ANN001
        super().__init__(parent)
        self.setWindowTitle("结果极值面板")
        self.resize(560, 260)
        lay = QVBoxLayout(self)
        rows = []
        u_mag = (fields.u**2).sum(axis=1) ** 0.5
        i = int(u_mag.argmax())
        rows.append(("位移 |U| max", f"{u_mag[i]:.6g} mm", f"节点 {fields.node_ids[i]}"))
        i = int(fields.s_mises.argmax())
        rows.append(("von Mises max", f"{fields.s_mises[i]:.6g} MPa", f"节点 {fields.node_ids[i]}"))
        rf_mag = (fields.rf**2).sum(axis=1) ** 0.5
        i = int(rf_mag.argmax())
        force_label = '支反力' if fields.source.get('reaction',{}).get('supported') else '节点外力'
        rows.append((force_label+" max", f"{rf_mag[i]:.6g} N", f"节点 {fields.node_ids[i]}"))
        s = fields.rf_sum
        rows.append((force_label+"合力 ΣF", f"({s[0]:.6f}, {s[1]:.6f}, {s[2]:.6f}) N", "逐节点求和"))
        lay.addWidget(_readonly_table(rows, ["量", "极值", "位置"]))
        lay.addWidget(QLabel("数值来源：DAT原始RF及INP载荷修正；FRD/VTU位移和应力。反力口径见合力表。"))


class AssemblyResultDialog(QDialog):
    """装配与部件结果仪表（模块20/49）：部件仪表卡片 + 部件明细表。

    parts: {部件名: {"nodes": int, "max_u": float, "max_mises": float}}
    （由 solver.fields.PartFieldView 的摘要构成；对话框只做展示，offscreen
    可构建——main.py --selfcheck 契约输出「装配结果对话框构建成功」）。
    parts_meta（可选）：{部件名: {"mass_kg": float, "material": str}}——质量按
    部件体积×材料密度与计算报告同源；report_path（可选）：最近计算报告路径，
    供『打开诊断』按钮。兼容保留 _table/_combo（行1列1 = max |U| 数值，
    verify_gui_assembly 门依此读取）。
    """

    part_selected = Signal(str)

    def __init__(self, parts: dict[str, dict], composite: dict | None = None, parent=None,
                 interfaces=None, parts_meta: dict[str, dict] | None = None,
                 report_path: str | None = None) -> None:  # noqa: ANN001
        super().__init__(parent)
        self.setWindowTitle("装配与部件结果")
        self.resize(720, 480)
        lay = QVBoxLayout(self)

        self._meta = dict(parts_meta or {})
        self._parts = dict(parts)
        if composite is not None:
            self._parts = {"装配合成": dict(composite), **self._parts}

        if not self._parts:
            # 红线：没有求解证据时只显示状态，不出现任何数值。
            self._combo = QComboBox()
            self._table = QTableWidget(1, 3)
            self._table.setHorizontalHeaderLabels(["量", "值", "说明"])
            self._table.horizontalHeader().setStretchLastSection(True)
            empty = QTableWidgetItem("未计算")
            self._table.setItem(0, 0, QTableWidgetItem("状态"))
            self._table.setItem(0, 1, empty)
            self._table.setItem(0, 2, QTableWidgetItem("当前没有可展示的求解结果"))
            lay.addWidget(QLabel("选择部件："))
            lay.addWidget(self._combo)
            lay.addWidget(self._table)
            self._hint = QLabel("尚无求解结果；运行静力分析后这里显示各部件极值与质量。口径见计算报告。")
            lay.addWidget(self._hint)
            return

        # 仪表卡片条：每部件一张卡（装配合成在最前），数值与求解结果同源。
        card_area = QScrollArea()
        card_area.setWidgetResizable(True)
        card_area.setFixedHeight(150)
        card_host = QWidget()
        cards_lay = QHBoxLayout(card_host)
        cards_lay.setContentsMargins(0, 0, 0, 0)
        self._cards: dict[str, dict] = {}
        for name, info in self._parts.items():
            meta = self._meta.get(name, {})
            mass = meta.get("mass_kg")
            frame = QFrame()
            frame.setFrameShape(QFrame.Shape.StyledPanel)
            if name == "装配合成":
                frame.setStyleSheet("QFrame { border: 2px solid #D6432C; }")
            card_lay = QVBoxLayout(frame)
            title = QLabel(name)
            title.setStyleSheet("font-weight: bold;")
            mises = QLabel(f"max Mises: {float(info.get('max_mises', 0.0)):.6g} MPa")
            mises.setProperty("value", float(info.get('max_mises', 0.0)))
            mises.setProperty("unit", "MPa")
            u_max = QLabel(f"max |U|: {float(info.get('max_u', 0.0)):.6g} mm")
            u_max.setProperty("value", float(info.get('max_u', 0.0)))
            u_max.setProperty("unit", "mm")
            mass_label = QLabel(f"质量: {'—' if mass is None else f'{float(mass):.6g} kg'}")
            if mass is not None:
                mass_label.setProperty("value", float(mass))
                mass_label.setProperty("unit", "kg")
            material = QLabel(f"材料: {meta.get('material') or '—'}")
            for label in (mises, u_max, mass_label, material):
                label.setAlignment(Qt.AlignmentFlag.AlignLeft)
            card_lay.addWidget(title)
            card_lay.addWidget(mises)
            card_lay.addWidget(u_max)
            card_lay.addWidget(mass_label)
            card_lay.addWidget(material)
            cards_lay.addWidget(frame)
            self._cards[name] = {"mises": mises, "u": u_max,
                                 "mass": mass_label, "material": material}
        cards_lay.addStretch(1)
        card_area.setWidget(card_host)
        lay.addWidget(card_area)

        self._combo = QComboBox()
        for name in self._parts:
            self._combo.addItem(name)
        lay.addWidget(QLabel("选择部件："))
        lay.addWidget(self._combo)

        self._table = QTableWidget(0, 3)
        self._table.setHorizontalHeaderLabels(["量", "值", "说明"])
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        lay.addWidget(self._table)

        self._hint = QLabel(
            "数值说明：应力与位移取该部件网格节点的最大值，质量按部件体积×材料密度估算，"
            "均与当前求解结果同源；口径见计算报告。")
        self._hint.setWordWrap(True)
        lay.addWidget(self._hint)
        buttons = QHBoxLayout()
        self._diag_button = QPushButton("打开诊断（计算报告）")
        if report_path and Path(report_path).is_file():
            self._diag_button.clicked.connect(lambda: os.startfile(str(report_path)))
        else:
            self._diag_button.setEnabled(False)
            self._diag_button.setToolTip("没有找到最近计算报告；先求解一次再打开")
        buttons.addWidget(self._diag_button)
        buttons.addStretch(1)
        lay.addLayout(buttons)

        if interfaces:
            rows = [(f"{v['connection']} / {v.get('side', 'SA')}",
                     ", ".join(f"{f:.6g}" for f in v.get("force_n", [])),
                     f"{v['normal_force_n']:.6g}") for v in interfaces]
            lay.addWidget(QLabel("界面力 [N]：由面应力外推后积分，存在离散误差；非直接接触压力输出。"))
            lay.addWidget(_readonly_table(rows, ["连接 / 侧", "Fx, Fy, Fz [N]", "法向压应力积分 [N]"]))

        self._combo.currentTextChanged.connect(self._show_part)
        self._show_part(self._combo.currentText())

    def _show_part(self, name: str) -> None:
        info = self._parts.get(name, {})
        meta = self._meta.get(name, {})
        mass = meta.get("mass_kg")
        rows = [
            ("max von Mises", f"{float(info.get('max_mises', 0.0)):.6g} MPa", "该部件网格节点的应力极值"),
            ("max |U|", f"{float(info.get('max_u', 0.0)):.6g} mm", "该部件网格节点的位移极值"),
            ("质量", "—" if mass is None else f"{float(mass):.6g} kg", "按部件体积×材料密度估算"),
            ("材料", meta.get("material") or "—", "随工程材料设置"),
            ("节点数", str(info.get("nodes", 0)), "该部件网格的节点总数"),
        ]
        self._table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            for j, v in enumerate(row):
                self._table.setItem(i, j, QTableWidgetItem(str(v)))
        self.part_selected.emit(name)


class CaseHistoryDialog(QDialog):
    case_selected = Signal(str)

    def __init__(self, project, parent=None):
        super().__init__(parent)
        from PySide6.QtWidgets import QPushButton, QHBoxLayout
        self.setWindowTitle("历史结果与工况对比")
        self.resize(1050, 480)
        layout = QVBoxLayout(self)
        note=QLabel("双击恢复该次结果。表内力值保留归档时口径；旧RF在打开时按原INP重算，原报告不改写。历史结果不代表当前工况。")
        note.setWordWrap(True)
        layout.addWidget(note)
        self._names = [r.name for r in project.cases]
        rows=[]
        for r in project.cases:
            import json
            from pathlib import Path
            ctx={}
            try:
                ctx=json.loads((Path(project.project_dir)/"cases"/r.name/"results.json").read_text(encoding="utf-8"))
                material=ctx["material"]["name"]
                mesh=str(ctx["mesh"]["mesh_size_mm"])
            except (OSError,ValueError,KeyError):
                material=mesh="未能读取归档"
            rows.append((r.name,r.created,r.load_summary,
                         material,mesh,
                         "—" if r.max_u_mag_mm is None else f"{r.max_u_mag_mm:.8g}",
                         "—" if r.max_mises_mpa is None else f"{r.max_mises_mpa:.8g}",
                         ('支反力 ' if ctx.get('reaction_processing',{}).get('supported') else '原始RF ')+str(r.rf_sum_n)))
        self.table=_readonly_table(rows,["计算","时间","当次载荷","当次材料","网格 [mm]","max|U| [mm]","max Mises [MPa]","归档力值 [N]"])
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.cellDoubleClicked.connect(lambda row,col:self.case_selected.emit(self._names[row]))
        layout.addWidget(self.table)
        buttons=QHBoxLayout()
        open_button=QPushButton("查看所选计算")
        open_button.clicked.connect(self._open_selected)
        buttons.addWidget(open_button)
        close_button=QPushButton("关闭");close_button.clicked.connect(self.accept);buttons.addWidget(close_button)
        layout.addLayout(buttons)
        if rows:self.table.selectRow(len(rows)-1)

    def _open_selected(self):
        row=self.table.currentRow()
        if row>=0:self.case_selected.emit(self._names[row])
