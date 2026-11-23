# -*- coding: utf-8 -*-
"""自动网格划分与 INP 导出（GUI 无关层，mesh 层）。

gmsh 3D 二阶四面体（C3D10）；MeshSizeMin=Max=mesh_size（probes/probe1 配方，
均匀网格）+ ElementOrder=2。档位：粗 4 / 中 2（默认）/ 细 1 mm。

规避 gmshInpExportNote 三坑（E:/rm_sim/config.json，probe2 实证）：
1. gmsh .inp 在 SaveAll 下会写 1D T3D3 / 2D CPS6 块 → CalculiX zero thickness
   报错：导出时过滤到 *NODE + C3D10，其余单元块全部丢弃（坑回归门）；
2. 物理组只出 ELSET → 面 NSET 不依赖物理组，按**节点坐标**自建
   （面平面距离 + bbox 双条件，probes/probe1 的 1e-6 容差配方）；
3. C3D10 节点序：沿用 gmsh 自身 .inp 输出的连接表（probe1→probe2→ccx
   beamCheck 实证正确），仅对本网格实证，换单元类型/阶次/网格器须重定基线。

质量指标进统计接口与报告：负 Jacobian 单元数（minJacob<0）、min/mean SICN
（gmsh getElementQualities）。
"""
from __future__ import annotations

import logging
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .mesh_data import MeshData

log = logging.getLogger("rm_sim.mesh.mesher")

MESH_PRESETS = {"coarse": 4.0, "medium": 2.0, "fine": 1.0}
DEFAULT_MESH_SIZE_MM = MESH_PRESETS["medium"]
DEFAULT_ORDER = 2
_PLANE_TOL_MM = 1e-6  # probe1 节点坐标选面容差（gmsh 节点落在几何上，~1e-9）

_ELEMENTS_PER_LINE = 10  # probe2 nset/elset 行格式：10 个 id/行


class MeshGenerationError(RuntimeError):
    """网格生成/导出失败（含明确原因）。"""


@dataclass(frozen=True)
class MeshStats:
    """网格统计（与导出的 .inp 逐项一致，验收 ⑥）。"""

    element_count: int
    node_count: int
    element_type: str = "C3D10"
    mesh_size_mm: float = DEFAULT_MESH_SIZE_MM
    order: int = DEFAULT_ORDER
    negative_jacobian_count: int = 0
    min_sicn: float = 0.0
    mean_sicn: float = 0.0

    def report_lines(self) -> list[str]:
        """统计报告行（报告层 mesh_stats_section 直接取用）。"""
        return [
            f"单元: {self.element_count} × {self.element_type}（二阶四面体，order={self.order}）",
            f"节点: {self.node_count}",
            f"MeshSize: {self.mesh_size_mm:g} mm",
            f"质量: 负 Jacobian 单元 {self.negative_jacobian_count} 个, "
            f"min SICN {self.min_sicn:.4f}, mean SICN {self.mean_sicn:.4f}",
        ]


@dataclass
class MeshResult:
    """一次网格划分的结果：网格数据 + 统计 + 按坐标自建的面 NSET。

    node_tags：与 mesh.points 行一一对应的 gmsh 节点 tag（*NODE/*.NSET 编号）；
    消费方严禁用行下标当节点 id（模块 7 实测踩坑：差 1 且可能含 0）。
    element_sets：部件 ELSET（{名: gmsh 单元 tag 元组}，模块 16 装配 INP 用，
    单元 tag 与 INP 单元号同一编号体系）。
    """

    mesh: MeshData
    stats: MeshStats
    node_tags: np.ndarray
    node_sets: dict[str, tuple[int, ...]] = field(default_factory=dict)
    element_sets: dict[str, tuple[int, ...]] = field(default_factory=dict)
    inp_path: Path | None = None
    quality: dict = field(default_factory=dict)


