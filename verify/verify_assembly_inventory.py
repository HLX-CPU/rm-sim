# -*- coding: utf-8 -*-
"""装配现状盘点与基线快照（模块44）：只读盘点，产出后续装配功能的验收基线 JSON。

盘点三块（接续纪律：先盘点、不推倒重做）：
  ① 工作区未提交装配相关改动（git 只读实测：status/diff --numstat，跟踪+未跟踪分开计数）；
  ② 自动连接草稿遗留：151 候选 → 39 补丁 / 22 部件对、28 个 requires_review 未复核、
     另有 18 个无效 SPC（自由度已固定）诊断号——单列不混算；
  ③ 9 个基线失败回归脚本逐个归类：2 编排无参调用、2 chrono 环境导入、
     5 内存守卫/ccx 0xC0000005（来源：2026-11-02 基线回归，CLI 运行库 ask#6 核验记录）。

只读自证（验收标准：不写项目文件、不启动 CAD/网格/求解器，exit 0 = 通过）：
  - 禁进程断言：subprocess.Popen / os.system / os.spawn* / os.exec* / os.startfile
    全部拦截，仅放行 `git` 只读子命令（status/diff/rev-parse/log）；任何其他进程立即抛错；
  - 写盘断言：运行前后各取一次 `git status --porcelain -z` 快照并断言一致
    （脚本自身只写 verify/logs/ 日志——gitignore 内——与证据目录 JSON，均在项目树外或被忽略）。

证据路径：
  - 日志 verify/logs/verify_assembly_inventory.log；
  - 盘点 JSON 落 E:/rm_sim/verification/assembly_inventory_<时间戳>/assembly_inventory.json。
基线失败记录：dwfrun-88bb5b72（门脚本实跑，22:23-22:27 日志）→ dwfrun-0944b8fb 续跑
ask#6（基线结果核验员，23:55:12 完成分类）；运行库以 mode=ro 只读读取，仅作核对，不在其写入。
"""
from __future__ import annotations

import datetime
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
sys.dont_write_bytecode = True

LOG_PATH = ROOT / "verify" / "logs" / "verify_assembly_inventory.log"
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


# ---------------------------------------------------------------- 进程守卫
LAUNCHED: list[dict] = []


class ForbiddenProcess(AssertionError):
    """盘点脚本试图启动 CAD/网格/求解器或任何非 git 只读命令。"""


ALLOWED_GIT_VERBS = {"status", "diff", "rev-parse", "log"}


def _install_process_guard() -> None:
    real_popen = subprocess.Popen

    # git 全局选项（值跟随）：扫描子命令时成对跳过
    GIT_GLOBAL_OPTS_WITH_VALUE = {"-c", "-C", "--exec-path", "--git-dir", "--work-tree",
                                  "--namespace", "--super-prefix", "--config-env"}

    def git_verb(argv: list[str]) -> str:
        rest = argv[1:]
        index = 0
        while index < len(rest):
            token = str(rest[index])
            if token in GIT_GLOBAL_OPTS_WITH_VALUE:
                index += 2
                continue
            if token.startswith("-"):
                index += 1
                continue
            return token
        return ""

    def guarded_popen(args, **kwargs):
        argv = [args] if isinstance(args, str) else list(args)
        exe_name = Path(str(argv[0])).name.lower()
        joined = " ".join(str(a) for a in argv)
        if exe_name == "git":
            verb = git_verb([str(a) for a in argv])
            if verb not in ALLOWED_GIT_VERBS:
                raise ForbiddenProcess(f"git 子命令不在只读白名单 {sorted(ALLOWED_GIT_VERBS)}: {verb}")
            LAUNCHED.append({"kind": "git_readonly", "cmd": joined[:200]})
            return real_popen(args, **kwargs)
        raise ForbiddenProcess(f"盘点脚本禁止启动进程（禁进程断言）：{joined[:200]}")

    subprocess.Popen = guarded_popen  # subprocess.run/check_output 内部经模块全局 Popen，同样被拦

    def _deny(name):
        def denied(*args, **kwargs):
            raise ForbiddenProcess(f"盘点脚本禁止 os.{name}（禁进程断言）")
        return denied

    os.system = _deny("system")
    os.startfile = _deny("startfile")
    for family in ("spawn", "exec", "posix_spawn"):
        for attr in (a for a in dir(os) if a.startswith(family)):
            setattr(os, attr, _deny(attr))


