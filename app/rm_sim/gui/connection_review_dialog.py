# -*- coding: utf-8 -*-
"""连接复核决策对话框（模块47 GUI 路径）。

逐连接展示复核证据（从面总节点/可绑节点/贴合区外最近距离——绑定带注释；
关联节点数：从面节点/未绑定节点——诊断 coverage），三枚举决策
保留粘接 / 改为可分离接触 / 删除连接，写回工程并刷新列表。
未决策且 needs_review 的连接在求解入口被拦截（prepare_analysis 同一口径），
本对话框不替代该拦截，只提供决策入口。

入口：自动连接草稿对话框的「连接复核…」（需要工程上下文）；批处理路径见
solver/connection_review.py。物理假设与决策语义见 app/docs/connections.md。
"""
from __future__ import annotations

from pathlib import Path

from ..core.connections import (REVIEW_DECISION_LABELS, apply_connection_decision,
                                undecided_connections)
from ..solver.connection_review import project_review_records

try:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QListWidget,
                                   QListWidgetItem, QPushButton, QVBoxLayout)
except ImportError:  # pragma: no cover - GUI 无关层静态扫描只查 solver/core/cad/report
    raise


class ConnectionReviewDialog(QDialog):
    """逐连接复核：左侧连接列表（含 needs_review/决策徽标），右侧证据与决策。"""

    def __init__(self, project, diagnostics: dict | None = None, parent=None) -> None:  # noqa: ANN001
        super().__init__(parent)
        self.setWindowTitle("连接复核 — 保留粘接 / 改为可分离接触 / 删除")
        self.resize(860, 560)
        self._project = project
        self._diagnostics = diagnostics or {}
        self._inp_text = self._last_solve_inp_text()
        self._records = project_review_records(project, self._diagnostics, self._inp_text)
        self._has_inp = self._inp_text is not None
        self.applied: list[dict] = []

        layout = QVBoxLayout(self)
        head = QLabel(
            f"决策单元 = 逐连接（{len(self._records)} 条）。needs_review = 上次求解该连接"
            "从面出现未绑定节点；未决策的连接会被求解入口拦截。节点计数只是关联证据，"
            "不据此自动决策；SPC 自由度已固定的诊断编号单列，不与未绑定混算。"
        )
        head.setWordWrap(True)
        layout.addWidget(head)

        self._list = QListWidget()
        self._refresh_list()
        layout.addWidget(self._list)

        self._detail = QLabel("（选择左侧连接查看证据）")
        self._detail.setWordWrap(True)
        layout.addWidget(self._detail)

        buttons = QHBoxLayout()
        self._btn_keep = QPushButton("保留粘接")
        self._btn_contact = QPushButton("改为可分离接触")
        self._btn_remove = QPushButton("删除连接")
        for btn in (self._btn_keep, self._btn_contact, self._btn_remove):
            buttons.addWidget(btn)
        layout.addLayout(buttons)

        self._status = QLabel(self._status_text())
        layout.addWidget(self._status)

        close = QPushButton("关闭")
        buttons.addWidget(close)

        self._list.currentItemChanged.connect(self._on_select)
        self._btn_keep.clicked.connect(lambda: self._decide("keep_bonded"))
        self._btn_contact.clicked.connect(lambda: self._decide("switch_contact"))
        self._btn_remove.clicked.connect(lambda: self._decide("remove"))
        close.clicked.connect(self.reject)

    # ---------------------------------------------------------------- 展示
    def _last_solve_inp_text(self) -> str | None:
        """最近一次求解的 INP 文本（绑定带证据来源）；无求解证据 → None。"""
        for conn in self._project.connections:
            evidence = (getattr(conn, "review_evidence", {}) or {}).get("last_solve") or {}
            case_dir, job = evidence.get("case_dir"), evidence.get("job")
            if case_dir and job:
                path = Path(case_dir) / f"{job}.inp"
                try:
                    return path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
        return None

    def _status_text(self) -> str:
        undecided = undecided_connections(self._project)
        return (f"未决策且需复核：{len(undecided)} 条"
                + ("——求解入口将拦截。" if undecided else "（可求解）"))

    def _refresh_list(self) -> None:
        self._records = project_review_records(self._project, self._diagnostics, self._inp_text)
        self._list.clear()
        for record in self._records:
            label = REVIEW_DECISION_LABELS.get(record["decision"], record["decision"])
            flag = "⚠需复核" if record["needs_review"] else ""
            item = QListWidgetItem(f"#{record['index']} 「{record['name']}」 "
                                   f"[{record['type']}] {record['part_pair'][0]}↔{record['part_pair'][1]}"
                                   f" — 决策：{label}{flag}")
            item.setData(Qt.ItemDataRole.UserRole, record)
            self._list.addItem(item)

    def _on_select(self, current, _previous) -> None:  # noqa: ANN001
        if current is None:
            self._detail.setText("（选择左侧连接查看证据）")
            return
        record = current.data(Qt.ItemDataRole.UserRole)
        band = record.get("bond_band") or {}
        evidence = (record.get("review_evidence") or {}).get("last_solve") or {}
        lines = [
            f"#{record['index']} 「{record['name']}」 [{record['type']}]",
            f"部件对：{record['part_pair'][0]} ↔ {record['part_pair'][1]}",
            f"needs_review（上次求解出现未绑定从面节点）：{'是' if record['needs_review'] else '否'}",
            f"关联节点数：从面 {record.get('slave_surface_nodes', '—')} / "
            f"未绑定 {record.get('reported_missed_nodes', '—')} / "
            f"SPC 自由度已固定 {record.get('dof_inactive_on_surface', '—')}",
        ]
        if band:
            lines.append(
                f"绑定带（{band.get('band_mm')} mm）：从面总节点 {band.get('slave_total')} / "
                f"可绑节点 {band.get('tieable')} / 贴合区外 {band.get('outside')} 个"
                f"（最近距离 {band.get('gap_outside_range_mm')} mm）；主面 {band.get('master_facets')} 面片")
        elif not self._has_inp:
            lines.append("绑定带证据：无最近求解 INP（尚未求解或证据不可读）——如实标注")
        else:
            lines.append("绑定带证据：本算例 INP 无「** 绑定带」注释（未启用绑定带或共节点模式）——如实标注")
        if evidence:
            lines.append(f"最近求解：{evidence.get('job')} @ {evidence.get('case_dir')}")
        self._detail.setText("\n".join(lines))

    # ---------------------------------------------------------------- 决策
    def _current_record(self) -> dict | None:
        item = self._list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item is not None else None

    def _decide(self, decision: str) -> None:
        record = self._current_record()
        if record is None:
            self._status.setText("请先选择一条连接。")
            return
        conn = self._project.connections[record["index"] - 1]
        if conn.name != record["name"]:
            self._status.setText(f"连接列表已过期（#{record['index']} 现为「{conn.name}」），请关闭后重开。")
            return
        try:
            result = apply_connection_decision(
                self._project, conn, decision,
                evidence={"review_source": "connection_review_dialog"})
        except Exception as exc:  # noqa: BLE001 - 决策错误显式呈现，不静默
            self._status.setText(f"决策失败：{exc}")
            return
        self.applied.append({"index": record["index"], "name": record["name"],
                             "decision": decision,
                             "removed": result["removed"] is not None,
                             "switched": result["replacement"] is not None})
        self._refresh_list()
        self._status.setText(self._status_text()
                             + f"　最近决策：「{record['name']}」→ "
                               f"{REVIEW_DECISION_LABELS.get(decision, decision)}")

    # ---------------------------------------------------------------- 批处理共口径
    def undecided_names(self) -> list[str]:
        return [c.name for c in undecided_connections(self._project)]
