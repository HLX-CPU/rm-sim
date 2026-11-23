# -*- coding: utf-8 -*-
"""验收脚本：修复「内存守卫截停的五门静力回归」（模块46）。exit 0 = 通过；日志 → verify/logs/。

2026-11-03 增量（验收提速·套件标签，不改本验收任何门与判据）：每轮每门耗时分解
（timing_breakdown.json/verification.json + 日志 §2 表同口径打印）、轮墙钟 wall_seconds、
逐门套件标签标注（五门=real_small，suite_labels 口径，只作报告与选择）。

背景：2026-11-02 22:23-22:27 基线（module_44 盘点③）五门静力回归
（static_solve / assembly_results / bonded_connection / bolt_pin_connection /
contact_friction_connection）被 tasks.py 内存预算守卫截停
（stop_reason=memory_limit / memory_limit_near_exit → STATE_FAILED）或 ccx
0xC0000005——根因是旧动态预算 = 0.6×当时可用内存/并发，系统内存被其他进程挤压
时预算缩水（资源压力，非力学回归：同规模求解在 20:27-20:56 全 PASS）。

修复（本验收的对象）：
① 预算分级 tasks.resolve_memory_limit_bytes：小算例固定足额（4GiB/任务）不随
  挤压缩水、大网格档上探（10GiB 实测锚）受可用内存 80% 封顶、OMP>2 每线程
  +25% 谨慎余量；默认行为（memory_limit_bytes=None）不变。
② 五门算例规模参数化（verify/memory_budget.py::scale_tier，RM_SIM_SCALE）：
  默认档跑得过回归机；大网格档走 --slow——bonded ⑤ 1mm 收敛档（145613 节点）
  与 bolt_pin 1.0mm 原始档（95433 节点）在 large 档执行，默认档 bonded=2mm+
  共节点、bolt_pin=1.5mm（Ux 锚 ±10% 内）。
③ 守卫语义单测：注入峰值 ≥98% 限额 → STATE_FAILED 且 error 含『内存预算』；
  限额内真实求解 → 正常完成不触发。

覆盖验收标准：
- ① 守卫单测（C# fixture 注入 + 真实 ccx 限额内完成）；
- ② 五门各自单跑 exit 0 + 逐门理论锚核对（round 1）：
    static_solve 悬臂 δ=PL³/3EI=0.1904762mm（60MPa Mises 锚由其配套门
    verify_results_view 实测 σ=Mc/I=60MPa 相对误差 ≤10% 承载——static_solve
    本身无 Mises 门，如实分工）；
    bonded_connection ΣRF=1000N（脚本门 0.01% ≤ 0.5% 口径）；
    bolt_pin_connection 预紧力路径反力=预紧值（±1% 门）+ Ux=9.5238e-4mm±10%；
    contact_friction_connection 界面法向压力积分（只压不拉）+ 粘着切向 u_x 门；
    assembly_results 单部件应力与整体解同源量级（per-part max Mises 门）；
- ③ 五门连续重跑 3 轮（轮内五门并发进程——串行全程约 24-28 分钟超过 20 分钟
  运行窗，第 1 次修复反馈 world.run timeout 1200000ms；轮次连续、门内检查与
  判据不变，合计峰值 ≈6.6GiB 远低于宿主可用，并发恰好复现基线内存压力场景）：
  无 0xC0000005（exit≠3221225477 且输出无崩溃标记），每轮每门记录退出码与
  峰值内存（[memory] 行）到证据目录。
"""
from __future__ import annotations

import datetime
import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "verify"))
sys.path.insert(0, str(ROOT / "app"))
import suite_labels  # 套件标签标注与耗时分解口径（调研 §2/§4；只作报告，不改门与判据）
LOG_PATH = ROOT / "verify" / "logs" / "verify_memory_budget_guard.log"
_LOG_LOCK = threading.Lock()  # 并发轮内多线程写同一日志
CRASH_EXIT = 3221225477  # 0xC0000005

GATES = [
    ("static_solve", ROOT / "verify" / "verify_static_solve.py"),
    ("assembly_results", ROOT / "verify" / "verify_assembly_results.py"),
    ("bonded_connection", ROOT / "verify" / "verify_bonded_connection.py"),
    ("bolt_pin_connection", ROOT / "verify" / "verify_bolt_pin_connection.py"),
    ("contact_friction_connection", ROOT / "verify" / "verify_contact_friction_connection.py"),
]
ROUND_TIMEOUT_S = 900

_log_handle = None