def git(*args: str, timeout: int = 120) -> str:
    result = subprocess.run(["git", "-c", "core.quotepath=false", *args],
                            cwd=ROOT, capture_output=True, timeout=timeout)
    if result.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} 失败 exit={result.returncode}: "
                             f"{result.stderr.decode('utf-8', errors='replace')[:300]}")
    return result.stdout.decode("utf-8", errors="replace")


# ---------------------------------------------------------------- 盘点①：工作区
def group_of(path: str) -> str:
    p = path.replace("\\", "/")
    if p.startswith("app/rm_sim/"):
        return "assembly_chain_code"
    if p.startswith("app/docs/"):
        return "module_docs"
    if p.startswith("verify/"):
        return "verify"
    if p.startswith(("analysis/", "probes/")) or p.endswith(".md"):
        return "docs_or_probes"
    return "other"


def status_entries() -> list[tuple[str, str]]:
    """porcelain v1 -z：XY<空格>path，NUL 分隔；R/C 记录带第二段 origPath。"""
    raw = git("status", "--porcelain=v1", "-z")
    entries: list[tuple[str, str]] = []
    tokens = [t for t in raw.split("\0") if t]
    index = 0
    while index < len(tokens):
        token = tokens[index]
        xy, path = token[:2], token[3:]
        if xy[0] in "RC" and index + 1 < len(tokens):
            index += 1  # 跳过 rename 原路径
        entries.append((xy, path))
        index += 1
    return entries


def snapshot_status() -> str:
    return git("status", "--porcelain=v1", "-z")


def inventory_workspace() -> dict:
    modified, deleted, untracked = [], [], []
    for xy, path in status_entries():
        if xy == "??":
            untracked.append(path)
        elif "D" in xy:
            deleted.append(path)
        else:
            modified.append(path)

    numstat: list[dict] = []
    for line in git("diff", "--numstat").splitlines():
        if not line.strip():
            continue
        ins, dels, path = line.split("\t", 2)
        numstat.append({"path": path, "insertions": int(ins) if ins != "-" else 0,
                        "deletions": int(dels) if dels != "-" else 0,
                        "group": group_of(path)})
    insertions = sum(e["insertions"] for e in numstat)
    deletions = sum(e["deletions"] for e in numstat)
    head = git("rev-parse", "HEAD").strip()
    branch = git("rev-parse", "--abbrev-ref", "HEAD").strip()
    head_info = git("log", "-1", "--format=%H %ci %s").strip()

    untracked_groups: dict[str, list] = {}
    for path in untracked:
        untracked_groups.setdefault(group_of(path), []).append(path)

    measured = {
        "tracked_modified": len(modified),
        "tracked_deleted": len(deleted),
        "untracked_paths": len(untracked),
        "total_uncommitted_paths": len(modified) + len(deleted) + len(untracked),
        "tracked_diff": {"files": len(numstat), "insertions": insertions, "deletions": deletions},
        "git_head": head, "git_branch": branch, "git_head_commit": head_info,
    }
    log(f"[①] 跟踪修改={measured['tracked_modified']} 删除={measured['tracked_deleted']} "
        f"未跟踪={measured['untracked_paths']} ；git diff --numstat 实测 "
        f"+{insertions}/-{deletions}（{len(numstat)} 个跟踪文件）")
    for entry in numstat:
        log(f"     {entry['path']}: +{entry['insertions']}/-{entry['deletions']} [{entry['group']}]")
    for name, paths in sorted(untracked_groups.items()):
        log(f"     未跟踪[{name}]={len(paths)}")
    return {
        "task_claim": {
            "uncommitted_files": 27, "insertions": 1468, "deletions": 368,
            "note": "任务书引用数字（上一轮中断时的快照口径）；与本脚本当前实测不一致："
                    "git diff --numstat 只覆盖跟踪文件、未含 139 条未跟踪路径，且工作区随后续提交演进。"
                    "两套数字并列如实记录，不互相冒充。",
        },
        "measured": measured,
        "tracked_files": numstat,
        "untracked": {"total": len(untracked),
                      "groups": {k: len(v) for k, v in sorted(untracked_groups.items())},
                      "assembly_chain_code": untracked_groups.get("assembly_chain_code", []),
                      "verify": untracked_groups.get("verify", []),
                      "module_docs": untracked_groups.get("module_docs", [])},
    }


