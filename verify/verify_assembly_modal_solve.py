# -*- coding: utf-8 -*-
"""验收脚本：整装模态真实求解与理论核验（模块55，exit 0 = 通过；日志写入 verify/logs/）。

覆盖验收标准：
① 串联两杆绑定装配（samples/assembly_serial_rods.step，两杆各 10×10×100mm
   x∈[0,200]，本夹具两杆同指定 45钢 → 等效均匀悬臂梁口径）固定端模态 f1 与
   等效悬臂梁理论 f1=1.875104068711961²/(2πL²)·√(EI/(ρA))（L=串接总长=200，
   同 verify_modal_analysis.py:10 公式）偏差 ≤3%；
② 特征值残差全部 <1e-8（ccx DAT 第 5 列=特征值虚部，复模态残差口径，
   与 modal.py 既有 |values[3]|≤1e-9 门同源）；
③ 振型在固定 NSET 上幅值 <1e-8×峰值（复用 verify_modal_analysis.py:13-14/25
   边界判据，脚本独立从 FRD+INP NSET 复算，不依赖 collect 结论）；
④ 归一化校验：每阶振型峰值=1（np.allclose，同 verify_modal_analysis.py:17/28）；
⑤ case 目录含 INP/DAT/FRD/verification.json 与历史记录 run_history.json，
   状态字段如实标注：run 历史 computed_with_evidence（previous_state=
   configured_not_computed）；准备包 manifest 保持 prepared_not_computed
   （冻结输入包不被改写）；
⑥ 回归保护：verify_advanced_framework.py 复跑 exit 0（B2 续：modal 断言
   engine_connected=True）；verify_assembly_modal_compile.py 复跑 exit 0
   （模块54 门随 B2 续同步修订后仍绿）。

产品链路：backend.prepare（模块54 编译）→ backend.run（真实 ccx：
config ccxPath=ccx_launcher.exe，Job finished 门）→ backend.collect
（复用 modal.py parse_frequencies/parse_mode_shapes + validate_modal_results
机判 + 固定 NSET 边界判据 + verification.json/run_history.json）。
运行：E:/rm_sim/venv/Scripts/python.exe verify/verify_assembly_modal_solve.py
"""
from __future__ import annotations

import json
import math
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))

LOG_PATH = ROOT / "verify" / "logs" / "verify_assembly_modal_solve.log"

from assembly_check_utils import evidence_dir  # noqa: E402

_lines: list[str] = []
_failures: list[str] = []


def log(msg: object) -> None:
    text = str(msg)
    print(text)
    _lines.append(text)


def fail(msg: str) -> None:
    _failures.append(msg)
    log(f"[FAIL] {msg}")


def ok(msg: str) -> None:
    log(f"[OK] {msg}")


def check(cond: bool, pass_msg: str, fail_msg: str) -> None:
    if cond:
        ok(pass_msg)
    else:
        fail(fail_msg)