def log(message: str) -> None:
    global _log_handle
    if _log_handle is None:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        _log_handle = LOG_PATH.open("a", encoding="utf-8")
        _log_handle.write(f"\n===== 运行 {datetime.datetime.now().isoformat(timespec='seconds')} =====\n")
    with _LOG_LOCK:
        _log_handle.write(message + "\n")
        _log_handle.flush()
    print(message, flush=True)


def _fmt(bytes_value) -> str:
    return "unlimited" if bytes_value is None else f"{float(bytes_value) / 1024 ** 3:.2f}GiB"


def run_gate(script: Path, env_extra: dict | None = None, timeout: int = ROUND_TIMEOUT_S):
    env = dict(os.environ)
    env.update({"PYTHONUTF8": "1", "OMP_NUM_THREADS": "2",
                "TEMP": "E:/rm_sim/temp", "TMP": "E:/rm_sim/temp",
                "QT_QPA_PLATFORM": "offscreen"})
    if env_extra:
        env.update(env_extra)
    cmd = [sys.executable, "-X", "utf8", "-B", str(script)]
    log(f"[run] {script.name}")
    started = time.monotonic()
    proc = subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True, timeout=timeout)
    seconds = time.monotonic() - started
    stdout = proc.stdout.decode("utf-8", errors="replace").replace("\r\n", "\n")
    stderr = proc.stderr.decode("utf-8", errors="replace")
    peaks = [line for line in stdout.splitlines() if line.startswith("[memory]")]
    for line in peaks:
        log("      " + line)
    log(f"      exit={proc.returncode} seconds={seconds:.1f}s")
    return {"exit_code": proc.returncode, "seconds": round(seconds, 1),
            "stdout": stdout, "stderr": stderr[-2000:], "peaks": peaks}




def run_round_parallel(rnd: int, work: Path) -> dict:
    """一轮 = 五门并发进程，轮与轮连续（round N+1 等全部 round N 终态）。

    编排理由（第 1 次修复反馈）：串行全程 ≈24-28 分钟，超过运行窗 1200000ms
    （20 分钟）——world.run 超时不是实现门失败，而是验收脚本自身的串行设计缺陷。
    五门相互独立（独立进程/日志/临时目录），并发不改变任何门内检查与判据；
    合计实测峰值 ≈6.6GiB，远低于宿主可用，且并发负载恰好复现基线失败的
    内存压力场景——预算分级修复必须在并发下依然成立（更强的稳定性证据）。
    """
    results: dict = {}
    threads = []
    round_started = time.monotonic()  # 轮墙钟（新增报告字段；含错峰启动与收尾，不改编排）

    def worker(gate: str, script: Path):
        try:
            results[gate] = run_gate(script)
        except subprocess.TimeoutExpired:
            results[gate] = {"exit_code": "TIMEOUT", "seconds": ROUND_TIMEOUT_S,
                             "stdout": "", "stderr": f"gate timed out after {ROUND_TIMEOUT_S}s",
                             "peaks": []}
        except Exception as exc:  # noqa: BLE001
            results[gate] = {"exit_code": "LAUNCH_ERROR", "seconds": 0,
                             "stdout": "", "stderr": f"{type(exc).__name__}: {exc}", "peaks": []}

    log(f"\n===== ③ 第 {rnd}/3 轮（五门并发进程，轮次连续） =====")
    for gate, script in GATES:
        thread = threading.Thread(target=worker, args=(gate, script), name=f"gate-{gate}")
        threads.append(thread)
        thread.start()
        time.sleep(1.0)  # 错峰启动，避免同时进程创建抖动
    for thread in threads:
        thread.join(timeout=ROUND_TIMEOUT_S + 120)
    for gate, _script in GATES:
        if gate not in results:
            failures_placeholder = f"round{rnd} {gate} 线程未在时限内结束"
            results[gate] = {"exit_code": "NO_JOIN", "seconds": ROUND_TIMEOUT_S + 120,
                             "stdout": "", "stderr": failures_placeholder, "peaks": []}
    return results, round(time.monotonic() - round_started, 2)


