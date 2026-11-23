# -*- coding: utf-8 -*-
"""STEP 导入与三维选面（GUI 无关层，gmsh/OCC）。

导入即三检（调研报告 §4 第一步；§7 内部单位体系固定 N-mm-MPa-t-s）：
① 单位检查：解析 STEP 单位声明（SI_UNIT / CONVERSION_BASED_UNIT(INCH)），
   非 mm 时在日志显示「源单位=<单位>」；换算到内部单位 mm，防 1000×/25.4×
   静默量级错误。OCC 读取行为（读时换算到 mm / 原样读入）不做硬编码假设，
   首次导入时用临时米制标定件实测判定（_unit_read_behavior），
   「原样读入」分支由导入器自行 dilate 缩放。
② 实体门：结果必须含 dim=3 实体且体积>0；壳/片体报「非实体，无法网格化」。
③ 部件清单：实体（tag/体积/bbox）与表面（tag/面积/法向/中心点）列表；
   pick_face(点)→面 供约束/载荷挂接。

探针证据：probes/probe1_log.txt（STEP round-trip bbox 精确、x=0 面 tag1、
x=100 面 tag2）；本模块 2026-11-02 探针实测 gmsh 4.15.2 导出写
``SI_UNIT(.MILLI.,.METRE.)``、OCC 读入自动换算到 mm、损坏文件直接 raise。
"""
from __future__ import annotations

import itertools
import logging
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

import gmsh
import numpy as np

from .. import gmsh_runtime
from .units import LengthUnit, UnitParseError, detect_source_unit

log = logging.getLogger("rm_sim.cad.step_import")

# OCC 读 STEP 的单位行为（首次导入时标定，不做硬编码假设）
READ_CONVERTS_TO_MM = "occ_converted_to_mm"  # 读入即换算到 mm（本次环境实测行为）
READ_IDENTITY = "identity"  # 原样读入文件数值，需导入器自行缩放

_behavior_cache: str | None = None
_model_counter = itertools.count(1)

STEP_READ_RE = re.compile(r"SI_UNIT\s*\(\s*\.MILLI\.\s*,\s*\.METRE\.\s*\)")


class StepImportError(RuntimeError):
    """STEP 导入失败（含明确原因）。"""


class NotSolidError(StepImportError):
    """实体门不通过：壳/片体或零体积，无法进入网格化流程。"""


class PickError(StepImportError):
    """选面失败：查询点不在任何表面容差范围内。"""


# --------------------------------------------------------------------------- 标定
def _unit_read_behavior() -> str:
    """实测 gmsh/OCC 读 STEP 的单位行为（进程内缓存一次）。

    标定件：occ 1×1×1 方块导出 STEP（gmsh 写 mm 声明），把声明补正为 METRE
    后重新导入：边长≈1000 ⇒ 读时换算到 mm；≈1 ⇒ 原样读入。
    同时自检单位解析器必须把补正后文件解析为 m（×1000）。
    """
    global _behavior_cache
    if _behavior_cache is not None:
        return _behavior_cache

    with gmsh_runtime.session():
        tmpdir = tempfile.mkdtemp(prefix="rm_sim_unit_calib_")
        try:
            src = Path(tmpdir) / "calib_mm.step"
            name = f"rm_sim_calib_{next(_model_counter)}"
            gmsh.model.add(name)
            gmsh.model.occ.addBox(0.0, 0.0, 0.0, 1.0, 1.0, 1.0)
            gmsh.model.occ.synchronize()
            gmsh.write(str(src))
            gmsh.model.remove()  # 清掉标定临时模型
            text = src.read_text(encoding="utf-8", errors="replace")

            met_text = STEP_READ_RE.sub("SI_UNIT($,$,.METRE.)", text)
            met = Path(tmpdir) / "calib_m.step"
            met.write_text(met_text, encoding="utf-8")

            # 解析器自检：补正后的文件必须被解析为「米」
            from .units import parse_length_units

            met_units = parse_length_units(met_text)
            if not any(abs(u.factor_to_mm - 1000.0) < 1e-6 for u in met_units):
                raise StepImportError(
                    "单位解析器自检失败：标定文件（METRE 声明）未解析出 ×1000 的长度单位"
                )

            name2 = f"rm_sim_calib_{next(_model_counter)}"
            gmsh.model.add(name2)
            ents = gmsh.model.occ.importShapes(str(met))
            gmsh.model.occ.synchronize()
            d3 = [t for d, t in ents if d == 3]
            if not d3:
                raise StepImportError("单位行为标定失败：标定文件没有三维实体")
            bb = gmsh.model.getBoundingBox(3, d3[0])
            edge = bb[3] - bb[0]
            gmsh.model.remove()

            if edge > 316.0:  # 1 m 被读成 1000 mm
                behavior = READ_CONVERTS_TO_MM
            elif edge < 3.16:  # 数值 1 被原样读入（按 mm 解释）
                behavior = READ_IDENTITY
            else:
                raise StepImportError(f"无法判定 gmsh 单位读取行为（标定边长 {edge:.6g}）")
            log.info("gmsh STEP 单位读取行为标定: %s（米制标定件边长 %.6g）", behavior, edge)
            _behavior_cache = behavior
            return behavior
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)  # 只清理本次创建的临时目录


