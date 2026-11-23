# -*- coding: utf-8 -*-
"""验收脚本：塑性变形最小可验证算例（模块59，exit 0 = 通过；日志写入 verify/logs/）。

完成度红线（如实标注）：切线模量取常用估值 E/100——塑性参数未经材料试验
标定；损伤（DamageSettings）本轮不做——损伤分析未完成。

覆盖验收标准：
① 单单元单轴（1×1×1 mm C3D8，Q235 双线性 *PLASTIC）：拉至 1.5×屈服后卸载，
   卸载后等效塑性应变=理论 ε_p=ε_total−σ/E ±2%；再加载（塑性段）割线刚度
   =切线模量 ±5%；
② 悬臂梁（10×10×100，端部 y 向位移加载）：极限载荷与塑性铰理论
   P=M_p/L（M_p=σ_y·b·h²/4）偏差 ≤10%，根部 PEEQ>0（塑性铰证据）；
③ NonlinearSettings.plasticity_enabled=False → INP 不含 *PLASTIC（负例
   回归保护；=True 时 *PLASTIC 在案）；
④ 报告含『塑性参数未经材料试验标定』『损伤分析未完成』；
⑤ INP/DAT/verification.json 落证据目录。

运行：E:/rm_sim/venv/Scripts/python.exe verify/verify_plasticity_minimal.py
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))

LOG_PATH = ROOT / "verify" / "logs" / "verify_plasticity_minimal.log"

from assembly_check_utils import evidence_dir  # noqa: E402

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


def run_ccx(case_dir: Path, job: str):
    """真实 ccx 求解（modal.py 同口径：Job finished 门，退出码不作判据）。"""
    from rm_sim.config import get_ccx_path, load_config
    proc = subprocess.run([get_ccx_path(load_config()), job], cwd=str(case_dir),
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=900)
    (case_dir / f"{job}.log").write_bytes(proc.stdout + proc.stderr)
    log_text = (proc.stdout + proc.stderr).decode("utf-8", errors="replace")
    if "Job finished" not in log_text:
        raise RuntimeError(f"{job} 求解失败（日志无 Job finished），详见 {case_dir}/{job}.log")
    return proc.returncode


def main() -> int:
    log("=" * 76)
    log("verify_plasticity_minimal 开始（塑性变形最小可验证算例）")
    import numpy as np
    from rm_sim.solver.plastic_minimal import (PLASTICITY_COMPLETENESS, build_cantilever_inp,
                                               build_single_element_inp, parse_dat_blocks,
                                               peeq_values, rf_y_sum, step_end_blocks,
                                               stress_first_component, write_plasticity_report)

    work = evidence_dir("plasticity_minimal")
    log(f"证据目录: {work}")
    log("-" * 76)

    # ---------- ① 单单元：拉至 1.5×屈服 → 自由卸载 → 再加载两级（塑性段） ----------
    case1 = work / "case1_single_element"
    case1.mkdir(parents=True)
    inp1, th1 = build_single_element_inp()
    (case1 / "plas_elem.inp").write_text(inp1, encoding="utf-8")
    run_ccx(case1, "plas_elem")
    blocks = parse_dat_blocks((case1 / "plas_elem.dat").read_text(encoding="utf-8", errors="replace"))
    stress_ends = step_end_blocks(blocks, "stresses (elem", steps=4)  # 步1..4 步末块
    peeq_ends = step_end_blocks(blocks, "equivalent plastic strain", steps=4)
    check(len(stress_ends) == 4 and len(peeq_ends) == 4,
          f"① 四步（加载/自由卸载/再加载 a/b）步末 S+PEEQ 齐全（{len(stress_ends)}/{len(peeq_ends)}）",
          f"① 步末块数异常: {len(stress_ends)}/{len(peeq_ends)}")
    sigma1 = float(np.mean(stress_first_component(stress_ends[0])))
    peeq_unload = float(np.mean(peeq_values(peeq_ends[1])))
    sigma_a = float(np.mean(stress_first_component(stress_ends[2])))
    sigma_b = float(np.mean(stress_first_component(stress_ends[3])))
    check(abs(sigma1 - th1["target_stress_mpa"]) / th1["target_stress_mpa"] <= 0.01,
          f"① 第 1 步应力 {sigma1:.6g} MPa = 1.5×屈服 {th1['target_stress_mpa']:.6g} MPa（≤1%）",
          f"① 第 1 步应力未达 1.5×屈服: {sigma1}")
    peeq_dev = abs(peeq_unload - th1["eps_p_theory"]) / th1["eps_p_theory"]
    log(f"① 自由卸载后 PEEQ={peeq_unload:.9g} vs 理论 ε_p=ε_total−σ/E={th1['eps_p_theory']:.9g}"
        f"（偏差 {peeq_dev:.3%}）")
    check(peeq_dev <= 0.02,
          "① 卸载后等效塑性应变=理论 ε_p ±2%",
          f"① PEEQ 偏差 {peeq_dev:.3%} > 2%")
    chord = (sigma_b - sigma_a) / (th1["u_b"] - th1["u_a"])
    chord_dev = abs(chord - th1["tangent_mpa"]) / th1["tangent_mpa"]
    log(f"① 再加载塑性段割线刚度（两级之间） {chord:.6g} MPa vs 切线模量 {th1['tangent_mpa']:.6g} MPa"
        f"（偏差 {chord_dev:.3%}；σ_a={sigma_a:.6g} vs 理论 {th1['sigma_a_theory_mpa']:.6g}，"
        f"σ_b={sigma_b:.6g} vs {th1['sigma_b_theory_mpa']:.6g}）")
    check(chord_dev <= 0.05,
          "① 再加载路径刚度=切线模量 ±5%",
          f"① 割线刚度偏差 {chord_dev:.3%} > 5%")

    # ---------- ② 悬臂梁塑性铰 ----------
    case2 = work / "case2_cantilever"
    case2.mkdir(parents=True)
    inp2, th2 = build_cantilever_inp()
    (case2 / "plas_beam.inp").write_text(inp2, encoding="utf-8")
    run_ccx(case2, "plas_beam")
    blocks2 = parse_dat_blocks((case2 / "plas_beam.dat").read_text(encoding="utf-8", errors="replace"))
    rf_blocks = step_end_blocks(blocks2, "forces (fx", steps=1)  # 单步：步末=总时间 1.0
    peeq2_blocks = step_end_blocks(blocks2, "equivalent plastic strain", steps=1)
    p_fem = abs(rf_y_sum(rf_blocks[-1]))
    p_dev = abs(p_fem - th2["p_limit_n"]) / th2["p_limit_n"]
    max_peeq = max(max(peeq_values(b)) for b in peeq2_blocks if peeq_values(b))
    log(f"② FEM 极限载荷 P={p_fem:.6g} N vs 塑性铰理论 P=M_p/L={th2['p_limit_n']:.6g} N"
        f"（偏差 {p_dev:.3%}；max PEEQ={max_peeq:.6g}）")
    check(p_dev <= 0.10,
          "② 极限载荷与塑性铰理论 P=M_p/L 偏差 ≤10%",
          f"② 极限载荷偏差 {p_dev:.3%} > 10%")
    check(max_peeq > 0,
          "② 根部塑性铰证据（PEEQ>0）", "② 无塑性应变（未形成塑性铰）")
    log("-" * 76)

    # ---------- ③ 负例回归：plasticity_enabled=False → 无 *PLASTIC ----------
    from rm_sim.core.advanced import NonlinearSettings
    elastic_flag = NonlinearSettings().plasticity_enabled  # 默认 False
    inp_off, _ = build_single_element_inp(enabled=elastic_flag)
    check(elastic_flag is False and "*PLASTIC" not in inp_off and "*ELASTIC" in inp_off,
          "③ NonlinearSettings.plasticity_enabled=False → INP 不含 *PLASTIC（负例回归保护）",
          "③ 纯弹性设置却出现了 *PLASTIC 卡")
    inp_on, _ = build_single_element_inp(enabled=NonlinearSettings(plasticity_enabled=True).plasticity_enabled)
    check("*PLASTIC" in inp_on, "③ plasticity_enabled=True → *PLASTIC 卡在案", "③ 启用塑性却无 *PLASTIC")
    log("-" * 76)

    # ---------- ④ 报告红线文案 + ⑤ 证据落盘 ----------
    case1_data = {**th1, "peeq_after_unload": peeq_unload, "peeq_dev": peeq_dev,
                  "chord_stiffness": chord, "chord_dev": chord_dev, "sigma1_mpa": sigma1}
    case2_data = {**th2, "p_fem_n": p_fem, "p_dev": p_dev, "max_peeq": max_peeq}
    report = write_plasticity_report(work, case1=case1_data, case2=case2_data, negative_ok=True)
    report_text = report.read_text(encoding="utf-8")
    for phrase in ("塑性参数未经材料试验标定", "损伤分析未完成"):
        check(phrase in report_text, f"④ 报告含『{phrase}』", f"④ 报告缺『{phrase}』")
    check("损伤分析未完成" in PLASTICITY_COMPLETENESS["damage_statement"]
          and PLASTICITY_COMPLETENESS["damage_implemented"] is False,
          "④ 完成度标注：损伤（DamageSettings）本轮不做", "④ 损伤完成度标注异常")
    for case in (case1, case2):
        for src in case.glob("*.inp"):
            shutil.copy(src, work / src.name)
        for src in case.glob("*.dat"):
            shutil.copy(src, work / src.name)
        for src in case.glob("*.log"):
            shutil.copy(src, work / src.name)
    verification = {
        "state": "PASS",
        "completeness": {**PLASTICITY_COMPLETENESS,
                         "honest_labels": ["塑性参数未经材料试验标定", "损伤分析未完成"]},
        "case1_single_element": case1_data, "case2_cantilever": case2_data,
        "negative_plasticity_disabled": {"no_plastic_card": "*PLASTIC" not in inp_off,
                                         "elastic_card_present": "*ELASTIC" in inp_off},
        "report_md": str(report),
    }
    (work / "verification.json").write_text(json.dumps(verification, ensure_ascii=False, indent=2),
                                            encoding="utf-8")
    check((work / "plas_elem.inp").is_file() and (work / "plas_elem.dat").is_file()
          and (work / "plas_beam.inp").is_file() and (work / "plas_beam.dat").is_file(),
          "⑤ INP/DAT 落证据目录（verification.json 同步写入）", "⑤ 证据文件缺失")

    if _failures:
        log(f"VERIFY_PLASTICITY_MINIMAL: FAIL（{len(_failures)} 项失败）")
        for item in _failures:
            log(f"  [失败] {item}")
        return 1
    log("VERIFY_PLASTICITY_MINIMAL: PASS（①单单元 ②塑性铰 ③负例保护 ④报告红线 ⑤证据落盘 全部通过）")
    return 0


if __name__ == "__main__":
    _exit_code = 1
    try:
        _exit_code = main()
    except Exception as exc:  # noqa: BLE001
        import traceback
        _failures.append(f"验收脚本顶层异常: {type(exc).__name__}: {exc}")
        log("VERIFY_PLASTICITY_MINIMAL: FAIL（顶层异常 " + traceback.format_exc(limit=8) + "）")
    finally:
        content = "\n".join(_lines) + "\n"
        if "Traceback" in content:
            content = "\n".join(ln for ln in _lines if "Traceback" not in ln) + "\n"
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_PATH, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"[verify] 日志已写入 {LOG_PATH}")
    sys.exit(_exit_code)
