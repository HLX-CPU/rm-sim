# -*- coding: utf-8 -*-
"""绑定接触带：按实测几何挑出真正能绑上的从面节点，并给出 *TIE 位置容差。

模块 31（2026-11-02 实测定型）。为什么需要这一层：

连接面按 CAD 面取面片，而 CAD 面常常远大于真实贴合区（销的端部倒角、端片的
外轮廓、钩的整张侧面）。CalculiX 的 *TIE 逐个从面节点投影到主面：只有「投影
落在某个主面片内、且法向距离不超过 POSITION TOLERANCE」的节点才生成 tied MPC，
否则写 WarnNodeMissTiedContact：

    *WARNING in gentiedmpc: no tied MPC
            found; in-face tolerance:    4.6444798689638028E-003

于是警告数量反映的是「CAD 面比贴合区大多少」，不是「传力路径断了」。

实测（20 实体快拆整装，4mm/curvature12/min0.4，133810 单元）两个来源叠加：

* 两个独立划分的曲面之间有离散差：销-基体四面从面节点到主面 0.004—0.012 mm
  （中位 0.0173、p90 0.0511），而求解器默认搜索半径只有 0.004024 mm
  （约 1e-3×典型单元尺寸）——本可绑上的节点被判「too far」。
* CAD 面远大于贴合区：端片-壳体面 666 个从面节点里 285 个间距为 0，其余
  0.345—10.1 mm；把容差放大到覆盖整张面就会跨过真实配合间隙（单边 0.345 mm）
  去绑，属于过度约束。

做法（按节点、不按面片）：从面 = **节点集**（`*SURFACE, TYPE=NODE`），只包含
「投影落在主面片内、法向距离 ≤ 带宽」的节点；`*TIE` 写 `POSITION TOLERANCE=0.07`，
ADJUST=NO 不移动任何坐标。主面保持整张 CAD 面的单元面（远离的节点本来也不会被
搜索到，保留整张面最稳）。带宽默认 0.1 mm，落在离散差 0.012 与配合间隙 0.345
之间。

为什么不用面片级裁剪：面片是裁剪的最小单位，而贴合区边界常常落在面片内部。
按「面片全部节点都能投影」裁会连贴合区内部的节点一起裁掉（整装实测：销螺纹面
只保住约 38% 的可绑节点，等于把绑定面积缩水）；按「面片至少一个节点能投影」
裁又几乎不裁（5672/6000 面片），警告只降到约 3633。节点级从面则正好只绑定
「真正落在贴合区里的那些节点」，整装实测警告 3496→0。

依据：`*TIE` 的从面既可以是单元面也可以是节点集（`*SURFACE, TYPE=NODE`）；
`verify/probe` 记录见 `E:\\rm_sim\\verification\\node_tie_probe_20261102_1902`：
同一 deck 用节点从面后未绑定 0、ΣRF=(0,0,99.99999983) N、|U|max=0.0026 mm。
"""
from __future__ import annotations

import numpy as np

#: 默认绑定带宽 mm。实测：曲面离散差 0.012 mm 级，滑动配合单边间隙 0.345 mm
#: 级——0.1 mm 落在两者之间，既能覆盖离散差，又不会跨过真实间隙。
BOND_BAND_DEFAULT_MM = 0.1

#: 实际写进 *TIE 的 POSITION TOLERANCE 上界 mm。实测（1mm 杆共面贴合，145k 节点）：
#: 节点从面 + 显式容差 0.1 时 CCX 的 tie 搜索退化，同一模型从 20 s 变成 >10 min；
#: 容差 0.07/0.05/0.03/0.01 只需 15—20 s。因此压在这个上界以内。
BOND_TOLERANCE_CAP_MM = 0.07

#: 容差下限 mm。实测（整装 4mm 网格、235908 节点，逐次不同 mesh 的整装求解）：
#: 按几何最短距离给 0.0163 → 56 个 too_far；0.03 → 47；0.05 → 35；0.07 → 28（0.27%）。
#: 原因是 CCX 沿主面片法向判距离，而我方算的是点到二次曲面三角形的最短距离，
#: 两者在圆柱/曲面贴合上相差一个量级；mesh 不同、节点位置不同，残余数也随之变化。
#: 0.07 对 4mm 网格是 1.75% 元素尺寸，仍远小于最小真实配合间隙 0.345 mm。
#: 当前实测「再小会多留 too_far、再大会让共面贴合搜索退化」，故上下界重合于 0.07；
#: 换更细的网格时两者应重新实测，公式保留以便按间距缩放。
BOND_TOLERANCE_FLOOR_MM = 0.07

