# -*- coding: utf-8 -*-
"""ccx2paraview 桥（GUI 无关层，solver 层）：.frd → .vtu → meshio 逐场读取。

monkeypatch（probes/probe3_post.py 实证）：ccx2paraview 3.2.0 的
common.calculate_principal 调用 sorted(np.linalg.eigvals(tensor))，在
numpy 2.5.3 下遇到复数特征值即 TypeError——补丁在虚部可忽略时返回实部。

ccx2paraview.Converter 以**文件基名**工作、产物写在进程 CWD——本桥用
chdir 守卫临时切到 .frd 所在目录（os.chdir 为进程级状态，加锁串行化；
Popen 的 cwd 参数不受影响）。
"""
from __future__ import annotations

import os
import threading
from pathlib import Path

import numpy as np

_chdir_lock = threading.Lock()
_patched = False


def apply_numpy_eigvals_patch() -> None:
    """probe3 配方：np.linalg.eigvals 虚部可忽略时返回实部（幂等）。"""
    global _patched
    if _patched:
        return
    import numpy as _np

    orig = _np.linalg.eigvals

    def _eigvals_real(a):  # noqa: ANN001
        w = orig(a)
        if _np.iscomplexobj(w) and _np.all(
            _np.abs(w.imag) <= 1e-8 * (1.0 + _np.abs(w.real))
        ):
            return w.real
        return w

    _np.linalg.eigvals = _eigvals_real
    _patched = True


def frd_to_vtu(frd_path: str | Path) -> Path:
    """把 ccx 的 .frd 转成 .vtu（ccx2paraview 3.2.0），返回 .vtu 路径。"""
    frd = Path(frd_path)
    if not frd.is_file():
        raise RuntimeError(f".frd 文件不存在: {frd}")
    apply_numpy_eigvals_patch()
    from ccx2paraview.common import Converter  # patch 之后才能导入

    with _chdir_lock:
        prev = os.getcwd()
        os.chdir(str(frd.parent))  # Converter 按基名工作、产物写 CWD
        try:
            Converter(frd.name, ["vtu"]).run()
        finally:
            os.chdir(prev)
    vtu = frd.with_suffix(".vtu")
    if not vtu.is_file():
        # 多增量 .frd（非线性步，如接触——模块 18 实证）：ccx2paraview 按增量
        # 写 {base}.{inc}.vtu；取**最后一个增量**（=终态）。
        incs = sorted(
            (p for p in frd.parent.glob(f"{frd.stem}.*.vtu")
             if p.stem.rsplit(".", 1)[-1].isdigit()),
            key=lambda p: int(p.stem.rsplit(".", 1)[-1]),
        )
        if incs:
            vtu = incs[-1]
    if not vtu.is_file():
        raise RuntimeError(f"ccx2paraview 未产出 .vtu: {frd.with_suffix('.vtu')}")
    return vtu


def read_vtu_point_data(vtu_path: str | Path) -> dict[str, np.ndarray]:
    """meshio 读 .vtu 的 point_data（键名做 ccx2paraview 别名归一）。"""
    import meshio

    m = meshio.read(str(vtu_path))
    data = {k: np.asarray(v, dtype=float) for k, v in m.point_data.items()}

    def pick(*names: str) -> np.ndarray | None:
        for n in names:
            for k, v in data.items():
                if k.upper() == n:
                    return v
        return None

    out: dict[str, np.ndarray] = {}
    u = pick("DISP", "U")
    s = pick("STRESS", "S")
    mises = pick("S_MISES")
    forc = pick("FORC", "RF")
    if u is not None:
        out["U"] = u
    if s is not None:
        out["S"] = s
    if mises is not None:
        out["S_Mises"] = mises
    if forc is not None:
        out["FORC"] = forc
    if not out:
        raise RuntimeError(f".vtu 中没有可识别的场（point_data keys={list(data)}）")
    return out


def mises_from_components(s6: np.ndarray) -> np.ndarray:
    """由 6 分量应力 (sxx,syy,szz,sxy,syz,szx) 行数组计算 von Mises（校核用）。"""
    s = np.asarray(s6, dtype=float)
    sx, sy, sz = s[:, 0], s[:, 1], s[:, 2]
    txy, tyz, tzx = s[:, 3], s[:, 4], s[:, 5]
    return np.sqrt(
        0.5 * ((sx - sy) ** 2 + (sy - sz) ** 2 + (sz - sx) ** 2)
        + 3.0 * (txy**2 + tyz**2 + tzx**2)
    )
