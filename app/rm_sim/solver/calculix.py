# -*- coding: utf-8 -*-
"""CalculiX 探测骨架（GUI 无关层）。

模块 1 只做本地路径探测；求解任务调度、inp 写出与结果回收由后续模块实现。
注意（config.json ccxVersionCheckNote）：官方 CalculiX 对 ``-v`` 按设计 exit 201
（stop() -> exit(201)），ccx_launcher.exe 已把「唯一参数为 -v」的 201 归一化为 0；
真实求解调用不受影响。功能级验证应使用 config 的 ccxVerifyCmd 跑单元素算例。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..config import get_ccx_path


@dataclass
class SolverInfo:
    """本地求解器信息（不发起新进程）。"""

    ccx_path: str
    ccx_exists: bool
    real_ccx_path: str = ""
    note: str = ""


def probe_solver(cfg: dict) -> SolverInfo:
    """从 config.json 读 ccxPath / realCcxPath 并检查文件存在性。"""
    ccx = get_ccx_path(cfg)
    real = str(cfg.get("realCcxPath", "") or "")
    note = str(cfg.get("ccxVersion", "") or "")
    return SolverInfo(
        ccx_path=ccx,
        ccx_exists=Path(ccx).is_file(),
        real_ccx_path=real,
        note=note,
    )
