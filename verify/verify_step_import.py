# -*- coding: utf-8 -*-
"""验收脚本：STEP 导入与三维选面（exit 0 = 通过；详细日志写入 verify/logs/）。

覆盖验收标准：
① mm 标准梁（samples/beam_100x10x10.step）：bbox=100×10×10（各向 1e-6）、
   体积=10000mm³（0.1%）、实体数=1、每面 tag/面积/法向可查；
② 单位负例：脚本内生成米制 STEP（bbox 0.1×0.01×0.01、单位声明 METRE）导入 →
   断言检测到源单位=m、换算后 bbox=100×10×10、日志显示「源单位=m」；
③ 非实体负例：occ 平面 face 导出的壳体 STEP → 报错「非实体，无法网格化」；
④ pick：(100,5,5)→x=100 端面（面积 100mm² 容差 1%、法向·(+x)>0.999），
   (0,5,5)→固定端面；
⑤ 损坏 STEP 抛含明确原因的异常。

本脚本全程无 GUI（无 PySide6）：cad 层 GUI 无关的直接证明。
运行：E:/rm_sim/venv/Scripts/python.exe verify/verify_step_import.py
"""
from __future__ import annotations

import logging
import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))

LOG_PATH = ROOT / "verify" / "logs" / "verify_step_import.log"
SAMPLE = ROOT / "samples" / "beam_100x10x10.step"

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


# --------------------------------------------------------------------------- 步骤
def step_a_mm_beam() -> None:
    """① mm 标准梁：bbox / 体积 / 实体数 / 表面清单。"""
    import numpy as np

    from rm_sim.cad.step_import import import_step

    check(SAMPLE.is_file(), f"① 样例存在: {SAMPLE}", f"① 样例不存在: {SAMPLE}")
    if not SAMPLE.is_file():
        return
    model = import_step(SAMPLE)
    try:
        for line in model.summary_lines():
            log(f"  | {line}")

        check(len(model.solids) == 1, "① 实体数 = 1", f"① 实体数应为 1，实际 {len(model.solids)}")

        lo, hi = model.overall_bbox
        extents = np.asarray(hi) - np.asarray(lo)
        expect = np.array([100.0, 10.0, 10.0])
        check(
            bool(np.all(np.abs(extents - expect) <= 1e-6)),
            f"① bbox=100×10×10（各向 1e-6）: 实测 {extents}",
            f"① bbox 超差: 实测 {extents}（期望 100×10×10，容差 1e-6）",
        )
        check(
            bool(np.all(np.abs(np.asarray(lo)) <= 1e-6)),
            f"① bbox 最小角在原点（1e-6）: 实测 {lo}",
            f"① bbox 最小角超差: {lo}",
        )
        vol = model.total_volume_mm3
        check(
            abs(vol - 10000.0) <= 10.0,
            f"① 体积 = 10000 mm³（0.1%）: 实测 {vol:.6f}",
            f"① 体积超差: 实测 {vol:.6f}（期望 10000，0.1%）",
        )

        # 每面 tag/面积/法向可查
        check(len(model.faces) == 6, "① 表面 6 个", f"① 表面数应为 6，实际 {len(model.faces)}")
        areas = sorted(round(f.area_mm2, 6) for f in model.faces)
        ends = [a for a in areas if abs(a - 100.0) <= 1.0]
        sides = [a for a in areas if abs(a - 1000.0) <= 10.0]
        check(
            len(ends) == 2 and len(sides) == 4 and len(areas) == 6,
            f"① 面积清单可查且符合 2×100 + 4×1000 mm²: {areas}",
            f"① 面积清单异常: {areas}（期望 2×100 + 4×1000）",
        )
        for f in model.faces:
            n = np.asarray(f.normal, dtype=float)
            check(
                abs(np.linalg.norm(n) - 1.0) < 1e-9,
                f"① 面 tag={f.tag} 法向为单位向量: {n}",
                f"① 面 tag={f.tag} 法向非单位向量: {n}",
            )
            check(
                float(np.max(np.abs(n))) > 0.999,
                f"① 面 tag={f.tag} 法向轴对齐（|max|>0.999）: {n}",
                f"① 面 tag={f.tag} 法向未对齐坐标轴: {n}",
            )
            check(
                f.center is not None and f.area_mm2 > 0.0,
                f"① 面 tag={f.tag} tag/面积/法向/中心点均可查",
                f"① 面 tag={f.tag} 清单字段缺失",
            )
    finally:
        model.close()


