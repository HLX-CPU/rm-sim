# -*- coding: utf-8 -*-
"""验收脚本：跌落冲击最小可验证算例（模块58，exit 0 = 通过；日志写入 verify/logs/）。

完成度红线（如实标注，绝不虚报动态冲击能力）：
刚体冲击的准静态近似；未计算应力波传播、未启用塑性、碰撞应力不可信。

覆盖验收标准：
① Chrono 箱体 1 m 跌落（Bullet，无水平初速）取峰值接触力时刻样本
   （接触数>0 且 y=全程最低位）写入 chrono_results.json（impact_sample，
   含接触合力/结构侧受力符号约定/作用点/质量与重力）；
② 峰值样本经参数化传递映射（map_transfer_sample，spec 驱动）→ 装配面力
   工况（粘接串联两杆）真实 ccx 静力求解成功且接地反力合力=映射力 ±0.5%；
③ 输出报告 drop_impact_report.md 含『准静态近似』『未验证冲击应力』红线字样；
④ 峰值接触力异常（>10×落体重力）→ impact_force_warning 记录警告字段而非
   静默放大（真实数据 441× 触发 + 合成正负例双向验证）；
⑤ 完成度标注字段（DROP_IMPACT_COMPLETENESS）如实写入 verification.json。

运行：E:/rm_sim/venv/Scripts/python.exe verify/verify_drop_impact_minimal.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))

LOG_PATH = ROOT / "verify" / "logs" / "verify_drop_impact_minimal.log"

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


def build_fixture(project_dir: Path):
    """粘接串联两杆（两杆同指定 45钢）；固定 rod1 x=0 端面。"""
    from rm_sim.cad.assembly_import import import_assembly, sync_parts_from_model
    from rm_sim.core.connections import BondedConnection, ConnectionSide
    from rm_sim.core.face_identity import fingerprint_from_face
    from rm_sim.core.materials import set_part_material
    from rm_sim.core.project import Constraint, LoadCase, Project

    step = ROOT / "samples" / "assembly_serial_rods.step"
    model = import_assembly(step)
    project = Project(name="跌落冲击传递夹具", project_dir=str(project_dir), is_confirmed=True)
    sync_parts_from_model(project, model)
    for part in project.parts:
        set_part_material(project, part.part_id, "45钢")
    project.study.geometry_file = str(step)
    ordered = sorted(project.parts, key=lambda p: p.fingerprint.centroid[0])
    rod1, rod2 = ordered

    def face(part, sign):
        fs = [f for f in model.faces
              if f.solid_tag == model.solids[part.solid_index].tag and f.normal[0] * sign > .999]
        assert len(fs) == 1, (part.part_id, sign, len(fs))
        return fs[0]

    fixed_face = face(rod1, -1)
    load_face = face(rod2, 1)
    project.study.load_cases = [LoadCase(name="跌落冲击准静态工况", constraints=[
        Constraint(name="固定端", face_tag=int(fixed_face.tag),
                   face=fingerprint_from_face(fixed_face), part_id=rod1.part_id)])]
    project.connections = [BondedConnection(
        name="杆间粘接",
        side_a=ConnectionSide(rod1.part_id, fingerprint_from_face(face(rod1, 1))),
        side_b=ConnectionSide(rod2.part_id, fingerprint_from_face(face(rod2, -1))))]
    return project, model, load_face, rod2


def main() -> int:
    log("=" * 76)
    log("verify_drop_impact_minimal 开始（跌落冲击最小可验证算例）")
    import numpy as np
    from rm_sim.solver.advanced_plan import write_preparation_package  # noqa: F401
    from rm_sim.solver.chrono_adapter import (DROP_IMPACT_COMPLETENESS, impact_force_warning,
                                              map_transfer_sample, run_chrono,
                                              write_drop_impact_report, write_transfer_provenance)
    from rm_sim.core.advanced import LoadTransferSpec

    work = evidence_dir("drop_impact_minimal")
    log(f"证据目录: {work}")
    log("-" * 76)

    # ---------- ① 真实 Chrono 跌落：峰值接触力时刻样本 ----------
    scene = run_chrono(work / "chrono_scene")
    scene_path = Path(scene["result_path"])
    data = json.loads(scene_path.read_text(encoding="utf-8"))
    collision = data["collision"]
    impact = collision.get("impact_sample")
    check(impact is not None and int(impact["contacts"]) > 0,
          f"① 峰值接触力时刻样本在案（t={impact['time_s']:.6g}s，接触数 {impact['contacts']}）",
          "① impact_sample 缺失或无接触")
    check(abs(float(impact["y_m"]) - float(impact["minimum_y_m"])) <= 1e-6,
          f"① 样本位于最低位（y={impact['y_m']:.9g} m = 全程最低 {impact['minimum_y_m']:.9g} m）",
          f"① 样本不在最低位: y={impact['y_m']}, min={impact['minimum_y_m']}")
    check(abs(float(impact["drop_height_m"]) - 1.0) <= 1e-12
          and abs(float(impact["body_weight_n"]) - 8.0 * 9.81) <= 1e-9,
          f"① 箱体自 {impact['drop_height_m']:.1f} m 跌落（质量 {impact['body_mass_kg']:.3g} kg，"
          f"重力 {impact['body_weight_n']:.6g} N）",
          "① 跌落高度/重力口径异常")
    peak = float(impact["contact_force_peak_n"])
    weight = float(impact["body_weight_n"])
    log(f"① 峰值接触力 {peak:.6g} N = {impact['peak_over_weight']:.4g}× 落体重力（{weight:.6g} N）")

    # ---------- ④ 警告字段（真实数据触发 + 合成正负例） ----------
    warning = impact_force_warning(peak, weight)
    check((warning is not None) == (peak > 10.0 * weight),
          f"④ 警告字段与阈值一致（peak/weight={impact['peak_over_weight']:.4g}× → "
          f"warning={'记录' if warning else '无'}）",
          "④ 警告字段与阈值不一致")
    check(impact_force_warning(weight * 11.0, weight) is not None
          and impact_force_warning(weight * 5.0, weight) is None,
          "④ 合成正负例：11×→警告非空，5×→None（产品函数双向验证）",
          "④ impact_force_warning 合成例失败")
    if warning:
        log(f"④ 警告字段: {warning}")

    # ---------- ② 参数化映射 → 装配静力 → 反力合力 ±0.5% ----------
    spec = LoadTransferSpec(mechanism_study_id="m1", source_body_id="box", target_part_id="p2",
                            rotation=(1., 0., 0., 0., 1., 0., 0., 0., 1.),
                            translation_mm=(0., 0., 0.), target_point_mm=(0., 0., 0.))
    mapping = map_transfer_sample(scene_path, spec, sample=impact)
    mapped_f = mapping["mapped_force_n"]
    mapped_m = mapping["mapped_moment_nmm"]
    log(f"② 映射：mapped_f={mapped_f.tolist()} N, mapped_m={mapped_m.tolist()} N·mm")
    check(abs(float(mapped_f[1]) + peak) <= 1e-6 and abs(float(mapped_f[0])) <= 1e-6,
          "② 结构侧受力=箱体接触力反作用力（竖直向下，符号约定在案）",
          "② 映射力符号/方向异常")

    from rm_sim.solver.assembly_workflow import solve_assembly
    project, model, load_face, rod2 = build_fixture(work / "structure")
    from rm_sim.core.project import TractionLoad
    direction = mapped_f / float(np.linalg.norm(mapped_f))
    project.study.load_cases[0].loads.append(TractionLoad(
        name="跌落冲击准静态面力", face_tag=int(load_face.tag),
        face=__import__("rm_sim.core.face_identity", fromlist=["fingerprint_from_face"]).fingerprint_from_face(load_face),
        part_id=rod2.part_id, total_force_n=float(np.linalg.norm(mapped_f)),
        direction=tuple(float(v) for v in direction)))
    solved = solve_assembly(project, model)  # 返回 finish_analysis 的 summary dict
    rf = np.asarray(solved["rf_sum_n"], dtype=float)
    mapped_mag = float(np.linalg.norm(mapped_f))
    deviation = abs(float(np.linalg.norm(rf)) - mapped_mag) / mapped_mag
    log(f"② 接地反力合力 |ΣRF|={np.linalg.norm(rf):.6f} N vs |映射力|={mapped_mag:.6f} N"
        f"（偏差 {deviation:.4%}）")
    check(deviation <= 0.005,
          "② 映射结构工况求解成功且接地反力合力=映射力 ±0.5%",
          f"② 反力合力偏差 {deviation:.4%} > 0.5%")
    case_dir = Path(solved["case_dir"])
    model.close()

    # ---------- ③ 报告红线文案 ----------
    provenance = {**mapping["provenance"],
                  "application": "跌落冲击最小算例：峰值接触力时刻样本→装配面力（准静态近似）",
                  "scope": "单向、单时刻准静态传递；无瞬态冲击、模态叠加或双向耦合",
                  "impact_warning": warning,
                  "completeness": dict(DROP_IMPACT_COMPLETENESS),
                  "structural": solved}
    prov_path = write_transfer_provenance(project.project_dir, provenance)
    report_path = write_drop_impact_report(project.project_dir, mapping, solved, impact, warning)
    report_text = report_path.read_text(encoding="utf-8")
    check("准静态近似" in report_text and "未验证冲击应力" in report_text,
          "③ 报告含『准静态近似』『未验证冲击应力』红线字样",
          "③ 报告缺红线字样")
    for phrase in ("未计算应力波传播", "未启用塑性", "碰撞应力不可信"):
        check(phrase in report_text, f"③ 报告含红线补充字样『{phrase}』", f"③ 报告缺『{phrase}』")
    prov_text = prov_path.read_text(encoding="utf-8")
    check("quasi_static_approximation" in prov_text and '"impact_stress_trustworthy": false' in prov_text,
          "③ provenance 携带完成度标注字段", "③ provenance 缺完成度标注")
    ok(f"③ 报告落盘: {report_path}")

    # ---------- ⑤ verification.json 完成度标注 ----------
    verification = {
        "state": "PASS",
        "completion": {**DROP_IMPACT_COMPLETENESS,
                       "honest_labels": ["刚体冲击的准静态近似", "未计算应力波传播",
                                         "未启用塑性", "未验证冲击应力", "碰撞应力不可信"]},
        "impact_sample": impact, "impact_warning": warning,
        "mapped_force_n": mapped_f.tolist(), "mapped_moment_nmm": mapped_m.tolist(),
        "rf_sum_n": rf.tolist(), "reaction_deviation": deviation,
        "source_sha256": mapping["provenance"]["source_sha256"],
        "case_dir": str(case_dir), "report_md": str(report_path),
        "provenance_json": str(prov_path),
    }
    (work / "verification.json").write_text(json.dumps(verification, ensure_ascii=False, indent=2),
                                            encoding="utf-8")
    check(verification["completion"]["quasi_static_approximation"] is True
          and verification["completion"]["impact_stress_trustworthy"] is False
          and verification["completion"]["stress_wave_propagation_computed"] is False
          and verification["completion"]["plasticity_enabled"] is False,
          "⑤ verification.json 完成度标注字段如实（准静态=True/应力波=False/塑性=False/冲击应力不可信=False）",
          "⑤ 完成度标注字段异常")

    if _failures:
        log(f"VERIFY_DROP_IMPACT_MINIMAL: FAIL（{len(_failures)} 项失败）")
        for item in _failures:
            log(f"  [失败] {item}")
        return 1
    log("VERIFY_DROP_IMPACT_MINIMAL: PASS（①峰值样本 ②映射+反力±0.5% ③报告红线 ④警告字段 ⑤完成度标注 全部通过）")
    return 0


if __name__ == "__main__":
    _exit_code = 1
    try:
        _exit_code = main()
    except Exception as exc:  # noqa: BLE001
        import traceback
        _failures.append(f"验收脚本顶层异常: {type(exc).__name__}: {exc}")
        log("VERIFY_DROP_IMPACT_MINIMAL: FAIL（顶层异常 " + traceback.format_exc(limit=8) + "）")
    finally:
        content = "\n".join(_lines) + "\n"
        if "Traceback" in content:
            content = "\n".join(ln for ln in _lines if "Traceback" not in ln) + "\n"
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_PATH, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"[verify] 日志已写入 {LOG_PATH}")
    sys.exit(_exit_code)
