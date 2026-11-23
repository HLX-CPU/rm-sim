# -*- coding: utf-8 -*-
"""面指纹身份（GUI 无关层，core）：跨会话持久化的 CAD 面身份。

调研报告 §7：选面保存项目内身份；gmsh 不承诺重导入后 occ 编号不变——
因此持久化用「面指纹」（面积 + 形心 + 法向，相对容差 1e-6）而非裸 tag。
重开工程时对几何副本重解析（re-resolve）：
- 唯一命中 → 恢复 face_tag（BC 仍指向同一张 CAD 面）；
- 无命中/多命中 → 「需重新确认」+ 显式警告，绝不静默贴错面。

容差定义（rel_tol=1e-6）：
- 面积：|Δ| ≤ rel_tol × max(两侧面积)；
- 形心：逐轴 |Δ| ≤ rel_tol × 模型包围盒对角线（分量可为 0，纯相对无定义，
  用模型长度尺度作基准，由调用方传入 diag）；
- 法向：1 − |n1·n2| ≤ rel_tol（方向含反向——平行反向面由形心区分）。
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class FaceFingerprint:
    """面的持久化身份三元组（内部单位 N-mm-MPa-t-s）。"""

    area_mm2: float
    centroid: tuple[float, float, float]
    normal: tuple[float, float, float]


def fingerprint_from_face(face_info) -> FaceFingerprint:  # noqa: ANN001
    """从面几何信息（duck-typing：area_mm2/center/normal）构造指纹。"""
    c = np.asarray(face_info.center, dtype=float)
    n = np.asarray(face_info.normal, dtype=float)
    n = n / float(np.linalg.norm(n))
    return FaceFingerprint(
        area_mm2=float(face_info.area_mm2),
        centroid=(float(c[0]), float(c[1]), float(c[2])),
        normal=(float(n[0]), float(n[1]), float(n[2])),
    )


def face_matches_fingerprint(face_info, fp: FaceFingerprint, diag: float, rel_tol: float = 1e-6) -> bool:  # noqa: ANN001
    """判断当前面是否与指纹匹配（面积+形心+法向，见模块 docstring 容差定义）。"""
    area = float(face_info.area_mm2)
    if abs(area - fp.area_mm2) > rel_tol * max(abs(fp.area_mm2), abs(area), 1e-30):
        return False
    c = np.asarray(face_info.center, dtype=float)
    c0 = np.asarray(fp.centroid, dtype=float)
    scale = max(float(diag), 1e-30)
    if bool(np.any(np.abs(c - c0) > rel_tol * scale)):
        return False
    n = np.asarray(face_info.normal, dtype=float)
    n = n / float(np.linalg.norm(n))
    n0 = np.asarray(fp.normal, dtype=float)
    n0 = n0 / float(np.linalg.norm(n0))
    if 1.0 - abs(float(np.dot(n, n0))) > rel_tol:
        return False
    return True


def resolve_face(faces, fp: FaceFingerprint, diag: float, rel_tol: float = 1e-6) -> tuple[int | None, str]:  # noqa: ANN001
    """在当前模型面清单中解析指纹 → (occ tag, 状态)。

    状态："ok"（唯一命中）/ "missing"（无命中）/ "ambiguous"（多命中，
    几何歧义——一律不静默贴面，交由上层进入需重新确认流程）。
    """
    hits = [f for f in faces if face_matches_fingerprint(f, fp, diag, rel_tol)]
    if len(hits) == 1:
        return int(hits[0].tag), "ok"
    if not hits:
        return None, "missing"
    return None, "ambiguous"
