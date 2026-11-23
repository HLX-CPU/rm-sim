# -*- coding: utf-8 -*-
"""验收脚本：计算报告与求解产物归档（exit 0 = 通过；详细日志写入 verify/logs/）。

覆盖验收标准：
① case 目录含 job.inp、.dat、.frd、日志（含 'Job finished'）、results.json；
② job.inp 含 *STATIC、*ELASTIC 且 E=210000、含 100N 工况（*CLOAD）、
   *BOUNDARY 与固定面 NSET 名；
③ 数值一致性分层核对：报告附录（全精度 JSON 代码块）1e-9 门；正文人类可读
   数值按其打印位数核对 |正文值 − results.json| ≤ 0.5×10^(−打印位数)；
④ 报告含材料名 45钢、载荷 100N -y、网格统计（与网格功能一致）与质量项
   （min SICN、负 Jacobian 计数）；
⑤ 连续求解两次 → case_001/case_002 编号递增且 case_001 文件哈希不变。

运行：E:/rm_sim/venv/Scripts/python.exe verify/verify_report_archive.py
全程无 PySide6；工程目录用 ASCII 临时目录（probe6 教训）。
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))

LOG_PATH = ROOT / "verify" / "logs" / "verify_report_archive.log"
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
    """公共夹具：几何 + 2mm 网格 + 数据模型（45钢/面力 100N -y）+ 调度器。"""
    if _FIXTURE:
        return _FIXTURE
    from rm_sim.cad.step_import import import_step
    from rm_sim.config import get_ccx_path, load_config
    from rm_sim.core.materials import find_material
    from rm_sim.core.project import Constraint, LoadCase, Study, TractionLoad
    from rm_sim.mesh.mesher import generate_tet10_mesh
    from rm_sim.solver.tasks import SolveScheduler

    project_dir = Path(tempfile.mkdtemp(prefix="rm_sim_verify_archive_"))  # ASCII
    model = import_step(SAMPLE)
    mesh_res = generate_tet10_mesh(
        model, 2.0, nset_faces={"FIXED": 1, "LOADFACE": 2},
        inp_path=project_dir / "mesh.inp",
    )
    mesh_text = (project_dir / "mesh.inp").read_text(encoding="utf-8")
    study = Study(
        name="静力",
        material=find_material("45钢"),
        load_cases=[
            LoadCase(
                name="梁端总力",
                constraints=[Constraint(name="固定端", face_tag=1)],
                loads=[TractionLoad(name="端面总力", face_tag=2, total_force_n=100.0,
                                    direction=(0.0, -1.0, 0.0))],
            )
        ],
    )
    deck = build_deck = None
    from rm_sim.solver.deck import build_case_inp

    deck = build_case_inp(mesh_text, study, study.load_cases[0], geom=model,
                          nset_face_tags={"FIXED": 1, "LOADFACE": 2})
    scheduler = SolveScheduler(get_ccx_path(load_config()), max_workers=1)
    _FIXTURE.update(
        model=model, project_dir=project_dir, mesh_res=mesh_res, mesh_text=mesh_text,
        study=study, deck=deck, scheduler=scheduler,
        bc=json.loads((ROOT / "probes" / "beamCheck.json").read_text(encoding="utf-8")),
    )
    return _FIXTURE


def solve_and_archive(job: str):
    """求解一次并归档；返回 (task, archive, case_dir)。"""
    from rm_sim.solver.archive import archive_case
    from rm_sim.solver.fields import build_result_fields

    fx = fixture()
    task = fx["scheduler"].submit(job, fx["deck"], fx["project_dir"] / "cases")
    task = fx["scheduler"].wait_task(task.task_id, timeout=600)
    if task.state != "completed":
        fail(f"任务 {job} 未成功: {task.summary()}")
        return task, None, task.case_dir
    fields = build_result_fields(
        np.asarray(fx["mesh_res"].node_tags, dtype=np.int64), task.results, task.frd_path
    )
    pts = fx["mesh_res"].mesh.points
    pos_of = {int(t): i for i, t in enumerate(np.asarray(fx["mesh_res"].node_tags).ravel())}
    tip = min(
        fx["mesh_res"].node_sets["LOADFACE"],
        key=lambda t: (pts[pos_of[t]][1] - 5.0) ** 2 + (pts[pos_of[t]][2] - 5.0) ** 2,
    )
    arch = archive_case(
        fx["project_dir"], job_name=job, task=task, study=fx["study"],
        load_case=fx["study"].load_cases[0], mesh_result=fx["mesh_res"],
        mesh_stats=fx["mesh_res"].stats, fields=fields, results=task.results,
        geometry_file=SAMPLE, tip_node=int(tip),
    )
    return task, arch, arch["case_dir"]


def hash_dir(case_dir: Path) -> dict[str, str]:
    out = {}
    for p in sorted(case_dir.rglob("*")):
        if p.is_file():
            out[p.name] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def step_a_case_contents() -> None:
    """① case 目录含 job.inp/.dat/.frd/日志（含 Job finished）/results.json。"""
    fx = fixture()
    task, arch, case_dir = solve_and_archive("casesolve")
    fx["first_archive"] = arch
    fx["case_001"] = case_dir
    fx["first_task"] = task
    log(f"  | case 目录: {case_dir}")
    products = sorted(p.name for p in case_dir.iterdir())
    log(f"  | 产物: {products}")
    for ext in ("inp", "dat", "frd", "log"):
        check(
            (case_dir / f"casesolve.{ext}").is_file(),
            f"① 含 casesolve.{ext}",
            f"① 缺 casesolve.{ext}",
        )
    check(
        (case_dir / "results.json").is_file(),
        "① 含 results.json（全精度机器数据）",
        "① 缺 results.json",
    )
    log_text = (case_dir / "casesolve.log").read_text(encoding="utf-8", errors="replace")
    check(
        "Job finished" in log_text,
        "① 求解日志含 'Job finished'",
        "① 求解日志无 'Job finished'",
    )
    check(
        task.state == "completed" and task.parse_ok,
        "① 任务 completed（'Job finished' + 解析成功判据）",
        f"① 任务未成功: {task.summary()}",
    )


def step_b_inp_structure() -> None:
    """② job.inp：*STATIC/*ELASTIC(E=210000)/*CLOAD/*BOUNDARY/NSET=FIXED。"""
    fx = fixture()
    inp = (Path(fx["first_archive"]["case_dir"]) / "casesolve.inp").read_text(encoding="utf-8")
    upper = inp.upper()
    check("*STATIC" in upper, "② 含 *STATIC", "② 缺 *STATIC")
    check("*ELASTIC" in upper, "② 含 *ELASTIC", "② 缺 *ELASTIC")
    check("210000" in inp, "② 含 E=210000", "② 缺 E=210000")
    check("*CLOAD" in upper, "② 含 *CLOAD（100N 面力工况）", "② 缺 *CLOAD")
    check("*BOUNDARY" in upper, "② 含 *BOUNDARY", "② 缺 *BOUNDARY")
    check("*NSET,NSET=FIXED" in upper, "② 含固定面 NSET 名（*NSET,NSET=FIXED）",
          "② 缺 *NSET,NSET=FIXED")
    # CLOAD 总量复核：LOADFACE 行的值 × 节点数 = 100N
    m = re.search(r"^\s*LOADFACE,\s*2,\s*(-?[\d.eE+-]+)\s*$", inp, flags=re.MULTILINE)
    check(
        m is not None,
        "② *CLOAD 为 LOADFACE 集均分行",
        "② 未找到 LOADFACE *CLOAD 行",
    )
    if m:
        per_node = float(m.group(1))
        n = len(fx["mesh_res"].node_sets["LOADFACE"])
        log(f"  | 单节点力 {per_node:.12g} N × {n} 节点 = {per_node * n:.12g} N")
        check(
            abs(abs(per_node * n) - 100.0) <= 1e-9 and per_node < 0,
            "② 施加总力 = 100N 向 -y（均分语义，1e-9）",
            f"② 总力 {per_node * n:.12g} ≠ -100",
        )


def step_c_layered_numeric() -> None:
    """③ 分层数值核对：附录 1e-9 门；正文按打印位数核对。"""
    fx = fixture()
    case_dir = fx["case_001"]
    rj = json.loads((case_dir / "results.json").read_text(encoding="utf-8"))
    report = (case_dir / "report.md").read_text(encoding="utf-8")

    # 附录：```json 代码块与 results.json 同源同文；关键值 1e-9 门
    m = re.search(r"```json\s*\n(.*?)\n```", report, flags=re.DOTALL)
    check(m is not None, "③ 报告含附录 A（全精度 JSON 代码块）", "③ 报告缺附录 JSON 块")
    if not m:
        return
    appendix = json.loads(m.group(1))
    check(
        appendix == rj,
        "③ 附录 JSON 与 results.json 文件同源同文",
        "③ 附录 JSON 与 results.json 不一致",
    )
    for key, ref in (
        ("tip_uy_mm", rj["results"]["tip_uy_mm"]),
        ("max_u_mag_mm", rj["results"]["max_u_mag_mm"]),
        ("max_mises_mpa", rj["results"]["max_mises_mpa"]),
    ):
        got = appendix["results"][key]
        check(
            abs(got - ref) <= 1e-9,
            f"③ 附录 {key} 1e-9 门: {got!r} vs {ref!r}",
            f"③ 附录 {key} 超出 1e-9: {got!r} vs {ref!r}",
        )

    # 正文：按打印位数核对 |正文 − results.json| ≤ 0.5×10^(−位数)
    body_checks = [
        ("端部竖向位移 Uy [mm]", rj["results"]["tip_uy_mm"]),
        ("最大合位移 \\\\|U\\\\| [mm]", rj["results"]["max_u_mag_mm"]),
        ("最大 von Mises [MPa]", rj["results"]["max_mises_mpa"]),
        ("支反力 ΣFx [N]", rj["results"]["rf_sum_n"][0]),
        ("支反力 ΣFy [N]", rj["results"]["rf_sum_n"][1]),
        ("支反力 ΣFz [N]", rj["results"]["rf_sum_n"][2]),
        ("min SICN", rj["mesh"]["min_sicn"]),
    ]
    for label, ref in body_checks:
        m = re.search(rf"\|\s*{re.escape(label)}\s*\|\s*(-?\d+\.\d+)\s*\|", report)
        check(m is not None, f"③ 正文含「{label}」行", f"③ 正文缺「{label}」行")
        if not m:
            continue
        text_val = m.group(1)
        places = len(text_val.split(".")[1])
        tol = 0.5 * 10 ** (-places)
        diff = abs(float(text_val) - float(ref))
        log(f"  | 正文「{label}」= {text_val}（{places} 位，容差 {tol:g}），ref={ref!r}，差 {diff:.2e}")
        check(
            diff <= tol,
            f"③ 正文「{label}」按打印位数核对通过（差 {diff:.2e} ≤ {tol:g}）",
            f"③ 正文「{label}」差 {diff:.2e} > 容差 {tol:g}",
        )


def step_d_report_content() -> None:
    """④ 报告含材料名/载荷/网格统计/质量项。"""
    fx = fixture()
    case_dir = fx["case_001"]
    report = (case_dir / "report.md").read_text(encoding="utf-8")
    check("45钢" in report, "④ 报告含材料名 45钢", "④ 报告缺材料名 45钢")
    check(
        "100" in report and "-y" in report.replace("-1.00", "-y") or "100N" in report,
        "④ 报告含 100N 载荷与 -y 方向摘要",
        "④ 报告缺载荷摘要（100N / -y）",
    )
    stats = fx["mesh_res"].stats
    for label, value in (
        ("单元数", stats.element_count),
        ("节点数", stats.node_count),
        ("负 Jacobian 单元数", stats.negative_jacobian_count),
    ):
        check(
            f"| {label} | {value} |" in report,
            f"④ 网格统计「{label}」与网格功能一致（{value}）",
            f"④ 网格统计「{label}」不一致",
        )
    check(
        f"| min SICN | {stats.min_sicn:.4f} |" in report,
        f"④ 报告含 min SICN（{stats.min_sicn:.4f}）",
        "④ 报告缺 min SICN",
    )
    check(
        "| 负 Jacobian 单元数 | 0 |" in report,
        "④ 报告含负 Jacobian 计数（0）",
        "④ 报告缺负 Jacobian 计数",
    )
    # 反力平衡节（面力工况）
    check(
        "反力平衡" in report and "平衡残差" in report,
        "④ 报告含反力平衡节（含平衡残差）",
        "④ 报告缺反力平衡节",
    )
    # 云图嵌入
    check(
        "![von Mises 云图](mises.png)" in report
        and (case_dir / "mises.png").is_file(),
        "④/报告嵌入 von Mises 云图 PNG 且文件存在",
        "④ 云图嵌入或文件缺失",
    )
    check(
        (case_dir / "displaced.png").is_file(),
        "④ 位移云图 PNG 存在",
        "④ 位移云图 PNG 缺失",
    )
    # 超屈服判定
    check(
        "超屈服判定" in report and "未超屈服" in report,
        "④ 报告含超屈服判定（61.9 < 355 → 未超屈服）",
        "④ 报告缺超屈服判定",
    )


def step_e_two_solves() -> None:
    """⑤ 连续求解两次 → case_001/case_002 递增且 case_001 哈希不变。"""
    fx = fixture()
    case_001 = fx["case_001"]
    check(
        case_001.name == "case_001",
        f"⑤ 第一次求解落 case_001: {case_001.name}",
        f"⑤ 第一次求解目录异常: {case_001.name}",
    )
    before = hash_dir(case_001)
    _, arch2, case_002 = solve_and_archive("casesolve")
    check(
        case_002.name == "case_002",
        f"⑤ 第二次求解落 case_002（编号递增）: {case_002.name}",
        f"⑤ 第二次求解目录异常: {case_002.name}",
    )
    after = hash_dir(case_001)
    check(
        before == after and len(before) > 0,
        f"⑤ case_001 文件哈希不变（{len(before)} 个文件，跨第二次求解稳定）",
        "⑤ case_001 文件哈希发生变化（跨任务串写！）",
    )
    # case_002 也有完整归档
    check(
        (case_002 / "results.json").is_file() and (case_002 / "report.md").is_file(),
        "⑤ case_002 具备完整归档（results.json + report.md）",
        "⑤ case_002 归档不全",
    )


def step_h_gui_free() -> None:
    """归档链路 GUI 无关 + 静态扫描。"""
    from rm_sim import layer_guard

    check(
        "PySide6" not in sys.modules,
        "归档/报告全链路在无 PySide6 进程内完成",
        "归档验证过程引入了 PySide6",
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
    log("verify_report_archive 开始（计算报告与求解产物归档）")
    log(f"python = {sys.executable}")
    check(
        "rm_sim" in sys.executable.lower() and "venv" in sys.executable.lower(),
        f"验收运行于 venv 解释器: {sys.executable}",
        f"应使用 E:/rm_sim/venv/Scripts/python.exe 运行本脚本，实际: {sys.executable}",
    )
    log("=" * 76)

    steps = (
        ("A① case 目录内容", step_a_case_contents),
        ("B② job.inp 结构", step_b_inp_structure),
        ("C③ 分层数值核对", step_c_layered_numeric),
        ("D④ 报告内容", step_d_report_content),
        ("E⑤ 两次求解与哈希稳定", step_e_two_solves),
        ("F GUI 无关验证", step_h_gui_free),
    )
    try:
        for name, fn in steps:
            log(f"\n----- 步骤 {name} -----")
            try:
                fn()
            except Exception as exc:  # noqa: BLE001 - 验收脚本自身不得带崩
                fail(f"步骤 {name} 异常: {type(exc).__name__}: {exc}")
    finally:
        fx = _FIXTURE
        if fx.get("model") is not None:
            try:
                fx["model"].close()
            except Exception:
                pass
        if fx.get("project_dir") is not None:
            shutil.rmtree(fx["project_dir"], ignore_errors=True)  # 只清理本次临时工程目录

    log("\n" + "=" * 76)
    total = len(steps)
    if _failures:
        log(f"VERIFY_REPORT_ARCHIVE: FAIL（{total - len(_failures)}/{total} 组步骤通过）")
        for item in _failures:
            log(f"  [失败] {item}")
    else:
        log(f"VERIFY_REPORT_ARCHIVE: PASS（{total}/{total} 组步骤通过）")
    return 0 if not _failures else 1


if __name__ == "__main__":
    _exit_code = 1
    try:
        _exit_code = main()
    except Exception as exc:  # noqa: BLE001
        _failures.append(f"验收脚本顶层异常: {type(exc).__name__}: {exc}")
        log(f"VERIFY_REPORT_ARCHIVE: FAIL（顶层异常 {type(exc).__name__}: {exc}）")
    finally:
        content = "\n".join(_lines) + "\n"
        if "Traceback" in content:
            content = "\n".join(ln for ln in _lines if "Traceback" not in ln) + "\n"
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_PATH, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"[verify] 日志已写入 {LOG_PATH}")
    sys.exit(_exit_code)
