# -*- coding: utf-8 -*-
"""材料库（GUI 无关层）：内置常用材料 + 自定义库持久化 + 求解属性导出。

单位体系（全库强制，N-mm-MPa-t-s）：E/屈服强度 MPa，ν 无量纲，ρ 用 t/mm³。

- 内置库只读（BUILTIN_MATERIALS，8 种：Q235/Q345/45钢/6061/7075/TC4/HT250/尼龙PA6）；
- 自定义库持久化 materials.json（默认 E:/rm_sim/materials.json，
  环境变量 RM_SIM_MATERIALS 覆盖；测试用显式 path 参数写临时文件），
  原子写盘（.rm_tmp + os.replace），加载时逐条校验（防手改损坏）；
- 同名自定义材料遮蔽内置（内置不可删改）；
- 零件指定材料 set_study_material(project, name) 无 GUI 可调；
- solver_material_dict 导出求解器字段名（与 probes/beamCheck.json parameters
  的 E_MPa/nu 对齐；ρ 键 rho_t_mm3，beamCheck 无对应字段）。
"""
from __future__ import annotations

import datetime as _dt
import json
import os
from dataclasses import asdict
from pathlib import Path

from .project import Material

DEFAULT_MATERIALS_PATH = Path("E:/rm_sim/materials.json")
MATERIALS_ENV_VAR = "RM_SIM_MATERIALS"
MATERIALS_SCHEMA = "rm_sim.materials@1"

NUM_OK = (int, float)


class MaterialValidationError(ValueError):
    """材料参数校验失败（消息列出全部问题）。"""


def materials_path() -> Path:
    env = os.environ.get(MATERIALS_ENV_VAR, "").strip()
    return Path(env) if env else DEFAULT_MATERIALS_PATH


# ---------------------------------------------------------------------------
# 内置库（只读）。数值取常用文献/手册范围中值；验收 ① 对关键字段有 ±2%/±3% 门。
BUILTIN_MATERIALS: list[Material] = [
    Material(name="Q235", e_modulus_mpa=210000.0, poisson=0.3, density_t_mm3=7.85e-9,
             yield_strength_mpa=235.0),
    Material(name="Q345", e_modulus_mpa=210000.0, poisson=0.3, density_t_mm3=7.85e-9,
             yield_strength_mpa=345.0),
    Material(name="45钢", e_modulus_mpa=210000.0, poisson=0.3, density_t_mm3=7.85e-9,
             yield_strength_mpa=355.0),
    Material(name="6061", e_modulus_mpa=68900.0, poisson=0.33, density_t_mm3=2.70e-9,
             yield_strength_mpa=276.0),
    Material(name="7075", e_modulus_mpa=71700.0, poisson=0.33, density_t_mm3=2.81e-9,
             yield_strength_mpa=503.0),
    Material(name="TC4", e_modulus_mpa=110000.0, poisson=0.34, density_t_mm3=4.43e-9,
             yield_strength_mpa=825.0),
    Material(name="HT250", e_modulus_mpa=110000.0, poisson=0.26, density_t_mm3=7.28e-9,
             yield_strength_mpa=250.0),  # 灰铸铁无屈服，按抗拉强度 250MPa 近似
    Material(name="尼龙PA6", e_modulus_mpa=3000.0, poisson=0.4, density_t_mm3=1.14e-9,
             yield_strength_mpa=80.0),
]


def validate_material(m: Material) -> None:
    """校验材料字段；不合法抛 MaterialValidationError（消息列出全部问题）。"""
    errors: list[str] = []
    if not isinstance(m.name, str) or not m.name.strip():
        errors.append(f"材料名不能为空: {m.name!r}")
    if not (isinstance(m.e_modulus_mpa, NUM_OK) and m.e_modulus_mpa > 0):
        errors.append(f"弹性模量 E 必须为正数（MPa），实际 {m.e_modulus_mpa!r}")
    nu = m.poisson
    if not (isinstance(nu, NUM_OK) and 0.0 < nu < 0.5):
        errors.append(f"泊松比 ν 必须满足 0 < ν < 0.5，实际 {nu!r}")
    if not (isinstance(m.density_t_mm3, NUM_OK) and m.density_t_mm3 > 0):
        errors.append(f"密度 ρ 必须为正数（t/mm³），实际 {m.density_t_mm3!r}")
    y = m.yield_strength_mpa
    if y is not None and not (isinstance(y, NUM_OK) and y > 0):
        errors.append(f"屈服强度必须为正数或留空，实际 {y!r}")
    if errors:
        raise MaterialValidationError("材料校验失败: " + "; ".join(errors))


