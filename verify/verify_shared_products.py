# -*- coding: utf-8 -*-
"""验收脚本：「共享冻结产物与失败项重跑」（模块51·调研§4）。exit 0 = 通过；日志 → verify/logs/。

功能对象：verify/shared_products.py（缓存键/冻结/完整性/复用 + 读取型检查共通流程）、
三个读取型检查（reuse_results_gui/reuse_report_check/reuse_archive_roundtrip，显式点名，
不入 DEFAULT——原完整验收入口无参语义不变）、run_regression.py --only-failed 与
summary.json/timing.json 的来源单列字段。

覆盖验收标准：
① 一次真实求解 → ≥3 类读取型检查（GUI/报告/序列化）复用同一冻结产物且结论一致：
   隔离共享根首跑三读取器 = 恰 1 个「本轮新求解」（真实求解一次并冻结）+2 个
   「复用历史求解并重新检查」；重跑一轮 = 3 个全部复用；三读取器 READER_RESULT
   数值两两一致且等于冻结参考（1e-9）。
② 产物不完整或哈希不匹配拒绝复用（负例）：缺 report.md、篡改 .dat 字节、束截断
   三种损坏形态 verify_product_integrity 全拒（原因可查），try_reuse 返回 None 不复用。
③ 载荷变更后旧产物不可整份复用（负例）：force_n 1000→2000 → 真实重新生成 deck 后
   键改变（INP 内容进键）；新键 try_reuse = None；同规格两次算键跨不同临时目录相等
   （deck 生成确定性，无路径泄漏）。
④ --only failed 只重跑失败项且报告来源标注正确：基线显式点名 3 读取器+1 个不存在
   检查 → 仅失败项被重跑（重跑 summary 恰 1 行）；3 个读取器在 timing.json
   not_executed 中标注「未执行/待验证」；无失败项时 --only-failed 重跑 0 项 exit 0。
⑤ 报告含来源单列字段：summary.json 逐行 source + timing.json sources 计数与
   not_executed 清单，三种状态（本轮新求解/复用历史求解并重新检查/未执行/待验证）
   在本轮报告中实际出现。

边界：全程使用隔离共享根（RM_SIM_SHARED_PRODUCTS_ROOT 指向验收临时目录；共享数据
只读，负例写入的是验收自己的隔离副本）——与真实根 E:/rm_sim/verification/
shared_products 仅路径不同，机制同一代码路径；不改任何既有门/容差；三个读取器
不入 DEFAULT/SLOW（无参回归语义逐行不变）。
"""
from __future__ import annotations

import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "verify"))
sys.path.insert(0, str(ROOT / "app"))
os.environ.setdefault("OMP_NUM_THREADS", "2")

LOG_PATH = ROOT / "verify" / "logs" / "verify_shared_products.log"
READERS = [("reuse_results_gui", "gui"),
           ("reuse_report_check", "report"),
           ("reuse_archive_roundtrip", "serialization")]
SOURCE_REUSED = "复用历史求解并重新检查"
SOURCE_NEW = "本轮新求解"
SOURCE_NOT_RUN = "未执行/待验证"
SOURCE_PLAIN = "本轮执行（未参与共享产物）"

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


def run_cmd(cmd: list[str], timeout: int = 600) -> tuple[int, str, str]:
    env = dict(os.environ)
    env.update({"PYTHONUTF8": "1", "OMP_NUM_THREADS": "2",
                "TEMP": "E:/rm_sim/temp", "TMP": "E:/rm_sim/temp",
                "QT_QPA_PLATFORM": "offscreen"})
    log(f"[run] {' '.join(cmd)}")
    started = time.monotonic()
    proc = subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True, timeout=timeout)
    seconds = time.monotonic() - started
    stdout = proc.stdout.decode("utf-8", errors="replace").replace("\r\n", "\n")
    stderr = proc.stderr.decode("utf-8", errors="replace")
    log(f"      exit={proc.returncode} seconds={seconds:.1f}s")
    for line in stdout.splitlines():
        log("      | " + line)
    if stderr.strip():
        log("      [stderr tail] " + stderr[-400:])
    return proc.returncode, stdout, stderr


def run_reader_check(name: str, store_root: Path, work: Path) -> tuple[int, str, str, str]:
    """在隔离共享根下跑一个读取型检查；返回 (exit, stdout, source, reader_line)。"""
    code, stdout, stderr = run_cmd([sys.executable, "-X", "utf8", "-B",
                                    str(ROOT / "verify" / f"verify_{name}.py")],
                                   timeout=600)
    marks = re.findall(r"\[product\] source=(\S+)", stdout)
    source = marks[-1] if marks else ""
    reader_line = next((ln for ln in stdout.splitlines() if ln.startswith("READER_RESULT:")), "")
    return code, stdout, source, reader_line


