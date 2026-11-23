# -*- coding: utf-8 -*-
"""装配演示工程构建（GUI 无关顶层模块；--selfcheck 与验收共用）。

从装配样例 STEP 构建一个 2 部件的演示工程（部件/材料引用/可见性齐全），
用于 ``main.py --selfcheck`` 的装配树打印与验收脚本。本模块只依赖
core/cad 层（gmsh+numpy），是 GUI 无关层顶层文件——已登记
``layer_guard.GUI_FREE_ROOT_FILES``（静态扫描覆盖）。
"""
from __future__ import annotations

from pathlib import Path

from .cad.assembly_import import AssemblyModel, import_assembly, sync_parts_from_model
from .core.project import Project, assembly_tree_payload


def build_demo_assembly_project(step_path: str | Path) -> tuple[Project, AssemblyModel]:
    """构建 2 部件演示工程：p1=Q235、p2=6061（均可见），几何指纹来自真实导入。

    返回 (project, model)；model 持有 gmsh 引用槽，调用方负责 close()。
    """
    model = import_assembly(step_path)
    project = Project(name="装配演示工程（two_blocks）")
    project.study.geometry_file = str(step_path)
    parts = sync_parts_from_model(project, model)
    parts[0].material_name = "Q235"
    parts[1].material_name = "6061"
    for p in parts:
        p.validate()
    return project, model


__all__ = ["AssemblyModel", "assembly_tree_payload", "build_demo_assembly_project"]
