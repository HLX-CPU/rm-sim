# -*- coding: utf-8 -*-
"""验收脚本：读取型检查（GUI类）复用共享冻结产物（模块51·调研§4）。exit 0 = 通过。

流程（verify/shared_products.run_reader 共通口径；键含几何内容/几何映射/网格参数
+gmsh 版本+网格代码版本/最终 INP 与 INCLUDE/引擎指纹/线程与数值设置；未命中时
真实求解一次并冻结；共享数据只读，独立临时目录解包）。

本读取器的结论：offscreen 构造『装配与部件结果』仪表对话框
（AssemblyResultDialog，与 main_window._on_show_assembly_results 同一口径）——
- load_archived_case 读回历史 case（内部含 summary 与原始场一致性门）；
- 部件卡片/整装合成卡片数值（Qt property `value`，模块42/49 口径）与同源
  part_field_views/fields 重算 |diff|≤1e-9 相对；明细表 |U|（.6g 展示精度）
  ≤5e-6 相对；卡片文本与数值不脱节（≤1e-5 相对）；
- 以整装合成卡片显示值为结论，与冻结参考 1e-9 门（run_reader 统一执行）。
"""
from __future__ import annotations

import datetime
import os
import re
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "verify"))
sys.path.insert(0, str(ROOT / "app"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("OMP_NUM_THREADS", "2")

import shared_products  # noqa: E402
from assembly_check_utils import evidence_dir  # noqa: E402

LOG_PATH = ROOT / "verify" / "logs" / "verify_reuse_results_gui.log"
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
    from PySide6.QtWidgets import QApplication
    from rm_sim.gui.result_dialogs import AssemblyResultDialog
    from rm_sim.solver.history import load_archived_case

    app = QApplication.instance() or QApplication([])

    loaded = load_archived_case(mat["project"], mat["case_name"])
    fields = loaded["fields"]
    views = loaded["views"]
    ctx = loaded["context"]
    case_dir = mat["case_dir"]

    parts = {name: {"nodes": len(v.node_ids), "max_u": v.max_u_mag(), "max_mises": v.max_mises()}
             for name, v in views.items()}
    composite = {"nodes": len(fields.node_ids), "max_u": fields.max_u_mag(),
                 "max_mises": float(fields.s_mises.max())}
    parts_meta: dict[str, dict] = {}
    for k, entry in enumerate(ctx.get("assembly", {}).get("parts") or [], start=1):
        material = entry.get("material") or {}
        density = float(material.get("density_t_mm3") or 0.0)
        volume = float(entry.get("volume_mm3") or 0.0)
        parts_meta[f"PART{k}"] = {"mass_kg": volume * density * 1000.0,
                                  "material": material.get("name") or "—"}
    composite_meta = {"mass_kg": sum(m["mass_kg"] for m in parts_meta.values()),
                      "material": "全部部件（明细见各部件卡片）"}

    dialog = AssemblyResultDialog(
        parts, composite=composite,
        interfaces=ctx.get("assembly", {}).get("interface_pressure", []),
        parts_meta={"装配合成": composite_meta, **parts_meta},
        report_path=str(case_dir / "report.md"))
    dialog.resize(760, 520)
    dialog.show()
    app.processEvents()

    for name, view in views.items():
        card = dialog._cards.get(name) or {}
        if not card:
            raise AssertionError(f"缺部件卡片: {name}")
        mises_view, u_view = float(view.max_mises()), float(view.max_u_mag())
        card_mises = float(card["mises"].property("value"))
        card_u = float(card["u"].property("value"))
        for label, got, expect in (("卡片 Mises", card_mises, mises_view),
                                   ("卡片 |U|", card_u, u_view)):
            if abs(got - expect) > 1e-9 * max(abs(expect), 1e-30):
                raise AssertionError(f"{name} {label} 与同源重算不一致: {got} vs {expect}")
        text_mises = float(re.search(r":\s*([-+0-9.eE]+)", card["mises"].text()).group(1))
        if abs(text_mises - mises_view) > 1e-5 * max(abs(mises_view), 1e-30):
            raise AssertionError(f"{name} 卡片文本与数值脱节: {text_mises} vs {mises_view}")
        dialog._combo.setCurrentText(name)
        app.processEvents()
        table_u = float(dialog._table.item(1, 1).text().split()[0])
        if abs(table_u - u_view) > 5e-6 * max(abs(u_view), 1e-30):
            raise AssertionError(f"{name} 明细表 |U| 与同源重算不一致: {table_u} vs {u_view}")
        log(f"[GUI] {name}: 卡片 Mises={card_mises:.9g} MPa、|U|={card_u:.9g} mm 与同源重算一致；"
            f"明细表 |U|={table_u:.6g}；节点 {len(view.node_ids)}")
    card = dialog._cards.get("装配合成")
    if card is None:
        raise AssertionError("缺整装合成卡片")
    composite_u = float(card["u"].property("value"))
    composite_mises = float(card["mises"].property("value"))
    if abs(composite_u - composite["max_u"]) > 1e-9 * max(composite["max_u"], 1e-30) \
            or abs(composite_mises - composite["max_mises"]) > 1e-9 * max(composite["max_mises"], 1e-30):
        raise AssertionError("整装合成卡片与 fields 同源重算不一致")
    dialog.close()
    dialog.deleteLater()
    app.processEvents()
    log(f"[GUI] 整装合成卡片: max Mises={composite_mises:.9g} MPa、max|U|={composite_u:.9g} mm"
        "（对话框显示值即结论，交冻结参考 1e-9 门）")
    return {"max_mises_mpa": composite_mises, "max_u_mag_mm": composite_u,
            "rf_sum_n": [float(x) for x in ctx["results"]["rf_sum_n"]]}


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    work = evidence_dir("reuse_results_gui")
    log(f"证据目录: {work}")
    try:
        source, numbers = shared_products.run_reader(work, "reuse_results_gui", "gui", read_fn)
    except Exception as exc:  # noqa: BLE001
        log(f"REUSE_RESULTS_GUI: FAIL（{type(exc).__name__}: {exc}）")
        log(traceback.format_exc()[-2000:])
        return 1
    log(f"[结论] GUI类读取型检查 source={source}，max Mises={numbers['max_mises_mpa']:.9g} MPa、"
        f"max|U|={numbers['max_u_mag_mm']:.9g} mm、ΣFy={numbers['rf_sum_n'][1]:.9g} N"
        "——与冻结参考 1e-9 门一致")
    log("REUSE_RESULTS_GUI: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