# ---------------------------------------------------------------- 盘点②：连接草稿遗留
DRAFT_JSON = Path("E:/rm_sim/verification/draft_qr_asm_named_20261102_120005/draft.json")
SOLVE_DIR = Path("E:/rm_sim/verification/current_assembly_solve7_c12_20261102_1935")


def _load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def inventory_connections() -> tuple[dict, dict]:
    sources = {"draft_json": str(DRAFT_JSON), "solve_dir": str(SOLVE_DIR)}
    try:
        draft = _load_json(DRAFT_JSON)
        result = _load_json(SOLVE_DIR / "result.json")
        independent = _load_json(SOLVE_DIR / "independent_check.json")
        attribution = _load_json(SOLVE_DIR / "warning_attribution.json")
    except (OSError, ValueError) as exc:
        log(f"[②] 证据读取失败，如实记 unknown：{exc}")
        return ({"status": "unknown", "reason": str(exc), "sources": sources}, {})

    from rm_sim.solver.diagnostics import _read_node_set, tie_warning_kinds

    case_dir = SOLVE_DIR / "cases" / "case_001"
    nam = case_dir / "assembly_full_WarnNodeMissTiedContact.nam"
    log_file = case_dir / "assembly_full.log"
    missed = _read_node_set(nam)
    kinds = tie_warning_kinds(log_file)
    untied = missed & (kinds["no_face"] | kinds["too_far"])
    dof_inactive = sorted(missed - untied)

    patches = result.get("connections")
    pairs = draft.get("joint_pairs")
    candidates = len(draft.get("candidates", []))
    requires_review = independent.get("untied_nodes")
    reported = independent.get("reported_missed_nodes")

    log(f"[②] 草稿候选={candidates} 部件对={pairs} 最终求解补丁(连接)={patches} "
        f"覆盖部件={len(draft.get('covered_parts', []))}/{draft.get('body_count')}")
    log(f"[②] 报告编号={reported} 真未绑定(requires_review)={requires_review} "
        f"SPC 自由度已固定={len(dof_inactive)}（单列）")
    log(f"[②] 分类细目 no_face={independent['warning_kinds'].get('no_face')} "
        f"too_far={independent['warning_kinds'].get('too_far')} "
        f"dof_inactive={independent['warning_kinds'].get('dof_inactive')}")

    stats = {
        "status": "ok",
        "sources": {**sources,
                    "independent_check": str(SOLVE_DIR / "independent_check.json"),
                    "warning_attribution": str(SOLVE_DIR / "warning_attribution.json"),
                    "case_dir": str(case_dir)},
        "candidates_total": candidates,
        "part_pairs": pairs,
        "patches_in_final_deck": patches,
        "bodies": draft.get("body_count"),
        "covered_parts": len(draft.get("covered_parts", [])),
        "unconnected_parts": len(draft.get("unconnected_parts", [])),
        "draft_step_sha256": draft.get("step_sha256"),
        "requires_review_count": requires_review,
        "requires_review_meaning": "真未绑定的从面节点数（复核前不计合格；diagnostics.requires_review 语义）",
        "reported_missed_nodes": reported,
        "solve": {"elements": result.get("elements"), "nodes": result.get("nodes"),
                  "job_finished": result.get("job_finished"),
                  "gravity_only_note": "整装仅自重工况（约2.177N），无外载，不能当强度结论"},
        "recomputed_matches_independent_check": bool(
            len(untied) == requires_review and len(dof_inactive)
            == independent.get("warning_kinds", {}).get("dof_inactive") and len(missed) == reported),
    }
    spc = {
        "count": len(dof_inactive),
        "node_numbers": dof_inactive,
        "meaning": "18 个『DOF 已被 SPC 固定而跳过绑定』的诊断编号（求解器正常行为），"
                   "单列不与 28 个真未绑定混算（ZCODE交接说明书 §3.1）",
        "breakdown": {"no_face": len(kinds["no_face"] & missed), "too_far": len(kinds["too_far"] & missed),
                      "dof_inactive": len(dof_inactive)},
        "evidence": [str(nam), str(log_file),
                     str(SOLVE_DIR / "warning_attribution.json"),
                     str(SOLVE_DIR / "independent_check.json")],
    }
    return stats, spc


