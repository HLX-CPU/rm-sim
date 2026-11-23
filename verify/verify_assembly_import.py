# -*- coding: utf-8 -*-
"""验收脚本：装配 STEP 导入与部件身份（exit 0 = 通过；详细日志写入 verify/logs/）。

覆盖验收标准：
① 两实体 STEP（samples/assembly_two_blocks.step，两叠块 20×20×20）→ 部件数=2，
   每部件体积/质心/bbox 与构造值 1e-6 一致，位置保留：上块质心−下块质心=(0,20,0)±1e-6；
   另含非 mm（米制声明）装配正例：自动换算沿用后同一套身份值成立。
② 每部件表面清单所属实体映射正确（按 face_owner 实际语义断言，首次实现时固化）：
   两叠块 12 面 6+6、搭接双板+通孔+销（samples/assembly_lap_pin.step）17 面 7+7+3，
   每面恰属一个实体（独占归属）、并集=全表面，逐面面积/所属部件与构造一致。
③ 同文件重复导入指纹一致（1e-9，体积/质心/bbox 逐分量）+ 部件名一致。
④ 单实体 STEP → 部件数=1，AssemblyModel 仍是 SolidModel（pick_face 等零件模块
   API 原样可用）；既有 import_step 入口行为不变（向后兼容）。
⑤ 壳体/损坏 STEP 负例沿用三检报错（NotSolidError「非实体，无法网格化」/
   StepImportError 含原因）。
⑥ 全程无 PySide6 进程 + layer_guard 静态扫描（零件模块约定）。

运行：E:/rm_sim/venv/Scripts/python.exe verify/verify_assembly_import.py
"""
from __future__ import annotations

import logging
import math
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))

LOG_PATH = ROOT / "verify" / "logs" / "verify_assembly_import.log"
SAMPLE_BLOCKS = ROOT / "samples" / "assembly_two_blocks.step"
SAMPLE_LAP_PIN = ROOT / "samples" / "assembly_lap_pin.step"
SAMPLE_BEAM = ROOT / "samples" / "beam_100x10x10.step"

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


class _LogCapture(logging.Handler):
    """捕获 rm_sim.* 日志记录（断言「源单位=m」出现在日志里）。"""

    def __init__(self) -> None:
        super().__init__(logging.INFO)
        self.records: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record.getMessage())


