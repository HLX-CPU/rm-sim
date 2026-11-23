# -*- coding: utf-8 -*-
"""位移控制滑块摩擦基准（模块52·调研§3）。

为什么：常规 μ=0.1 负例用「载荷控制 300N > μN=200N → 300s 不收敛/超时取消」作
滑移证据——调研 §3 指出单凭 failed/超时不能排除内存保护、非法输入、引擎故障等原因，
判别力弱且每轮固定占用 300s。本基准用**小型位移控制滑块试验**直接测库仑摩擦：

设计（标准滑块理想化，与接触门同一夹具/网格/deck 产品代码路径）：
- 几何/接触对：两叠块 20×20×20（verify_contact_friction_connection.fixture 同源，
  界面 y=20，PART1 底面固定——下块刚体自由度全约束）；
- **法向夹紧力可核对**：上块顶面 5MPa 均布压力 → N = 5MPa×400mm² = 2000N
  （无重力 → 底面 ΣRFy 解析精确 = −2000N，逐点核对 ±1%）；
- **非测试方向刚体自由度限制**：驱动侧面（PART2_SIDE, x=20）节点 u_z≡0（面外）
  且 u_x=d（测试方向，整面同值=刚性压板理想化）；法向由接触+压力持有；
  记录口径见本文件与 module_52 文档；
- **逐步施加切向位移**：位移程序 d（mm，几何级数跨越约 3.5 个量级），每个位移点
  独立单步求解（ccx 步内自动增量 0→d 斜坡）；F_t(d) = |底面 ΣRFx|；
  摩擦平台与加载历史无关（库仑），分点独立求解与连续增量等价（记录口径）；
- μ∈{0.1, 0.3} 两档 × 网格 {2.0, 4.0} 两档（判别力双网格验证）。

判据（验收标准①②③）：
- 粘着阶段：至少 1 个位移点 F_t/N < 0.7×μ（未达滑移条件的切向弹性段）；
- 滑移平台：末段连续 ≥2 点 |F_t/N − μ| ≤ 0.15μ，平台均值在 μ±15%；
- 两档可区分：μ=0.3 平台 / μ=0.1 平台 ≥ 2.0（名义 3.0，±15% 区间不交）；
- 平衡：每个位移点 |ΣRFx(底面) + ΣRFx(驱动面)| / F_t ≤ 0.5%；
- 法向：|ΣRFy(底面)| = N ±1%（夹紧力可核对）；
- 接触开闭：界面法向压力积分 = N ±10%（闭合传压）且最小面压 ≥0（只压不拉、
  未张开）；
- 日志状态：全部任务 Job finished 且 state=completed。

软件行为（调研§3：超时/取消/崩溃等用可控测试进程秒级验证）：run_lifecycle_
injections 用 C# 夹具 scheduler_probe（非求解器证据）注入——超时（等待超时→取消
→STATE_CANCELLED/stop_reason=cancelled）、运行中取消（秒级终态）、崩溃（未知作业
→exit 23→STATE_FAILED/『日志无 Job finished』）；每个注入秒级完成。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "verify") not in sys.path:
    sys.path.insert(0, str(ROOT / "verify"))
if str(ROOT / "app") not in sys.path:
    sys.path.insert(0, str(ROOT / "app"))

# 判据常数（验收标准①③口径）
CLAMP_PRESSURE_MPA = 5.0
N_NOMINAL = 2000.0          # 5MPa × 400mm²
DISP_PROGRAM = (1e-4, 5e-4, 3e-3, 2e-2, 0.1, 0.25)  # mm，测试方向位移程序
STICK_FACTOR = 0.7          # 粘着判据：F_t/N < 0.7×μ
PLATEAU_TOL = 0.15          # 平台判据：|F_t/N − μ| ≤ 0.15μ（验收标准①）
MIN_PLATEAU_POINTS = 2
EQ_TOL = 0.005              # 平衡残差 ≤0.5%（验收标准③）
NORMAL_TOL = 0.01           # 夹紧力可核对：±1%
PRESSURE_TOL = 0.10         # 界面法向压力积分 ±10%
MU_VALUES = (0.1, 0.3)
MESH_SIZES = (2.0, 4.0)
TIER_RATIO_MIN = 2.0        # 两档可区分：平台比 ≥2.0（名义 3.0，±15% 区间不交）


def _boundary_inject(base_deck: str, d: float) -> str:
    """在 *BOUNDARY 节注入驱动面位移（与接触门步骤⑤预定位移同款注入路径），
    并为驱动面追加 RF 打印（平衡核对需要驱动面反力；deck 默认只对固定面打印 RF）。"""
    deck = base_deck.replace(
        "*BOUNDARY\n",
        f"*BOUNDARY\nPART2_SIDE, 1, 1, {d}\nPART2_SIDE, 3, 3, 0\n", 1)
    if f"PART2_SIDE, 1, 1, {d}" not in deck:
        raise ValueError("位移边界注入失败（deck 缺 *BOUNDARY 节）")
    needle = "*NODE PRINT, NSET=PART1_FIXED\nRF\n"
    if needle in deck:
        deck = deck.replace(needle, needle + "*NODE PRINT, NSET=PART2_SIDE\nRF\n", 1)
    elif "*NODE PRINT, NSET=PART1_FIXED" in deck:
        deck = deck.replace("*NODE PRINT, NSET=PART1_FIXED",
                            "*NODE PRINT, NSET=PART1_FIXED\n*NODE PRINT, NSET=PART2_SIDE\nRF", 1)
    else:
        raise ValueError("deck 缺固定面 RF 打印锚点，无法注入驱动面 RF 打印")
    return deck


def run_benchmark(work_root: Path, mesh_size_mm: float = 2.0,
                  mu_values=MU_VALUES, log=print) -> dict:
    """跑一个网格的位移控制滑块基准；返回 {mu: 结果 dict}（含判据明细）。"""
    import verify_contact_friction_connection as gate
    from rm_sim.config import get_ccx_path, load_config
    from rm_sim.core.project import Constraint, LoadCase, PressureLoad
    from rm_sim.solver.deck import build_assembly_case_inp
    from memory_budget import make_scheduler

    work_root = Path(work_root)
    fx = gate.fixture(mesh_size_mm)
    if mesh_size_mm != 2.0:
        gate.fixture(2.0)  # 门辅助函数 _interface_pressure_integral 引用默认夹具登记键，先物化
    load_case = LoadCase(
        name=f"夹紧{CLAMP_PRESSURE_MPA}MPa位移滑块",
        constraints=[Constraint(name="固定", kind="fixed",
                                face_tag=int(fx["f_fixed"].tag), part_id="p1")],
        loads=[PressureLoad(name="夹紧", face_tag=int(fx["f_top"].tag), part_id="p2",
                            pressure_mpa=CLAMP_PRESSURE_MPA)],
    )
    fx["load_case_current"] = load_case
    jobs = []
    for mu in mu_values:
        project = gate._mk_project(mu, fx)
        base_deck = build_assembly_case_inp(fx["mesh_text"], project, load_case, fx["model"],
                                            nset_face_tags=fx["nset_face_tags"])
        for d in DISP_PROGRAM:
            jobs.append((mu, d, _boundary_inject(base_deck, d)))
    sched = make_scheduler(get_ccx_path(load_config()), max_workers=4)
    log(f"[基准] mesh={mesh_size_mm}mm：提交 {len(jobs)} 个位移点任务（μ={'/'.join(str(m) for m in mu_values)}，"
        f"d 程序={DISP_PROGRAM}，调度并发 4——各位移点独立求解，可并行）")
    tasks = []
    for i, (mu, d, deck) in enumerate(jobs):
        t = sched.submit(f"fb_{int(mesh_size_mm * 10):02d}_mu{int(mu * 10):02d}_d{i}",
                         deck, work_root / f"mu{int(mu * 10):02d}")
        tasks.append((mu, d, t))
    rows: dict = {mu: [] for mu in mu_values}
    pressure_task = {}
    started = time.monotonic()
    for mu, d, t in tasks:
        done = sched.wait_task(t.task_id, timeout=600)
        if done.state != "completed" or not done.job_finished:
            raise RuntimeError(f"基准任务未完成 mu={mu} d={d}: {done.summary()}")
        rf_base = done.results.rf_sum("PART1_FIXED")
        rf_drive = done.results.rf_sum("PART2_SIDE")
        rows[mu].append({"d": d, "base_rx": float(rf_base[0]), "base_ry": float(rf_base[1]),
                         "drive_rx": float(rf_drive[0]), "drive_ry": float(rf_drive[1]),
                         "task": done})
        if abs(d - DISP_PROGRAM[-1]) < 1e-15:
            pressure_task[mu] = (done, base_deck)
    log(f"[基准] mesh={mesh_size_mm}mm 全部 {len(jobs)} 点完成（{time.monotonic() - started:.0f}s）")

    node_ids = np.asarray(sorted(int(t) for t in np.asarray(fx["mesh"].node_tags).ravel()),
                          dtype=np.int64)
    out: dict = {}
    for mu in mu_values:
        pts = rows[mu]
        n_vals = [abs(p["base_ry"]) for p in pts]
        n_meas = float(np.mean(n_vals))
        for p in pts:
            p["N"] = n_meas
            p["F_t"] = abs(p["base_rx"])
            p["ratio"] = p["F_t"] / n_meas
            p["eq_res"] = abs(p["base_rx"] + p["drive_rx"]) / max(p["F_t"], 1e-30)
            p["normal_err"] = abs(n_meas - N_NOMINAL) / N_NOMINAL
        sticking = [(p["d"], p["ratio"]) for p in pts if p["ratio"] < STICK_FACTOR * mu]
        plateau_pts = []
        for p in reversed(pts):
            if abs(p["ratio"] - mu) <= PLATEAU_TOL * mu:
                plateau_pts.insert(0, p)
            else:
                break
        plateau_ratio = float(np.mean([p["ratio"] for p in plateau_pts])) if plateau_pts else float("nan")
        max_eq = max(p["eq_res"] for p in pts)
        done, deck_text = pressure_task[mu]
        p_int, n_faces, p_min = gate._interface_pressure_integral(done, deck_text, node_ids)
        out[mu] = {"N_measured": n_meas, "points": pts, "sticking": sticking,
                   "plateau_points": len(plateau_pts), "plateau_ratio": plateau_ratio,
                   "max_eq_residual": max_eq, "pressure_integral": p_int,
                   "pressure_faces": n_faces, "min_pressure": p_min,
                   "job_finished": all(p["task"].job_finished for p in pts)}
        log(f"[基准] μ={mu}: N(实测底面ΣRFy)={n_meas:.4f}N（解析 {N_NOMINAL:.0f}N, "
            f"差 {abs(n_meas - N_NOMINAL) / N_NOMINAL:.2e}）；F_t/N 序列="
            + " ".join(f"{p['ratio']:.3f}@{p['d']:g}" for p in pts)
            + f"；粘着点={len(sticking)} 平台点={len(plateau_pts)} 平台均值={plateau_ratio:.4f}"
              f"（μ±15% 判据），最大平衡残差={max_eq:.2e}，界面压力积分={p_int:.2f}N/"
              f"{n_faces}面（最小面压 {p_min:.4f}MPa）")
    return out


def check_benchmark(results: dict, failures: list, log=print, tag: str = "") -> None:
    """验收标准①③的单网格判据核对（两档可区分也在内）；两网格一致性由调用方汇总。"""
    plateaus = {}
    for mu, r in results.items():
        if not r["job_finished"]:
            failures.append(f"{tag} μ={mu} 有任务未 Job finished")
        if len(r["sticking"]) < 1:
            failures.append(f"{tag} μ={mu} 未观察到粘着阶段（F_t/N < {STICK_FACTOR}×μ 无一点）")
        if r["plateau_points"] < MIN_PLATEAU_POINTS:
            failures.append(f"{tag} μ={mu} 滑移平台点不足（<{MIN_PLATEAU_POINTS}）")
        dev = abs(r["plateau_ratio"] - mu) / mu
        if dev > PLATEAU_TOL:
            failures.append(f"{tag} μ={mu} 平台均值 {r['plateau_ratio']:.4f} 偏离 μ 超 ±15%（{dev:.1%}）")
        if r["max_eq_residual"] > EQ_TOL:
            failures.append(f"{tag} μ={mu} 平衡残差 {r['max_eq_residual']:.2e} > 0.5%")
        if abs(r["N_measured"] - N_NOMINAL) / N_NOMINAL > NORMAL_TOL:
            failures.append(f"{tag} μ={mu} 实测法向力 {r['N_measured']:.3f}N 偏离解析 {N_NOMINAL}N 超 1%")
        if abs(r["pressure_integral"] - r["N_measured"]) > PRESSURE_TOL * r["N_measured"]:
            failures.append(f"{tag} μ={mu} 界面法向压力积分 {r['pressure_integral']:.2f}N 偏离 N 超 10%")
        if r["min_pressure"] < 0.0:
            failures.append(f"{tag} μ={mu} 界面出现负压力（张开传拉）: {r['min_pressure']:.4f}MPa")
        plateaus[mu] = r["plateau_ratio"]
        log(f"[{tag}] μ={mu} 判据核对：平台均值={r['plateau_ratio']:.4f}（μ±15% 内）、"
            f"粘着点={len(r['sticking'])}、平台点={r['plateau_points']}、平衡残差≤{r['max_eq_residual']:.2e}、"
            f"N={r['N_measured']:.3f}N、界面压力积分={r['pressure_integral']:.2f}N（min p={r['min_pressure']:.4f}MPa）")
    # 核验员缺陷修复：min/max 必须取实测平台均值（dict 值）而非名义 μ 键——
    # 原写法 hi/lo 恒为 3.00（名义比），判别力断言恒真形同虚设
    lo, hi = min(plateaus.values()), max(plateaus.values())
    if hi <= 0 or lo <= 0 or hi / lo < TIER_RATIO_MIN:
        failures.append(f"{tag} 两档 μ 平台不可区分: {plateaus}（要求比值 ≥{TIER_RATIO_MIN}）")
    else:
        # 注意：前缀与数值格式须保持与 verify_friction_benchmark.TIER_LINE 正则兼容
        log(f"[{tag}] 两档可区分：平台比 {hi:.4f}/{lo:.4f} = {hi / lo:.2f} ≥ {TIER_RATIO_MIN}"
            f"（实测均值口径；名义比 3.00）")


def run_lifecycle_injections(work: Path, log=print) -> dict:
    """调研§3：超时/取消/崩溃等软件行为用可控注入进程秒级验证（C# 夹具，非求解器证据）。"""
    from verify_memory_budget_guard import build_probe  # 复用模块46 的夹具编译（含其日志路径约定）
    from rm_sim.solver.tasks import (STATE_CANCELLED, STATE_FAILED, SolveScheduler)

    work = Path(work)
    binary = build_probe(work / "probe")
    sched = SolveScheduler(binary, max_workers=1)
    out = {}
    t0 = time.monotonic()
    # ① 超时：等待超时 → 取消 → cancelled/stop_reason=cancelled
    t = sched.submit("stall_at_cap", "fixture, not a solver deck", work / "cases_timeout")
    try:
        sched.wait_task(t.task_id, timeout=4.0)
        raise AssertionError("stall 注入任务在 4s 内未超时（夹具未挂起？）")
    except TimeoutError:
        sched.cancel(t.task_id)
        done = sched.wait_task(t.task_id, timeout=30)
        if done.state != STATE_CANCELLED or done.stop_reason != "cancelled":
            raise AssertionError(f"超时→取消分类不符: {done.summary()}")
        out["timeout"] = done
        log(f"[注入·超时] PASS 等待 4s 超时→取消：state={done.state} "
            f"stop_reason={done.stop_reason}（{time.monotonic() - t0:.1f}s）")
    # ② 运行中取消：提交后立即取消（排队/运行皆秒级终态）
    t = sched.submit("stall_at_cap", "fixture, not a solver deck", work / "cases_cancel")
    sched.cancel(t.task_id)
    done = sched.wait_task(t.task_id, timeout=30)
    if done.state != STATE_CANCELLED:
        raise AssertionError(f"运行中取消分类不符: {done.summary()}")
    out["cancel"] = done
    log(f"[注入·取消] PASS 提交即取消：state={done.state}（累计 {time.monotonic() - t0:.1f}s）")
    # ③ 崩溃：未知作业 → 夹具 exit 23 → failed（日志无 Job finished 分类）
    t = sched.submit("crash_probe_23", "fixture, not a solver deck", work / "cases_crash")
    done = sched.wait_task(t.task_id, timeout=60)
    if done.state != STATE_FAILED or int(done.exit_code or 0) != 23 \
            or "Job finished" not in (done.error or ""):
        raise AssertionError(f"崩溃分类不符: {done.summary()}")
    out["crash"] = done
    log(f"[注入·崩溃] PASS 未知作业 exit 23：state={done.state} exit={done.exit_code} "
        f"error={done.error!r}（累计 {time.monotonic() - t0:.1f}s）")
    out["elapsed_s"] = time.monotonic() - t0
    return out
