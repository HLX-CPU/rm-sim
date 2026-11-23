# -*- coding: utf-8 -*-
"""求解规模估算与内存硬闸门（GUI 无关层）。

旧流水线曾出现高内存报告，保守闸门暂保留。2026-11-02 接续复测：23.6万节点
单独解析峰值0.23 GiB，完整网格/建deck准备峰值1.10 GiB。不能把旧流水线的
19.21 GiB归因于parse_mesh_inp；求解阶段峰值尚未核实。

诚实原则（与项目其它模块一致）：
- Python侧暂沿用旧流水线保守上界，不能称作当前解析器的实测占用；
  CCX 求解峰值未标定就明确返回 None，只说明「未知」，绝不拿经验公式冒充预算。
- refuse 级别的判定只看 Python 侧——这是已知且量化的部分，也是刚才卡死机器的直接原因。
"""
from __future__ import annotations

import ctypes
import sys

# 旧流水线保守上界，保留以免在CCX峰值未核实前放宽闸门。
# 当前准备阶段测量见 E:/rm_sim/verification/resume_ds_memory_20261102_1412/full/。
PYTHON_BYTES_PER_NODE = 25_200

# CCX 峰值标定表：每项必须来自真实求解的峰值内存采样；空表时估算返回 None。
CCX_CALIBRATION: list[dict] = []

PREP_REFUSE_SHARE = 0.85  # 光是读网格就超过可用内存这个比例 → 直接拒绝
PREP_WARN_SHARE = 0.55
DEFAULT_DOF_WARN = 1_500_000


class _MemoryStatusEx(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_ulong),
        ("dwMemoryLoad", ctypes.c_ulong),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


def available_memory_gb() -> float | None:
    """本机当前可用物理内存 [GB]；平台不支持或 API 失败时返回 None。"""
    if sys.platform != "win32":
        return None
    try:
        status = _MemoryStatusEx()
        status.dwLength = ctypes.sizeof(_MemoryStatusEx)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return None
        return float(status.ullAvailPhys) / (1024.0 ** 3)
    except Exception:  # noqa: BLE001 - 取不到就明确返回 None，不猜
        return None


def python_peak_gb(nodes: int) -> float:
    """旧流水线保守上界 [GiB]，不是解析器单独占用的实测值。"""
    return float(int(nodes)) * PYTHON_BYTES_PER_NODE / (1024.0 ** 3)


def solve_scale(nodes: int, elements: int, inp_bytes: int | None = None) -> dict:
    """规模摘要：节点/单元/自由度估算/INP 体积/Python 侧内存（C3D10 每节点 3 自由度）。"""
    nodes = int(nodes)
    elements = int(elements)
    return {
        "nodes": nodes,
        "elements": elements,
        "dof": 3 * nodes,
        "inp_mb": (float(inp_bytes) / (1024.0 ** 2)) if inp_bytes else None,
        "python_peak_gb": python_peak_gb(nodes),
    }


def estimate_ccx_gb(scale: dict) -> float | None:
    """CCX 求解峰值估算 [GB]：无实测标定返回 None（不编造）。"""
    if not CCX_CALIBRATION:
        return None
    point = max(CCX_CALIBRATION, key=lambda row: float(row.get("dof", 0)))
    base_dof = float(point.get("dof", 0.0))
    base_gb = float(point.get("peak_gb", 0.0))
    if base_dof <= 0 or base_gb <= 0:
        return None
    dof = float(scale.get("dof", 0))
    return base_gb if dof <= base_dof else dof / base_dof * base_gb


def suggest_size_mm(scale: dict, available_gb: float, current_size_mm: float) -> float | None:
    """「把网格尺寸放大到多少」的建议：按单元数 ∝ size^-3 用当前实测节点数反推。"""
    target_nodes = PREP_WARN_SHARE * available_gb * (1024.0 ** 3) / PYTHON_BYTES_PER_NODE
    nodes = float(scale.get("nodes", 0))
    if nodes <= 0 or target_nodes <= 0 or nodes <= target_nodes:
        return None
    return float(current_size_mm) * (nodes / target_nodes) ** (1.0 / 3.0)


def memory_verdict(scale: dict, available_gb: float | None = None,
                   current_size_mm: float | None = None) -> dict:
    """规模判定：{"level": ok|warn|refuse, "message", "advice", "python_gb", "ccx_gb"}。

    - refuse：Python 侧读网格的内存已超过可用内存 PREP_REFUSE_SHARE → 不许算；
    - warn  ：占用在 PREP_WARN_SHARE 之上，或求解峰值未标定 → 提醒后可继续；
    - ok    ：规模在已实测范围内。
    """
    dof = int(scale.get("dof", 0))
    available = available_gb if available_gb is not None else available_memory_gb()
    python_gb = float(scale.get("python_peak_gb") or python_peak_gb(scale.get("nodes", 0)))
    ccx_gb = estimate_ccx_gb(scale)
    head = (f"模型 {int(scale.get('nodes', 0)):,} 个节点 / {int(scale.get('elements', 0)):,} 个单元"
            f"（自由度约 {dof:,}）").replace(",", " ")
    detail = head + f"；准备阶段的保守内存预算约 {python_gb:.1f} GiB（沿用旧流水线上界）"
    if available:
        detail += f"，本机当前可用 {available:.1f} GB"
    detail += "。"

    suggested = None
    if available and python_gb > PREP_REFUSE_SHARE * available:
        if current_size_mm:
            suggested = suggest_size_mm(scale, available, current_size_mm)
        advice = ("内存不够，已拒绝计算——继续跑会把系统内存吃光、整台电脑失去响应。"
                  "请先把网格尺寸放大" + (f"到约 {suggested:.1f} mm" if suggested else "一档") + "再试。")
        return {"level": "refuse", "message": detail, "advice": advice,
                "python_gb": python_gb, "ccx_gb": ccx_gb, "suggested_size_mm": suggested}

    tight = bool(available and python_gb > PREP_WARN_SHARE * available)
    big = dof >= DEFAULT_DOF_WARN
    parts = []
    if tight:
        parts.append("内存偏紧：算之前请先关闭 SolidWorks、浏览器等占内存的程序。")
    if big:
        if ccx_gb is None:
            parts.append("求解阶段还需要额外内存（本软件尚未实测标定）；建议分阶段运行——"
                         "先生成求解文件并退出，再单独启动求解器。")
        else:
            parts.append(f"估算求解峰值另需 {ccx_gb:.1f} GB。")
    level = "warn" if (tight or big) else "ok"
    return {"level": level, "message": detail, "advice": " ".join(parts),
            "python_gb": python_gb, "ccx_gb": ccx_gb, "suggested_size_mm": None}