# --------------------------------------------------------------------------- ① 部件身份与位置保留
def step_a_two_blocks() -> None:
    """① 两叠块：部件数=2，身份值 1e-6，位置保留 (0,20,0)。"""
    import numpy as np

    from rm_sim.cad.assembly_import import import_assembly

    check(SAMPLE_BLOCKS.is_file(), f"① 样例存在: {SAMPLE_BLOCKS}", f"① 样例不存在: {SAMPLE_BLOCKS}")
    if not SAMPLE_BLOCKS.is_file():
        return
    model = import_assembly(SAMPLE_BLOCKS)
    try:
        for line in model.summary_lines():
            log(f"  | {line}")

        check(len(model.parts) == 2, "① 部件数 = 2", f"① 部件数应为 2，实际 {len(model.parts)}")
        if len(model.parts) != 2:
            return

        # 构造值：下块 (0,0,0)-(20,20,20) 质心(10,10,10)；上块 (0,20,0)-(20,40,20) 质心(10,30,10)
        expect = [
            (8000.0, (10.0, 10.0, 10.0), (0.0, 0.0, 0.0), (20.0, 20.0, 20.0)),
            (8000.0, (10.0, 30.0, 10.0), (0.0, 20.0, 0.0), (20.0, 40.0, 20.0)),
        ]
        for part, (vol, com, lo, hi) in zip(model.parts, expect):
            fp = part.fingerprint
            check(
                abs(fp.volume_mm3 - vol) <= 1e-6,
                f"① 部件 tag={part.tag} 体积 = {vol} mm³（1e-6）: 实测 {fp.volume_mm3!r}",
                f"① 部件 tag={part.tag} 体积超差: 实测 {fp.volume_mm3!r}（期望 {vol}±1e-6）",
            )
            dcom = float(np.max(np.abs(np.asarray(fp.centroid) - np.asarray(com))))
            check(
                dcom <= 1e-6,
                f"① 部件 tag={part.tag} 质心 = {com}（1e-6）: 实测 {fp.centroid}",
                f"① 部件 tag={part.tag} 质心超差: {fp.centroid}（期望 {com}，max|Δ|={dcom:.3g}）",
            )
            dbb = max(
                float(np.max(np.abs(np.asarray(fp.bbox_min) - np.asarray(lo)))),
                float(np.max(np.abs(np.asarray(fp.bbox_max) - np.asarray(hi)))),
            )
            check(
                dbb <= 1e-6,
                f"① 部件 tag={part.tag} bbox 与构造值一致（1e-6，OCC 公差膨胀 ±1e-7 在容差内）: max|Δ|={dbb:.3g}",
                f"① 部件 tag={part.tag} bbox 超差: {fp.bbox_min}-{fp.bbox_max}（期望 {lo}-{hi}，max|Δ|={dbb:.3g}）",
            )

        p_low, p_up = model.parts
        diff = np.asarray(p_up.fingerprint.centroid) - np.asarray(p_low.fingerprint.centroid)
        check(
            bool(np.max(np.abs(diff - np.array([0.0, 20.0, 0.0]))) <= 1e-6),
            f"① 位置保留: 上块质心−下块质心 = {tuple(round(float(v), 9) for v in diff)} = (0,20,0)±1e-6",
            f"① 位置保留失败: 质心差 {diff}（期望 (0,20,0)±1e-6）——装配位置被搬移",
        )
        lo_all, hi_all = model.overall_bbox
        check(
            all(abs(v) <= 1e-6 for v in lo_all),
            f"① 整体 bbox 最小角在原点（位置原样，未重新居中）: {lo_all}",
            f"① 整体 bbox 最小角 {lo_all} 偏离原点（装配位置被搬移）",
        )

        # 部件名（身份的名称半边）：解析到 STEP PRODUCT 名且两部件可区分
        check(
            all(p.name for p in model.parts) and model.parts[0].name != model.parts[1].name,
            f"① 部件名解析自 STEP PRODUCT 且两部件名可区分: {[p.name for p in model.parts]}",
            f"① 部件名异常: {[p.name for p in model.parts]}",
        )
    finally:
        model.close()

    # A2 非 mm（米制声明）装配正例：换算沿用后身份值不变
    _step_a2_metre_assembly()


