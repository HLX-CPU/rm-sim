# -*- coding: utf-8 -*-
"""验收脚本：读取型检查（报告类）复用共享冻结产物（模块51·调研§4）。exit 0 = 通过。

流程（verify/shared_products.run_reader 共通口径；键含几何内容/几何映射/网格参数
+gmsh 版本+网格代码版本/最终 INP 与 INCLUDE/引擎指纹/线程与数值设置；未命中时
真实求解一次并冻结）。

本读取器的结论（全部读取自冻结产物，不做任何求解）：
- report.md 附录 A（全精度 JSON 代码块）与 case 内 results.json 同源同文（精确相等）；
- 附录关键值（max|U|/max Mises/ΣRF/tip Uy）与冻结参考（manifest.reference，
  冻结时独立复算）1e-9 门；
- 正文人类可读数值按打印位数核对 |正文 − results.json| ≤ 0.5×10^(−位数)
  （与 verify_report_archive ③ 同口径的行：位移/合位移/Mises/三分量反力/min SICN）。
"""
from __future__ import annotations

import datetime
import json
import re
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "verify"))
sys.path.insert(0, str(ROOT / "app"))

import shared_products  # noqa: E402
from assembly_check_utils import evidence_dir  # noqa: E402

LOG_PATH = ROOT / "verify" / "logs" / "verify_reuse_report_check.log"
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


def read_fn(work: Path, mat: dict, manifest: dict) -> dict:
    case_dir = mat["case_dir"]
    rj = json.loads((case_dir / "results.json").read_text(encoding="utf-8"))
    report = (case_dir / "report.md").read_text(encoding="utf-8")
    ref = manifest["reference"]

    m = re.search(r"```json\s*\n(.*?)\n```", report, flags=re.DOTALL)
    if m is None:
        raise AssertionError("报告缺附录 A（全精度 JSON 代码块）")
    appendix = json.loads(m.group(1))
    if appendix != rj:
        raise AssertionError("附录 JSON 与 results.json 不同源同文")
    for key in ("max_u_mag_mm", "max_mises_mpa", "tip_uy_mm"):
        got, want = float(appendix["results"][key]), float(ref[key])
        if abs(got - want) > 1e-9 * max(abs(want), 1e-30):
            raise AssertionError(f"附录 {key} 与冻结参考超出 1e-9: {got!r} vs {want!r}")
    for i, (g, w) in enumerate(zip(appendix["results"]["rf_sum_n"], ref["rf_sum_n"])):
        if abs(float(g) - float(w)) > 1e-9 * max(abs(float(w)), 1e-30):
            raise AssertionError(f"附录 rf_sum_n[{i}] 与冻结参考超出 1e-9: {g!r} vs {w!r}")
    log("[报告] 附录 A 与 results.json 同源同文；关键值与冻结参考 1e-9 门一致")

    body_checks = [
        ("端部竖向位移 Uy [mm]", rj["results"]["tip_uy_mm"]),
        # 报告正文里管道符按 markdown 转义为两个反斜杠（与 verify_report_archive ③ 同款字节）
        (f"最大合位移 {chr(92) * 2}|U{chr(92) * 2}| [mm]", rj["results"]["max_u_mag_mm"]),
        ("最大 von Mises [MPa]", rj["results"]["max_mises_mpa"]),
        ("支反力 ΣFx [N]", rj["results"]["rf_sum_n"][0]),
        ("支反力 ΣFy [N]", rj["results"]["rf_sum_n"][1]),
        ("支反力 ΣFz [N]", rj["results"]["rf_sum_n"][2]),
        ("min SICN", rj["mesh"]["min_sicn"]),
    ]
    for label, ref_val in body_checks:
        mm = re.search(rf"\|\s*{re.escape(label)}\s*\|\s*(-?\d+\.\d+)\s*\|", report)
        if mm is None:
            raise AssertionError(f"正文缺「{label}」行")
        text_val = mm.group(1)
        places = len(text_val.split(".")[1])
        diff = abs(float(text_val) - float(ref_val))
        if diff > 0.5 * 10 ** (-places):
            raise AssertionError(f"正文「{label}」={text_val} 与 results.json {ref_val!r} "
                                 f"超打印位数容差（差 {diff:.2e}）")
    log(f"[报告] 正文 {len(body_checks)} 行按打印位数核对一致（≤0.5×10^(−位数)）")
    return {"max_mises_mpa": float(appendix["results"]["max_mises_mpa"]),
            "max_u_mag_mm": float(appendix["results"]["max_u_mag_mm"]),
            "rf_sum_n": [float(x) for x in appendix["results"]["rf_sum_n"]]}


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    work = evidence_dir("reuse_report_check")
    log(f"证据目录: {work}")
    try:
        source, numbers = shared_products.run_reader(work, "reuse_report_check", "report", read_fn)
    except Exception as exc:  # noqa: BLE001
        log(f"REUSE_REPORT_CHECK: FAIL（{type(exc).__name__}: {exc}）")
        log(traceback.format_exc()[-2000:])
        return 1
    log(f"[结论] 报告类读取型检查 source={source}，max Mises={numbers['max_mises_mpa']:.9g} MPa、"
        f"max|U|={numbers['max_u_mag_mm']:.9g} mm、ΣFy={numbers['rf_sum_n'][1]:.9g} N"
        "——与冻结参考 1e-9 门一致")
    log("REUSE_REPORT_CHECK: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