# --------------------------------------------------------------------------- 数据
@dataclass(frozen=True)
class SolidInfo:
    """部件清单条目（内部单位 mm）。"""

    tag: int
    volume_mm3: float
    bbox_min: tuple[float, float, float]
    bbox_max: tuple[float, float, float]
    center_of_mass: tuple[float, float, float]


@dataclass(frozen=True)
class FaceInfo:
    """表面清单条目（内部单位 mm；normal 为单位法向，按实体朝向取外向）。"""

    tag: int
    area_mm2: float
    normal: tuple[float, float, float]
    center: tuple[float, float, float]  # 面上一点（质心的最近点）
    bbox_min: tuple[float, float, float]
    bbox_max: tuple[float, float, float]
    solid_tag: int | None


@dataclass(frozen=True)
class FacePick:
    face: FaceInfo
    distance: float
    closest_point: tuple[float, float, float]


# --------------------------------------------------------------------------- 模型
class SolidModel:
    """导入后的几何模型：清单数据 + 持久的 gmsh 会话（供 pick/后续网格化）。"""

    def __init__(
        self,
        path: Path,
        source_unit: LengthUnit,
        unit_handling: str,
        solids: list[SolidInfo],
        faces: list[FaceInfo],
    ) -> None:
        self.path = path
        self.source_unit = source_unit
        self.unit_handling = unit_handling  # already_mm / occ_converted_to_mm / importer_scaled_to_mm
        self.solids = solids
        self.faces = faces
        self._name: str | None = None
        self._owns_runtime_slot = False

    # ---- 生命周期 ----
    def close(self) -> None:
        """释放 gmsh 模型与运行时引用槽；调用后 pick 会按需重建会话。"""
        if self._name is not None:
            try:
                with gmsh_runtime.session():
                    gmsh.model.setCurrent(self._name)
                    gmsh.model.remove()
            except Exception:
                pass
            self._name = None
        if self._owns_runtime_slot:
            self._owns_runtime_slot = False
            gmsh_runtime.release()

    def __enter__(self) -> "SolidModel":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ---- 查询 ----
    @property
    def total_volume_mm3(self) -> float:
        return sum(s.volume_mm3 for s in self.solids)

    @property
    def overall_bbox(self) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
        lo = [min(s.bbox_min[i] for s in self.solids) for i in range(3)]
        hi = [max(s.bbox_max[i] for s in self.solids) for i in range(3)]
        return tuple(lo), tuple(hi)  # type: ignore[return-value]

    def source_unit_log_line(self) -> str:
        name = self.source_unit.name
        if self.unit_handling == "already_mm":
            return f"源单位={name}（内部单位 N-mm-MPa-t-s，无需换算）"
        if self.unit_handling == "occ_converted_to_mm":
            return f"源单位={name}（读取时已换算到内部单位 mm，×{self.source_unit.factor_to_mm:g}）"
        return f"源单位={name}（导入后已按 ×{self.source_unit.factor_to_mm:g} 缩放到内部单位 mm）"

    def summary_lines(self) -> list[str]:
        lo, hi = self.overall_bbox
        return [
            f"几何已导入: {self.path}",
            self.source_unit_log_line(),
            f"实体 {len(self.solids)} 个, 表面 {len(self.faces)} 个, "
            f"总体积 {self.total_volume_mm3:.6g} mm³, "
            f"bbox=({lo[0]:.6g},{lo[1]:.6g},{lo[2]:.6g})-({hi[0]:.6g},{hi[1]:.6g},{hi[2]:.6g}) mm",
        ]

    # ---- 选面 ----
    def pick_face(self, point, tolerance: float | None = None) -> FacePick:
        """查询点 → 面：返回距点最近的面（点到面距离最小）。

        tolerance 默认 1e-6 × 包围盒对角线；最近距离超差抛 PickError。
        """
        import numpy as np

        self._ensure_loaded()
        lo, hi = self.overall_bbox
        diag = float(np.linalg.norm(np.asarray(hi) - np.asarray(lo)))
        tol = float(tolerance) if tolerance is not None else 1e-6 * max(diag, 1.0)
        coord = [float(point[0]), float(point[1]), float(point[2])]

        best: FaceInfo | None = None
        best_dist = float("inf")
        best_closest: tuple[float, float, float] = (0.0, 0.0, 0.0)
        with gmsh_runtime.session():
            gmsh.model.setCurrent(self._name)
            for f in self.faces:
                closest, _param = gmsh.model.getClosestPoint(2, f.tag, coord)
                dist = float(np.linalg.norm(np.asarray(closest, dtype=float) - np.asarray(coord)))
                if dist < best_dist:
                    best, best_dist = f, dist
                    best_closest = tuple(float(c) for c in closest)  # type: ignore[assignment]
        if best is None:
            raise PickError(f"pick 失败：模型没有表面: {self.path}")
        if best_dist > tol:
            raise PickError(
                f"pick 失败：点 {tuple(coord)} 距最近表面 {best_dist:.6g} mm，"
                f"超过容差 {tol:.6g} mm（面 {best.tag}）: {self.path}"
            )
        return FacePick(face=best, distance=best_dist, closest_point=best_closest)

    # ---- 显示用表面细分（GL/Agg 视口上屏与选面交互的基础） ----
    def tessellate_faces(self, relative_size: float = 50.0) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """把 B-rep 表面细分成三角网格：(points (n,3), tris (m,3), tri→occ面tag (m,))。

        在当前 gmsh 模型上做 2D 网格（MeshSizeMax = 对角线/relative_size），
        提取后清空网格——不影响几何查询（pick_face 用 getClosestPoint，走几何）。
        """
        self._ensure_loaded()
        with gmsh_runtime.session():
            gmsh.model.setCurrent(self._name)
            lo, hi = self.overall_bbox
            diag = float(np.linalg.norm(np.asarray(hi) - np.asarray(lo)))
            gmsh.option.setNumber("Mesh.MeshSizeMax", max(diag / float(relative_size), 1e-9))
            gmsh.option.setNumber("Mesh.ElementOrder", 1)  # 显示细分固定 1 阶（3 节点三角）；
            # C3D10 路径会设 ElementOrder=2，残留会让本函数产出 etype 9 → 0 三角形（模块 8 实测）
            gmsh.model.mesh.generate(2)
            node_tags, node_coords, _param = gmsh.model.mesh.getNodes()
            tags = np.asarray(node_tags, dtype=np.int64)
            xyz = np.ascontiguousarray(node_coords, dtype=np.float64).reshape(-1, 3)
            lut = np.full(int(tags.max()) + 1, -1, dtype=np.int64)
            lut[tags] = np.arange(len(tags), dtype=np.int64)
            tri_blocks: list[np.ndarray] = []
            tag_blocks: list[np.ndarray] = []
            for _d, ftag in gmsh.model.getEntities(2):
                etypes, _etags, enodes = gmsh.model.mesh.getElements(2, int(ftag))
                for et, conn in zip(etypes, enodes):
                    if int(et) != 2:  # 只取 3 节点三角形（默认 1 阶单元）
                        continue
                    conn = np.asarray(conn, dtype=np.int64)
                    if conn.size == 0:
                        continue
                    tri = lut[conn.reshape(-1, 3)]
                    if (tri < 0).any():
                        raise StepImportError(f"表面细分失败：面 {ftag} 引用了未知节点: {self.path}")
                    tri_blocks.append(tri)
                    tag_blocks.append(np.full(len(tri), int(ftag), dtype=np.int64))
            gmsh.model.mesh.clear()
        if not tri_blocks:
            raise StepImportError(f"表面细分失败（0 个三角形）: {self.path}")
        points = xyz
        tris = np.vstack(tri_blocks)
        face_tags = np.concatenate(tag_blocks)
        log.info("表面细分: %d 点, %d 三角形 (MeshSizeMax=%.4g mm)", len(points), len(tris), diag / float(relative_size))
        return points, tris, face_tags

    # ---- 几何查询（约束/载荷挂接用） ----
    def face_by_tag(self, tag: int) -> FaceInfo:
        """occ 面 tag → 面几何信息（面积/法向/中心点/bbox）；未知 tag 抛 KeyError。"""
        for f in self.faces:
            if f.tag == int(tag):
                return f
        raise KeyError(f"模型中没有 occ 面 tag={tag}: {self.path}")

    def point_to_face_distance(self, tag: int, point) -> float:  # noqa: ANN001
        """点到 occ 面的几何距离（getClosestPoint）；用于边界面归类歧义裁决。"""
        self._ensure_loaded()
        with gmsh_runtime.session():
            gmsh.model.setCurrent(self._name)
            closest, _param = gmsh.model.getClosestPoint(
                2, int(tag), [float(point[0]), float(point[1]), float(point[2])]
            )
            return float(np.linalg.norm(np.asarray(closest, dtype=float) - np.asarray(point, dtype=float)))

    # ---- 体积网格（压力单元面集归类用；正式网格模块交付前的网格准备） ----
    def volume_mesh(self, relative_size: float = 10.0) -> "MeshData":
        """对当前模型做 3D 网格并提取四面体（MeshSizeMax = 对角线/relative_size）。

        返回 MeshData（cells 含 "tetra" 或 "tetra10"）；提取后清空网格，
        不影响几何查询。正式网格模块交付后，此处仅作转换器演示/自检用途。
        """
        from ..mesh.mesh_data import MeshData

        self._ensure_loaded()
        with gmsh_runtime.session():
            gmsh.model.setCurrent(self._name)
            lo, hi = self.overall_bbox
            diag = float(np.linalg.norm(np.asarray(hi) - np.asarray(lo)))
            gmsh.option.setNumber("Mesh.MeshSizeMax", max(diag / float(relative_size), 1e-9))
            gmsh.model.mesh.generate(3)
            node_tags, node_coords, _param = gmsh.model.mesh.getNodes()
            tags = np.asarray(node_tags, dtype=np.int64)
            xyz = np.ascontiguousarray(node_coords, dtype=np.float64).reshape(-1, 3)
            lut = np.full(int(tags.max()) + 1, -1, dtype=np.int64)
            lut[tags] = np.arange(len(tags), dtype=np.int64)
            cells: dict[str, list[np.ndarray]] = {}
            etypes, _etags, enodes = gmsh.model.mesh.getElements(3)
            for et, conn in zip(etypes, enodes):
                et = int(et)
                conn = np.asarray(conn, dtype=np.int64)
                if conn.size == 0:
                    continue
                if et == 4:  # 4 节点四面体
                    key, nper = "tetra", 4
                elif et == 11:  # 10 节点四面体
                    key, nper = "tetra10", 10
                else:
                    log.warning("volume_mesh: 忽略未预期的 3D 单元类型 %s", et)
                    continue
                idx = lut[conn.reshape(-1, nper)]
                if (idx < 0).any():
                    raise StepImportError(f"体积网格提取失败：未知节点引用: {self.path}")
                cells.setdefault(key, []).append(idx)
            gmsh.model.mesh.clear()
        if not cells:
            raise StepImportError(f"体积网格提取失败（0 个四面体）: {self.path}")
        flat = {k: np.vstack(v) for k, v in cells.items()}
        log.info(
            "体积网格: %d 点, %s (MeshSizeMax=%.4g mm)",
            len(xyz), {k: len(v) for k, v in flat.items()}, diag / float(relative_size),
        )
        return MeshData(points=xyz, cells=flat)

    # ---- gmsh 会话 ----
    def _ensure_loaded(self) -> None:
        """保证 gmsh 模型在位；模型被回收（finalize/外部清理）时按源文件重建。"""
        try:
            with gmsh_runtime.session():
                gmsh.model.setCurrent(self._name)  # type: ignore[arg-type]
            return
        except Exception:
            log.warning("gmsh 模型 %s 不在位，按源文件重建: %s", self._name, self.path)
        self._rebuild()

    def _rebuild(self) -> None:
        with gmsh_runtime.session():
            self._load_geometry()

    def _load_geometry(self) -> None:
        """从 self.path 导入 gmsh 并重建全部清单数据（需已持有 gmsh 会话）。

        抛出：StepImportError / NotSolidError / UnitParseError（含明确原因）。
        """
        self._name = f"rm_sim_cad_{next(_model_counter)}"
        gmsh.model.add(self._name)

        gmsh.logger.start()
        try:
            ents = gmsh.model.occ.importShapes(str(self.path))
            gmsh_logs = list(gmsh.logger.get())
        except Exception as exc:
            gmsh_logs = _safe_logger_get()
            raise StepImportError(
                f"STEP 导入失败: {self.path}\n"
                f"原因: {type(exc).__name__}: {exc}\n"
                f"gmsh 日志尾部: {_tail(gmsh_logs)}"
            ) from exc
        finally:
            gmsh.logger.stop()
        if not ents:
            raise StepImportError(
                f"STEP 导入失败（0 个实体）: {self.path}\n"
                f"gmsh 日志尾部: {_tail(gmsh_logs)}"
            )
        gmsh.model.occ.synchronize()

        # ② 实体门：必须有 dim=3 实体
        solids_dim = gmsh.model.getEntities(3)
        faces_dim = gmsh.model.getEntities(2)
        if not solids_dim:
            raise NotSolidError(
                f"非实体，无法网格化：STEP 中没有三维实体（dim=3），"
                f"只有 {len(faces_dim)} 个面/壳/片体: {self.path}"
            )

        # ① 单位换算（若 OCC 原样读入且源单位非 mm → 导入器自行缩放）
        behavior = _unit_read_behavior()
        factor = self.source_unit.factor_to_mm
        unit_handling: str
        if abs(factor - 1.0) < 1e-12:
            unit_handling = "already_mm"
        elif behavior == READ_CONVERTS_TO_MM:
            unit_handling = "occ_converted_to_mm"
        else:
            all_dimtags = [(d, t) for d, t in solids_dim + faces_dim]
            gmsh.model.occ.dilate(all_dimtags, 0.0, 0.0, 0.0, factor, factor, factor)
            gmsh.model.occ.synchronize()
            unit_handling = "importer_scaled_to_mm"
            log.info("源文件非 mm 且 OCC 未换算：已按 ×%g 缩放到内部单位 mm", factor)

        # ③ 部件清单：实体
        solids: list[SolidInfo] = []
        for _d, tag in solids_dim:
            vol = float(gmsh.model.occ.getMass(3, tag))
            if vol <= 0.0:
                raise NotSolidError(
                    f"非实体，无法网格化：实体 (3,{tag}) 体积 {vol:.6g} <= 0（退化几何）: {self.path}"
                )
            bb = gmsh.model.getBoundingBox(3, tag)
            com = gmsh.model.occ.getCenterOfMass(3, tag)
            solids.append(
                SolidInfo(
                    tag=int(tag),
                    volume_mm3=vol,
                    bbox_min=(bb[0], bb[1], bb[2]),
                    bbox_max=(bb[3], bb[4], bb[5]),
                    center_of_mass=(com[0], com[1], com[2]),
                )
            )

        # 面 → 所属实体映射
        face_owner: dict[int, int] = {}
        for s in solids:
            try:
                boundary = gmsh.model.getBoundary([(3, s.tag)], oriented=False)
            except Exception:
                boundary = []
            for d, t in boundary:
                if d == 2:
                    face_owner[int(t)] = s.tag

        # ③ 表面清单：面积/法向/中心点
        faces: list[FaceInfo] = []
        for _d, tag in faces_dim:
            area = float(gmsh.model.occ.getMass(2, tag))
            com = gmsh.model.occ.getCenterOfMass(2, tag)
            closest, param = gmsh.model.getClosestPoint(2, tag, list(com))
            normal = gmsh.model.getNormal(tag, list(param)[:2])
            n = _normalize(normal)
            bb = gmsh.model.getBoundingBox(2, tag)
            faces.append(
                FaceInfo(
                    tag=int(tag),
                    area_mm2=area,
                    normal=n,
                    center=(closest[0], closest[1], closest[2]),
                    bbox_min=(bb[0], bb[1], bb[2]),
                    bbox_max=(bb[3], bb[4], bb[5]),
                    solid_tag=face_owner.get(int(tag)),
                )
            )

        self.solids = solids
        self.faces = faces
        self.unit_handling = unit_handling


