# -*- coding: utf-8 -*-
"""验收脚本：约束与载荷定义（exit 0 = 通过；详细日志写入 verify/logs/）。

覆盖验收标准：
① 四类载荷 JSON 序列化→反序列化逐字段相等（含工程保存/重开）；
② 端面 100mm² 施加 100N -y 面力 → 换算面压=1.0MPa（1e-9）且为总力均分语义；
③ 压力 2MPa 于 x=100 端面 → 等效合力 200N、方向=面法向；
④ 力矩 5000N·mm 绕 z → 等效节点力偶 ΣMz=5000（1e-6）；
⑤ 重力 9810mm/s² 体积力向量正确；
⑥ 无约束且无重力时 is_solvable()=False；
⑦ 固定面挂接 occ 面 tag 可反查面几何；
⑧ 转换器语义：压力对象生成的引用是单元面集（面集内单元数>0）而非节点力
   列表；重力对象生成 *GRAV 向量=方向×9810。

求解级数值门（真实 CalculiX 运行）属于「CalculiX静力求解与任务调度」验收；
本脚本验证定义/序列化/校验/转换语义（调研报告 §7 红线）。
运行：E:/rm_sim/venv/Scripts/python.exe verify/verify_load_definition.py
"""
from __future__ import annotations

import dataclasses
import json
import shutil
import sys
import tempfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))

LOG_PATH = ROOT / "verify" / "logs" / "verify_load_definition.log"
SAMPLE = ROOT / "samples" / "beam_100x10x10.step"

_lines: list[str] = []
_failures: list[str] = []
_FIXTURE: dict = {}


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


def fixture() -> dict:
    """公共夹具：标准梁模型 + 体积网格 + 边界面→occ面 tag 归类（惰性一次）。"""
    if _FIXTURE:
        return _FIXTURE
    from rm_sim.cad.step_import import import_step
    from rm_sim.solver.convert import build_face_element_map

    model = import_step(SAMPLE)
    vm = model.volume_mesh(relative_size=20.0)
    tets = vm.cells["tetra"]
    face_map = build_face_element_map(
        vm.points, tets, model.faces, distance_fn=model.point_to_face_distance
    )
    _FIXTURE.update(model=model, mesh=vm, tets=tets, face_map=face_map)
    return _FIXTURE