def _node_ids_on_face(
    points: np.ndarray,
    tags: np.ndarray,
    face_info,  # noqa: ANN001
    tol: float = _PLANE_TOL_MM,
) -> tuple[int, ...]:
    """按节点坐标选面节点，返回 **gmsh 节点 tag**（不是数组下标！）。

    坑（模块 7 实证）：下标 0 起而 ccx 节点 tag 1 起，把下标写进 NSET 会产生
    非法节点 id 0，CalculiX 无边界检查直接内存破坏挂死。points 与 tags 一一
    对应（均来自 gmsh.model.mesh.getNodes()）。
    """
    center = np.asarray(face_info.center, dtype=float)
    normal = np.asarray(face_info.normal, dtype=float)
    normal = normal / float(np.linalg.norm(normal))
    lo = np.asarray(face_info.bbox_min, dtype=float) - tol
    hi = np.asarray(face_info.bbox_max, dtype=float) + tol
    dist_plane = np.abs((points - center) @ normal)
    in_bbox = np.all(points >= lo, axis=1) & np.all(points <= hi, axis=1)
    mask = (dist_plane <= tol) & in_bbox
    selected = np.asarray(tags, dtype=np.int64)[mask]
    if len(selected) and int(selected.min()) < 1:
        raise MeshGenerationError("节点 tag 异常（<1），getNodes 返回值不可信")
    return tuple(int(t) for t in selected)


def _filter_mesh_inp(raw_text: str) -> tuple[str, set[int]]:
    """probe2 filter_mesh_inp 的产品化版本：只保留 *NODE 与 *ELEMENT,TYPE=C3D10。

    1D（T3D3）/2D（CPS6）单元块与全部 *ELSET/*NSET 丢弃——前者触发 CalculiX
    zero-thickness 报错（坑 1），后者由 _write_inp 统一重建 ELSET=Eall 与 NSET。
    返回 (过滤后文本, 保留的单元 id 集)。
    """
    out: list[str] = []
    mode: str | None = None
    kept_elems: set[int] = set()
    for line in raw_text.splitlines():
        s = line.strip()
        if s.startswith("*"):
            upper = s.upper()
            if upper.startswith("*NODE"):
                mode, out = "NODE", out + [line]
            elif upper.startswith("*ELEMENT"):
                etype = "?"
                for part in s.split(",")[1:]:
                    key, _, val = part.partition("=")
                    if key.strip().lower() == "type":
                        etype = val.strip().upper()
                        break
                mode = "ELEM" if etype == "C3D10" else None
                if mode:
                    out.append(line)
            else:
                mode = None  # 丢弃 *ELSET / *NSET / 其他关键字块
            continue
        if mode == "NODE":
            out.append(line)
        elif mode == "ELEM":
            kept_elems.add(int(s.split(",")[0]))
            out.append(line)
    return "\n".join(out) + "\n", kept_elems


def _id_block(header: str, ids) -> str:  # noqa: ANN001
    """probe2 行格式：每行 10 个 id，逗号分隔。"""
    lines: list[str] = [header]
    row: list[str] = []
    for i in ids:
        row.append(str(int(i)))
        if len(row) == _ELEMENTS_PER_LINE:
            lines.append(", ".join(row))
            row = []
    if row:
        lines.append(", ".join(row))
    return "\n".join(lines) + "\n"


