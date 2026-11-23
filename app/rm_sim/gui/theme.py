# -*- coding: utf-8 -*-
"""现代视觉主题（模块41）：浅色底 + RM 主题色 accent + 面板卡片化 + 等宽日志区。

- ``apply_theme(app)``：全局 QSS（main.py launch_gui 调用；验收脚本亦可显式调用）；
- ``chinese_button_box(box, **labels)``：QDialogButtonBox 标准按钮统一中文
  setText（中文化收口：Ok/Cancel/Close/Yes/No → 确定/取消/关闭/是/否）——
  全部对话框经此助手构造标准按钮盒，杜绝英文默认按钮（评审 high-5/F3）。
"""
from __future__ import annotations

ACCENT = "#D6432C"        # RM 红橙主题色
ACCENT_DARK = "#A93322"
BG = "#F4F5F7"
PANEL = "#FFFFFF"
BORDER = "#D8DCE3"
TEXT = "#263238"
MUTED = "#78909C"

THEME_QSS = f"""
QMainWindow, QDialog {{ background: {BG}; }}
QDockWidget::title {{
    background: {PANEL}; padding: 6px 8px;
    border-bottom: 1px solid {BORDER};
}}
QMenuBar {{ background: {PANEL}; border-bottom: 1px solid {BORDER}; }}
QMenuBar::item {{ padding: 4px 10px; }}
QMenuBar::item:selected {{ background: {ACCENT}; color: white; }}
QMenu {{ background: {PANEL}; border: 1px solid {BORDER}; }}
QMenu::item {{ padding: 4px 24px 4px 12px; }}
QMenu::item:selected {{ background: {ACCENT}; color: white; }}
QPushButton {{
    background: {PANEL}; border: 1px solid {BORDER}; border-radius: 4px;
    padding: 4px 12px; color: {TEXT};
}}
QPushButton:hover {{ border-color: {ACCENT}; color: {ACCENT_DARK}; }}
QPushButton:pressed {{ background: {BG}; }}
QComboBox, QLineEdit, QDoubleSpinBox, QSpinBox {{
    background: white; border: 1px solid {BORDER}; border-radius: 4px; padding: 2px 6px;
}}
QComboBox:focus, QLineEdit:focus, QDoubleSpinBox:focus, QSpinBox:focus {{
    border-color: {ACCENT};
}}
QTableWidget, QTreeWidget, QListWidget {{
    background: {PANEL}; alternate-background-color: {BG};
    border: 1px solid {BORDER};
}}
QPlainTextEdit {{ font-family: 'Consolas', 'Courier New', monospace; background: #FBFCFE; }}
QHeaderView::section {{
    background: {BG}; border: none; border-bottom: 1px solid {BORDER}; padding: 4px;
}}
QTabWidget::pane {{ border: 1px solid {BORDER}; background: {PANEL}; }}
QStatusBar {{ background: {PANEL}; border-top: 1px solid {BORDER}; }}
QToolTip {{ background: {PANEL}; color: {TEXT}; border: 1px solid {ACCENT}; }}
"""


def apply_theme(app) -> None:  # noqa: ANN001
    """应用全局 QSS（幂等）。"""
    app.setStyleSheet(THEME_QSS)


def chinese_button_box(box, **labels):  # noqa: ANN001
    """QDialogButtonBox 标准按钮统一中文（未提供的标准按钮用默认中文映射）。

    labels: ok/cancel/close/yes/no → 自定义文本；返回原 box 便于链式书写。
    """
    from PySide6.QtWidgets import QDialogButtonBox

    defaults = {
        QDialogButtonBox.StandardButton.Ok: "确定",
        QDialogButtonBox.StandardButton.Cancel: "取消",
        QDialogButtonBox.StandardButton.Close: "关闭",
        QDialogButtonBox.StandardButton.Yes: "是",
        QDialogButtonBox.StandardButton.No: "否",
    }
    key_map = {"ok": "Ok", "cancel": "Cancel", "close": "Close", "yes": "Yes", "no": "No"}
    for label_key, std_name in key_map.items():
        std = getattr(QDialogButtonBox.StandardButton, std_name, None)
        if std is None:
            continue
        button = box.button(std)
        if button is not None:
            text = labels.get(label_key) or defaults[std]
            button.setText(text)
    return box
