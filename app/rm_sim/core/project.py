# -*- coding: utf-8 -*-
"""数据模型骨架（GUI 无关层）。

约束/载荷模型（本模块定义，多态序列化见 core/store.py 的 __type__ 标记）：
- Constraint：固定区域——occ 面 tag 挂接，全约束三向位移。
  简化假设（须随结果一起告知用户）：**理想固定**——挂接面上全部节点
  三向平动位移为零，不含转动自由度、不含螺栓/接触柔性，实际刚度高于真实施加。
- 载荷四类（LoadBase 子类，各自 validate/summary）：
  TractionLoad 面力（总力+方向，语义=总力均分到面，调研报告 §4）；
  PressureLoad 压力（沿面法向，正值为压向面内，CalculiX *DLOAD P 约定）；
  MomentLoad   力矩（加载面+轴+大小）；
  GravityLoad  重力（g 方向可配，mm/s²）。
- LoadCase.is_solvable()：静力可解性——至少一个**已挂接**（face_tag>0）的
  固定约束。重力不消除刚体位移，仅重力无约束的静力问题同样不可解。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .face_identity import FaceFingerprint
from .mesh_controls import MeshControl
from .advanced import AdvancedStudy
from .connections import ConnectionBase
from .part_identity import PartFingerprint


class CaseValidationError(ValueError):
    """约束/载荷定义校验失败（消息列出全部问题）。"""


def _check_num(value, label: str, errors: list[str], *, positive: bool = False, nonzero: bool = False) -> None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        errors.append(f"{label} 必须为数值，实际 {value!r}")
        return
    if positive and value <= 0:
        errors.append(f"{label} 必须为正数，实际 {value!r}")
    if nonzero and value == 0:
        errors.append(f"{label} 不能为 0，实际 {value!r}")


def _check_direction(direction, label: str, errors: list[str]) -> None:
    try:
        arr = np.asarray(direction, dtype=float)
    except (TypeError, ValueError):
        errors.append(f"{label} 必须是长度 3 的数值序列，实际 {direction!r}")
        return
    if arr.shape != (3,) or not np.all(np.isfinite(arr)) or float(np.linalg.norm(arr)) <= 0.0:
        errors.append(f"{label} 必须是长度 3 的非零向量，实际 {direction!r}")


# ---------------------------------------------------------------------------
@dataclass
class Material:
    """材料（默认 Q235 占位；字段单位 N-mm-MPa-t-s，ρ 用 t/mm³）。

    yield_strength_mpa：屈服强度；脆性材料（如灰铸铁）以抗拉强度近似填入，
    None 表示未提供。库定义见 core/materials.py。
    """

    name: str = "Q235"
    e_modulus_mpa: float = 210000.0
    poisson: float = 0.3
    density_t_mm3: float = 7.85e-9
    yield_strength_mpa: float | None = None


@dataclass
class Constraint:
    """约束/连接：挂接目标 = (part_id, 面指纹)（模块 14；单零件 part_id=""）。

    kind 两种（模块 14 is_solvable 扩展）：
    - "fixed"：**外部固定**——挂接面全部节点三向位移为零（接地约束）；
      简化假设（须随结果一起告知用户）：理想固定，不含转动自由度、
      不含螺栓/接触柔性，实际刚度高于真实施加。
    - "bonded"：**内部连接**——部件间粘接/绑定的语义声明（该面与相邻部件
      连接）；**不构成接地**（is_solvable 不计入），接触/绑定求解实现属
      后续模块，现阶段仅进入数据模型与装配树展示。
    face_tag=0 表示尚未挂接几何（定义态）。part_id 指向 Project.parts 条目；
    空 = 旧版单零件全局语义（面在全模型面清单中解析）。
    """

    name: str
    kind: str = "fixed"
    face_tag: int = 0
    face: FaceFingerprint | None = None
    part_id: str = ""  # 挂接部件（空=单零件全局语义）

    VALID_KINDS = ("fixed", "bonded")

    def validate(self) -> None:
        errors: list[str] = []
        if not isinstance(self.name, str) or not self.name.strip():
            errors.append(f"约束名不能为空: {self.name!r}")
        if self.kind not in self.VALID_KINDS:
            errors.append(
                f"kind 必须为 {'/'.join(self.VALID_KINDS)}（fixed=外部固定接地，"
                f"bonded=内部连接不接地），实际 {self.kind!r}"
            )
        if self.kind == "bonded" and not (isinstance(self.part_id, str) and self.part_id.strip()):
            errors.append("内部连接（bonded）必须指明挂接部件 part_id（内部连接只存在于装配部件间）")
        if not isinstance(self.face_tag, int) or isinstance(self.face_tag, bool) or self.face_tag < 0:
            errors.append(f"face_tag 必须为非负整数（0=未挂接），实际 {self.face_tag!r}")
        if errors:
            raise CaseValidationError("约束校验失败: " + "; ".join(errors))

    def summary(self) -> str:
        where = f" @ occ面{self.face_tag}" if self.face_tag else "（未挂接）"
        part = f"［部件 {self.part_id}］" if self.part_id else ""
        if self.kind == "bonded":
            return f"内部连接{part}{where}（不接地）"
        return f"理想固定{part}{where}"


# ---------------------------------------------------------------------------
@dataclass
class LoadBase:
    """载荷基类：name + 挂接面（重力无面，face_tag 恒 0）。

    face：持久化面指纹（跨会话身份，重开时 re-resolve 恢复 face_tag；
    调研报告 §7——gmsh 不承诺重导入后 occ 编号不变，禁止只存整数 tag）。
    part_id（模块 14）：挂接部件引用——空 = 旧版单零件全局语义（面在全模型
    面清单解析）；非空 = 指向 Project.parts 条目，面指纹只在**该部件**的
    面清单内解析（core.face_identity.resolve_face 复用；不匹配该部件任何面
    → 显式报错/需重新确认，绝不静默贴到其他部件的面）。
    """

    name: str
    face_tag: int = 0
    face: FaceFingerprint | None = None
    part_id: str = ""  # 挂接部件（空=单零件全局语义）

    def _part_label(self) -> str:
        """摘要用部件标签（装配树/日志展示；单零件为空串）。"""
        return f"［部件 {self.part_id}］" if self.part_id else ""

    def validate(self) -> None:  # pragma: no cover - 子类实现
        raise NotImplementedError

    def summary(self) -> str:  # pragma: no cover - 子类实现
        raise NotImplementedError


@dataclass
class TractionLoad(LoadBase):
    """面力：总力 + 方向；语义=**总力均分到面**（调研报告 §4：防「每面力」重复施力歧义）。

    转换为*CLOAD：equal_nodes保留旧节点等分；consistent_area按单元面形函数积分均匀面力。
    新GUI默认面积一致积分，旧工程与API默认保留原节点等分。
    """

    total_force_n: float = 0.0
    direction: tuple[float, float, float] = (0.0, -1.0, 0.0)
    distribution: str = 'equal_nodes'  # preserves existing API/projects; new GUI uses consistent_area

    def validate(self) -> None:
        errors: list[str] = []
        if self.distribution not in ('equal_nodes','consistent_area'):
            errors.append('面力分配必须是equal_nodes或consistent_area')
        if not isinstance(self.name, str) or not self.name.strip():
            errors.append(f"载荷名不能为空: {self.name!r}")
        if not isinstance(self.face_tag, int) or isinstance(self.face_tag, bool) or self.face_tag < 0:
            errors.append(f"face_tag 必须为非负整数（0=未挂接），实际 {self.face_tag!r}")
        _check_num(self.total_force_n, "总力 total_force_n [N]", errors, positive=True)
        _check_direction(self.direction, "方向 direction", errors)
        if errors:
            raise CaseValidationError("面力校验失败: " + "; ".join(errors))

    def summary(self) -> str:
        where = f" @ occ面{self.face_tag}" if self.face_tag else "（未挂接）"
        d = np.asarray(self.direction, dtype=float)
        d = d / float(np.linalg.norm(d))
        mode='面积一致积分' if self.distribution=='consistent_area' else '节点等分'
        return f"面力 {self.total_force_n:g}N×({d[0]:.2f},{d[1]:.2f},{d[2]:.2f})，{mode}{self._part_label()}{where}"


@dataclass
class PressureLoad(LoadBase):
    """压力：沿面法向；正值为压向面内（CalculiX *DLOAD P 约定）。

    转换语义红线（调研报告 §7）：必须是**加载面所属单元面集**上的 *DLOAD P，
    禁止按质心面积折算成节点 CLOAD 近似（solver/convert.py::pressure_to_face_set）。
    """

    pressure_mpa: float = 0.0

    def validate(self) -> None:
        errors: list[str] = []
        if not isinstance(self.name, str) or not self.name.strip():
            errors.append(f"载荷名不能为空: {self.name!r}")
        if not isinstance(self.face_tag, int) or isinstance(self.face_tag, bool) or self.face_tag < 0:
            errors.append(f"face_tag 必须为非负整数（0=未挂接），实际 {self.face_tag!r}")
        _check_num(self.pressure_mpa, "压力 pressure_mpa [MPa]", errors, nonzero=True)
        if errors:
            raise CaseValidationError("压力校验失败: " + "; ".join(errors))

    def summary(self) -> str:
        where = f" @ occ面{self.face_tag}" if self.face_tag else "（未挂接）"
        return f"压力 {self.pressure_mpa:g}MPa（沿面法向）{self._part_label()}{where}"


@dataclass
class MomentLoad(LoadBase):
    """力矩：加载面 + 轴 + 大小（N·mm，符号按右手定则）。

    转换语义：加载面等效节点力偶（f_i ∝ 轴×r_i 线性分布，对轴矩精确守恒）。
    """

    axis: tuple[float, float, float] = (0.0, 0.0, 1.0)
    moment_nmm: float = 0.0

    def validate(self) -> None:
        errors: list[str] = []
        if not isinstance(self.name, str) or not self.name.strip():
            errors.append(f"载荷名不能为空: {self.name!r}")
        if not isinstance(self.face_tag, int) or isinstance(self.face_tag, bool) or self.face_tag < 0:
            errors.append(f"face_tag 必须为非负整数（0=未挂接），实际 {self.face_tag!r}")
        _check_num(self.moment_nmm, "力矩 moment_nmm [N·mm]", errors, nonzero=True)
        _check_direction(self.axis, "轴 axis", errors)
        if errors:
            raise CaseValidationError("力矩校验失败: " + "; ".join(errors))

    def summary(self) -> str:
        where = f" @ occ面{self.face_tag}" if self.face_tag else "（未挂接）"
        a = np.asarray(self.axis, dtype=float)
        a = a / float(np.linalg.norm(a))
        return f"力矩 {self.moment_nmm:g}N·mm 绕({a[0]:.2f},{a[1]:.2f},{a[2]:.2f}){self._part_label()}{where}"


@dataclass
class GravityLoad(LoadBase):
    """重力：g 方向可配（mm/s²，N-mm 单位制下标准重力 9810）。

    转换语义：*GRAV，加速度向量 = 单位方向 × g；体积力作用于全体单元。
    """

    direction: tuple[float, float, float] = (0.0, -1.0, 0.0)
    g_mm_s2: float = 9810.0

    def validate(self) -> None:
        errors: list[str] = []
        if not isinstance(self.name, str) or not self.name.strip():
            errors.append(f"载荷名不能为空: {self.name!r}")
        _check_num(self.g_mm_s2, "重力加速度 g [mm/s²]", errors, positive=True)
        _check_direction(self.direction, "重力方向 direction", errors)
        if errors:
            raise CaseValidationError("重力校验失败: " + "; ".join(errors))

    def summary(self) -> str:
        d = np.asarray(self.direction, dtype=float)
        d = d / float(np.linalg.norm(d))
        v = d * self.g_mm_s2
        return f"重力 g={self.g_mm_s2:g}mm/s² → 体积力向量({v[0]:g},{v[1]:g},{v[2]:g}){self._part_label()}"


LoadLike = TractionLoad | PressureLoad | MomentLoad | GravityLoad


# ---------------------------------------------------------------------------
@dataclass
class LoadCase:
    """工况：约束 + 载荷的集合。"""

    name: str = "工况1"
    description: str = ""
    constraints: list[Constraint] = field(default_factory=list)
    loads: list[LoadLike] = field(default_factory=list)

    def validate(self) -> None:
        for c in self.constraints:
            c.validate()
        for ld in self.loads:
            ld.validate()

    def is_solvable(self) -> bool:
        """静力可解性：至少一个**外部固定**（kind="fixed" 且已挂接）约束。

        is_solvable 扩展（模块 14）：内部连接（kind="bonded"）**不构成接地**
        ——装配体各部件经内部连接互连后，仍至少需要一个外部固定约束把系统
        固定到地面，否则整体存在刚体位移（刚度矩阵奇异）。重力不消除刚体
        位移——仅重力/仅内部连接无外部固定的静力问题同样不可解。
        """
        return any(c.kind == "fixed" and c.face_tag > 0 for c in self.constraints)


@dataclass
class Study:
    """研究：材料 + 几何/网格引用 + 工况列表。"""

    name: str = "静力分析"
    material: Material = field(default_factory=Material)
    mesh_size_mm: float = 2.0  # 网格参数（MeshSize mm，档位粗4/中2/细1）
    geometry_file: str = ""  # STEP 几何路径，cad.import_step 后填入
    mesh_file: str = ""  # 网格文件路径，网格模块生成后填入
    load_cases: list[LoadCase] = field(default_factory=lambda: [LoadCase()])
    mesh_controls: list["MeshControl"] = field(default_factory=list)


@dataclass
class CaseRecord:
    """历史求解 case 的可读索引（产物目录 = project_dir/cases/<name>/）。"""

    name: str  # case_001
    job_name: str = ""
    created: str = ""
    load_summary: str = ""
    tip_uy_mm: float | None = None
    max_u_mag_mm: float | None = None
    max_mises_mpa: float | None = None
    rf_sum_n: tuple[float, float, float] | None = None


@dataclass
class Part:
    """装配部件（= STEP 中一个 dim=3 实体的持久化条目；模块 13）。

    身份 = part_id（工程内唯一，p1/p2…）+ fingerprint（几何指纹
    体积/质心/bbox，core.part_identity，跨会话匹配）；name 仅供人读。
    source 为源 STEP 路径，solid_index 为源内实体序（0-based，
    MANIFOLD_SOLID_BREP 文档序，与 occ 实体 tag 升序对应——模块 12 实证）；
    solid_tag 为运行期 occ tag 缓存（重导入后由指纹/solid_index 重定）。
    材料引用 material_name：空 = 继承研究材料（旧版单部件语义，逐字段
    迁移不变量）；非空 = 材料库名（core.materials.part_material 解析）。
    visible：装配树可见性（视口按部件显示/隔离使用）。
    """

    part_id: str
    name: str = ""
    source: str = ""
    solid_index: int = 0
    solid_tag: int = 0  # 运行期缓存；0=未解析
    fingerprint: PartFingerprint | None = None
    material_name: str = ""
    visible: bool = True
    material_snapshot: Material | None = None
    material_confirmed: bool = True

    def validate(self) -> None:
        errors: list[str] = []
        if not isinstance(self.part_id, str) or not self.part_id.strip():
            errors.append(f"part_id 不能为空: {self.part_id!r}")
        if not isinstance(self.solid_index, int) or isinstance(self.solid_index, bool) or self.solid_index < 0:
            errors.append(f"solid_index 必须为非负整数，实际 {self.solid_index!r}")
        if not isinstance(self.visible, bool):
            errors.append(f"visible 必须为布尔值，实际 {self.visible!r}")
        if errors:
            raise CaseValidationError("部件校验失败: " + "; ".join(errors))


def migrate_legacy_parts(project: "Project") -> bool:
    """旧版单部件工程 → parts=[1 条]（模块 13 验收②；幂等，打开路径自动调用）。

    仅有 geometry_file 且无 parts 时迁移：part_id=p1、name=零件、source=几何
    路径、solid_index=0、fingerprint=None（待重导入补齐）、material_name=""
    （继承研究材料——与旧版「整体单一材料」语义逐字段等价）、visible=True。
    工程其余字段一律不动。
    """
    if project.parts or not project.study.geometry_file:
        return False
    project.parts = [
        Part(
            part_id="p1",
            name="零件",
            source=project.study.geometry_file,
            solid_index=0,
            fingerprint=None,
            material_name="",
            visible=True,
        )
    ]
    return True


def assembly_tree_payload(project: "Project") -> dict:
    """装配树 JSON 结构（GUI 装配节点 / --selfcheck 打印 / 验收共用）。

    每部件给出生效材料名（material_name 非空 → 材料库解析；空 → 继承研究
    材料；库名未知显示「未知:<名>」不抛错——树是展示层）。
    """
    from .materials import part_material

    parts = []
    for p in project.parts:
        try:
            mat = part_material(project, p).name
        except KeyError:
            mat = f"未知:{p.material_name}"
        parts.append(
            {
                "part_id": p.part_id,
                "name": p.name,
                "material": mat,
                "material_ref": p.material_name or "(继承研究材料)",
                "visible": bool(p.visible),
                "source": p.source,
                "solid_index": int(p.solid_index),
                "volume_mm3": (p.fingerprint.volume_mm3 if p.fingerprint is not None else None),
            }
        )
    return {
        "assembly": {
            "inherited_material": project.study.material.name,
            "part_count": len(parts),
            "parts": parts,
        }
    }


@dataclass
class Project:
    """工程（持久化根对象）。

    project_dir：工程目录（求解归档 cases/case_NNN/ 与报告的落点）；
    空 = 未设置（此时求解归档走调用方提供的临时目录并明确告知）。
    is_confirmed：边界/载荷面指纹 re-resolve 全部唯一命中为 True；
    False（「需重新确认」）时禁止直接求解（不静默贴错面）。
    dirty：模型/载荷/约束/网格参数修改后置 True，重算生成新 case 后清除。
    cases：历史求解 case 的可读索引。
    """

    name: str = "未命名工程"
    project_dir: str = ""
    is_confirmed: bool = True
    dirty: bool = False
    cases: list[CaseRecord] = field(default_factory=list)
    parts: list[Part] = field(default_factory=list)  # 装配部件（单部件=1 条；空+有几何 → 打开时迁移）
    connections: list[ConnectionBase] = field(default_factory=list)  # 装配连接（模块 15；空=无连接）
    study: Study = field(default_factory=Study)
    geometry_provenance: dict = field(default_factory=dict)
    advanced_studies: list[AdvancedStudy] = field(default_factory=list)

    def part_by_id(self, part_id: str) -> "Part":
        """part_id → 部件；未知抛 KeyError。"""
        for p in self.parts:
            if p.part_id == part_id:
                return p
        raise KeyError(f"工程中没有部件 part_id={part_id!r}")