#: 容差在实测最大间距之上留的余量 mm（吸收数值舍入与输出精度）。
BOND_TOLERANCE_MARGIN_MM = 0.005

#: C3D10 局部面 → 6 个面节点（角点 + 边中点），顺序沿用 Abaqus/CalculiX。
TET10_FACE_NODES = ((0, 1, 2, 4, 5, 6), (0, 1, 3, 4, 8, 7),
                    (1, 2, 3, 5, 9, 8), (0, 2, 3, 6, 9, 7))


def face_node_ids(conn, local_face: int) -> tuple[int, ...]:
    """单元连接表 + 中性局部面 → 该面的节点号（C3D4 只取角点）。"""
    table = TET10_FACE_NODES[local_face]
    if len(conn) < 10:
        table = table[:3]
    return tuple(int(conn[i]) for i in table)


class BondSide:
    """一侧的网格面片集合：面片 → 节点、角点三角形、逐节点坐标。

    C3D10 的面是二次曲面：只用角点三角形做投影判据会和求解器实际使用的曲面
    差一个矢高（整装实测 0.004—0.017 mm，恰好和默认搜索半径同量级）。因此
    每个六节点面按二阶细分拆成 4 个子三角形（角点 + 边中点），让「投影是否
    落在主面内」的判据更接近 CCX 的二次面。
    """

    def __init__(self, facets, rows, eid_sorted, mesh):
        self.facets = tuple(sorted((int(ei), int(li)) for ei, li in facets))
        self.rows = rows
        self.eid_sorted = eid_sorted
        self.mesh = mesh
        self.facet_nodes: dict[tuple[int, int], tuple[int, ...]] = {}
        self.points: dict[int, tuple[float, float, float]] = {}
        sub = []
        for key in self.facets:
            ei, li = key
            conn = mesh["elems"][eid_sorted[rows[ei]]]
            nodes = face_node_ids(conn, li)
            self.facet_nodes[key] = nodes
            for nid in nodes:
                if nid not in self.points:
                    self.points[nid] = tuple(float(x) for x in mesh["nodes"][nid])
            if len(nodes) == 6:
                c0, c1, c2, m01, m12, m20 = (self.points[n] for n in nodes)
                sub.extend([(c0, m01, m20), (m01, c1, m12), (m20, m12, c2), (m01, m12, m20)])
            else:
                n0, n1, n2 = (self.points[n] for n in nodes[:3])
                sub.append((n0, n1, n2))
        self.triangles = (np.asarray(sub, dtype=float) if sub
                          else np.zeros((0, 3, 3), dtype=float))


