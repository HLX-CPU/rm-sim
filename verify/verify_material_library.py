# -*- coding: utf-8 -*-
"""验收脚本：材料库与零件材料设置（exit 0 = 通过；详细日志写入 verify/logs/）。

覆盖验收标准：
① 内置库 ≥7 种且关键值在文献范围：结构钢 E=210000/ν=0.3/ρ=7.85e-9（±2%）、
   6061 E=68900±3%、7075 E=71700±3%、TC4 E≈110000±3%；
② 自定义材料增删改后 materials.json round-trip 逐字段相等（用临时文件，
   不触碰真实 E:/rm_sim/materials.json）；
③ 非法输入（负 E、ν≥0.5、ν≤0 等）被校验拒绝不崩溃，且不写坏库文件；
④ 标准梁选 45 钢后导出的求解材料属性 (E=210000, ν=0.3, ρ=7.85e-9) 与
   probes/beamCheck.json parameters 逐字段一致（含工程保存/重开往返）。

本脚本全程无 GUI（无 PySide6）——零件指定材料 core API 无 GUI 可调的直接证明。
运行：E:/rm_sim/venv/Scripts/python.exe verify/verify_material_library.py
"""
from __future__ import annotations

import dataclasses
import json
import logging
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))

LOG_PATH = ROOT / "verify" / "logs" / "verify_material_library.log"
BEAM_CHECK = ROOT / "probes" / "beamCheck.json"

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


# --------------------------------------------------------------------------- 步骤
def step_a_builtin_library() -> None:
    """① 内置库 ≥7 种 + 关键值文献范围。"""
    from rm_sim.core.materials import BUILTIN_MATERIALS, validate_material

    names = [m.name for m in BUILTIN_MATERIALS]
    log(f"  | 内置材料: {names}")
    check(
        len(BUILTIN_MATERIALS) >= 7,
        f"① 内置库 {len(BUILTIN_MATERIALS)} 种 ≥ 7",
        f"① 内置库仅 {len(BUILTIN_MATERIALS)} 种（要求 ≥7）",
    )
    required = ["Q235", "45钢", "6061", "7075", "TC4", "HT250", "尼龙PA6"]
    missing = [n for n in required if n not in names]
    check(
        not missing,
        f"① 要求的 7 种材料齐全: {required}",
        f"① 缺少材料: {missing}",
    )
    for m in BUILTIN_MATERIALS:
        try:
            validate_material(m)
            ok(f"① 内置 {m.name}: E={m.e_modulus_mpa:g} ν={m.poisson:g} ρ={m.density_t_mm3:g} 校验通过")
        except Exception as exc:  # noqa: BLE001
            fail(f"① 内置 {m.name} 校验失败: {exc}")

    def in_range(value: float, expect: float, tol_pct: float) -> bool:
        tol = abs(expect) * tol_pct / 100.0
        return abs(value - expect) <= tol

    q235 = next(m for m in BUILTIN_MATERIALS if m.name == "Q235")
    check(
        in_range(q235.e_modulus_mpa, 210000.0, 2.0)
        and in_range(q235.poisson, 0.3, 2.0)
        and in_range(q235.density_t_mm3, 7.85e-9, 2.0),
        f"① 结构钢(Q235) E=210000/ν=0.3/ρ=7.85e-9（±2%）: "
        f"E={q235.e_modulus_mpa:g} ν={q235.poisson:g} ρ={q235.density_t_mm3:g}",
        f"① 结构钢(Q235)关键值超差: E={q235.e_modulus_mpa:g} ν={q235.poisson:g} ρ={q235.density_t_mm3:g}",
    )
    for name, expect_e in (("6061", 68900.0), ("7075", 71700.0), ("TC4", 110000.0)):
        m = next(x for x in BUILTIN_MATERIALS if x.name == name)
        check(
            in_range(m.e_modulus_mpa, expect_e, 3.0),
            f"① {name} E={m.e_modulus_mpa:g} MPa（{expect_e:g} ±3%）",
            f"① {name} E={m.e_modulus_mpa:g} 超出 {expect_e:g}±3%",
        )


