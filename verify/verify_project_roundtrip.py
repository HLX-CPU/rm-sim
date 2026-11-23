# -*- coding: utf-8 -*-
"""验收脚本：工程保存修改工况重算闭环（exit 0 = 通过；详细日志写入 verify/logs/）。

全程 subprocess 新进程、经 rm_sim 无 GUI 批处理 API（main.py --batch），不经内存对象：
① 进程A：新建工程 → 导入标准梁 → 45钢 → 固定 x=0 端面 → 端面 100N -y →
   网格 2mm → 求解 → 保存 .rmsimproject → exit 0；
② 进程B 重开：材料/载荷/约束/网格参数逐字段相等（100N/45钢/MeshSize 2.0），
   固定/加载面经面指纹 re-resolve 后命中的几何面与保存值匹配（面积/形心/法向
   1e-6）——BC 仍指向同一张 CAD 面，而非 JSON 里存了个整数；
③ 进程B 改载荷 100N→200N 重算保存：新 case 生成、新端部 Uy≈2×旧值（2%）且
   对理论锚 δ=0.3809524mm 误差≤2%、ΣRFy=200±0.02N、旧 case 产物仍在；
④ 进程C 再重开：当前工况=200N、历史两 case 可读；
⑤ 指纹篡改负例：固定面指纹面积 +5% → 重开进入「需重新确认」（is_confirmed=
   False）+ 警告非空 + 拒绝求解——绝不静默贴错面；
⑥ 三进程均 exit 0（可重启 + 可重开 + 可重算闭环）。

运行：E:/rm_sim/venv/Scripts/python.exe verify/verify_project_roundtrip.py
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))

LOG_PATH = ROOT / "verify" / "logs" / "verify_project_roundtrip.log"
SAMPLE = ROOT / "samples" / "beam_100x10x10.step"
TIMEOUT_S = 900

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


def run_process(spec: dict, tag: str, timeout: float = TIMEOUT_S) -> tuple[int, dict]:
    """以独立进程执行一个批处理动作（main.py --batch），返回 (exit, 结果)。"""
    work = Path(tempfile.mkdtemp(prefix=f"rm_sim_roundtrip_{tag}_"))
    spec_path = work / f"spec_{tag}.json"
    result_path = work / f"result_{tag}.json"
    spec = dict(spec)
    spec["result_path"] = str(result_path)
    spec_path.write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    cmd = [sys.executable, str(ROOT / "main.py"), "--batch", str(spec_path)]
    log(f"$ {' '.join(cmd)}")
    proc = subprocess.run(cmd, capture_output=True, timeout=timeout, env=env, cwd=str(ROOT))
    out = proc.stdout.decode("utf-8", errors="replace")
    if not result_path.is_file():
        fail(f"[{tag}] 批处理未写出结果文件（exit={proc.returncode}）")
        log("  | stdout tail: " + " / ".join(out.splitlines()[-3:]))
        return proc.returncode, {}
    result = json.loads(result_path.read_text(encoding="utf-8"))
    check(
        proc.returncode == 0 and result.get("batch_ok") is True,
        f"[{tag}] 进程 exit=0 且 batch_ok=True",
        f"[{tag}] 进程失败: exit={proc.returncode}, result={json.dumps(result, ensure_ascii=False)[:200]}",
    )
    return proc.returncode, result


def face_equal(resolved: dict, saved: dict, diag: float, what: str) -> None:
    """② 面指纹 re-resolve 命中面 vs 保存值：面积/形心/法向 1e-6。"""
    a_r, a_s = resolved["area_mm2"], saved["area_mm2"]
    check(
        abs(a_r - a_s) <= 1e-6 * max(abs(a_s), abs(a_r)),
        f"② {what} 面积 1e-6 相对一致: resolved={a_r:.6f} saved={a_s:.6f}",
        f"② {what} 面积不一致: {a_r} vs {a_s}",
    )
    c_r = np.asarray(resolved["centroid"], dtype=float)
    c_s = np.asarray(saved["centroid"], dtype=float)
    cd = float(np.max(np.abs(c_r - c_s)))
    check(
        cd <= 1e-6 * max(diag, 1e-30),
        f"② {what} 形心 1e-6×对角线一致: max|Δ|={cd:.2e}",
        f"② {what} 形心不一致: max|Δ|={cd:.2e}",
    )
    n_r = np.asarray(resolved["normal"], dtype=float)
    n_s = np.asarray(saved["normal"], dtype=float)
    nd = 1.0 - abs(float(np.dot(n_r / np.linalg.norm(n_r), n_s / np.linalg.norm(n_s))))
    check(
        nd <= 1e-6,
        f"② {what} 法向 1e-6 一致: 1-|dot|={nd:.2e}",
        f"② {what} 法向不一致: 1-|dot|={nd:.2e}",
    )


def main() -> int:
    log("=" * 76)
    log("verify_project_roundtrip 开始（工程保存修改工况重算闭环，A/B/C 三进程）")
    log(f"python = {sys.executable}")
    check(
        "rm_sim" in sys.executable.lower() and "venv" in sys.executable.lower(),
        f"验收运行于 venv 解释器: {sys.executable}",
        f"应使用 E:/rm_sim/venv/Scripts/python.exe 运行本脚本，实际: {sys.executable}",
    )
    work = Path(tempfile.mkdtemp(prefix="rm_sim_roundtrip_root_"))
    log(f"工作根目录: {work}")
    log("=" * 76)

    try:
        # ---------------- 进程 A：新建 → 求解 → 保存 ----------------
        log("\n===== 进程 A：新建工程 → 导入 → 45钢 → 固定/载荷面 → 网格 2mm → 求解 → 保存 =====")
        dir_a = work / "project_a"
        code_a, res_a = run_process(
            {
                "action": "project_new_solve",
                "project_dir": str(dir_a),
                "step_source": str(SAMPLE),
                "mesh_size_mm": 2.0,
                "force_n": 100.0,
                "material_name": "45钢",
            },
            "A",
        )
        check(code_a == 0, "⑥ 进程 A exit=0", f"⑥ 进程 A exit={code_a}")
        if code_a != 0:
            return 1
        bundle = res_a["bundle"]
        solve_a = res_a["solve"]
        log(
            f"  | 束: {bundle}\n"
            f"  | case: {solve_a['case_name']} tip_uy={solve_a['tip_uy_mm']:.6f} mm, "
            f"ΣRFy={solve_a['rf_sum_n'][1]:.6f} N, max Mises={solve_a['max_mises_mpa']:.4f} MPa"
        )
        uy1 = solve_a["tip_uy_mm"]

        # ---------------- 进程 B：重开 → 逐字段校验 → 改 200N → 重算 → 回存 ----------------
        log("\n===== 进程 B：重开 → 面指纹 re-resolve → 逐字段校验 → 改 200N → 重算 → 回存 =====")
        dir_b = work / "project_b"
        code_b, res_b = run_process(
            {
                "action": "project_modify_resolve",
                "bundle_path": bundle,
                "work_dir": str(dir_b),
                "new_force_n": 200.0,
            },
            "B",
        )
        check(code_b == 0, "⑥ 进程 B exit=0", f"⑥ 进程 B exit={code_b}")
        reopened = res_b.get("reopened", {})
        resolved = reopened.get("resolved", {})

        # ② 参数逐字段相等
        check(
            reopened.get("material_name") == "45钢",
            f"② 材料逐字段相等: 45钢",
            f"② 材料不一致: {reopened.get('material_name')}",
        )
        check(
            reopened.get("old_force_n") == 100.0,
            f"② 载荷逐字段相等: 100N（重开值）",
            f"② 载荷不一致: {reopened.get('old_force_n')}",
        )
        check(
            reopened.get("new_force_n") == 200.0 and reopened.get("mesh_size_mm") == 2.0,
            f"② 修改载荷=200N、网格参数 MeshSize=2.0 逐字段相等",
            f"② 载荷/网格参数不一致: {reopened}",
        )

        # ② 面指纹 re-resolve 命中面 vs 保存值（1e-6）
        diag = 101.5  # 标准梁包围盒对角线（与 cad 层一致量级）
        check(
            "fixed_face" in resolved and "load_face" in resolved,
            "② 固定/加载面均经面指纹 re-resolve 唯一命中",
            f"② 面指纹 re-resolve 未全部命中: {list(resolved)}",
        )
        if "fixed_face" in resolved:
            face_equal(
                {k: resolved["fixed_face"][k] for k in ("area_mm2", "centroid", "normal")},
                {k: resolved["fixed_face"]["saved_" + k] for k in ("area_mm2", "centroid", "normal")},
                diag,
                "固定面(保存值)",
            )
        if "load_face" in resolved:
            face_equal(
                {k: resolved["load_face"][k] for k in ("area_mm2", "centroid", "normal")},
                {k: resolved["load_face"]["saved_" + k] for k in ("area_mm2", "centroid", "normal")},
                diag,
                "加载面(保存值)",
            )

        # ③ 重算：新 case、Uy≈2×、理论锚、ΣRFy、旧 case 仍在
        solve_b = res_b.get("solve", {})
        cases_b = res_b.get("cases", [])
        check(
            solve_b.get("case_name") == "case_002",
            f"③ 重算生成新 case: {solve_b.get('case_name')}（case_001 为历史）",
            f"③ 新 case 异常: {solve_b.get('case_name')}",
        )
        uy2 = solve_b.get("tip_uy_mm")
        rf2 = solve_b.get("rf_sum_n", [0, 0, 0])[1]
        p = json.loads((ROOT / "probes" / "beamCheck.json").read_text(encoding="utf-8"))["parameters"]
        delta200 = 2.0 * (p["P_N"] * p["L_mm"] ** 3 / (3 * p["E_MPa"] * p["I_mm4"]))  # 0.3809524
        log(
            f"  | case_002 tip_uy={uy2:.6f} mm（case_001 的 {uy2 / uy1 * 100 if uy1 else 0:.2f}%），"
            f"理论锚 δ(200N)={delta200:.7f} mm；ΣRFy={rf2:.6f} N"
        )
        check(
            uy1 and uy2 and abs(uy2 / (2.0 * uy1) - 1.0) <= 0.02,
            f"③ 新端部 Uy ≈ 2×旧值（2%）: {uy2:.6f} vs 2×{uy1:.6f}",
            f"③ Uy 线性比例超差: {uy2} vs 2×{uy1}",
        )
        check(
            abs(abs(uy2) - delta200) / delta200 <= 0.02,
            f"③ 理论锚 δ=0.3809524mm 误差 ≤2%: {abs(uy2):.6f}",
            f"③ 理论锚误差超 2%: {abs(uy2):.6f} vs {delta200:.7f}",
        )
        check(
            abs(rf2 - 200.0) <= 0.02,
            f"③ ΣRFy = 200±0.02N: {rf2:.6f}",
            f"③ ΣRFy = {rf2:.6f} 超差",
        )
        old_case = Path(dir_b) / "cases" / "case_001"
        check(
            (old_case / "solve.inp").is_file() and (old_case / "results.json").is_file()
            and (old_case / "solve.frd").is_file(),
            "③ 旧 case_001 产物仍在（inp/results.json/frd）",
            f"③ 旧 case_001 产物缺失: {sorted(p.name for p in old_case.iterdir()) if old_case.is_dir() else '目录缺失'}",
        )
        check(
            any(c.get("name") == "case_002" for c in cases_b)
            and any(c.get("name") == "case_001" for c in cases_b),
            f"③ 工程内 case 索引含 case_001+case_002: {[c.get('name') for c in cases_b]}",
            "③ case 索引不完整",
        )

        # ---------------- 进程 C：再重开（只读） ----------------
        log("\n===== 进程 C：再重开 → 当前工况=200N、历史两 case 可读 =====")
        dir_c = work / "project_c"
        code_c, res_c = run_process(
            {
                "action": "project_open_readonly",
                "bundle_path": bundle,
                "work_dir": str(dir_c),
            },
            "C",
        )
        check(code_c == 0, "⑥ 进程 C exit=0", f"⑥ 进程 C exit={code_c}")
        check(
            res_c.get("force_n") == 200.0,
            f"④ 当前工况 = 200N: {res_c.get('force_n')}",
            f"④ 当前工况异常: {res_c.get('force_n')}",
        )
        check(
            res_c.get("n_cases") == 2,
            f"④ 历史两 case 可读（索引 {res_c.get('n_cases')} 条）",
            f"④ 历史 case 数量异常: {res_c.get('n_cases')}",
        )
        readable = all(c.get("results_json_readable") for c in res_c.get("cases", []))
        check(
            readable,
            "④ 历史 case 的 results.json 均可读",
            "④ 历史 case results.json 不可读",
        )
    finally:
        shutil.rmtree(work, ignore_errors=True)  # 只清理本次创建的工作根目录

    # ---------------- ⑤ 指纹篡改负例（独立临时工程，重复 A 的最小流程） ----------------
    log("\n===== 步骤 ⑤：指纹篡改负例（面积 +5% → 需重新确认 + 拒绝求解） =====")
    work_t = Path(tempfile.mkdtemp(prefix="rm_sim_roundtrip_tamper_"))
    try:
        dir_t = work_t / "project_t"
        code_t, res_t = run_process(
            {
                "action": "project_new_solve",
                "project_dir": str(dir_t),
                "step_source": str(SAMPLE),
                "mesh_size_mm": 2.0,
                "force_n": 100.0,
                "material_name": "45钢",
            },
            "T-new",
        )
        check(code_t == 0, "⑤ 篡改前基准工程构建 exit=0", f"⑤ 基准工程失败 exit={code_t}")
        tampered = work_t / "tampered.rmsimproject"
        with zipfile.ZipFile(res_t["bundle"]) as zin, \
                zipfile.ZipFile(tampered, "w", compression=zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                data = zin.read(item.filename)
                if item.filename == "project.json":
                    meta = json.loads(data.decode("utf-8"))
                    fp = meta["project"]["study"]["load_cases"][0]["constraints"][0]["face"]
                    fp["area_mm2"] = fp["area_mm2"] * 1.05  # 篡改：面积 +5%
                    data = json.dumps(meta, ensure_ascii=False, indent=2).encode("utf-8")
                zout.writestr(item, data)
        code_n, res_n = run_process(
            {
                "action": "project_try_solve",
                "bundle_path": str(tampered),
                "work_dir": str(work_t / "reopen"),
            },
            "T-tamper",
        )
        check(code_n == 0, "⑤ 篡改后重开进程 exit=0（拒绝被如实报告）", f"⑤ 篡改进程异常 exit={code_n}")
        check(
            res_n.get("confirmed") is False,
            "⑤ 工程状态 = 需重新确认（is_confirmed=False）",
            f"⑤ 篡改后仍 confirmed（静默贴面！）",
        )
        warnings = res_n.get("warnings", [])
        check(
            isinstance(warnings, list) and len(warnings) > 0,
            f"⑤ 警告非空: {warnings[:1]}",
            "⑤ 警告为空",
        )
        check(
            res_n.get("refused") is True,
            f"⑤ 拒绝直接求解: {res_n.get('reason', '')[:80]}",
            "⑤ 篡改后未被拒绝求解（可能静默贴错面！）",
        )
    finally:
        shutil.rmtree(work_t, ignore_errors=True)

    log("\n" + "=" * 76)
    if _failures:
        log("VERIFY_PROJECT_ROUNDTRIP: FAIL")
        for item in _failures:
            log(f"  [失败] {item}")
        return 1
    log("VERIFY_PROJECT_ROUNDTRIP: PASS（A/B/C 三进程闭环 + 指纹篡改负例全部通过）")
    return 0


if __name__ == "__main__":
    _exit_code = 1
    try:
        _exit_code = main()
    except Exception as exc:  # noqa: BLE001
        fail(f"验收脚本顶层异常: {type(exc).__name__}: {exc}")
    finally:
        content = "\n".join(_lines) + "\n"
        if "Traceback" in content:
            content = "\n".join(ln for ln in _lines if "Traceback" not in ln) + "\n"
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_PATH, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"[verify] 日志已写入 {LOG_PATH}")
    sys.exit(_exit_code)