def parse_reader_line(line: str) -> dict:
    m = re.match(r"READER_RESULT: kind=(\S+) max_mises_mpa=(\S+) max_u_mag_mm=(\S+) rf_sum_n_y=(\S+)",
                 line)
    if m is None:
        raise AssertionError(f"READER_RESULT 行不可解析: {line!r}")
    return {"kind": m.group(1), "max_mises_mpa": float(m.group(2)),
            "max_u_mag_mm": float(m.group(3)), "rf_sum_n_y": float(m.group(4))}


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    failures: list[str] = []
    log("「共享冻结产物与失败项重跑」验收开始（模块51·调研§4）")

    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    work = Path("E:/rm_sim/verification") / f"shared_products_{stamp}"
    work.mkdir(parents=True, exist_ok=False)
    store = work / "shared_store"
    store.mkdir(parents=True, exist_ok=False)
    log(f"证据目录：{work}（隔离共享根：{store}）")
    os.environ["RM_SIM_SHARED_PRODUCTS_ROOT"] = str(store)  # 本进程内的直接 API 调用用
    import shared_products as sp

    # ---- ① 一次真实求解 → 三类读取型检查复用同一产物且结论一致 ----
    round_sources: list[dict] = []
    round_numbers: list[dict] = []
    for rnd in (1, 2):
        log(f"—— ① 第 {rnd} 轮：三个读取型检查顺序运行（隔离共享根）——")
        sources_r, numbers_r = {}, {}
        for name, kind in READERS:
            code, stdout, source, reader_line = run_reader_check(name, store, work)
            if code != 0:
                failures.append(f"① round{rnd} {name} exit={code}")
                continue
            if not source or not reader_line:
                failures.append(f"① round{rnd} {name} 缺 [product] source 或 READER_RESULT 标记")
                continue
            sources_r[name] = source
            try:
                numbers_r[name] = parse_reader_line(reader_line)
            except AssertionError as exc:
                failures.append(f"① round{rnd} {name}: {exc}")
        round_sources.append(sources_r)
        round_numbers.append(numbers_r)
    if not failures:
        r1 = round_sources[0]
        fresh = [n for n, s in r1.items() if s == SOURCE_NEW]
        reused1 = [n for n, s in r1.items() if s == SOURCE_REUSED]
        if len(fresh) != 1 or len(reused1) != 2:
            failures.append(f"① 首轮来源标注不符预期（恰1新+2复用）: {r1}")
        else:
            log(f"[①] PASS 首轮：{fresh[0]} 「{SOURCE_NEW}」（真实求解一次并冻结）；"
                f"{reused1} 「{SOURCE_REUSED}」——一次求解供三类读取型检查复用")
        r2 = round_sources[1]
        if not all(s == SOURCE_REUSED for s in r2.values()) or len(r2) != 3:
            failures.append(f"① 第二轮应三个读取器全部「复用历史求解并重新检查」: {r2}")
        else:
            log(f"[①] PASS 第二轮：三个读取器全部「{SOURCE_REUSED}」（同一冻结产物）")
        nums = round_numbers[1]
        if len(nums) == 3:
            vals = {(round(float(d["max_mises_mpa"]), 12), round(float(d["max_u_mag_mm"]), 15),
                     round(float(d["rf_sum_n_y"]), 12)) for d in nums.values()}
            if len(vals) != 1:
                failures.append(f"① 三类读取型检查结论不一致: {nums}")
            else:
                key_dir = next(p for p in store.iterdir()
                               if p.is_dir() and not p.name.startswith("_"))
                manifest = json.loads((key_dir / "manifest.json").read_text(encoding="utf-8"))
                ref = manifest["reference"]
                v = next(iter(vals))
                gates = (abs(v[0] - ref["max_mises_mpa"]) <= 1e-9 * abs(ref["max_mises_mpa"]),
                         abs(v[1] - ref["max_u_mag_mm"]) <= 1e-9 * abs(ref["max_u_mag_mm"]),
                         abs(v[2] - ref["rf_sum_n"][1]) <= 1e-9 * abs(ref["rf_sum_n"][1]))
                if not all(gates):
                    failures.append(f"① 读取器结论与冻结参考超 1e-9: {v} vs {ref}")
                else:
                    log(f"[①] PASS 三类结论两两一致且与冻结参考一致（max Mises={v[0]:.9g} MPa、"
                        f"max|U|={v[1]:.9g} mm、ΣFy={v[2]:.9g} N，|差|≤1e-9 相对）")

    # ---- ② 产物不完整或哈希不匹配拒绝复用（负例） ----
    key_dir = next((p for p in store.iterdir() if p.is_dir() and not p.name.startswith("_")), None)
    if key_dir is None:
        failures.append("② 隔离共享根没有冻结产物可做负例")
    else:
        manifest = json.loads((key_dir / "manifest.json").read_text(encoding="utf-8"))
        bundle = key_dir / manifest["bundle_file"]
        tampered = work / "tamper"
        tampered.mkdir(exist_ok=True)
        with zipfile.ZipFile(bundle) as z:
            blobs = {n: z.read(n) for n in z.namelist() if not n.endswith("/")}
        # (a) 缺 report.md：成员完整性层（validate_bundle）必须拒且指名缺失
        missing = tampered / "missing_report.rmsimproject"
        with zipfile.ZipFile(missing, "w", zipfile.ZIP_DEFLATED) as z:
            for n, b in blobs.items():
                if not n.endswith("/report.md"):
                    z.writestr(n, b)
        ok, reason, _ = sp.validate_bundle(missing)
        if ok or "report.md" not in reason:
            failures.append(f"②a 缺 report.md 未被成员完整性层拒绝（ok={ok} reason={reason!r}）")
        else:
            log(f"[②a] PASS 缺 report.md → 成员完整性层拒绝（{reason}）")
        ok2, reason2, _ = sp.verify_product_integrity(manifest, missing)
        if ok2:
            failures.append("②a verify_product_integrity 未拒绝缺 report.md 的束")
        else:
            log(f"[②a] PASS verify_product_integrity 亦拒绝（{reason2}）")
        # (b) 篡改 .dat 一个字节：把 manifest 的束级哈希改成与篡改束一致，
        #     强制走到成员哈希层——成员哈希不匹配必须被拒
        dat_name = next(n for n in blobs if n.endswith(".dat"))
        blobs_t = dict(blobs)
        blobs_t[dat_name] = bytes([blobs[dat_name][0] ^ 0xFF]) + blobs[dat_name][1:]
        tampered_dat = tampered / "tampered_dat.rmsimproject"
        with zipfile.ZipFile(tampered_dat, "w", zipfile.ZIP_DEFLATED) as z:
            for n, b in blobs_t.items():
                z.writestr(n, b)
        manifest_t = {**manifest, "bundle_sha256": sp.sha256_file(tampered_dat)}
        ok, reason, _ = sp.verify_product_integrity(manifest_t, tampered_dat)
        if ok or "成员哈希不匹配" not in reason:
            failures.append(f"②b 篡改 .dat 未被成员哈希层拒绝（ok={ok} reason={reason!r}）")
        else:
            log(f"[②b] PASS 篡改 {dat_name} 字节 → 成员哈希层拒绝（{reason}）")
        # (c) 束截断
        truncated = tampered / "truncated.rmsimproject"
        truncated.write_bytes(bundle.read_bytes()[: len(bundle.read_bytes()) // 2])
        ok, reason, _ = sp.verify_product_integrity(manifest, truncated)
        if ok:
            failures.append("②c 束截断未被拒")
        else:
            log(f"[②c] PASS 束截断 → 拒绝（{reason}）")
        # (d) try_reuse 在损坏根下返回 None（不复用、不抛）
        broken_store = work / "broken_store" / manifest["key"]
        broken_store.mkdir(parents=True, exist_ok=False)
        shutil.copy2(tampered_dat, broken_store / manifest["bundle_file"])
        shutil.copy2(key_dir / "manifest.json", broken_store / "manifest.json")
        sp.STORE_ROOT = work / "broken_store"
        reused = sp.try_reuse(manifest["key"])
        sp.STORE_ROOT = store
        if reused is not None:
            failures.append("②d 损坏根下 try_reuse 未返回 None")
        else:
            log("[②d] PASS 损坏共享根下 try_reuse → None（拒绝复用，不异常不静默通过）")

    # ---- ③ 载荷变更 → 旧产物不可整份复用（负例）+ 键确定性 ----
    k1 = sp.compute_key(work / "keycheck_a")
    k1b = sp.compute_key(work / "keycheck_a2")
    if k1["key"] != k1b["key"]:
        failures.append("③ 同规格两次算键（不同临时目录）不相等——deck 生成不确定或路径泄漏")
    else:
        log(f"[③] PASS 同规格跨不同临时目录两次算键相等（key={k1['key'][:16]}…，deck 生成确定性）")
    k2 = sp.compute_key(work / "keycheck_b", force_n=2000.0)
    if k2["key"] == k1["key"]:
        failures.append("③ 载荷 1000→2000N 后键未变")
    else:
        inp_diff = {n for n in k1["components"]["inp_contents"]
                    if k1["components"]["inp_contents"][n] != k2["components"]["inp_contents"].get(n)}
        log(f"[③] PASS 载荷变更 → 键改变（{k1['key'][:12]}… → {k2['key'][:12]}…；"
            f"变更分量={sorted(inp_diff)}——最终 INP 内容进键）")
    sp.STORE_ROOT = store
    if sp.try_reuse(k2["key"]) is not None:
        failures.append("③ 变更载荷后的键在共享根命中了旧产物（不应命中）")
    else:
        log("[③] PASS 变更载荷后的键 try_reuse → None（旧产物不可整份复用）")

    # ---- ④ --only failed 只重跑失败项且报告来源标注正确 ----
    runner = ROOT / "verify" / "run_regression.py"
    env = dict(os.environ)
    env.update({"PYTHONUTF8": "1", "OMP_NUM_THREADS": "2",
                "TEMP": "E:/rm_sim/temp", "TMP": "E:/rm_sim/temp",
                "QT_QPA_PLATFORM": "offscreen",
                "RM_SIM_SHARED_PRODUCTS_ROOT": str(store)})
    base_code, base_out, _ = run_cmd([sys.executable, "-X", "utf8", "-B", str(runner),
                                      "reuse_results_gui", "reuse_report_check",
                                      "reuse_archive_roundtrip", "no_such_check_xyz"], timeout=900)
    base_dirs = sorted(d for d in Path("E:/rm_sim/verification").glob("regression_*")
                       if re.fullmatch(r"regression_\d{8}_\d{6}_\d+", d.name))
    base_summary = json.loads((base_dirs[-1] / "summary.json").read_text(encoding="utf-8"))
    if base_code != 1:
        failures.append(f"④ 基线（3 读取器+1 不存在检查）应 exit 1: exit={base_code}")
    base_failures_before = len(failures)
    rows = {r["check"]: r for r in base_summary}
    if sorted(rows) != ["no_such_check_xyz", "reuse_archive_roundtrip", "reuse_report_check",
                        "reuse_results_gui"]:
        failures.append(f"④ 基线检查集不符: {sorted(rows)}")
    for name, kind in READERS:
        row = rows.get(name)
        if row is None:
            continue
        if row["exit_code"] != 0 or row.get("source") != SOURCE_REUSED:
            failures.append(f"④ 基线 {name} 应 PASS 且 source=复用历史求解并重新检查: {row}")
    bogus = rows.get("no_such_check_xyz")
    if bogus is not None and (bogus["exit_code"] == 0 or bogus.get("source") != SOURCE_PLAIN
                              or bogus.get("label") != "unknown"):
        failures.append(f"④ 基线不存在检查应 FAIL 且 source={SOURCE_PLAIN!r}/label=unknown: {bogus}")
    if len(failures) == base_failures_before:
        log("[④a] PASS 基线：3 读取器 source=复用历史求解并重新检查；不存在检查 "
            "source=本轮执行（未参与共享产物）/label=unknown；summary.json 逐行含 source 字段")
    only_code, only_out, _ = run_cmd([sys.executable, "-X", "utf8", "-B", str(runner),
                                      "--only-failed"], timeout=600)
    only_dirs = [d for d in Path("E:/rm_sim/verification").glob("regression_*")
                 if re.fullmatch(r"regression_\d{8}_\d{6}_\d+", d.name)]
    only_summary = json.loads((sorted(only_dirs)[-1] / "summary.json").read_text(encoding="utf-8"))
    only_timing = json.loads((sorted(only_dirs)[-1] / "timing.json").read_text(encoding="utf-8"))
    if only_code != 1:
        failures.append(f"④ 重跑（失败项确实仍失败）应 exit 1: exit={only_code}")
    if [r["check"] for r in only_summary] != ["no_such_check_xyz"]:
        failures.append(f"④ --only-failed 应只重跑失败项 1 个: {[r['check'] for r in only_summary]}")
    not_run = {e["check"]: e for e in only_timing.get("not_executed", [])}
    if sorted(not_run) != ["reuse_archive_roundtrip", "reuse_report_check", "reuse_results_gui"] \
            or any(e["source"] != SOURCE_NOT_RUN for e in not_run.values()):
        failures.append(f"④ not_executed 应为 3 读取器且标注「未执行/待验证」: {not_run}")
    if not any(f.startswith("④") for f in failures):
        log("[④b] PASS --only-failed 只重跑失败项（重跑 summary 恰 1 行 no_such_check_xyz）；"
            "3 个读取器在报告 not_executed 中标注「未执行/待验证」；失败项仍失败 → exit 1（如实）")
    # 无失败项路径
    ok_code, ok_out, _ = run_cmd([sys.executable, "-X", "utf8", "-B", str(runner),
                                  "advanced_framework"], timeout=300)
    nf_code, nf_out, _ = run_cmd([sys.executable, "-X", "utf8", "-B", str(runner),
                                  "--only-failed"], timeout=300)
    nf_dirs = sorted(d for d in Path("E:/rm_sim/verification").glob("regression_*")
                     if re.fullmatch(r"regression_\d{8}_\d{6}_\d+", d.name))
    nf_timing = json.loads((nf_dirs[-1] / "timing.json").read_text(encoding="utf-8"))
    if ok_code != 0 or nf_code != 0:
        failures.append(f"④ 无失败项路径应 exit 0（advanced_framework={ok_code}, only-failed={nf_code}）")
    if "REGRESSION: 0/0" not in nf_out or nf_timing.get("not_executed", []) == []:
        failures.append("④ 无失败项 --only-failed 应报告 0 项重跑并把未执行项标注「未执行/待验证」")
    elif not any(f.startswith("④") for f in failures):
        log("[④c] PASS 无失败项 --only-failed：重跑 0 项 exit 0，未执行项标注「未执行/待验证」")

    # ---- ⑤ 报告含来源单列字段（三种状态实际出现） ----
    all_timing = [json.loads((d / "timing.json").read_text(encoding="utf-8"))
                  for d in sorted(d for d in Path("E:/rm_sim/verification").glob("regression_*")
                                  if re.fullmatch(r"regression_\d{8}_\d{6}_\d+", d.name))[-4:]]
    seen_sources = set(round_sources[0].values()) | set(round_sources[1].values())
    for t in all_timing:
        seen_sources |= set(t.get("sources", {}))
        seen_sources |= {e["source"] for e in t.get("not_executed", [])}
        if not all("source" in r for r in t.get("rows", [])):
            failures.append("⑤ timing.json rows 存在缺 source 字段的行")
    base_rows = all(r.get("source") for r in base_summary)
    needed = {SOURCE_NEW, SOURCE_REUSED, SOURCE_NOT_RUN}
    if not needed <= seen_sources:
        failures.append(f"⑤ 报告未出现全部三种来源状态: 缺 {needed - seen_sources}")
    elif not base_rows:
        failures.append("⑤ 基线 summary.json 行缺 source 字段")
    else:
        log(f"[⑤] PASS 来源单列字段在位：summary.json 逐行 source + timing.json sources 计数；"
            f"本轮实际出现状态={sorted(seen_sources)}（读取器 stdout 标记 + 回归报告 JSON 单列）")

    log("\n==== 摘要 ====")
    if failures:
        log(f"SHARED_PRODUCTS: FAIL（{len(failures)} 项）")
        for item in failures:
            log(f"  - {item}")
        return 1
    log("① 一次真实求解 → GUI/报告/序列化三类读取型检查复用同一冻结产物，结论两两一致且与冻结参考 1e-9 一致")
    log("② 不完整/哈希不匹配/截断三种损坏形态全部拒绝复用，try_reuse 返回 None 不静默通过")
    log("③ 载荷变更 → 最终 INP 内容进键 → 键改变 → 旧产物不可整份复用；同规格跨目录算键确定")
    log("④ --only-failed 只重跑失败项；来源标注正确（复用/本轮执行/未执行-待验证）；无失败项时 0 重跑 exit 0")
    log("⑤ 报告含来源单列字段（summary.json source / timing.json sources + not_executed），三种状态实际出现")
    log("SHARED_PRODUCTS: PASS")
    return 0


if __name__ == "__main__":
    _exit = 1
    try:
        _exit = main()
    except Exception as exc:  # noqa: BLE001
        log(f"SHARED_PRODUCTS: FAIL（顶层异常 {type(exc).__name__}: {exc}）")
        raise
    raise SystemExit(_exit)
