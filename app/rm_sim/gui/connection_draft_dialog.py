# -*- coding: utf-8 -*-
"""连接草稿审核对话框（GUI 层）：一次看清所有贴合面，逐条选类型后统一采用。

不自动写工程：几何只说明「两面贴在一起」，是不是力学连接由用户决定
（默认全选、默认粘接，但每条都能改类型或取消）。
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

_KIND_LABELS = [
    ("bonded", "粘接（界面不分离、不滑移）"),
    ("contact", "可分离接触 + 摩擦 μ=0.3"),
    ("pin", "简化销轴（零间隙绑定）"),
]
_LABEL_TO_KIND = {label: kind for kind, label in _KIND_LABELS}


class AssemblyConnectionDraftDialog(QDialog):
    """连接草稿审核：勾选 + 选类型 → accepted / kinds。"""

    def __init__(self, draft: dict, parent=None, project=None) -> None:  # noqa: ANN001
        super().__init__(parent)
        self._project = project  # 模块47：连接复核入口需要工程上下文（可 None）
        self.setWindowTitle("自动识别配合面 — 连接草稿")
        self.resize(1180, 700)
        from ..core.connections import consistent_draft_directions
        draft = consistent_draft_directions(draft)
        self._draft = draft
        self.accepted: list[int] = []
        self.kinds: dict[int, str] = {}

        indexed = sorted(enumerate(draft.get("candidates") or []),
                         key=lambda pair: -float(pair[1].get("common_area_mm2") or 0.0))
        self._source_indices = [index for index, _ in indexed]
        candidates = [item for _, item in indexed]
        self._rows = candidates
        self._checks: list[QCheckBox] = []
        self._combos: list[QComboBox] = []

        layout = QVBoxLayout(self)
        head = QLabel(
            f"共 {len(candidates)} 处贴合面，覆盖 {len(draft.get('covered_parts') or [])} / "
            f"{draft.get('body_count', 0)} 个部件；几何零距离但无共同面 {len(draft.get('point_or_edge') or [])} 处，"
            f"检查失败 {int(draft.get('geometry_errors') or 0) + len(draft.get('patch_errors') or [])} 项。"
        )
        head.setWordWrap(True)
        layout.addWidget(head)

        hint = QLabel(
            "默认按「粘接」近似：界面既不分离也不滑移，是学生级静力分析里最常用、最容易收敛的假设。"
            "如果某个面实际会张开或滑移（例如钩子挂在端片上），把它改成「可分离接触」。"
            "轴承与销钉按零间隙绑定简化，报告里会写明该假设。"
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)

        table = QTableWidget(len(candidates), 8)
        table.setHorizontalHeaderLabels(
            ["采用", "部件 A", "部件 B", "面 A", "面 B", "公共面积 mm²", "几何", "连接类型"]
        )
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.horizontalHeader().setStretchLastSection(True)
        for row, item in enumerate(candidates):
            check = QCheckBox()
            check.setChecked(True)
            check.setToolTip("取消勾选 = 这一处不建立连接")
            self._checks.append(check)
            table.setCellWidget(row, 0, check)
            values = [
                str(item.get("name_a", item.get("part_a", ""))),
                str(item.get("name_b", item.get("part_b", ""))),
                str(item.get("face_a", "")),
                str(item.get("face_b", "")),
                f"{float(item.get('common_area_mm2') or 0.0):.4g}",
                "平面" if item.get("geometry_type") == "planar" else "曲面",
            ]
            for col, value in enumerate(values, start=1):
                cell = QTableWidgetItem(value)
                cell.setFlags(Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled)
                table.setItem(row, col, cell)
            combo = QComboBox()
            for kind, label in _KIND_LABELS:
                combo.addItem(label, kind)
            recommended = str(item.get("recommended") or "bonded")
            index = next((i for i, (kind, _) in enumerate(_KIND_LABELS) if kind == recommended), 0)
            combo.setCurrentIndex(index)
            self._combos.append(combo)
            table.setCellWidget(row, 7, combo)
        table.resizeColumnsToContents()
        self._table = table
        layout.addWidget(table)

        buttons = QHBoxLayout()
        select_all = QPushButton("全选")
        select_none = QPushButton("全不选")
        select_all.clicked.connect(lambda: [c.setChecked(True) for c in self._checks])
        select_none.clicked.connect(lambda: [c.setChecked(False) for c in self._checks])
        apply_btn = QPushButton("采用所选连接")
        apply_btn.setDefault(True)
        apply_btn.clicked.connect(self._on_apply)
        # 模块47：连接复核决策闭环（GUI 路径）——needs_review 连接的逐条决策入口。
        review_btn = QPushButton("连接复核…")
        review_btn.setToolTip("对上次求解出现未绑定节点的连接逐条决策：保留粘接/改为可分离接触/删除")
        review_btn.clicked.connect(self._on_review)
        review_btn.setEnabled(self._project is not None)
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.reject)
        for widget in (select_all, select_none, apply_btn, review_btn, close_btn):
            buttons.addWidget(widget)
        layout.addLayout(buttons)

        # 需人工判断的对：只说事实，不猜连接
        edge = draft.get("point_or_edge") or []
        if edge:
            notes = "；".join(
                f"{row.get('name_a')} ↔ {row.get('name_b')}（{row.get('classification')}）" for row in edge[:6]
            )
            more = "" if len(edge) <= 6 else f" 等 {len(edge)} 对"
            extra = QLabel(f"以下部件几何上抵接但没有共同面，可能是点/线接触，未自动建连接：{notes}{more}")
            extra.setWordWrap(True)
            layout.addWidget(extra)
        truncated = draft.get("truncated") or []
        if truncated:
            loss = "；".join(
                f"{row.get('part_a')} ↔ {row.get('part_b')} 丢弃 {row.get('dropped_patches')} 个补丁"
                f"（{float(row.get('dropped_area_mm2') or 0.0):.3g} mm²）"
                for row in truncated[:4]
            )
            warn = QLabel(f"贴合面数量超过单对上限，已按面积截断：{loss}")
            warn.setWordWrap(True)
            layout.addWidget(warn)

    def _on_review(self) -> None:
        """打开连接复核对话框（工程上下文缺失时按钮已禁用）。"""
        from .connection_review_dialog import ConnectionReviewDialog

        ConnectionReviewDialog(self._project, parent=self).exec()

    def _on_apply(self) -> None:
        self.accepted = [self._source_indices[i] for i, check in enumerate(self._checks) if check.isChecked()]
        self.kinds = {self._source_indices[i]: str(combo.currentData()) for i, combo in enumerate(self._combos)}
        if not self.accepted:
            self.accepted = []
        self.accept()