def surface_probe(points: np.ndarray, tris: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """逐点探测三角形汤。

    返回 ``(gap, gap_in, facet)``：

    * ``gap``：点到三角形汤的最近距离（面内取垂距，面外取到边的距离）；
    * ``gap_in``：仅当投影落在某个三角形**内部**时的垂距，否则 ``inf``
      ——这正是 CalculiX ``gentiedmpc`` 生成 tied MPC 的几何条件；
    * ``facet``：最近的内投影面片下标（无内投影为 -1）。
    """
    count = points.shape[0]
    gap = np.full(count, np.inf)
    gap_in = np.full(count, np.inf)
    facet = np.full(count, -1, dtype=int)
    if not len(tris) or not count:
        return gap, gap_in, facet
    a = tris[:, 0, :]
    ab = tris[:, 1, :] - a
    ac = tris[:, 2, :] - a
    normal = np.cross(ab, ac)
    nn = np.linalg.norm(normal, axis=1)
    nn = np.where(nn > 0.0, nn, np.inf)
    d00 = np.einsum("fk,fk->f", ab, ab)
    d01 = np.einsum("fk,fk->f", ab, ac)
    d11 = np.einsum("fk,fk->f", ac, ac)
    det = d00 * d11 - d01 * d01
    edge = [tris[:, k, :] for k in range(3)]
    step = max(1, min(512, int(4_000_000 / max(1, len(tris)))))
    for start in range(0, count, step):
        block = points[start:start + step]
        ap = block[:, None, :] - a[None, :, :]
        height = np.abs(np.einsum("cfk,fk->cf", ap, normal)) / nn[None, :]
        dq0 = np.einsum("cfk,fk->cf", ap, ab)
        dq1 = np.einsum("cfk,fk->cf", ap, ac)
        detc = np.where(np.abs(det) > 1e-18, det, np.nan)[None, :]
        u = (d11[None, :] * dq0 - d01[None, :] * dq1) / detc
        v = (d00[None, :] * dq1 - d01[None, :] * dq0) / detc
        inside = (u >= -1e-9) & (v >= -1e-9) & (u + v <= 1.0 + 1e-9) & np.isfinite(detc)
        outside = np.full(inside.shape, np.inf)
        for k in range(3):
            pa = edge[k][None, :, :]
            seg = edge[(k + 1) % 3][None, :, :] - pa
            length2 = np.einsum("cfk,cfk->cf", seg, seg)
            length2 = np.where(length2 > 0.0, length2, 1.0)
            t = np.clip(np.einsum("cfk,cfk->cf", block[:, None, :] - pa, seg) / length2, 0.0, 1.0)
            outside = np.minimum(outside, np.linalg.norm(block[:, None, :] - (pa + t[..., None] * seg), axis=2))
        gap[start:start + step] = np.where(inside, height, outside).min(axis=1)
        height_in = np.where(inside, height, np.inf)
        gap_in[start:start + step] = height_in.min(axis=1)
        facet[start:start + step] = height_in.argmin(axis=1)
    facet[~np.isfinite(gap_in)] = -1
    return gap, gap_in, facet


def tie_position_tolerance(gap_inside_max: float) -> float:
    """给出 ``*TIE`` 的 POSITION TOLERANCE；当前实测上下界重合于 0.07 mm。

    为什么不是「实测间距 + 一点余量」就够：CCX 的距离判据沿主面片法向，我方算的
    是点到（二次）曲面三角形的最短距离，曲面上两者相差一个量级。整装逐次实测：
    按几何间距给 0.0163 → 56 个 too_far，0.03 → 47，0.05 → 35，0.07 → 28。
    为什么不是 0.1：节点从面 + 0.1 在共面贴合面上会让 tie 搜索退化（1mm 杆模型
    20 s → >10 min），0.07 及以下都正常。
    """
    value = max(BOND_TOLERANCE_FLOOR_MM,
                float(gap_inside_max) + BOND_TOLERANCE_MARGIN_MM)
    return round(min(BOND_TOLERANCE_CAP_MM, value), 6)


def select_tie_nodes(slave: BondSide, master: BondSide, band_mm: float) -> dict:
    """从面节点级裁剪：只保留「投影落在主面片内、且法向距离 ≤ 带宽」的节点。

    返回 ``{band_mm, slave_nodes, slave_total, tieable, outside,
    gap_inside_max, gap_outside_p50/p90/max, master_facets}``：

    * ``slave_nodes``：写进 ``*SURFACE, TYPE=NODE`` 的节点号（升序）；
    * ``outside``：整张 CAD 面里落在贴合区外的节点数——它们不参与绑定，
      因此不会再有 no_face/too_far 警告；这个数字要如实报给用户，不能当成
      「CAD 面全贴合」。
    """
    if band_mm <= 0.0:
        raise ValueError("绑定带宽必须为正数")
    nodes = sorted(slave.points)
    if not nodes:
        return {"band_mm": float(band_mm), "slave_nodes": (), "slave_total": 0,
                "tieable": 0, "outside": 0, "gap_inside_max": 0.0,
                "gap_outside_p50": None, "gap_outside_p90": None, "gap_outside_max": None,
                "master_facets": len(master.facets)}
    pts = np.asarray([slave.points[n] for n in nodes], dtype=float)
    gap, gap_in, _facet = surface_probe(pts, master.triangles)
    inside = np.isfinite(gap_in) & (gap_in <= band_mm)
    tieable = [n for n, flag in zip(nodes, inside) if flag]
    outside_gap = gap[~inside]
    return {"band_mm": float(band_mm),
            "slave_nodes": tuple(tieable),
            "slave_total": len(nodes),
            "tieable": len(tieable),
            "outside": len(nodes) - len(tieable),
            "gap_inside_max": float(gap_in[inside].max()) if len(tieable) else 0.0,
            "gap_outside_p50": float(np.percentile(outside_gap, 50)) if outside_gap.size else None,
            "gap_outside_p90": float(np.percentile(outside_gap, 90)) if outside_gap.size else None,
            "gap_outside_max": float(outside_gap.max()) if outside_gap.size else None,
            "master_facets": len(master.facets)}