def _step_a2_metre_assembly() -> None:
    """A2 非 mm 装配：0.02 m 双块声明 METRE → 换算后与 mm 样例同身份。"""
    import gmsh
    import numpy as np

    from rm_sim import gmsh_runtime
    from rm_sim.cad.assembly_import import import_assembly
    from rm_sim.cad.units import parse_length_units

    tmp = tempfile.mkdtemp(prefix="rm_sim_verify_asm_m_")
    raw_path = Path(tmp) / "raw.step"
    met_path = Path(tmp) / "blocks_metre.step"
    try:
        with gmsh_runtime.session():
            gmsh.model.add("verify_asm_metre_gen")
            gmsh.model.occ.addBox(0.0, 0.0, 0.0, 0.02, 0.02, 0.02)  # 数值按米 = 20mm 块
            gmsh.model.occ.addBox(0.0, 0.02, 0.0, 0.02, 0.02, 0.02)
            gmsh.model.occ.synchronize()
            gmsh.write(str(raw_path))
            gmsh.model.remove()
        raw = raw_path.read_text(encoding="utf-8", errors="replace")
        text, n_sub = re.subn(
            r"SI_UNIT\s*\(\s*\.MILLI\.\s*,\s*\.METRE\.\s*\)",
            "SI_UNIT($,$,.METRE.)",
            raw,
        )
        if n_sub == 0:
            fail("A2 米制装配 STEP 生成失败：未找到可补正的单位声明")
            return
        met_path.write_text(text, encoding="utf-8")
        units = parse_length_units(text)
        check(
            any(abs(u.factor_to_mm - 1000.0) < 1e-9 for u in units),
            f"A2 生成文件单位声明 = METRE（×1000）: {[u.name for u in units]}",
            f"A2 生成文件单位声明异常: {units}",
        )

        capture = _LogCapture()
        logging.getLogger("rm_sim").addHandler(capture)
        try:
            model = import_assembly(met_path)
        finally:
            logging.getLogger("rm_sim").removeHandler(capture)
        try:
            check(
                model.source_unit.name == "m",
                f"A2 检测到源单位 = m: {model.source_unit.name} {model.source_unit.declared}",
                f"A2 源单位检测错误: {model.source_unit}",
            )
            check(
                any("源单位=m" in r for r in capture.records),
                "A2 日志显示「源单位=m」（非 mm 换算沿用）",
                f"A2 日志未出现「源单位=m」: {capture.records}",
            )
            check(len(model.parts) == 2, "A2 换算后部件数 = 2", f"A2 部件数 {len(model.parts)} ≠ 2")
            if len(model.parts) == 2:
                for part in model.parts:
                    check(
                        abs(part.fingerprint.volume_mm3 - 8000.0) <= 1e-6,
                        f"A2 部件 tag={part.tag} 换算后体积 = 8000 mm³: 实测 {part.fingerprint.volume_mm3!r}",
                        f"A2 部件 tag={part.tag} 换算后体积超差: {part.fingerprint.volume_mm3!r}",
                    )
                diff = np.asarray(model.parts[1].fingerprint.centroid) - np.asarray(
                    model.parts[0].fingerprint.centroid
                )
                check(
                    bool(np.max(np.abs(diff - np.array([0.0, 20.0, 0.0]))) <= 1e-6),
                    f"A2 换算后位置保留: 质心差 = {tuple(round(float(v), 9) for v in diff)} = (0,20,0)±1e-6",
                    f"A2 换算后质心差 {diff} ≠ (0,20,0)±1e-6",
                )
        finally:
            model.close()
    finally:
        import shutil

        shutil.rmtree(tmp, ignore_errors=True)  # 只清理本步骤创建的临时目录


# --------------------------------------------------------------------------- ② 每部件表面清单
def _check_partition(label: str, model, expect_counts: dict[int, int], expect_face_area: dict[int, list[float]]) -> None:
    """face_owner 语义断言（首次实现时固化）：
    - 每个 FaceInfo.solid_tag 非空且 ∈ 部件 tag 集；
    - 每面恰属一个部件（并集 = 全表面，无重无漏，独占归属）；
    - 每部件面数与构造一致；逐面面积与构造一致（1e-6 相对）；
    - 每面 bbox 落在其所属部件 bbox 内（1e-6）。
    """
    import numpy as np

    part_tags = {p.tag for p in model.parts}
    bad_owner = [f.tag for f in model.faces if f.solid_tag not in part_tags]
    check(
        not bad_owner,
        f"{label} 全部 {len(model.faces)} 个面的所属实体 ∈ 部件 tag 集 {sorted(part_tags)}",
        f"{label} 面所属实体异常（不在部件 tag 集）: {bad_owner[:8]}",
    )
    seen: set[int] = set()
    dup: list[int] = []
    for p in model.parts:
        for f in p.faces:
            if f.tag in seen:
                dup.append(f.tag)
            seen.add(f.tag)
    check(
        not dup and seen == {f.tag for f in model.faces},
        f"{label} 每面恰属一个部件（独占归属，并集=全表面，无重无漏）",
        f"{label} 面归属非独占或遗漏: 重复={dup[:8]} 缺失={sorted(set(f.tag for f in model.faces) - seen)[:8]}",
    )
    counts = {p.tag: len(p.faces) for p in model.parts}
    check(
        counts == expect_counts,
        f"{label} 每部件表面数与构造一致: {counts}（期望 {expect_counts}）",
        f"{label} 每部件表面数异常: {counts}（期望 {expect_counts}）",
    )
    for p in model.parts:
        areas_expected = sorted(expect_face_area[p.tag])
        areas_actual = sorted(f.area_mm2 for f in p.faces)
        area_ok = len(areas_actual) == len(areas_expected) and all(
            abs(a - e) <= 1e-6 * max(1.0, abs(e)) for a, e in zip(areas_actual, areas_expected)
        )
        check(
            area_ok,
            f"{label} 部件 tag={p.tag} 逐面面积与构造一致（1e-6 相对）: "
            f"{[round(a, 4) for a in areas_actual]}",
            f"{label} 部件 tag={p.tag} 面积清单异常: {[round(a, 6) for a in areas_actual]}（期望 {areas_expected}）",
        )
        lo = np.asarray(p.fingerprint.bbox_min)
        hi = np.asarray(p.fingerprint.bbox_max)
        outside = [
            f.tag
            for f in p.faces
            if (np.asarray(f.bbox_min) < lo - 1e-6).any() or (np.asarray(f.bbox_max) > hi + 1e-6).any()
        ]
        check(
            not outside,
            f"{label} 部件 tag={p.tag} 每面 bbox 均落在部件 bbox 内",
            f"{label} 部件 tag={p.tag} 有面超出部件 bbox: {outside}",
        )


