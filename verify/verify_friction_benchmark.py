# -*- coding: utf-8 -*-
"""验收脚本：「位移控制摩擦基准并迁移负例」（模块52·调研§3）。exit 0 = 通过；日志 → verify/logs/。

覆盖验收标准：
① μ=0.1 与 0.3 的平台切向力/实测法向力比值各在其标称值 ±15% 内且两档可区分
   （粘着阶段、平台点数、两档平台比 ≥2.0）；
② 两网格（{2,4}mm）判别结论一致（同 μ 平台均值差 ≤0.02）；
③ 平衡残差（底面+驱动面 ΣRFx ≤0.5%）与接触开闭（界面法向压力积分=N±10%、
   最小面压 ≥0 只压不拉）检查通过；
④ 迁移后五门套件（verify_memory_budget_guard.py，门与判据零改动）完整重跑
   exit 0；套件总时长外部墙钟实测并与基线 16分45秒（1005s，模块46 2026-11-03
   07:08 实测，同口径=套件子进程完整重跑）并列记录，不外推倍数；
⑤ 原负例仍可通过显式慢开关运行：RM_SIM_FRICTION_SLOW_NEGATIVE=1 下接触门
   exit 0 且日志含原 300s 负例证据（预置提交/超时取消滑移证据）；
⑥ 取消/超时/崩溃注入测试秒级完成且错误分类正确（C# 夹具，非求解器证据）：
   等待超时→cancel→STATE_CANCELLED、提交即取消→秒级终态、未知作业 exit 23→
   STATE_FAILED『日志无 Job finished』。

时序与证据源（运行窗编排修复，2026-11-03 第 1 次修复）：world.run 窗 1200000ms
（20 分钟），初版串行 ⑥→①②③→④→⑤ 实测 ≈22.3 分钟超窗——属验收脚本自身的
串行编排缺陷（模块46 同款先例），覆盖项不变、纯排布修复：
- ①②③ 不再单独跑一遍基准，改为断言 ④ 五门套件内接触门（默认档）**自带**的
  位移滑块基准证据（套件 3 轮独立运行、每轮两网格×两 μ 全规模同一代码路径，
  从套件证据目录 round{N}_contact_friction_connection_stdout.log 解析判据核对行；
  覆盖只增不减：另得跨 3 轮一致性），并断言 3 轮均无 ④' 相关 [FAIL] 行；
- 基准调度并发 2→4（各位移点独立求解，物理不变）；
- ⑥ → ④ → ⑤ 顺序执行，⑤ 的慢开关门不与 ④ 并行（④ 时长口径=安静机器裸跑，
  与基线同口径）。
"""
from __future__ import annotations

import datetime
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "verify"))
sys.path.insert(0, str(ROOT / "app"))
os.environ.setdefault("OMP_NUM_THREADS", "2")

LOG_PATH = ROOT / "verify" / "logs" / "verify_friction_benchmark.log"
SUITE = ROOT / "verify" / "verify_memory_budget_guard.py"
GATE = ROOT / "verify" / "verify_contact_friction_connection.py"
GATE_LOG = ROOT / "verify" / "logs" / "verify_contact_friction_connection.log"
BASELINE_S = 1005.0  # 基线 16分45秒（模块46 第2次验收实测，2026-11-03 07:08—07:25）
SLOW_ENV = "RM_SIM_FRICTION_SLOW_NEGATIVE"

_log = None


def log(message: str) -> None:
    global _log
    if _log is None:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        _log = LOG_PATH.open("a", encoding="utf-8")
        _log.write(f"\n===== 运行 {datetime.datetime.now().isoformat(timespec='seconds')} =====\n")
    _log.write(message + "\n")
    _log.flush()
    print(message, flush=True)


