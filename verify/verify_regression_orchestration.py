# -*- coding: utf-8 -*-
"""验收脚本：修复「回归编排无参调用」（模块45）。exit 0 = 通过；日志 → verify/logs/。

背景：2026-11-02 基线回归 9 个失败脚本中 2 个为「编排无参调用」——
verify_full_assembly_solve.py（需 <case_dir>，脚本 :9 用法行）与
verify_workflow_bundle_relocation.py（:15 直取 sys.argv[1]）。两工具均不在
run_regression.py DEFAULT 清单（run_regression.py:13-20，本会话核对），失败来自
显式点名的基线运行。修复：无参自检模式（自动定位最近归档；无归档现场跑最小
两块装配求解再复核；带参模式行为不变；无可用 case 且求解失败 → 非零退出如实报告）。

覆盖验收标准：
① 无参自检（显式点名 + PYTHONUTF8=1/OMP_NUM_THREADS=2）：两脚本各自 exit 0，
   stdout 声明所用 case 目录/完成记录与复核结论（PASS 行）；
② 复核门：无参运行落盘的 review 记录 ΣF/ΣM 平衡残差 ≤0.5%、位移有界
   （displacements_finite + no_runaway_part）、绑定警告不阻断复核
   （requires_review/untied 记录在案但不参与 ok 判定——静态核查 checks 构成）；
③ 带参回归对照：两脚本带参运行于固定样例（orchestration_fixture_2block_grav 的
   case / 既有后台工作流完成记录），归一化输出与修复前预拍基线
   （verify/fixtures/orchestration_prefix_*，2026-11-03 修复前实测预拍）一致；
④ 回退路径演练：证据根指向空目录（RM_SIM_NOARG_EVIDENCE_ROOT）→ 两脚本无参运行
   真实走「现做」回退（最小两块装配：直接 prepare/求解 / 后台工作流）后 exit 0，
   复核门同样 ≤0.5%；
⑤ 口径记录：两脚本当前是否在 run_regression.py DEFAULT 清单——只记录现状；
   是否扩入由用户确认后执行（不自动扩，与盘点①口径一致）。
"""
from __future__ import annotations

import datetime
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "verify"))
LOG_PATH = ROOT / "verify" / "logs" / "verify_regression_orchestration.log"

TOOL_REVIEW = ROOT / "verify" / "verify_full_assembly_solve.py"
TOOL_RELOCATE = ROOT / "verify" / "verify_workflow_bundle_relocation.py"
FIXTURE_CASE = Path("E:/rm_sim/verification/orchestration_fixture_2block_grav/build/cases/case_001")
FIXTURE_RECORD = Path("E:/rm_sim/verification/background_workflow_20261102_222343_962990/verification.json")
PREFIX_REVIEW_STDOUT = ROOT / "verify" / "fixtures" / "orchestration_prefix_full_assembly_solve_stdout.txt"
PREFIX_RELOCATE_STDOUT = ROOT / "verify" / "fixtures" / "orchestration_prefix_workflow_bundle_relocation_stdout.txt"
PREFIX_RELOCATE_SEMANTICS = ROOT / "verify" / "fixtures" / "orchestration_prefix_workflow_bundle_relocation_semantics.json"

BASELINE_PREFIX = "2026-11-02 基线（dwfrun-88bb5b72 实跑 22:23-22:27 → ask#6 归类）"

_log_handle = None


def log(message: str) -> None:
    global _log_handle
    if _log_handle is None:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        _log_handle = LOG_PATH.open("a", encoding="utf-8")
        _log_handle.write(f"\n===== 运行 {time.strftime('%Y-%m-%d %H:%M:%S')} =====\n")
    _log_handle.write(message + "\n")
    _log_handle.flush()
    print(message, flush=True)