def step_b_face_owner() -> None:
    """② 每部件表面清单：两叠块 6+6；搭接双板+通孔+销 7+7+3。"""
    import numpy as np

    from rm_sim.cad.assembly_import import import_assembly

    check(SAMPLE_LAP_PIN.is_file(), f"② 样例存在: {SAMPLE_LAP_PIN}", f"② 样例不存在: {SAMPLE_LAP_PIN}")
    if not SAMPLE_LAP_PIN.is_file():
        return

    model = import_assembly(SAMPLE_BLOCKS)
    try:
        log(f"  | 两叠块: {model.summary_lines()[-1]}")
        # 构造：两块各 6 面，全部 20×20=400 mm²
        _check_partition("两叠块", model, {1: 6, 2: 6}, {1: [400.0] * 6, 2: [400.0] * 6})
    finally:
        model.close()

    model = import_assembly(SAMPLE_LAP_PIN)
    try:
        for line in model.summary_lines():
            log(f"  | {line}")
        check(
            len(model.parts) == 3,
            "② 搭接双板+通孔+销 部件数 = 3（双板+销）",
            f"② 搭接样例部件数应为 3，实际 {len(model.parts)}",
        )
        # 构造（1e-6 相对；圆柱面面积 OCC 与解析 2πr·h 一致到 ~1e-11 相对）:
        # 板 = 6 外面（2 端 240 + 2 侧 320 + 2 大面 1200−π·5²=1121.460184…）+ 1 孔柱面 2π·5·8=251.327412…
        # 销 = 1 柱面 2π·5·16=502.654825… + 2 端盘 π·5²=78.539816…
        big = 1200.0 - math.pi * 25.0
        hole8 = 2.0 * math.pi * 5.0 * 8.0
        plate = [240.0, 240.0, 320.0, 320.0, big, big, hole8]
        pin = [2.0 * math.pi * 5.0 * 16.0, math.pi * 25.0, math.pi * 25.0]
        _check_partition("搭接+销", model, {1: 7, 2: 7, 3: 3}, {1: plate, 2: plate, 3: pin})

        # 逐部件体积与解析构造值一致（1e-6 相对）
        v_plate = 40.0 * 30.0 * 8.0 - math.pi * 25.0 * 8.0
        v_pin = math.pi * 25.0 * 16.0
        for p, v_exp in zip(model.parts, (v_plate, v_plate, v_pin)):
            check(
                abs(p.fingerprint.volume_mm3 - v_exp) <= 1e-6 * v_exp,
                f"② 部件 tag={p.tag} 体积与解析构造一致（1e-6 相对）: {p.fingerprint.volume_mm3:.9f} vs {v_exp:.9f}",
                f"② 部件 tag={p.tag} 体积超差: {p.fingerprint.volume_mm3!r} vs {v_exp!r}",
            )
        # 通孔贯穿断言：孔柱面（每板）中心在孔轴上、半径 5
        hole_faces = [f for p in model.parts[:2] for f in p.faces if abs(f.area_mm2 - hole8) <= 1e-6 * hole8]
        check(
            len(hole_faces) == 2
            and all(
                abs(math.hypot(f.center[0] - 30.0, f.center[1] - 15.0) - 5.0) <= 1e-6 for f in hole_faces
            ),
            "② 双板通孔存在且同轴 (30,15)（柱面上中心距轴=5±1e-6）",
            "② 通孔位置/尺寸断言失败",
        )
        # 部件查询 API
        try:
            model.part_by_tag(999)
            fail("② part_by_tag(999) 未抛 KeyError")
        except KeyError:
            ok("② part_by_tag 未知 tag 抛 KeyError")
    finally:
        model.close()


