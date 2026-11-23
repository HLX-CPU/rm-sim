# -*- coding: utf-8 -*-
"""solver 层（GUI 无关）：约束/载荷语义转换（convert）、分析 .inp 生成（deck）、
结果解析（results）、CalculiX 任务调度（tasks）。全程不 import PySide6。"""
from .convert import (
    ConversionError,
    PressureDLoadRef,
    gravity_vector,
    moment_to_nodal_couples,
    pressure_to_face_set,
    traction_to_cload,
)
from .deck import DeckError, build_case_inp
from .results import ResultsParseError, SolveResults, load_results
from .tasks import SolveScheduler, SolveTask, SchedulerError

__all__ = [
    "ConversionError",
    "DeckError",
    "PressureDLoadRef",
    "ResultsParseError",
    "SchedulerError",
    "SolveResults",
    "SolveScheduler",
    "SolveTask",
    "build_case_inp",
    "gravity_vector",
    "load_results",
    "moment_to_nodal_couples",
    "pressure_to_face_set",
    "traction_to_cload",
]
