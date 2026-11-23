# -*- coding: utf-8 -*-
"""rm_sim —— RoboMaster 零件静力分析软件主包。

分层与 GUI 硬约束（详见 app/ARCHITECTURE.md）：
- ``rm_sim.core`` / ``rm_sim.mesh`` / ``rm_sim.solver`` / ``rm_sim.report``
  以及 ``rm_sim.config`` / ``rm_sim.layer_guard``：GUI 无关层，
  禁止 import PySide6 / pyvista / pyvistaqt / vtk（由 layer_guard 静态扫描 +
  verify/verify_main_entry.py 的毒化子进程双重强制）。
- ``rm_sim.gui``：唯一允许 import PySide6 的层。

GUI 无关层可在无显示环境进程内独立运行，这是 F11 三进程验证流程的前提。
"""

__version__ = "0.1.0"