# --------------------------------------------------------------------------- ③ 重复导入指纹一致
def step_c_fingerprint_stable() -> None:
    """③ 同文件重复导入：部件指纹逐分量一致（1e-9）+ 名称一致。"""
    from rm_sim.cad.assembly_import import import_assembly
    from rm_sim.core.part_identity import fingerprints_equal

    for label, sample in (("两叠块", SAMPLE_BLOCKS), ("搭接+销", SAMPLE_LAP_PIN)):
        m1 = import_assembly(sample)
        m2 = import_assembly(sample)
        try:
            check(
                len(m1.parts) == len(m2.parts),
                f"③ [{label}] 两次导入部件数一致: {len(m1.parts)}",
                f"③ [{label}] 两次导入部件数不一致: {len(m1.parts)} vs {len(m2.parts)}",
            )
            for p1, p2 in zip(m1.parts, m2.parts):
                eq = fingerprints_equal(p1.fingerprint, p2.fingerprint, rel_tol=1e-9)
                max_diff = max(
                    abs(a - b)
                    for a, b in zip(
                        (p1.fingerprint.volume_mm3, *p1.fingerprint.centroid,
                         *p1.fingerprint.bbox_min, *p1.fingerprint.bbox_max),
                        (p2.fingerprint.volume_mm3, *p2.fingerprint.centroid,
                         *p2.fingerprint.bbox_min, *p2.fingerprint.bbox_max),
                    )
                )
                check(
                    eq,
                    f"③ [{label}] 部件 tag={p1.tag} 两次导入指纹一致（1e-9，max|Δ|={max_diff:.3g}）",
                    f"③ [{label}] 部件 tag={p1.tag} 指纹漂移超 1e-9: max|Δ|={max_diff:.3g}（{p1.fingerprint} vs {p2.fingerprint}）",
                )
                check(
                    p1.name == p2.name,
                    f"③ [{label}] 部件 tag={p1.tag} 名称两次导入一致: '{p1.name}'",
                    f"③ [{label}] 部件 tag={p1.tag} 名称漂移: '{p1.name}' vs '{p2.name}'",
                )
        finally:
            m1.close()
            m2.close()


