# -*- coding: utf-8 -*-
"""GUI 无关层守卫（GUI 无关层，仅用 stdlib ast）。

静态扫描 core/mesh/solver/report 及包根 GUI 无关模块，禁止出现：
- PySide6 / PyQt5 / PyQt6 / pyvista / pyvistaqt / vtk 等任何 GUI/GL 导入；
- 对 rm_sim.gui 的导入（含相对导入）；
- matplotlib 的 Qt 后端（report 层只允许 Agg）。

本守卫在 --selfcheck 与 verify/verify_main_entry.py 中都会执行；
运行时级别的强制由 verify 的毒化子进程（sys.modules 注入 None）覆盖。
"""
from __future__ import annotations

import ast
from pathlib import Path

FORBIDDEN_PREFIXES = ("PySide6", "PyQt5", "PyQt6", "pyvista", "vtk")
GUI_FREE_SUBDIRS = ("core", "mesh", "solver", "report", "cad")
GUI_FREE_ROOT_FILES = (
    "__init__.py",
    "config.py",
    "layer_guard.py",
    "gmsh_runtime.py",
    "assembly_demo.py",  # 模块 13：装配演示工程构建（--selfcheck/验收共用）
    "batch.py",
)


def scan_forbidden_gui_imports(package_dir: str | Path) -> list[str]:
    """返回违规描述列表；空列表 = 通过。"""
    pkg = Path(package_dir)
    targets: list[Path] = []
    for sub in GUI_FREE_SUBDIRS:
        d = pkg / sub
        if d.is_dir():
            targets.extend(sorted(d.rglob("*.py")))
    for name in GUI_FREE_ROOT_FILES:
        f = pkg / name
        if f.is_file():
            targets.append(f)

    violations: list[str] = []
    for f in targets:
        rel = f.relative_to(pkg).as_posix()
        try:
            tree = ast.parse(f.read_text(encoding="utf-8"))
        except (OSError, SyntaxError) as exc:
            violations.append(f"{rel}: 源码读取/解析失败: {type(exc).__name__}: {exc}")
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    violations.extend(_check_top(rel, node.lineno, alias.name))
            elif isinstance(node, ast.ImportFrom):
                if node.level == 0 and node.module:
                    violations.extend(_check_top(rel, node.lineno, node.module))
                    if node.module == "rm_sim.gui" or node.module.startswith("rm_sim.gui."):
                        violations.append(f"{rel}:{node.lineno} 禁止导入 GUI 层: {node.module}")
                    if node.module.startswith("matplotlib") and "qt" in node.module.lower():
                        violations.append(f"{rel}:{node.lineno} 报告层禁止 Qt 后端: {node.module}")
                elif node.level > 0:
                    base = (node.module or "").split(".")[0]
                    imported = [a.name for a in node.names]
                    if base == "gui" or (not node.module and "gui" in imported):
                        violations.append(f"{rel}:{node.lineno} 禁止相对导入 GUI 层")
    return violations


def _check_top(rel: str, lineno: int, module: str) -> list[str]:
    top = module.split(".")[0]
    if top.startswith(FORBIDDEN_PREFIXES):
        return [f"{rel}:{lineno} 禁止导入 {module}"]
    return []
