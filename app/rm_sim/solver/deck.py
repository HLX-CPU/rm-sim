# -*- coding: utf-8 -*-
"""完整分析 .inp 生成（GUI 无关层，solver 层）。

结构 = 网格段（模块 6 mesher 的过滤文本：*NODE + C3D10 + ELSET=Eall + NSET）
+ 分析段（*MATERIAL,*ELASTIC,*DENSITY,*SOLID SECTION,*BOUNDARY,*STEP,*STATIC,
*CLOAD/*DLOAD/*GRAV,*NODE PRINT RF,U,*NODE FILE,*EL FILE,*END STEP）。

载荷全部经 solver.convert 语义转换（模块 5，红线：压力=单元面集 *DLOAD P，
禁止节点 CLOAD 近似）。C3D10 面码映射：中性局部面 0..3 → P1..P4（角点组
{1,2,3}/{1,2,4}/{2,3,4}/{1,3,4}，CalculiX/Abaqus C3D4·C3D10 约定；
验收 ⑦ ΣRFx=200N 提供方向级实证裁决）。
"""
from __future__ import annotations

import re
from pathlib import Path

import numpy as np

from .convert import (
    TET_LOCAL_FACES,
    ConversionError,
    build_face_element_map,
    moment_to_nodal_couples,
    traction_to_cload,
)
from .results import SolveResults  # noqa: F401 - 便于从本包一次导入

NEUTRAL_FACE_TO_P = {0: "P1", 1: "P2", 2: "P3", 3: "P4"}


def _id_block(header: str, ids) -> str:  # noqa: ANN001
    """集合块行格式（与 mesher._id_block 同款）：每行 10 个 id，逗号分隔，尾随换行。"""
    lines: list[str] = [header]
    row: list[str] = []
    for i in ids:
        row.append(str(int(i)))
        if len(row) == 10:
            lines.append(", ".join(row))
            row = []
    if row:
        lines.append(", ".join(row))
    return "\n".join(lines) + "\n"


class DeckError(RuntimeError):
    """分析 .inp 生成失败（数据模型/网格/NSET 不满足前提）。"""


def parse_mesh_inp(text: str) -> dict:
    """解析网格段 .inp（模块 6 导出）：nodes/elems/nsets/elsets（elem 连接表含 10 节点）。

    elsets（模块 16 新增，向后兼容附加键）：*ELSET 块 → {名: [单元号]}——
    部件 ELSET PARTn 与界面单元面集都从这里读。
    """
    mode: str | None = None
    cur_set = ""
    nodes: dict[int, tuple[float, float, float]] = {}
    elems: dict[int, list[int]] = {}
    nsets: dict[str, list[int]] = {}
    elsets: dict[str, list[int]] = {}
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("*"):
            upper = s.upper()
            mode = None
            if upper.startswith("*NODE") and not upper.startswith(
                ("*NODE PRINT", "*NODE FILE", "*NODE OUTPUT")
            ):
                mode = "NODE"
            elif upper.startswith("*ELEMENT"):
                mode = "ELEM"
            elif upper.startswith("*NSET"):
                mode, cur_set = "SET", ""
                for part in s.split(",")[1:]:
                    key, _, val = part.partition("=")
                    if key.strip().upper() == "NSET":
                        cur_set = val.strip()
            elif upper.startswith("*ELSET"):
                mode, cur_set = "ELSET", ""
                for part in s.split(",")[1:]:
                    key, _, val = part.partition("=")
                    if key.strip().upper() == "ELSET":
                        cur_set = val.strip()
            else:
                mode = None
            continue
        if not s or s.startswith("**"):
            continue
        if mode == "NODE":
            parts = [p for p in s.split(",") if p.strip()]
            nodes[int(parts[0])] = tuple(float(x) for x in parts[1:4])
        elif mode == "ELEM":
            parts = [p for p in s.split(",") if p.strip()]
            elems[int(parts[0])] = [int(x) for x in parts[1:]]
        elif mode == "SET":
            nsets.setdefault(cur_set, []).extend(
                int(x) for x in s.split(",") if x.strip()
            )
        elif mode == "ELSET":
            elsets.setdefault(cur_set, []).extend(
                int(x) for x in s.split(",") if x.strip()
            )
    if not nodes or not elems:
        raise DeckError("网格段缺少 *NODE/*ELEMENT 数据")
    return {"nodes": nodes, "elems": elems, "nsets": nsets, "elsets": elsets}


def _mat_name(study) -> str:  # noqa: ANN001
    safe = re.sub(r"[^A-Za-z0-9_]", "_", study.material.name) or "MAT"
    return f"MAT_{safe}"


def _fixed_nset_name() -> str:
    return "FIXED"


def _load_nset_name() -> str:
    return "LOADFACE"


