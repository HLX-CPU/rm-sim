# -*- coding: utf-8 -*-
"""生成悬臂梁标准样例 STEP（内部单位 N-mm-MPa-t-s，几何 100×10×10 mm）。

用法：E:/rm_sim/venv/Scripts/python.exe -m rm_sim.cad.make_beam_sample [输出路径]
默认输出 <仓库根>/samples/beam_100x10x10.step。

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


def make_beam_step(out_path: str | Path, length: float = 100.0, width: float = 10.0, height: float = 10.0) -> Path:
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with gmsh_runtime.session():
        gmsh.model.add("rm_sim_beam_sample")
        gmsh.model.occ.addBox(0.0, 0.0, 0.0, float(length), float(width), float(height))
        gmsh.model.occ.synchronize()
        gmsh.write(str(out))
        gmsh.model.remove()
    text = out.read_text(encoding="utf-8", errors="replace")
    patched = _ANY_METRE_DECL.sub(MM_DECL, text)
    if patched != text:
        out.write_text(patched, encoding="utf-8")
    return out


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    here = Path(__file__).resolve()
    root = here.parents[3]  # app/rm_sim/cad/make_beam_sample.py → 仓库根
    out = Path(argv[0]) if argv else root / "samples" / "beam_100x10x10.step"
    p = make_beam_step(out)
    decl = sorted(set(_ANY_METRE_DECL.findall(p.read_text(encoding="utf-8", errors="replace"))))
    print(f"样例已生成: {p}")
    print(f"长度单位声明: {decl or '（SI_UNIT(.MILLI.,.METRE.)）'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
