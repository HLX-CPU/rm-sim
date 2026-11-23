# -*- coding: utf-8 -*-
"""工程束格式 .rmsimproject（GUI 无关层，core）：zip 容器。

布局（format.txt 随包写入，操作说明见 app/docs/module_11_project_roundtrip.md）：
  project.json          schema=rm_sim.bundle@1；project 节 = core.store 全精度序列化
                        （含面指纹 FaceFingerprint / CaseRecord / is_confirmed / dirty）
  geometry/<原文件名>    STEP 几何副本（重开时对该副本 re-resolve 面指纹）
  cases/case_NNN/**      各次求解产物（job.inp/.dat/.frd/.sta/.cvg/.12d/日志/
                         results.json/report.md/云图 PNG）
  format.txt             本格式说明

打开 = 解包到工作目录 + from_dict 恢复数据模型；面身份重解析由
solver.project_workflow（需几何内核）负责，本模块只做容器读写。
"""
from __future__ import annotations

import datetime as _dt
import json
import zipfile
from pathlib import Path
from pathlib import PureWindowsPath

from .project import migrate_legacy_parts
from .store import from_dict, to_dict

BUNDLE_SUFFIX = ".rmsimproject"
BUNDLE_SCHEMA = "rm_sim.bundle@1"

FORMAT_DOC = """rm_sim 工程束格式 (.rmsimproject) — zip 容器, schema=rm_sim.bundle@1

  project.json
      {"schema": "rm_sim.bundle@1", "format_version": 1, "created": ...,
       "geometry": {"source_name": 原始 STEP 文件名, "bundled": "geometry/<名>"},
       "project": { ... 数据模型全量（core.store to_dict，含面指纹） ... }}
  geometry/<原文件名>
      STEP 几何副本。重开工程时对该副本重新导入并按面指纹（面积+形心+法向，
      相对容差 1e-6）re-resolve 约束/载荷面；不匹配 → 工程进入「需重新确认」
      状态（is_confirmed=False），绝不静默贴错面。
  cases/case_NNN/
      每次求解的完整产物：job.inp、ccx 原始产物（.dat/.frd/.sta/.cvg/.12d）、
      {job}.log（stdout）、results.json（全精度机器数据）、report.md（计算报告，
      正文人类可读位数 + 附录 A 全精度）、mises.png / displaced.png。
  format.txt
      本说明。

面指纹持久化约定：约束/载荷保存 face=FaceFingerprint(面积,形心,法向)，
face_tag 仅为运行期缓存（重解析后回写）。修改材料/载荷/约束/网格参数后
dirty=True，重算生成新 case_NNN（旧 case 产物永不覆盖）。
"""


class BundleError(RuntimeError):
    """工程束读写失败。"""