def evaluate_round(rnd: int, results: dict, work: Path, failures: list[str],
                   wall_seconds: float | None = None) -> dict:
    round_record: dict = {"round": rnd,
                          # 新增报告字段（调研 §2 口径：每轮每门秒数；轮墙钟=五门并发全程）
                          "wall_seconds": wall_seconds,
                          "gates": {}}
    for gate, _script in GATES:
        entry = results[gate]
        crashes = (entry["exit_code"] == CRASH_EXIT
                   or "0xC0000005" in entry["stdout"]
                   or "0xC0000005" in entry["stderr"]
                   or "C0000005" in entry["stderr"])
        entry["crash_suspect"] = bool(crashes)
        entry["peaks_parsed"] = []
        for line in entry["peaks"]:
            match = re.search(r"peak=([0-9.]+)GiB", line)
            if match and "unlimited" not in line:
                entry["peaks_parsed"].append(float(match.group(1)))
        round_record["gates"][gate] = {
            "exit_code": entry["exit_code"], "seconds": entry["seconds"],
            "label": suite_labels.label_of(gate),  # 新增：套件标签标注（只作报告）
            "crash_suspect": entry["crash_suspect"],
            "peaks_gib": entry["peaks_parsed"],
            "memory_lines": entry["peaks"],
        }
        (work / f"round{rnd}_{gate}_stdout.log").write_text(
            entry["stdout"] + "\n[stderr]\n" + entry["stderr"], encoding="utf-8")
        if entry["exit_code"] != 0:
            failures.append(f"③ round{rnd} {gate} exit={entry['exit_code']}"
                            + (f"；stderr 尾部: {entry['stderr'][-300:]}" if entry["stderr"] else ""))
        if crashes:
            failures.append(f"③ round{rnd} {gate} 疑似 0xC0000005 崩溃")
        log(f"[③] round{rnd} {gate} [{suite_labels.label_of(gate)}]: exit={entry['exit_code']} "
            f"峰值={entry['peaks_parsed'] or '见 [memory] 行'} "
            f"crash_suspect={entry['crash_suspect']}")
    return round_record


def timing_breakdown(rounds: list[dict]) -> dict:
    """每轮每门耗时分解（调研 §2 表同口径）+ 每门合计/均值 + 轮墙钟；只作报告。"""
    gate_names = [g for g, _ in GATES]
    matrix = {f"round{r['round']}": {g: r["gates"][g]["seconds"] for g in gate_names}
              for r in rounds}
    walls = {f"round{r['round']}": r.get("wall_seconds") for r in rounds}
    per_gate = {}
    for g in gate_names:
        values = [float(r["gates"][g]["seconds"]) for r in rounds]
        per_gate[g] = {"label": suite_labels.label_of(g),
                       "total": round(sum(values), 1),
                       "mean": round(sum(values) / len(values), 1)}
    return {"口径": "seconds=run_gate 内 time.monotonic 对门子进程的 wall 计时；"
                    "round_wall_seconds=该轮五门并发全程（含 1s×5 错峰启动与收尾）——对照调研报告 §2 表",
            "labels": {g: suite_labels.label_of(g) for g in gate_names},
            "matrix_seconds": matrix,
            "round_wall_seconds": walls,
            "per_gate": per_gate}


def print_timing_table(breakdown: dict) -> None:
    gate_names = list(breakdown["labels"])
    log("TIMING 每轮每门秒数（调研 §2 同口径）：")
    log("TIMING 轮次 | " + " | ".join(gate_names) + " | 轮wall")
    for round_name, row in breakdown["matrix_seconds"].items():
        cells = " | ".join(f"{row[g]:.1f}" for g in gate_names)
        wall = breakdown["round_wall_seconds"].get(round_name)
        wall_text = f"{wall:.1f}" if isinstance(wall, (int, float)) else "n/a"
        log(f"TIMING {round_name} | {cells} | {wall_text}")
    log("TIMING 合计/均值 | " + " | ".join(
        f"{breakdown['per_gate'][g]['total']:.1f}/{breakdown['per_gate'][g]['mean']:.1f}"
        for g in gate_names))


# ---------------------------------------------------------------- ① 守卫语义单测
def build_probe(work: Path) -> Path:
    """编译 C# 生命周期夹具（非求解器证据；与 verify_scheduler_lifecycle 同源）。"""
    binary = work / "scheduler_probe.exe"
    binary.parent.mkdir(parents=True, exist_ok=True)
    compiler = Path("C:/Windows/Microsoft.NET/Framework64/v4.0.30319/csc.exe")
    build = subprocess.run([str(compiler), "/nologo", "/target:exe", f"/out:{binary}",
                            str(ROOT / "verify" / "fixtures" / "scheduler_probe.cs")],
                           capture_output=True)
    (work / "build.log").write_bytes(build.stdout + build.stderr)
    assert build.returncode == 0, build.stdout + build.stderr
    return binary


