# -*- coding: utf-8 -*-
"""整装连接草稿的后台作业（隔离 OCC 运行时，GUI 进程不开 gmsh 模型）。

与 inspection_job 同一套隔离理由：OCC 布尔/求距离很慢且可能触发 gmsh 全局
状态异常，放进独立进程后 GUI 可以取消、可以看日志、失败也不会带走界面。

spec.json 字段：
- step：装配 STEP 绝对路径；
- output：结果 JSON 输出路径（必须不存在，open('x') 防覆盖已有证据）；
- part_names：可选 {solid tag 字符串: 显示名}，让草稿里的部件名与 GUI 一致；
- planar_only / min_area_mm2 / max_patches_per_pair：透传给 draft_assembly_connections。

结束时打印 DRAFT_COMPLETED: completed|incomplete，供 GUI 侧判断。
"""
from pathlib import Path
from dataclasses import replace
import hashlib
import json
import sys

from ..core.project import Project
from .assembly_import import import_assembly, sync_parts_from_model
from .connection_draft import draft_assembly_connections


def _labelled_project(model, labels):
    """按模型实体建最小工程（part_id 与 GUI 的 sync_parts_from_model 一致）。"""
    project = Project(name=Path(model.path).stem, project_dir=str(Path(model.path).parent))
    sync_parts_from_model(project, model)
    if labels:
        solids = list(model.solids)
        for part in project.parts:
            index = int(getattr(part, "solid_index", -1))
            if 0 <= index < len(solids):
                name = labels.get(str(int(solids[index].tag)))
                if name:
                    part.name = str(name)
    return project


def run(spec_path):
    spec = json.loads(Path(spec_path).read_text(encoding="utf-8"))
    source = Path(spec["step"])
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    model = import_assembly(source)
    try:
        project = _labelled_project(model, spec.get("part_names") or {})
        print(f"Drafting connections for {len(project.parts)} parts", flush=True)
        draft = draft_assembly_connections(
            model,
            project,
            planar_only=bool(spec.get("planar_only", False)),
            min_area_mm2=float(spec.get("min_area_mm2", 0.05)),
            max_patches_per_pair=int(spec.get("max_patches_per_pair", 64)),
        )
        if hashlib.sha256(source.read_bytes()).hexdigest() != before:
            raise ValueError("STEP changed during connection draft")
        status = "incomplete" if (draft["geometry_errors"] or draft["patch_errors"]) else "completed"
        result = dict(draft)
        result.update({
            "step": str(source),
            "step_sha256": before,
            "part_names": {str(int(s.tag)): (getattr(p, "name", "") or p.part_id)
                           for s, p in zip(model.solids, project.parts)},
            "status": status,
        })
        output = Path(spec["output"])
        with output.open("x", encoding="utf-8") as stream:
            json.dump(result, stream, ensure_ascii=False, indent=2)
        print(f"DRAFT_COMPLETED: {status}", flush=True)
        return result
    finally:
        model.close()


if __name__ == "__main__":
    run(sys.argv[1])