def _safe_logger_get() -> list[str]:
    try:
        return list(gmsh.logger.get())
    except Exception:
        return []


def _tail(logs: list[str], n: int = 12) -> str:
    msgs = [ln for ln in logs if ln.strip()]
    return " | ".join(msgs[-n:]) if msgs else "（无）"


def _normalize(v) -> tuple[float, float, float]:  # noqa: ANN001
    import math

    x, y, z = float(v[0]), float(v[1]), float(v[2])
    norm = math.sqrt(x * x + y * y + z * z)
    if norm < 1e-300:
        raise StepImportError(f"面法向长度为 0，无法归一化: {v}")
    return (x / norm, y / norm, z / norm)


# --------------------------------------------------------------------------- 入口
def _import_model(path: Path, cls: type[SolidModel]) -> SolidModel:
    """三检公共入口（单位门在文本层、实体门/清单在 _load_geometry）。

    cls 为 SolidModel 或其子类（装配导入复用同一门禁，见 assembly_import）。
    成功时调用方获得持有 gmsh 引用槽的模型；失败时不遗留引用槽。
    """
    p = Path(path)
    if not p.is_file():
        raise StepImportError(f"STEP 文件不存在: {p}")

    text = p.read_text(encoding="utf-8", errors="replace")
    try:
        unit = detect_source_unit(text)
    except UnitParseError as exc:
        raise StepImportError(f"STEP 单位声明解析失败: {p}: {exc}") from exc
    if unit.kind == "assumed":
        log.warning("未找到长度单位声明，按内部单位 mm 处理（请人工确认）: %s", p)

    model = cls(path=p, source_unit=unit, unit_handling="", solids=[], faces=[])
    gmsh_runtime.acquire()
    model._owns_runtime_slot = True  # 该引用槽随模型生命周期转移
    try:
        model._load_geometry()
    except Exception:
        model._owns_runtime_slot = False
        gmsh_runtime.release()
        raise
    return model


