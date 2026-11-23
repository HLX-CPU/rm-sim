"""按显式列表运行回归，保存每个进程的输出、退出码、时长及失败原因。"""
import argparse
import datetime
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from assembly_check_utils import ROOT, PYTHON, EVIDENCE_ROOT, evidence_dir
import suite_labels  # 套件标签（fast_config/real_small/reliability/full）——只作选择与报告（调研 §7-1）

DEFAULT = ["main_entry", "step_import", "material_library", "load_definition", "mesh_generation",
           "static_solve", "results_view", "report_archive", "relaunch", "project_roundtrip",
           "assembly_import", "assembly_loads", "connection_model", "assembly_solve", "bonded_connection",
           "contact_friction_connection", "bolt_pin_connection", "assembly_results", "assembly_example",
           "assembly_project_roundtrip", "gui_assembly", "assembly_geometry", "history_restore", "step_material_gate",
           "contact_candidates", "geometry_inspection_gui", "invalid_mesh_gate", "connection_draft", "material_suggestions",
           "scheduler_lifecycle", "tie_diagnostics", "solver_gui_lifecycle", "background_workflow", "tie_policy",
           "bond_band", "prepare_retry", "reaction_semantics", "local_mesh_controls", "consistent_traction", "advanced_framework"]

# 分钟级/小时级检查：不进默认回归（会拖长每次回归），用 --slow 显式追加。
SLOW = ["assembly_auto_connections", "real_assembly_prepare_cancel"]

SOURCE_REUSED = "复用历史求解并重新检查"
SOURCE_NEW = "本轮新求解"
SOURCE_NOT_RUN = "未执行/待验证"
SOURCE_PLAIN = "本轮执行（未参与共享产物）"


def _latest_regression_summary():
    """最近一轮 run_regression 证据（目录名 regression_纯时间戳 的 summary.json）。"""
    root = EVIDENCE_ROOT
    best = None
    for d in sorted(root.glob("regression_*")):
        if not re.fullmatch(r"regression_\d{8}_\d{6}_\d+", d.name):
            continue
        p = d / "summary.json"
        if p.is_file():
            best = p
    if best is None:
        return None
    try:
        return json.loads(best.read_text(encoding="utf-8")), best
    except (json.JSONDecodeError, OSError):
        return None


