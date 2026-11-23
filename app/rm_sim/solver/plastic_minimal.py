# -*- coding: utf-8 -*-
"""塑性变形最小可验证算例（模块59）：CalculiX *PLASTIC 双线性模型。

把握不足、拆最小算例如实标注：
- 切线模量取常用估值 E/100，**未经材料试验标定**；
- 损伤（DamageSettings）本轮不做——『损伤分析未完成』；
- 几何线性（小应变）口径；NonlinearSettings.plasticity_enabled=False 时
  材料卡不得出现 *PLASTIC（回归保护）。

两个最小算例：
① 单单元（1×1×1 mm C3D8）单轴拉至 1.5×屈服后卸载再加载：
   残余等效塑性应变 vs ε_p=ε_total−σ/E；塑性段割线刚度 vs 切线模量；
② 悬臂梁（10×10×100）端部位移加载：极限载荷 vs 塑性铰理论 P=M_p/L
   （矩形截面 M_p=σ_y·b·h²/4）。
"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np

# 完成度标注（模块59 红线，写进报告与 verification.json）。
PLASTICITY_COMPLETENESS = {
    "bilinear_model": "*PLASTIC 双线性（屈服 + 切线模量估值）",
    "tangent_modulus_calibrated": False,
    "tangent_modulus_source": "常用估值 E/100——未经材料试验标定",
    "damage_implemented": False,
    "damage_statement": "损伤分析未完成（DamageSettings 本轮不实现，未生成任何损伤卡）",
    "statement": "塑性参数未经材料试验标定；损伤分析未完成",
}


def tangent_estimate(e_mpa: float) -> float:
    """双线性切线模量常用估值 E/100（未经材料试验标定——报告必须明示）。"""
    return float(e_mpa) / 100.0


def material_block(name: str, e_mpa: float, nu: float, *, enabled: bool,
                   yield_mpa: float = 235.0, tangent_mpa: float | None = None,
                   curve_points: list[tuple[float, float]] | None = None,
                   table_end_plastic_strain: float = 0.5) -> tuple[list[str], float]:
    """材料卡生成。enabled 驱动 *PLASTIC 卡的有无（模块59 ③回归保护：
    NonlinearSettings.plasticity_enabled=False 时不得出现 *PLASTIC）。

    *PLASTIC 表（等效应变, 真实应力）：优先 curve_points（PlasticCurve.points
    同序），否则双线性估值表 (σ_y,0)→(σ_y+E_t·ε_end, ε_end)（末段斜率=E_t，
    CalculiX 线性插值即双线性）。返回 (行列表, 实际使用的切线模量)。
    """
    e_mpa = float(e_mpa)
    tangent = float(tangent_mpa) if tangent_mpa is not None else tangent_estimate(e_mpa)
    lines = [f"*MATERIAL, NAME={name}", "*ELASTIC", f"{e_mpa:.12g}, {nu:.12g}"]
    if enabled:
        if curve_points:
            lines.append("*PLASTIC")
            lines += [f"{float(s):.12g}, {float(p):.12g}" for p, s in sorted(curve_points)]
        else:
            lines.append("*PLASTIC")
            lines += [f"{float(yield_mpa):.12g}, 0.",
                      f"{float(yield_mpa) + tangent * table_end_plastic_strain:.12g}, "
                      f"{table_end_plastic_strain:.12g}"]
    return lines, tangent


def _cube_nodes():
    """1×1×1 单元 8 节点（C3D8 角点序：底面逆时针+顶面逆时针）。"""
    coords = [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0),
              (0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1)]
    return coords


def build_single_element_inp(*, yield_mpa: float = 235.0, e_mpa: float = 210000.0,
                             nu: float = 0.3, tangent_mpa: float | None = None,
                             enabled: bool = True, target_stress_factor: float = 1.5,
                             reload_strains: tuple[float, float] = (0.08, 0.10)) -> tuple[str, dict]:
    """算例①：单单元单轴拉至 target_stress_factor×屈服 → 自由卸载 → 再加载两
    级（均超出再屈服点，塑性段）。

    理论（双线性）：塑性段 ε_p=(σ−σ_y)/E_t，ε_total=σ/E+ε_p；自由卸载后
    σ=0、u_resid=ε_p·L（PEEQ 不变）；再加载先弹性至再屈服，其后斜率=E_t——
    割线刚度取再加载两级之间（均在塑性段）。

    关键：卸载步用 *BOUNDARY, OP=NEW 且**不**再约束加载面（自由弹回）——
    若强压 u=0，总应变 0 与残余塑性应變矛盾 → 巨大压应力 → 反向塑性流动
    （PEEQ 翻倍，首跑实测），非物理卸载。
    """
    tangent = float(tangent_mpa) if tangent_mpa is not None else tangent_estimate(e_mpa)
    target_stress = float(target_stress_factor) * float(yield_mpa)
    eps_p_theory = (target_stress - float(yield_mpa)) / tangent
    eps_total_1 = target_stress / e_mpa + eps_p_theory
    u1 = eps_total_1
    u_resid = eps_p_theory  # 自由卸载后的残余位移（σ=0）
    u_a, u_b = (float(v) for v in reload_strains)
    denom = 1.0 + tangent / e_mpa
    # 各向同性硬化：卸载后再屈服发生在当前流动应力（表内 ε_p 累积不改表），
    # 再加载塑性段 σ=[σ_y+E_t·ε]/(1+E_t/E)（ε=总应变；与首压加载同式）。
    sigma_a = (float(yield_mpa) + tangent * u_a) / denom
    sigma_b = (float(yield_mpa) + tangent * u_b) / denom

    coords = _cube_nodes()
    lines = ["*NODE"]
    for i, (x, y, z) in enumerate(coords, start=1):
        lines.append(f"{i}, {x:.12g}, {y:.12g}, {z:.12g}")
    lines += ["*ELEMENT, TYPE=C3D8, ELSET=Eall", "1, 1, 2, 3, 4, 5, 6, 7, 8"]
    mat_lines, tangent_used = material_block("Q235_PLASTIC_EST", e_mpa, nu, enabled=enabled,
                                             yield_mpa=yield_mpa, tangent_mpa=tangent_mpa)
    lines += mat_lines
    lines += ["*SOLID SECTION, ELSET=Eall, MATERIAL=Q235_PLASTIC_EST",
              "** 边界：x=0 面约束 u_x；最小刚体约束（n1 u_y/u_z，n4 u_z，n5 u_y）——侧面自由",
              "*BOUNDARY",
              "1, 1, 1, 0.0", "4, 1, 1, 0.0", "5, 1, 1, 0.0", "8, 1, 1, 0.0",
              "1, 2, 3, 0.0", "4, 3, 3, 0.0", "5, 2, 2, 0.0"]
    # 加载面（x=1）：n2,n3,n6,n7 位移控制
    loaded = [2, 3, 6, 7]
    lines += ["*STEP", "*STATIC", "0.05, 1.0, 1e-05, 0.2",
              f"** 拉至 {target_stress_factor:g}×屈服（应力 {target_stress:.6g} MPa）",
              "*BOUNDARY"]
    for n in loaded:
        lines.append(f"{n}, 1, 1, {u1:.12g}")
    lines += ["*EL PRINT, ELSET=Eall", "S", "PEEQ", "*END STEP",
              "*STEP", "*STATIC", "0.1, 1.0, 1e-06, 0.2",
              "** 自由卸载：OP=NEW 清加载面约束（弹回至残余塑性应变，σ→0）",
              "*BOUNDARY, OP=NEW",
              "1, 1, 1, 0.0", "4, 1, 1, 0.0", "5, 1, 1, 0.0", "8, 1, 1, 0.0",
              "1, 2, 3, 0.0", "4, 3, 3, 0.0", "5, 2, 2, 0.0",
              "*EL PRINT, ELSET=Eall", "S", "PEEQ", "*END STEP"]
    for label, u in (("再加载 a", u_a), ("再加载 b", u_b)):
        lines += [f"*STEP", "*STATIC", "0.1, 1.0, 1e-06, 0.2",
                  f"** {label}：u_x={u:.6g}（塑性段）", "*BOUNDARY"]
        for n in loaded:
            lines.append(f"{n}, 1, 1, {u:.12g}")
        lines += ["*EL PRINT, ELSET=Eall", "S", "PEEQ", "*END STEP"]
    lines.append("")
    theory = {"target_stress_mpa": target_stress, "eps_total_1": eps_total_1,
              "eps_p_theory": eps_p_theory, "u1": u1, "u_resid": u_resid,
              "u_a": u_a, "u_b": u_b,
              "sigma_a_theory_mpa": sigma_a, "sigma_b_theory_mpa": sigma_b,
              "tangent_mpa": tangent_used, "yield_mpa": float(yield_mpa),
              "e_mpa": float(e_mpa)}
    return "\n".join(lines), theory


def build_cantilever_inp(*, yield_mpa: float = 235.0, e_mpa: float = 210000.0,
                         nu: float = 0.3, tangent_mpa: float | None = None,
                         enabled: bool = True, length: float = 100.0, b: float = 10.0,
                         h: float = 10.0, nx: int = 40, ny: int = 4, nz: int = 4,
                         tip_uy: float = -1.5, element_type: str = "C3D8R") -> tuple[str, dict]:
    """算例②：悬臂梁端部 y 向位移加载 → 塑性铰 → 极限载荷对照 P=M_p/L。

    element_type 默认 C3D8R（减缩积分）：C3D8 全积分弯曲剪切锁死实测使
    极限载荷偏高 18.5%（首跑），减缩积分消除锁死。"""
    tangent = float(tangent_mpa) if tangent_mpa is not None else tangent_estimate(e_mpa)
    m_p = float(yield_mpa) * b * h * h / 4.0
    p_limit = m_p / length
    p_first_yield = float(yield_mpa) * b * h * h / 6.0 / length
    dx, dy, dz = length / nx, b / ny, h / nz
    lines = ["*NODE"]
    nid = {}
    counter = 0
    for i in range(nx + 1):
        for j in range(ny + 1):
            for k in range(nz + 1):
                counter += 1
                nid[(i, j, k)] = counter
                lines.append(f"{counter}, {i * dx:.12g}, {j * dy:.12g}, {k * dz:.12g}")
    lines += [f"*ELEMENT, TYPE={element_type}, ELSET=Eall"]
    elem_lines = []
    eid = 0
    for i in range(nx):
        for j in range(ny):
            for k in range(nz):
                eid += 1
                n = [nid[(i, j, k)], nid[(i + 1, j, k)], nid[(i + 1, j + 1, k)], nid[(i, j + 1, k)],
                     nid[(i, j, k + 1)], nid[(i + 1, j, k + 1)], nid[(i + 1, j + 1, k + 1)],
                     nid[(i, j + 1, k + 1)]]
                elem_lines.append(f"{eid}, " + ", ".join(str(v) for v in n))
    lines += elem_lines
    mat_lines, tangent_used = material_block("Q235_PLASTIC_EST", e_mpa, nu, enabled=enabled,
                                             yield_mpa=yield_mpa, tangent_mpa=tangent_mpa)
    lines += mat_lines
    lines += ["*SOLID SECTION, ELSET=Eall, MATERIAL=Q235_PLASTIC_EST",
              "** 固定端（x=0 全约束）；自由端 y 向位移控制（塑性铰在根部）",
              "*BOUNDARY"]
    for j in range(ny + 1):
        for k in range(nz + 1):
            lines.append(f"{nid[(0, j, k)]}, 1, 3, 0.0")
    lines += ["*NSET, NSET=tip"]
    tip_nodes = [nid[(nx, j, k)] for j in range(ny + 1) for k in range(nz + 1)]
    # CalculiX 数据行 ≤16 项——每行 8 项分块（实测 16+ 项单行 *ERROR in splitline）
    for start in range(0, len(tip_nodes), 8):
        lines.append(", ".join(str(v) for v in tip_nodes[start:start + 8]))
    lines += ["*STEP", "*STATIC", "0.05, 1.0, 1e-05, 0.2",
              f"** 自由端 u_y={tip_uy:.6g} mm（位移控制，塑性铰理论 P=M_p/L={p_limit:.6g} N）",
              "*BOUNDARY"]
    for n in tip_nodes:
        lines.append(f"{n}, 2, 2, {tip_uy:.12g}")
    lines += ["*NODE PRINT, NSET=tip", "RF", "*EL PRINT, ELSET=Eall", "S", "PEEQ",
              "*END STEP", ""]
    theory = {"m_p_nmm": m_p, "p_limit_n": p_limit, "p_first_yield_n": p_first_yield,
              "tip_uy_mm": float(tip_uy), "length_mm": float(length), "b_mm": float(b),
              "h_mm": float(h), "tangent_mpa": tangent_used, "yield_mpa": float(yield_mpa),
              "elements": eid, "nodes": counter}
    return "\n".join(lines), theory


def parse_dat_blocks(dat_text: str) -> list[tuple[str, list[list[float]]]]:
    """DAT 的 *EL PRINT/*NODE PRINT 块解析：按出现顺序返回 (标题, 数值行)。"""
    blocks: list[tuple[str, list[list[float]]]] = []
    current: tuple[str, list[list[float]]] | None = None
    for line in dat_text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped[0].isalpha() and "(" in stripped and not stripped[0].isdigit():
            current = (stripped, [])
            blocks.append(current)
            continue
        if current is None:
            continue
        parts = stripped.replace("D", "E").split()
        try:
            values = [float(v) for v in parts]
        except ValueError:
            current = None
            continue
        current[1].append(values)
    return blocks


def blocks_for(blocks, needle: str) -> list[list[list[float]]]:
    """按标题子串取某变量的全部块（顺序=输出顺序，含步内增量）。"""
    return [rows for title, rows in blocks if needle in title]


def step_end_blocks(blocks, needle: str, steps: int = 3) -> list[list[list[float]]]:
    """取某变量各步的**步末**块（*EL PRINT 按增量输出；DAT time=总时间，
    步 k 的步末增量 time≈k——本模块 *STATIC 总时间均为 1.0）。
    按步序返回 [步1, 步2, …] 数据行；任一步步末缺失即返回 []（调用方按
    数量断言失败，不静默取错块）。"""
    import re
    by_step = []
    for s in range(1, steps + 1):
        found = None
        for title, rows in blocks:
            if needle not in title:
                continue
            m = re.search(r"time\s+([0-9.E+-]+)", title)
            if m and abs(float(m.group(1)) - float(s)) <= 1e-9:
                found = rows
        if found is None:
            return []
        by_step.append(found)
    return by_step


def stress_first_component(block_rows) -> list[float]:
    """S 块 → 每积分点 S11（单轴状态各点相同，取列表）。"""
    return [row[2] for row in block_rows if len(row) >= 8]


def peeq_values(block_rows) -> list[float]:
    """PEEQ 块 → 每积分点等效塑性应变。"""
    return [row[2] for row in block_rows if len(row) >= 3]


def rf_y_sum(block_rows) -> float:
    """RF 块 → ΣF_y。"""
    return float(sum(row[2] for row in block_rows if len(row) >= 3))


PLASTIC_REPORT_NOTE = "塑性参数未经材料试验标定（切线模量取常用估值 E/100）；损伤分析未完成（DamageSettings 本轮不实现）"


def write_plasticity_report(directory: Path, *, case1: dict, case2: dict,
                            negative_ok: bool) -> Path:
    """最小算例报告（④：『塑性参数未经材料试验标定』『损伤分析未完成』如实标注）。"""
    d = Path(directory)
    d.mkdir(parents=True, exist_ok=True)
    lines = [
        "# 塑性变形最小可验证算例报告", "",
        "## 完成度红线（如实标注）", "",
        f"- **塑性参数未经材料试验标定**：切线模量取常用估值 E/100"
        f"（Q235：E={case1['tangent_mpa'] * 100:.0f} MPa → E_t={case1['tangent_mpa']:.0f} MPa），"
        "双线性模型仅作量级演示，不能用于寿命/成形评估。",
        "- **损伤分析未完成**：DamageSettings 本轮不实现，未生成任何损伤卡/损伤变量输出。",
        "- 几何线性（小应变）口径；未启用 NLGEOM。",
        "", "## 算例①：单单元单轴加载-卸载-再加载", "",
        f"- 目标应力 {case1['target_stress_mpa']:.6g} MPa（=1.5×屈服 {case1['yield_mpa']:.6g} MPa）；"
        f"理论 ε_p=ε_total−σ/E={case1['eps_p_theory']:.9g}。",
        f"- 卸载后 PEEQ（FEM）={case1['peeq_after_unload']:.9g}（偏差 {case1['peeq_dev']:.3%}，门 ±2%）。",
        f"- 再加载塑性段割线刚度 {case1['chord_stiffness']:.9g} MPa vs 切线模量 "
        f"{case1['tangent_mpa']:.9g} MPa（偏差 {case1['chord_dev']:.3%}，门 ±5%）。",
        "", "## 算例②：悬臂梁塑性铰", "",
        f"- 截面 {case2['b_mm']:.0f}×{case2['h_mm']:.0f} mm，L={case2['length_mm']:.0f} mm；"
        f"M_p=σ_y·b·h²/4={case2['m_p_nmm']:.6g} N·mm；理论极限载荷 P=M_p/L={case2['p_limit_n']:.6g} N。",
        f"- 端部位移 {case2['tip_uy_mm']:.6g} mm 下 FEM 反力 P={case2['p_fem_n']:.6g} N"
        f"（偏差 {case2['p_dev']:.3%}，门 ≤10%）。",
        f"- 根部 PEEQ>0（塑性铰证据：max PEEQ={case2['max_peeq']:.6g}）。",
        "", "## 负例保护", "",
        f"- NonlinearSettings.plasticity_enabled=False → INP 不含 *PLASTIC 卡：{'通过' if negative_ok else '失败'}。",
        "", "原始 INP/DAT 与 verification.json 在本证据目录。",
    ]
    path = d / "plasticity_minimal_report.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