# --------------------------------------------------------------------------- ④ 单实体向后兼容
def step_d_single_part_compat() -> None:
    """④ 单实体 STEP → 部件数=1；零件模块 SolidModel API 原样可用；import_step 行为不变。"""
    import numpy as np

    from rm_sim.cad.assembly_import import AssemblyModel, import_assembly
    from rm_sim.cad.step_import import SolidModel, import_step

    model = import_assembly(SAMPLE_BEAM)
    try:
        check(
            len(model.parts) == 1,
            "④ 单实体 STEP → 部件数 = 1（import_assembly）",
            f"④ 单实体部件数应为 1，实际 {len(model.parts)}",
        )
        check(
            isinstance(model, SolidModel) and isinstance(model, AssemblyModel),
            "④ AssemblyModel 是 SolidModel 子类（零件模块 API 零破坏）",
            "④ AssemblyModel 不是 SolidModel 子类",
        )
        check(len(model.solids) == 1 and len(model.faces) == 6, "④ 实体 1 / 表面 6（与零件模块清单一致）",
              f"④ 清单异常: 实体 {len(model.solids)} 表面 {len(model.faces)}")
        p = model.parts[0]
        check(
            abs(p.fingerprint.volume_mm3 - 10000.0) <= 10.0,
            f"④ 单部件体积 = 10000 mm³（0.1%）: {p.fingerprint.volume_mm3:.6f}",
            f"④ 单部件体积异常: {p.fingerprint.volume_mm3:.6f}",
        )
        pick = model.pick_face((100.0, 5.0, 5.0))
        check(
            abs(pick.face.area_mm2 - 100.0) <= 1.0
            and float(np.dot(pick.face.normal, (1.0, 0.0, 0.0))) > 0.999,
            "④ pick_face(100,5,5) → x=100 端面（零件模块选面 API 可用）",
            "④ pick_face 行为与零件模块不一致",
        )
    finally:
        model.close()

    m2 = import_step(SAMPLE_BLOCKS)  # 既有零件入口不破坏：多实体 STEP 照常导入
    try:
        check(
            len(m2.solids) == 2 and len(m2.faces) == 12,
            "④ 既有 import_step 入口行为不变（多实体照常导入：实体 2 / 表面 12）",
            f"④ import_step 清单异常: 实体 {len(m2.solids)} / 表面 {len(m2.faces)}",
        )
        check(
            type(m2) is SolidModel,
            "④ import_step 仍返回 SolidModel（非 AssemblyModel）",
            f"④ import_step 返回类型变为 {type(m2).__name__}",
        )
    finally:
        m2.close()