def generate_tet10_mesh(
    geom,  # noqa: ANN001 - cad.SolidModel（duck-typing，避免 mesh→cad 静态依赖）
    mesh_size_mm: float = DEFAULT_MESH_SIZE_MM,
    *,
    nset_faces: dict[str, int] | None = None,
    inp_path: str | Path | None = None,
    part_elsets: dict[str, int] | None = None,
    remove_duplicate_nodes: bool = False,
    curvature: float | None = None,
    min_size_mm: float | None = None,
    high_order_optimize: bool = False,
    local_controls: list[dict] | None = None,
) -> MeshResult:
    """划分二阶四面体网格并可选导出 CalculiX INP（三坑规避内置）。

    geom: 已导入的 cad.SolidModel（duck-typing：_ensure_loaded/_name/overall_bbox/
    faces/face_by_tag）。nset_faces: {NSET 名: occ 面 tag}（如 {"FIXED": 1,
    "LOADFACE": 2}），按节点坐标自建 NSET（装配流用部件前缀名）。inp_path 给出时
    导出过滤后的 INP。

    part_elsets（模块 16 装配）: {ELSET 名: occ 实体 tag}（如 {"PART1": 1}）——
    每实体的 C3D10 单元 tag 集，写 *ELSET 并回填 element_sets；整个装配在
    同一 gmsh 会话内网格化，多实体节点号天然不重、无需重映射。
    remove_duplicate_nodes: 网格化后调用 gmsh.model.mesh.removeDuplicateNodes()
    ——共节点粘接模式：贴合界面重合节点合并为共享节点（注意：gmsh 4.15 的
    API 名是 removeDuplicateNodes，不是 removeAllDuplicates）。合并后 INP 由
    gmsh.write 导出（C3D10 连接表按 ccx 约定重排，手工写 getElements 连接表
    会中节点错位 → 负 Jacobian，探针实证）。
    """
    from .. import gmsh_runtime

    if mesh_size_mm <= 0.0:
        raise MeshGenerationError(f"MeshSize 必须为正数，实际 {mesh_size_mm!r}")
    geom._ensure_loaded()
    with gmsh_runtime.session():
        import gmsh

        gmsh.model.setCurrent(geom._name)
        # 默认 probe1 配方 min=max；curvature/min_size 给出时按曲率自适应加密
        # （整装薄壁/销孔曾因均匀 3mm 出现 2520 个负 Jacobian，曲率+高阶优化后归零）。
        controls=list(local_controls or [])
        minimum=min([float(min_size_mm or mesh_size_mm)]+[c['size_mm'] for c in controls])
        gmsh.option.setNumber("Mesh.MeshSizeMin", minimum)
        gmsh.option.setNumber("Mesh.MeshSizeMax", float(mesh_size_mm))
        gmsh.option.setNumber("Mesh.MeshSizeFromCurvature", float(curvature or 0))
        gmsh.option.setNumber("Mesh.ElementOrder", DEFAULT_ORDER)
        from .refinement import size_fields
        with size_fields(gmsh,controls,mesh_size_mm):
            gmsh.model.mesh.generate(3)
        if high_order_optimize:
            # 两步缺一不可（2026-11-02 整装实测）：
            # * 只做 HighOrderElastic + HighOrderFastCurving 时仍有 88 个
            #   负 Jacobian、minSICN=-0.374；先一阶优化再升阶也留下 74/18 个
            #   负单元——都不可用；
            # * optimize("HighOrder") 里的 netgen 高阶优化才是消掉这些负单元的
            #   一步，但它偶发抛 C++ runtime_error
            #   「Failed to reach critical value in pass 1 for measure(s): ScaledJac」，
            #   native terminate、Python 接不住，进程直接被杀死。实测固定单线程
            #   （General.NumThreads/Mesh.MaxNumThreads* = 1）也不能消除，只是
            #   让网格阶段慢很多，故已回退；崩溃由 solver/workflow.py 的
            #   进程级重试兜底（识别崩溃文案 → 换更保守配方重试）。
            gmsh.model.mesh.optimize("HighOrderElastic")
            gmsh.model.mesh.optimize("HighOrder")
        if remove_duplicate_nodes:
            gmsh.model.mesh.removeDuplicateNodes()

        node_tags, node_coords, _param = gmsh.model.mesh.getNodes()
        tags = np.asarray(node_tags, dtype=np.int64)
        xyz = np.ascontiguousarray(node_coords, dtype=np.float64).reshape(-1, 3)
        lut = np.full(int(tags.max()) + 1, -1, dtype=np.int64)
        lut[tags] = np.arange(len(tags), dtype=np.int64)

        etypes, etags_list, enodes_list = gmsh.model.mesh.getElements(3)
        tet_tags: np.ndarray | None = None
        tet_conn: np.ndarray | None = None
        for et, etags, enodes in zip(etypes, etags_list, enodes_list):
            if int(et) == 11:  # 10 节点二阶四面体
                tet_tags = np.asarray(etags, dtype=np.int64)
                conn = np.asarray(enodes, dtype=np.int64).reshape(-1, 10)
                tet_conn = lut[conn]
                if (tet_conn < 0).any():
                    raise MeshGenerationError("网格提取失败：C3D10 引用了未知节点")
                break
        if tet_conn is None or len(tet_conn) == 0:
            raise MeshGenerationError(
                f"未生成二阶四面体（C3D10）网格：请检查 ElementOrder/几何（尺寸 {mesh_size_mm}mm）"
            )

        # 质量指标（gmsh getElementQualities）：minSICN + minDetJac（Jacobian 行列式，
        # 本版 gmsh 4.15 无 "minJacob" 名——实测探针确认，minDetJac<0 即负 Jacobian 单元）
        try:
            sicn = np.asarray(
                gmsh.model.mesh.getElementQualities(list(map(int, tet_tags)), "minSICN"),
                dtype=float,
            )
            jac = np.asarray(
                gmsh.model.mesh.getElementQualities(list(map(int, tet_tags)), "minDetJac"),
                dtype=float,
            )
        except Exception as exc:
            raise MeshGenerationError(f"质量指标计算失败（getElementQualities）: {exc}") from exc
        stats = MeshStats(
            element_count=int(len(tet_conn)),
            node_count=int(len(xyz)),
            element_type="C3D10",
            mesh_size_mm=float(mesh_size_mm),
            order=DEFAULT_ORDER,
            negative_jacobian_count=int((jac < 0.0).sum()),
            min_sicn=float(sicn.min()) if len(sicn) else 0.0,
            mean_sicn=float(sicn.mean()) if len(sicn) else 0.0,
        )

        # 面 NSET：按节点坐标自建（坑 2）——注意返回的是 gmsh 节点 tag
        node_sets: dict[str, tuple[int, ...]] = {}
        for name, face_tag in (nset_faces or {}).items():
            face_info = geom.face_by_tag(int(face_tag))
            face_nodes, _, _ = gmsh.model.mesh.getNodes(2, int(face_tag), includeBoundary=True)
            node_sets[str(name)] = tuple(sorted(set(int(n) for n in face_nodes)))

        # 部件 ELSET（模块 16）：每实体的 C3D10 单元 tag（同一编号体系 == INP 单元号）
        element_sets: dict[str, tuple[int, ...]] = {}
        for name, solid_tag in (part_elsets or {}).items():
            _et, etl, _en = gmsh.model.mesh.getElements(3, int(solid_tag))
            ids: list[int] = []
            for block in etl:
                ids.extend(int(x) for x in block)
            element_sets[str(name)] = tuple(sorted(ids))

        from .refinement import quality_record
        quality=quality_record(tet_tags,tet_conn,xyz,sicn,jac,element_sets,
            dict(global_size_mm=float(mesh_size_mm),min_size_mm=minimum,curvature=float(curvature or 0),
                 high_order_optimize=bool(high_order_optimize),local_controls=controls))
        if inp_path is not None:
            import json
            quality_path=Path(inp_path).with_suffix('.quality.json')
            quality_path.parent.mkdir(parents=True,exist_ok=True)
            quality_path.write_text(json.dumps(quality,ensure_ascii=False,indent=2),encoding='utf-8')

        # 可选 INP 导出（坑 1/3：gmsh 原始输出 → 过滤 → 重建 ELSET/NSET）
        inp_path_out: Path | None = None
        if inp_path is not None:
            gmsh.option.setNumber("Mesh.SaveAll", 1)
            tmpdir = tempfile.mkdtemp(prefix="rm_sim_mesh_inp_")
            try:
                raw = Path(tmpdir) / "raw.inp"
                gmsh.write(str(raw))
                filtered, kept_elems = _filter_mesh_inp(
                    raw.read_text(encoding="utf-8", errors="replace")
                )
            finally:
                shutil.rmtree(tmpdir, ignore_errors=True)
            if not kept_elems:
                raise MeshGenerationError("INP 过滤后没有任何 C3D10 单元")
            if kept_elems != set(int(t) for t in tet_tags):
                raise MeshGenerationError(
                    f"INP 过滤后单元集与提取网格不一致（{len(kept_elems)} vs {len(tet_tags)}）"
                )
            text = filtered
            text += _id_block("*ELSET,ELSET=Eall", sorted(kept_elems))
            for name, ids in element_sets.items():
                if not ids:
                    raise MeshGenerationError(
                        f"部件 ELSET {name}（实体 {dict(part_elsets or {}).get(name)}）没有任何 C3D10 单元"
                    )
                text += _id_block(f"*ELSET,ELSET={name}", ids)
            for name, ids in node_sets.items():
                if not ids:
                    log.warning("NSET %s 为空（面 %s 上没有节点）", name, dict(nset_faces or {}).get(name))
                text += _id_block(f"*NSET,NSET={name}", ids)
            out = Path(inp_path)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(text, encoding="utf-8")
            inp_path_out = out
            log.info("INP 已导出: %s", out)

        gmsh.model.mesh.clear()

    mesh = MeshData(points=xyz, cells={"tetra10": tet_conn})
    log.info(
        "网格生成: %d 单元 C3D10, %d 节点, MeshSize=%gmm (min=%g, curvature=%g, 高阶优化=%s); "
        "负 Jacobian %d, min SICN %.4f",
        stats.element_count, stats.node_count, mesh_size_mm,
        float(min_size_mm or mesh_size_mm), float(curvature or 0.0), high_order_optimize,
        stats.negative_jacobian_count, stats.min_sicn,
    )
    result = MeshResult(mesh=mesh, stats=stats, node_tags=tags,
                        node_sets=node_sets, element_sets=element_sets,
                        inp_path=inp_path_out,quality=quality)
    return result
