# -*- coding: utf-8 -*-
"""STEP 文件长度单位解析（GUI 无关层）。

解析 ISO-10303 文本中的长度单位声明：
- ``SI_UNIT(.MILLI.,.METRE.)`` 等带词头形式；
- ``SI_UNIT($,$,.METRE.)`` 无词头形式（= 米）；
- ``CONVERSION_BASED_UNIT('INCH',#n)`` 换算基准单位，按其
  ``LENGTH_MEASURE_WITH_UNIT(LENGTH_MEASURE(25.4),#ref)`` 引用图解析因子。

角度（RADIAN）/立体角（STERADIAN）/质量（GRAM）等非长度单位一律跳过。
factor_to_mm 是把「文件中的数值」换算成毫米的乘数：
METRE→1000，MILLI.METRE→1，INCH→25.4（内部单位体系固定 N-mm-MPa-t-s）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

PREFIX_TO_FACTOR = {
    "MICRO": 1e-6,
    "MILLI": 1e-3,
    "CENTI": 1e-2,
    "DECI": 1e-1,
    "DECA": 1e1,
    "HECTO": 1e2,
    "KILO": 1e3,
}
PREFIX_TO_NAME = {
    "MICRO": "µm",
    "MILLI": "mm",
    "CENTI": "cm",
    "DECI": "dm",
    "DECA": "dam",
    "HECTO": "hm",
    "KILO": "km",
}


class UnitParseError(ValueError):
    """STEP 单位声明无法解析或无法识别。"""


@dataclass(frozen=True)
class LengthUnit:
    """文件数值 → 毫米 的长度单位语义。"""

    name: str  # 展示名：mm / m / in / cm ...
    factor_to_mm: float
    declared: str  # 命中的原文片段
    kind: str  # "si" | "conversion" | "assumed"


_SI_PREFIXED = re.compile(r"SI_UNIT\s*\(\s*\.([A-Za-z]+)\.\s*,\s*\.([A-Za-z]+)\.\s*\)")
_SI_BARE = re.compile(r"SI_UNIT\s*\(\s*\$\s*,\s*\$\s*,\s*\.([A-Za-z]+)\.\s*\)")
_CBU = re.compile(r"CONVERSION_BASED_UNIT\s*\(\s*'([^']*)'\s*,\s*#(\d+)\s*\)")
_LMWU = re.compile(
    r"LENGTH_MEASURE_WITH_UNIT\s*\(\s*LENGTH_MEASURE\s*\(\s*([-+0-9.Ee]+)\s*\)\s*,\s*#(\d+)\s*\)"
)
_ENTITY = re.compile(r"#(\d+)\s*=\s*([^;]*);")


def parse_length_units(text: str) -> list[LengthUnit]:
    """返回文件中全部互不重复的长度单位声明（可能多个，调用方负责裁决）。"""
    units: list[LengthUnit] = []

    for m in _SI_PREFIXED.finditer(text):
        prefix, name = m.group(1).upper(), m.group(2).upper()
        if name != "METRE":  # RADIAN / STERADIAN / GRAM 等非长度单位跳过
            continue
        factor = PREFIX_TO_FACTOR.get(prefix)
        if factor is None:
            raise UnitParseError(f"无法识别的 SI 词头: .{prefix}.（原文: {m.group(0)}）")
        units.append(LengthUnit(PREFIX_TO_NAME[prefix], factor * 1000.0, m.group(0), "si"))

    for m in _SI_BARE.finditer(text):
        name = m.group(1).upper()
        if name != "METRE":
            continue
        units.append(LengthUnit("m", 1000.0, m.group(0), "si"))

    # 换算基准单位（INCH 等）：沿 #引用 找 LENGTH_MEASURE_WITH_UNIT 的数值与其基准 SI 单位
    entities: dict[int, str] = {
        int(m.group(1)): m.group(2) for m in _ENTITY.finditer(text)
    }
    for m in _CBU.finditer(text):
        cname, ref = m.group(1).strip(), int(m.group(2))
        def_line = entities.get(ref, "")
        lm = _LMWU.search(def_line)
        if not lm:
            raise UnitParseError(
                f"CONVERSION_BASED_UNIT '{cname}' 未找到 LENGTH_MEASURE_WITH_UNIT 定义"
                f"（引用 #{ref}）"
            )
        value = float(lm.group(1))
        si_line = entities.get(int(lm.group(2)), "")
        base_to_mm: float | None = None
        for sm in _SI_PREFIXED.finditer(si_line):
            if sm.group(2).upper() == "METRE":
                p = sm.group(1).upper()
                if p not in PREFIX_TO_FACTOR:
                    raise UnitParseError(f"无法识别的 SI 词头: .{p}.（{cname} 的基准单位）")
                base_to_mm = PREFIX_TO_FACTOR[p] * 1000.0
        for sm in _SI_BARE.finditer(si_line):
            if sm.group(1).upper() == "METRE":
                base_to_mm = 1000.0
        if base_to_mm is None:
            raise UnitParseError(f"CONVERSION_BASED_UNIT '{cname}' 的基准不是长度单位")
        units.append(LengthUnit(cname.lower(), value * base_to_mm, m.group(0), "conversion"))

    # 去重：同名同因子只保留一份
    uniq: list[LengthUnit] = []
    for u in units:
        if not any(
            u.name == v.name and abs(u.factor_to_mm - v.factor_to_mm) < 1e-9 * max(1.0, v.factor_to_mm)
            for v in uniq
        ):
            uniq.append(u)
    return uniq


def detect_source_unit(text: str) -> LengthUnit:
    """取文件的唯一长度单位；缺失则显式按 mm 假设（kind=assumed），矛盾则报错。"""
    units = parse_length_units(text)
    if not units:
        return LengthUnit("mm", 1.0, "", "assumed")
    factors = {round(u.factor_to_mm, 9) for u in units}
    if len(factors) > 1:
        raise UnitParseError(
            "文件含多种相互矛盾的长度单位声明: "
            + ", ".join(f"{u.name}(×{u.factor_to_mm:g})" for u in units)
        )
    return units[0]