def import_step(path: str | Path, *, model_name: str | None = None) -> SolidModel:
    """导入 STEP：三检（单位/实体/清单）+ 源单位日志；失败抛含原因的异常。

    返回的 SolidModel 持有 gmsh 会话（引用槽）；用完调用 close()，
    或用 with 语句。pick_face 在会话被回收后会按源文件自动重建。
    """
    if model_name:
        # 允许调用方指定模型名（当前实现仍使用内部计数名，保证可重建性）
        log.debug("model_name 参数仅作标识，内部使用计数模型名")

    model = _import_model(Path(path), SolidModel)
    p = model.path
    log.info("STEP 导入: %s", p)
    log.info(model.source_unit_log_line())
    lo, hi = model.overall_bbox
    log.info(
        "部件清单: 实体 %d 个（总体积 %.6g mm³）, 表面 %d 个, "
        "bbox=(%.6g,%.6g,%.6g)-(%.6g,%.6g,%.6g) mm",
        len(model.solids), model.total_volume_mm3, len(model.faces),
        lo[0], lo[1], lo[2], hi[0], hi[1], hi[2],
    )
    for s in model.solids:
        log.info(
            "  实体 tag=%d 体积=%.6g mm³ bbox=(%.6g,%.6g,%.6g)-(%.6g,%.6g,%.6g)",
            s.tag, s.volume_mm3, *s.bbox_min, *s.bbox_max,
        )
    return model
