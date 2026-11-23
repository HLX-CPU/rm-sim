# -*- coding: utf-8 -*-
"""约束/载荷 → CalculiX 语义转换器（GUI 无关层，solver 层）。

调研报告 §7 语义红线（本模块的职责就是把红线变成类型与数值）：
- 压力 → 加载面所属**单元面集**的 *DLOAD P：本模块产出 PressureDLoadRef
  （entries = (体积单元索引, 局部面索引)，绝不是节点力列表）——
  禁止按质心面积折算成节点 CLOAD 近似；
- 面力 → *CLOAD 节点力（总力均分到面节点；等效面压 = 总力/面积 仅作核对视图）；
- 力矩 → 加载面等效节点力偶（f_i ∝ 轴×r_i 线性分布，对轴矩精确守恒；
  合力与非轴矩分量为网格对称性的小残差，随转换结果一并报告）；
- 重力 → *DLOAD 的 GRAV 标签（行格式 `Eall, GRAV, g, nx, ny, nz`；
  ccx 2.22 实证无 *GRAV 独立关键字——calinput 对 *GRAV 报
  "cannot be interpreted"，正确写出见 solver/deck.py，模块 7 实测）。

实际 .inp 文本写出（含中性局部面号 → CalculiX P1..P4 面码映射）属于
「CalculiX静力求解与任务调度」模块；求解级数值门也在该模块验收。

本模块只用 numpy/stdlib。occ 面几何信息以 duck-typing 传入
（tag/bbox_min/bbox_max/area_mm2/normal/center——即 cad.FaceInfo 的字段），
不 import cad 层（层间经入口编排，见 ARCHITECTURE §2）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import numpy as np

# 四面体局部面组合（中性编号 0..3，与 probes/probe8 边界面配方一致；
# CalculiX C3D4 的 P1..P4 面码映射由求解模块的 deck writer 完成）
TET_LOCAL_FACES = ((0, 2, 1), (0, 1, 3), (1, 2, 3), (0, 3, 2))


def safe_part_label(part_id: str) -> str:
    """part_id → CalculiX 集合名安全片段（非字母数字下划线 → _；空入空出）。"""
    if not part_id:
        return ""
    return re.sub(r"[^A-Za-z0-9_]", "_", part_id) or "P"


class ConversionError(RuntimeError):
    """约束/载荷 → 求解器语义转换失败（几何/网格不满足转换前提）。"""


def _unit(v) -> np.ndarray:  # noqa: ANN001
    arr = np.asarray(v, dtype=float)
    norm = float(np.linalg.norm(arr))
    if not np.all(np.isfinite(arr)) or norm <= 0.0:
        raise ConversionError(f"向量非法（零向量或非有限值）: {v!r}")
    return arr / norm


def normalize_direction(direction) -> np.ndarray:  # noqa: ANN001
    """方向向量单位化（载荷定义存原向量，转换时统一归一）。"""
    return _unit(direction)


# ---------------------------------------------------------------- 压力：单元面集
@dataclass(frozen=True)
class PressureDLoadRef:
    """*DLOAD P 的单元面集引用（语义红线：不是节点力列表）。

    entries: (体积单元索引, 中性局部面索引 0..3)；单元面数恒 >0 才算有效引用。
    part_id：载荷挂接部件（空=单零件全局语义）；非空时集合名带部件前缀
    PRESS_{part}_FACE_{tag}——装配体多部件同名/同号面不串件（模块 14）。
    """

    kind: str = "element_face_set"
    face_tag: int = 0
    set_name: str = ""
    entries: tuple[tuple[int, int], ...] = ()
    total_area_mm2: float = 0.0
    pressure_mpa: float = 0.0
    part_id: str = ""

    def summary(self) -> str:
        part = f"［部件 {self.part_id}］" if self.part_id else ""
        return (
            f"*DLOAD P 单元面集 {self.set_name}: {len(self.entries)} 个单元面, "
            f"面积 {self.total_area_mm2:.6g} mm², 压力 {self.pressure_mpa:g} MPa{part} (occ面{self.face_tag})"
        )


def build_face_element_map(
    points: np.ndarray,
    tets: np.ndarray,  # (m,4) 四面体角点连接
    face_infos,  # noqa: ANN001 - duck-typing cad.FaceInfo
    distance_fn=None,  # noqa: ANN001 - (tag, point)->float，bbox 歧义裁决
    pad_rel: float = 1e-6,
) -> dict[int, list[tuple[int, int]]]:
    """体积网格边界面 → occ 面 tag 归类：{tag: [(单元索引, 局部面索引), ...]}。

    分类：边界面三角的质心按 occ 面 bbox（含相对容差）唯一命中即归类；
    多候选（几何歧义，如曲面/倒角邻接）时用 distance_fn 最近距离裁决；
    无候选视为网格与几何不匹配 → ConversionError。
    """
    pts = np.asarray(points, dtype=float)
    tets = np.asarray(tets, dtype=np.int64)
    if tets.ndim != 2 or tets.shape[1] != 4:
        raise ConversionError(f"tets 必须是 (m,4) 四面体角点连接，实际 {tets.shape}")

    # 边界面检测（probe8 角点三角配方 + 局部面索引追踪）
    owners: dict[tuple, list[tuple[int, int]]] = {}
    for ei in range(len(tets)):
        for li, combo in enumerate(TET_LOCAL_FACES):
            key = tuple(sorted(int(tets[ei, k]) for k in combo))
            owners.setdefault(key, []).append((ei, li))
    boundary = [usage[0] for usage in owners.values() if len(usage) == 1]
    if not boundary:
        raise ConversionError("体积网格没有边界面（网格退化？）")

    lo_all = np.min([np.asarray(f.bbox_min, dtype=float) for f in face_infos], axis=0)
    hi_all = np.max([np.asarray(f.bbox_max, dtype=float) for f in face_infos], axis=0)
    pad = max(float(np.max(hi_all - lo_all)) * pad_rel, 1e-9)

    result: dict[int, list[tuple[int, int]]] = {int(f.tag): [] for f in face_infos}
    unassigned = 0
    for ei, li in boundary:
        c = pts[[int(tets[ei, k]) for k in TET_LOCAL_FACES[li]]].mean(axis=0)
        candidates = [
            f
            for f in face_infos
            if bool(np.all(np.asarray(f.bbox_min, dtype=float) - pad <= c))
            and bool(np.all(c <= np.asarray(f.bbox_max, dtype=float) + pad))
        ]
        if len(candidates) > 1:
            if distance_fn is None:
                raise ConversionError(
                    f"边界面质心 {c.tolist()} 命中 {len(candidates)} 个 occ 面 "
                    f"{[int(f.tag) for f in candidates]}，且未提供 distance_fn 裁决"
                )
            candidates = [min(candidates, key=lambda f: distance_fn(int(f.tag), c))]
        if len(candidates) == 1:
            result[int(candidates[0].tag)].append((ei, li))
        else:
            unassigned += 1
    if unassigned:
        raise ConversionError(f"{unassigned} 个边界面无法归类到任何 occ 面（网格/几何不匹配）")
    return result


def pressure_to_face_set(load, face_map: dict, face_infos, part_label: str = "") -> PressureDLoadRef:  # noqa: ANN001
    """PressureLoad → 单元面集引用（load.validate() 须已通过；face_tag 须已挂接）。

    part_label：载荷挂接部件（load.part_id 或调用方解析出的部件标识）；非空时
    集合名带部件前缀 PRESS_{part}_FACE_{tag}，装配体不串件（模块 14）。
    """
    load.validate()
    if int(load.face_tag) <= 0:
        raise ConversionError(f"压力载荷「{load.name}」未挂接 occ 面（face_tag={load.face_tag}）")
    entries = face_map.get(int(load.face_tag)) or []
    if not entries:
        raise ConversionError(
            f"压力载荷「{load.name}」的 occ 面 {load.face_tag} 没有任何单元面（网格未覆盖）"
        )
    area = 0.0
    for f in face_infos:
        if int(f.tag) == int(load.face_tag):
            area = float(f.area_mm2)
            break
    else:
        raise ConversionError(f"occ 面 {load.face_tag} 不在面清单中")
    part = safe_part_label(part_label or getattr(load, "part_id", ""))
    set_name = f"PRESS_{part}_FACE_{int(load.face_tag)}" if part else f"PRESS_FACE_{int(load.face_tag)}"
    return PressureDLoadRef(
        face_tag=int(load.face_tag),
        set_name=set_name,
        entries=tuple(entries),
        total_area_mm2=area,
        pressure_mpa=float(load.pressure_mpa),
        part_id=str(getattr(load, "part_id", "") or ""),
    )


def pressure_resultant(pressure_mpa: float, area_mm2: float, outward_normal) -> np.ndarray:  # noqa: ANN001
    """压力等效合力：大小 p·A，方向 = −外法向（压向面内，CalculiX P 正值约定）。"""
    if area_mm2 <= 0.0:
        raise ConversionError(f"面积必须为正，实际 {area_mm2!r}")
    return -_unit(outward_normal) * float(pressure_mpa) * float(area_mm2)


# ---------------------------------------------------------------- 面力：CLOAD
@dataclass(frozen=True)
class CloadPlan:
    """*CLOAD 节点力计划：总力均分到面节点（semantics=total_force_equal_split）。"""

    kind: str = "nodal_cload"
    face_tag: int = 0
    node_ids: tuple[int, ...] = ()
    forces: np.ndarray = field(default_factory=lambda: np.zeros((0, 3)))  # (n,3)
    direction_unit: tuple[float, float, float] = (0.0, 0.0, 0.0)
    total_force_n: float = 0.0
    semantics: str = "total_force_equal_split"
    part_id: str = ""  # 挂接部件（空=单零件；模块 14）

    def summary(self) -> str:
        part = f"［部件 {self.part_id}］" if self.part_id else ""
        return (
            f"*CLOAD 面力: {len(self.node_ids)} 节点 × 均分, 总力 {self.total_force_n:g}N "
            f"方向{tuple(round(c, 4) for c in self.direction_unit)}{part} (occ面{self.face_tag})"
        )


def traction_face_nodes(
    tets: np.ndarray, face_map: dict, points: np.ndarray, face_tag: int
) -> tuple[np.ndarray, np.ndarray]:
    """occ 面上的体积网格节点（唯一升序）与坐标（来自该面的边界面三角）。"""
    entries = face_map.get(int(face_tag)) or []
    if not entries:
        raise ConversionError(f"occ 面 {face_tag} 没有任何单元面（网格未覆盖）")
    tets = np.asarray(tets, dtype=np.int64)
    ids = set()
    for ei, li in entries:
        for k in TET_LOCAL_FACES[li]:
            ids.add(int(tets[ei, k]))
    idx = np.array(sorted(ids), dtype=np.int64)
    return idx, np.asarray(points, dtype=float)[idx]


def traction_to_cload(load, node_ids, points) -> CloadPlan:  # noqa: ANN001
    """面力 → *CLOAD：总力均分到面节点（每节点力 = 单位方向 × 总力/节点数）。"""
    load.validate()
    if getattr(load,'distribution','equal_nodes')!='equal_nodes':
        raise ConversionError('面积一致面力需要单元面几何，请使用consistent_traction')
    if int(load.face_tag) <= 0:
        raise ConversionError(f"面力「{load.name}」未挂接 occ 面（face_tag={load.face_tag}）")
    ids = tuple(int(i) for i in node_ids)
    if not ids:
        raise ConversionError(f"面力「{load.name}」的挂接面上没有节点")
    d = _unit(load.direction)
    f_each = d * (float(load.total_force_n) / len(ids))
    return CloadPlan(
        face_tag=int(load.face_tag),
        node_ids=ids,
        forces=np.tile(f_each, (len(ids), 1)),
        direction_unit=(float(d[0]), float(d[1]), float(d[2])),
        total_force_n=float(load.total_force_n),
        part_id=str(getattr(load, "part_id", "") or ""),
    )


def traction_equivalent_pressure(total_force_n: float, face_area_mm2: float) -> float:
    """面力的等效面压核对视图：p = 总力 / 面面积（MPa）——仅核对，不用于写 *DLOAD。"""
    if face_area_mm2 <= 0.0:
        raise ConversionError(f"面积必须为正，实际 {face_area_mm2!r}")
    return float(total_force_n) / float(face_area_mm2)


# ---------------------------------------------------------------- 力矩：节点力偶
@dataclass(frozen=True)
class MomentCouplePlan:
    """力矩的等效节点力偶：f_i ∝ 轴×r_i，对轴矩精确守恒（semantics=axis_exact）。"""

    kind: str = "nodal_couple"
    face_tag: int = 0
    axis_unit: tuple[float, float, float] = (0.0, 0.0, 0.0)
    node_ids: tuple[int, ...] = ()
    forces: np.ndarray = field(default_factory=lambda: np.zeros((0, 3)))
    total_moment: np.ndarray = field(default_factory=lambda: np.zeros(3))
    net_force: np.ndarray = field(default_factory=lambda: np.zeros(3))
    semantics: str = "linear_distribution_axis_exact"
    part_id: str = ""  # 挂接部件（空=单零件；模块 14）

    def summary(self) -> str:
        part = f"［部件 {self.part_id}］" if self.part_id else ""
        return (
            f"等效节点力偶: {len(self.node_ids)} 节点, ΣM·轴 = "
            f"{float(np.dot(self.total_moment, self.axis_unit)):.6g} N·mm, "
            f"合力残差 {float(np.linalg.norm(self.net_force)):.3g} N{part} (occ面{self.face_tag})"
        )


def moment_to_nodal_couples(load, node_ids, node_points) -> MomentCouplePlan:  # noqa: ANN001
    """力矩 → 加载面等效节点力偶。

    f_i = (M/S)·(m̂ × r_i)，r_i = 节点 − 节点质心，S = Σ|m̂×r_i|²；
    对轴矩精确守恒：Σ(r_i×f_i)·m̂ = M（浮点 ~1e-12 相对）。
    合力残差 = (M/S)(m̂×Σr_i)，随结果报告（网格对称时≈0）。
    """
    load.validate()
    if int(load.face_tag) <= 0:
        raise ConversionError(f"力矩「{load.name}」未挂接 occ 面（face_tag={load.face_tag}）")
    pts = np.asarray(node_points, dtype=float)
    if pts.ndim != 2 or pts.shape[0] < 2 or pts.shape[1] != 3:
        raise ConversionError(f"力偶需要 ≥2 个面节点的 (n,3) 坐标，实际 {pts.shape}")
    m = _unit(load.axis)
    r = pts - pts.mean(axis=0)
    cross = np.cross(m, r)
    S = float(np.sum(cross * cross))
    if S <= 1e-12 * max(1.0, float(np.sum(r * r))):
        raise ConversionError("面节点全部位于力矩轴上，无法生成等效节点力偶")
    k = float(load.moment_nmm) / S
    forces = k * cross
    total_moment = np.cross(r, forces).sum(axis=0)
    ids = tuple(int(i) for i in node_ids)
    return MomentCouplePlan(
        face_tag=int(load.face_tag),
        axis_unit=(float(m[0]), float(m[1]), float(m[2])),
        node_ids=ids,
        forces=forces,
        total_moment=total_moment,
        net_force=forces.sum(axis=0),
        part_id=str(getattr(load, "part_id", "") or ""),
    )


# ---------------------------------------------------------------- 重力：*DLOAD GRAV 标签
def gravity_vector(load) -> np.ndarray:  # noqa: ANN001
    """GravityLoad → GRAV 体积力向量 = 单位方向 × g（mm/s²）。

    注意（ccx 2.22 实证）：ccx **没有 *GRAV 独立关键字**——重力经 *DLOAD
    行的 GRAV 标签施加（幅值 + 单位方向 3 分量）；.inp 文本写出见
    solver/deck.py（`Eall, GRAV, g, nx, ny, nz`）。
    """
    load.validate()
    return _unit(load.direction) * float(load.g_mm_s2)
