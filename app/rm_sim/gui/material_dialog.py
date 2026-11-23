# -*- coding: utf-8 -*-
"""材料库对话框（GUI 层）：选择材料 + 自定义材料增删改（内置库只读）。

校验与持久化全部走 core.materials（GUI 无关层），校验失败以弹窗显示原因，
不崩溃；本对话框不被任何验收脚本自动驱动（GUI 冒烟仅构造检查）。
"""
from __future__ import annotations

import logging

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from ..core import materials
from ..core.project import Material
from .theme import chinese_button_box

log = logging.getLogger("rm_sim.gui.material_dialog")


class MaterialDialog(QDialog):
    """材料库：表格列出 内置+自定义；支持 选择 / 新增 / 更新 / 删除自定义。"""

    def __init__(self, parent=None, current_name: str = "") -> None:  # noqa: ANN001
        super().__init__(parent)
        self.setWindowTitle("材料库（单位：E/屈服 MPa，ρ t/mm³）")
        self.resize(640, 440)
        self.selected: Material | None = None
        self._current_name = current_name

        lay = QVBoxLayout(self)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["名称", "E [MPa]", "ν", "ρ [t/mm³]", "屈服 [MPa]", "来源"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        lay.addWidget(self.table)

        form = QFormLayout()
        self.ed_name = QLineEdit()
        self.ed_e = QDoubleSpinBox()
        self.ed_nu = QDoubleSpinBox()
        self.ed_rho = QDoubleSpinBox()
        self.ed_yield = QDoubleSpinBox()
        for ed, lo, hi, dec in (
            (self.ed_e, 0.0, 10_000_000.0, 3),
            (self.ed_nu, 0.0, 0.499, 4),
            (self.ed_rho, 0.0, 100.0, 12),
            (self.ed_yield, 0.0, 100_000.0, 3),
        ):
            ed.setRange(lo, hi)
            ed.setDecimals(dec)
        self.ed_yield.setSpecialValueText("（未提供）")
        form.addRow("名称", self.ed_name)
        form.addRow("弹性模量 E [MPa]", self.ed_e)
        form.addRow("泊松比 ν", self.ed_nu)
        form.addRow("密度 ρ [t/mm³]", self.ed_rho)
        form.addRow("屈服强度 [MPa]", self.ed_yield)
        lay.addLayout(form)

        btns = QHBoxLayout()
        btn_ok = QPushButton("选择并应用")
        btn_add = QPushButton("新增自定义")
        btn_upd = QPushButton("更新所选自定义")
        btn_del = QPushButton("删除所选自定义")
        btn_ok.setDefault(True)
        for b in (btn_ok, btn_add, btn_upd, btn_del):
            btns.addWidget(b)
        close_box = chinese_button_box(QDialogButtonBox(QDialogButtonBox.StandardButton.Close))
        close_box.rejected.connect(self.reject)
        btns.addWidget(close_box)
        lay.addLayout(btns)

        btn_ok.clicked.connect(self._on_apply)
        btn_add.clicked.connect(self._on_add)
        btn_upd.clicked.connect(self._on_update)
        btn_del.clicked.connect(self._on_remove)
        self.table.itemSelectionChanged.connect(self._on_row_selected)

        self.refresh_table()

    # ---- 表格 ----
    def _materials_in_table(self) -> list[Material]:
        return materials.all_materials()

    def refresh_table(self) -> None:
        custom_names = {m.name for m in materials.load_custom_materials()}
        rows = self._materials_in_table()
        self.table.setRowCount(len(rows))
        for i, m in enumerate(rows):
            values = [
                m.name,
                f"{m.e_modulus_mpa:g}",
                f"{m.poisson:g}",
                f"{m.density_t_mm3:g}",
                "—" if m.yield_strength_mpa is None else f"{m.yield_strength_mpa:g}",
                "自定义" if m.name in custom_names else "内置",
            ]
            for col, v in enumerate(values):
                item = QTableWidgetItem(v)
                self.table.setItem(i, col, item)
            if m.name == self._current_name:
                self.table.selectRow(i)

    def _selected_material(self) -> Material | None:
        row = self.table.currentRow()
        if row < 0:
            return None
        rows = self._materials_in_table()
        return rows[row] if row < len(rows) else None

    def _on_row_selected(self) -> None:
        m = self._selected_material()
        if m is None:
            return
        self.ed_name.setText(m.name)
        self.ed_e.setValue(m.e_modulus_mpa)
        self.ed_nu.setValue(m.poisson)
        self.ed_rho.setValue(m.density_t_mm3)
        self.ed_yield.setValue(m.yield_strength_mpa or 0.0)

    def _read_form(self) -> Material | None:
        """表单 → Material；数值非法弹窗并返回 None（不抛出、不崩溃）。"""
        try:
            m = Material(
                name=self.ed_name.text().strip(),
                e_modulus_mpa=self.ed_e.value(),
                poisson=self.ed_nu.value(),
                density_t_mm3=self.ed_rho.value(),
                yield_strength_mpa=self.ed_yield.value() or None,
            )
            materials.validate_material(m)  # 先校验，错误弹窗
            return m
        except materials.MaterialValidationError as exc:
            QMessageBox.warning(self, "材料参数不合法", str(exc))
            return None
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "输入错误", f"{type(exc).__name__}: {exc}")
            return None

    # ---- 动作 ----
    def _on_apply(self) -> None:
        m = self._selected_material()
        if m is None:
            QMessageBox.information(self, "选择材料", "请先在表格中选择一种材料")
            return
        self.selected = m
        self.accept()

    def _on_add(self) -> None:
        m = self._read_form()
        if m is None:
            return
        try:
            materials.add_custom_material(m)
        except materials.MaterialValidationError as exc:
            QMessageBox.warning(self, "新增失败", str(exc))
            return
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "新增失败", f"{type(exc).__name__}: {exc}")
            return
        self._current_name = m.name
        self.refresh_table()
        log.info("新增自定义材料: %s (E=%g MPa, ν=%g, ρ=%g t/mm³)", m.name, m.e_modulus_mpa, m.poisson, m.density_t_mm3)

    def _on_update(self) -> None:
        old = self._selected_material()
        if old is None:
            QMessageBox.information(self, "更新材料", "请先选择要更新的自定义材料")
            return
        m = self._read_form()
        if m is None:
            return
        try:
            materials.update_custom_material(old.name, m)
        except materials.MaterialValidationError as exc:
            QMessageBox.warning(self, "更新失败", str(exc))
            return
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "更新失败", f"{type(exc).__name__}: {exc}")
            return
        self._current_name = m.name
        self.refresh_table()
        log.info("更新自定义材料: %s", m.name)

    def _on_remove(self) -> None:
        m = self._selected_material()
        if m is None:
            return
        try:
            materials.remove_custom_material(m.name)
        except materials.MaterialValidationError as exc:
            QMessageBox.warning(self, "删除失败", str(exc))  # 内置材料走到这里（库中不存在）
            return
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "删除失败", f"{type(exc).__name__}: {exc}")
            return
        log.info("删除自定义材料: %s", m.name)
        self.refresh_table()
