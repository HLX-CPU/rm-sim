# -*- coding: utf-8 -*-
"""共享冻结产物：一次真实小算例求解，供 GUI/报告/序列化读取型检查复用（调研 §4）。

口径（analysis/装配仿真验收提速调研_2026-11-03.md §4）：
- 同一个两块粘接梁的真实求解，多个读取型检查读取同一输入冻结的结果，不必各自
  重新网格并求解；
- 缓存键含**几何文件内容、几何映射、网格参数、Gmsh 版本与网格生成代码版本**、
  **最终 INP 及全部 INCLUDE 内容、引擎版本与二进制指纹、线程/数值设置**；
- 「新版本输入生成逻辑必须实际执行后再判断能否复用」——try 复用前真实执行
  几何导入 + 网格 + deck 组装（不求解），用产出的最终 INP 计算完整键；
- **载荷变更不能不经判断就重用整份结果**——载荷进入最终 INP 内容，载荷一变
  键即变，旧产物整份拒绝；
- 缓存只接受**完成、文件齐全且哈希匹配**的产物（压缩束成员齐全 + results.json
  state=completed + 日志 Job finished + 成员哈希逐一匹配 + 参考数独立复算通过），
  来源与时间记入 manifest 可追查；
- 共享数据只读：读取型检查在独立临时目录解包使用（materialize）。

产物形态：save_bundle 的工程束（project.rmsimproject）——内含 project.json
（含 case 索引/部件指纹/网格参数）、几何副本、完整 case 产物
（inp/dat/frd/log/sta/cvg/12d/vtu/results.json/report.md/云图）。束本身即
「同一输入冻结的结果」的 app 原生序列化形态。

读取型检查约定输出（供 run_regression 来源单列与验收脚本断言）：
    [product] source=<本轮新求解|复用历史求解并重新检查> key=<key前16>
    READER_RESULT: kind=<gui|report|serialization> max_mises_mpa=<...> \\
        max_u_mag_mm=<...> rf_sum_n_y=<...>
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os
import re
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "app") not in sys.path:
    sys.path.insert(0, str(ROOT / "app"))
if str(ROOT / "verify") not in sys.path:
    sys.path.insert(0, str(ROOT / "verify"))

# 项目求解环境契约（run_regression.py 同款约定）：共享产物的键含线程设置，
# 读取/冻结方必须与求解方同环境口径——未设时固定 2（已设不覆盖）。
os.environ.setdefault("OMP_NUM_THREADS", "2")

# 证据根可被验收脚本重定向到隔离目录（共享数据只读；负例在独立根演练）
STORE_ROOT = Path(os.environ.get("RM_SIM_SHARED_PRODUCTS_ROOT",
                                 "E:/rm_sim/verification/shared_products"))

# 冻结规格：标准两块粘接梁（与 assembly_example bonded 同参；Q235、mm 单位约定）
SPEC = {"step_source": str(ROOT / "samples" / "assembly_two_blocks.step"),
        "force_n": 1000.0, "mesh_size_mm": 2.0, "connection": "bonded", "gravity": True}
SPEC_FIELDS = ("step_source", "force_n", "mesh_size_mm", "connection", "gravity")

MANIFEST_SCHEMA = "rm_sim_shared_product_v1"


def sha256_file(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


# ---------------------------------------------------------------- 键（输入级 + INP 级）
def build_spec_project(work_dir: Path, *, step_source: str, force_n: float,
                       mesh_size_mm: float, connection: str, gravity: bool):
    """构造规格工程（不求解）——与 solver.assembly_workflow.new_assembly_project
    的工程构造逐字段一致（去掉求解与束保存）。"""
    from rm_sim.cad.assembly_import import import_assembly, sync_parts_from_model
    from rm_sim.core.project import Project, Constraint, LoadCase, TractionLoad, GravityLoad
    from rm_sim.core.connections import ConnectionSide, ContactFrictionConnection, BondedConnection
    from rm_sim.core.face_identity import fingerprint_from_face
    from rm_sim.core.materials import set_part_material, part_material

    model = import_assembly(step_source)
    try:
        if len(model.solids) != 2:
            raise ValueError("标准叠块规格需要两个实体")
        project = Project(name="共享产物规格（两块粘接梁）",
                          project_dir=str(Path(work_dir).resolve()))
        sync_parts_from_model(project, model)
        for p in project.parts:
            set_part_material(project, p.part_id, "Q235")
        project.study.material = part_material(project, project.parts[0])
        project.study.geometry_file = str(Path(step_source).resolve())
        project.study.mesh_size_mm = mesh_size_mm
        ordered = sorted(project.parts, key=lambda p: p.fingerprint.centroid[1])
        lower, upper = ordered

        def face(part, sign):
            fs = [f for f in model.faces
                  if f.solid_tag == model.solids[part.solid_index].tag and f.normal[1] * sign > .999]
            if len(fs) != 1:
                raise ValueError("规格要求平面叠块；无法唯一识别 y 向端面")
            return fs[0]

        bottom, top = face(lower, -1), face(upper, 1)
        a, b = face(lower, 1), face(upper, -1)
        project.study.load_cases = [LoadCase(name="叠块压缩与重力", constraints=[
            Constraint(name="底面固定", face_tag=bottom.tag, face=fingerprint_from_face(bottom),
                       part_id=lower.part_id)],
            loads=[TractionLoad(name="顶面总力", face_tag=top.tag, face=fingerprint_from_face(top),
                                part_id=upper.part_id, total_force_n=force_n, direction=(0., -1., 0.))])]
        if gravity:
            project.study.load_cases[0].loads.append(GravityLoad(name="自重"))
        sides = dict(name="叠块界面", side_a=ConnectionSide(lower.part_id, fingerprint_from_face(a)),
                     side_b=ConnectionSide(upper.part_id, fingerprint_from_face(b)))
        if connection not in ("contact", "bonded"):
            raise ValueError("connection 只能是 contact 或 bonded")
        project.connections = [ContactFrictionConnection(**sides, friction_mu=0.3)
                               if connection == "contact" else BondedConnection(**sides)]
        return project, model
    except Exception:
        model.close()
        raise


def _engine_fingerprint() -> dict:
    from rm_sim.config import get_ccx_path, load_config
    ccx = Path(get_ccx_path(load_config()))
    return {"ccx_path": str(ccx), "binary_sha256": sha256_file(ccx)}


def _mesh_code_version() -> dict:
    """网格生成代码版本（内容哈希——代码一变键即变，旧产物不盲复用）。"""
    files = ["app/rm_sim/mesh/mesher.py", "app/rm_sim/mesh/refinement.py",
             "app/rm_sim/gmsh_runtime.py"]
    return {name: sha256_file(ROOT / name) for name in files}


def compute_key(work_dir, **overrides) -> dict:
    """真实执行几何导入+网格+deck 组装（不求解）后计算完整缓存键。

    返回 {"key", "components", "deck_path"}；components 即键的全部输入口径
    （几何内容/几何映射/网格参数+gmsh 版本+网格代码版本/最终 INP 与 INCLUDE/
    引擎指纹/线程与数值设置）。
    """
    spec = {**SPEC, **overrides}
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    import gmsh
    from rm_sim.solver.assembly_workflow import prepare_analysis

    project, model = build_spec_project(work_dir / "spec", **spec)
    try:
        prepared = prepare_analysis(project, model, work_dir / "mesh.inp")
    finally:
        model.close()
    deck_text = prepared["deck"]
    if not isinstance(deck_text, str) or "*NODE" not in deck_text:
        raise ValueError(f"prepare_analysis 未返回 deck 文本（得到 {type(deck_text).__name__}）")
    includes = {}
    for m in re.finditer(r"(?im)^\*INCLUDE,\s*INPUT\s*=\s*(.+?)\s*$", deck_text):
        inc = (work_dir / "mesh.inp").parent / m.group(1).strip().strip('"')
        includes[m.group(1).strip()] = sha256_file(inc) if inc.is_file() else "MISSING"
    if any(v == "MISSING" for v in includes.values()):
        raise ValueError("deck 含无法在生成目录解析的 *INCLUDE，不能计算完整键")
    mapping = [{"part_id": p.part_id, "name": p.name,
                "fingerprint": {"volume_mm3": p.fingerprint.volume_mm3,
                                "centroid": list(p.fingerprint.centroid),
                                "bbox_min": list(p.fingerprint.bbox_min),
                                "bbox_max": list(p.fingerprint.bbox_max)}}
               for p in project.parts]
    solver_lines = [ln.strip() for ln in deck_text.splitlines()
                    if ln.strip().upper().startswith("*SOLVER")]
    components = {
        "spec": {k: spec[k] for k in SPEC_FIELDS},
        "geometry_files": {Path(spec["step_source"]).name: sha256_file(spec["step_source"])},
        "geometry_mapping": mapping,
        "mesh_params": {"mesh_size_mm": float(spec["mesh_size_mm"]),
                        "gmsh_version": str(gmsh.GMSH_API_VERSION),
                        "mesh_code_sha256": _mesh_code_version()},
        "inp_contents": {"case_deck.inp": sha256_text(deck_text), "**includes": includes},
        "engine": _engine_fingerprint(),
        "threads_numerics": {"omp_num_threads": os.environ.get("OMP_NUM_THREADS", "unset"),
                             "units": "mm/N/MPa/t-per-mm3（产品固定单位约定）",
                             "solver_directive": solver_lines[0] if solver_lines else "default"},
    }
    return {"key": sha256_bytes(canonical(components).encode("utf-8")),
            "components": components}


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- 求解（未命中时）
def solve_fresh(work: Path, **overrides) -> dict:
    """未命中时按规格真实求解一次（batch assembly_new_solve，独立进程）。"""
    spec = {**SPEC, **overrides}
    from assembly_check_utils import batch
    ctx = batch(Path(work), "fresh_solve", action="assembly_new_solve",
                project_dir=str(Path(work) / "fresh_project"),
                step_source=spec["step_source"], connection=spec["connection"],
                force_n=spec["force_n"], mesh_size_mm=spec["mesh_size_mm"],
                gravity=spec["gravity"])
    if not ctx.get("bundle"):
        raise RuntimeError(f"求解完成但未返回工程束路径: {sorted(ctx)}")
    return ctx


# ---------------------------------------------------------------- 束完整性与冻结
REQUIRED_CASE_SUFFIXES = (".inp", ".dat", ".frd", ".log", ".vtu", ".sta")
REQUIRED_MEMBERS = ("project.json",)


def _member_map(bundle_path: Path) -> dict:
    with zipfile.ZipFile(bundle_path) as z:
        return {name: sha256_bytes(z.read(name)) for name in z.namelist() if not name.endswith("/")}


def validate_bundle(bundle_path: Path) -> tuple[bool, str, dict]:
    """完成 + 文件齐全（case 产物成员齐全、results.json completed、日志 Job finished）。"""
    bundle_path = Path(bundle_path)
    if not bundle_path.is_file():
        return False, f"束文件不存在: {bundle_path}", {}
    try:
        with zipfile.ZipFile(bundle_path) as z:
            names = z.namelist()
            blobs = {n: z.read(n) for n in names if not n.endswith("/")}
    except zipfile.BadZipFile as exc:
        return False, f"束不是有效 zip: {exc}", {}
    for req in REQUIRED_MEMBERS:
        if req not in blobs:
            return False, f"缺必需成员 {req}", {}
    if not any(n.startswith("geometry/") for n in blobs):
        return False, "缺几何副本成员 geometry/*", {}
    case_dirs = {n.split("/")[1] for n in blobs if n.startswith("cases/") and len(n.split("/")) > 2}
    if not case_dirs:
        return False, "束内无 case 产物", {}
    for case in case_dirs:
        present = {n for n in blobs if n.startswith(f"cases/{case}/")}
        for suffix in REQUIRED_CASE_SUFFIXES:
            if not any(n.endswith(suffix) for n in present):
                return False, f"case {case} 缺 {suffix} 产物", {}
        for fname in ("results.json", "report.md"):
            if not any(n.endswith("/" + fname) for n in present):
                return False, f"case {case} 缺 {fname}", {}
        rj = json.loads(next(blobs[n] for n in present if n.endswith("/results.json")))
        if rj.get("state") != "completed" or not rj.get("job_finished"):
            return False, f"case {case} 求解未完成（state={rj.get('state')}）", {}
        logs = sorted(n for n in present if n.endswith(".log"))
        ccx_logs = [n for n in logs
                    if not n.endswith((".stdout.log", ".stderr.log"))]  # 求解器日志=<job>.log
        if not ccx_logs:
            return False, f"case {case} 缺求解器 .log 产物（仅有 {logs}）", {}
        if "Job finished" not in blobs[ccx_logs[0]].decode("utf-8", errors="replace"):
            return False, f"case {case} 日志无 Job finished", {}
    info = {"case_dirs": sorted(case_dirs), "members": len(blobs)}
    return True, "ok", info


def _bundle_reference(bundle_path: Path, extract_dir: Path) -> dict:
    """冻结前独立复算参考数：解包 → 打开 → load_archived_case（其内部已对
    summary 与原始场做 rtol=1e-5 一致性门）。不一致即冻结被拒。"""
    from rm_sim.core.project_bundle import open_bundle
    from rm_sim.solver.history import load_archived_case

    opened = open_bundle(bundle_path, extract_dir)
    project = opened["project"]
    project.project_dir = str(Path(extract_dir).resolve())
    if not project.cases:
        raise ValueError("束内工程没有 case 索引，无法冻结为共享产物")
    record = project.cases[-1]
    loaded = load_archived_case(project, record.name)
    ctx = loaded["context"]
    r = ctx["results"]
    return {"case_name": record.name, "job_name": record.job_name,
            "max_u_mag_mm": float(r["max_u_mag_mm"]),
            "max_mises_mpa": float(r["max_mises_mpa"]),
            "rf_sum_n": [float(x) for x in r["rf_sum_n"]],
            "tip_uy_mm": float(r["tip_uy_mm"]),
            "frd_node_count": int(r.get("frd_node_count") or 0)}


def freeze_product(key: str, components: dict, bundle_path, *, source_check: str) -> dict:
    """把完成、齐全、哈希可查且参考数复算通过的束冻结为共享产物（原子建 <key> 目录）。

    已存在同名有效产物 → 直接复用（不覆盖）；存在无效产物 → 拒绝（不清理不掩盖）。
    """
    bundle_path = Path(bundle_path)
    ok, reason, _ = validate_bundle(bundle_path)
    if not ok:
        raise ValueError(f"冻结被拒（产物不完整）: {reason}")
    target = STORE_ROOT / key
    if target.is_dir():
        manifest_path = target / "manifest.json"
        if manifest_path.is_file():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            ok2, reason2, _ = verify_product_integrity(manifest, target / manifest["bundle_file"])
            if ok2:
                return manifest
        raise ValueError(f"冻结被拒（同键目录已存在且无效，不覆盖）: {target}")
    reference = _bundle_reference(bundle_path, STORE_ROOT / f"_ref_{key[:16]}.tmp")
    manifest = {"schema": MANIFEST_SCHEMA, "key": key,
                "created": datetime.datetime.now().isoformat(timespec="seconds"),
                "source_check": source_check, "components": components,
                "bundle_file": "frozen.rmsimproject",
                "bundle_sha256": sha256_file(bundle_path),
                "members": _member_map(bundle_path),
                "reference": reference,
                "provenance": {"solve_bundle": str(bundle_path), "frozen_by": source_check}}
    tmp = STORE_ROOT / f"_freeze_{key[:16]}.tmp{os.getpid()}"
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)
    shutil.copy2(bundle_path, tmp / manifest["bundle_file"])
    (tmp / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                                       encoding="utf-8")
    STORE_ROOT.mkdir(parents=True, exist_ok=True)
    try:
        os.rename(tmp, target)
    except OSError:
        shutil.rmtree(tmp, ignore_errors=True)  # 并发冻结另一方已落盘 → 复用其产物
        manifest = json.loads((target / "manifest.json").read_text(encoding="utf-8"))
        ok2, reason2, _ = verify_product_integrity(manifest, target / manifest["bundle_file"])
        if not ok2:
            raise ValueError(f"并发冻结产物无效: {reason2}")
        return manifest
    finally:
        ref_tmp = STORE_ROOT / f"_ref_{key[:16]}.tmp"
        if ref_tmp.exists():
            shutil.rmtree(ref_tmp, ignore_errors=True)
    return manifest


def load_manifest(key: str):
    path = STORE_ROOT / key / "manifest.json"
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def verify_product_integrity(manifest: dict, bundle_path) -> tuple[bool, str, dict]:
    """只接受完成、文件齐全且哈希匹配的产物：束文件哈希 + 全成员哈希 + 完成标记。"""
    bundle_path = Path(bundle_path)
    if not bundle_path.is_file():
        return False, "产物束文件缺失", {}
    if sha256_file(bundle_path) != manifest.get("bundle_sha256"):
        return False, "束文件哈希与 manifest 不符", {}
    ok, reason, info = validate_bundle(bundle_path)
    if not ok:
        return False, f"产物不完整: {reason}", info
    members = _member_map(bundle_path)
    recorded = manifest.get("members") or {}
    if set(members) != set(recorded):
        missing = sorted(set(recorded) - set(members))[:5]
        extra = sorted(set(members) - set(recorded))[:5]
        return False, f"成员清单不符 missing={missing} extra={extra}", info
    for name, digest in recorded.items():
        if members[name] != digest:
            return False, f"成员哈希不匹配: {name}", info
    return True, "ok", info


def try_reuse(key: str):
    """命中且完整 → 返回 manifest；否则 None（调用方未命中即自行真实求解）。"""
    manifest = load_manifest(key)
    if manifest is None:
        return None
    ok, reason, _ = verify_product_integrity(manifest, STORE_ROOT / key / manifest["bundle_file"])
    if not ok:
        print(f"[product] reuse_rejected key={key[:16]} reason={reason}", flush=True)
        return None
    return manifest


def materialize(key: str, extract_dir):
    """把共享产物解包到独立临时目录（共享数据只读）并再次完整性校验。"""
    manifest = load_manifest(key)
    if manifest is None:
        raise ValueError(f"无此键的共享产物: {key[:16]}")
    bundle_path = STORE_ROOT / key / manifest["bundle_file"]
    ok, reason, _ = verify_product_integrity(manifest, bundle_path)
    if not ok:
        raise ValueError(f"复用被拒（完整性校验失败）: {reason}")
    from rm_sim.core.project_bundle import open_bundle
    extract_dir = Path(extract_dir)
    opened = open_bundle(bundle_path, extract_dir)
    project = opened["project"]
    project.project_dir = str(extract_dir.resolve())
    case = next(r for r in project.cases if r.name == manifest["reference"]["case_name"])
    case_dir = Path(extract_dir).resolve() / "cases" / case.name
    return {"manifest": manifest, "project": project, "case_name": case.name,
            "job_name": case.job_name, "case_dir": case_dir,
            "bundle_path": bundle_path}


def run_reader(work: Path, check_name: str, kind: str, read_fn):
    """读取型检查共通流程（调研 §4）：
    算键 → 查复用 → 未命中则真实求解一次并冻结 → 物化（独立临时目录，只读使用）
    → read_fn(mat) 独立重推导结论 → 与冻结参考一致性门（|x−ref| ≤ 1e-9×|ref|，
    rf 逐分量同口径）→ 打印 [product] source=… 与 READER_RESULT。
    返回 (source, numbers)。
    """
    work = Path(work)
    key_info = compute_key(work / "keywork")
    manifest = try_reuse(key_info["key"])
    if manifest is None:
        ctx = solve_fresh(work)
        manifest = freeze_product(key_info["key"], key_info["components"],
                                  ctx["bundle"], source_check=check_name)
        source = "本轮新求解"
    else:
        source = "复用历史求解并重新检查"
    mat = materialize(key_info["key"], work / "product")
    numbers = read_fn(work, mat, manifest)
    ref = manifest["reference"]
    for name, got, want in (("max_mises_mpa", numbers["max_mises_mpa"], ref["max_mises_mpa"]),
                            ("max_u_mag_mm", numbers["max_u_mag_mm"], ref["max_u_mag_mm"])):
        if abs(float(got) - float(want)) > 1e-9 * max(abs(float(want)), 1e-30):
            raise AssertionError(f"{check_name} 结论与冻结参考不一致（{name}）: {got} vs {want}")
    got_rf = [float(x) for x in numbers["rf_sum_n"]]
    want_rf = [float(x) for x in ref["rf_sum_n"]]
    for i, (g, w) in enumerate(zip(got_rf, want_rf)):
        if abs(g - w) > 1e-9 * max(abs(w), 1e-30):
            raise AssertionError(f"{check_name} 结论与冻结参考不一致（rf_sum_n[{i}]）: {g} vs {w}")
    print(f"[product] source={source} key={key_info['key'][:16]}", flush=True)
    print(f"READER_RESULT: kind={kind} max_mises_mpa={numbers['max_mises_mpa']!r} "
          f"max_u_mag_mm={numbers['max_u_mag_mm']!r} rf_sum_n_y={got_rf[1]!r}", flush=True)
    return source, numbers
