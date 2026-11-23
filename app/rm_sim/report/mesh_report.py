# -*- coding: utf-8 -*-
"""报告层的网格统计小节（GUI 无关层）。

mesh_stats_section 接受任何带 report_lines() 的统计对象（duck-typing，
不 import mesh 层——层间经入口编排），供后续报告生成器与 GUI 日志复用。
"""
from __future__ import annotations


def mesh_stats_section(stats) -> list[str]:  # noqa: ANN001
    """网格统计报告小节（含质量指标：负 Jacobian / SICN）。"""
    lines = ["【网格统计】"]
    lines.extend(f"  {line}" for line in stats.report_lines())
    return lines