def run_tool(script: Path, args: list[str], env_extra: dict[str, str] | None = None) -> tuple[int, str]:
    """显式点名运行验收工具（PYTHONUTF8=1、OMP_NUM_THREADS=2，验收环境基线）。"""
    env = dict(os.environ)
    env.update({"PYTHONUTF8": "1", "OMP_NUM_THREADS": "2",
                "TEMP": "E:/rm_sim/temp", "TMP": "E:/rm_sim/temp",
                "QT_QPA_PLATFORM": "offscreen"})
    if env_extra:
        env.update(env_extra)
    cmd = [sys.executable, "-X", "utf8", "-B", str(script), *args]
    log(f"[run] {' '.join(cmd)}")
    log(f"      env: PYTHONUTF8=1 OMP_NUM_THREADS=2"
        + (f" {' '.join(f'{k}={v}' for k, v in env_extra.items())}" if env_extra else ""))
    started = time.monotonic()
    proc = subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True, timeout=1200)
    stdout = proc.stdout.decode("utf-8", errors="replace")
    log(f"      exit={proc.returncode} seconds={time.monotonic()-started:.1f}s")
    for line in stdout.splitlines():
        log("      | " + line)
    if proc.stderr.strip():
        log("      [stderr tail] " + proc.stderr.decode("utf-8", errors="replace")[-400:])
    return proc.returncode, stdout


def review_gates(record: dict, label: str, failures: list[str]) -> None:
    """② 复核门：ΣF/ΣM ≤0.5%、位移有界、绑定警告记录在案但不阻断。"""
    checks = [
        (record.get("force_ratio") is not None and record["force_ratio"] <= 0.005,
         f"ΣF 平衡残差 {record.get('force_ratio')!r} ≤ 0.5%"),
        (record.get("moment_ratio") is not None and record["moment_ratio"] <= 0.005,
         f"ΣM 平衡残差 {record.get('moment_ratio')!r} ≤ 0.5%"),
        (record.get("displacements_finite") is True, "位移全部有限（displacements_finite）"),
        (record.get("checks", {}).get("no_runaway_part") is True, "无部件飞掉（no_runaway_part）"),
        (record.get("job_finished") is True, "Job finished"),
        (record.get("ok") is True, "复核整体 ok（绑定警告不阻断——requires_review 不参与判定）"),
        ("requires_review" in record and "untied_nodes" in record,
         f"绑定警告记录在案（requires_review={record.get('requires_review')}, "
         f"untied={record.get('untied_nodes')}）"),
    ]
    for ok, desc in checks:
        if ok:
            log(f"[②/{label}] PASS {desc}")
        else:
            failures.append(f"②/{label} 复核门失败: {desc}")
            log(f"[②/{label}] FAIL {desc}")


def static_no_block_check(failures: list[str]) -> None:
    """绑定警告不阻断的静态核查：ok 只由 checks 构成，checks 不含 requires_review。

    （untied_within_tolerance 是 0.5% 比例筛查门——警告存在本身不阻断；整装实测
    28/10319=0.27% 警告在案且 PASS，即该语义。）
    """
    source = TOOL_REVIEW.read_text(encoding="utf-8")
    match = re.search(r'"checks":\s*\{(.*?)\n\s*\},', source, re.DOTALL)
    if match is None:
        failures.append("② 静态核查失败：工具源码里找不到 checks 构成块")
        return
    block = match.group(1)
    if "requires_review" in block:
        failures.append("② 静态核查失败：checks 构成里出现了 requires_review（存在警告即阻断，违反口径）")
    else:
        log("[②/静态] PASS ok 判定 = checks 全真；checks 不含 requires_review（绑定警告记录在案不阻断；"
            "untied_within_tolerance 为 0.5% 比例筛查门）")


def newest_relocation_work() -> Path | None:
    works = sorted(p for p in Path("E:/rm_sim/verification").glob("workflow_bundle_relocation_*")
                   if p.is_dir())
    return works[-1] if works else None