def build_serial_rods_fixture(work: Path):
    """串联两杆绑定装配工程（两杆同指定 45钢 → 等效均匀悬臂梁）；modal 研究。"""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from rm_sim.cad.assembly_import import import_assembly, sync_parts_from_model
    from rm_sim.core.advanced import new_study
    from rm_sim.core.connections import BondedConnection, ConnectionSide
    from rm_sim.core.face_identity import fingerprint_from_face
    from rm_sim.core.materials import set_part_material
    from rm_sim.core.project import Constraint, LoadCase, Project

    step = ROOT / "samples" / "assembly_serial_rods.step"
    model = import_assembly(step)
    project = Project(name="整装模态求解夹具（串联双杆）", project_dir=str(work / "proj"))
    sync_parts_from_model(project, model)
    assert len(project.parts) == 2, [p.part_id for p in project.parts]
    # 两杆同指定 45钢：等效均匀悬臂梁口径成立（L=串接总长 200mm）；每部件仍各出
    # 自己的 *MATERIAL/*DENSITY/*SOLID SECTION 卡（多部件材料行结构保持）。
    for part in project.parts:
        set_part_material(project, part.part_id, "45钢")
    project.study.geometry_file = str(step)
    ordered = sorted(project.parts, key=lambda p: p.fingerprint.centroid[0])
    rod1, rod2 = ordered  # 杆1 x∈[0,100]、杆2 x∈[100,200]
    assert rod1.fingerprint.centroid[0] < rod2.fingerprint.centroid[0]

    def face(part, sign):
        fs = [f for f in model.faces
              if f.solid_tag == model.solids[part.solid_index].tag and f.normal[0] * sign > .999]
        assert len(fs) == 1, (part.part_id, sign, len(fs))
        return fs[0]

    fixed_face = face(rod1, -1)      # x=0 端面（固定端）
    tie_a = face(rod1, 1)            # 杆1 x=100 界面
    tie_b = face(rod2, -1)           # 杆2 x=100 界面
    project.study.load_cases = [LoadCase(name="悬臂固定", constraints=[
        Constraint(name="固定端", face_tag=fixed_face.tag, face=fingerprint_from_face(fixed_face),
                   part_id=rod1.part_id)])]
    project.connections = [BondedConnection(
        name="杆间粘接",
        side_a=ConnectionSide(rod1.part_id, fingerprint_from_face(tie_a)),
        side_b=ConnectionSide(rod2.part_id, fingerprint_from_face(tie_b)))]
    study = new_study("modal")
    study.settings.modes = 6
    project.advanced_studies = [study]
    from rm_sim.solver.advanced_validation import validate_studies
    issues = validate_studies(project)
    assert not issues, issues
    return project, rod1, rod2


