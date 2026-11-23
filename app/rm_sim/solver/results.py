# -*- coding: utf-8 -*-
"""求解结果解析（GUI 无关层，solver 层）：.dat（*NODE PRINT）与 .frd（DISP）。

解析器沿用 probes/probe2_solve_check.py 实证实现（beamCheck 7 门即基于它）：
- .dat：块头 ``displacements (vx,vy,vz) for set <NAME> and time ...`` /
  ``forces (fx,fy,fz) for set <NAME> and time ...``，行 = ``节点 ux uy uz``；
- .frd：DISP 块（``-4`` 起、``-3`` 止、``-1`` 数据行，12 字符定宽列），
  多时间步时后块覆盖前块（本模块单步静力）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np


class ResultsParseError(RuntimeError):
    """求解结果解析失败（.dat/.frd 缺失或格式不符）。"""


@dataclass
class SolveResults:
    """解析后的求解结果（门计算的数据层输入——变异注入点在此之后、门之前）。"""

    dat_blocks: dict[tuple[str, str], dict[int, list[float]]] = field(default_factory=dict)
    frd_disp: dict[int, list[float]] = field(default_factory=dict)
    dat_path: Path | None = None
    frd_path: Path | None = None

    def rf_block(self, set_name: str) -> dict[int, list[float]]:
        return self.dat_blocks.get(("forces", set_name), {})

    def u_block(self, set_name: str) -> dict[int, list[float]]:
        return self.dat_blocks.get(("displacements", set_name), {})

    def rf_sum(self, set_name: str) -> np.ndarray:
        block = self.rf_block(set_name)
        if not block:
            raise ResultsParseError(f".dat 中没有 forces 块（set={set_name}）")
        return np.sum([block[k] for k in sorted(block)], axis=0)

    def frd_node_count(self) -> int:
        return len(self.frd_disp)


_DAT_HEADER = re.compile(
    r"^\s*(displacements \(vx,vy,vz\)|forces \(fx,fy,fz\)).*set (\w+).*time"
)


def parse_dat(path: str | Path) -> dict[tuple[str, str], dict[int, list[float]]]:
    """解析 .dat 的 *NODE PRINT 块（probe2 配方）。"""
    p = Path(path)
    if not p.is_file():
        raise ResultsParseError(f".dat 文件不存在: {p}")
    blocks: dict[tuple[str, str], dict[int, list[float]]] = {}
    cur: tuple[str, str] | None = None
    with open(p, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            s = line.strip()
            m = _DAT_HEADER.match(s)
            if m:
                cur = (m.group(1).split(" ")[0], m.group(2))
                blocks[cur] = {}
                continue
            if cur and s:
                parts = s.split()
                if len(parts) == 4:
                    try:
                        blocks[cur][int(parts[0])] = [float(x) for x in parts[1:]]
                    except ValueError:
                        pass
    return blocks


def parse_frd_disp(path: str | Path) -> dict[int, list[float]]:
    """解析 .frd 的 DISP 块（probe2 定宽列配方，取最后一次出现=最后时间步）。"""
    p = Path(path)
    if not p.is_file():
        raise ResultsParseError(f".frd 文件不存在: {p}")
    disp: dict[int, list[float]] = {}
    cur = None
    with open(p, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            if line.startswith(" -4") and "DISP" in line:
                cur = "DISP"
            elif line.startswith(" -3"):
                cur = None
            elif cur == "DISP" and line.startswith(" -1"):
                try:
                    n = int(line[3:13])
                    disp[n] = [float(line[13 + 12 * i: 25 + 12 * i]) for i in range(3)]
                except ValueError:
                    continue
    return disp


def load_results(dat_path: str | Path, frd_path: str | Path) -> SolveResults:
    """读 .dat/.frd → SolveResults；两者都必须可解析（成败判定的一半）。"""
    dat_blocks = parse_dat(dat_path)
    frd_disp = parse_frd_disp(frd_path)
    if not dat_blocks:
        raise ResultsParseError(f".dat 中没有任何 *NODE PRINT 数据块: {dat_path}")
    if not frd_disp:
        raise ResultsParseError(f".frd 中没有 DISP 数据: {frd_path}")
    return SolveResults(
        dat_blocks=dat_blocks,
        frd_disp=frd_disp,
        dat_path=Path(dat_path),
        frd_path=Path(frd_path),
    )