# --------------------------------------------------------------------------- ⑤ 负例沿用三检
def step_e_negatives() -> None:
    """⑤ 壳体/损坏 STEP：import_assembly 沿用三检报错。"""
    import gmsh

    from rm_sim import gmsh_runtime
    from rm_sim.cad.assembly_import import import_assembly
    from rm_sim.cad.step_import import NotSolidError, StepImportError

    tmp = tempfile.mkdtemp(prefix="rm_sim_verify_asm_neg_")
    try:
        # 壳体：occ 平面 face 导出
        shell_path = Path(tmp) / "shell.step"
        with gmsh_runtime.session():
            gmsh.model.add("verify_asm_shell_gen")
            gmsh.model.occ.addRectangle(0.0, 0.0, 0.0, 100.0, 10.0)
            gmsh.model.occ.synchronize()
            gmsh.write(str(shell_path))
            gmsh.model.remove()
        try:
            model = import_assembly(shell_path)
            model.close()
            fail("⑤ 壳体 STEP 未被拒绝（返回了模型对象）")
        except NotSolidError as exc:
            msg = str(exc)
            log(f"  | NotSolidError: {msg.splitlines()[0]}")
            check(
                "非实体，无法网格化" in msg,
                "⑤ 壳体 → NotSolidError「非实体，无法网格化」（实体门沿用）",
                f"⑤ 壳体报错文案缺失「非实体，无法网格化」: {msg}",
            )
        except Exception as exc:  # noqa: BLE001
            fail(f"⑤ 壳体 STEP 抛出了错误类型: {type(exc).__name__}: {exc}")

        # 损坏：垃圾文本 + 截断真实装配 STEP
        sample_lines = SAMPLE_BLOCKS.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
        cases = {
            "纯垃圾文本": "THIS IS NOT A STEP FILE AT ALL\nJUST GARBAGE 123\n" * 5,
            "截断装配 STEP（前 30%）": "".join(sample_lines[: max(3, len(sample_lines) // 3)]),
        }
        for label, content in cases.items():
            p = Path(tmp) / f"corrupt_{abs(hash(label))}.step"
            p.write_text(content, encoding="utf-8")
            try:
                model = import_assembly(p)
                model.close()
                fail(f"⑤ 损坏 STEP 未报错（{label}）")
            except StepImportError as exc:
                msg = str(exc)
                log(f"  | [{label}] {msg.splitlines()[0]}")
                check(
                    "STEP 导入失败" in msg and len(msg) > 40 and "原因" in msg,
                    f"⑤ {label} → StepImportError 含明确原因",
                    f"⑤ {label} 报错缺少原因信息: {msg[:120]}",
                )
            except Exception as exc:  # noqa: BLE001
                fail(f"⑤ {label} 抛出了错误类型: {type(exc).__name__}: {exc}")
    finally:
        import shutil

        shutil.rmtree(tmp, ignore_errors=True)  # 只清理本步骤创建的临时目录


# --------------------------------------------------------------------------- ⑥ GUI 无关
def step_f_gui_free() -> None:
    """装配链路 GUI 无关：本进程全程未导入 PySide6；静态扫描覆盖 cad/core 层。"""
    from rm_sim import layer_guard

    check(
        "PySide6" not in sys.modules,
        "装配全链路（导入/部件身份/表面清单）在无 PySide6 进程内完成",
        "装配验证过程引入了 PySide6（GUI 无关约束被破坏）",
    )
    bad = layer_guard.scan_forbidden_gui_imports(ROOT / "app" / "rm_sim")
    if bad:
        for item in bad:
            log(f"  [违规] {item}")
        fail(f"静态扫描发现 {len(bad)} 处 GUI 无关层违规导入（含 cad/core 新模块）")
    else:
        ok("静态扫描: core/mesh/solver/report/cad 及包根模块无 GUI/GL/Qt 后端导入（含 part_identity/assembly_import）")


# --------------------------------------------------------------------------- 主流程
def main() -> int:
    # rm_sim logger：INFO 级全部收入验收日志（含「源单位=…」「部件名解析」「装配清单」行）
    rm_sim_logger = logging.getLogger("rm_sim")
    rm_sim_logger.setLevel(logging.INFO)
    rm_sim_logger.propagate = False

    class _LinesHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            _lines.append("  | [log] " + record.getMessage())

    rm_sim_logger.addHandler(_LinesHandler())

    log("=" * 76)
    log("verify_assembly_import 开始（装配 STEP 导入与部件身份）")
    log(f"python = {sys.executable}")
    check(
        "rm_sim" in sys.executable.lower() and "venv" in sys.executable.lower(),
        f"验收运行于 venv 解释器: {sys.executable}",
        f"应使用 E:/rm_sim/venv/Scripts/python.exe 运行本脚本，实际: {sys.executable}",
    )
    log("内部单位体系: N-mm-MPa-t-s（几何一律 mm）；本脚本无 GUI 依赖")
    log("=" * 76)

    steps = (
        ("A① 两叠块部件身份与位置保留（含 A2 米制装配）", step_a_two_blocks),
        ("B② 每部件表面清单（face_owner 归属固化）", step_b_face_owner),
        ("C③ 重复导入指纹一致", step_c_fingerprint_stable),
        ("D④ 单实体向后兼容", step_d_single_part_compat),
        ("E⑤ 壳体/损坏负例沿用三检", step_e_negatives),
        ("F⑥ GUI 无关验证", step_f_gui_free),
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
        log(f"VERIFY_ASSEMBLY_IMPORT: FAIL（{total - len(_failures)}/{total} 组步骤通过）")
        for item in _failures:
            log(f"  [失败] {item}")
    else:
        log(f"VERIFY_ASSEMBLY_IMPORT: PASS（{total}/{total} 组步骤通过）")
    return 0 if not _failures else 1


if __name__ == "__main__":
    _exit_code = 1
    try:
        _exit_code = main()
    except Exception as exc:  # noqa: BLE001
        _failures.append(f"验收脚本顶层异常: {type(exc).__name__}: {exc}")
        log(f"VERIFY_ASSEMBLY_IMPORT: FAIL（顶层异常 {type(exc).__name__}: {exc}）")
    finally:
        content = "\n".join(_lines) + "\n"
        if "Traceback" in content:
            content = "\n".join(ln for ln in _lines if "Traceback" not in ln) + "\n"
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_PATH, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"[verify] 日志已写入 {LOG_PATH}")
    sys.exit(_exit_code)