# ---------------------------------------------------------------------------
# 自定义库持久化
def load_custom_materials(path: str | Path | None = None) -> list[Material]:
    """读自定义材料库；文件不存在返回空表；损坏/非法条目抛 MaterialValidationError。"""
    p = Path(path) if path is not None else materials_path()
    if not p.is_file():
        return []
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
    except ValueError as exc:
        raise MaterialValidationError(f"自定义材料库文件损坏: {p}: {exc}") from exc
    if not isinstance(data, dict) or data.get("schema") != MATERIALS_SCHEMA:
        raise MaterialValidationError(f"自定义材料库 schema 不符（期望 {MATERIALS_SCHEMA}）: {p}")
    items = data.get("materials", [])
    if not isinstance(items, list):
        raise MaterialValidationError(f"自定义材料库 materials 节必须是数组: {p}")
    out: list[Material] = []
    for item in items:
        m = Material(
            name=str(item.get("name", "")),
            e_modulus_mpa=item.get("e_modulus_mpa"),
            poisson=item.get("poisson"),
            density_t_mm3=item.get("density_t_mm3"),
            yield_strength_mpa=item.get("yield_strength_mpa"),
        )
        validate_material(m)  # 防手改损坏：库内条目也必须合法
        out.append(m)
    return out


def save_custom_materials(materials: list[Material], path: str | Path | None = None) -> Path:
    """保存自定义材料库（先逐条校验，再原子写盘）。"""
    for m in materials:
        validate_material(m)
    p = Path(path) if path is not None else materials_path()
    payload = {
        "schema": MATERIALS_SCHEMA,
        "saved_at": _dt.datetime.now().isoformat(timespec="seconds"),
        "materials": [asdict(m) for m in materials],
    }
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".rm_tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp, p)
    except Exception:
        try:
            tmp.unlink()  # 仅清理本次创建的临时文件
        except OSError:
            pass
        raise
    return p


def add_custom_material(m: Material, path: str | Path | None = None) -> Material:
    """新增自定义材料（校验 + 查重 + 持久化）。"""
    validate_material(m)
    mats = load_custom_materials(path)
    if any(x.name == m.name for x in mats):
        raise MaterialValidationError(f"自定义材料重名: {m.name}")
    mats.append(m)
    save_custom_materials(mats, path)
    return m


def update_custom_material(name: str, m: Material, path: str | Path | None = None) -> Material:
    """更新自定义材料（可改名；改名后与其他条目查重）。"""
    validate_material(m)
    mats = load_custom_materials(path)
    idx = next((i for i, x in enumerate(mats) if x.name == name), None)
    if idx is None:
        raise MaterialValidationError(f"自定义材料不存在（内置材料不可修改）: {name}")
    if m.name != name and any(x.name == m.name for x in mats):
        raise MaterialValidationError(f"自定义材料重名: {m.name}")
    mats[idx] = m
    save_custom_materials(mats, path)
    return m


def remove_custom_material(name: str, path: str | Path | None = None) -> None:
    """删除自定义材料；内置材料不在自定义库中 → 报错（保护内置库）。"""
    mats = load_custom_materials(path)
    remaining = [x for x in mats if x.name != name]
    if len(remaining) == len(mats):
        raise MaterialValidationError(f"自定义材料不存在（内置材料不可删除）: {name}")
    save_custom_materials(remaining, path)


# ---------------------------------------------------------------------------
# 合并视图与零件指定
def all_materials(path: str | Path | None = None) -> list[Material]:
    """内置 + 自定义合并视图（同名自定义遮蔽内置；内置在前）。"""
    custom = load_custom_materials(path)
    custom_names = {m.name for m in custom}
    return [m for m in BUILTIN_MATERIALS if m.name not in custom_names] + custom