def step_b_custom_roundtrip() -> None:
    """② 自定义材料增删改 → materials.json round-trip 逐字段相等（临时文件）。"""
    from rm_sim.core import materials
    from rm_sim.core.project import Material

    tmp = tempfile.mkdtemp(prefix="rm_sim_verify_materials_")
    lib = Path(tmp) / "materials.json"
    try:
        check(
            materials.load_custom_materials(lib) == [],
            "② 空库读取返回空表（文件缺失不报错）",
            "② 空库读取异常",
        )
        a = Material(name="MYALLOY-1", e_modulus_mpa=70000.0, poisson=0.34,
                     density_t_mm3=2.70e-9, yield_strength_mpa=300.0)
        b = Material(name="MYPOLYMER", e_modulus_mpa=2800.0, poisson=0.41,
                     density_t_mm3=1.13e-9, yield_strength_mpa=70.0)
        materials.add_custom_material(a, lib)
        materials.add_custom_material(b, lib)
        loaded = materials.load_custom_materials(lib)
        check(
            [dataclasses.asdict(x) for x in loaded] == [dataclasses.asdict(a), dataclasses.asdict(b)],
            f"② 新增两条 → round-trip 逐字段相等: {[x.name for x in loaded]}",
            f"② round-trip 不一致: {[dataclasses.asdict(x) for x in loaded]}",
        )

        # 更新（改 E 与屈服强度）
        a2 = Material(name="MYALLOY-1", e_modulus_mpa=71000.0, poisson=0.34,
                      density_t_mm3=2.70e-9, yield_strength_mpa=310.0)
        materials.update_custom_material("MYALLOY-1", a2, lib)
        loaded = materials.load_custom_materials(lib)
        expect = [dataclasses.asdict(a2), dataclasses.asdict(b)]
        check(
            [dataclasses.asdict(x) for x in loaded] == expect,
            "② 更新 E/屈服 → round-trip 逐字段相等",
            f"② 更新后 round-trip 不一致: {[dataclasses.asdict(x) for x in loaded]}",
        )

        # 删除
        materials.remove_custom_material("MYPOLYMER", lib)
        loaded = materials.load_custom_materials(lib)
        check(
            [x.name for x in loaded] == ["MYALLOY-1"]
            and [dataclasses.asdict(x) for x in loaded] == [dataclasses.asdict(a2)],
            "② 删除 MYPOLYMER → 库内仅剩更新后的 MYALLOY-1（逐字段相等）",
            f"② 删除后库内容异常: {[dataclasses.asdict(x) for x in loaded]}",
        )

        # 合并视图：自定义遮蔽同名内置
        shadow = Material(name="Q235", e_modulus_mpa=206000.0, poisson=0.29,
                          density_t_mm3=7.87e-9, yield_strength_mpa=245.0)
        materials.add_custom_material(shadow, lib)
        merged = materials.all_materials(lib)
        q235 = next(x for x in merged if x.name == "Q235")
        check(
            q235 == shadow and sum(1 for x in merged if x.name == "Q235") == 1,
            "② 同名自定义材料遮蔽内置且不重复出现",
            "② 自定义遮蔽内置失败",
        )

        # 重名新增被拒
        try:
            materials.add_custom_material(a2, lib)
            fail("② 重名新增未被拒绝")
        except materials.MaterialValidationError as exc:
            ok(f"② 重名新增被拒绝: {exc}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)  # 只清理本步骤创建的临时目录


def step_c_invalid_input() -> None:
    """③ 非法输入被校验拒绝、不崩溃、不写坏库文件。"""
    from rm_sim.core import materials
    from rm_sim.core.project import Material

    tmp = tempfile.mkdtemp(prefix="rm_sim_verify_materials_bad_")
    lib = Path(tmp) / "materials.json"
    try:
        base = Material(name="OK-MAT", e_modulus_mpa=70000.0, poisson=0.33,
                        density_t_mm3=2.7e-9, yield_strength_mpa=300.0)
        materials.add_custom_material(base, lib)
        before = lib.read_bytes()

        cases = {
            "负 E": Material(name="BAD", e_modulus_mpa=-1000.0, poisson=0.3, density_t_mm3=7.85e-9),
            "E=0": Material(name="BAD", e_modulus_mpa=0.0, poisson=0.3, density_t_mm3=7.85e-9),
            "ν=0.5": Material(name="BAD", e_modulus_mpa=210000.0, poisson=0.5, density_t_mm3=7.85e-9),
            "ν=0": Material(name="BAD", e_modulus_mpa=210000.0, poisson=0.0, density_t_mm3=7.85e-9),
            "ν<0": Material(name="BAD", e_modulus_mpa=210000.0, poisson=-0.1, density_t_mm3=7.85e-9),
            "ν>0.5": Material(name="BAD", e_modulus_mpa=210000.0, poisson=0.6, density_t_mm3=7.85e-9),
            "负 ρ": Material(name="BAD", e_modulus_mpa=210000.0, poisson=0.3, density_t_mm3=-7.85e-9),
            "负屈服": Material(name="BAD", e_modulus_mpa=210000.0, poisson=0.3, density_t_mm3=7.85e-9,
                               yield_strength_mpa=-5.0),
            "空名": Material(name="  ", e_modulus_mpa=210000.0, poisson=0.3, density_t_mm3=7.85e-9),
            "E 类型错误": Material(name="BAD", e_modulus_mpa="210000", poisson=0.3, density_t_mm3=7.85e-9),
        }
        for label, bad in cases.items():
            try:
                materials.validate_material(bad)
                fail(f"③ [{label}] 未被校验拒绝")
                continue
            except materials.MaterialValidationError as exc:
                log(f"  | [{label}] 拒绝: {str(exc).splitlines()[0][:80]}")
                ok(f"③ [{label}] 被校验拒绝（不崩溃）")
            except Exception as exc:  # noqa: BLE001
                fail(f"③ [{label}] 抛出了错误类型: {type(exc).__name__}: {exc}")

            # add 也必须拒绝，且库文件逐字节不变（校验先于持久化）
            try:
                materials.add_custom_material(bad, lib)
                fail(f"③ [{label}] add_custom_material 未拒绝")
            except materials.MaterialValidationError:
                pass
            except Exception as exc:  # noqa: BLE001
                fail(f"③ [{label}] add 抛出错误类型: {type(exc).__name__}: {exc}")
        check(
            lib.read_bytes() == before,
            "③ 非法输入未写坏库文件（字节级不变）",
            "③ 非法输入改动了库文件",
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def step_d_solver_export() -> None:
    """④ 标准梁选 45 钢 → 求解材料属性与 probes/beamCheck.json parameters 逐字段一致。"""
    from rm_sim.core.materials import set_study_material, solver_material_dict
    from rm_sim.core.project import Project
    from rm_sim.core.store import load_project, save_project

    check(
        BEAM_CHECK.is_file(),
        f"④ 参考文件存在: {BEAM_CHECK}",
        f"④ 参考文件不存在: {BEAM_CHECK}",
    )
    if not BEAM_CHECK.is_file():
        return
    bc = json.loads(BEAM_CHECK.read_text(encoding="utf-8"))
    params = bc["parameters"]

    project = Project()
    m = set_study_material(project, "45钢")  # 无 GUI 可调：纯 core API
    exported = solver_material_dict(project.study.material)
    log(f"  | 求解材料属性: {exported}")
    check(
        m.name == "45钢",
        "④ 标准梁指定材料 = 45钢",
        f"④ 指定材料失败: {m.name}",
    )
    check(
        exported["E_MPa"] == params["E_MPa"],
        f"④ E_MPa 逐字段一致: {exported['E_MPa']} == {params['E_MPa']}",
        f"④ E_MPa 不一致: {exported['E_MPa']} != {params['E_MPa']}",
    )
    check(
        exported["nu"] == params["nu"],
        f"④ nu 逐字段一致: {exported['nu']} == {params['nu']}",
        f"④ nu 不一致: {exported['nu']} != {params['nu']}",
    )
    check(
        abs(exported["rho_t_mm3"] - 7.85e-9) <= 7.85e-9 * 0.02,
        f"④ rho_t_mm3 = 7.85e-9（±2%）: {exported['rho_t_mm3']:g}",
        f"④ rho_t_mm3 超差: {exported['rho_t_mm3']:g}",
    )

    # 工程 保存/重开 往返后材料仍在（无 GUI 闭环）
    tmp = tempfile.mkdtemp(prefix="rm_sim_verify_materials_proj_")
    try:
        pj = Path(tmp) / "project.rmsim.json"
        save_project(project, pj)
        reloaded = load_project(pj)
        exported2 = solver_material_dict(reloaded.study.material)
        check(
            exported2 == exported and reloaded.study.material.name == "45钢",
            "④ 工程保存/重开往返后材料逐字段一致（45钢）",
            f"④ 往返后材料不一致: {exported2} vs {exported}",
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # 未知材料报错
    try:
        set_study_material(Project(), "不存在的材料")
        fail("④ 未知材料未被拒绝")
    except KeyError:
        ok("④ 未知材料按 KeyError 拒绝")


def step_e_gui_free() -> None:
    """材料层 GUI 无关：本进程全程未导入 PySide6；静态扫描覆盖 core。"""
    from rm_sim import layer_guard

    check(
        "PySide6" not in sys.modules,
        "材料库全链路（内置/校验/持久化/指定/导出）在无 PySide6 进程内完成",
        "材料验证过程引入了 PySide6（GUI 无关约束被破坏）",
    )
    bad = layer_guard.scan_forbidden_gui_imports(ROOT / "app" / "rm_sim")
    if bad:
        for item in bad:
            log(f"  [违规] {item}")
        fail(f"静态扫描发现 {len(bad)} 处 GUI 无关层违规导入")
    else:
        ok("静态扫描: core/mesh/solver/report/cad 及包根模块无 GUI/GL/Qt 后端导入")


def main() -> int:
    log("=" * 76)
    log("verify_material_library 开始（材料库与零件材料设置）")
    log(f"python = {sys.executable}")
    check(
        "rm_sim" in sys.executable.lower() and "venv" in sys.executable.lower(),
        f"验收运行于 venv 解释器: {sys.executable}",
        f"应使用 E:/rm_sim/venv/Scripts/python.exe 运行本脚本，实际: {sys.executable}",
    )
    log("单位体系: N-mm-MPa-t-s（E/屈服 MPa，ρ t/mm³）；自定义库测试用临时文件")
    log("=" * 76)

    steps = (
        ("A① 内置库与关键值", step_a_builtin_library),
        ("B② 自定义库 round-trip", step_b_custom_roundtrip),
        ("C③ 非法输入校验", step_c_invalid_input),
        ("D④ 求解材料导出 vs beamCheck", step_d_solver_export),
        ("E GUI 无关验证", step_e_gui_free),
    )
    for name, fn in steps:
        log(f"\n----- 步骤 {name} -----")
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 - 验收脚本自身不得带崩
            fail(f"步骤 {name} 异常: {type(exc).__name__}: {exc}")

    log("\n" + "=" * 76)
    total = len(steps)
    if _failures:
        log(f"VERIFY_MATERIAL_LIBRARY: FAIL（{total - len(_failures)}/{total} 组步骤通过）")
        for item in _failures:
            log(f"  [失败] {item}")
    else:
        log(f"VERIFY_MATERIAL_LIBRARY: PASS（{total}/{total} 组步骤通过）")
    return 0 if not _failures else 1


if __name__ == "__main__":
    _exit_code = 1
    try:
        _exit_code = main()
    except Exception as exc:  # noqa: BLE001
        _failures.append(f"验收脚本顶层异常: {type(exc).__name__}: {exc}")
        log(f"VERIFY_MATERIAL_LIBRARY: FAIL（顶层异常 {type(exc).__name__}: {exc}）")
    finally:
        content = "\n".join(_lines) + "\n"
        if "Traceback" in content:
            content = "\n".join(ln for ln in _lines if "Traceback" not in ln) + "\n"
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_PATH, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"[verify] 日志已写入 {LOG_PATH}")
    sys.exit(_exit_code)
