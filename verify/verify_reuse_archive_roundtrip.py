# -*- coding: utf-8 -*-
"""验收脚本：读取型检查（序列化类）复用共享冻结产物（模块51·调研§4）。exit 0 = 通过。

流程（verify/shared_products.run_reader 共通口径）：
- 真实执行几何导入+网格+deck 组装（不求解）算完整缓存键（几何内容/几何映射/
  网格参数+gmsh 版本+网格代码版本/最终 INP 与 INCLUDE/引擎指纹/线程与数值设置）；
- 命中 → 「复用历史求解并重新检查」；未命中 → 真实求解一次并冻结（「本轮新求解」）；
- 共享数据只读：独立临时目录解包（materialize 内再次完整性校验）；
- 本读取器的结论：load_archived_case 原生序列化路径读回历史 case——其内部自带
  summary 与原始场（DAT/FRD/VTU）rtol=1e-5 一致性门与反应力溯源哈希门；
  读回值再与冻结参考 1e-9 门（run_reader 统一执行）。
"""
from __future__ import annotations

import datetime
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "verify"))
sys.path.insert(0, str(ROOT / "app"))

import shared_products  # noqa: E402  （含 OMP 环境契约 setdefault）
from assembly_check_utils import evidence_dir  # noqa: E402

LOG_PATH = ROOT / "verify" / "logs" / "verify_reuse_archive_roundtrip.log"
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
    from rm_sim.solver.history import load_archived_case

    loaded = load_archived_case(mat["project"], mat["case_name"])
    r = loaded["context"]["results"]
    case_dir = mat["case_dir"]
    for suffix in (".inp", ".dat", ".frd", ".log", ".vtu", "results.json", "report.md"):
        hits = list(case_dir.glob("*" + suffix)) if suffix.startswith(".") else [case_dir / suffix]
        if not any(p.is_file() and p.stat().st_size > 0 for p in hits):
            raise AssertionError(f"序列化读回：case 目录缺 {suffix}")
    log(f"[序列化] load_archived_case 读回 case={mat['case_name']} job={mat['job_name']}"
        f"：max|U|={r['max_u_mag_mm']!r} mm、max Mises={r['max_mises_mpa']!r} MPa、"
        f"ΣRF={r['rf_sum_n']} N（内部已对 DAT/FRD/VTU 原始场过 rtol=1e-5 一致性门）")
    return {"max_mises_mpa": float(r["max_mises_mpa"]),
            "max_u_mag_mm": float(r["max_u_mag_mm"]),
            "rf_sum_n": [float(x) for x in r["rf_sum_n"]]}


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    work = evidence_dir("reuse_archive_roundtrip")
    log(f"证据目录: {work}")
    try:
        source, numbers = shared_products.run_reader(work, "reuse_archive_roundtrip",
                                                     "serialization", read_fn)
    except Exception as exc:  # noqa: BLE001
        log(f"REUSE_ARCHIVE_ROUNDTRIP: FAIL（{type(exc).__name__}: {exc}）")
        log(traceback.format_exc()[-2000:])
        return 1
    log(f"[结论] 序列化类读取型检查 source={source}，max Mises={numbers['max_mises_mpa']:.9g} MPa、"
        f"max|U|={numbers['max_u_mag_mm']:.9g} mm、ΣFy={numbers['rf_sum_n'][1]:.9g} N"
        "——与冻结参考 1e-9 门一致")
    log("REUSE_ARCHIVE_ROUNDTRIP: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
