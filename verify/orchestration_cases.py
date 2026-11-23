# -*- coding: utf-8 -*-
"""回归编排自检共享件：整装归档 case 与后台工作流完成记录的定位与最小现做。

背景（模块45）：verify_full_assembly_solve.py 与 verify_workflow_bundle_relocation.py
原本是带参独立复核工具（基线回归无参调用失败，归类「编排无参调用」）。修复给两者
增加无参自检模式——经本模块「自动定位最近归档；无归档时现场跑一次最小两块装配求解」。
带参模式不经过本模块，行为不变。

定位定义（只读，不启动任何进程）：
- 整装归档 case：case 目录含 <job>.inp/.dat/.frd/.log/.vtu 全部非空、日志含
  'Job finished'、INP 含 *DLOAD GRAV（自重工况）且含 ELSET=PART<n>（装配 deck）；
  取 .frd 修改时间最新者。排除 orchestration_fixture 标记目录（那是 ③ 带参对照
  用的固定样例，不是「最近整装归档」）。
- 后台工作流完成记录：E:/rm_sim/verification/*/verification.json 含 'complete_work'，
  指向的任务目录有 completed.json，且其中 project_dir 存在并有 cases/；取最新。

现做（会启动真实 ccx / 后台工作流进程——只应在无归档可用时发生）：
- 最小两块装配（自重）——直接 prepare_analysis + SolveScheduler，供
  full_assembly_solve 复核；
- 最小两块装配（后台工作流，固定+面力）——WorkflowScheduler 产出 completed.json
  完成记录，供 bundle_relocation 迁移复核。
"""
from __future__ import annotations

import datetime
import json
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

# 证据根目录；RM_SIM_NOARG_EVIDENCE_ROOT 仅供验收脚本演练「无归档→现做」回退路径
# （指向空目录即可让定位必然未命中，从而真实走一次最小求解回退）。
EVIDENCE_ROOT = Path(os.environ.get("RM_SIM_NOARG_EVIDENCE_ROOT", "E:/rm_sim/verification"))
FIXTURE_MARKER = "orchestration_fixture"  # ③ 带参对照固定样例的目录名标记