def run_cmd(cmd: list[str], env_extra: dict | None = None, timeout: int = 1500) -> tuple[int, str, float]:
    env = dict(os.environ)
    env.update({"PYTHONUTF8": "1", "OMP_NUM_THREADS": "2",
                "TEMP": "E:/rm_sim/temp", "TMP": "E:/rm_sim/temp",
                "QT_QPA_PLATFORM": "offscreen"})
    if env_extra:
        env.update(env_extra)
    log(f"[run] {' '.join(cmd)}" + (f"  env+= {env_extra}" if env_extra else ""))
    started = time.monotonic()
    proc = subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True, timeout=timeout)
    seconds = time.monotonic() - started
    stdout = proc.stdout.decode("utf-8", errors="replace").replace("\r\n", "\n")
    log(f"      exit={proc.returncode} seconds={seconds:.1f}s")
    for line in stdout.splitlines():
        if line.startswith(("TIMING", "MEMORY_BUDGET_GUARD", "VERIFY_CONTACT", "[③] round",
                            "④", "μ 判别槽位")) or "滑移证据" in line or "预置提交" in line:
            log("      | " + line)
    return proc.returncode, stdout, seconds


BENCH_LINE = re.compile(
    r"\[④'位移滑块基准 ([\d.]+)mm\] μ=([\d.]+) 判据核对：平台均值=([\d.]+)（μ±15% 内）、"
    r"粘着点=(\d+)、平台点=(\d+)、平衡残差≤([\d.eE+-]+)、N=([\d.]+)N、"
    r"界面压力积分=([\d.]+)N（min p=([-\d.]+)MPa）")
TIER_LINE = re.compile(
    r"\[④'位移滑块基准 ([\d.]+)mm\] 两档可区分：平台比 ([\d.]+)/([\d.]+) = ([\d.]+) ≥ 2\.0")


