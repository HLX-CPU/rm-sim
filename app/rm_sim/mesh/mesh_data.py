# -*- coding: utf-8 -*-
"""网格数据结构（GUI 无关层）。

MeshData 是求解器无关的内存网格表示（numpy 数组）；
boundary_triangles 沿用 probes/probe8_ui_headless_fallback.py 已验证的
「角点三角 + 去重计次」表面提取配方。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass
class MeshData:
    """points (n,3) + 按 cell 类型分块的连接表 + 点数据（如 U / U_mag / S）。"""

    points: np.ndarray
    cells: dict[str, np.ndarray]
    point_data: dict[str, np.ndarray] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.points = np.ascontiguousarray(self.points, dtype=np.float64)
        if self.points.ndim != 2 or self.points.shape[1] != 3:
            raise ValueError(f"points 必须是 (n,3)，实际 {self.points.shape}")
        self.cells = {
            k: np.ascontiguousarray(v, dtype=np.int64) for k, v in self.cells.items()
        }
        self.point_data = {k: np.asarray(v) for k, v in self.point_data.items()}
        n = self.points.shape[0]
        for name, conn in self.cells.items():
            if conn.ndim != 2:
                raise ValueError(f"cells[{name}] 必须是二维连接表")
            if conn.size and (conn.min() < 0 or conn.max() >= n):
                raise ValueError(f"cells[{name}] 节点索引越界（n={n}）")

    @property
    def num_points(self) -> int:
        return int(self.points.shape[0])

    @property
    def num_cells(self) -> int:
        return int(sum(len(v) for v in self.cells.values()))


_TETRA_KEYS = ("tetra10", "tetra")


def tetra_corners(mesh: MeshData) -> np.ndarray:
    """四面体角点连接表 (m,4)；tetra10 的第 5-10 列为边中点节点，取前 4 列。"""
    for key in _TETRA_KEYS:
        if key in mesh.cells:
            return mesh.cells[key][:, :4]
    raise ValueError(f"仅支持四面体网格（{_TETRA_KEYS}），实际 cell 类型: {sorted(mesh.cells)}")


def boundary_triangles(mesh: MeshData) -> np.ndarray:
    """表面三角形 (k,3)：仅被一个四面体使用的角点三角（probe8 配方）。"""
    corners = tetra_corners(mesh)
    faces = np.concatenate(
        [
            corners[:, [0, 2, 1]],
            corners[:, [0, 1, 3]],
            corners[:, [1, 2, 3]],
            corners[:, [0, 3, 2]],
        ]
    )
    faces.sort(axis=1)
    uniq, counts = np.unique(faces, axis=0, return_counts=True)
    return uniq[counts == 1]


_TET_FACE_COMBOS = ((0, 2, 1), (0, 1, 3), (1, 2, 3), (0, 3, 2))


def boundary_faces_corners(tets: np.ndarray) -> np.ndarray:
    """四面体（角点连接，行为位置索引）的边界面三角 (k,3)（probe8 去重计次配方）。

    返回的行是调用方 points 数组的**位置索引**（调用方保证与标量场行对齐）。
    """
    tets = np.asarray(tets, dtype=np.int64)
    count: dict[tuple, int] = {}
    first: dict[tuple, list[int]] = {}
    for ei in range(tets.shape[0]):
        for combo in _TET_FACE_COMBOS:
            face = [int(tets[ei, k]) for k in combo]
            key = tuple(sorted(face))
            if key not in count:
                count[key] = 0
                first[key] = face
            count[key] += 1
    result = [first[key] for key, c in count.items() if c == 1]
    if not result:
        raise ValueError("没有边界面（网格退化？）")
    return np.asarray(result, dtype=np.int64)


def demo_mesh() -> MeshData:
    """自检/演示用单位立方体：8 角点 + 1 体心 → 12 个 tetra4；点标量 = 到体心距离。"""
    pts = np.array(
        [
            [0.0, 0.0, 0.0],
            [1.0, 0.0, 0.0],
            [1.0, 1.0, 0.0],
            [0.0, 1.0, 0.0],
            [0.0, 0.0, 1.0],
            [1.0, 0.0, 1.0],
            [1.0, 1.0, 1.0],
            [0.0, 1.0, 1.0],
            [0.5, 0.5, 0.5],
        ],
        dtype=np.float64,
    )
    center_idx = 8
    quads = (
        (0, 1, 2, 3),
        (4, 5, 6, 7),
        (0, 1, 5, 4),
        (1, 2, 6, 5),
        (2, 3, 7, 6),
        (0, 3, 7, 4),
    )
    tets = []
    for a, b, c, d in quads:
        tets.append((a, b, c, center_idx))
        tets.append((a, c, d, center_idx))
    dist = np.linalg.norm(pts - pts.mean(axis=0), axis=1)
    return MeshData(
        points=pts,
        cells={"tetra": np.array(tets, dtype=np.int64)},
        point_data={"dist": dist},
    )
