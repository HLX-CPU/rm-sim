# -*- coding: utf-8 -*-
"""整装原始结果复核：力/力矩载荷积分调用产品模块，位移和连接读取原始产物。
独立64点自重积分对照见 probes/probe_quadratic_gravity_balance.py。

不重新求解、不改原始产物；只读 case 目录。用于回答「整装到底解出来了没有、
反力和力矩是否平衡、有没有部件飞掉、连接有没有未绑定」。

用法：
  python verify/verify_full_assembly_solve.py <case_dir> [--job assembly_full] [--out 记录.json]
  python verify/verify_full_assembly_solve.py            # 无参自检（模块45 修复「编排无参调用」）

无参自检模式：自动定位最近整装归档 case（E:/rm_sim/verification 下 case 目录，
须 <job>.inp/.dat/.frd/.log/.vtu 齐全、Job finished、*DLOAD GRAV、ELSET=PART<n>）；
无归档时现场跑一次最小两块装配（界面粘接+底面固定+自重）真实求解再复核；
复核门按任务书口径收紧为 ΣF/ΣM ≤0.5%；复核记录自动落证据目录。
无可用 case 且最小求解也失败时以非零退出如实报告，不虚报。
带参模式行为与修复前完全一致（默认筛查阈值 2%/1% 不变）。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))


def _blocks(text: str) -> dict:
    """*MATERIAL/*DENSITY/*SOLID SECTION/*DLOAD 的极简解析（够本复核用）。"""
    lines = text.splitlines()
    densities: dict[str, float] = {}
    part_material: dict[str, str] = {}
    gravity = None
    current = None
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.upper().startswith("*MATERIAL"):
            match = re.search(r"NAME\s*=\s*([^,\s]+)", line, re.IGNORECASE)
            current = match.group(1) if match else None
        elif line.upper().startswith("*DENSITY"):
            j = i + 1
            while j < len(lines) and (not lines[j].strip() or lines[j].lstrip().startswith("*")):
                j += 1
            if j < len(lines) and current:
                densities[current] = float(lines[j].split(",")[0])
        elif line.upper().startswith("*SOLID SECTION"):
            elset = re.search(r"ELSET\s*=\s*([^,\s]+)", line, re.IGNORECASE)
            material = re.search(r"MATERIAL\s*=\s*([^,\s]+)", line, re.IGNORECASE)
            if elset and material:
                part_material[elset.group(1)] = material.group(1)
        elif line.upper().startswith("*DLOAD"):
            j = i + 1
            while j < len(lines):
                raw = lines[j].strip()
                if not raw or raw.startswith("**"):
                    j += 1
                    continue
                parts = [p.strip() for p in raw.split(",")]
                if len(parts) >= 3 and parts[1].upper() == "GRAV":
                    gravity = (float(parts[2]), [float(v) for v in parts[3:6]])
                    break
                j += 1
        i += 1
    return {"densities": densities, "part_material": part_material, "gravity": gravity}


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser()
    parser.add_argument("case_dir", nargs="?", default=None,
                        help="整装 case 目录；缺省进入无参自检模式（自动定位/现做，见模块45）")
    parser.add_argument("--job", default="assembly_full")
    parser.add_argument("--out", default="")
    parser.add_argument("--max-warning-ratio", type=float, default=0.02,
                        help="修正后力平衡残差/外力合力 的筛查阈值，默认2%；不是连接或强度验收门。")
    parser.add_argument("--max-moment-ratio", type=float, default=0.01,
                        help="力矩残差/(总重×包围盒对角线) 的允许比")
    parser.add_argument("--max-untied-ratio", type=float, default=0.005,
                        help="未绑定从面节点/从面节点总数 的允许比。默认 0.5%："
                             "绑定带的节点级投影用平三角/二次子三角形近似，"
                             "与 CCX 的二次面/法向判据在贴合区边界会有少量差异；"
                             "超过这个比例说明不是边界数值差异，必须查连接。")
    args = parser.parse_args()

    auto_note = None
    if args.case_dir is None:
        # 无参自检模式（模块45）：自动定位最近整装归档 case；无归档时现场跑一次
        # 最小两块装配（自重）求解再复核。复核门按任务书口径收紧为 ΣF/ΣM ≤0.5%。
        args.max_warning_ratio = 0.005
        args.max_moment_ratio = 0.005
        sys.path.insert(0, str(ROOT / "verify"))
        try:
            from orchestration_cases import (ensure_minimal_assembly_case,
                                             find_latest_assembly_case, new_evidence_dir)
        except ImportError as exc:  # 共享件缺失属安装错误，如实非零退出
            print(f"VERIFY_FULL_ASSEMBLY_SOLVE: FAIL（无参自检共享件缺失：{exc}）")
            return 1
        located = find_latest_assembly_case()
        if located is not None:
            args.case_dir, args.job = str(located[0]), located[1]
            auto_note = (f"[无参自检] 自动定位最近整装归档 case_dir={args.case_dir}"
                         f"（job={args.job}）；复核门收紧为 ΣF/ΣM ≤0.5%")
        else:
            print("[无参自检] 未找到整装归档 case，现场求解一次最小两块装配（自重）……", flush=True)
            try:
                case_dir, job, _solved = ensure_minimal_assembly_case(
                    new_evidence_dir("assembly_solve_noarg"))
            except Exception as exc:  # noqa: BLE001 - 诚实失败：不虚报
                print(f"VERIFY_FULL_ASSEMBLY_SOLVE: FAIL（无可用归档 case，且最小两块装配"
                      f"求解也失败：{type(exc).__name__}: {exc}）")
                return 1
            args.case_dir, args.job = str(case_dir), job
            auto_note = (f"[无参自检] 已现场求解最小两块装配 case_dir={args.case_dir}"
                         f"（job={args.job}）；复核门 ΣF/ΣM ≤0.5%")
        if not args.out:
            from assembly_check_utils import evidence_dir
            args.out = str(evidence_dir("full_assembly_solve_noarg") / "review.json")
        print(auto_note, flush=True)

    from rm_sim.solver.deck import parse_mesh_inp
    from rm_sim.solver.results import load_results
    from rm_sim.solver.fields import build_result_fields
    from rm_sim.solver.diagnostics import collect_tie_diagnostics
    from rm_sim.solver.nodal_loads import evaluate_nodal_loads

    case = Path(args.case_dir)
    inp = case / f"{args.job}.inp"
    dat = case / f"{args.job}.dat"
    frd = case / f"{args.job}.frd"
    log = case / f"{args.job}.log"
    for path in (inp, dat, frd, log):
        assert path.is_file() and path.stat().st_size > 0, f"缺少或空文件：{path}"

    text = inp.read_text(encoding="utf-8")
    mesh = parse_mesh_inp(text)
    parsed = _blocks(text)
    gravity = parsed["gravity"]
    assert gravity is not None, "deck 里没有 GRAV 重力载荷"
    magnitude, direction = gravity
    direction = np.asarray(direction, dtype=float)
    direction = direction / np.linalg.norm(direction)

    # 每个 PARTn 的密度（按 *SOLID SECTION 的 ELSET→MATERIAL→*DENSITY）
    rho_of_part: dict[str, float] = {}
    for part, material in parsed["part_material"].items():
        if material in parsed["densities"]:
            rho_of_part[part] = parsed["densities"][material]
    assert rho_of_part, "没有解析到材料密度"

    results = load_results(dat, frd)
    fixed_names = [m.strip() for m in re.findall(r"\*NODE PRINT, NSET=([^\n]+)\nRF", text)]
    assert fixed_names, "deck 里没有 RF 输出的固定节点集"
    rf_block: dict[int, np.ndarray] = {}
    for name in fixed_names:
        rf_block.update({int(k): np.asarray(v, dtype=float) for k, v in results.rf_block(name).items()})
    assert rf_block, ".dat 里没有 RF 块（可能未收敛或输出被截断）"
    rf_nodes = sorted(rf_block)
    rf = np.asarray([rf_block[n] for n in rf_nodes], dtype=float)
    rf_sum = rf.sum(axis=0)

    raw_rf_sum = rf_sum.copy()
    loads = evaluate_nodal_loads(text,mesh,rf_nodes)
    rf = np.asarray([rf_block[n]-loads['nodes'][n] for n in rf_nodes])
    rf_sum = rf.sum(axis=0)
    applied = np.asarray(loads['applied_total_n'])
    weight = float(np.linalg.norm(applied))
    volumes = {name:None for name in mesh['elsets'] if re.fullmatch(r'PART\d+',name)}
    all_nodes = np.asarray([mesh['nodes'][n] for n in sorted(mesh['nodes'])])
    r0 = all_nodes.mean(axis=0)
    moment_gravity = np.asarray(loads['applied_moment_origin_nmm'])-np.cross(r0,applied)
    force_residual = float(np.linalg.norm(rf_sum+applied))
    force_ratio = force_residual/weight if weight else float('inf')

    rf_positions = np.asarray([mesh["nodes"][n] for n in rf_nodes], dtype=float)
    moment_reaction = np.cross(rf_positions - r0, rf).sum(axis=0)
    moment_total = moment_reaction + moment_gravity
    extent = float(np.linalg.norm(all_nodes.max(axis=0) - all_nodes.min(axis=0)))
    moment_ratio = float(np.linalg.norm(moment_total)) / (weight * extent) if weight else float("inf")

    # Read existing numeric artifacts only: no FRD conversion in the source directory.
    increments = sorted((p for p in case.glob(args.job+'.*.vtu') if p.stem.rsplit('.',1)[-1].isdigit()),
                        key=lambda p:int(p.stem.rsplit('.',1)[-1]))
    vtu = increments[-1] if increments else case/(args.job+'.vtu')
    assert vtu.is_file(), '缺少已有VTU；此只读检查不在源目录转换结果'
    fields = build_result_fields(sorted(mesh['nodes']),results,frd,fixed_set=fixed_names,
                                 vtu_path=vtu,inp_text=text,mesh=mesh)
    u = np.asarray(fields.u, dtype=float)
    u_mag = np.linalg.norm(u, axis=1)
    max_u = float(u_mag.max())
    finite = bool(np.isfinite(u).all())
    per_part = {}
    node_index = {n: i for i, n in enumerate(fields.node_ids)}
    for part, volume in volumes.items():
        ids = set()
        for eid in mesh["elsets"][part]:
            ids.update(int(n) for n in mesh["elems"][eid])
        idx = [node_index[n] for n in ids if n in node_index]
        if idx:
            per_part[part] = {"nodes": len(idx), "max_u_mm": float(u_mag[idx].max()),
                              "mean_u_mm": float(u_mag[idx].mean())}

    diagnostics = collect_tie_diagnostics(case, args.job, mesh=mesh)
    # 从面节点总数（绑定带的 TYPE=NODE 集合）：用于判断未绑定比例是否只是边界差异。
    slave_nodes: set[int] = set()
    for name, ids in mesh["nsets"].items():
        if name.endswith("_SAT"):
            slave_nodes.update(int(n) for n in ids)
    untied_ratio = (diagnostics["untied_nodes"] / len(slave_nodes)) if slave_nodes else 0.0
    log_text = log.read_text(encoding="utf-8", errors="replace")
    job_finished = "Job finished" in log_text
    error_count = diagnostics["solver_errors"]["count"]

    record = {
        "case_dir": str(case), "job": args.job,
        "inp_sha256": __import__("hashlib").sha256(inp.read_bytes()).hexdigest(),
        "dat_sha256": __import__("hashlib").sha256(dat.read_bytes()).hexdigest(),
        "frd_bytes": frd.stat().st_size,
        "job_finished": job_finished, "solver_errors": error_count,
        "reported_missed_nodes": diagnostics["reported_missed_nodes"],
        "untied_nodes": diagnostics["untied_nodes"],
        "slave_nodes_total": len(slave_nodes),
        "untied_ratio": untied_ratio,
        "warning_kinds": diagnostics["warning_kinds"],
        "gravity": {"magnitude_mm_s2": magnitude, "direction": direction.tolist()},
        "applied_total_n": applied.tolist(),
        "applied_resultant_magnitude_n": weight,
        "raw_rf_sum_n": raw_rf_sum.tolist(),
        "reaction_processing": fields.source['reaction'],
        "requires_review": diagnostics['requires_review'],
        "scope": "数值筛查；载荷积分调用产品模块，不构成独立载荷验证或结构合格结论",
        "rf_sum_n": rf_sum.tolist(),
        "force_residual_n": force_residual, "force_ratio": force_ratio,
        "moment_about_centroid_nmm": moment_total.tolist(),
        "moment_ratio": moment_ratio,
        "extent_mm": extent,
        "max_u_mm": max_u, "displacements_finite": finite,
        "per_part": per_part,
        "fixed_sets": fixed_names,
        "checks": {
            "job_finished": job_finished,
            "no_solver_error": error_count == 0,
            "untied_within_tolerance": untied_ratio <= args.max_untied_ratio,
            "force_balance": force_ratio <= args.max_warning_ratio,
            "moment_balance": moment_ratio <= args.max_moment_ratio,
            "displacements_finite": finite,
            "no_runaway_part": all(v["max_u_mm"] < 10.0 * max(max_u, 1e-9)
                                   for v in per_part.values()) and max_u < 10.0,
        },
    }
    ok = all(record["checks"].values())
    record["ok"] = ok
    print(f"case={case.name} job_finished={job_finished} ERROR={error_count} "
          f"未绑定={diagnostics['untied_nodes']}")
    print(f"外力合力模={weight:.6g} N  支反力={np.round(rf_sum, 6).tolist()}  "
          f"残差={force_residual:.3g} N（{100*force_ratio:.3f}%）")
    print(f"力矩残差={np.round(moment_total, 6).tolist()} N·mm "
          f"（相对 {100*moment_ratio:.3f}% of W×{extent:.1f}mm）")
    print(f"max|U|={max_u:.6g} mm；各部件 max|U| 前 5：")
    for part, info in sorted(per_part.items(), key=lambda kv: -kv[1]["max_u_mm"])[:5]:
        print(f"  {part}: {info['max_u_mm']:.6g} mm（{info['nodes']} 节点）")
    print("检查：" + json.dumps(record["checks"], ensure_ascii=False))
    print("连接仍待检查：", diagnostics["requires_review"], "；筛查通过不代表结构合格")
    print("VERIFY_FULL_ASSEMBLY_SOLVE:", "PASS" if ok else "FAIL")
    if args.out:
        Path(args.out).write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