def save_bundle(
    path: str | Path,
    project,  # noqa: ANN001 - core.Project
    geometry_source: str | Path | None = None,
    cases_root: str | Path | None = None,
    part_geometry_sources: dict | None = None,
) -> Path:
    """保存工程束（zip；先写 .tmp 再原子替换）。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    geometry_source = Path(geometry_source) if geometry_source else None
    cases_root = Path(cases_root) if cases_root else None
    # 把自定义材料数值随工程携带；重开不依赖外部材料库的当前内容。
    from dataclasses import replace
    from .materials import part_material
    for part in project.parts:
        if part.material_name:
            part.material_snapshot = replace(part_material(project, part))

    meta: dict = {
        "schema": BUNDLE_SCHEMA,
        "format_version": 1,
        "created": _dt.datetime.now().isoformat(timespec="milliseconds"),
        "project": to_dict(project),
        "geometry": None,
        "part_geometries": {pid: f"geometry/parts/{pid}.step" for pid in (part_geometry_sources or {})},
    }
    if geometry_source is not None and geometry_source.is_file():
        meta["geometry"] = {
            "source_name": geometry_source.name,
            "bundled": f"geometry/{geometry_source.name}",
        }

    tmp = p.with_name(p.name + ".rm_tmp")
    try:
        with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as z:
            z.writestr("format.txt", FORMAT_DOC)
            z.writestr("project.json", json.dumps(meta, ensure_ascii=False, indent=2))
            if geometry_source is not None and geometry_source.is_file():
                z.write(geometry_source, f"geometry/{geometry_source.name}")
            for pid, source in (part_geometry_sources or {}).items():
                z.write(source, f"geometry/parts/{pid}.step")
            if cases_root is not None and cases_root.is_dir():
                for case_dir in sorted(cases_root.glob("case_*")):
                    if not case_dir.is_dir():
                        continue
                    for f in sorted(case_dir.rglob("*")):
                        if f.is_file():
                            z.write(f, f"cases/{case_dir.name}/{f.relative_to(case_dir).as_posix()}")
        import os

        os.replace(tmp, p)
    except Exception:
        try:
            tmp.unlink()  # 仅清理本次创建的临时文件
        except OSError:
            pass
        raise
    return p


def open_bundle(path: str | Path, extract_dir: str | Path) -> dict:
    """打开工程束：校验 schema、恢复数据模型、解包几何副本与 case 产物。

    返回 {"project", "meta", "geometry_path"|None, "cases_dir"|None, "bundle_path"}。
    """
    p = Path(path)
    if not p.is_file():
        raise BundleError(f"工程文件不存在: {p}")
    extract_dir = Path(extract_dir)
    extract_dir.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(p, "r") as z:
            names = z.namelist()
            if "project.json" not in names:
                raise BundleError(f"工程束缺少 project.json: {p}")
            meta = json.loads(z.read("project.json").decode("utf-8"))
            if not isinstance(meta, dict) or meta.get("schema") != BUNDLE_SCHEMA:
                raise BundleError(f"工程束 schema 不符（期望 {BUNDLE_SCHEMA}）: {p}")
            project = from_dict(meta.get("project"))
            # 旧版单部件工程（无 parts 键）→ parts=[1 条]（与 store.load_project 同一迁移）
            migrate_legacy_parts(project)
            # Validate every selected path and every existing target before the
            # first extraction write; reopening cannot overwrite newer work.
            geo = meta.get("geometry") or {}
            selected = list((meta.get("part_geometries") or {}).values())
            if geo.get("bundled"):
                selected.append(geo["bundled"])
            selected += [n for n in names if n.startswith("cases/") and not n.endswith("/")]
            targets = {}
            root = extract_dir.resolve()
            for name in selected:
                relative = Path(name)
                if ("\\" in name or ":" in name or relative.is_absolute() or PureWindowsPath(name).drive
                        or ".." in relative.parts or not relative.parts):
                    raise BundleError("工程束中含非法路径: " + name)
                target = (root / relative).resolve()
                if not target.is_relative_to(root):
                    raise BundleError("工程束路径越出解包目录: " + name)
                if name not in names:
                    raise BundleError("工程束缺少声明的文件: " + name)
                if target.exists() and (not target.is_file() or target.read_bytes() != z.read(name)):
                    raise BundleError("解包目标已有不同内容，请选择新的工作目录: " + str(target))
                targets[name] = target
            for name, target in targets.items():
                if not target.exists():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with target.open("xb") as stream:
                        stream.write(z.read(name))
            geometry_path = None
            bundled = geo.get("bundled")
            if bundled and bundled in names:
                geometry_path = targets[bundled]
            cases_dir = None
            case_names = [n for n in names if n.startswith("cases/") and not n.endswith("/")]
            if case_names:
                cases_dir = extract_dir / "cases"
    except zipfile.BadZipFile as exc:
        raise BundleError(f"工程束不是有效的 zip 文件: {p}: {exc}") from exc
    return {
        "project": project,
        "meta": meta,
        "geometry_path": geometry_path,
        "cases_dir": cases_dir,
        "bundle_path": p,
    }