# ---------------------------------------------------------------- 盘点③：基线失败
MEMORY_GUARD = "求解内存达到预算"
NEAR_LIMIT = "峰值提交内存接近内核作业限额"
CRASH_TOKENS = ("3221225477", "0xC0000005")  # 同一访问违例的十进制/十六进制写法

BASELINE_ITEMS = [
    {"script": "verify/verify_assembly_results.py", "bucket": "memory_guard_or_ccx_crash",
     "category": "内存守卫截停（tasks.py 内存预算守卫）",
     "evidence_paths": ["verify/logs/verify_assembly_results.log"],
     "signatures": [MEMORY_GUARD],
     "note": "rods_tie×4 与 blocks 共 5 个求解任务 state=failed，内存预算守卫截停；GUI 冒烟与静态扫描段仍 PASS。"},
    {"script": "verify/verify_bolt_pin_connection.py", "bucket": "memory_guard_or_ccx_crash",
     "category": "内存守卫截停（仅销轴求解段）",
     "evidence_paths": ["verify/logs/verify_bolt_pin_connection.log"],
     "signatures": [MEMORY_GUARD],
     "note": "螺栓段物理门全过（预紧自平衡/2000N 界面力/−500N 接地反力）；销轴 pin_solve 任务被守卫截停。"},
    {"script": "verify/verify_bonded_connection.py", "bucket": "memory_guard_or_ccx_crash",
     "category": "内存守卫截停（仅步骤 E⑤ 1mm 收敛点）",
     "evidence_paths": ["verify/logs/verify_bonded_connection.log"],
     "signatures": [MEMORY_GUARD],
     "note": "2mm 主门全过（δ 0.198%）；1mm 网格（94726 单元/145613 节点）求解被截停，收敛数据点缺失。"},
    {"script": "verify/verify_contact_friction_connection.py", "bucket": "memory_guard_or_ccx_crash",
     "category": "ccx 0xC0000005 访问违例（内存压力）",
     "evidence_paths": ["verify/logs/verify_contact_friction_connection.log"],
     "signatures": [NEAR_LIMIT, "3221225477"],
     "note": "接触五组工况全部 exit=3221225477（0xC0000005）；上一轮同门全 PASS，非接触算法回归。"},
    {"script": "verify/verify_static_solve.py", "bucket": "memory_guard_or_ccx_crash",
     "category": "内存守卫截停 + ccx 0xC0000005（混合）",
     "evidence_paths": ["verify/logs/verify_static_solve.log"],
     "signatures": [MEMORY_GUARD, NEAR_LIMIT, "3221225477"],
     "note": "beamcheck 主门被守卫截停；重力/力矩任务 0xC0000005；压力门 ΣRFx=199.999999N 仍过。"},
    {"script": "verify/verify_chrono_load_transfer.py", "bucket": "chrono_env_import",
     "category": "chrono 环境导入失败（cp313 numpy 污染 Python3.12 chrono-env）",
     "evidence_paths": ["E:/rm_sim/verification/chrono_20261102_222504_775873/motion/chrono_stderr.log"],
     "signatures": ["ImportError", "numpy"],
     "note": "chrono-env(Python3.12) 加载了 E:/Codex/python-packages 下 numpy 2.5.3 cp313 二进制 → ImportError。"},
    {"script": "verify/verify_extensions_gui.py", "bucket": "chrono_env_import",
     "category": "chrono 环境导入失败（同上，GUI Chrono 入口链路）",
     "evidence_paths": ["E:/rm_sim/verification/extensions_gui_20261102_222529_258699/chrono_20261102_222529/chrono_stderr.log"],
     "signatures": ["ImportError", "numpy"],
     "note": "主窗口本身构建成功；仅 run_chrono 的 PyChrono 导入崩溃，Qt warning 断言捕获。"},
    {"script": "verify/verify_full_assembly_solve.py", "bucket": "orchestration_no_arg_call",
     "category": "编排无参调用（argparse 缺 case_dir）",
     "evidence_paths": ["verify/verify_full_assembly_solve.py"],
     "signatures": [],
     "static_check": "usage_requires_case_dir",
     "note": "只读复核工具，须 `python verify/verify_full_assembly_solve.py <case_dir>`；"
             "基线被无参调用 → 'error: the following arguments are required: case_dir'，工具未执行任何检查。"},
    {"script": "verify/verify_workflow_bundle_relocation.py", "bucket": "orchestration_no_arg_call",
     "category": "编排无参调用（缺 argv[1] 完成记录 JSON）",
     "evidence_paths": ["verify/verify_workflow_bundle_relocation.py"],
     "signatures": [],
     "static_check": "requires_argv1",
     "note": "独立工具，需传入后台工作流完成记录 JSON 路径（脚本第 15 行 sys.argv[1]）；"
             "基线无参调用 → IndexError: list index out of range，迁移逻辑未执行。"},
]

