# -*- coding: utf-8 -*-
"""cad 层（GUI 无关）：STEP 导入、单位三检、几何查询与选面接口。"""
from .assembly_import import AssemblyModel, PartInfo, import_assembly
from .step_import import (
    FaceInfo,
    FacePick,
    NotSolidError,
    PickError,
    SolidInfo,
    SolidModel,
    StepImportError,
    import_step,
)

__all__ = [
    "AssemblyModel",
    "FaceInfo",
    "FacePick",
    "NotSolidError",
    "PartInfo",
    "PickError",
    "SolidInfo",
    "SolidModel",
    "StepImportError",
    "import_assembly",
    "import_step",
]