def _norm(text: str) -> str:
    """stdout 归一化：时间戳证据目录 + Windows 管道 CRLF。"""
    text = re.sub(r"workflow_bundle_relocation_\d+_\d+_\d+", "<WORK>", text)
    return text.replace("\r\n", "\n")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    failures: list[str] = []
    log("修复「回归编排无参调用」验收开始（模块45）")
    log(f"基线出处：{BASELINE_PREFIX}；两工具不在 run_regression.py DEFAULT（run_regression.py:13-20，已核对）")

    work = Path("E:/rm_sim/verification") / (
        "regression_orchestration_" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f"))
    work.mkdir(parents=True, exist_ok=False)

    # ---- ③ 前置：固定样例就位（幂等；缺失时现场求解一次） ----
    from orchestration_cases import ensure_minimal_assembly_case
    fixture_case, fixture_job, solved_now = ensure_minimal_assembly_case(FIXTURE_CASE.parent.parent.parent)
    log(f"[③] 固定样例 case={fixture_case}（job={fixture_job}，本次{'现做' if solved_now else '复用'}）")
    if not FIXTURE_RECORD.is_file():
        failures.append(f"③ 固定完成记录不存在：{FIXTURE_RECORD}（迁移复核需旧目录在盘）")

    # ---- ①a verify_full_assembly_solve.py 无参 ----
    code, stdout = run_tool(TOOL_REVIEW, ["--out", str(work / "review_noarg.json")])
    if code != 0:
        failures.append(f"① 无参运行 verify_full_assembly_solve.py exit={code}")
    elif "VERIFY_FULL_ASSEMBLY_SOLVE: PASS" not in stdout or "case_dir=" not in stdout:
        failures.append("① 无参运行 stdout 未声明 case 目录或复核结论")
    else:
        log("[①] PASS verify_full_assembly_solve.py 无参自检 exit 0，stdout 声明 case 目录与结论")
    review_path = work / "review_noarg.json"
    if review_path.is_file():
        review_gates(json.loads(review_path.read_text(encoding="utf-8")), "无参定位", failures)
    else:
        failures.append("② 无参运行的复核记录未落盘（--out 缺失）")

    # ---- ①b verify_workflow_bundle_relocation.py 无参 ----
    code, stdout = run_tool(TOOL_RELOCATE, [])
    if code != 0:
        failures.append(f"① 无参运行 verify_workflow_bundle_relocation.py exit={code}")
    elif "VERIFY_WORKFLOW_BUNDLE_RELOCATION: PASS" not in stdout or "自动定位" not in stdout:
        failures.append("① 无参运行 stdout 未声明所用完成记录或结论")
    else:
        log("[①] PASS verify_workflow_bundle_relocation.py 无参自检 exit 0，stdout 声明记录与结论")

    # ---- ② 静态核查：绑定警告不阻断 ----
    static_no_block_check(failures)

    # ---- ③ 带参回归对照（同一 case / 记录，输出与修复前预拍一致） ----
    code, stdout = run_tool(TOOL_REVIEW, [str(fixture_case), "--job", fixture_job])
    expected = PREFIX_REVIEW_STDOUT.read_text(encoding="utf-8")
    if code != 0:
        failures.append(f"③ 带参运行 verify_full_assembly_solve.py exit={code}")
    elif _norm(stdout) != _norm(expected):
        failures.append("③ 带参输出与修复前预拍不一致（verify_full_assembly_solve）\n"
                        f"--- 预拍 ---\n{_norm(expected)}\n--- 本次 ---\n{_norm(stdout)}")
    else:
        log("[③] PASS verify_full_assembly_solve.py 带参输出与修复前逐字节一致")

    code, stdout = run_tool(TOOL_RELOCATE, [str(FIXTURE_RECORD)])
    normalized = _norm(stdout)
    expected = _norm(PREFIX_RELOCATE_STDOUT.read_text(encoding="utf-8"))
    if code != 0:
        failures.append(f"③ 带参运行 verify_workflow_bundle_relocation.py exit={code}")
    elif normalized != expected:
        failures.append("③ 带参输出（归一化时间戳目录）与修复前预拍不一致\n"
                        f"--- 预拍 ---\n{expected}\n--- 本次 ---\n{normalized}")
    else:
        log("[③] PASS verify_workflow_bundle_relocation.py 带参输出与修复前一致（时间戳目录归一化）")
    semantics_path = PREFIX_RELOCATE_SEMANTICS.read_text(encoding="utf-8")
    semantics = json.loads(semantics_path)
    work_dir = newest_relocation_work()
    if work_dir is None or not (work_dir / "verification.json").is_file():
        failures.append("③ 迁移复核 verification.json 未找到")
    else:
        fresh = json.loads((work_dir / "verification.json").read_text(encoding="utf-8"))
        same = (fresh.get("old_project_reads_blocked") is True
                and fresh.get("old_project_reads_attempted") == []
                and len(fresh.get("original_case_hashes", {})) == semantics["original_case_count"]
                and json.dumps(fresh.get("rf_sum_n")) == json.dumps(semantics["rf_sum_n"]))
        if same:
            log("[③] PASS 迁移复核语义与修复前一致（旧目录零读取 + case 哈希数一致 + rf_sum_n 相等）")
        else:
            failures.append(f"③ 迁移复核语义与修复前不一致：{semantics_path} vs {work_dir}")

    # ---- ④ 回退路径演练：空证据根 → 现场最小求解 ----
    empty_root = work / "empty_evidence"
    empty_root.mkdir(parents=True, exist_ok=False)
    code, stdout = run_tool(TOOL_REVIEW, ["--out", str(work / "review_fallback.json")],
                            env_extra={"RM_SIM_NOARG_EVIDENCE_ROOT": str(empty_root)})
    if code != 0:
        failures.append(f"④ 回退演练 verify_full_assembly_solve.py exit={code}")
    elif "现场求解一次最小两块装配" not in stdout or "VERIFY_FULL_ASSEMBLY_SOLVE: PASS" not in stdout:
        failures.append("④ 回退演练 stdout 未体现「现做最小两块装配」路径")
    else:
        log("[④] PASS 空证据根 → 现场最小两块装配（自重）真实求解 → 复核 PASS")
    fallback_review = work / "review_fallback.json"
    if fallback_review.is_file():
        review_gates(json.loads(fallback_review.read_text(encoding="utf-8")), "回退现做", failures)

    code, stdout = run_tool(TOOL_RELOCATE, [],
                            env_extra={"RM_SIM_NOARG_EVIDENCE_ROOT": str(empty_root)})
    if code != 0:
        failures.append(f"④ 回退演练 verify_workflow_bundle_relocation.py exit={code}")
    elif "现场跑一次最小两块装配后台工作流" not in stdout or "VERIFY_WORKFLOW_BUNDLE_RELOCATION: PASS" not in stdout:
        failures.append("④ 回退演练 stdout 未体现「现做后台工作流」路径")
    else:
        log("[④] PASS 空证据根 → 现场最小两块装配后台工作流（真实 prepare/solve/collect）→ 迁移复核 PASS")

    # ---- ⑤ 口径记录：DEFAULT 清单现状（不自动扩，等待用户确认） ----
    regression_source = (ROOT / "verify" / "run_regression.py").read_text(encoding="utf-8")
    in_default_review = "full_assembly_solve" in regression_source
    in_default_relocate = "workflow_bundle_relocation" in regression_source
    log(f"[⑤] run_regression.py DEFAULT 现状：full_assembly_solve 在清单={in_default_review}，"
        f"workflow_bundle_relocation 在清单={in_default_relocate}"
        f"（基线口径=不在清单；是否扩入由用户确认后执行，本脚本不擅自扩）")

    log("\n==== 摘要 ====")
    if failures:
        log(f"REGRESSION_ORCHESTRATION: FAIL（{len(failures)} 项）")
        for item in failures:
            log(f"  - {item}")
        return 1
    log("① 两脚本无参自检 exit 0（显式点名 + PYTHONUTF8=1/OMP_NUM_THREADS=2），stdout 声明所用 case/记录与结论")
    log("② 复核门：ΣF/ΣM ≤0.5%、位移有界、绑定警告记录在案但不阻断（静态+记录双核查）")
    log("③ 带参模式对固定样例输出与修复前预拍一致（两工具）")
    log("④ 空证据根回退：两脚本真实现做最小两块装配后复核 PASS（含一次直接求解 + 一次后台工作流）")
    log("⑤ DEFAULT 清单口径已记录，未自动扩（待用户确认）")
    log("REGRESSION_ORCHESTRATION: PASS")
    return 0


if __name__ == "__main__":
    _exit = 1
    try:
        _exit = main()
    except Exception as exc:  # noqa: BLE001
        log(f"REGRESSION_ORCHESTRATION: FAIL（顶层异常 {type(exc).__name__}: {exc}）")
        raise
    finally:
        pass
    raise SystemExit(_exit)