# 基线运行库核对是可选步骤：原始开发机指向 ZCode 的只读 sqlite；
# 开源环境通常没有该库，此时 read_baseline_record() 如实返回 unavailable，
# 不伪造交叉核对结论。需要时用 RM_SIM_BASELINE_DB 指向自己的运行库。
DB_PATH = Path(os.environ.get("RM_SIM_BASELINE_DB", str(ROOT / ".zcode" / "baseline.sqlite")))
BASELINE_RUN_ID = "dwfrun-0944b8fb-f7fd-426e-a7af-4b821344e0d4"
BASELINE_GATE_RUN_ID = "dwfrun-88bb5b72-b873-4ab4-8f51-0e675524e436"


def read_baseline_record() -> dict:
    """运行库只读核对：ask#6（基线结果核验员）记录的 53 门通过/失败清单。"""
    if not DB_PATH.exists():
        return {"db_cross_check": "unavailable", "reason": f"{DB_PATH} 不存在"}
    try:
        con = sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=ro", uri=True, timeout=5)
    except sqlite3.Error as exc:
        return {"db_cross_check": "unavailable", "reason": str(exc)}
    try:
        cur = con.cursor()
        cur.execute("select result_json from dwf_node where run_id=? and site_id='ask#6' and kind='ask'",
                    (BASELINE_RUN_ID,))
        row = cur.fetchone()
        if not row:
            return {"db_cross_check": "unavailable", "reason": "ask#6 记录不存在"}
        record = json.loads(row[0])
        failed = sorted(g["script"] for g in record.get("gates", []) if not g.get("pass", True))
        cur.execute("select resumed_from, status from dwf_run where id=?", (BASELINE_RUN_ID,))
        resumed_from, status = cur.fetchone()
        return {"db_cross_check": "loaded",
                "run_id": BASELINE_RUN_ID, "run_status": status, "resumed_from": resumed_from,
                "gate_run_id": BASELINE_GATE_RUN_ID,
                "record_note": "门脚本实跑于 dwfrun-88bb5b72（2026-11-02 22:06 起，日志 22:23-22:27）；"
                               "dwfrun-0944b8fb 为其续跑，ask#6 于 2026-11-02 23:55:12 完成归类",
                "total_gates": len(record.get("gates", [])),
                "passed": record.get("passedCount"), "failed": record.get("failedCount"),
                "failed_scripts": failed,
                "summary": record.get("summary", "")}
    except (sqlite3.Error, ValueError) as exc:
        return {"db_cross_check": "unavailable", "reason": str(exc)}
    finally:
        con.close()


