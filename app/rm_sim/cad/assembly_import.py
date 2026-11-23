# -*- coding: utf-8 -*-
"""装配 STEP 导入与部件身份（GUI 无关层，cad）。

在 ``step_import`` 既有能力上的扩展（复用同一三检入口 ``_import_model``：
单位三检 + 实体门 NotSolidError + 清单，位置一律原样保留——STEP 装配位置
不做任何搬移；非 mm 源沿用「读时换算/导入器缩放」两条路径）：

- **部件**：STEP 中每个 (3,tag) 实体 = 一个部件（ProductInfo/PartInfo）；
  装配体不 fuse、不 boolean，实体间贴合/干涉几何按原样保留。
- **部件身份 = 名称 + 几何指纹**：
  - 名称：解析 STEP 文本 PRODUCT 实体（leaf 部件名，见 ``_resolve_part_names``
    的两条引用链）；解析失败回退 ``Part<tag>``。名称仅供人读，同名不保证唯一。
  - 指纹：``core/part_identity.PartFingerprint``（体积/质心/bbox 角点，
    与 core/face_identity.py 面指纹同一模式），是跨会话匹配身份。
- **每部件表面清单**：``FaceInfo.solid_tag``（step_import._load_geometry 的
  face_owner 面→所属实体映射，step_import.py:455-463）按部件分组。
  face_owner 语义（首次实现时固化，probes 实证）：对每个实体取
  getBoundary(oriented=False) 声明其边界面；面被多实体共享时按实体 tag
  递增序后写优先（最后声明者胜）。本模块两个样例均为独占归属（每面恰属
  一个实体）：两叠块 12 面 6+6，搭接+销 17 面 7+7+3——OCC 读入不合并
  不同 B-rep 的重合面。
- **单实体 STEP** → 部件数=1，AssemblyModel 仍是 SolidModel 子类，
  零件模块全部查询（pick_face/tessellate_faces/face_by_tag/…）原样可用。

gmsh 实测（2026-11-02，本模块探针）：两个 box 导出的 STEP 含 2 个
MANIFOLD_SOLID_BREP、3 个 PRODUCT（1 个装配根 + 2 个叶子，叶子名形如
"Open CASCADE STEP translator 7.8 1.1"）；importShapes 返回序 = 文件内
MSB 文档序（(3,1)=先构造的下块）。
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from ..core.part_identity import PartFingerprint, part_fingerprint_from_solid
from ..core.project import Project, Part
from .step_import import FaceInfo, SolidInfo, SolidModel, _import_model

log = logging.getLogger("rm_sim.cad.assembly_import")


@dataclass(frozen=True)
class PartInfo:
    """装配体中的一个部件（= STEP 中一个 dim=3 实体）。

    身份 = name（STEP PRODUCT 名，人读；解析失败回退 Part<tag>）
    + fingerprint（体积/质心/bbox 几何指纹，匹配用）；
    faces = 该部件表面清单（FaceInfo.solid_tag == tag）。
    """

    tag: int
    name: str
    fingerprint: PartFingerprint
    faces: tuple[FaceInfo, ...]


class AssemblyModel(SolidModel):
    """装配几何模型：SolidModel 全部能力 + 部件划分与部件身份。

    单实体 STEP 导入后 parts 恰有 1 个元素——零件模块把返回值当 SolidModel
    用的代码路径不受任何影响（向后兼容）。
    """

    def __init__(
        self,
        path: Path,
        source_unit,
        unit_handling: str,
        solids: list[SolidInfo],
        faces: list[FaceInfo],
        parts: tuple[PartInfo, ...] = (),
    ) -> None:
        super().__init__(path, source_unit, unit_handling, solids, faces)
        self.parts: tuple[PartInfo, ...] = tuple(parts)

    # ---- 部件 ----
    def part_by_tag(self, tag: int) -> PartInfo:
        """occ 实体 tag → 部件；未知 tag 抛 KeyError。"""
        for p in self.parts:
            if p.tag == int(tag):
                return p
        raise KeyError(f"装配中没有部件 tag={tag}: {self.path}")

    def _load_geometry(self) -> None:
        """三检与清单完全复用 SolidModel（含单位换算/face_owner）；随后划分部件。"""
        super()._load_geometry()
        self.parts = _build_parts(self.path, self.solids, self.faces)

    # ---- 展示 ----
    def summary_lines(self) -> list[str]:
        lo, hi = self.overall_bbox
        lines = [
            f"装配已导入: {self.path}",
            self.source_unit_log_line(),
            f"部件 {len(self.parts)} 个, 实体 {len(self.solids)} 个, 表面 {len(self.faces)} 个, "
            f"总体积 {self.total_volume_mm3:.6g} mm³, "
            f"bbox=({lo[0]:.6g},{lo[1]:.6g},{lo[2]:.6g})-({hi[0]:.6g},{hi[1]:.6g},{hi[2]:.6g}) mm",
        ]
        for p in self.parts:
            c = p.fingerprint.centroid
            lines.append(
                f"  部件 tag={p.tag} name='{p.name}' 体积={p.fingerprint.volume_mm3:.6g} mm³ "
                f"质心=({c[0]:.6g},{c[1]:.6g},{c[2]:.6g}) 表面 {len(p.faces)} 个"
            )
        return lines


# --------------------------------------------------------------------------- 部件划分
def _build_parts(path: Path, solids: list[SolidInfo], faces: list[FaceInfo]) -> tuple[PartInfo, ...]:
    """实体清单 + 面归属 → 部件列表（名称/指纹/每部件表面清单）。

    实体序 = occ tag 升序 = STEP 内 MANIFOLD_SOLID_BREP 文档序（gmsh/OCC
    按文档序读取，探针实证）；名称按同一序位关联叶子 PRODUCT。
    """
    names = _resolve_part_names(path, len(solids))
    # OCC labels retain repeated STEP instances that a document-order MSB
    # parser cannot identify. Do not discard them when MSB != instance count.
    import gmsh
    for i, solid in enumerate(solids):
        label = gmsh.model.getEntityName(3, solid.tag)
        if label:
            names[i] = label.rsplit("/", 1)[-1]
    parts: list[PartInfo] = []
    for i, s in enumerate(solids):
        parts.append(
            PartInfo(
                tag=s.tag,
                name=display_part_name(names.get(i, f"Part{s.tag}"), i),
                fingerprint=part_fingerprint_from_solid(s),
                faces=tuple(f for f in faces if f.solid_tag == s.tag),
            )
        )
    unowned = [f.tag for f in faces if f.solid_tag is None]
    if unowned:
        log.warning("%d 个面不属于任何实体（face_owner 未覆盖）: %s", len(unowned), unowned[:8])
    return tuple(parts)


# --------------------------------------------------------------------------- 部件名兜底
# 体验评审 high（部件命名）：OCC 的 STEP 翻译器给无 PRODUCT 名/默认名的实体写
# 「Open CASCADE STEP translator 7.8 1.1」类内部名，学生无法对应到自己的零件。
# 兜底规则：空名或命中翻译器默认名模式 → 『部件N』（N=部件序，1 起）；
# 面引用走 part_id + 几何指纹（与名字无关），改名/兜底不破坏面引用。

_OCC_DEFAULT_NAME = re.compile(r"Open CASCADE|STEP translator", re.IGNORECASE)


def display_part_name(raw: str, index: int) -> str:
    """部件显示名兜底：空名/OCC 翻译器默认名 → 『部件N』（N 从 1 起）。"""
    text = (raw or "").strip()
    if not text or _OCC_DEFAULT_NAME.search(text):
        return f"部件{index + 1}"
    return text


# --------------------------------------------------------------------------- PRODUCT 名解析
# 名称解析语义（首次实现时固化）：leaf 部件名 = 通过以下两条引用链之一可达
# PRODUCT 的名字；两条链都走不通的实体回退 Part<tag>：
#   链A（单件/多数导出器）：ADVANCED_BREP_SHAPE_REPRESENTATION(ABSR, 含 MSB)
#     ← SHAPE_DEFINITION_REPRESENTATION(PDS, ABSR) → PRODUCT_DEFINITION
#     → PRODUCT_DEFINITION_FORMATION → PRODUCT
#   链B（OCC 装配导出）：ABSR ← (REPRESENTATION_RELATIONSHIP(rep1,rep2))
#     ← CONTEXT_DEPENDENT_SHAPE_REPRESENTATION(RR, PDS) → PDS.definition=
#     NEXT_ASSEMBLY_USAGE_OCCURRENCE(parent_PD, child_PD) → child_PD → … → PRODUCT
# MSB 文档序 ↔ occ 实体 tag 升序一一对应（探针实证），名称按该序关联。

_RE_PRODUCT = re.compile(r"#(\d+)\s*=\s*PRODUCT\s*\(\s*'([^']*)'")
_RE_PDF = re.compile(
    r"#(\d+)\s*=\s*PRODUCT_DEFINITION_FORMATION\s*\(\s*'[^']*'\s*,\s*'[^']*'\s*,\s*#(\d+)"
)
_RE_PD = re.compile(
    r"#(\d+)\s*=\s*PRODUCT_DEFINITION\s*\(\s*'[^']*'\s*,\s*'[^']*'\s*,\s*#(\d+)"
)
_RE_PDS = re.compile(r"#(\d+)\s*=\s*PRODUCT_DEFINITION_SHAPE\s*\([^;]*?,\s*#(\d+)\s*\)\s*;")
_RE_NAUO = re.compile(
    r"#(\d+)\s*=\s*NEXT_ASSEMBLY_USAGE_OCCURRENCE\s*\([^;]*?,\s*#(\d+)\s*,\s*#(\d+)"
)
_RE_SDR = re.compile(
    r"#(\d+)\s*=\s*SHAPE_DEFINITION_REPRESENTATION\s*\(\s*#(\d+)\s*,\s*#(\d+)\s*\)"
)
_RE_CDSR = re.compile(
    r"#(\d+)\s*=\s*CONTEXT_DEPENDENT_SHAPE_REPRESENTATION\s*\(\s*#(\d+)\s*,\s*#(\d+)\s*\)"
)
_RE_RR = re.compile(
    r"#(\d+)\s*=\s*\(?\s*REPRESENTATION_RELATIONSHIP\s*\("
    r"\s*'[^']*'\s*,\s*'[^']*'\s*,\s*#(\d+)\s*,\s*#(\d+)"
)
_RE_ABSR = re.compile(
    r"#(\d+)\s*=\s*ADVANCED_BREP_SHAPE_REPRESENTATION\s*\(\s*'[^']*'\s*,\s*\(([^)]*)\)"
)
_RE_MSB = re.compile(r"#(\d+)\s*=\s*MANIFOLD_SOLID_BREP\s*\(")
_ID = re.compile(r"#(\d+)")


def _resolve_part_names(path: Path, solid_count: int) -> dict[int, str]:
    """STEP 文本 → {MSB 文件序(0-based): leaf PRODUCT 名}。

    纯文本层解析（与 cad/units.py 同风格）；任何解析失败只降级为回退名，
    绝不影响导入本身。文件读不到时返回空表。
    """
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        log.warning("部件名解析：STEP 文本不可读（%s），部件名回退 Part<tag>", exc)
        return {}

    product_names = {int(m.group(1)): m.group(2) for m in _RE_PRODUCT.finditer(text)}
    formation_to_product = {int(m.group(1)): int(m.group(2)) for m in _RE_PDF.finditer(text)}
    pd_to_formation = {int(m.group(1)): int(m.group(2)) for m in _RE_PD.finditer(text)}
    # PRODUCT_DEFINITION_SHAPE 末参 = 定义（PD 或 NAUO）
    pds_to_def: dict[int, int] = {}
    for m in _RE_PDS.finditer(text):
        pds_to_def[int(m.group(1))] = int(m.group(2))
    nauo_child_pd = {int(m.group(1)): int(m.group(3)) for m in _RE_NAUO.finditer(text)}
    sdr_pairs = [(int(m.group(2)), int(m.group(3))) for m in _RE_SDR.finditer(text)]
    cdsr_pairs = [(int(m.group(2)), int(m.group(3))) for m in _RE_CDSR.finditer(text)]
    rr_reps = {int(m.group(1)): (int(m.group(2)), int(m.group(3))) for m in _RE_RR.finditer(text)}
    # ABSR 的 items 列表（含 MSB 引用）
    absr_items: dict[int, set[int]] = {}
    for m in _RE_ABSR.finditer(text):
        absr_items[int(m.group(1))] = {int(x) for x in _ID.findall(m.group(2))}
    msb_ids = [int(m.group(1)) for m in _RE_MSB.finditer(text)]

    def name_of_pd(pd_id: int) -> str | None:
        fid = pd_to_formation.get(pd_id)
        pid = formation_to_product.get(fid) if fid is not None else None
        return product_names.get(pid) if pid is not None else None

    def name_of_def_link(def_id: int) -> str | None:
        """PDS/NAUO 末参 → 名：可经 NAUO(child) 或直接 PD。"""
        if def_id in nauo_child_pd:
            return name_of_pd(nauo_child_pd[def_id])
        return name_of_pd(def_id)

    if len(msb_ids) != solid_count:
        log.warning(
            "部件名解析：MANIFOLD_SOLID_BREP %d 个与实体 %d 个不一致，名称回退 Part<tag>",
            len(msb_ids), solid_count,
        )
        return {}

    names: dict[int, str] = {}
    for k, msb in enumerate(msb_ids):
        name: str | None = None
        absrs = [r for r, items in absr_items.items() if msb in items]
        for r in absrs:
            # 链A：SDR(PDS, ABSR)
            for pds_id, rep in sdr_pairs:
                if rep == r and name is None:
                    name = name_of_def_link(pds_to_def.get(pds_id, -1))
            # 链B：CDSR(RR, PDS)，RR 一侧为本 ABSR
            if name is None:
                for rr_id, pds_id in cdsr_pairs:
                    reps = rr_reps.get(rr_id, ())
                    if r in reps and name is None:
                        name = name_of_def_link(pds_to_def.get(pds_id, -1))
            if name:
                break
        if name:
            names[k] = name
    resolved = len(names)
    if resolved and resolved < solid_count:
        log.warning("部件名解析：仅 %d/%d 个实体解析到 PRODUCT 名，其余回退 Part<tag>", resolved, solid_count)
    if resolved:
        log.info("部件名解析: %d/%d 个部件取到 STEP PRODUCT 名", resolved, solid_count)
    return names


# --------------------------------------------------------------------------- 工程部件同步
def sync_parts_from_model(project: Project, model: AssemblyModel) -> list[Part]:
    """AssemblyModel 实体清单 → 工程 parts 列表（导入装配 STEP 后调用，GUI 无关）。

    按 model.solids 序（= occ tag 升序 = MSB 文档序，模块 12 实证）重建：
    part_id=p1..pN、name=STEP PRODUCT 名、source=源路径、solid_index=k、
    solid_tag=occ tag、fingerprint=几何指纹。既有部件按 solid_index 继承
    材料引用与可见性（重新导入同源几何不丢部件管理数据）。
    """
    old_by_index = {p.solid_index: p for p in project.parts}
    parts: list[Part] = []
    for k, solid in enumerate(model.solids):
        old = old_by_index.get(k)
        parts.append(
            Part(
                part_id=f"p{k + 1}",
                name=display_part_name(
                    model.parts[k].name if k < len(model.parts) else f"Part{solid.tag}", k
                ),
                source=str(model.path),
                solid_index=k,
                solid_tag=solid.tag,
                fingerprint=part_fingerprint_from_solid(solid),
                material_name=old.material_name if old is not None else "",
                visible=old.visible if old is not None else True,
                material_snapshot=old.material_snapshot if old is not None else None,
                material_confirmed=old.material_confirmed if old is not None else True,
            )
        )
        parts[-1].validate()
    project.parts = parts
    log.info("工程部件同步: %d 个部件（source=%s）", len(parts), model.path)
    return parts


# --------------------------------------------------------------------------- 入口
def import_assembly(path: str | Path) -> AssemblyModel:
    """导入装配 STEP：与 import_step 同一三检入口 + 部件划分与身份。

    - 每个实体位置原样保留（不做搬移/对齐）；非 mm 源单位沿用换算。
    - 失败抛 StepImportError/NotSolidError（含原因），不产生半成品对象。
    - 返回的 AssemblyModel 持有 gmsh 会话（引用槽）；用完 close() 或 with。
    """
    model = _import_model(Path(path), AssemblyModel)
    log.info("装配导入: %s（部件 %d 个）", model.path, len(model.parts))
    log.info(model.source_unit_log_line())
    lo, hi = model.overall_bbox
    log.info(
        "装配清单: 实体 %d 个（总体积 %.6g mm³）, 表面 %d 个, "
        "bbox=(%.6g,%.6g,%.6g)-(%.6g,%.6g,%.6g) mm",
        len(model.solids), model.total_volume_mm3, len(model.faces),
        lo[0], lo[1], lo[2], hi[0], hi[1], hi[2],
    )
    for p in model.parts:
        log.info(
            "  %s",
            f"部件 tag={p.tag} name='{p.name}' 体积={p.fingerprint.volume_mm3:.6g} mm³ 表面 {len(p.faces)} 个",
        )
    return model


def export_part_steps(project: Project, model: AssemblyModel, directory: str | Path) -> dict[str, Path]:
    """逐部件 STEP 副本；保持全局装配位置，恢复模型可见性和全局导出选项。"""
    import re
    import gmsh
    from .. import gmsh_runtime

    out = Path(directory)
    out.mkdir(parents=True, exist_ok=True)
    model._ensure_loaded()
    result = {}
    with gmsh_runtime.session():
        gmsh.model.setCurrent(model._name)
        entities = gmsh.model.getEntities()
        visibility = [(d, t, gmsh.model.getVisibility(d,t)) for d,t in entities]
        option = gmsh.option.getNumber("Geometry.OCCExportOnlyVisible")
        try:
            gmsh.option.setNumber("Geometry.OCCExportOnlyVisible",1)
            for part in project.parts:
                if not re.fullmatch(r"[A-Za-z0-9_-]+", part.part_id):
                    raise ValueError("部件标识不能用于文件名: " + part.part_id)
                gmsh.model.setVisibility(entities,0,recursive=True)
                gmsh.model.setVisibility([(3,int(model.solids[part.solid_index].tag))],1,recursive=True)
                path = out/(part.part_id+".step")
                gmsh.write(str(path))
                result[part.part_id] = path
        finally:
            for d,t,v in visibility:
                gmsh.model.setVisibility([(d,t)],v)
            gmsh.option.setNumber("Geometry.OCCExportOnlyVisible",option)
    return result