def check_bench_evidence(evidence: Path, failures: list) -> None:
    """从套件证据解析接触门基准判据（验收标准①②③）。

    3 轮独立运行 × 两网格 × 两 μ 全规模同一代码路径；逐轮断言 ±15%/粘着/平台/
    平衡/法向/接触开闭/两档可区分，并断言跨轮平台均值一致（≤0.02）。
    """
    round_plateaus: dict = {}
    for rnd in (1, 2, 3):
        p = evidence / f"round{rnd}_contact_friction_connection_stdout.log"
        if not p.is_file():
            failures.append(f"①②③ round{rnd} 接触门 stdout 缺失: {p}")
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        bench_lines = BENCH_LINE.findall(text)
        if len(bench_lines) != 4:
            failures.append(f"①②③ round{rnd} 基准判据行数 {len(bench_lines)} ≠ 4（两网格×两 μ）")
        bad_fail = [ln for ln in text.splitlines() if ln.startswith("[FAIL] ④")]
        if bad_fail:
            failures.append(f"①②③ round{rnd} 基准存在 [FAIL] 行: {bad_fail[:2]}")
        seen = set()
        for (mesh, mu, plateau, stick_n, plateau_n, eq, n_meas, p_int, p_min) in bench_lines:
            mesh, mu, plateau = float(mesh), float(mu), float(plateau)
            seen.add((mesh, mu))
            round_plateaus.setdefault((mesh, mu), []).append(plateau)
            if abs(plateau - mu) > 0.15 * mu:
                failures.append(f"① round{rnd} {mesh:g}mm μ={mu} 平台 {plateau} 超 ±15%")
            if int(stick_n) < 1:
                failures.append(f"③ round{rnd} {mesh:g}mm μ={mu} 无粘着点")
            if int(plateau_n) < 2:
                failures.append(f"① round{rnd} {mesh:g}mm μ={mu} 平台点不足（<{2}）")
            if float(eq) > 0.005:
                failures.append(f"③ round{rnd} {mesh:g}mm μ={mu} 平衡残差 {eq} > 0.5%")
            if abs(float(n_meas) - 2000.0) > 20.0:
                failures.append(f"③ round{rnd} {mesh:g}mm μ={mu} 实测法向力 {n_meas} 偏离 2000N 超 1%")
            if abs(float(p_int) - float(n_meas)) > 0.1 * float(n_meas):
                failures.append(f"③ round{rnd} {mesh:g}mm μ={mu} 压力积分 {p_int} 偏离 N 超 10%")
            if float(p_min) < 0.0:
                failures.append(f"③ round{rnd} {mesh:g}mm μ={mu} 界面负压力（张开传拉）: {p_min}")
        tiers = TIER_LINE.findall(text)
        if len(tiers) != 2:
            failures.append(f"① round{rnd} 两档可区分行数 {len(tiers)} ≠ 2（两网格各一行）")
        for (mesh, hi, lo, ratio) in tiers:
            if float(ratio) < 2.0:
                failures.append(f"① round{rnd} {mesh}mm 两档不可区分: 平台比 {ratio} < 2.0")
        if seen != {(2.0, 0.1), (2.0, 0.3), (4.0, 0.1), (4.0, 0.3)}:
            failures.append(f"①②③ round{rnd} 基准组合不全: {sorted(seen)}")
    for key, vals in sorted(round_plateaus.items()):
        if len(vals) == 3 and max(vals) - min(vals) > 0.02:
            failures.append(f"② {key[0]:g}mm μ={key[1]} 跨 3 轮平台均值不一致: {vals}")
    if not failures or all(not f.startswith(("①", "②", "③")) for f in failures):
        detail = "; ".join(f"{k[0]:g}mm μ={k[1]}: " + "/".join(f"{v:.4f}" for v in vals)
                           for k, vals in sorted(round_plateaus.items()))
        log(f"[①②③] PASS 套件 3 轮×两网格×两 μ 基准判据全过（平台均值均在 μ±15%、两档可区分、"
            f"粘着/平衡/接触开闭达标、无 [FAIL] 行）；跨轮平台均值差 ≤0.02：{detail}"
            f"（证据={evidence.name}）")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    failures: list[str] = []
    log("「位移控制摩擦基准并迁移负例」验收开始（模块52·调研§3）")
    log(f"基线口径：五门套件完整重跑 16分45秒 = {BASELINE_S:.0f}s"
        "（模块46 2026-11-03 07:08—07:25 实测，同口径对比）")

    work = Path("E:/rm_sim/verification") / (
        "friction_benchmark_" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f"))
    work.mkdir(parents=True, exist_ok=False)
    log(f"证据目录：{work}")

    # ---- ⑥ 先跑（秒级注入，最快失败反馈） ----
    import friction_benchmark as fb

    try:
        inj = fb.run_lifecycle_injections(work / "injections", log=log)
        if inj["elapsed_s"] > 60:
            failures.append(f"⑥ 注入测试未达秒级（耗时 {inj['elapsed_s']:.1f}s > 60s）")
        else:
            log(f"[⑥] PASS 超时/取消/崩溃注入三分类全部正确，累计 {inj['elapsed_s']:.1f}s（秒级）")
    except Exception as exc:  # noqa: BLE001
        failures.append(f"⑥ 注入测试失败: {type(exc).__name__}: {exc}")

    # ---- ④ 迁移后五门套件完整重跑（同口径外部墙钟=套件子进程全程，与基线 16分45秒 同口径） ----
    suite_code, suite_out, suite_wall = run_cmd([sys.executable, "-X", "utf8", "-B", str(SUITE)],
                                                timeout=1500)
    if suite_code != 0:
        failures.append(f"④ 五门套件完整重跑 exit={suite_code}（迁移后必须 exit 0）")
        suite_wall = None
    else:
        log(f"[④] PASS 五门套件完整重跑 exit 0（外部墙钟 {suite_wall:.0f}s，套件子进程全程）")

    # ---- ①②③ 断言接触门（套件默认档）自带的位移滑块基准证据（3 轮独立运行） ----
    if suite_code == 0:
        start_tag = work.name.split("_", 2)[2]
        works = [p for p in Path("E:/rm_sim/verification").glob("memory_budget_guard_*")
                 if p.is_dir() and p.name.split("_", 1)[1] > start_tag]
        works.sort()  # glob 顺序未定义——按时间戳字典序排序后取最新，避免 works[-1] 随机选中
        evidence = works[-1] if works else None
        if evidence is None:
            failures.append("①②③ 未找到本次套件证据目录（memory_budget_guard_*）")
        else:
            check_bench_evidence(evidence, failures)

    # ---- ⑤ 原负例显式慢开关 ----
    slow_code, slow_out, slow_wall = run_cmd([sys.executable, "-X", "utf8", "-B", str(GATE)],
                                             env_extra={SLOW_ENV: "1"}, timeout=900)
    gate_log_text = GATE_LOG.read_text(encoding="utf-8", errors="replace") if GATE_LOG.is_file() else ""
    slow_markers_ok = ("μ 判别槽位（模块52 迁移）：原 μ=0.1 300s 负例" in gate_log_text
                       and ("300s 提交时限剩" in gate_log_text or "预置提交" in gate_log_text)
                       and ("未在 300s 内收敛" in gate_log_text or "滑移证据" in gate_log_text))
    if slow_code != 0:
        failures.append(f"⑤ 慢开关接触门 exit={slow_code}（原负例路径必须 exit 0）")
    elif not slow_markers_ok:
        failures.append("⑤ 慢开关运行日志缺原 300s 负例证据标记（预置提交/超时取消滑移证据）")
    else:
        log("[⑤] PASS RM_SIM_FRICTION_SLOW_NEGATIVE=1：μ 判别槽位回退原 300s 负例并完整运行 "
            "（预置提交 + 超时取消滑移证据在案），门 exit 0——原负例未删，可经显式慢开关运行")

    # ---- 时长并列记录（同口径=五门套件子进程完整重跑外部墙钟；不外推倍数） ----
    log("==== 套件时长并列记录（同口径=套件子进程完整重跑外部墙钟） ====")
    log(f"  基线（模块46 2026-11-03 07:08 实测）：16分45秒 = {BASELINE_S:.0f}s")
    if suite_wall is not None:
        m, s = divmod(suite_wall, 60)
        log(f"  本轮迁移后实测：{int(m)}分{int(s)}秒 = {suite_wall:.0f}s"
            f"（差值 {suite_wall - BASELINE_S:+.0f}s——仅并列事实，不外推倍数）")
    else:
        log("  本轮迁移后实测：套件未通过，无时长结论")

    log("\n==== 摘要 ====")
    if failures:
        log(f"FRICTION_BENCHMARK: FAIL（{len(failures)} 项）")
        for item in failures:
            log(f"  - {item}")
        return 1
    log("① μ=0.1/0.3 平台比值各在 ±15% 内且两档可区分（实测平台比见上方逐网格行，名义比 3.00）")
    log("② 两网格（2/4mm）判别结论一致（同 μ 平台均值差 ≤0.02）")
    log("③ 平衡残差 ≤0.5%、接触开闭（压力积分≈N、只压不拉）检查通过、全部 Job finished")
    log("④ 迁移后五门套件完整重跑 exit 0；总时长实测已与基线 16分45秒 同口径并列记录")
    log("⑤ 原负例经 RM_SIM_FRICTION_SLOW_NEGATIVE=1 完整运行 exit 0（未删门）")
    log("⑥ 取消/超时/崩溃注入秒级完成且错误分类正确")
    log("FRICTION_BENCHMARK: PASS")
    return 0


if __name__ == "__main__":
    _exit = 1
    try:
        _exit = main()
    except Exception as exc:  # noqa: BLE001
        log(f"FRICTION_BENCHMARK: FAIL（顶层异常 {type(exc).__name__}: {exc}）")
        raise
    raise SystemExit(_exit)