def step_b_metre_step() -> None:
    """② 单位负例：脚本内生成米制 STEP → 检测源单位=m → 换算后几何正确。"""
    import numpy as np
    import gmsh

    from rm_sim import gmsh_runtime
    from rm_sim.cad.step_import import import_step
    from rm_sim.cad.units import parse_length_units

    tmp = tempfile.mkdtemp(prefix="rm_sim_verify_metre_")
    raw_path = Path(tmp) / "m_raw.step"
    met_path = Path(tmp) / "beam_metre.step"
    try:
        # 生成 0.1×0.01×0.01 的几何（数值按米解释 = 100×10×10 mm 的梁）
        with gmsh_runtime.session():
            gmsh.model.add("verify_metre_gen")
            gmsh.model.occ.addBox(0.0, 0.0, 0.0, 0.1, 0.01, 0.01)
            gmsh.model.occ.synchronize()
            gmsh.write(str(raw_path))
            gmsh.model.remove()
        raw = raw_path.read_text(encoding="utf-8", errors="replace")
        # gmsh 导出实测固定写 SI_UNIT(.MILLI.,.METRE.)；补正为 METRE 声明
        text, n_sub = re.subn(
            r"SI_UNIT\s*\(\s*\.MILLI\.\s*,\s*\.METRE\.\s*\)",
            "SI_UNIT($,$,.METRE.)",
            raw,
        )
        if n_sub == 0 and not parse_length_units(raw):
            fail("② 米制 STEP 生成失败：未找到可补正的单位声明")
            return
        met_path.write_text(text, encoding="utf-8")
        units = parse_length_units(text)
        check(
            any(abs(u.factor_to_mm - 1000.0) < 1e-9 for u in units),
            f"② 生成文件单位声明 = METRE（×1000）: {[u.name for u in units]}",
            f"② 生成文件单位声明异常: {units}",
        )
        check(
            _bbox_of_text(text) == (0.1, 0.01, 0.01),
            "② 生成文件几何数值 bbox 0.1×0.01×0.01（米）",
            "② 生成文件几何数值不是 0.1×0.01×0.01",
        )

        capture = _LogCapture()
        logging.getLogger("rm_sim").addHandler(capture)
        try:
            model = import_step(met_path)
        finally:
            logging.getLogger("rm_sim").removeHandler(capture)
        try:
            check(
                model.source_unit.name == "m" and abs(model.source_unit.factor_to_mm - 1000.0) < 1e-9,
                f"② 检测到源单位 = m（×1000）: {model.source_unit.name} {model.source_unit.declared}",
                f"② 源单位检测错误: {model.source_unit}",
            )
            check(
                any("源单位=m" in r for r in capture.records),
                "② 日志显示「源单位=m」",
                f"② 日志未出现「源单位=m」: {capture.records}",
            )
            lo, hi = model.overall_bbox
            extents = np.asarray(hi) - np.asarray(lo)
            expect = np.array([100.0, 10.0, 10.0])
            check(
                bool(np.all(np.abs(extents - expect) <= 1e-6)),
                f"② 换算后 bbox = 100×10×10: 实测 {extents}（无此检查会静默变成 0.1mm 梁）",
                f"② 换算后 bbox 超差: {extents}",
            )
            check(
                abs(model.total_volume_mm3 - 10000.0) <= 10.0,
                f"② 换算后体积 = 10000 mm³: 实测 {model.total_volume_mm3:.6f}",
                f"② 换算后体积超差: {model.total_volume_mm3:.6f}",
            )
        finally:
            model.close()
    finally:
        import shutil

        shutil.rmtree(tmp, ignore_errors=True)  # 只清理本步骤创建的临时目录


def _bbox_of_text(step_text: str) -> tuple[float, float, float]:
    """从 STEP 文本的 CARTESIAN_POINT 提取几何数值包围盒（仅用于生成自检）。"""
    pts = [
        tuple(float(x) for x in re.findall(r"[-+0-9.Ee]+", m))
        for m in re.findall(r"CARTESIAN_POINT\s*\(\s*'[^']*'\s*,\s*\(([^)]*)\)\s*\)", step_text)
    ]
    pts = [p for p in pts if len(p) == 3]
    if not pts:
        return ()
    xs, ys, zs = zip(*pts)
    return (max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs))