def _nset_for_tag(
    nset_face_tags: dict[str, int] | None,
    mesh_nsets: dict[str, list[int]],
    tag: int,
    purpose: str,
) -> str:
    """occ 面 tag → 承载它的 NSET 名（模块 14：装配体按部件命名，禁止串件）。

    nset_face_tags 是网格生成时传入 mesher 的 {NSET 名: occ 面 tag} 映射；
    legacy 名 FIXED/LOADFACE 继续可用，装配流可用 FIXED_P1/LOAD_P2 等部件前缀。
    无命中 / 映射冲突（同名 tag 多个 NSET）/ 网格段缺该 NSET → DeckError
    （显式报错，绝不静默选错节点集）。
    """
    if not nset_face_tags:
        raise DeckError(f"未提供 NSET↔occ面 映射（nset_face_tags），无法定位{purpose}的节点集")
    hits = sorted(name for name, t in nset_face_tags.items() if int(t) == int(tag))
    if not hits:
        raise DeckError(
            f"{purpose}挂接面 occ {tag} 没有对应 NSET（nset_face_tags={nset_face_tags}）——"
            "请按挂接面重新生成网格"
        )
    if len(hits) > 1:
        raise DeckError(f"{purpose}挂接面 occ {tag} 命中多个 NSET {hits}（映射冲突，禁止串件）")
    name = hits[0]
    if name not in mesh_nsets or not mesh_nsets[name]:
        raise DeckError(f"网格段缺少 NSET {name}（生成网格时须通过 nset_faces 挂接端面）")
    return name


def _boundary_cards(load_case, mesh, nset_face_tags) -> tuple[list[str], list[str]]:  # noqa: ANN001
    """固定/内部连接 → (*BOUNDARY 节内行, fixed_nsets)（模块 14/16 共用）。"""
    nsets = mesh["nsets"]
    fixed_constraints = [c for c in load_case.constraints if c.kind == "fixed"]
    bonded_constraints = [c for c in load_case.constraints if c.kind == "bonded"]
    if not fixed_constraints:
        raise DeckError(
            "工况没有外部固定约束，静力不可解（内部连接 bonded 不构成接地，is_solvable=False）"
        )
    lines: list[str] = []
    fixed_nsets: list[str] = []
    for c in fixed_constraints:
        c.validate()
        nset = _nset_for_tag(nset_face_tags, nsets, int(c.face_tag), f"固定约束「{c.name}」")
        if nset not in fixed_nsets:
            fixed_nsets.append(nset)
        lines.append(f"{nset}, 1, 3, 0.0")
    for c in bonded_constraints:
        c.validate()
        # 内部连接（bonded）不构成接地、不产生 *BOUNDARY（模块 14）；
        # 结构化连接对象（core.connections）的连接卡见 build_assembly_case_inp。
        part = f"部件 {c.part_id} " if c.part_id else ""
        lines.append(f"** 内部连接「{c.name}」{part}@ occ面{c.face_tag}：不接地，绑定/接触实现属后续模块")
    return lines, fixed_nsets


def _load_cards(load_case, mesh, geom, nset_face_tags, project=None) -> tuple[list[str], list[str], list[str]]:  # noqa: ANN001
    """工况载荷 → (*CLOAD 行, *DLOAD 行, 载荷 NSET 列表)（模块 14/16 共用）。

    压力=单元面集 *DLOAD P；面力按显式分配设置生成*CLOAD（旧节点等分/面积一致积分）；
    力矩=节点力偶、重力=*DLOAD GRAV 标签（ccx 2.22 实证）。
    """
    nsets = mesh["nsets"]
    points = {nid: np.asarray(mesh["nodes"][nid], dtype=float) for nid in mesh["nodes"]}
    cload_lines: list[str] = []
    dload_lines: list[str] = []
    load_nsets: list[str] = []
    for ld in load_case.loads:
        ld.validate()
        kind = type(ld).__name__
        if kind == "TractionLoad":
            if int(ld.face_tag) <= 0:
                raise DeckError(f"面力「{ld.name}」未挂接 occ 面")
            nset = _nset_for_tag(nset_face_tags, nsets, int(ld.face_tag), f"面力「{ld.name}」")
            if nset not in load_nsets:
                load_nsets.append(nset)
            node_ids_load = tuple(sorted(nsets[nset]))
            if ld.distribution=='consistent_area':
                from .traction import consistent_traction
                own=None
                if project is not None and ld.part_id:
                    k=next(i for i,p in enumerate(project.parts,1) if p.part_id==ld.part_id)
                    own=mesh['elsets'][f'PART{k}']
                plan,meta=consistent_traction(ld,mesh,node_ids_load,own)
                cload_lines.append(f"** 一致面力 {ld.name}: area_mm2={meta['area_mm2']:.12g}, centroid_mm={meta['centroid_mm']}, faces={meta['faces']}")
                for nid,force in zip(plan.node_ids,plan.forces):
                    for dof,value in enumerate(force,1):
                        if abs(value)>0:cload_lines.append(f'{nid}, {dof}, {value:.12g}')
                continue
            plan = traction_to_cload(ld, node_ids_load, [points[i] for i in node_ids_load])
            n = len(plan.node_ids)
            for dof in (1, 2, 3):
                val = float(plan.direction_unit[dof - 1]) * plan.total_force_n / n
                if abs(val) > 0.0:
                    cload_lines.append(f"{nset}, {dof}, {val:.12g}")
        elif kind == "MomentLoad":
            nset = _nset_for_tag(nset_face_tags, nsets, int(ld.face_tag), f"力矩「{ld.name}」")
            if nset not in load_nsets:
                load_nsets.append(nset)
            node_ids_load = tuple(sorted(nsets[nset]))
            plan = moment_to_nodal_couples(ld, node_ids_load, [points[i] for i in node_ids_load])
            for nid, f in zip(plan.node_ids, plan.forces):
                for dof in (1, 2, 3):
                    if abs(f[dof - 1]) > 0.0:
                        cload_lines.append(f"{nid}, {dof}, {f[dof - 1]:.12g}")
        elif kind == "PressureLoad":
            if geom is None:
                raise DeckError(f"压力载荷「{ld.name}」需要 occ 面几何（geom 未提供）")
            # 压力同样登记其挂接面 NSET（*NODE PRINT U 输出与 legacy 一致）
            pressure_nset = _nset_for_tag(nset_face_tags, nsets, int(ld.face_tag), f"压力「{ld.name}」")
            if pressure_nset not in load_nsets:
                load_nsets.append(pressure_nset)
            eid_sorted = sorted(mesh["elems"].keys())
            face_infos = geom.faces
            if project is not None and ld.part_id:
                from ..core.connections import part_faces_for
                k = next(i for i, p in enumerate(project.parts, 1) if p.part_id == ld.part_id)
                own = set(mesh["elsets"][f"PART{k}"])
                eid_sorted = [e for e in eid_sorted if e in own]
                face_infos = part_faces_for(project, geom, ld.part_id)
            # build_face_element_map 契约：tets 是 points 的**位置索引**（不是节点 tag）——
            # 节点 tag 与位置差 1 会静默错位（模块 7 实测踩坑，与 NSET 下标坑同类）
            nid_sorted = sorted(mesh["nodes"].keys())
            pos_of = {nid: i for i, nid in enumerate(nid_sorted)}
            points_arr = np.asarray([mesh["nodes"][nid] for nid in nid_sorted], dtype=float)
            conn_pos = np.asarray(
                [[pos_of[n] for n in mesh["elems"][eid][:4]] for eid in eid_sorted],
                dtype=np.int64,
            )
            face_map = build_face_element_map(
                points_arr,
                conn_pos,
                face_infos,
                distance_fn=geom.point_to_face_distance,
            )
            from .convert import pressure_to_face_set

            ref = pressure_to_face_set(ld, face_map, face_infos, part_label=str(ld.part_id or ""))
            dload_lines.append(f"** 压力「{ld.name}」：单元面集 {ref.set_name}（{len(ref.entries)} 面，非节点 CLOAD 近似）")
            for ei, li in ref.entries:
                dload_lines.append(f"{eid_sorted[ei]}, {NEUTRAL_FACE_TO_P[li]}, {ref.pressure_mpa:.12g}")
        elif kind == "GravityLoad":
            if getattr(ld, "part_id", ""):
                raise DeckError(
                    f"重力「{ld.name}」指定了部件 {ld.part_id}：per-part 重力需要部件 ELSET，"
                    "属后续模块（当前 GRAV 体积力仅支持全体 Eall——不做静默范围改变）"
                )
            # 实证（模块 7）：ccx 2.22 不支持 *GRAV 关键字（calinput WARNING
            # "cannot be interpreted"、载荷未生效、反力恒 0）；ccx 的重力正确
            # 语法是 *DLOAD 的 GRAV 标签（幅值 + 单位方向 3 分量）。
            d = np.asarray(ld.direction, dtype=float)
            d = d / float(np.linalg.norm(d))
            dload_lines.append(f"** 重力「{ld.name}」：GRAV 体积力（ccx 2.22 无 *GRAV 关键字）")
            dload_lines.append(
                f"Eall, GRAV, {ld.g_mm_s2:.12g}, {d[0]:.12g}, {d[1]:.12g}, {d[2]:.12g}"
            )
        else:
            raise DeckError(f"未知载荷类型: {kind}")
    return cload_lines, dload_lines, load_nsets