def new_evidence_dir(prefix: str) -> Path:
    """项目统一证据目录约定：E:/rm_sim/verification/<prefix>_<时间戳>。"""
    path = EVIDENCE_ROOT / (prefix + "_" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f"))
    path.mkdir(parents=True, exist_ok=False)
    return path


# ---------------------------------------------------------------- 定位（只读）
def _case_candidates(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    found = []
    for case in root.glob("**/case_*"):
        try:
            if case.is_dir() and case.parent.name == "cases":
                found.append(case)
        except OSError:
            continue
    return found


def _job_of_case(case: Path) -> tuple[str, Path] | None:
    """case 目录里能构成完整整装 deck 产物的 job 名；无则 None。"""
    try:
        inps = [p for p in case.glob("*.inp") if p.is_file() and p.stat().st_size > 0]
    except OSError:
        return None
    for inp in sorted(inps):
        job = inp.stem
        if job in ("mesh", "deck"):
            continue  # 网格段/中间产物不是完整 deck
        needed = [case / f"{job}.{ext}" for ext in ("dat", "frd", "log", "vtu")]
        try:
            if not all(p.is_file() and p.stat().st_size > 0 for p in needed):
                continue
            if "Job finished" not in (case / f"{job}.log").read_text(encoding="utf-8", errors="replace"):
                continue
            text = inp.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "*DLOAD" not in text.upper() or "GRAV" not in text.upper():
            continue  # 复核工具以自重载荷积分口径为锚，需 GRAV 工况
        if not re.search(r"ELSET\s*=\s*PART\d+", text):
            continue  # 只认装配 deck（部件 ELSET）
        return job, inp
    return None


def find_latest_assembly_case(roots: tuple[Path, ...] = (EVIDENCE_ROOT,)) -> tuple[Path, str] | None:
    """最近整装归档 case → (case_dir, job)；找不到返回 None。只读。"""
    best: tuple[float, Path, str] | None = None
    for root in roots:
        for case in _case_candidates(root):
            if FIXTURE_MARKER in str(case):
                continue  # ③ 固定对照样例不算「最近整装归档」
            got = _job_of_case(case)
            if got is None:
                continue
            job, _inp = got
            try:
                mtime = (case / f"{job}.frd").stat().st_mtime
            except OSError:
                continue
            if best is None or mtime > best[0]:
                best = (mtime, case, job)
    if best is None:
        return None
    return best[1], best[2]


def find_latest_workflow_record(roots: tuple[Path, ...] = (EVIDENCE_ROOT,)) -> Path | None:
    """最近可用的后台工作流完成记录 JSON → 路径；找不到返回 None。只读。"""
    best: tuple[float, Path] | None = None
    for root in roots:
        if not root.is_dir():
            continue
        for record in root.glob("*/verification.json"):
            if FIXTURE_MARKER in str(record):
                continue
            try:
                checks = json.loads(record.read_text(encoding="utf-8"))
                work = Path(checks["complete_work"])
                complete = json.loads((work / "completed.json").read_text(encoding="utf-8"))
                old = Path(complete["project"]["project_dir"])
                if not (work / "completed.json").is_file():
                    continue
                if not (old / "cases").is_dir():
                    continue
            except (OSError, ValueError, KeyError, TypeError):
                continue  # 记录缺项/旧目录不在：如实跳过，不硬凑
            try:
                mtime = record.stat().st_mtime
            except OSError:
                continue
            if best is None or mtime > best[0]:
                best = (mtime, record)
    return best[1] if best else None


# ---------------------------------------------------------------- 现做（真实求解）
def ensure_minimal_assembly_case(fixture_root: Path, *, force: bool = False) -> tuple[Path, str, bool]:
    """最小两块装配自重求解 → (case_dir, job, solved_now)。

    fixture_root 固定时幂等：已有完整产物则直接复用（供 ③ 带参对照的稳定样例）。
    """
    work = fixture_root / "build"
    cases_root = work / "cases"  # 红线①：调度 work_root 必须为 project_dir/cases
    if not force and cases_root.is_dir():
        for existing in sorted(cases_root.glob("case_*")):
            got = _job_of_case(existing)
            if got and got[0] == "assembly_full":
                return existing, "assembly_full", False

    from rm_sim.cad.assembly_import import import_assembly, sync_parts_from_model
    from rm_sim.config import get_ccx_path, load_config
    from rm_sim.core.connections import BondedConnection, ConnectionSide
    from rm_sim.core.face_identity import fingerprint_from_face
    from rm_sim.core.materials import set_part_material
    from rm_sim.core.project import Constraint, GravityLoad, LoadCase, Project
    from rm_sim.solver.assembly_workflow import finish_analysis, prepare_analysis
    from rm_sim.solver.tasks import SolveScheduler, STATE_COMPLETED

    work.mkdir(parents=True, exist_ok=True)
    model = import_assembly(ROOT / "samples" / "assembly_two_blocks.step")
    try:
        project = Project(name="编排自检最小两块装配（自重）", project_dir=str(work))
        sync_parts_from_model(project, model)
        for part in project.parts:
            set_part_material(project, part.part_id, "Q235")
        p1, p2 = project.parts
        f_fixed = next(f for f in model.faces
                       if f.solid_tag == int(model.solids[0].tag) and f.normal[1] < -0.999)
        f_top1 = next(f for f in model.faces
                      if f.solid_tag == int(model.solids[0].tag) and f.normal[1] > 0.999)
        f_bottom2 = next(f for f in model.faces
                         if f.solid_tag == int(model.solids[1].tag) and f.normal[1] < -0.999)
        # 装配连接性门（deck.py）要求 p2 经连接边接地：界面粘接 + 底面固定 + 自重
        project.connections = [BondedConnection(
            name="界面粘接",
            side_a=ConnectionSide(p1.part_id, fingerprint_from_face(f_top1)),
            side_b=ConnectionSide(p2.part_id, fingerprint_from_face(f_bottom2)),
        )]
        project.study.geometry_file = str(ROOT / "samples" / "assembly_two_blocks.step")
        project.study.mesh_size_mm = 2.5
        project.study.load_cases = [LoadCase(
            name="仅重力",
            constraints=[Constraint(name="固定", kind="fixed", face_tag=int(f_fixed.tag),
                                    face=fingerprint_from_face(f_fixed), part_id=p1.part_id)],
            loads=[GravityLoad(name="重力", direction=(0.0, -1.0, 0.0), g_mm_s2=9810.0)],
        )]
        prepared = prepare_analysis(project, model, work / "mesh.inp")
        sched = SolveScheduler(get_ccx_path(load_config()), max_workers=1)
        task = sched.submit("assembly_full", prepared["deck"], cases_root)
        task = sched.wait_task(task.task_id, timeout=900)
        if task.state != STATE_COMPLETED:
            raise RuntimeError(f"最小两块装配求解失败：{task.summary()}")
        finish_analysis(project, task, prepared)
        return task.case_dir, task.job_name, True
    finally:
        model.close()


def ensure_workflow_record(record_path: Path, *, force: bool = False) -> tuple[Path, bool]:
    """最小两块装配后台工作流 → 完成记录 JSON → (record_path, solved_now)。

    固定 record_path 时幂等；记录由 WorkflowScheduler 真实跑 prepare/solve/collect
    产出 completed.json（工作流模块的完成契约，与 GUI 同一条链路）。
    """
    if not force and record_path.is_file():
        return record_path, False

    from rm_sim.cad.assembly_import import import_assembly, sync_parts_from_model
    from rm_sim.config import get_ccx_path, load_config
    from rm_sim.core.connections import BondedConnection, ConnectionSide
    from rm_sim.core.face_identity import fingerprint_from_face
    from rm_sim.core.materials import set_part_material
    from rm_sim.core.project import Constraint, LoadCase, Project, TractionLoad
    from rm_sim.solver.workflow import WorkflowScheduler

    source = ROOT / "samples" / "assembly_two_blocks.step"
    work = record_path.parent
    project_dir = work / "project"
    project_dir.mkdir(parents=True, exist_ok=True)
    model = import_assembly(source)
    try:
        project = Project(name="编排自检最小两块装配（后台工作流）", project_dir=str(project_dir))
        sync_parts_from_model(project, model)
        for part in project.parts:
            set_part_material(project, part.part_id, "Q235")
        p1, p2 = project.parts
        f_fixed = next(f for f in model.faces
                       if f.solid_tag == int(model.solids[0].tag) and f.normal[1] < -0.999)
        f_top1 = next(f for f in model.faces
                      if f.solid_tag == int(model.solids[0].tag) and f.normal[1] > 0.999)
        f_bottom2 = next(f for f in model.faces
                         if f.solid_tag == int(model.solids[1].tag) and f.normal[1] < -0.999)
        f_load = next(f for f in model.faces
                      if f.solid_tag == int(model.solids[1].tag) and f.normal[1] > 0.999)
        # 装配连接性门（deck.py）要求 p2 经连接边接地：界面粘接 + 底面固定
        project.connections = [BondedConnection(
            name="界面粘接",
            side_a=ConnectionSide(p1.part_id, fingerprint_from_face(f_top1)),
            side_b=ConnectionSide(p2.part_id, fingerprint_from_face(f_bottom2)),
        )]
        project.study.geometry_file = str(source)
        project.study.mesh_size_mm = 2.5
        project.study.load_cases = [LoadCase(
            name="100N",
            constraints=[Constraint(name="固定", kind="fixed", face_tag=int(f_fixed.tag),
                                    face=fingerprint_from_face(f_fixed), part_id=p1.part_id)],
            loads=[TractionLoad(name="面力", face_tag=int(f_load.tag),
                                face=fingerprint_from_face(f_load), part_id=p2.part_id,
                                total_force_n=100.0, direction=(0.0, -1.0, 0.0))],
        )]
        sched = WorkflowScheduler(get_ccx_path(load_config()))
        task = sched.submit(project, source)
        end = time.monotonic() + 900
        while time.monotonic() < end and task.state in ("pending", "running"):
            time.sleep(0.1)
        if task.state != "completed":
            raise RuntimeError(f"最小两块装配后台工作流失败：{task.summary()}")
        if not (task.work / "completed.json").is_file():
            raise RuntimeError(f"后台工作流完成但缺 completed.json：{task.work}")
    finally:
        model.close()
    record_path.parent.mkdir(parents=True, exist_ok=True)
    record_path.write_text(json.dumps(
        {"complete_work": str(task.work), "source": "orchestration_noarg_minimal_two_block"},
        ensure_ascii=False, indent=2), encoding="utf-8")
    return record_path, True