def guard_unit_tests(work: Path, failures: list[str]) -> None:
    from rm_sim.config import get_ccx_path, load_config
    from rm_sim.solver.tasks import (STATE_COMPLETED, STATE_FAILED, SolveScheduler,
                                     resolve_memory_limit_bytes)

    binary = build_probe(work / "probe")
    # --- 注入：峰值 ≥98% 限额 → STATE_FAILED + error 含『内存预算』 ---
    scheduler = SolveScheduler(binary, max_workers=1, memory_limit_bytes=192 * 1024 ** 2)
    task = scheduler.submit("stall_at_cap", "fixture, not a solver deck", work / "cases")
    task = scheduler.wait_task(task.task_id, timeout=60)
    usage = task.resource_usage or {}
    kernel_limit = usage.get("job_memory_limit_bytes")
    peak = usage.get("peak_job_memory_bytes", 0)
    ok_a = (task.state == STATE_FAILED and task.stop_reason == "memory_limit"
            and "内存预算" in task.error and kernel_limit and peak >= 0.98 * kernel_limit)
    record_a = {"state": task.state, "stop_reason": task.stop_reason,
                "error": task.error, "peak_job_memory_bytes": peak,
                "job_memory_limit_bytes": kernel_limit}
    (work / "guard_injection.json").write_text(
        json.dumps(record_a, ensure_ascii=False, indent=2), encoding="utf-8")
    if ok_a:
        log(f"[①a] PASS 注入峰值 {_fmt(peak)} ≥ 98%×限额 {_fmt(kernel_limit)} → "
            f"state={task.state} stop_reason=memory_limit，error 含『内存预算』")
    else:
        failures.append(f"①a 守卫注入单测未达标: {record_a}")

    # --- 限额内真实求解：正常完成，守卫不触发 ---
    fixture_deck = Path("E:/rm_sim/verification/orchestration_fixture_2block_grav/build/cases/case_001/assembly_full.inp")
    if not fixture_deck.is_file():
        from orchestration_cases import ensure_minimal_assembly_case
        case, _job, _solved = ensure_minimal_assembly_case(
            Path("E:/rm_sim/verification/orchestration_fixture_2block_grav"))
        fixture_deck = case / "assembly_full.inp"
    limit = resolve_memory_limit_bytes(1, tier="small", available_gb=16.0)  # 固定 4GiB，确定性强
    real = SolveScheduler(get_ccx_path(load_config()), max_workers=1, memory_limit_bytes=limit)
    task = real.submit_file("guard_within", fixture_deck, work / "cases_real")
    task = real.wait_task(task.task_id, timeout=600)
    usage = task.resource_usage or {}
    record_b = {"state": task.state, "stop_reason": task.stop_reason,
                "error": task.error, "limit_bytes": limit,
                "peak_job_memory_bytes": usage.get("peak_job_memory_bytes")}
    (work / "guard_within_limit.json").write_text(
        json.dumps(record_b, ensure_ascii=False, indent=2), encoding="utf-8")
    peak = usage.get("peak_job_memory_bytes", 0)
    ok_b = (task.state == STATE_COMPLETED and task.stop_reason is None
            and "内存预算" not in task.error and peak < 0.98 * limit)
    if ok_b:
        log(f"[①b] PASS 限额内真实求解（预算 {_fmt(limit)}，峰值 {_fmt(peak)}）→ "
            f"state=completed、stop_reason=None、无『内存预算』文案")
    else:
        failures.append(f"①b 限额内完成单测未达标: {record_b}")


# ---------------------------------------------------------------- ② 逐门理论锚
ANCHORS = {
    "static_solve": ["vs δ=", "0.1904762"],
    "assembly_results": ["max Mises"],
    "bonded_connection": ["ΣRF", "误差"],
    "bolt_pin_connection": ["ΣRF", "9.5238e-4"],
    "contact_friction_connection": ["界面法向压力积分", "u_x"],
}


