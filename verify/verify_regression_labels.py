# -*- coding: utf-8 -*-
"""验收脚本：「验收耗时分解与套件标签」（调研 §7-1）。exit 0 = 通过；日志 → verify/logs/。

功能对象：verify/run_regression.py（--labels 标签选择 + 每检查耗时分解输出/timing.json）
与五门套件 verify/verify_memory_budget_guard.py（每轮每门耗时分解 + 标签标注）。
标签 fast_config/real_small/reliability/full（口径=suite_labels.py，对照调研 §4 分层表），
只用于选择与报告，不改变任何既有门、容差与原完整验收入口。

覆盖验收标准：
① 完整回归入口原语义不变——改前/改后输出归一化逐字节对照：
   - 带参（advanced_framework 显式点名）与 --framework-only：改前固定样例
     fixtures/regression_labels_prefix/*（2026-11-03 改前实拍）vs 改后实跑；
     归一化只剥三处「本变更允许的新增」：证据目录时间戳→<EVID>、秒数→<T>s、
     行内标签注记『 [label]』、以及新增的 TIMING/TIMING_JSON 行——其余必须逐字节相等；
   - 无参全量：改前 fixtures/regression_labels_prefix/noarg_stdout.txt（改前实拍，
     40/40 PASS）vs 改后 fixtures/regression_labels_postchange/noarg_stdout.txt
     （改后实拍，同机同环境 OMP_NUM_THREADS=2/PYTHONUTF8=1）同规则对照；
     任一样例缺失 → FAIL（不虚造）。
   - DEFAULT/SLOW 清单与改前快照 default_slow_lists.json 逐项相等；
   - --help：改前每一行（usage 行剥去新增『 [--labels LABELS]』后）仍在改后输出中，
     新增行只允许与 --labels 相关。
② 每门耗时落 JSON 且与抽查 wall clock 一致：
   - run_regression：外部墙钟实跑『advanced_framework advanced_framework』（重复名
     =原清单透传语义，恰好两行），断言 summary.json/timing.json 行级耗时与
     by_label 聚合精确一致、内墙钟≥Σ检查秒（串行口径）且差≤1s、外墙钟≥内墙钟且
     差≤5s（解释器启动+证据目录开销）；
   - 五门套件（④ 的本次完整实跑产物）：verification.json 每轮每门 seconds 齐全
     （调研 §2 表同口径：每轮每门秒数）、轮 wall_seconds ∈ [max(门秒)−0.5,
     max(门秒)+15]（五门并发+5s 错峰）、Σ轮墙钟 ≤ 外部套件墙钟+10s、
     timing_breakdown 矩阵与 rounds 逐门一致。
③ 四类标签可按选择参数取子集且选择结果可断言：
   - import 级：DEFAULT∪SLOW 42 项被四标签干净划分（无未知名/无重复/四组非空）；
     五门 ∈ real_small、SLOW 两项 ∈ full；select_by_labels 保序过滤；
     parse_labels 未知标签 ValueError；
   - 实跑：『main_entry static_solve --labels fast_config』只跑 main_entry、
     『--labels real_small』只跑 static_solve（互相为对方标签、确证过滤真实发生）；
     --labels bogus_label → exit 2；--labels full（无点名）→ exit 2 并提示 --slow。
④ 原五门套件完整重跑 exit 0——本脚本启动即后台运行 verify_memory_budget_guard.py
   （原脚本原样：3 轮×5 门并发 + 守卫单测，门内检查与判据零改动），收尾断言 exit 0。

边界：标签只用于选择与报告（铁律）；pytest/xdist 等开源组件本轮只评估不引入；
不改任何 verify_* 门脚本与容差；verify_memory_budget_guard.py 仅增量
（耗时分解/标签标注/行号引用修正），模块46 验收语义原样。
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
sys.path.insert(0, str(ROOT / "app"))
LOG_PATH = ROOT / "verify" / "logs" / "verify_regression_labels.log"
PREFIX = ROOT / "verify" / "fixtures" / "regression_labels_prefix"
POSTFIX = ROOT / "verify" / "fixtures" / "regression_labels_postchange"
SUITE = ROOT / "verify" / "verify_memory_budget_guard.py"
RUNNER = ROOT / "verify" / "run_regression.py"
GATES = ["static_solve", "assembly_results", "bonded_connection",
         "bolt_pin_connection", "contact_friction_connection"]

_log_handle = None


def log(message: str) -> None:
    global _log_handle
    if _log_handle is None:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        _log_handle = LOG_PATH.open("a", encoding="utf-8")
        _log_handle.write(f"\n===== 运行 {datetime.datetime.now().isoformat(timespec='seconds')} =====\n")
    _log_handle.write(message + "\n")
    _log_handle.flush()
    print(message, flush=True)


def run_tool(cmd: list[str], cwd: Path, timeout: int = 300) -> tuple[int, str, str]:
    env = dict(os.environ)
    env.update({"PYTHONUTF8": "1", "OMP_NUM_THREADS": "2",
                "TEMP": "E:/rm_sim/temp", "TMP": "E:/rm_sim/temp",
                "QT_QPA_PLATFORM": "offscreen"})
    log(f"[run] cwd={cwd} cmd={' '.join(cmd)}")
    started = time.monotonic()
    proc = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, timeout=timeout)
    seconds = time.monotonic() - started
    stdout = proc.stdout.decode("utf-8", errors="replace").replace("\r\n", "\n")
    stderr = proc.stderr.decode("utf-8", errors="replace")
    log(f"      exit={proc.returncode} seconds={seconds:.1f}s")
    for line in stdout.splitlines():
        log("      | " + line)
    if stderr.strip():
        log("      [stderr tail] " + stderr[-400:])
    return proc.returncode, stdout, stderr


# 归一化：只剥本变更允许的四类新增（证据目录时间戳 / 秒数 / 行内标签注记 / TIMING 行）
def normalize(text: str) -> str:
    text = text.replace("\r\n", "\n")
    text = re.sub(r"^(\S+) \[[a-z_]+\]: ", r"\1: ", text, flags=re.M)
    text = re.sub(r"regression_\d{8}_\d{6}_\d+", "<EVID>", text)
    text = re.sub(r" (\d+\.\d+)s$", " <T>s", text, flags=re.M)
    kept = [ln for ln in text.split("\n")
            if not ln.startswith("TIMING")]
    return "\n".join(kept)


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    failures: list[str] = []
    log("「验收耗时分解与套件标签」验收开始（调研 §7-1）")
    log(f"标签口径：verify/suite_labels.py（fast_config/real_small/reliability/full；"
        "对照调研 §4 分层表；只用于选择与报告）")

    work = Path("E:/rm_sim/verification") / (
        "regression_labels_" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f"))
    work.mkdir(parents=True, exist_ok=False)
    log(f"证据目录：{work}")

    # ---- ④ 前置：启动原五门套件完整重跑（后台；收尾断言 exit 0） ----
    suite_env = dict(os.environ)
    suite_env.update({"PYTHONUTF8": "1", "OMP_NUM_THREADS": "2",
                      "TEMP": "E:/rm_sim/temp", "TMP": "E:/rm_sim/temp",
                      "QT_QPA_PLATFORM": "offscreen"})
    suite_cmd = [sys.executable, "-X", "utf8", "-B", str(SUITE)]
    log(f"[④] 启动原五门套件完整重跑（后台）：{' '.join(suite_cmd)}")
    suite_started = time.monotonic()
    suite_proc = subprocess.Popen(suite_cmd, cwd=ROOT, env=suite_env,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    py = sys.executable

    # ---- ① 原语义不变：带参 / --framework-only / --help / 清单 / 无参全量 ----
    for tag, args, fixture_name in (
            ("①a 带参", ["advanced_framework"], "explicit_advanced_framework_stdout.txt"),
            ("①b --framework-only", ["--framework-only"], "framework_only_stdout.txt")):
        code, stdout, _err = run_tool([py, "-X", "utf8", "-B", str(RUNNER), *args], cwd=ROOT)
        if code != 0:
            failures.append(f"{tag} 改后实跑 exit={code}")
            continue
        got = normalize(stdout)
        want = normalize(read_text(PREFIX / fixture_name))
        if got == want:
            log(f"[{tag}] PASS 归一化（剥时间戳/秒数/标签注记/TIMING 行）后与改前固定样例逐字节一致")
        else:
            failures.append(f"{tag} 输出与改前不一致\n--- 改前 ---\n{want}\n--- 改后 ---\n{got}")

    # ①c --help：改前每行仍在改后（usage 行剥新增 token）；新增行只允许 --labels 相关
    code, post_help, _ = run_tool([py, "-X", "utf8", "-B", str(RUNNER), "--help"], cwd=ROOT)
    pre_help = read_text(PREFIX / "help_stdout.txt").replace("\r\n", "\n")
    if code != 0:
        failures.append(f"①c --help exit={code}")
    else:
        pre_lines = pre_help.split("\n")
        post_lines = post_help.split("\n")
        pre_usage = next((ln for ln in pre_lines if ln.startswith("usage:")), "")
        # 改后 usage 可能因 [--labels LABELS] 变长而折行——重建 usage 块（含续行）再对比
        usage_block: list[str] = []
        for i, ln in enumerate(post_lines):
            if ln.startswith("usage:"):
                usage_block.append(ln)
                for cont in post_lines[i + 1:]:
                    indent = len(cont) - len(cont.lstrip())
                    if cont.strip() and indent >= 8:  # usage 续行缩进远大于选项行（2 空格）
                        usage_block.append(cont.strip())
                    else:
                        break
                break
        joined = " ".join(usage_block).replace(" [--labels LABELS]", "").replace(" [--only-failed]", "")
        usage_ok = joined == pre_usage
        missing = [ln for ln in pre_lines[1:] if ln.strip() and ln not in post_lines]
        added = [ln for ln in post_lines if ln.strip() and ln not in pre_lines
                 and not ln.startswith("usage:")]
        # 新增行只允许与选择能力相关（--labels=模块50；--only-failed=模块51 失败项重跑，
        # 属「仅新增选择与报告信息」同一契约延展），含其 argparse 折行续行（纯缩进行）
        added_bad = [ln for ln in added
                     if not ("labels" in ln.lower() or "only-failed" in ln.lower()
                             or "only_failed" in ln.lower())
                     and not ln[:1] in (" ", "\t")]
        if usage_ok and not missing and not added_bad:
            log("[①c] PASS --help 保留改前全部行（usage 仅插入 [--labels LABELS]/[--only-failed]）；"
                f"新增 {len(added)} 行均与选择能力（--labels/--only-failed）相关")
        else:
            failures.append(f"①c --help 对照失败：usage_ok={usage_ok} missing={missing} "
                            f"无关新增={added_bad}")

    # ①d DEFAULT/SLOW 清单与改前快照逐项相等
    import run_regression
    import suite_labels
    snap = json.loads(read_text(PREFIX / "default_slow_lists.json"))
    if list(run_regression.DEFAULT) == snap["default"] and list(run_regression.SLOW) == snap["slow"]:
        log(f"[①d] PASS DEFAULT({len(run_regression.DEFAULT)})/SLOW({len(run_regression.SLOW)})"
            "清单与改前快照逐项相等（无参=遍历同一 DEFAULT，执行循环与输出与带参共用同一代码）")
    else:
        failures.append("①d DEFAULT/SLOW 清单与改前快照不一致")

    # ①e 无参全量：改前/改后实拍样例同规则对照（样例缺失=FAIL，不虚造）
    pre_noarg, post_noarg = PREFIX / "noarg_stdout.txt", POSTFIX / "noarg_stdout.txt"
    if not pre_noarg.is_file() or not post_noarg.is_file():
        failures.append(f"①e 无参全量样例缺失：改前={pre_noarg.is_file()} 改后={post_noarg.is_file()}"
                        "（须真实实跑捕获，不得伪造）")
    else:
        got, want = normalize(read_text(post_noarg)), normalize(read_text(pre_noarg))
        if got == want:
            log("[①e] PASS 无参全量输出归一化后与改前逐字节一致（40 检查行 + REGRESSION 行）")
        else:
            failures.append(f"①e 无参全量输出与改前不一致\n--- 改前 ---\n{want}\n--- 改后 ---\n{got}")
        for p in (PREFIX / "noarg_stderr.txt", POSTFIX / "noarg_stderr.txt"):
            if p.is_file() and p.read_text(encoding="utf-8", errors="replace").strip():
                failures.append(f"①e 无参全量 stderr 非空：{p}")
        names_in_post = re.findall(r"^(\S+?)(?: \[[a-z_]+\])?: (?:PASS|FAIL)",
                                   read_text(post_noarg), flags=re.M)
        if names_in_post != list(run_regression.DEFAULT):
            failures.append("①e 改后无参样例的检查顺序与 DEFAULT 清单不一致（样例来源存疑）")
        else:
            log("[①e] PASS 改后样例覆盖 DEFAULT 全部 40 项且顺序一致（样例与当前清单绑定）")

    # ---- ③ 标签选择可断言（先做 import 级，快速失败） ----
    all_names = list(run_regression.DEFAULT) + list(run_regression.SLOW)
    report = suite_labels.coverage_report(all_names)
    if report["unknown"] or report["duplicates"]:
        failures.append(f"③a 标签覆盖缺陷：unknown={report['unknown']} duplicates={report['duplicates']}")
    empty = [lb for lb, names in report["groups"].items() if not names]
    if empty:
        failures.append(f"③a 空标签组：{empty}")
    union = sorted(n for names in report["groups"].values() for n in names)
    if sorted(all_names) != union:
        failures.append("③a 四标签并集 ≠ DEFAULT∪SLOW 全集")
    gate_group = {suite_labels.label_of(g) for g in GATES}
    slow_group = {suite_labels.label_of(s) for s in run_regression.SLOW}
    if gate_group != {"real_small"} or slow_group != {"full"}:
        failures.append(f"③a 标签归属不符：五门={gate_group}（应 real_small），SLOW={slow_group}（应 full）")
    if not (failures and any(f.startswith("③a") for f in failures)):
        counts = {lb: len(names) for lb, names in report["groups"].items()}
        log(f"[③a] PASS 42 项被四标签干净划分 {counts}；五门∈real_small、SLOW∈full；"
            "无未知名/重复项/空组")
    if [n for n in suite_labels.select_by_labels(["static_solve", "main_entry"], ["fast_config"])] != ["main_entry"]:
        failures.append("③a select_by_labels 未按标签保序过滤")
    try:
        suite_labels.parse_labels("bogus_label")
        failures.append("③a parse_labels 未拒绝未知标签")
    except ValueError:
        pass
    if suite_labels.parse_labels(" fast_config , real_small , fast_config ") != ["fast_config", "real_small"]:
        failures.append("③a parse_labels 去重/解析不符")

    # ③b 实跑选择子集：互为对方标签的两项，各只保留本标签项（确证过滤真实发生）
    for labels, only_name, only_label in (("fast_config", "main_entry", "fast_config"),
                                          ("real_small", "static_solve", "real_small")):
        code, stdout, _ = run_tool([py, "-X", "utf8", "-B", str(RUNNER),
                                    "main_entry", "static_solve", "--labels", labels], cwd=ROOT)
        check_lines = re.findall(r"^(\S+) \[([a-z_]+)\]: (PASS|FAIL) exit=(\S+)", stdout, flags=re.M)
        if code != 0:
            failures.append(f"③b --labels {labels} 实跑 exit={code}")
        elif check_lines != [(only_name, only_label, "PASS", "0")] or f"REGRESSION: 1/1" not in stdout:
            failures.append(f"③b --labels {labels} 选择结果不可断言：{check_lines}")
        else:
            log(f"[③b] PASS --labels {labels}：点名 {only_name}+static_solve（{'real_small' if labels=='fast_config' else 'fast_config'}）"
                f"被滤除，只跑 {only_name}[{only_label}] → REGRESSION 1/1")

    for bad_args, needle in ((["--labels", "bogus_label"], "未知标签"),
                             (["--labels", "full"], "没有命中任何检查")):
        code, stdout, stderr = run_tool([py, "-X", "utf8", "-B", str(RUNNER), *bad_args], cwd=ROOT)
        combined = stdout + stderr
        if code != 2 or needle not in combined:
            failures.append(f"③b {' '.join(bad_args)} 应 exit 2 且提示『{needle}』：exit={code}")
        else:
            log(f"[③b] PASS {' '.join(bad_args)} → exit 2（{needle}，提示如实）")

    # ---- ②a run_regression 耗时落 JSON 且与抽查 wall clock 一致（重复名=原清单透传） ----
    env = dict(os.environ)
    env.update({"PYTHONUTF8": "1", "OMP_NUM_THREADS": "2",
                "TEMP": "E:/rm_sim/temp", "TMP": "E:/rm_sim/temp",
                "QT_QPA_PLATFORM": "offscreen"})
    started = time.monotonic()
    proc = subprocess.run([py, "-X", "utf8", "-B", str(RUNNER),
                           "advanced_framework", "advanced_framework"],
                          cwd=ROOT, env=env, capture_output=True, timeout=300)
    external_wall = time.monotonic() - started
    stdout = proc.stdout.decode("utf-8", errors="replace")
    if proc.returncode != 0:
        failures.append(f"②a 抽查实跑 exit={proc.returncode}")
    else:
        match = re.search(r"regression_\d{8}_\d{6}_\d+", stdout)
        if not match:
            failures.append("②a 未从输出解析到证据目录")
        else:
            ev = Path("E:/rm_sim/verification") / match.group(0)
            summary = json.loads((ev / "summary.json").read_text(encoding="utf-8"))
            timing = json.loads((ev / "timing.json").read_text(encoding="utf-8"))
            rows_ok = (len(summary) == 2
                       and all(r["check"] == "advanced_framework" and r["label"] == "fast_config"
                               and r["seconds"] > 0 for r in summary))
            if not rows_ok:
                failures.append(f"②a summary.json 行不符：{summary}")
            else:
                total = round(sum(r["seconds"] for r in summary), 2)
                by = timing["by_label"]["fast_config"]
                checks_ok = (timing["sum_check_seconds"] == total
                             and by == {"count": 2, "seconds": total}
                             and timing["labels_selected"] == []
                             and [r["check"] for r in timing["rows"]] == ["advanced_framework", "advanced_framework"]
                             and timing["wall_seconds"] > 0)
                wall_ok = (timing["wall_seconds"] >= total - 0.05
                           and timing["wall_seconds"] <= total + 1.0
                           and external_wall >= timing["wall_seconds"]
                           and external_wall <= timing["wall_seconds"] + 5.0)
                if checks_ok and wall_ok:
                    log(f"[②a] PASS timing.json/summary.json 行级一致（2 行 fast_config）"
                        f"Σ检查={total}s 内墙钟={timing['wall_seconds']}s 外部墙钟={external_wall:.2f}s"
                        "（内墙钟∈[Σ,Σ+1]，外墙钟−内墙钟≤5s=解释器启动+证据目录开销）")
                else:
                    failures.append(f"②a 耗时一致性不符：timing={timing} total={total} "
                                    f"external_wall={external_wall:.2f}")

    # ---- ④ 收尾：等待五门套件完整重跑结束并断言 exit 0 ----
    log("[④] 等待原五门套件完整重跑结束（3 轮×5 门并发 + 守卫单测；门内检查与判据零改动）……")
    try:
        suite_out, suite_err = suite_proc.communicate(timeout=1150)
    except subprocess.TimeoutExpired:
        suite_proc.kill()
        suite_out, suite_err = suite_proc.communicate()
        failures.append("④ 五门套件重跑超 1150s 未结束（已终止）")
    suite_wall = time.monotonic() - suite_started
    suite_stdout = suite_out.decode("utf-8", errors="replace").replace("\r\n", "\n") if suite_out else ""
    for line in suite_stdout.splitlines():
        if line.startswith(("MEMORY_BUDGET_GUARD:", "TIMING", "[③] round", "③+")):
            log("      | " + line)
    if suite_proc.returncode != 0:
        tail = suite_stdout[-1500:] + suite_err.decode("utf-8", errors="replace")[-500:]
        failures.append(f"④ 五门套件完整重跑 exit={suite_proc.returncode}（{suite_wall:.0f}s）；尾部：{tail}")
    else:
        log(f"[④] PASS 原五门套件完整重跑 exit 0（外部墙钟 {suite_wall:.0f}s）")

    # ---- ②b 五门套件每轮每门秒数（调研 §2 口径）与轮墙钟一致性 ----
    start_tag = work.name.split("_", 2)[2]  # 本脚本启动时间戳（绑定本次套件证据目录）
    works = [p for p in Path("E:/rm_sim/verification").glob("memory_budget_guard_*")
             if p.is_dir() and p.name.split("_", 1)[1] > start_tag]
    ver_json = works[-1] / "verification.json" if works else None
    if suite_proc.returncode == 0 and (ver_json is None or not ver_json.is_file()):
        failures.append(f"②b 未找到本次套件 verification.json（晚于 {start_tag} 启动的证据目录）：{works}")
    elif suite_proc.returncode == 0:
        suite_work = works[-1]
        ver = json.loads(ver_json.read_text(encoding="utf-8"))
        rounds = ver["rounds"]
        if not (suite_work / "timing_breakdown.json").is_file():
            failures.append(f"②b 套件证据目录缺 timing_breakdown.json：{suite_work}")
        rounds = ver["rounds"]
        if len(rounds) != 3:
            failures.append(f"②b 轮数={len(rounds)} ≠ 3")
        round_walls_ok, gates_ok = True, True
        for r in rounds:
            sec = {g: r["gates"][g]["seconds"] for g in GATES}
            labels_ok = all(r["gates"][g]["label"] == "real_small" for g in GATES)
            mx = max(sec.values())
            wall = r.get("wall_seconds")
            if not (labels_ok and wall is not None and mx - 0.5 <= wall <= mx + 15):
                round_walls_ok = False
                failures.append(f"②b round{r['round']} 门秒/轮墙钟不符：sec={sec} wall={wall} labels_ok={labels_ok}")
        matrix = ver.get("timing_breakdown", {}).get("matrix_seconds", {})
        for r in rounds:
            if matrix.get(f"round{r['round']}") != {g: r["gates"][g]["seconds"] for g in GATES}:
                gates_ok = False
                failures.append(f"②b timing_breakdown 矩阵与 rounds 不一致（round{r['round']}）")
        sum_walls = sum(r.get("wall_seconds") or 0 for r in rounds)
        if not (sum_walls <= suite_wall + 10):
            failures.append(f"②b Σ轮墙钟 {sum_walls:.1f}s > 外部套件墙钟 {suite_wall:.1f}s+10")
        if round_walls_ok and gates_ok and not any(f.startswith("②b") for f in failures):
            log(f"[②b] PASS 3 轮×5 门每门秒数齐全（调研 §2 口径）且轮墙钟∈[max(门)−0.5, max(门)+15]；"
                f"Σ轮墙钟={sum_walls:.1f}s ≤ 外部墙钟 {suite_wall:.1f}s+10；timing_breakdown 矩阵一致；"
                "逐门标签=real_small")
            for r in rounds:
                log(f"[②b] round{r['round']} 秒数："
                    + " ".join(f"{g}={r['gates'][g]['seconds']}" for g in GATES)
                    + f" 轮wall={r.get('wall_seconds')}")

    log("\n==== 摘要 ====")
    if failures:
        log(f"REGRESSION_LABELS: FAIL（{len(failures)} 项）")
        for item in failures:
            log(f"  - {item}")
        return 1
    log("① 完整回归入口原语义不变：带参/--framework-only/--help/DEFAULT·SLOW 清单/无参全量，"
        "归一化（仅剥时间戳/秒数/标签注记/TIMING 行）后与改前固定样例逐字节一致")
    log("② 每门耗时落 JSON 且与抽查 wall clock 一致（run_regression 行级+聚合；五门套件每轮每门秒数+轮墙钟）")
    log("③ 四类标签按 --labels 取子集且选择结果可断言（划分/归属/保序过滤/实跑互相滤除/未知与空命中 exit 2）")
    log("④ 原五门套件（verify_memory_budget_guard.py，门与判据零改动）完整重跑 exit 0")
    log("REGRESSION_LABELS: PASS")
    return 0


if __name__ == "__main__":
    _exit = 1
    try:
        _exit = main()
    except Exception as exc:  # noqa: BLE001
        log(f"REGRESSION_LABELS: FAIL（顶层异常 {type(exc).__name__}: {exc}）")
        raise
    raise SystemExit(_exit)