def inventory_baseline() -> dict:
    items = []
    for spec in BASELINE_ITEMS:
        item = dict(spec)
        exists, found = [], []
        for ev in spec["evidence_paths"]:
            path = Path(ev) if Path(ev).is_absolute() else ROOT / ev
            exists.append(bool(path.exists()))
            if path.exists() and spec.get("signatures"):
                text = path.read_text(encoding="utf-8", errors="replace")
                found.append({s: (s in text) for s in spec["signatures"]})
        static = None
        if spec.get("static_check") == "usage_requires_case_dir":
            text = (ROOT / spec["script"]).read_text(encoding="utf-8", errors="replace")
            static = ("<case_dir>" in text) and ("case_dir" in text)
        elif spec.get("static_check") == "requires_argv1":
            text = (ROOT / spec["script"]).read_text(encoding="utf-8", errors="replace")
            static = "sys.argv[1]" in text
        item.update({"evidence_exists": exists, "signature_found": found, "static_check_passed": static})
        items.append(item)
        log(f"[③] {spec['script']} → {spec['bucket']}；证据存在={all(exists)} "
            f"签名={found if found else static}")

    record = read_baseline_record()
    log(f"[③] 运行库核对：{record['db_cross_check']}；"
        f"passed={record.get('passed')}/{record.get('total_gates')} failed={record.get('failed')}")
    buckets = {}
    for item in items:
        buckets[item["bucket"]] = buckets.get(item["bucket"], 0) + 1
    return {"source": "2026-11-02 基线回归（全部 verify/verify_*.py 逐个实跑）+ CLI 运行库 ask#6 核验记录",
            "record": record, "buckets": buckets, "items": items}