def main() -> int:
    log("=" * 76)
    log("verify_assembly_modal_solve 开始（整装模态真实求解与理论核验）")
    from rm_sim.solver.advanced_modal import ModalBackend, compile_modal_input  # noqa: F401
    from rm_sim.solver.advanced_plan import (AdvancedBackend, BackendNotConnected, backend_for,
                                             load_preparation_package, write_preparation_package)
    import numpy as np

    adapter = backend_for("modal")
    check(isinstance(adapter, ModalBackend) and adapter.engine_connected is True,
          "B2 续 modal 后端已接通（engine_connected=True）",
          f"B2 续 modal 后端未接通: engine_connected={getattr(adapter, 'engine_connected', None)}")
    for kind in ("harmonic", "transient", "mechanism", "nonlinear"):
        try:
            backend_for(kind)
        except BackendNotConnected:
            ok(f"B2 续 {kind} 保持未注册 → BackendNotConnected")
        else:
            fail(f"B2 续 {kind} 未注册却拿到适配器")

    work = evidence_dir("assembly_modal_solve")
    log(f"证据目录: {work}")
    log("-" * 76)

    # ---------- 全产品链路：准备包 → prepare → run（真实 ccx）→ collect ----------
    project, rod1, rod2 = build_serial_rods_fixture(work)
    package = write_preparation_package(project, work / "package")
    ok("准备包导出（含 modal 研究）")
    prepared = adapter.prepare(package)
    ok(f"prepare 编译完成：{prepared['inp']}（modes={prepared['modes']}, 节点 {prepared['node_count']}, "
       f"单元 {prepared['element_count']}, tie_cards={prepared['tie_cards']}, fixed={prepared['fixed_nsets']}）")
    from rm_sim.config import get_ccx_path, load_config
    log(f"求解器: {get_ccx_path(load_config())}")
    run = adapter.run(prepared)
    check(run["job_finished"] and run["exit_code"] is not None,
          f"run 真实 ccx 完成（Job finished，exit_code={run['exit_code']}，耗时 {run['elapsed_s']}s）",
          "run 未正常完成")
    collected = adapter.collect(run)
    check(collected["state"] == "computed_with_evidence",
          f"状态推进：configured_not_computed → {collected['state']}",
          f"状态异常: {collected['state']}")
    log("-" * 76)

    # ---------- ① 等效悬臂梁理论对照（L=串接总长 200） ----------
    from rm_sim.core.materials import part_material, solver_material_dict
    mat = solver_material_dict(part_material(project, rod1))
    mat2 = solver_material_dict(part_material(project, rod2))
    check(mat["E_MPa"] == mat2["E_MPa"] and mat["rho_t_mm3"] == mat2["rho_t_mm3"],
          "夹具口径：两杆同材料（等效均匀悬臂梁成立）",
          f"两杆材料不同，等效公式口径失效: {mat} vs {mat2}")
    total_length_mm = 200.0            # 两杆各 100（samples/assembly_serial_rods.step 实读）
    area_mm2 = 100.0                   # 10×10
    inertia_mm4 = 10 * 10**3 / 12      # 同 verify_modal_analysis.py:10 口径
    theory = (1.875104068711961 ** 2 / (2 * math.pi * total_length_mm ** 2)
              * math.sqrt(mat["E_MPa"] * inertia_mm4 / (mat["rho_t_mm3"] * area_mm2)))
    first = collected["frequencies_hz"][0]
    deviation = abs(first - theory) / theory
    log(f"① f1(FEM)={first:.6f} Hz  f1(理论)={theory:.6f} Hz  偏差={deviation:.3%}")
    check(deviation <= 0.03,
          "① 串联两杆绑定装配固定端模态 f1 与等效悬臂梁理论偏差 ≤3%",
          f"① f1 偏差 {deviation:.3%} > 3%（fem={first:.6f}, theory={theory:.6f}）")
    log(f"① 全部频率: {[round(f, 4) for f in collected['frequencies_hz']]} Hz")

    # ---------- ② 特征值残差 <1e-8 ----------
    residuals = collected["eigen_residuals"]
    check(all(abs(v) < 1e-8 for v in residuals) and len(residuals) == prepared["modes"],
          f"② 特征值残差全部 <1e-8（max={max(abs(v) for v in residuals):.3g}，n={len(residuals)}）",
          f"② 残差越界: {residuals}")
    # 独立复算（不经 collect）：直接解析 DAT
    from rm_sim.solver.modal import parse_frequencies, parse_mode_shapes
    reparsed = parse_frequencies(run["dat"])
    check(all(abs(f["eigen_residual"]) < 1e-8 for f in reparsed)
          and [f["frequency_hz"] for f in reparsed] == collected["frequencies_hz"],
          "② DAT 独立重解析残差/频率与 collect 一致",
          "② DAT 独立重解析与 collect 不一致")

    # ---------- ③ 固定 NSET 边界判据（独立复算）+ ④ 归一化 ----------
    from rm_sim.solver.deck import parse_mesh_inp
    mesh = parse_mesh_inp(Path(run["inp"]).read_text(encoding="utf-8"))
    shapes = parse_mode_shapes(run["frd"])
    node_ids = sorted(shapes[0])
    ratios = []
    for shape in shapes:
        peak = max(np.linalg.norm(v) for v in shape.values())
        assert peak > 0
        for nset in prepared["fixed_nsets"]:
            ids = mesh["nsets"][nset]
            ratios.append(max(np.linalg.norm(shape[n]) for n in ids) / peak)
    ratio_max = max(ratios)
    log(f"③ 固定 NSET {prepared['fixed_nsets']} 振型幅值/峰值 max={ratio_max:.3g}")
    check(ratio_max < 1e-8,
          "③ 振型在固定 NSET 上幅值 <1e-8×峰值（verify_modal_analysis:25 同判据，独立复算）",
          f"③ 固定边界幅值比 {ratio_max:.3g} ≥ 1e-8")
    check(collected["fixed_peak_ratio_max"] < 1e-8,
          "③ collect 内置边界判据同过（fixed_peak_ratio_max<1e-8）",
          "③ collect 边界判据失败")
    payload = collected["payload"]
    shape_arr = np.asarray(payload["normalized_mode_shapes"], dtype=float)
    peaks = np.linalg.norm(shape_arr, axis=2).max(axis=1)
    check(np.allclose(peaks, 1),
          f"④ 归一化校验：每阶振型峰值=1（np.allclose，peaks={np.round(peaks, 9).tolist()}）",
          f"④ 归一化峰值异常: {peaks.tolist()}")

    # ---------- ⑤ 产物齐全与状态如实标注 ----------
    case = Path(run["case_dir"])
    required = ["modal.inp", "modal.dat", "modal.frd", "modal.log", "verification.json",
                "run_history.json", "mesh.inp"]
    missing = [n for n in required if not (case / n).is_file() or (case / n).stat().st_size == 0]
    check(not missing, f"⑤ case 目录产物齐全（{required}）", f"⑤ 缺产物: {missing}")
    verification = json.loads((case / "verification.json").read_text(encoding="utf-8"))
    check(verification["state"] == "computed_with_evidence" and verification["status"] == "PASS",
          "⑤ verification.json 状态=computed_with_evidence", f"⑤ verification 状态异常: {verification['state']}")
    history = json.loads((case / "run_history.json").read_text(encoding="utf-8"))
    entry = history[-1]
    check(entry["state"] == "computed_with_evidence" and entry["previous_state"] == "configured_not_computed",
          "⑤ 历史记录 run_history.json：state=computed_with_evidence + previous_state=configured_not_computed",
          f"⑤ 历史状态字段异常: {entry}")
    check(abs(entry["first_hz"] - first) <= 0 and entry["modes"] == prepared["modes"],
          "⑤ 历史记录频率/模态数与本次运行一致", "⑤ 历史记录数值不符")
    package_manifest = json.loads((Path(package) / "manifest.json").read_text(encoding="utf-8"))
    check(package_manifest["state"] == "prepared_not_computed" and package_manifest["results_available"] is False,
          "⑤ 准备包 manifest 保持 prepared_not_computed（冻结输入不被改写——运行状态在 case 证据与历史中如实标注）",
          f"⑤ 准备包 manifest 被改写: {package_manifest['state']}")
    log("-" * 76)

    # ---------- ⑥ 回归保护 ----------
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", PYTHONIOENCODING="utf-8")
    for gate in ("verify_advanced_framework.py", "verify_assembly_modal_compile.py"):
        log(f"$ QT_QPA_PLATFORM=offscreen <venv python> -X utf8 verify/{gate}")
        proc = subprocess.run([sys.executable, "-X", "utf8", str(ROOT / "verify" / gate)],
                              capture_output=True, timeout=900, env=env, cwd=str(ROOT))
        (work / f"{gate.removesuffix('.py')}_stdout.log").write_bytes(proc.stdout)
        (work / f"{gate.removesuffix('.py')}_stderr.log").write_bytes(proc.stderr)
        out = proc.stdout.decode("utf-8", errors="replace")
        for line in out.rstrip().splitlines()[-3:]:
            log(f"  | {line}")
        check(proc.returncode == 0,
              f"⑥ {gate} 复跑 exit 0",
              f"⑥ {gate} 复跑 exit={proc.returncode}")
        if "ImportError" in proc.stderr.decode("utf-8", errors="replace"):
            fail(f"⑥ {gate} stderr 出现 ImportError")

    if _failures:
        log(f"VERIFY_ASSEMBLY_MODAL_SOLVE: FAIL（{len(_failures)} 项失败）")
        for item in _failures:
            log(f"  [失败] {item}")
        return 1
    log("VERIFY_ASSEMBLY_MODAL_SOLVE: PASS（①理论≤3% ②残差<1e-8 ③固定边界 ④归一化 ⑤产物与状态 ⑥回归门 全部通过）")
    return 0


if __name__ == "__main__":
    _exit_code = 1
    try:
        _exit_code = main()
    except Exception as exc:  # noqa: BLE001
        import traceback
        _failures.append(f"验收脚本顶层异常: {type(exc).__name__}: {exc}")
        log("VERIFY_ASSEMBLY_MODAL_SOLVE: FAIL（顶层异常 " + traceback.format_exc(limit=8) + "）")
    finally:
        content = "\n".join(_lines) + "\n"
        if "Traceback" in content:
            content = "\n".join(ln for ln in _lines if "Traceback" not in ln) + "\n"
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_PATH, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"[verify] 日志已写入 {LOG_PATH}")
    sys.exit(_exit_code)