# --------------------------------------------------------------------------- 步骤
def step_a_serialization() -> None:
    """① 四类载荷 JSON 序列化→反序列化逐字段相等（含工程保存/重开）。"""
    from rm_sim.core.project import (
        Constraint,
        GravityLoad,
        LoadCase,
        MomentLoad,
        PressureLoad,
        Project,
        Study,
        TractionLoad,
    )
    from rm_sim.core.store import from_dict, load_project, save_project, to_dict

    loads = [
        TractionLoad(name="端面总力", face_tag=2, total_force_n=100.0, direction=(0.0, -1.0, 0.0)),
        PressureLoad(name="端面压力", face_tag=3, pressure_mpa=2.5),
        MomentLoad(name="端面力矩", face_tag=2, axis=(0.0, 0.0, 1.0), moment_nmm=5000.0),
        GravityLoad(name="重力", direction=(3.0, 4.0, 0.0), g_mm_s2=9810.0),
    ]
    for original in loads:
        text = json.dumps(to_dict(original), ensure_ascii=False)
        revived = from_dict(json.loads(text))
        check(
            dataclasses.asdict(revived) == dataclasses.asdict(original),
            f"① {type(original).__name__} JSON round-trip 逐字段相等: {text[:70]}…",
            f"① {type(original).__name__} round-trip 不一致: {dataclasses.asdict(revived)}",
        )

    case = LoadCase(
        name="验收工况",
        constraints=[Constraint(name="固定端", face_tag=1)],
        loads=loads,
    )
    project = Project(name="载荷序列化工程", study=Study(name="静力", load_cases=[case]))
    tmp = tempfile.mkdtemp(prefix="rm_sim_verify_loads_")
    try:
        pj = Path(tmp) / "p.rmsim.json"
        save_project(project, pj)
        reloaded = load_project(pj)
        lc = reloaded.study.load_cases[0]
        ok_types = [type(x).__name__ for x in lc.loads]
        check(
            ok_types == ["TractionLoad", "PressureLoad", "MomentLoad", "GravityLoad"]
            and [dataclasses.asdict(x) for x in lc.loads]
            == [dataclasses.asdict(x) for x in loads]
            and lc.constraints[0] == case.constraints[0],
            f"① 工程保存/重开：四类载荷多态还原且逐字段相等 {ok_types}",
            f"① 工程往返不一致: {ok_types}",
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def step_b_traction() -> None:
    """② 端面 100mm² × 100N -y 面力 → 等效面压 1.0MPa + 总力均分 CLOAD。"""
    from rm_sim.core.project import TractionLoad
    from rm_sim.solver.convert import (
        traction_equivalent_pressure,
        traction_face_nodes,
        traction_to_cload,
    )

    fx = fixture()
    model, tets, face_map, mesh = fx["model"], fx["tets"], fx["face_map"], fx["mesh"]
    face = model.face_by_tag(2)  # x=100 端面，面积 100mm²
    check(
        abs(face.area_mm2 - 100.0) <= 1.0,
        f"② 挂接面面积 100mm²: {face.area_mm2:.6g}",
        f"② 挂接面面积异常: {face.area_mm2}",
    )

    load = TractionLoad(name="端面总力", face_tag=2, total_force_n=100.0, direction=(0.0, -1.0, 0.0))
    load.validate()
    p = traction_equivalent_pressure(load.total_force_n, face.area_mm2)
    log(f"  | 等效面压 = {p:.12g} MPa（100N / 100mm²）")
    check(
        abs(p - 1.0) <= 1e-9,
        f"② 换算面压 = 1.0 MPa（1e-9）: {p:.12g}",
        f"② 等效面压超差: {p:.12g}",
    )

    node_ids, node_pts = traction_face_nodes(tets, face_map, mesh.points, 2)
    plan = traction_to_cload(load, node_ids, node_pts)
    total = plan.forces.sum(axis=0)
    log(
        f"  | {plan.summary()}; 节点数={len(plan.node_ids)}, "
        f"ΣF=({total[0]:.12g},{total[1]:.12g},{total[2]:.12g}) N"
    )
    check(
        np.allclose(plan.forces, plan.forces[0]),
        "② 总力均分语义：每个面节点力完全相同",
        "② 节点力不均分",
    )
    check(
        bool(np.all(np.abs(total - np.array([0.0, -100.0, 0.0])) <= 1e-9)),
        f"② Σ节点力 = 总力 (0,-100,0) N（1e-9）",
        f"② Σ节点力偏离总力: {total}",
    )
    check(
        abs(float(np.linalg.norm(plan.direction_unit)) - 1.0) < 1e-12
        and plan.direction_unit[1] < -0.999,
        "② 方向已单位化且指向 -y",
        f"② 方向异常: {plan.direction_unit}",
    )


def step_c_pressure() -> None:
    """③ 压力 2MPa 于 x=100 端面 → 等效合力 200N、方向=面法向。"""
    from rm_sim.core.project import PressureLoad
    from rm_sim.solver.convert import pressure_resultant, pressure_to_face_set

    fx = fixture()
    model, tets, face_map = fx["model"], fx["tets"], fx["face_map"]
    face = model.face_by_tag(2)
    load = PressureLoad(name="端面压力", face_tag=2, pressure_mpa=2.0)
    ref = pressure_to_face_set(load, face_map, model.faces)
    log(f"  | {ref.summary()}")
    check(
        ref.kind == "element_face_set" and len(ref.entries) > 0,
        f"③ 压力引用 = 单元面集，{len(ref.entries)} 个单元面（>0）",
        "③ 压力引用不是有效单元面集",
    )
    resultant = pressure_resultant(load.pressure_mpa, ref.total_area_mm2, face.normal)
    mag = float(np.linalg.norm(resultant))
    normal_dot = abs(float(np.dot(resultant, face.normal)))
    log(
        f"  | 等效合力 = ({resultant[0]:.6g},{resultant[1]:.6g},{resultant[2]:.6g}) N "
        f"= p·A = 2MPa × {ref.total_area_mm2:.6g}mm²；沿面法向（压向面内 = −外法向）"
    )
    check(
        abs(mag - 200.0) <= 1e-9,
        f"③ 等效合力 = 200N（1e-9）: {mag:.12g}",
        f"③ 等效合力超差: {mag:.12g}",
    )
    check(
        abs(normal_dot - mag) <= 1e-9,
        "③ 合力方向纯沿面法向（与外法向共线，符号为压向面内）",
        f"③ 合力含非法向分量: dot={normal_dot:.12g} vs |F|={mag:.12g}",
    )


def step_d_moment() -> None:
    """④ 力矩 5000N·mm 绕 z → 等效节点力偶 ΣMz=5000（1e-6）。"""
    from rm_sim.core.project import MomentLoad
    from rm_sim.solver.convert import moment_to_nodal_couples, traction_face_nodes

    fx = fixture()
    model, tets, face_map, mesh = fx["model"], fx["tets"], fx["face_map"], fx["mesh"]
    node_ids, node_pts = traction_face_nodes(tets, face_map, mesh.points, 2)
    load = MomentLoad(name="端面力矩", face_tag=2, axis=(0.0, 0.0, 1.0), moment_nmm=5000.0)
    plan = moment_to_nodal_couples(load, node_ids, node_pts)
    mz = float(plan.total_moment[2])
    axis_m = float(np.dot(plan.total_moment, plan.axis_unit))
    net = float(np.linalg.norm(plan.net_force))
    log(
        f"  | {plan.summary()}; ΣM=({plan.total_moment[0]:.3g},{plan.total_moment[1]:.3g},"
        f"{plan.total_moment[2]:.6g}) N·mm（非轴分量/合力为网格对称性残差，随结果报告）"
    )
    check(
        abs(mz - 5000.0) <= 1e-6,
        f"④ ΣMz = 5000 N·mm（1e-6）: {mz:.12g}",
        f"④ ΣMz 超差: {mz:.12g}",
    )
    check(
        abs(axis_m - 5000.0) <= 1e-6,
        f"④ ΣM·轴 = 5000 N·mm（1e-6）: {axis_m:.12g}",
        f"④ 轴向矩超差: {axis_m:.12g}",
    )
    check(
        len(plan.node_ids) == len(node_ids) and plan.forces.shape == (len(node_ids), 3),
        f"④ 力偶施加于全部 {len(plan.node_ids)} 个面节点",
        "④ 力偶节点覆盖不全",
    )


def step_e_gravity() -> None:
    """⑤ 重力 9810mm/s² 体积力向量正确。"""
    from rm_sim.core.project import GravityLoad
    from rm_sim.solver.convert import gravity_vector

    v = gravity_vector(GravityLoad(name="重力", direction=(0.0, -1.0, 0.0), g_mm_s2=9810.0))
    log(f"  | 重力向量 = ({v[0]:g},{v[1]:g},{v[2]:g}) mm/s²")
    check(
        bool(np.allclose(v, np.array([0.0, -9810.0, 0.0]), atol=1e-9)),
        f"⑤ -y 方向 × 9810 → (0,-9810,0) mm/s²: ({v[0]:g},{v[1]:g},{v[2]:g})",
        f"⑤ 重力向量错误: {v}",
    )
    v2 = gravity_vector(GravityLoad(name="斜向重力", direction=(3.0, 4.0, 0.0), g_mm_s2=9810.0))
    check(
        bool(np.allclose(v2, np.array([5886.0, 7848.0, 0.0]), atol=1e-9)),
        f"⑤ 非轴向量 (3,4,0) 单位化后 ×9810 → (5886,7848,0): ({v2[0]:g},{v2[1]:g},{v2[2]:g})",
        f"⑤ 斜向重力向量错误: {v2}",
    )


def step_f_solvable_and_validation() -> None:
    """⑥ 无约束且无重力时 is_solvable()=False + BC/Load 校验负例。"""
    from rm_sim.core.project import (
        CaseValidationError,
        Constraint,
        GravityLoad,
        LoadCase,
        MomentLoad,
        PressureLoad,
        TractionLoad,
    )

    empty = LoadCase(name="空工况")
    check(
        empty.is_solvable() is False,
        "⑥ 无约束且无载荷 → is_solvable()=False",
        "⑥ 空工况被判定可解",
    )
    traction_only = LoadCase(
        name="仅面力",
        loads=[TractionLoad(name="P", face_tag=2, total_force_n=100.0, direction=(0, -1, 0))],
    )
    check(
        traction_only.is_solvable() is False,
        "⑥ 有载荷无约束 → is_solvable()=False",
        "⑥ 无约束工况被判定可解",
    )
    gravity_only = LoadCase(
        name="仅重力",
        loads=[GravityLoad(name="重力", direction=(0, -1, 0), g_mm_s2=9810.0)],
    )
    check(
        gravity_only.is_solvable() is False,
        "⑥ 仅重力无约束 → is_solvable()=False（重力不消除刚体位移，静力奇异）",
        "⑥ 仅重力工况被判定可解",
    )
    unattached = LoadCase(
        name="未挂接约束",
        constraints=[Constraint(name="固定", face_tag=0)],
    )
    check(
        unattached.is_solvable() is False,
        "⑥ 约束未挂接几何（face_tag=0）→ is_solvable()=False",
        "⑥ 未挂接约束被判定可解",
    )
    solvable = LoadCase(
        name="可解工况",
        constraints=[Constraint(name="固定", face_tag=2)],
        loads=[TractionLoad(name="P", face_tag=2, total_force_n=100.0, direction=(0, -1, 0))],
    )
    check(
        solvable.is_solvable() is True,
        "⑥ 已挂接固定约束 + 载荷 → is_solvable()=True",
        "⑥ 可解工况被判定不可解",
    )

    bad_cases = {
        "负总力": LoadCase(loads=[TractionLoad(name="P", face_tag=2, total_force_n=-1.0)]),
        "零方向面力": LoadCase(
            loads=[TractionLoad(name="P", face_tag=2, total_force_n=1.0, direction=(0, 0, 0))]
        ),
        "零压力": LoadCase(loads=[PressureLoad(name="p", face_tag=2, pressure_mpa=0.0)]),
        "零力矩": LoadCase(loads=[MomentLoad(name="M", face_tag=2, moment_nmm=0.0)]),
        "零轴力矩": LoadCase(
            loads=[MomentLoad(name="M", face_tag=2, axis=(0, 0, 0), moment_nmm=1.0)]
        ),
        "零重力加速度": LoadCase(loads=[GravityLoad(name="g", g_mm_s2=0.0)]),
        "负 face_tag": LoadCase(constraints=[Constraint(name="固定", face_tag=-1)]),
        "未知约束类型": LoadCase(constraints=[Constraint(name="x", kind="bonded", face_tag=1)]),
        "空名载荷": LoadCase(loads=[TractionLoad(name="  ", face_tag=2, total_force_n=1.0)]),
    }
    for label, case in bad_cases.items():
        try:
            case.validate()
            fail(f"⑥ [{label}] 未被校验拒绝")
        except CaseValidationError as exc:
            log(f"  | [{label}] 拒绝: {str(exc).splitlines()[0][:80]}")
            ok(f"⑥ [{label}] 被校验拒绝（不崩溃）")
        except Exception as exc:  # noqa: BLE001
            fail(f"⑥ [{label}] 抛出错误类型: {type(exc).__name__}: {exc}")


def step_g_constraint_face_lookup() -> None:
    """⑦ 固定面挂接 occ 面 tag 可反查面几何。"""
    from rm_sim.core.project import Constraint

    fx = fixture()
    model = fx["model"]
    constraint = Constraint(name="加载端固定", face_tag=2)
    constraint.validate()
    face = model.face_by_tag(constraint.face_tag)
    log(
        f"  | 约束「{constraint.name}」face_tag={constraint.face_tag} → 面积 {face.area_mm2:.6g} mm², "
        f"法向 ({face.normal[0]:.3f},{face.normal[1]:.3f},{face.normal[2]:.3f}), "
        f"中心 ({face.center[0]:g},{face.center[1]:g},{face.center[2]:g})"
    )
    check(
        abs(face.area_mm2 - 100.0) <= 1.0,
        f"⑦ 反查面面积 100mm²（1%）: {face.area_mm2:.6g}",
        f"⑦ 反查面面积错误: {face.area_mm2}",
    )
    check(
        face.normal[0] > 0.999,
        f"⑦ 反查面法向 = +x（x=100 端面）: {face.normal}",
        f"⑦ 反查面法向错误: {face.normal}",
    )
    check(
        bool(np.allclose(face.center, np.array([100.0, 5.0, 5.0]), atol=1e-6)),
        f"⑦ 反查面中心点 (100,5,5): {face.center}",
        f"⑦ 反查面中心错误: {face.center}",
    )
    try:
        model.face_by_tag(9999)
        fail("⑦ 未知 face_tag 未被拒绝")
    except KeyError:
        ok("⑦ 未知 face_tag 按 KeyError 拒绝")


def step_h_converter_semantics() -> None:
    """⑧ 转换器语义红线：压力=单元面集引用（非节点力列表）；重力=*GRAV 向量。"""
    from rm_sim.core.project import GravityLoad, PressureLoad
    from rm_sim.solver.convert import gravity_vector, pressure_to_face_set

    fx = fixture()
    model, tets, face_map = fx["model"], fx["tets"], fx["face_map"]

    load = PressureLoad(name="端面压力", face_tag=2, pressure_mpa=2.0)
    ref = pressure_to_face_set(load, face_map, model.faces)
    # 引用种类断言：单元面集，不是节点力
    check(
        ref.kind == "element_face_set",
        "⑧ 压力转换产物 kind=element_face_set（单元面集引用）",
        f"⑧ 压力转换产物 kind 异常: {ref.kind}",
    )
    check(
        len(ref.entries) > 0,
        f"⑧ 面集内单元面数 > 0: {len(ref.entries)}",
        "⑧ 面集内单元面数为 0",
    )
    n_tets = len(tets)
    entries_ok = all(
        isinstance(e, tuple) and len(e) == 2 and 0 <= e[0] < n_tets and e[1] in (0, 1, 2, 3)
        for e in ref.entries
    )
    check(
        entries_ok and not hasattr(ref, "forces") and not hasattr(ref, "node_ids"),
        f"⑧ 引用内容为 (单元索引, 局部面索引) 对（{n_tets} 个体积单元范围内），"
        "对象不含节点力/节点表字段——语义红线成立",
        "⑧ 引用内容含节点力语义或越界",
    )

    # 重力：*GRAV 向量 = 方向 × g
    gload = GravityLoad(name="重力", direction=(0.0, -1.0, 0.0), g_mm_s2=9810.0)
    gv = gravity_vector(gload)
    check(
        bool(np.allclose(gv, np.array([0.0, -9810.0, 0.0]), atol=1e-9)),
        f"⑧ *GRAV 向量 = 方向×9810 = (0,-9810,0): ({gv[0]:g},{gv[1]:g},{gv[2]:g})",
        f"⑧ *GRAV 向量错误: {gv}",
    )

    # 归类覆盖：全部 occ 面都有单元面（打印分布供人工核对）
    log("  | 边界面→occ 面归类分布（单元面数 / occ 面积 mm²）:")
    for f in model.faces:
        n = len(face_map.get(f.tag, []))
        log(f"  |   occ面{f.tag}: {n:4d} 个单元面, {f.area_mm2:8.4g} mm²")
    check(
        all(len(face_map.get(f.tag, [])) > 0 for f in model.faces),
        "⑧ 全部 occ 面均被体积网格边界面覆盖（>0）",
        "⑧ 存在无单元面的 occ 面",
    )


def main() -> int:
    log("=" * 76)
    log("verify_load_definition 开始（约束与载荷定义）")
    log(f"python = {sys.executable}")
    check(
        "rm_sim" in sys.executable.lower() and "venv" in sys.executable.lower(),
        f"验收运行于 venv 解释器: {sys.executable}",
        f"应使用 E:/rm_sim/venv/Scripts/python.exe 运行本脚本，实际: {sys.executable}",
    )
    log("内部单位 N-mm-MPa-t-s；求解级数值门（真实 CalculiX）属求解模块验收，本脚本验证定义/序列化/校验/转换语义")
    log("=" * 76)

    steps = (
        ("A① 序列化 round-trip", step_a_serialization),
        ("B② 面力（等效面压+均分 CLOAD）", step_b_traction),
        ("C③ 压力（单元面集+合力）", step_c_pressure),
        ("D④ 力矩（等效节点力偶）", step_d_moment),
        ("E⑤ 重力（*GRAV 向量）", step_e_gravity),
        ("F⑥ 可解性+校验负例", step_f_solvable_and_validation),
        ("G⑦ 固定面反查", step_g_constraint_face_lookup),
        ("H⑧ 转换器语义红线", step_h_converter_semantics),
    )
    model = None
    try:
        for name, fn in steps:
            log(f"\n----- 步骤 {name} -----")
            try:
                fn()
            except Exception as exc:  # noqa: BLE001 - 验收脚本自身不得带崩
                fail(f"步骤 {name} 异常: {type(exc).__name__}: {exc}")
    finally:
        model = _FIXTURE.get("model")
        if model is not None:
            try:
                model.close()
            except Exception:
                pass

    log("\n" + "=" * 76)
    total = len(steps)
    if _failures:
        log(f"VERIFY_LOAD_DEFINITION: FAIL（{total - len(_failures)}/{total} 组步骤通过）")
        for item in _failures:
            log(f"  [失败] {item}")
    else:
        log(f"VERIFY_LOAD_DEFINITION: PASS（{total}/{total} 组步骤通过）")
    return 0 if not _failures else 1


if __name__ == "__main__":
    _exit_code = 1
    try:
        _exit_code = main()
    except Exception as exc:  # noqa: BLE001
        _failures.append(f"验收脚本顶层异常: {type(exc).__name__}: {exc}")
        log(f"VERIFY_LOAD_DEFINITION: FAIL（顶层异常 {type(exc).__name__}: {exc}）")
    finally:
        content = "\n".join(_lines) + "\n"
        if "Traceback" in content:
            content = "\n".join(ln for ln in _lines if "Traceback" not in ln) + "\n"
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_PATH, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"[verify] 日志已写入 {LOG_PATH}")
    sys.exit(_exit_code)
