# -*- coding: utf-8 -*-
"""结果三场数据模型（GUI 无关层，solver 层）：U / S_Mises / RF。

主解析 .dat（*NODE PRINT 的 RF/U 分块），辅以 ccx2paraview → .vtu + meshio
（numpy 2.5.3 复数特征值 monkeypatch，probe3 配方）。

- U：全节点位移，取自 .vtu 的 DISP（与 .frd DISP 同源；③/⑥ 门与 .dat/.frd
  独立解析互核）；
- S_Mises：全节点 von Mises，取自 .vtu 的 S_Mises（ccx2paraview 由 STRESS
  分量计算；mises_from_components 供独立复算校核）；
- RF：约束节点原始外力减去其已施加节点载荷得到支反力；自由节点填0。
  raw_rf_table保留DAT原值，source.reaction记录修正口径；不支持的载荷明确标为外力。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .ccx2para import frd_to_vtu, read_vtu_point_data
from .results import SolveResults


@dataclass
class ResultFields:
    """结果三场（行序 = node_ids 升序 = 网格 node_tags 升序）。"""

    node_ids: np.ndarray  # (n,) gmsh 节点 tag
    u: np.ndarray  # (n,3) 位移 mm
    s_mises: np.ndarray  # (n,) von Mises MPa
    rf: np.ndarray  # (n,3) 反力 N（自由节点为 0，真值见 rf_table）
    rf_table: dict[int, list[float]]  # 修正后的逐约束节点支反力；口径见source.reaction
    rf_sum: np.ndarray  # (3,) 逐节点求和的支反力合力
    source: dict  # 来源文件与极值信息（供报告/日志）
    raw_rf_table: dict[int, list[float]] = field(default_factory=dict)

    def max_u_mag(self) -> float:
        return float(np.max(np.linalg.norm(self.u, axis=1)))

    def extreme_lines(self) -> list[str]:
        u_mag = np.linalg.norm(self.u, axis=1)
        i_umax = int(np.argmax(u_mag))
        i_smax = int(np.argmax(self.s_mises))
        rf_mag = np.linalg.norm(self.rf, axis=1)
        i_rfmax = int(np.argmax(rf_mag))
        label = '支反力' if self.source.get('reaction',{}).get('supported') else '节点外力'
        return [
            f"位移极值: |U|max = {u_mag[i_umax]:.6g} mm @ 节点 {self.node_ids[i_umax]}",
            f"von Mises 极值: max = {self.s_mises[i_smax]:.6g} MPa @ 节点 {self.node_ids[i_smax]}",
            f"{label}极值: max = {rf_mag[i_rfmax]:.6g} N @ 节点 {self.node_ids[i_rfmax]}"
            f"（约束节点 {len(self.rf_table)} 个）",
            f"{label}合力: ΣF = ({self.rf_sum[0]:.6g}, {self.rf_sum[1]:.6g}, {self.rf_sum[2]:.6g}) N",
        ]


def build_result_fields(
    node_ids,  # noqa: ANN001 - gmsh 节点 tag（升序，与 .frd/.vtu 行序一致）
    results: SolveResults,  # noqa: ANN001 - .dat 解析（RF 主来源）
    frd_path: str | Path,  # noqa: ANN001 - .frd（vtu 转换源）
    fixed_set: str = "FIXED",  # 反力 NSET 名（装配 deck 为 PART1_FIXED 等，模块 20）
    vtu_path: str | Path | None = None,
    *,
    inp_text: str | None = None,
    mesh: dict | None = None,
) -> ResultFields:
    """构建 U / S_Mises / RF 三场（节点数必须与网格一致）。

    fixed_set：反力 NSET 名（results.rf_block 的键）——单零件默认 FIXED；
    装配 deck 的固定 NSET 按部件前缀命名（如 PART1_FIXED），调用方传入。
    """
    node_ids = np.asarray(node_ids, dtype=np.int64)
    pos_of = {int(t): i for i, t in enumerate(node_ids)}
    n = len(node_ids)

    vtu = Path(vtu_path) if vtu_path is not None else frd_to_vtu(frd_path)
    data = read_vtu_point_data(vtu)
    if "U" not in data or len(data["U"]) != n:
        raise RuntimeError(
            f".vtu 位移场节点数 {len(data.get('U', []))} 与网格 {n} 不一致: {vtu}"
        )
    if "S_Mises" not in data or len(data["S_Mises"]) != n:
        raise RuntimeError(
            f".vtu S_Mises 场节点数 {len(data.get('S_Mises', []))} 与网格 {n} 不一致: {vtu}"
        )
    u = np.asarray(data["U"], dtype=float)
    s_mises = np.asarray(data["S_Mises"], dtype=float).reshape(-1)

    sets = [fixed_set] if isinstance(fixed_set, str) else list(fixed_set)
    rf_table = {}
    for name in sets:
        block = results.rf_block(name)
        if not block:
            raise RuntimeError(f"约束反力集合 {name} 缺失，不能将其当作零反力")
        for nid, vec in block.items():
            if nid in rf_table and not np.allclose(rf_table[nid], vec, rtol=1e-9, atol=1e-9):
                raise RuntimeError(f"重叠约束集合在节点 {nid} 给出不同反力")
            rf_table[nid] = vec
    raw_rf_table = {int(nid):[float(v) for v in vec] for nid,vec in rf_table.items()}
    from .nodal_loads import evaluate_nodal_loads, UnsupportedLoad, VERSION
    import hashlib
    inp = Path(frd_path).with_suffix('.inp')
    reaction = {'version':VERSION,'supported':False,'quantity':'external_nodal_force'}
    try:
        if inp_text is None:
            if not inp.is_file(): raise UnsupportedLoad('缺少本工况INP，未完成反力修正')
            inp_text = inp.read_text(encoding='utf-8')
        if mesh is None:
            from .deck import parse_mesh_inp
            mesh = parse_mesh_inp(inp_text)
        reaction = evaluate_nodal_loads(inp_text,mesh,rf_table)
        applied = reaction.pop('nodes')
        rf_table = {nid:(np.asarray(vec)-applied[nid]).tolist() for nid,vec in raw_rf_table.items()}
    except UnsupportedLoad as exc:
        reaction['reason'] = str(exc)
    if inp_text is not None:
        reaction['inp_normalized_sha256'] = hashlib.sha256(inp_text.encode('utf-8')).hexdigest()
        if inp.is_file():
            with inp.open('rb') as stream:
                reaction['inp_sha256'] = hashlib.file_digest(stream,'sha256').hexdigest()
    reaction['raw_rf_sum_n'] = np.sum(list(raw_rf_table.values()),axis=0).tolist()
    rf = np.zeros((n, 3), dtype=float)
    for nid, vec in rf_table.items():
        rf[pos_of[int(nid)]] = np.asarray(vec, dtype=float)
    rf_sum = (
        np.sum([rf_table[k] for k in sorted(rf_table)], axis=0)
        if rf_table
        else np.zeros(3)
    )

    source = {
        "vtu": str(vtu),
        "dat": str(results.dat_path),
        "frd": str(frd_path),
        "max_u_mag": float(np.max(np.linalg.norm(u, axis=1))),
        "max_mises": float(np.max(s_mises)),
        "reaction":reaction,
    }
    return ResultFields(
        node_ids=node_ids,
        u=u,
        s_mises=s_mises,
        rf=rf,
        rf_table=dict(rf_table),
        rf_sum=np.asarray(rf_sum, dtype=float),
        source=source,
        raw_rf_table=raw_rf_table,
    )


# ---------------------------------------------------------------- 装配级/单部件级结果（模块 20）
@dataclass
class PartFieldView:
    """单部件场视图（模块 20）：部件名 + 该部件节点的 U/S_Mises/RF 子场。

    node_ids 为全局节点 id 的升序子集（与装配合成场行序同源对齐）；
    共节点（粘接 merge 模式界面）会同时出现在多个部件视图——共享语义。
    """

    part: str  # 部件标识（ELSET 名，如 PART1）
    node_ids: np.ndarray  # (n,) 升序全局子集
    u: np.ndarray  # (n,3) 位移 mm
    s_mises: np.ndarray  # (n,) von Mises MPa
    rf: np.ndarray  # (n,3) 反力 N（自由节点为 0）

    def max_mises(self) -> float:
        return float(np.max(self.s_mises)) if len(self.s_mises) else 0.0

    def max_u_mag(self) -> float:
        return float(np.max(np.linalg.norm(self.u, axis=1))) if len(self.u) else 0.0

    def extreme_lines(self) -> list[str]:
        u_mag = np.linalg.norm(self.u, axis=1)
        i_u = int(np.argmax(u_mag)) if len(u_mag) else 0
        i_s = int(np.argmax(self.s_mises)) if len(self.s_mises) else 0
        return [
            f"[{self.part}] 节点 {len(self.node_ids)} 个",
            f"[{self.part}] |U|max = {u_mag[i_u]:.6g} mm @ 节点 {self.node_ids[i_u]}" if len(u_mag) else "",
            f"[{self.part}] von Mises max = {self.s_mises[i_s]:.6g} MPa @ 节点 {self.node_ids[i_s]}"
            if len(self.s_mises)
            else "",
        ]


def part_field_views(
    fields: ResultFields,  # noqa: ANN001 - 装配合成场
    part_elsets: dict[str, set[int]],  # {部件 ELSET 名: 单元 tag 集}
    elems_conn: dict[int, list[int]],  # 单元号 → 连接表（含 10 节点）
) -> dict[str, PartFieldView]:
    """按部件 ELSET 提取单部件场（模块 20）。

    部件节点 = 该 ELSET 全部单元连接表节点的并集；行序 = 全局 node_ids 的
    升序子集（同源对齐）。共节点（merge 模式界面）出现在多个部件视图。
    """
    pos_of = {int(t): i for i, t in enumerate(fields.node_ids)}
    views: dict[str, PartFieldView] = {}
    for name, elem_ids in part_elsets.items():
        node_set: set[int] = set()
        for eid in elem_ids:
            if eid in elems_conn:
                node_set.update(int(n) for n in elems_conn[eid])
        rows = np.asarray([pos_of[n] for n in sorted(node_set) if n in pos_of], dtype=np.int64)
        node_ids_v = fields.node_ids[rows]
        views[str(name)] = PartFieldView(
            part=str(name),
            node_ids=node_ids_v,
            u=fields.u[rows],
            s_mises=fields.s_mises[rows],
            rf=fields.rf[rows],
        )
    return views


def composite_from_parts(
    views: dict[str, PartFieldView],
    node_ids_global: np.ndarray,
) -> ResultFields:
    """按部件视图拼接回装配合成场（同源重排，模块 20 验收④的合成路径）。

    每个全局节点取其部件视图行值（同源同值，容差 1e-12）；不在任何部件
    视图中的节点（不应发生——ELSET 全覆盖时）保持 0 并由调用方发现。
    """
    node_ids_global = np.asarray(node_ids_global, dtype=np.int64)
    n = len(node_ids_global)
    pos_of = {int(t): i for i, t in enumerate(node_ids_global)}
    u = np.zeros((n, 3), dtype=float)
    s_mises = np.zeros(n, dtype=float)
    rf = np.zeros((n, 3), dtype=float)
    covered = np.zeros(n, dtype=bool)
    for view in views.values():
        rows = np.asarray([pos_of[int(t)] for t in view.node_ids if int(t) in pos_of], dtype=np.int64)
        u[rows] = view.u
        s_mises[rows] = view.s_mises
        nz = np.linalg.norm(view.rf, axis=1) > 0.0
        rf_nz = rows[nz]
        rf[rf_nz] = view.rf[nz]
        covered[rows] = True
    uncovered = int((~covered).sum())
    rf_table: dict[int, list[float]] = {
        int(t): [float(v) for v in rf[i]] for i, t in enumerate(node_ids_global)
        if np.linalg.norm(rf[i]) > 0.0
    }
    rf_sum = np.sum([rf_table[k] for k in sorted(rf_table)], axis=0) if rf_table else np.zeros(3)
    return ResultFields(
        node_ids=node_ids_global,
        u=u,
        s_mises=s_mises,
        rf=rf,
        rf_table=rf_table,
        rf_sum=np.asarray(rf_sum, dtype=float),
        source={"composite_from_parts": sorted(views.keys()), "uncovered_nodes": uncovered},
    )


def interface_force_table(
    forc: np.ndarray,  # (n,3) 逐节点外力（.frd FORC——含接触力，模块 18 实证）
    node_ids: np.ndarray,  # (n,) 与 forc 行对齐
    node_sets: dict[str, set[int]],  # {界面节点集名: 节点 tag 集}
) -> list[dict]:
    """界面力表（模块 20）：按界面节点集逐集合求合力。

    返回 [{set, nodes, force (3), magnitude}]——接触界面的从面节点集合力
    即界面传力（模块 18 实证 FORC 含接触力；*TIE 的 MPC 力不在 FORC——
    模块 17 实证，粘接界面传力用从面应力积分，见 module_17/19）。
    """
    pos_of = {int(t): i for i, t in enumerate(node_ids)}
    table: list[dict] = []
    for name, tags in node_sets.items():
        rows = [pos_of[int(t)] for t in sorted(tags) if int(t) in pos_of]
        fsum = np.sum(forc[rows], axis=0) if rows else np.zeros(3)
        table.append({
            "set": str(name),
            "nodes": len(rows),
            "force": (float(fsum[0]), float(fsum[1]), float(fsum[2])),
            "magnitude": float(np.linalg.norm(fsum)),
        })
    return table


def field_surface(mesh: dict, fields: ResultFields, element_ids=None) -> dict:
    """按所选体单元裁剪全部节点及结果；局部极值包含二阶中点节点。"""
    from ..mesh.mesh_data import boundary_faces_corners

    ids = sorted(mesh["elems"] if element_ids is None else element_ids)
    nodes = sorted({int(n) for eid in ids for n in mesh["elems"][eid]})
    if not nodes:
        raise ValueError("所选部件没有体单元")
    local = {n: i for i, n in enumerate(nodes)}
    global_rows = {int(n): i for i, n in enumerate(fields.node_ids)}
    rows = np.asarray([global_rows[n] for n in nodes], dtype=np.int64)
    cells = np.asarray([[local[n] for n in mesh["elems"][eid][:4]] for eid in ids])
    return {"node_ids": np.asarray(nodes),
            "points": np.asarray([mesh["nodes"][n] for n in nodes]),
            "tris": boundary_faces_corners(cells),
            "u": fields.u[rows], "s_mises": fields.s_mises[rows]}
