# -*- coding: utf-8 -*-
"""部件指纹身份（GUI 无关层，core）：跨会话持久化的 CAD 部件身份。

与 ``core/face_identity.py``（面指纹）同一模式：gmsh/OCC 不承诺重导入后
occ 编号不变，持久化身份用「几何指纹」而非裸 tag。装配体的每个部件
（= STEP 中一个 dim=3 实体）的身份 = 名称（人读）+ 几何指纹（匹配用）：
体积 + 质心 + 包围盒角点（内部单位 N-mm-MPa-t-s）。

容差定义（part_matches_fingerprint，rel_tol=1e-6，与面指纹一致）：
- 体积：|Δ| ≤ rel_tol × max(两侧体积)；
- 质心 / bbox 角点：逐轴 |Δ| ≤ rel_tol × 模型包围盒对角线（纯相对无定义
  的零分量用模型长度尺度作基准，由调用方传入 diag）。

同文件重复导入一致性门（fingerprints_equal，rel_tol=1e-9）：逐分量
|Δ| ≤ rel_tol × max(1.0, |a|, |b|)——坐标类小量按绝对、体积类大量按相对。
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class PartFingerprint:
    """部件的持久化几何身份四元组（内部单位 N-mm-MPa-t-s）。"""

    volume_mm3: float
    centroid: tuple[float, float, float]
    bbox_min: tuple[float, float, float]
    bbox_max: tuple[float, float, float]


def part_fingerprint_from_solid(solid_info) -> PartFingerprint:  # noqa: ANN001
    """从实体清单条目构造指纹（duck-typing：volume_mm3/center_of_mass/bbox_min/bbox_max）。"""
    c = np.asarray(solid_info.center_of_mass, dtype=float)
    lo = np.asarray(solid_info.bbox_min, dtype=float)
    hi = np.asarray(solid_info.bbox_max, dtype=float)
    return PartFingerprint(
        volume_mm3=float(solid_info.volume_mm3),
        centroid=(float(c[0]), float(c[1]), float(c[2])),
        bbox_min=(float(lo[0]), float(lo[1]), float(lo[2])),
        bbox_max=(float(hi[0]), float(hi[1]), float(hi[2])),
    )


def _vecs_close(a, b, diag: float, rel_tol: float) -> bool:  # noqa: ANN001
    scale = max(float(diag), 1e-30)
    return not bool(np.any(np.abs(np.asarray(a, dtype=float) - np.asarray(b, dtype=float)) > rel_tol * scale))


def part_matches_fingerprint(solid_info, fp: PartFingerprint, diag: float, rel_tol: float = 1e-6) -> bool:  # noqa: ANN001
    """判断当前实体是否与部件指纹匹配（体积+质心+bbox 角点，见模块 docstring 容差定义）。"""
    vol = float(solid_info.volume_mm3)
    if abs(vol - fp.volume_mm3) > rel_tol * max(abs(fp.volume_mm3), abs(vol), 1e-30):
        return False
    if not _vecs_close(solid_info.center_of_mass, fp.centroid, diag, rel_tol):
        return False
    if not _vecs_close(solid_info.bbox_min, fp.bbox_min, diag, rel_tol):
        return False
    if not _vecs_close(solid_info.bbox_max, fp.bbox_max, diag, rel_tol):
        return False
    return True


def fingerprints_equal(a: PartFingerprint, b: PartFingerprint, rel_tol: float = 1e-9) -> bool:
    """两次导入的指纹是否一致（同文件重导入门）：逐分量混合容差，见模块 docstring。"""
    for va, vb in zip(
        (a.volume_mm3, *a.centroid, *a.bbox_min, *a.bbox_max),
        (b.volume_mm3, *b.centroid, *b.bbox_min, *b.bbox_max),
    ):
        if abs(float(va) - float(vb)) > rel_tol * max(1.0, abs(float(va)), abs(float(vb))):
            return False
    return True