def _product_source(stdout_path: Path) -> str:
    """从检查 stdout 提取 [product] source=… 标记（共享产物模块51口径）；无标记如实回退。"""
    try:
        text = stdout_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return SOURCE_PLAIN
    marks = re.findall(r"\[product\] source=(\S+)", text)
    return marks[-1] if marks else SOURCE_PLAIN


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("checks",nargs="*")
    parser.add_argument("--slow",action="store_true",help="额外运行慢速检查（整装 20 实体连接草稿）")
    parser.add_argument('--framework-only',action='store_true',help='仅配置框架检查，不启动CAD、网格或求解器')
    parser.add_argument('--labels',default='',help='套件标签过滤，逗号分隔（fast_config/real_small/reliability/full）；只作选择与报告，不改门与容差')
    parser.add_argument('--only-failed',action='store_true',help='只重跑最近一轮回归证据中的失败项（可与 --labels 取交集）；其余项在报告中标注「未执行/待验证」')
    args = parser.parse_args()
    if args.framework_only:
        if args.checks or args.slow or args.labels or args.only_failed:parser.error('--framework-only不能与其他检查、--slow、--labels或--only-failed混用')
        args.checks=['advanced_framework']
    if args.slow and args.only_failed:parser.error('--only-failed不能与--slow混用')
    if args.only_failed and args.checks:parser.error('--only-failed不能与显式检查混用（失败项来自最近证据）')
    # 标签解析（新增能力；无 --labels 时选择行为与改前逐行一致）。
    selected_labels=[]
    if args.labels:
        try:
            selected_labels=suite_labels.parse_labels(args.labels)
        except ValueError as exc:
            parser.error(str(exc))
    baseline_rows=None
    if args.only_failed:
        latest=_latest_regression_summary()
        if latest is None:
            parser.error(f'--only-failed 找不到最近回归证据（{EVIDENCE_ROOT}/regression_时间戳/summary.json）')
        baseline_rows,baseline_path=latest
        failed=[r["check"] for r in baseline_rows if r["exit_code"]!=0]
        if args.labels:
            failed=suite_labels.select_by_labels(failed,selected_labels)
        args.checks=failed
        print(f"ONLY_FAILED: 基线={baseline_path} 失败项={failed}",flush=True)
    else:
        if args.slow and not args.checks:
            args.checks = list(DEFAULT) + list(SLOW)
        # 标签选择（新增能力；无 --labels 时选择行为与改前逐行一致，见 ①原语义口径）。
        if args.labels:
            universe = list(args.checks) if args.checks else list(DEFAULT)
            args.checks = suite_labels.select_by_labels(universe, selected_labels)
            if not args.checks:
                parser.error(f"--labels {args.labels} 在当前选择范围内没有命中任何检查"
                             "（full 标签仅覆盖 --slow 清单；可显式点名检查或加 --slow）")
    explicit_given = bool(args.checks) and baseline_rows is None
    if args.only_failed and not args.checks:
        # 无失败项（或标签交集为空）：不重跑任何检查，原样报告并如实标注未执行项。
        work = evidence_dir("regression")
        print(f"回归证据: {work}",flush=True)
        print("REGRESSION: 0/0，失败=[]（--only-failed 无需重跑：最近证据无失败项或标签交集为空）",flush=True)
        not_executed=[{"check":r["check"],"label":r.get("label") or suite_labels.try_label(r["check"]),
                       "source":SOURCE_NOT_RUN} for r in (baseline_rows or [])]
        timing={"evidence_dir":str(work),"wall_seconds":0.0,"labels_selected":selected_labels,
                "only_failed":{"baseline_rows":len(baseline_rows or []),"rerun":[]},
                "by_label":{},"sum_check_seconds":0.0,"checks":0,"rows":[],
                "sources":{SOURCE_NOT_RUN:len(not_executed)},"not_executed":not_executed}
        (work/"timing.json").write_text(json.dumps(timing,ensure_ascii=False,indent=2),encoding="utf-8")
        print(f"TIMING: wall=0.0s 检查合计=0.0s",flush=True)
        print(f"TIMING_JSON: {work/'timing.json'}",flush=True)
        return 0
    work = evidence_dir("regression")
    print(f"回归证据: {work}",flush=True)
    outcomes=[]
    suite_started=time.monotonic()
    for name in args.checks or DEFAULT:
        label=suite_labels.try_label(name)
        script=ROOT/"verify"/f"verify_{name}.py"
        temp=Path(EVIDENCE_ROOT)/"tmp"
        temp.mkdir(parents=True,exist_ok=True)
        env=dict(os.environ,PYTHONUTF8="1",OMP_NUM_THREADS="2",TEMP=str(temp),TMP=str(temp))
        env["QT_QPA_PLATFORM"]="offscreen"
        start=time.monotonic()
        with (work/f"{name}.stdout.log").open("wb") as out,(work/f"{name}.stderr.log").open("wb") as err:
            try:
                r=subprocess.run([str(PYTHON),"-X","utf8","-B",str(script)],cwd=ROOT,env=env,
                                 stdout=out,stderr=err,timeout=1200)
                code=r.returncode
            except subprocess.TimeoutExpired:
                code="TIMEOUT"
        row={"check":name,"label":label,"source":_product_source(work/f"{name}.stdout.log"),
             "exit_code":code,"seconds":round(time.monotonic()-start,2),
             "stdout":str(work/f"{name}.stdout.log"),"stderr":str(work/f"{name}.stderr.log")}
        outcomes.append(row)
        (work/"summary.json").write_text(json.dumps(outcomes,ensure_ascii=False,indent=2),encoding="utf-8")
        print(f"{name} [{label}]: {'PASS' if code==0 else 'FAIL'} exit={code} {row['seconds']}s",flush=True)
    wall_seconds=round(time.monotonic()-suite_started,2)
    failed=[x["check"] for x in outcomes if x["exit_code"]!=0]
    print(f"REGRESSION: {len(outcomes)-len(failed)}/{len(outcomes)}，失败={failed}",flush=True)
    # 耗时分解、来源单列与标签报告（新增输出；口径=调研 §2 表「每门/秒」与 §4
    # 报告来源单列。原输出行（回归证据/逐检查/REGRESSION）保持在前，新增信息只追加其后。）
    sources={}
    for r in outcomes:
        sources[r["source"]]=sources.get(r["source"],0)+1
    not_executed=[]
    if baseline_rows is not None:
        executed={r["check"] for r in outcomes}
        not_executed=[{"check":r["check"],"label":r.get("label") or suite_labels.try_label(r["check"]),
                       "source":SOURCE_NOT_RUN} for r in baseline_rows if r["check"] not in executed]
    elif args.labels and explicit_given is False:
        universe=list(DEFAULT)+(list(SLOW) if args.slow else [])
        executed={r["check"] for r in outcomes}
        not_executed=[{"check":n,"label":suite_labels.try_label(n),"source":SOURCE_NOT_RUN}
                      for n in universe if n not in executed]
    timing={"evidence_dir":str(work),
            "wall_seconds":wall_seconds,
            "labels_selected":selected_labels,
            **suite_labels.breakdown(outcomes),
            "rows":[{"check":r["check"],"label":r["label"],"source":r["source"],
                     "exit_code":r["exit_code"],"seconds":r["seconds"]} for r in outcomes],
            "sources":sources,
            "not_executed":not_executed}
    if baseline_rows is not None:
        timing["only_failed"]={"baseline_rows":len(baseline_rows),"rerun":[r["check"] for r in outcomes]}
    (work/"timing.json").write_text(json.dumps(timing,ensure_ascii=False,indent=2),encoding="utf-8")
    parts=" ".join(f"{lb}={data['count']}项/{data['seconds']}s" for lb,data in timing["by_label"].items())
    print(f"TIMING: wall={wall_seconds}s 检查合计={timing['sum_check_seconds']}s {parts}",flush=True)
    print(f"TIMING_JSON: {work/'timing.json'}",flush=True)
    return 1 if failed else 0


if __name__=="__main__":
    raise SystemExit(main())