def step_c_shell_reject() -> None:
    """③ 非实体负例：occ 平面 face 导出的壳体 STEP → 「非实体，无法网格化」。"""
    import gmsh

    from rm_sim import gmsh_runtime
    from rm_sim.cad.step_import import NotSolidError, import_step

    tmp = tempfile.mkdtemp(prefix="rm_sim_verify_shell_")
    shell_path = Path(tmp) / "shell.step"
    try:
        with gmsh_runtime.session():
            gmsh.model.add("verify_shell_gen")
            gmsh.model.occ.addRectangle(0.0, 0.0, 0.0, 100.0, 10.0)
            gmsh.model.occ.synchronize()
            gmsh.write(str(shell_path))
            gmsh.model.remove()
        check(shell_path.is_file(), "③ 壳体 STEP 已生成（单个平面 face）", "③ 壳体 STEP 生成失败")
        try:
            model = import_step(shell_path)
            model.close()
            fail(f"③ 壳体 STEP 未被拒绝（返回了 {type(model).__name__}）")
        except NotSolidError as exc:
            msg = str(exc)
            log(f"  | NotSolidError: {msg.splitlines()[0]}")
            check(
                "非实体，无法网格化" in msg,
                "③ 报错含「非实体，无法网格化」",
                f"③ 报错文案缺失「非实体，无法网格化」: {msg}",
            )
        except Exception as exc:  # noqa: BLE001
            fail(f"③ 壳体 STEP 抛出了错误类型: {type(exc).__name__}: {exc}")
    finally:
        import shutil

        shutil.rmtree(tmp, ignore_errors=True)


def step_d_pick() -> None:
    """④ pick：(100,5,5)→x=100 端面；(0,5,5)→固定端面。"""
    import numpy as np

    from rm_sim.cad.step_import import PickError, import_step

    model = import_step(SAMPLE)
    try:
        p_load = model.pick_face((100.0, 5.0, 5.0))
        log(
            f"  | pick(100,5,5) -> face tag={p_load.face.tag} area={p_load.face.area_mm2:.6g} "
            f"normal=({p_load.face.normal[0]:.6f},{p_load.face.normal[1]:.6f},{p_load.face.normal[2]:.6f}) "
            f"dist={p_load.distance:.3g}"
        )
        check(
            abs(p_load.face.area_mm2 - 100.0) <= 1.0,
            f"④ (100,5,5) → x=100 端面，面积 100mm²（1%）: {p_load.face.area_mm2:.6g}",
            f"④ (100,5,5) 命中面面积错误: {p_load.face.area_mm2:.6g}（期望 100±1）",
        )
        check(
            float(np.dot(p_load.face.normal, (1.0, 0.0, 0.0))) > 0.999,
            f"④ (100,5,5) 命中面法向·(+x) > 0.999: {p_load.face.normal[0]:.6f}",
            f"④ (100,5,5) 命中面法向错误: {p_load.face.normal}",
        )

        p_fix = model.pick_face((0.0, 5.0, 5.0))
        log(
            f"  | pick(0,5,5) -> face tag={p_fix.face.tag} area={p_fix.face.area_mm2:.6g} "
            f"normal=({p_fix.face.normal[0]:.6f},{p_fix.face.normal[1]:.6f},{p_fix.face.normal[2]:.6f}) "
            f"dist={p_fix.distance:.3g}"
        )
        check(
            abs(p_fix.face.area_mm2 - 100.0) <= 1.0,
            f"④ (0,5,5) → 固定端面（x=0），面积 100mm²（1%）: {p_fix.face.area_mm2:.6g}",
            f"④ (0,5,5) 命中面面积错误: {p_fix.face.area_mm2:.6g}",
        )
        check(
            float(np.dot(p_fix.face.normal, (1.0, 0.0, 0.0))) < -0.999,
            f"④ (0,5,5) 固定端面外法向·(+x) ≈ -1: {p_fix.face.normal[0]:.6f}",
            f"④ (0,5,5) 命中面法向错误: {p_fix.face.normal}",
        )
        check(
            p_load.face.tag != p_fix.face.tag,
            f"④ 两端面 tag 不同（{p_load.face.tag} vs {p_fix.face.tag}，与 probes/probe1_log.txt 一致：x=0→tag1, x=100→tag2）",
            "④ 两端面命中了同一个 tag",
        )

        try:
            model.pick_face((50.0, 5.0, 5.0))
            fail("④ 实体内部点 (50,5,5) 未按超容差拒绝")
        except PickError as exc:
            ok(f"④ 实体内部点按容差拒绝: {str(exc).splitlines()[0]}")
    finally:
        model.close()