def build_case_inp(
    mesh_inp_text: str,
    study,  # noqa: ANN001 - core.Study
    load_case,  # noqa: ANN001 - core.LoadCase
    geom=None,  # noqa: ANN001 - cad.SolidModel（压力载荷需要 occ 面信息）
    *,
    nset_face_tags: dict[str, int] | None = None,
    mesh: dict | None = None,
) -> str:
    """由数据模型 + 网格段生成完整分析 .inp 文本（单零件入口，文本与模块 14 一致）。

    geom：压力载荷需要 occ 面几何（face_map 归类）；无 geom 时压力载荷报错。
    nset_face_tags：网格生成时的 {NSET 名: occ 面 tag}（legacy {"FIXED": 1,
    "LOADFACE": 2}；装配流可用部件前缀 {"FIXED_P1": t1, "LOAD_P2": t2}）——
    逐约束/载荷按 face_tag 解析其 NSET（模块 14：装配体不串件），无命中/
    冲突显式报错，防止载荷被静默挂错面。装配版入口见 build_assembly_case_inp。
    """
    from ..core.materials import solver_material_dict

    load_case.validate()
    if mesh is None:
        mesh = parse_mesh_inp(mesh_inp_text)
    mat = solver_material_dict(study.material)
    mname = _mat_name(study)

    lines: list[str] = [mesh_inp_text.rstrip("\n"), ""]
    lines += [
        "** ===== 材料（N-mm-MPa-t-s，core.materials 导出） =====",
        f"*MATERIAL, NAME={mname}",
        "*ELASTIC",
        f"{mat['E_MPa']:.12g}, {mat['nu']:.12g}",
        "*DENSITY",
        f"{mat['rho_t_mm3']:.12g}",
        f"*SOLID SECTION, ELSET=Eall, MATERIAL={mname}",
        "**",
        "** ===== 边界条件（理想固定假设：面节点三向平动为零，无转动/无施加柔性） =====",
        "*BOUNDARY",
    ]
    boundary_lines, fixed_nsets = _boundary_cards(load_case, mesh, nset_face_tags)
    lines += boundary_lines
    lines += ["**", "*STEP", "*STATIC"]
    cload_lines, dload_lines, load_nsets = _load_cards(load_case, mesh, geom, nset_face_tags)
    if cload_lines:
        lines += ["*CLOAD"] + cload_lines
    if dload_lines:
        lines += ["*DLOAD"] + dload_lines
    lines += ["**"]
    for nset in fixed_nsets:
        lines += ["*NODE PRINT, NSET=" + nset, "RF"]
    for nset in load_nsets:
        lines += ["*NODE PRINT, NSET=" + nset, "U"]
    lines += [
        "*NODE FILE",
        "U, RF",
        "*EL FILE",
        "S",
        "*END STEP",
        "",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------- 装配 INP（模块 16）
# *SURFACE 面标识（ccx 2.22 实跑实证，probes 记录于 module_16 文档）：ELEMENT 型
# surface 数据行面码为 S1..S4（P 码被拒绝）；*DLOAD 的单元面码仍为 P1..P4
# （模块 7 实证）——两者是同一 face 编号体系的不同标签前缀。
NEUTRAL_FACE_TO_S = {0: "S1", 1: "S2", 2: "S3", 3: "S4"}

# 接触 LINEAR 罚刚度 [MPa/mm]（模块 18 首跑标定）：1000 时罚柔量吞掉间隙闭合
# 反力的绝大部分（⑤ 预定位移算例实测远偏 42000N）；1e6 时 ⑤ 理论偏差 ~0.3%，
# 且压缩/摩擦/重力/间隙/μ 判别全部算例真实 ccx 收敛（探针+验收实证）。
_CONTACT_LINEAR_STIFFNESS_MPA_MM = 1.0e6


def _resolve_conn_face(project, geom, side, conn_label: str, side_label: str) -> int:  # noqa: ANN001
    """(part_id, 面指纹) → occ 面 tag；不唯一命中 → DeckError（不静默贴面）。"""
    from ..core.connections import part_faces_for
    from ..core.face_identity import resolve_face

    faces = part_faces_for(project, geom, side.part_id)
    lo, hi = geom.overall_bbox
    diag = float(np.linalg.norm(np.asarray(hi) - np.asarray(lo)))
    tag, status = resolve_face(faces, side.face, diag)
    if status != "ok":
        raise DeckError(
            f"连接「{conn_label}」{side_label} 面指纹在部件 {side.part_id} 面清单中"
            f"{status}——拒绝生成连接卡（不静默贴面）"
        )
    return int(tag)


def _part_face_maps(project, geom, mesh) -> dict[int, tuple[list[int], dict]]:  # noqa: ANN001
    """按部件的 (部件单元在 eid_sorted 中的行下标, 部件 face_map)。

    贴合部件的干涉面（bbox 重合）在全模型 face_map 中歧义（模块 14 已知限制）
    ——把提取域限定为「部件自己的单元子集 + 部件自己的面清单」即天然消歧：
    每个部件的界面三角只可能命中自己部件的面。
    """
    eid_sorted = sorted(mesh["elems"].keys())
    nid_sorted = sorted(mesh["nodes"].keys())
    pos_of = {nid: i for i, nid in enumerate(nid_sorted)}
    points_arr = np.asarray([mesh["nodes"][nid] for nid in nid_sorted], dtype=float)
    result: dict[int, tuple[list[int], dict]] = {}
    for k, part in enumerate(project.parts, start=1):
        elset_name = f"PART{k}"
        elset_ids = set(mesh["elsets"].get(elset_name, []))
        if not elset_ids:
            raise DeckError(
                f"网格段缺少部件 ELSET {elset_name}（装配网格须以 part_elsets={{'PART{k}': 实体tag}} 生成）"
            )
        rows = [i for i, eid in enumerate(eid_sorted) if eid in elset_ids]
        solid_tag = int(geom.solids[part.solid_index].tag)
        part_faces = [f for f in geom.faces if f.solid_tag == solid_tag]
        conn_pos = np.asarray(
            [[pos_of[n] for n in mesh["elems"][eid_sorted[i]][:4]] for i in rows],
            dtype=np.int64,
        )
        face_map = build_face_element_map(
            points_arr, conn_pos, part_faces,
            distance_fn=geom.point_to_face_distance,
        )
        result[k] = (rows, face_map)
    return result


def _surface_cards(side_label: str, conn_i: int, face_tag: int, part_k: int,
                   part_maps, eid_sorted: list[int], elsets_out: list[str],
                   facets=None) -> list[str]:  # noqa: ANN001
    """连接侧面 → *ELSET（按 li 分组）+ *SURFACE 数据行（ccx 要 ELSET 名 + S 面码）。

    ELSET 写 **单元号**（eid_sorted[行号]），不是行下标——ccx 按单元号取面
    （行号/单元号错位会把绑到错误单元，ΣRF 失衡实证）。
    facets=None 用整张 CAD 面的面片；绑定类连接传入按带宽裁过的面片集。
    """
    rows, face_map = part_maps[part_k]
    if facets is None:
        facets = face_map[face_tag]
    by_li: dict[int, list[int]] = {}
    for ei, li in facets:
        by_li.setdefault(li, []).append(eid_sorted[rows[ei]])
    if not by_li:
        raise DeckError(f"连接 {conn_i} 侧 {side_label}（occ面{face_tag}）没有任何单元面（网格未覆盖）")
    lines: list[str] = []
    for li in sorted(by_li):
        set_name = f"C{conn_i}_{side_label}_{li}"
        elsets_out.append(_id_block(f"*ELSET,ELSET={set_name}", by_li[li]).rstrip("\n"))
        lines.append(f"{set_name}, {NEUTRAL_FACE_TO_S[li]}")
    return lines


def _face_nodes_of(part_maps, part_k: int, face_tag: int, mesh) -> tuple[list[int], list]:  # noqa: ANN001
    """连接侧面的体积网格节点（gmsh 节点 tag 升序）与坐标（模块 6 红线：写 tag）。"""
    rows, face_map = part_maps[part_k]
    eid_sorted = sorted(mesh["elems"].keys())
    conn = mesh["elems"]
    ids: set[int] = set()
    for ei, li in face_map[face_tag]:
        eid = eid_sorted[rows[ei]]
        ids.update(int(n) for n in conn[eid][:4])
    ids_sorted = sorted(ids)
    coords = [mesh["nodes"][i] for i in ids_sorted]
    return ids_sorted, coords


def _connection_cards(project, geom, mesh, *, bond_band_mm: float | None = None) -> tuple[list[str], list[str], bool]:  # noqa: ANN001
    """连接对象 → (模型级连接卡（*STEP 之前）, 预紧 *CLOAD 行, 是否含接触)。

    语法以 ccx 2.22 实跑为准（模块 16 探针，probes 记录于 module_16 文档）：
    - 粘接 tie 模式 / 销轴零间隙绑定 → *TIE, NAME=...（slave, master）；
    - 粘接 merge 模式 → 不出卡（共节点：gmsh removeDuplicateNodes；deck 校验
      两侧面确有共享节点，否则显式报错——防「以为连了其实没连」）；
    - 接触摩擦 → *SURFACE INTERACTION + *FRICTION + *SURFACE BEHAVIOR
      (PRESSURE-OVERCLOSURE=LINEAR) + *CONTACT PAIR（ccx 要求必须定义
      pressure-overclosure，探针实证）；
    - 简化螺栓 → *RIGID BODY（耦合区→参考节点）+ 预紧力对（参考节点上
      ±F_p·n̂ 自平衡 *CLOAD）。*KINEMATIC COUPLING 不被 ccx 2.22 识别
      （calinput fatal，探针实证）——按 ask「以实跑为准」用 ccx 原生等价卡。
    """
    from ..core.connections import (
        BoltConnection,
        BondedConnection,
        ContactFrictionConnection,
        PinConnection,
    )

    from .bond_band import (BOND_BAND_DEFAULT_MM, BOND_TOLERANCE_CAP_MM, BondSide,
                            select_tie_nodes, tie_position_tolerance)

    part_maps = _part_face_maps(project, geom, mesh)
    eid_sorted = sorted(mesh["elems"].keys())
    band_mm = float(bond_band_mm) if bond_band_mm is not None else BOND_BAND_DEFAULT_MM
    # band<=0 显式回到旧行为（整张 CAD 面 + 求解器默认搜索半径）：用于对照实验，
    # 也保证历史工程与旧回归的既有语义不被默认改动。
    band_active = band_mm > 0.0
    # 从面选点的带宽同时受容差上界约束：写进 *TIE 的位置容差不能超过上界，
    # 否则共面贴合面上 CCX 的 tie 搜索会退化（实测 >10 分钟 vs 20 秒）。
    select_band_mm = min(band_mm, BOND_TOLERANCE_CAP_MM) if band_active else band_mm
    cards: list[str] = ["** ===== 连接卡（ccx 2.22 实跑实证语法，模块 16） ====="]
    preload_cload: list[str] = []
    has_contact = False
    max_node = max(mesh["nodes"].keys())
    tie_groups = {}
    for i, conn in enumerate(project.connections, start=1):
        conn.validate(part_ids=[p.part_id for p in project.parts])
        ta = _resolve_conn_face(project, geom, conn.side_a, conn.name, "side_a")
        tb = _resolve_conn_face(project, geom, conn.side_b, conn.name, "side_b")
        ka = next(k for k, part in enumerate(project.parts, start=1) if part.part_id == conn.side_a.part_id)
        kb = next(k for k, part in enumerate(project.parts, start=1) if part.part_id == conn.side_b.part_id)
        faces_a = part_maps[ka][1][ta]
        faces_b = part_maps[kb][1][tb]
        tie_nodes = None
        tie_gap = 0.0
        is_tie = isinstance(conn, (BondedConnection, PinConnection)) and not (
            isinstance(conn, BondedConnection) and conn.mode == "merge")
        if is_tie and band_active:
            # 整张 CAD 面常远大于真实贴合区：按实测几何只把「投影确实落在主面内」
            # 的从面节点写进 *TIE（TYPE=NODE），既保住贴合区里的全部节点，又不会
            # 让贴合区外的节点刷成 no_face/too_far。依据见 solver/bond_band.py。
            side_a = BondSide(faces_a, part_maps[ka][0], eid_sorted, mesh)
            side_b = BondSide(faces_b, part_maps[kb][0], eid_sorted, mesh)
            selection = select_tie_nodes(side_a, side_b, select_band_mm)
            if not selection["slave_nodes"]:
                raise DeckError(
                    f"连接「{conn.name}」的从面 {selection['slave_total']} 个节点在 "
                    f"{select_band_mm:g} mm 内没有一个能投影进主面"
                    "——这不是贴合连接，请改用接触连接或核对所选面")
            tie_nodes = selection["slave_nodes"]
            tie_gap = float(selection["gap_inside_max"])
            tolerance = tie_position_tolerance(tie_gap)
            detail = (f"从面 A {selection['slave_total']} 节点里 {selection['tieable']} 个"
                      f"投影进主面并参与绑定（最大间距 {selection['gap_inside_max']:.4g} mm，"
                      f"位置容差 {tolerance:g} mm）")
            if selection["outside"]:
                detail += (f"，其余 {selection['outside']} 个在贴合区外、不参与绑定"
                           f"（到主面最近 {selection['gap_outside_p50']:.4g}"
                           f"—{selection['gap_outside_max']:.4g} mm，"
                           "因此不会再有未绑定警告，但也不能算全贴合）")
            detail += f"；主面 B {selection['master_facets']} 面片"
            cards.append(f"** 绑定带「{conn.name}」{band_mm:g} mm：{detail}；ADJUST=NO 不移动节点")
        elsets_out: list[str] = []
        sa_lines = _surface_cards("SA", i, ta, ka, part_maps, eid_sorted, elsets_out, facets=faces_a)
        sb_lines = _surface_cards("SB", i, tb, kb, part_maps, eid_sorted, elsets_out, facets=faces_b)
        cards += elsets_out
        cards.append(f"*SURFACE, NAME=C{i}_SA, TYPE=ELEMENT")
        cards += sa_lines
        cards.append(f"*SURFACE, NAME=C{i}_SB, TYPE=ELEMENT")
        cards += sb_lines
        if isinstance(conn, BondedConnection) and conn.mode == "merge":
            ids_a, _coords_a = _face_nodes_of(part_maps, ka, ta, mesh)
            ids_b, _coords_b = _face_nodes_of(part_maps, kb, tb, mesh)
            shared = set(ids_a) & set(ids_b)
            if not shared:
                raise DeckError(
                    f"粘接「{conn.name}」为共节点模式（merge），但两侧面没有共享节点——"
                    "网格须以 remove_duplicate_nodes=True 生成（gmsh removeDuplicateNodes）"
                )
            cards.append(f"** 粘接「{conn.name}」共节点模式：界面共享 {len(shared)} 节点，无需连接卡")
            continue
        if isinstance(conn, (BondedConnection, PinConnection)):
            # 销轴零间隙绑定：从面=销面（side_a，全长圆柱——ccx 投影实测全部成功）
            # 主面=孔壁（side_b，销半段高度）。若反用（从面=孔壁），ccx gentiedmpc
            # 对全部孔壁节点报 "no tied MPC"（588 条实证，module_19）——绑定失效。
            key = (conn.side_a.part_id, conn.side_b.part_id, conn.adjust_nodes)
            entry = tie_groups.setdefault(key, [])
            # 保存实际单元面（两侧整张 CAD 面，供成组主面与诊断用）和绑定带选出的
            # 从面节点。成组 TIE 若在此回退到整张面会把节点级裁剪静默撤销。
            facets = []
            for part_k, faces in ((ka, faces_a), (kb, faces_b)):
                rows = part_maps[part_k][0]
                facets.append({(eid_sorted[rows[ei]], li) for ei, li in faces})
            entry.append((i, facets[0], facets[1], tie_nodes, tie_gap))
        elif isinstance(conn, ContactFrictionConnection):
            cards.append(f"*SURFACE INTERACTION, NAME=C{i}_INT")
            cards.append("*FRICTION")
            cards.append(f"{float(conn.friction_mu):.12g}")
            cards.append("*SURFACE BEHAVIOR, PRESSURE-OVERCLOSURE=LINEAR")
            cards.append(f"{_CONTACT_LINEAR_STIFFNESS_MPA_MM:.12g}, 0.0")
            cards.append(f"*CONTACT PAIR, TYPE=SURFACE TO SURFACE, INTERACTION=C{i}_INT")
            cards.append(f"C{i}_SA, C{i}_SB")
            has_contact = True
        elif isinstance(conn, BoltConnection):
            ref_ids: dict[str, int] = {}
            for side_label, part_k, face_tag in (("A", ka, ta), ("B", kb, tb)):
                face_info = geom.face_by_tag(face_tag)
                center = np.asarray(face_info.center, dtype=float)
                node_ids, coords = _face_nodes_of(part_maps, part_k, face_tag, mesh)
                radius = float(conn.coupling_radius_mm)
                zone = [
                    nid for nid, c in zip(node_ids, coords)
                    if float(np.linalg.norm(np.asarray(c, dtype=float) - center)) <= radius
                ]
                if len(zone) < 2:
                    raise DeckError(
                        f"螺栓「{conn.name}」侧 {side_label} 耦合区（r={radius}mm）内节点不足 "
                        f"({len(zone)})——请增大 coupling_radius_mm 或细化网格"
                    )
                ref_id = max_node + len(ref_ids) * 2 + 1
                ref_ids[side_label] = ref_id
                cards.append(_id_block(f"*NSET,NSET=C{i}_Z{side_label}", zone).rstrip("\n"))
                cards.append(
                    f"*NODE, NSET=C{i}_R{side_label}\n{ref_id}, "
                    f"{center[0]:.12g}, {center[1]:.12g}, {center[2]:.12g}"
                )
                cards.append(f"*RIGID BODY,NSET=C{i}_Z{side_label},REF NODE={ref_id}")
            # 预紧力对：自平衡（ΣF=0）。预紧轴 = 各自侧面的外法向（贴合面法向
            # 互反）——两侧面质心重合（贴合界面），不能用心差作轴（NaN 实证）。
            F_a = np.asarray(geom.face_by_tag(ta).normal, dtype=float)
            F_a = F_a / float(np.linalg.norm(F_a))  # a 侧外法向（指向 b）→ 拉向界面
            F_b = np.asarray(geom.face_by_tag(tb).normal, dtype=float)
            F_b = F_b / float(np.linalg.norm(F_b))  # b 侧外法向（指向 a）→ 拉向界面
            fp = float(conn.pretension_n)
            for (ref_id, n_force) in ((ref_ids["A"], F_a), (ref_ids["B"], F_b)):
                for dof in (1, 2, 3):
                    val = fp * float(n_force[dof - 1])
                    if abs(val) > 0.0:
                        preload_cload.append(f"{ref_id}, {dof}, {val:.12g}")
            cards.append(f"** 螺栓「{conn.name}」预紧力对 F_p={fp:g}N（*RIGID BODY 耦合区 + 参考节点对力）")
        else:
            raise DeckError(f"未知连接类型: {type(conn).__name__}")
    cards.extend(tie_group_cards(tie_groups, band_mm if (tie_groups and band_active) else None))
    return cards, preload_cload, has_contact


def tie_group_cards(groups: dict, tolerance_mm: float | None = None) -> list[str]:
    """One TIE per oriented part pair and adjustment policy.

    Per-connection Cn element surfaces remain available for diagnostics and
    stress integration. Union element surfaces remove duplicate facets.

    tolerance_mm（绑定带）不为 None 时，从面写成 ``*SURFACE, TYPE=NODE``：只包含
    「投影落在主面内、且法向距离 ≤ 带宽」的节点（同一部件对的并集）。这样
    贴合区里的节点全部绑上，贴合区外的节点根本不进入从面，不会产生
    no_face/too_far 警告。``POSITION TOLERANCE`` 逐组按该组实测最大间距给出
    （下限 0.03、上限 0.07）：CCX 的距离判据沿主面片法向，与几何最短距离相差
    一个量级，按几何间距给的 0.0163 仍报 56 个 too_far，0.03 则 0 个；而一律
    0.1 会让共面贴合面的 tie 搜索退化（1mm 杆模型 20 s → >10 min）。ADJUST=NO
    时容差只影响「允许多远找主面」，不移动任何节点坐标。

    tolerance_mm 为 None 时保持旧行为：从面是整张 CAD 面的单元面并集。
    """
    from .bond_band import tie_position_tolerance

    cards = []
    for (_slave, _master, adjust), members in groups.items():
        first = members[0][0]
        prefix = 'G' if len(members) > 1 else 'C'
        if len(members) > 1:
            cards.append('** TIE group connections: ' + ', '.join(str(m[0]) for m in members))
        if tolerance_mm is None:
            names = (f'{prefix}{first}_SA', f'{prefix}{first}_SB')
            if len(members) > 1:
                for column, name in enumerate(names, start=1):
                    by_li = {}
                    for eid, li in sorted(set().union(*(m[column] for m in members))):
                        by_li.setdefault(li, []).append(eid)
                    for li, eids in sorted(by_li.items()):
                        cards.append(_id_block(f'*ELSET,ELSET={name}_{li}', eids).rstrip('\n'))
                    cards.append(f'*SURFACE, NAME={name}, TYPE=ELEMENT')
                    cards.extend(f'{name}_{li}, {NEUTRAL_FACE_TO_S[li]}' for li in sorted(by_li))
            slave_name, master_name = names
        else:
            nodes = sorted({n for member in members if member[3] for n in member[3]})
            slave_name = f'{prefix}{first}_SAT'
            master_name = f'{prefix}{first}_SB'
            cards.append(_id_block(f'*NSET,NSET={slave_name}', nodes).rstrip('\n'))
            cards.append(f'*SURFACE, NAME={slave_name}, TYPE=NODE')
            cards.append(f'{slave_name},')
            if len(members) > 1:
                by_li = {}
                for eid, li in sorted(set().union(*(m[2] for m in members))):
                    by_li.setdefault(li, []).append(eid)
                for li, eids in sorted(by_li.items()):
                    cards.append(_id_block(f'*ELSET,ELSET={master_name}_{li}', eids).rstrip('\n'))
                cards.append(f'*SURFACE, NAME={master_name}, TYPE=ELEMENT')
                cards.extend(f'{master_name}_{li}, {NEUTRAL_FACE_TO_S[li]}' for li in sorted(by_li))
            else:
                master_name = f'C{first}_SB'  # 单成员：沿用已写出的整张主面
        option = f'*TIE, NAME=C{first}_TIE, ADJUST={"YES" if adjust else "NO"}'
        if tolerance_mm is not None:
            # 逐组按实测间距给容差（下限 0.03、上限 0.07）：下限覆盖 CCX 沿法向的
            # 距离判据与几何最短距离的差异，上限避开共面贴合面上的 tie 搜索退化。
            needed = max(float(member[4] or 0.0) for member in members)
            option += f', POSITION TOLERANCE={tie_position_tolerance(needed):g}'
        cards.append(option)
        cards.append(f'{slave_name}, {master_name}')
    return cards


def build_assembly_case_inp(
    mesh_inp_text: str,
    project,  # noqa: ANN001 - core.Project（parts 材料/引用 + connections 连接对象）
    load_case,  # noqa: ANN001 - core.LoadCase
    geom,  # noqa: ANN001 - cad.AssemblyModel（多实体几何；occ tag/面信息）
    *,
    nset_face_tags: dict[str, int] | None = None,
    mesh: dict | None = None,
    bond_band_mm: float | None = None,
) -> str:
    """装配分析 .inp（模块 16）：装配整体在一个 gmsh 会话内网格化（网格段由
    mesher 以 part_elsets 生成 PARTn ELSET——多实体节点号天然不重，免重映射），
    每部件 *MATERIAL/*SOLID SECTION（材料引用 core.materials.part_material），
    连接对象 → 连接卡（*TIE / *CONTACT PAIR+*SURFACE INTERACTION+*FRICTION /
    *RIGID BODY+预紧力对——ccx 2.22 实跑实证语法），载荷/边界复用模块 14
    语义（红线不变：压力=单元面集、面力=CLOAD、力矩=力偶、重力=*DLOAD GRAV）。

    调度沿用 solver/tasks.py（case_NNN 先建后解、二进制捕获、成败='Job
    finished'+解析成功、退出码不作判据）。
    """
    from ..core.materials import part_material, solver_material_dict

    load_case.validate()
    if mesh is None:
        mesh = parse_mesh_inp(mesh_inp_text)
    if not project.parts:
        raise DeckError("装配 deck 需要 project.parts（单零件请用 build_case_inp）")

    # 连接性门（模块 17）：每个部件必须经外部固定（fixed 且已挂接）或某条
    # 连接边链接地——只查**拓扑完全断开**的部件（任何连接对象都计为边）。
    # 断开部件 = 刚度阵奇异：首跑实证（module_17）断带算例 ccx 仍
    # 'Job finished' 但 Ux≈-8.6e10 mm 垃圾解——deck 层显式拒绝比把垃圾结果
    # 交给用户安全。注意（如实记录，不在此拒）：接触可分离、简化螺栓无杆
    # 实体传力路径——拓扑连通不保证传力方向可解。
    grounded: set[str] = set()
    for c in load_case.constraints:
        if c.kind == "fixed" and c.face_tag > 0:
            if c.part_id:
                grounded.add(c.part_id)
            else:
                grounded |= {p.part_id for p in project.parts}  # 旧版整模型固定
    edges: dict[str, set[str]] = {}
    for conn in project.connections:
        edges.setdefault(conn.side_a.part_id, set()).add(conn.side_b.part_id)
        edges.setdefault(conn.side_b.part_id, set()).add(conn.side_a.part_id)
    seen: set[str] = set(grounded)
    stack = list(grounded)
    while stack:
        cur = stack.pop()
        for nb in edges.get(cur, ()):
            if nb not in seen:
                seen.add(nb)
                stack.append(nb)
    floating = sorted({p.part_id for p in project.parts} - seen)
    if floating:
        raise DeckError(
            f"装配连接性检查失败：部件 {floating} 未被外部固定或任何连接边链接地"
            "（拓扑断开=刚度阵奇异）——拒绝求解（实证：断带算例 ccx 仍 Job finished"
            " 但 Ux≈-8.6e10 mm 垃圾解）；注意：接触可分离/简化螺栓无杆实体，"
            "拓扑连通不保证传力方向可解"
        )

    lines: list[str] = [mesh_inp_text.rstrip("\n"), ""]
    lines.append("** ===== 部件材料与截面（装配：每部件一套，模块 16） =====")
    for k, part in enumerate(project.parts, start=1):
        elset_name = f"PART{k}"
        if elset_name not in mesh["elsets"] or not mesh["elsets"][elset_name]:
            raise DeckError(
                f"网格段缺少部件 ELSET {elset_name}（装配网格须以 part_elsets 生成）"
            )
        m = part_material(project, part)
        mat = solver_material_dict(m)
        mname = "MAT_" + re.sub(r"[^A-Za-z0-9_]", "_", part.part_id or f"p{k}")
        lines += [
            f"** 部件 {part.part_id}（{part.name}）材料 {m.name}",
            f"*MATERIAL, NAME={mname}",
            "*ELASTIC",
            f"{mat['E_MPa']:.12g}, {mat['nu']:.12g}",
            "*DENSITY",
            f"{mat['rho_t_mm3']:.12g}",
            f"*SOLID SECTION, ELSET={elset_name}, MATERIAL={mname}",
        ]
    conn_cards, preload_cload, has_contact = _connection_cards(
        project, geom, mesh, bond_band_mm=bond_band_mm)
    lines += conn_cards
    lines += [
        "**",
        "** ===== 边界条件（理想固定假设：面节点三向平动为零，无转动/无施加柔性） =====",
        "*BOUNDARY",
    ]
    boundary_lines, fixed_nsets = _boundary_cards(load_case, mesh, nset_face_tags)
    lines += boundary_lines
    lines += ["**", "*STEP", "*STATIC"]
    if has_contact:
        # 接触为非线性：*STATIC 数据行给初始/总增量（ccx 读卡实证，
        # probe 记录 module_16；增量行必须跟在 *STATIC 之后，不能替换关键字）
        lines.append("0.1, 1.0")
    cload_lines, dload_lines, load_nsets = _load_cards(load_case, mesh, geom, nset_face_tags, project=project)
    if preload_cload:
        cload_lines = preload_cload + cload_lines
    if cload_lines:
        lines += ["*CLOAD"] + cload_lines
    if dload_lines:
        lines += ["*DLOAD"] + dload_lines
    lines += ["**"]
    for nset in fixed_nsets:
        lines += ["*NODE PRINT, NSET=" + nset, "RF"]
    for nset in load_nsets:
        lines += ["*NODE PRINT, NSET=" + nset, "U"]
    lines += [
        "*NODE FILE",
        "U, RF",
        "*EL FILE",
        "S",
        "*END STEP",
        "",
    ]
    return "\n".join(lines)