def find_material(name: str, path: str | Path | None = None) -> Material:
    """按名查找（先自定义后内置）；未知抛 KeyError。"""
    for m in all_materials(path):
        if m.name == name:
            return m
    raise KeyError(f"未知材料: {name!r}")


def set_study_material(project, name: str, path: str | Path | None = None) -> Material:
    """零件/研究指定材料并写入数据模型（无 GUI 可调）。"""
    m = find_material(name, path)
    project.study.material = m
    return m


def part_material(project, part, path: str | Path | None = None) -> Material:  # noqa: ANN001
    """部件生效材料（模块 13）：material_name 非空 → 库解析（未知抛 KeyError）；
    空 = 继承研究材料（旧版单部件迁移语义）。"""
    if part.material_name:
        snapshot = getattr(part, "material_snapshot", None)
        if snapshot is not None and snapshot.name == part.material_name:
            return snapshot
        return find_material(part.material_name, path)
    return project.study.material


def set_part_material(project, part_id: str, name: str, path: str | Path | None = None) -> Material:  # noqa: ANN001
    """按 part_id 设置部件材料引用（先在库中校验存在性再写入；未知部件/材料抛 KeyError）。
    只改动目标部件——其余部件的 material_name 与研究材料不受影响（per-part 隔离）。"""
    part = project.part_by_id(part_id)
    m = find_material(name, path)
    part.material_name = m.name
    from dataclasses import replace
    part.material_snapshot = replace(m)
    part.material_confirmed = True
    return m


# 部件名 → 推荐材料的规则（只做建议，用户一次性确认后才写入）。
# 已指定的材料优先保留；名称没有足够信息时维持待指定。
_SUGGEST_RULES: list[tuple[tuple[str, ...], str, str]] = [
    (("6061",), "6061", "名称明确标注 6061；材料状态与强度仍需核实"),
    (("7075",), "7075", "名称明确标注 7075；材料状态与强度仍需核实"),
    (("钛", "tc4", "titanium"), "TC4", "名称含钛"),
    (("尼龙", "pa6", "nylon"), "尼龙PA6", "名称含尼龙"),
    (("轴承", "bearing"), "45钢", "轴承按整体钢件近似（滚动体与游隙不建模）"),
    (("销", "轴", "螺钉", "螺栓", "螺母", "pin", "shaft", "screw", "bolt"), "45钢", "轴销类常用 45 钢"),
]

_SUGGEST_DEFAULT = (None, "名称无法确定材料，请指定；不自动假定铝合金牌号")


def suggest_material(part) -> dict:  # noqa: ANN001
    """按部件名推荐内置材料：{"name", "reason"}。

    只做建议：装配里 20 个部件逐一手点太慢，但材料直接影响结果，必须由用户
    一次性确认（GUI 会列出每条推荐与理由）。保留已有确认材料，包括它的快照。
    没有可靠关键词时 name=None；碳纤维不能由名称转换成各向同性金属。
    """
    text = f"{getattr(part, 'name', '')} {getattr(part, 'part_id', '')}".lower()
    if getattr(part, 'material_confirmed', False) and getattr(part, 'material_name', ''):
        return {"name": part.material_name, "reason": "保留已指定材料及材料快照", "preserve": True}
    if any(word in text for word in ("碳纤", "cfrp", "carbon fiber")):
        return {"name": None, "reason": "需要复合材料属性，不能用金属材料自动替代"}
    for words, name, reason in _SUGGEST_RULES:
        if any(word in text for word in words):
            return {"name": name, "reason": reason}
    return {"name": _SUGGEST_DEFAULT[0], "reason": _SUGGEST_DEFAULT[1]}


def solver_material_dict(m: Material) -> dict:
    """导出求解器输入材料属性；键名与 probes/beamCheck.json parameters 对齐
    （E_MPa/nu），ρ 键名 rho_t_mm3（beamCheck 无该字段）。屈服强度不进求解
    弹性段（后续塑性模块使用）。"""
    return {
        "E_MPa": float(m.e_modulus_mpa),
        "nu": float(m.poisson),
        "rho_t_mm3": float(m.density_t_mm3),
    }