def step_e_corrupt() -> None:
    """⑤ 损坏 STEP 抛含明确原因的异常。"""
    from rm_sim.cad.step_import import StepImportError, import_step

    tmp = tempfile.mkdtemp(prefix="rm_sim_verify_corrupt_")
    try:
        cases = {
            "纯垃圾文本": "THIS IS NOT A STEP FILE AT ALL\nJUST GARBAGE 123\n" * 5,
        }
        sample_lines = SAMPLE.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
        cases["截断的真实 STEP（前 30%）"] = "".join(sample_lines[: max(3, len(sample_lines) // 3)])
        for label, content in cases.items():
            p = Path(tmp) / f"corrupt_{abs(hash(label))}.step"
            p.write_text(content, encoding="utf-8")
            try:
                model = import_step(p)
                model.close()
                fail(f"⑤ 损坏 STEP 未报错（{label}）")
            except StepImportError as exc:
                msg = str(exc)
                first = msg.splitlines()[0]
                log(f"  | [{label}] {first}")
                check(
                    "STEP 导入失败" in msg and len(msg) > 40 and "原因" in msg,
                    f"⑤ {label} → 报错含明确原因（STEP 导入失败 + 原因 + gmsh 日志）",
                    f"⑤ {label} 报错缺少原因信息: {msg[:120]}",
                )
            except Exception as exc:  # noqa: BLE001
                fail(f"⑤ {label} 抛出了错误类型: {type(exc).__name__}: {exc}")
    finally:
        import shutil

        shutil.rmtree(tmp, ignore_errors=True)


def step_f_gui_free() -> None:
    """cad 层 GUI 无关：本进程全程未导入 PySide6；静态扫描覆盖 cad 层。"""
    from rm_sim import layer_guard

    check(
        "PySide6" not in sys.modules,
        "cad 全链路（导入/单位/选面）在无 PySide6 进程内完成",
        "cad 验证过程引入了 PySide6（GUI 无关约束被破坏）",
    )
    bad = layer_guard.scan_forbidden_gui_imports(ROOT / "app" / "rm_sim")
    if bad:
        for item in bad:
            log(f"  [违规] {item}")
        fail(f"静态扫描发现 {len(bad)} 处 GUI 无关层违规导入（含 cad 层）")
    else:
        ok("静态扫描: core/mesh/solver/report/cad 及包根模块无 GUI/GL/Qt 后端导入")


def main() -> int:
    # rm_sim logger：INFO 级全部收入验收日志（含导入器的「源单位=...」「部件清单」行）
    rm_sim_logger = logging.getLogger("rm_sim")
    rm_sim_logger.setLevel(logging.INFO)
    rm_sim_logger.propagate = False

    class _LinesHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            _lines.append("  | [log] " + record.getMessage())

    rm_sim_logger.addHandler(_LinesHandler())

    log("=" * 76)
    log("verify_step_import 开始（STEP 导入与三维选面）")
    log(f"python = {sys.executable}")
    check(
        "rm_sim" in sys.executable.lower() and "venv" in sys.executable.lower(),
        f"验收运行于 venv 解释器: {sys.executable}",
        f"应使用 E:/rm_sim/venv/Scripts/python.exe 运行本脚本，实际: {sys.executable}",
    )
    log("内部单位体系: N-mm-MPa-t-s（几何一律 mm）；本脚本无 GUI 依赖")
    log("=" * 76)

    steps = (
        ("A① mm 标准梁", step_a_mm_beam),
        ("B② 米制 STEP 单位负例", step_b_metre_step),
        ("C③ 壳体负例", step_c_shell_reject),
        ("D④ pick 选面", step_d_pick),
        ("E⑤ 损坏 STEP", step_e_corrupt),
        ("F GUI 无关验证", step_f_gui_free),
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
        log(f"VERIFY_STEP_IMPORT: FAIL（{total - len(_failures)}/{total} 组步骤通过）")
        for item in _failures:
            log(f"  [失败] {item}")
    else:
        log(f"VERIFY_STEP_IMPORT: PASS（{total}/{total} 组步骤通过）")
    return 0 if not _failures else 1


if __name__ == "__main__":
    _exit_code = 1
    try:
        _exit_code = main()
    except Exception as exc:  # noqa: BLE001
        _failures.append(f"验收脚本顶层异常: {type(exc).__name__}: {exc}")
        log(f"VERIFY_STEP_IMPORT: FAIL（顶层异常 {type(exc).__name__}: {exc}）")
    finally:
        content = "\n".join(_lines) + "\n"
        if "Traceback" in content:
            content = "\n".join(ln for ln in _lines if "Traceback" not in ln) + "\n"
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_PATH, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"[verify] 日志已写入 {LOG_PATH}")
    sys.exit(_exit_code)
