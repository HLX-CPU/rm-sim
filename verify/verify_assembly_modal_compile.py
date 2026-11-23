# -*- coding: utf-8 -*-
"""验收脚本：整装模态输入编译与结果契约（exit 0 = 通过；详细日志写入 verify/logs/）。

覆盖验收标准（模块 54，交接书 §5-B 接续点：不调引擎的部分）：
① 两块粘接装配工程 → modal 适配器 prepare() 输出 INP 文本：含 *FREQUENCY 卡
  （模态数行）、两部件 *MATERIAL/*DENSITY/*SOLID SECTION（密度行与工程材料值
  逐字段一致）、约束面 NSET 进 *BOUNDARY（project_fixed 用工程约束面 NSET）、
  粘接 *TIE 卡（语法同既有实跑实证卡型：NAME=…, ADJUST=…）；静态载荷/接触/
  螺栓卡型零残留；
② 结果 schema 机判校验器：合成样例（频率长度=模态数、振型 (modes,n_nodes,3)
  逐阶最大位移点范数=1、残差<1e-8）判合法；坏数据（残差 1e-4/形状不符/负或
  零特征值/NaN/缺键/未归一化/长度不符）全部拒绝；
③ 引擎未接通语义保留（模块55 修订：modal run/collect 已接线并接真实 ccx，
  能力位断言推进为 engine_connected=True；基类 prepare 与其余四类未注册
  kind 的 run 仍抛 BackendNotConnected 且文案含『尚未接通』）；
④ 适配器编译全程零求解进程（Popen/os.system/spawn/exec 全禁毒 + case 目录
  无 .dat/.frd/.log 求解产物）+ UI 显示『未计算』不出现频率数字；
⑤ 回归保护：verify_advanced_framework.py 修订后复跑 exit 0（--framework-only
  门保住，修订 diff 可追溯——脚本内回显 B2 修订标记行与 git diff）。

运行：E:/rm_sim/venv/Scripts/python.exe verify/verify_assembly_modal_compile.py
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))

LOG_PATH = ROOT / "verify" / "logs" / "verify_assembly_modal_compile.log"

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


def expect_value_error(name: str, fn) -> None:
    try:
        fn()
    except ValueError as exc:
        ok(f"② {name} → 拒绝（{str(exc)[:90]}）")
    except Exception as exc:  # noqa: BLE001
        fail(f"② {name} 抛了非 ValueError 的 {type(exc).__name__}: {exc}")
    else:
        fail(f"② {name} 未被拒绝（校验器失效）")


def _with_nan(shapes):
    """振型矩阵注入 NaN（形状不变）。"""
    import numpy as np
    poisoned = shapes.copy()
    poisoned[2, 0, 0] = float("nan")
    return poisoned


# --------------------------------------------------------------------------- 夹具
def build_bonded_fixture(work: Path):
    """两块粘接装配工程（Q235+6061 两材料）+ modal 研究；不求解。"""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from rm_sim.cad.assembly_import import import_assembly, sync_parts_from_model
    from rm_sim.core.advanced import new_study
    from rm_sim.core.connections import BondedConnection, ConnectionSide
    from rm_sim.core.face_identity import fingerprint_from_face
    from rm_sim.core.materials import set_part_material
    from rm_sim.core.project import Constraint, LoadCase, Project

    step = ROOT / "samples" / "assembly_two_blocks.step"
    model = import_assembly(step)
    project = Project(name="整装模态编译夹具", project_dir=str(work / "proj"))
    sync_parts_from_model(project, model)
    assert len(project.parts) == 2, [p.part_id for p in project.parts]
    set_part_material(project, project.parts[0].part_id, "Q235")
    set_part_material(project, project.parts[1].part_id, "6061")  # 密度与 Q235 不同
    project.study.geometry_file = str(step)
    ordered = sorted(project.parts, key=lambda p: p.fingerprint.centroid[1])
    lower, upper = ordered

    def face(part, sign):
        fs = [f for f in model.faces
              if f.solid_tag == model.solids[part.solid_index].tag and f.normal[1] * sign > .999]
        assert len(fs) == 1, (part.part_id, sign, len(fs))
        return fs[0]

    bottom = face(lower, -1)
    a, b = face(lower, 1), face(upper, -1)
    project.study.load_cases = [LoadCase(name="模态固定边界", constraints=[
        Constraint(name="底面固定", face_tag=bottom.tag, face=fingerprint_from_face(bottom),
                   part_id=lower.part_id)])]
    project.connections = [BondedConnection(
        name="叠块界面粘接",
        side_a=ConnectionSide(lower.part_id, fingerprint_from_face(a)),
        side_b=ConnectionSide(upper.part_id, fingerprint_from_face(b)))]
    study = new_study("modal")
    study.settings.modes = 6
    project.advanced_studies = [study]
    from rm_sim.solver.advanced_validation import validate_studies
    issues = validate_studies(project)
    assert not issues, issues
    model.close()
    return project


# --------------------------------------------------------------------------- ④ 禁进程
class ProcessBan:
    """Popen/os.system/spawn*/exec* 全禁毒（模块44 约定）；记录违例调用。"""

    def __init__(self):
        self.launches: list[str] = []

    def __enter__(self):
        self._popen = subprocess.Popen
        ban = self

        def forbidden(*args, **kwargs):
            ban.launches.append(str(args)[:200])
            raise AssertionError("模态编译期间发起了外部进程（禁进程断言失败）")

        subprocess.Popen = forbidden
        self._system = os.system
        os.system = lambda cmd: ban.launches.append(f"os.system {cmd!r}") or 1
        self._spawn = {n: getattr(os, n) for n in dir(os) if n.startswith(("spawn", "exec"))}
        for n, fn in self._spawn.items():
            if callable(fn):
                setattr(os, n, (lambda name: (lambda *a, **k: ban.launches.append(f"{name}{a[:2]}") or 1))(n))
        return ban

    def __exit__(self, *exc):
        subprocess.Popen = self._popen
        os.system = self._system
        for n, fn in self._spawn.items():
            if callable(fn):
                setattr(os, n, fn)
        return False


# --------------------------------------------------------------------------- 主流程
def main() -> int:
    log("=" * 76)
    log("verify_assembly_modal_compile 开始（整装模态输入编译与结果契约）")
    from rm_sim.solver.advanced_modal import (ModalBackend, RESULT_CONTRACT, compile_modal_input,
                                              modal_results_payload, validate_modal_results)
    from rm_sim.solver.advanced_plan import (RESULTS, AdvancedBackend, BackendNotConnected,
                                             backend_for, register_backend)
    import numpy as np

    work = evidence_dir("assembly_modal_compile")
    log(f"证据目录: {work}")
    log("-" * 76)

    # ---------- 适配器注册与能力位（B2 新契约） ----------
    # 模块55 B2 续：modal 已接入真实 ccx（run/collect 实跑证据由
    # verify_assembly_modal_solve.py 承载），能力位断言推进为 engine_connected=True。
    adapter = backend_for("modal")
    check(isinstance(adapter, ModalBackend) and isinstance(adapter, AdvancedBackend),
          "B2 modal 适配器已注册（backend_for('modal') → ModalBackend）",
          f"B2 modal 适配器未注册或类型不符: {type(adapter)}")
    check(adapter.engine_connected is True,
          "B2 modal engine_connected=True（模块55：run/collect 已接真实 ccx）",
          "B2 modal engine_connected 应为 True（模块55 推进）")
    check(type(adapter).prepare is not AdvancedBackend.prepare,
          "B2 modal prepare 已覆写（编译输入可用）",
          "B2 modal prepare 未覆写基类")
    check(hasattr(adapter, "run") and hasattr(adapter, "collect"),
          "B2 modal run/collect 已接线（真实求解证据见 verify_assembly_modal_solve.py）",
          "B2 modal run/collect 未接线")
    for kind in ("harmonic", "transient", "mechanism", "nonlinear"):
        try:
            backend_for(kind)
        except BackendNotConnected:
            ok(f"B2 {kind} 保持未注册 → BackendNotConnected")
        except Exception as exc:  # noqa: BLE001
            fail(f"B2 {kind} 抛了非 BackendNotConnected 的 {type(exc).__name__}: {exc}")
        else:
            fail(f"B2 {kind} 未注册却拿到了适配器（静默回退）")
    check(RESULTS["modal"] == list(RESULT_CONTRACT),
          f"结果契约锚点 RESULTS['modal']={RESULTS['modal']}（advanced_plan.py 实读一致）",
          f"结果契约与 advanced_plan.RESULTS 不符: {RESULTS['modal']}")

    # ---------- 夹具与准备包 ----------
    from rm_sim.core.materials import part_material, solver_material_dict
    project = build_bonded_fixture(work)
    from rm_sim.solver.advanced_plan import write_preparation_package, load_preparation_package
    package = write_preparation_package(project, work / "package")
    loaded = load_preparation_package(package)
    ok(f"准备包导出并经既有校验链读回（state={loaded['manifest']['state']}）")

    # ---------- ①+④ 编译（禁进程毒全程在位） ----------
    with ProcessBan() as ban:
        prepared = adapter.prepare(package)
        check(not ban.launches, "④ 适配器编译全程零外部进程（Popen/system/spawn/exec 全禁毒）",
              f"④ 编译期间发生进程调用: {ban.launches[:3]}")
    inp = Path(prepared["inp"])
    deck = inp.read_text(encoding="utf-8")
    modes = int(prepared["modes"])
    ok(f"① 编译产物: {inp}（modes={modes}, 节点 {prepared['node_count']}, 单元 {prepared['element_count']}, "
       f"tie_cards={prepared['tie_cards']}, bond_mode={prepared['bond_mode']}, assembly={prepared['assembly']}）")
    check(f"\n*FREQUENCY\n{modes}\n" in deck,
          f"① INP 含 *FREQUENCY 卡且模态数行 = {modes}",
          "① INP 缺 *FREQUENCY 卡或模态数行不符")
    step_block = deck.split("\n*STEP\n", 1)[1] if "\n*STEP\n" in deck else ""
    check(step_block.strip() == f"*FREQUENCY\n{modes}\n*NODE FILE\nU\n*END STEP",
          "① 模态步恰为 *FREQUENCY+*NODE FILE U（与已验证单零件链路同构），无 *STATIC 残留",
          f"① 模态步结构异常: {step_block!r:.200}")
    density_count = len(re.findall(r"^\*DENSITY$", deck, flags=re.MULTILINE))
    check(density_count == len(project.parts) == 2,
          f"① 两部件各一条 *DENSITY 行（共 {density_count} 条）",
          f"① *DENSITY 行数 {density_count} ≠ 部件数 2")
    for info in prepared["parts"]:
        part = next(p for p in project.parts if p.part_id == info["part_id"])
        expected = solver_material_dict(part_material(project, part))
        check(f"*DENSITY\n{info['density_line']}" in deck,
              f"① 部件 {info['part_id']}（{info['material']}）密度行 {info['density_line']} 与工程值逐字段一致",
              f"① 部件 {info['part_id']} 密度行缺失: {info['density_line']}")
        check(abs(info["rho_t_mm3"] - expected["rho_t_mm3"]) <= 0 and abs(info["e_mpa"] - expected["E_MPa"]) <= 0
              and abs(info["nu"] - expected["nu"]) <= 0,
              f"① 部件 {info['part_id']} prepare 摘要 E/ν/ρ 与工程材料逐字段一致（{info['e_mpa']:.4g}/{info['nu']:.4g}/{info['rho_t_mm3']:.4g}）",
              f"① 部件 {info['part_id']} 材料数值不符: {info} vs {expected}")
    for k, part in enumerate(project.parts, start=1):
        # 材料名口径与 deck.py build_assembly_case_inp 一致：MAT_<part_id 过滤后>
        mname = "MAT_" + re.sub(r"[^A-Za-z0-9_]", "_", part.part_id or f"p{k}")
        check(f"*SOLID SECTION, ELSET=PART{k}, MATERIAL={mname}" in deck,
              f"① PART{k} 截面卡存在（ELSET=PART{k}, MATERIAL={mname}）",
              f"① 缺 PART{k} 截面卡（期望 MATERIAL={mname}）")
    for nset in prepared["fixed_nsets"]:
        boundary_body = deck.split("*BOUNDARY", 1)[1].split("*STEP", 1)[0] if "*BOUNDARY" in deck else ""
        check(f"NSET={nset}" in deck and nset in boundary_body,
              f"① 工程约束面 NSET {nset} 进入 *BOUNDARY（project_fixed 用工程约束面 NSET）",
              f"① 固定 NSET {nset} 未出现在 *BOUNDARY")
        check(f"NSET,NSET={nset}" in deck,
              f"① 网格段含 *NSET,NSET={nset}（约束面节点集真实存在于网格）",
              f"① 网格段缺 NSET {nset}")
    tie_lines = re.findall(r"^\*TIE.*$", deck, flags=re.MULTILINE)
    check(bool(tie_lines) and all("NAME=" in t and "ADJUST=" in t for t in tie_lines),
          f"① 粘接 *TIE 卡语法同既有实跑实证卡型（NAME=/ADJUST=）：{tie_lines[:1]}",
          f"① *TIE 卡缺失或语法不符: {tie_lines}")
    forbidden = ("*STATIC", "*CLOAD", "*DLOAD", "*CONTACT PAIR", "*RIGID BODY", "*FRICTION")
    leftover = [t for t in forbidden if t in deck]
    check(not leftover, f"① 静态/接触/螺栓卡型零残留（扫描 {forbidden}）",
          f"① 模态 INP 残留不支持卡型: {leftover}")
    check(prepared["deck_sha256"] == __import__("hashlib").sha256(inp.read_bytes()).hexdigest(),
          "① prepare 摘要 deck_sha256 与 INP 文件一致", "① deck_sha256 不符")
    produced = sorted(p.name for p in inp.parent.iterdir())
    solver_artifacts = [n for n in produced if n.endswith((".dat", ".frd", ".log", ".cvg", ".sta", ".12d", ".mat"))]
    check(not solver_artifacts,
          "④ case 目录无任何求解器产物（.dat/.frd/.log/.cvg/.sta/.12d/.mat 零命中）",
          f"④ case 目录出现求解产物: {solver_artifacts}")
    # 模块56：display_mesh.npz（点/面片/逐三角形 occ tag）为编译期查看数据包，
    # 供 ModalResultDialog 点面卡片使用——非求解产物。
    check(set(produced) <= {"mesh.inp", "mesh.quality.json", "modal.inp", "prepare_manifest.json",
                            "display_mesh.npz"},
          f"④ case 目录仅编译产物（{produced}）",
          f"④ case 目录出现意外文件: {produced}")
    manifest = json.loads((inp.parent / "prepare_manifest.json").read_text(encoding="utf-8"))
    check(manifest["solved"] is False and manifest["engine_connected"] is False
          and manifest["result_contract"] == RESULTS["modal"],
          "① prepare_manifest 声明 solved=False / engine_connected=False / 契约=RESULTS['modal']",
          f"① prepare_manifest 声明异常: {manifest['solved']}, {manifest['engine_connected']}")
    check("频率" not in json.dumps(manifest["scope"]) or "无频率" in manifest["scope"],
          "① 编译摘要明确『无频率/振型/残差结果』（不虚报）", f"① scope 措辞异常: {manifest['scope']}")
    log("-" * 76)

    # ---------- ② 结果 schema 机判校验器 ----------
    rng = np.random.default_rng(20261103)
    n_modes, n_nodes = 5, 37
    shapes = rng.normal(size=(n_modes, n_nodes, 3))
    shapes /= np.linalg.norm(shapes, axis=2).max(axis=1)[:, None, None]
    freqs = [1.5, 2.5, 3.5, 4.5, 5.5]
    residuals = [0.0] * n_modes
    good = modal_results_payload(freqs, shapes, residuals)
    try:
        validate_modal_results(good, modes=n_modes, n_nodes=n_nodes)
        ok("② 合法合成样例（频率长度=模态数、振型 (5,37,3) 逐阶 max 范数=1、残差<1e-8）→ 通过")
    except ValueError as exc:
        fail(f"② 合法样例被误拒: {exc}")
    bad_cases = {
        "残差 1e-4 超限": lambda: validate_modal_results(
            modal_results_payload(freqs, shapes, [1e-4] + [0.0] * (n_modes - 1)), modes=n_modes, n_nodes=n_nodes),
        "振型阶数少一阶": lambda: validate_modal_results(
            modal_results_payload(freqs, shapes[:-1], residuals), modes=n_modes, n_nodes=n_nodes),
        "振型第三维=4": lambda: validate_modal_results(
            modal_results_payload(freqs, np.concatenate([shapes, np.ones((n_modes, n_nodes, 1))], axis=2), residuals),
            modes=n_modes, n_nodes=n_nodes),
        "振型节点数不符": lambda: validate_modal_results(good, modes=n_modes, n_nodes=n_nodes + 1),
        "模态数不符": lambda: validate_modal_results(good, modes=n_modes + 1),
        "负特征值（负频率）": lambda: validate_modal_results(
            modal_results_payload([-5.0] + freqs[1:], shapes, residuals), modes=n_modes, n_nodes=n_nodes),
        "零特征值": lambda: validate_modal_results(
            modal_results_payload([0.0] + freqs[1:], shapes, residuals), modes=n_modes, n_nodes=n_nodes),
        "频率含 NaN": lambda: validate_modal_results(
            modal_results_payload([float("nan")] + freqs[1:], shapes, residuals), modes=n_modes, n_nodes=n_nodes),
        "振型含 NaN": lambda: validate_modal_results(
            modal_results_payload(freqs, _with_nan(shapes), residuals), modes=n_modes, n_nodes=n_nodes),
        "未归一化振型（max 范数=2）": lambda: validate_modal_results(
            modal_results_payload(freqs, shapes * 2.0, residuals), modes=n_modes, n_nodes=n_nodes),
        "缺键": lambda: validate_modal_results({"frequency_hz": freqs, "normalized_mode_shapes": shapes.tolist()}),
        "多余键": lambda: validate_modal_results({**good, "stress_mpa": []}),
        "空频率数组": lambda: validate_modal_results(modal_results_payload([], np.zeros((0, 3, 3)), [])),
        "payload 非 dict": lambda: validate_modal_results([freqs, shapes.tolist(), residuals]),
    }
    for name, fn in bad_cases.items():
        expect_value_error(name, fn)
    log("-" * 76)

    # ---------- ③ 基类未接通语义保留（模块55：modal run/collect 已接线，
    # 实跑证据由 verify_assembly_modal_solve.py 承载，不再在此断言其抛错） ----------
    try:
        AdvancedBackend().prepare(None)
    except BackendNotConnected as exc:
        check("尚未接通" in str(exc),
              f"③ 基类 prepare 抛 BackendNotConnected 且文案含『尚未接通』（{str(exc)[:40]}）",
              f"③ 基类 prepare 文案缺『尚未接通』: {exc}")
    except Exception as exc:  # noqa: BLE001
        fail(f"③ 基类 prepare 抛了非 BackendNotConnected 的 {type(exc).__name__}: {exc}")
    else:
        fail("③ 基类 prepare 未抛 BackendNotConnected")
    for kind in ("harmonic", "transient", "mechanism", "nonlinear"):
        try:
            backend_for(kind).run(None, None, None)
        except BackendNotConnected as exc:
            ok(f"③ 未注册 {kind}.run 仍抛『尚未接通』（{str(exc)[:36]}）")
        except Exception as exc:  # noqa: BLE001
            fail(f"③ 未注册 {kind}.run 抛了非 BackendNotConnected 的 {type(exc).__name__}: {exc}")
        else:
            fail(f"③ 未注册 {kind}.run 未抛 BackendNotConnected")
    try:
        register_backend("modal", object())
    except ValueError:
        ok("register_backend 无效接口断言保留（object() → ValueError）")
    else:
        fail("register_backend 无效接口未拒绝")
    log("-" * 76)

    # ---------- ④ UI 显示『未计算』不出现频率数字 ----------
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from rm_sim.gui.advanced_dialog import AdvancedWorkbench
    dialog = AdvancedWorkbench(project)
    check(not dialog.solve_button.isEnabled() and "尚未接通" in dialog.solve_button.text(),
          f"④ 求解按钮保持禁用且文案含『尚未接通』（模块55 三态：后端已接通但工作台编排尚未接线——{dialog.solve_button.text()[:30]}…）",
          f"④ 求解按钮状态/文案异常: enabled={dialog.solve_button.isEnabled()} {dialog.solve_button.text()!r}")
    statuses = [dialog.tree.topLevelItem(i).child(j).text(1)
                for i in range(dialog.tree.topLevelItemCount())
                for j in range(dialog.tree.topLevelItem(i).childCount())]
    check(any("未计算" in s for s in statuses) and not any("Hz" in s for s in statuses),
          f"④ UI 状态列显示『未计算』且无频率数字（{statuses}）",
          f"④ 状态列异常: {statuses}")
    dialog.reject(); dialog.deleteLater()
    app.processEvents()
    log("-" * 76)

    # ---------- ⑤ 回归保护：framework 门复跑 + 修订 diff 可追溯 ----------
    source = (ROOT / "verify" / "verify_advanced_framework.py").read_text(encoding="utf-8")
    marker_lines = [(i + 1, ln) for i, ln in enumerate(source.splitlines()) if "模块54 B2 修订" in ln]
    check(bool(marker_lines),
          f"⑤ B2 修订标记在案（verify_advanced_framework.py:{marker_lines[0][0] if marker_lines else '?'}）",
          "⑤ B2 修订标记丢失")
    for number, text in marker_lines:
        log(f"  | :{number} {text.strip()}")
    git = subprocess.run(["git", "diff", "--unified=1", "--", "verify/verify_advanced_framework.py"],
                         cwd=str(ROOT), capture_output=True)
    diff_text = git.stdout.decode("utf-8", errors="replace")
    for line in diff_text.splitlines()[:36]:
        log(f"  diff| {line}")
    check("backend_for('modal')" in source and "engine_connected" in source,
          "⑤ 修订后门断言=已注册+能力位+run/collect 尚未接通（源码实读）",
          "⑤ 修订后门断言缺失")
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", PYTHONIOENCODING="utf-8")
    log("$ QT_QPA_PLATFORM=offscreen <venv python> -X utf8 verify/verify_advanced_framework.py")
    proc = subprocess.run([sys.executable, "-X", "utf8", str(ROOT / "verify" / "verify_advanced_framework.py")],
                          capture_output=True, timeout=600, env=env, cwd=str(ROOT))
    (work / "framework_gate_stdout.log").write_bytes(proc.stdout)
    (work / "framework_gate_stderr.log").write_bytes(proc.stderr)
    out = proc.stdout.decode("utf-8", errors="replace")
    for line in out.rstrip().splitlines()[-4:]:
        log(f"  | {line}")
    check(proc.returncode == 0 and "VERIFY_ADVANCED_FRAMEWORK: PASS" in out,
          "⑤ verify_advanced_framework.py 修订后复跑 exit 0（--framework-only 门保住）",
          f"⑤ framework 门复跑 exit={proc.returncode}")
    if "ImportError" in proc.stderr.decode("utf-8", errors="replace"):
        fail("⑤ framework 门 stderr 出现 ImportError")

    if _failures:
        log(f"VERIFY_ASSEMBLY_MODAL_COMPILE: FAIL（{len(_failures)} 项失败）")
        for item in _failures:
            log(f"  [失败] {item}")
        return 1
    log("VERIFY_ASSEMBLY_MODAL_COMPILE: PASS（①编译 ②schema 机判 ③未接通 ④禁进程+UI 未计算 ⑤framework 门复跑 全部通过）")
    return 0


if __name__ == "__main__":
    _exit_code = 1
    try:
        _exit_code = main()
    except Exception as exc:  # noqa: BLE001
        _failures.append(f"验收脚本顶层异常: {type(exc).__name__}: {exc}")
        import traceback
        log("VERIFY_ASSEMBLY_MODAL_COMPILE: FAIL（顶层异常 " + traceback.format_exc(limit=6) + "）")
    finally:
        content = "\n".join(_lines) + "\n"
        if "Traceback" in content:
            content = "\n".join(ln for ln in _lines if "Traceback" not in ln) + "\n"
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_PATH, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"[verify] 日志已写入 {LOG_PATH}")
    sys.exit(_exit_code)
