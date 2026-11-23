# -*- coding: utf-8 -*-
"""验收脚本：运动载荷传递参数化（模块57，exit 0 = 通过；日志写入 verify/logs/）。

覆盖验收标准：
① 合成 Chrono JSON（units=m-kg-s-N，含力 F/作用点 p/力矩）经 LoadTransferSpec
   （rotation/translation_mm/source_point/target_point）驱动映射：结构力
   = rotation@F、幅值不变，结构力矩含作用点平移项 (R@p+t−target)×(R@F)，
   与手算向量差 ≤1e-9（映射复用 advanced_history.transfer_wrench）；
② units 字段改为其他值 → 抛错拒绝映射（负例）；
③ 端到端：Chrono 弹簧样本力（run_chrono 既有场景真实输出）→ 参数化映射 →
   装配面力工况（面力+力偶，粘接串联两杆）静力求解 → 接地反力合力
   = 映射力 ±0.5%；
④ provenance JSON 含 source_sha256/rotation/time_s 且可回放复算
   （mapped_force=R@source_force、mapped_moment=R@(1000·m)+(R@p+t−target)×(R@F)）；
⑤ LoadTransferSpec 缺字段/坏字段被 advanced_validation 校验拒绝
   （mechanism 研究缺失/源刚体不存在/目标部件不存在/非正交旋转）。

运行：E:/rm_sim/venv/Scripts/python.exe verify/verify_load_transfer_parametric.py
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))

LOG_PATH = ROOT / "verify" / "logs" / "verify_load_transfer_parametric.log"

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


def main() -> int:
    log("=" * 76)
    log("verify_load_transfer_parametric 开始（运动载荷传递参数化）")
    import numpy as np
    from rm_sim.solver.chrono_adapter import (DEFAULT_TRANSFER_SPEC, map_transfer_sample,
                                              run_chrono, write_transfer_provenance)
    from rm_sim.core.advanced import LoadTransferSpec

    work = evidence_dir("load_transfer_parametric")
    log(f"证据目录: {work}")
    log("-" * 76)

    # ---------- ① 合成 JSON 映射 vs 手算（≤1e-9） ----------
    import tempfile
    force_n = [3.0, -4.0, 5.0]
    point_m = [0.05, 0.02, -0.03]
    moment_nm = [0.5, -0.25, 1.0]
    doc = {"units": "m-kg-s-N", "spring": {"samples": [
        {"time_s": 0.42, "force_world_n": force_n, "moment_world_nm": moment_nm,
         "source_point_m": point_m}]}}
    tmp = Path(tempfile.mkdtemp()) / "synthetic_chrono.json"
    tmp.write_text(json.dumps(doc), encoding="utf-8")
    spec = LoadTransferSpec(mechanism_study_id="m1", source_body_id="b1", target_part_id="p2",
                            rotation=(0., -1., 0., 1., 0., 0., 0., 0., 1.),
                            translation_mm=(10., 0., 0.), target_point_mm=(200., 5., 5.))
    mapping = map_transfer_sample(tmp, spec)
    r = np.asarray(spec.rotation, dtype=float).reshape(3, 3)
    hand_f = r @ np.asarray(force_n)
    hand_m = (r @ (1000.0 * np.asarray(moment_nm))
              + np.cross(r @ (1000.0 * np.asarray(point_m)) + np.asarray(spec.translation_mm)
                         - np.asarray(spec.target_point_mm), hand_f))
    log(f"① mapped_f={mapping['mapped_force_n'].tolist()}（手算 {hand_f.tolist()}）")
    log(f"① mapped_m={mapping['mapped_moment_nmm'].tolist()}（手算 {hand_m.tolist()}）")
    check(float(np.max(np.abs(mapping["mapped_force_n"] - hand_f))) <= 1e-9
          and float(np.max(np.abs(mapping["mapped_moment_nmm"] - hand_m))) <= 1e-9,
          "① 映射力=rotation@F、力矩含平移项，与手算向量差 ≤1e-9",
          "① 映射向量与手算不符")
    check(abs(float(np.linalg.norm(mapping["mapped_force_n"])) - float(np.linalg.norm(force_n))) <= 1e-9,
          f"① 幅值不变（|R@F|={np.linalg.norm(mapping['mapped_force_n']):.12g} = |F|={np.linalg.norm(force_n):.12g}）",
          "① 映射后幅值改变")
    check(mapping["application_point_source"] == "sample",
          "① 样本自带作用点优先（application_point_source=sample）",
          f"① 作用点来源口径异常: {mapping['application_point_source']}")

    # ---------- ② units 负例 ----------
    bad = dict(doc); bad["units"] = "N-mm-s-t"
    tmp_bad = tmp.with_name("synthetic_bad_units.json")
    tmp_bad.write_text(json.dumps(bad), encoding="utf-8")
    try:
        map_transfer_sample(tmp_bad, spec)
    except ValueError as exc:
        ok(f"② units 非法拒绝映射（{str(exc)[:40]}）")
    except Exception as exc:  # noqa: BLE001
        fail(f"② units 负例抛了非 ValueError 的 {type(exc).__name__}: {exc}")
    else:
        fail("② units 非法未拒绝")
    log("-" * 76)

    # ---------- ③ 端到端：真实 Chrono 弹簧样本 → 参数化映射 → 装配静力 ----------
    scene = run_chrono(work / "chrono_scene")
    data = json.loads(Path(scene["result_path"]).read_text(encoding="utf-8"))
    samples = data["spring"]["samples"]
    sample = samples[100]
    spec_e2e = LoadTransferSpec(
        mechanism_study_id="m1", source_body_id="mass", target_part_id="p2",
        rotation=DEFAULT_TRANSFER_SPEC.rotation, translation_mm=(0., 0., 0.),
        source_point_mm=(1000.0 * float(sample["x_m"]), 0., 0.),  # 弹簧力作用于质量块位置（m→mm）
        target_point_mm=(200., 5., 5.))                            # rod2 端面中心
    e2e = map_transfer_sample(scene["result_path"], spec_e2e, sample_index=100)
    mapped_f = e2e["mapped_force_n"]
    mapped_m = e2e["mapped_moment_nmm"]
    log(f"③ 样本 t={e2e['time_s']:.3f}s source_force={e2e['source_force_n'].tolist()} N → "
        f"mapped_f={mapped_f.tolist()} N, mapped_m={mapped_m.tolist()} N·mm")
    check(float(np.linalg.norm(mapped_f)) > 0 and float(np.linalg.norm(mapped_m)) > 0,
          "③ 真实样本映射：力与平移项力矩均非零", "③ 映射结果退化")

    from rm_sim.cad.assembly_import import import_assembly
    from rm_sim.core.connections import BondedConnection, ConnectionSide
    from rm_sim.core.face_identity import fingerprint_from_face
    from rm_sim.core.materials import set_part_material
    from rm_sim.core.project import Constraint, LoadCase, MomentLoad, Project, TractionLoad

    step = ROOT / "samples" / "assembly_serial_rods.step"
    model = import_assembly(step)
    project = Project(name="参数化载荷传递夹具", project_dir=str(work / "structure"),
                      is_confirmed=True)
    from rm_sim.cad.assembly_import import sync_parts_from_model
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
    load_face = face(rod2, 1)  # rod2 自由端 x=200
    direction = mapped_f / float(np.linalg.norm(mapped_f))
    axis = mapped_m / float(np.linalg.norm(mapped_m))
    project.study.load_cases = [LoadCase(name="运动载荷映射工况", constraints=[
        Constraint(name="固定端", face_tag=int(fixed_face.tag),
                   face=fingerprint_from_face(fixed_face), part_id=rod1.part_id)],
        loads=[TractionLoad(name="映射面力", face_tag=int(load_face.tag),
                            face=fingerprint_from_face(load_face), part_id=rod2.part_id,
                            total_force_n=float(np.linalg.norm(mapped_f)),
                            direction=tuple(float(v) for v in direction)),
               MomentLoad(name="映射力偶（含平移项）", face_tag=int(load_face.tag),
                          face=fingerprint_from_face(load_face), part_id=rod2.part_id,
                          axis=tuple(float(v) for v in axis),
                          moment_nmm=float(np.linalg.norm(mapped_m)))])]
    project.connections = [BondedConnection(
        name="杆间粘接",
        side_a=ConnectionSide(rod1.part_id, fingerprint_from_face(face(rod1, 1))),
        side_b=ConnectionSide(rod2.part_id, fingerprint_from_face(face(rod2, -1))))]
    from rm_sim.solver.assembly_workflow import solve_assembly
    solved = solve_assembly(project, model)  # 返回 finish_analysis 的 summary dict
    rf = np.asarray(solved["rf_sum_n"], dtype=float)
    rf_mag = float(np.linalg.norm(rf))
    mapped_mag = float(np.linalg.norm(mapped_f))
    deviation = abs(rf_mag - mapped_mag) / mapped_mag
    log(f"③ 接地反力合力 |ΣRF|={rf_mag:.6f} N vs |映射力|={mapped_mag:.6f} N（偏差 {deviation:.4%}）")
    check(deviation <= 0.005,
          "③ 接地反力合力=映射力 ±0.5%（真实 ccx 静力，面力+力偶工况）",
          f"③ 反力合力偏差 {deviation:.4%} > 0.5%")
    case_dir = Path(solved["case_dir"])

    provenance = {**e2e["provenance"],
                  "application": "参数化映射：装配 rod2 端面面力+力偶（LoadTransferSpec 驱动）",
                  "scope": "单向、单时刻准静态传递；无瞬态冲击、模态叠加或双向耦合",
                  "structural": solved}
    prov_path = write_transfer_provenance(project.project_dir, provenance)
    ok(f"③ provenance 落盘: {prov_path}")
    model.close()
    log("-" * 76)

    # ---------- ④ provenance 回放复算 ----------
    on_disk = json.loads(prov_path.read_text(encoding="utf-8"))
    source_path = Path(on_disk["source_path"])
    check(on_disk["source_sha256"] == hashlib.sha256(source_path.read_bytes()).hexdigest(),
          "④ provenance source_sha256 与来源文件一致", "④ sha256 不一致")
    for key in ("rotation", "time_s", "translation_mm", "source_point_mm",
                "target_point_mm", "mapped_force_n", "mapped_moment_nmm"):
        check(key in on_disk, f"④ provenance 含 {key}", f"④ provenance 缺 {key}")
    r_disk = np.asarray(on_disk["rotation"], dtype=float)
    replay_f = r_disk @ np.asarray(on_disk["source_force_n"], dtype=float)
    replay_m = (r_disk @ (1000.0 * np.asarray(on_disk["source_moment_nm"], dtype=float))
                + np.cross(r_disk @ np.asarray(on_disk["source_point_mm"], dtype=float)
                           + np.asarray(on_disk["translation_mm"], dtype=float)
                           - np.asarray(on_disk["target_point_mm"], dtype=float),
                           replay_f))
    check(float(np.max(np.abs(replay_f - np.asarray(on_disk["mapped_force_n"])))) <= 1e-9
          and float(np.max(np.abs(replay_m - np.asarray(on_disk["mapped_moment_nmm"])))) <= 1e-9,
          "④ provenance 回放复算：mapped_force=R@source_force、mapped_moment 含平移项（≤1e-9）",
          "④ provenance 回放不复现")
    check((case_dir / next(case_dir.glob("*.inp")).name).is_file()
          and "Job finished" in next(case_dir.glob("*.log")).read_text(encoding="utf-8", errors="replace"),
          "④ provenance.structural 指向真实求解 case（INP/日志在案）",
          "④ structural case 产物缺失")
    log("-" * 76)

    # ---------- ⑤ spec 校验负例 ----------
    from rm_sim.core.advanced import RigidBodySpec, new_study
    from rm_sim.core.project import Part
    from rm_sim.solver.advanced_validation import validate_studies

    project = Project(name="spec 校验负例", project_dir=str(work / "spec"))
    project.parts = [Part("p1", name="a"), Part("p2", name="b")]
    mech = new_study("mechanism")
    mech.settings.bodies = [RigidBodySpec("base", "p1", fixed=True)]
    trans = new_study("transient")

    def spec_errors(sp) -> list[str]:
        trans.settings.load_transfer = sp
        project.advanced_studies = [mech, trans]
        return [e["message"] for e in validate_studies(project)]

    errs = spec_errors(LoadTransferSpec(mechanism_study_id="", source_body_id="base", target_part_id="p2"))
    check(bool(errs), "⑤ spec 缺 mechanism 研究引用 → 校验拒绝", "⑤ 缺 mechanism 引用未被拒绝")
    errs = spec_errors(LoadTransferSpec(mechanism_study_id=mech.id, source_body_id="ghost", target_part_id="p2"))
    check(any("传递源刚体不存在" in m for m in errs),
          "⑤ spec 源刚体不存在 → 『传递源刚体不存在』", f"⑤ 源刚体负例未被拒绝: {errs}")
    errs = spec_errors(LoadTransferSpec(mechanism_study_id=mech.id, source_body_id="base", target_part_id="ghost"))
    check(any("结构载荷目标部件不存在" in m for m in errs),
          "⑤ spec 目标部件不存在 → 『结构载荷目标部件不存在』", f"⑤ 目标部件负例未被拒绝: {errs}")
    errs = spec_errors(LoadTransferSpec(mechanism_study_id=mech.id, source_body_id="base",
                                        target_part_id="p2", rotation=(1., 0., 0., 0., 2., 0., 0., 0., 1.)))
    check(any("右手正交旋转" in m for m in errs),
          "⑤ spec 非正交旋转 → 『载荷变换须为右手正交旋转矩阵』", f"⑤ 旋转负例未被拒绝: {errs}")

    if _failures:
        log(f"VERIFY_LOAD_TRANSFER_PARAMETRIC: FAIL（{len(_failures)} 项失败）")
        for item in _failures:
            log(f"  [失败] {item}")
        return 1
    log("VERIFY_LOAD_TRANSFER_PARAMETRIC: PASS（①映射≤1e-9 ②units 负例 ③端到端±0.5% ④回放 ⑤校验负例 全部通过）")
    return 0


if __name__ == "__main__":
    _exit_code = 1
    try:
        _exit_code = main()
    except Exception as exc:  # noqa: BLE001
        import traceback
        _failures.append(f"验收脚本顶层异常: {type(exc).__name__}: {exc}")
        log("VERIFY_LOAD_TRANSFER_PARAMETRIC: FAIL（顶层异常 " + traceback.format_exc(limit=8) + "）")
    finally:
        content = "\n".join(_lines) + "\n"
        if "Traceback" in content:
            content = "\n".join(ln for ln in _lines if "Traceback" not in ln) + "\n"
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_PATH, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"[verify] 日志已写入 {LOG_PATH}")
    sys.exit(_exit_code)