def anchor_checks(round1: dict, failures: list[str]) -> None:
    """round 1 输出的逐门理论锚核对；60MPa Mises 锚由配套门 results_view 承载。"""
    for gate, needles in ANCHORS.items():
        stdout = round1[gate]["stdout"]
        missing = [needle for needle in needles if needle not in stdout]
        if missing:
            failures.append(f"② {gate} 理论锚输出缺失: {missing}")
        else:
            log(f"[②] PASS {gate} 理论锚在位: {needles}")
    # 60 MPa Mises 锚：verify_results_view（实测 σ=Mc/I=60MPa 相对误差 ≤10% 门）
    result = run_gate(ROOT / "verify" / "verify_results_view.py")
    code, stdout = result["exit_code"], result["stdout"]
    if code != 0 or "VERIFY_RESULTS_VIEW: PASS" not in stdout or "梁理论 σ=Mc/I" not in stdout:
        failures.append(f"② static_solve 的 60MPa Mises 配套锚（results_view）未过: exit={code}")
    else:
        line = next((ln for ln in stdout.splitlines() if "梁理论 σ=Mc/I" in ln), "")
        log(f"[②] PASS 60MPa Mises 锚（results_view 承载，static_solve 本身无 Mises 门）: {line.strip()}")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    # 项目回归约定（run_regression.py:57）：求解线程数固定 2。本脚本的守卫单测 ①b
    # 在本进程内直接跑真实 ccx——若运行方未设 OMP_NUM_THREADS，ccx 默认用全部核心
    # （本机 32 核），PaStiX 每线程内存闸使同一 884KB 小算例峰值 0.79GiB→4.42GiB，
    # 正常完成也会撞预算。setdefault：运行方已设时不覆盖（与 run_regression 一致）。
    os.environ.setdefault("OMP_NUM_THREADS", "2")
    log(f"求解线程环境：OMP_NUM_THREADS={os.environ.get('OMP_NUM_THREADS')}（run_regression.py:57 同款约定，"
        "本进程内单测与子进程门一致）")
    failures: list[str] = []
    log("修复「内存守卫截停的五门静力回归」验收开始（模块46）")
    log("基线出处：module_44 盘点③——2026-11-02 22:23-22:27 五门被内存守卫截停/0xC0000005；"
        "本轮修复=①预算分级 ②规模参数化 ③守卫单测")

    work = Path("E:/rm_sim/verification") / (
        "memory_budget_guard_" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f"))
    work.mkdir(parents=True, exist_ok=False)
    log(f"证据目录：{work}")

    # ---- ① 守卫语义单测 ----
    guard_unit_tests(work, failures)

    # ---- ②+③ 五门各自单跑（round1=②锚核对）+ 连续 3 轮（③；轮内并发进程） ----
    rounds: list[dict] = []
    for rnd in (1, 2, 3):
        results, wall_seconds = run_round_parallel(rnd, work)
        round_record = evaluate_round(rnd, results, work, failures, wall_seconds)
        rounds.append(round_record)
        (work / f"round{rnd}.json").write_text(
            json.dumps(round_record, ensure_ascii=False, indent=2), encoding="utf-8")

    # 每轮每门耗时分解（新增报告；不改门与判据）
    timing = timing_breakdown(rounds)
    print_timing_table(timing)
    (work / "timing_breakdown.json").write_text(
        json.dumps(timing, ensure_ascii=False, indent=2), encoding="utf-8")

    # round 1 的逐门锚核对
    round1_stdout = {}
    for gate, _script in GATES:
        round1_stdout[gate] = {"stdout": (work / f"round1_{gate}_stdout.log").read_text(encoding="utf-8")}
    anchor_checks(round1_stdout, failures)

    (work / "verification.json").write_text(json.dumps(
        {"created": datetime.datetime.now().isoformat(timespec="seconds"),
         "rounds": rounds, "timing_breakdown": timing, "failures": failures},
        ensure_ascii=False, indent=2), encoding="utf-8")

    log("\n==== 摘要 ====")
    if failures:
        log(f"MEMORY_BUDGET_GUARD: FAIL（{len(failures)} 项）")
        for item in failures:
            log(f"  - {item}")
        return 1
    log("① 守卫单测：注入峰值≥98%限额→STATE_FAILED+『内存预算』；限额内真实求解→completed 不触发")
    log("② 五门各自单跑 exit 0 + 逐门理论锚在位（δ=0.1904762/60MPa Mises/ΣRF/预紧反力/接触只压不拉/部件应力同源）")
    log("③ 五门连续 3 轮全部 exit 0，无 0xC0000005；每轮退出码与峰值内存已落证据目录")
    log(f"③+ 每轮每门耗时分解已落 timing_breakdown.json/verification.json（调研 §2 口径；标签={{{', '.join(suite_labels.LABELS)}}} 只作报告）")
    log("MEMORY_BUDGET_GUARD: PASS")
    return 0


if __name__ == "__main__":
    _exit = 1
    try:
        _exit = main()
    except Exception as exc:  # noqa: BLE001
        log(f"MEMORY_BUDGET_GUARD: FAIL（顶层异常 {type(exc).__name__}: {exc}）")
        raise
    raise SystemExit(_exit)