# ---------------------------------------------------------------- 主流程
def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    _install_process_guard()
    log("装配现状盘点与基线快照（只读）：开始")

    failures: list[str] = []

    # 写盘断言基线：运行前快照
    before = snapshot_status()

    workspace = inventory_workspace()
    conn_stats, spc = inventory_connections()
    baseline = inventory_baseline()

    # 写盘断言：运行后快照必须一致（项目树内零改动；日志与证据目录在忽略清单/树外）
    after = snapshot_status()
    untouched = (before == after)
    log(f"[自证] git status 运行前后一致（未写任何项目文件）: {untouched}")
    status_diff_detail = ""
    if not untouched:
        before_set, after_set = set(before.split("\0")), set(after.split("\0"))
        status_diff_detail = json.dumps({"disappeared": sorted(before_set - after_set),
                                         "appeared": sorted(after_set - before_set)},
                                        ensure_ascii=False)[:1000]
        log(f"[自证] 快照差异：{status_diff_detail}")

    # 禁进程断言
    forbidden = [entry for entry in LAUNCHED if entry["kind"] != "git_readonly"]
    log(f"[自证] 进程守卫：本次共启动 {len(LAUNCHED)} 个进程，全部为 git 只读命令；"
        f"违规进程={len(forbidden)}")

    # ---- 门 ----
    if not untouched:
        failures.append("写盘断言失败：git status 运行前后不一致 " + status_diff_detail)
    if forbidden:
        failures.append("禁进程断言失败：存在非 git 只读进程")
    if len(baseline["items"]) != 9:
        failures.append(f"基线失败脚本应为 9 个，实际 {len(baseline['items'])}")
    for item in baseline["items"]:
        if not item["category"] or not item["evidence_paths"]:
            failures.append(f"基线项缺归类或证据路径: {item['script']}")
        if not all(item["evidence_exists"]):
            failures.append(f"基线项证据文件缺失: {item['script']}")
        if item.get("signature_found"):
            for entry in item["signature_found"]:
                if not all(entry.values()):
                    failures.append(f"基线项证据签名缺失: {item['script']} {entry}")
        if item.get("static_check") is not None and not item["static_check_passed"]:
            failures.append(f"基线项静态核查失败: {item['script']}")
    expected_buckets = {"orchestration_no_arg_call": 2, "chrono_env_import": 2,
                        "memory_guard_or_ccx_crash": 5}
    if baseline["buckets"] != expected_buckets:
        failures.append(f"基线归类桶应为 {expected_buckets}，实际 {baseline['buckets']}")
    record = baseline["record"]
    if record["db_cross_check"] == "loaded":
        if record["failed"] != 9 or record["failed_scripts"] != sorted(i["script"] for i in baseline["items"]):
            failures.append("运行库 ask#6 失败清单与盘点归类不一致")
    if conn_stats.get("status") == "ok":
        if conn_stats["candidates_total"] != 151 or conn_stats["part_pairs"] != 22 \
                or conn_stats["patches_in_final_deck"] != 39:
            failures.append("连接草稿统计与预期不符（151/22/39）")
        if conn_stats["requires_review_count"] != 28:
            failures.append(f"requires_review 计数应为 28，实际 {conn_stats['requires_review_count']}")
        if spc["count"] != 18 or len(spc["node_numbers"]) != 18:
            failures.append(f"SPC 诊断号应为 18 个，实际 {spc['count']}")
        if not conn_stats["recomputed_matches_independent_check"]:
            failures.append("复算 46/28/18 与 independent_check.json 不一致")
    else:
        log("[②] 证据不可读，已如实记 unknown（验收允许，不得编造）")

    payload = {
        "module": "模块44 装配现状盘点与基线快照",
        "created": datetime.datetime.now().isoformat(timespec="seconds"),
        "script": "verify/verify_assembly_inventory.py",
        "read_only_proof": {
            "processes_launched": LAUNCHED,
            "forbidden_process_launched": bool(forbidden),
            "process_guard": "subprocess.Popen/os.system/os.spawn*/os.exec*/os.startfile 全拦截，"
                             "仅放行 git status/diff/rev-parse/log 只读子命令",
            "git_status_identical_before_after": untouched,
            "script_writes": ["verify/logs/verify_assembly_inventory.log（gitignore 内）",
                              "证据目录 assembly_inventory_*.json（E:/rm_sim/verification，项目树外）"],
            "no_cad_mesh_solver_started": not forbidden,
        },
        "workspace_changes": workspace,
        "connection_draft_leftovers": conn_stats,
        "spc_dof_inactive_diagnostics": spc,
        "baseline_failures": baseline,
        "gates_failed": failures,
    }

    out_dir = Path("E:/rm_sim/verification") / (
        "assembly_inventory_" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f"))
    out_dir.mkdir(parents=True, exist_ok=False)
    out_path = out_dir / "assembly_inventory.json"
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"[出] 盘点 JSON：{out_path}")

    if failures:
        log(f"盘点未通过（{len(failures)} 项）：")
        for reason in failures:
            log(f"  - {reason}")
        log("INVENTORY: FAIL")
        return 1

    log("")
    log("==== 盘点摘要 ====")
    m = workspace["measured"]
    log(f"① 工作区：跟踪修改 {m['tracked_modified']} + 删除 {m['tracked_deleted']} + 未跟踪 "
        f"{m['untracked_paths']} = {m['total_uncommitted_paths']} 条路径；"
        f"跟踪 diff 实测 +{m['tracked_diff']['insertions']}/-{m['tracked_diff']['deletions']} "
        f"（任务书口径 27 文件 +1468/-368 并列记录于 task_claim）")
    c = conn_stats
    log(f"② 连接草稿遗留：候选 {c['candidates_total']} → 补丁(连接) {c['patches_in_final_deck']} / "
        f"部件对 {c['part_pairs']}；requires_review（真未绑定）{c['requires_review_count']} 个未复核；"
        f"另有 18 个 SPC 自由度已固定的诊断号单列：{spc['node_numbers']}")
    b = baseline
    log(f"③ 基线失败：{record.get('passed', '?')}/{record.get('total_gates', '?')} 门通过，"
        f"{len(b['items'])} 个失败脚本归类齐全："
        f"编排无参调用 {b['buckets']['orchestration_no_arg_call']}、"
        f"chrono 环境导入 {b['buckets']['chrono_env_import']}、"
        f"内存守卫/ccx 0xC0000005 {b['buckets']['memory_guard_or_ccx_crash']}")
    for item in b["items"]:
        log(f"    - {item['script']} [{item['bucket']}] 证据: {item['evidence_paths'][0]}")
    log("[自证] 零违规进程、零项目文件写入（git status 前后一致）")
    log("INVENTORY: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
