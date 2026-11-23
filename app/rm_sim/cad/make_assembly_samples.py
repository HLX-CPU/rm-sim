# -*- coding: utf-8 -*-
"""生成装配样例 STEP（内部单位 N-mm-MPa-t-s，几何一律 mm）。

用法：E:/rm_sim/venv/Scripts/python.exe -m rm_sim.cad.make_assembly_samples [输出目录]
默认输出 <仓库根>/samples/。

样例（几何规格首次实现时固化；「螺栓与销轴」模块可在此调整重生成）：

1. assembly_two_blocks.step — 两叠块 20×20×20：
   下块 bbox (0,0,0)-(20,20,20)（质心 (10,10,10)），
   上块 bbox (0,20,0)-(20,40,20)（质心 (10,30,10)），
   沿 +y 叠放贴合 → 上块质心 − 下块质心 = (0,20,0)。
2. assembly_lap_pin.step — 搭接双板 + 通孔 + 销（3 部件）：
   - 下板  40×30×8：x∈[0,40], y∈[0,30], z∈[0,8]
   - 上板  40×30×8：x∈[20,60], y∈[0,30], z∈[8,16]（搭接区 x∈[20,40]）
   - 通孔  轴 z、中心 (30,15)、直径 10，贯穿 z∈[0,16]（双板）
   - 销    圆柱 中心 (30,15)、半径 5、z∈[0,16]（独立实体，与孔等径贴合）
   体积：每板 40·30·8 − π·5²·8 = 8971.681469…mm³；销 π·5²·16 = 1256.637061…mm³。

文件保证以 SI_UNIT(.MILLI.,.METRE.)（mm）声明：gmsh 实测固定写 mm 声明，
若未来版本改变则在此处补正，确保文件语义与内部单位一致。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import gmsh

from .. import gmsh_runtime

MM_DECL = "SI_UNIT(.MILLI.,.METRE.)"
_ANY_METRE_DECL = re.compile(r"SI_UNIT\s*\(\s*(?:\.MILLI\.\s*,\s*\.METRE\.|\$\s*,\s*\$\s*,\s*\.METRE\.)\s*\)")


def _write_mm_step(out: Path, build_shapes) -> Path:
    """建几何 → 写 STEP → 单位声明补正为 mm。"""
    out.parent.mkdir(parents=True, exist_ok=True)
    with gmsh_runtime.session():
        gmsh.model.add(f"rm_sim_{out.stem}")
        build_shapes()
        gmsh.model.occ.synchronize()
        gmsh.write(str(out))
        gmsh.model.remove()
    text = out.read_text(encoding="utf-8", errors="replace")
    patched = _ANY_METRE_DECL.sub(MM_DECL, text)
    if patched != text:
        out.write_text(patched, encoding="utf-8")
    return out


def make_two_blocks_step(out_path: str | Path, size: float = 20.0) -> Path:
    """两叠块 20×20×20（下块 y∈[0,20]，上块 y∈[20,40]，沿 +y 叠放）。"""
    s = float(size)

    def build() -> None:
        gmsh.model.occ.addBox(0.0, 0.0, 0.0, s, s, s)  # 下块
        gmsh.model.occ.addBox(0.0, s, 0.0, s, s, s)  # 上块

    return _write_mm_step(Path(out_path), build)


def make_lap_pin_step(out_path: str | Path) -> Path:
    """搭接双板 + 通孔 + 销（3 部件，几何规格见模块 docstring）。"""
    plate_a = (0.0, 0.0, 0.0, 40.0, 30.0, 8.0)  # x0,y0,z0,dx,dy,dz
    plate_b = (20.0, 0.0, 8.0, 40.0, 30.0, 8.0)
    hole_cx, hole_cy, hole_r, z_top = 30.0, 15.0, 5.0, 16.0

    def build() -> None:
        occ = gmsh.model.occ
        a = occ.addBox(*plate_a)
        b = occ.addBox(*plate_b)
        # 通孔（各板自切， cutter 轴向加长避免共面布尔歧义）
        hole_a = occ.addCylinder(hole_cx, hole_cy, -1.0, 0.0, 0.0, plate_a[5] + 2.0, hole_r)
        hole_b = occ.addCylinder(hole_cx, hole_cy, plate_b[2] - 1.0, 0.0, 0.0, plate_b[5] + 2.0, hole_r)
        occ.cut([(3, a)], [(3, hole_a)])
        occ.cut([(3, b)], [(3, hole_b)])
        # 销：独立实体，与孔等径贴合（z 0..16，恰好贯穿双板）
        occ.addCylinder(hole_cx, hole_cy, 0.0, 0.0, 0.0, z_top, hole_r)

    return _write_mm_step(Path(out_path), build)


def make_serial_rods_step(out_path: str | Path) -> Path:
    """串联双杆（粘接连接验证算例，模块 17）：杆1 钢 x∈[0,100]、杆2 铝 x∈[100,200]，
    各 10×10×100mm，x=100 界面粘接；固定 x=0、自由端 +x 拉 1000N →
    δ=FL/(E1A)+FL/(E2A)=0.0190476mm（材料在验证脚本/工程侧按部件指定）。"""

    def build() -> None:
        gmsh.model.occ.addBox(0.0, 0.0, 0.0, 100.0, 10.0, 10.0)  # 杆1
        gmsh.model.occ.addBox(100.0, 0.0, 0.0, 100.0, 10.0, 10.0)  # 杆2

    return _write_mm_step(Path(out_path), build)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    here = Path(__file__).resolve()
    root = here.parents[3]  # app/rm_sim/cad/make_assembly_samples.py → 仓库根
    out_dir = Path(argv[0]) if argv else root / "samples"
    paths = [
        make_two_blocks_step(out_dir / "assembly_two_blocks.step"),
        make_lap_pin_step(out_dir / "assembly_lap_pin.step"),
        make_serial_rods_step(out_dir / "assembly_serial_rods.step"),
    ]
    for p in paths:
        decl = sorted(set(_ANY_METRE_DECL.findall(p.read_text(encoding="utf-8", errors="replace"))))
        print(f"样例已生成: {p}")
        print(f"  长度单位声明: {decl or '（SI_UNIT(.MILLI.,.METRE.)）'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
