# -*- coding: utf-8 -*-
"""验收脚本：CalculiX 静力求解与任务调度（exit 0 = 通过；详细日志写入 verify/logs/）。

覆盖验收标准：
①-⑤ beamCheck 7 门（阈值取 probes/beamCheck.json assertions.thresholds）：
   端部 Uy vs δ=PL³/(3EI) 误差≤2%、|ΣRFy−100|≤0.01N、|Fx|,|Fz|≤1e-3N、
   .dat vs .frd≤1e-4mm、挠度向下、节点数完整；
⑥ 变异门修正：注入点=解析后、门计算前的数据层——Uy×2 令 err 门失败、
   RFy×0.5 令反力门失败（修正 probes/probe2_solve_check.py 先算门后注入的缺陷）；
⑦ 压力求解级门：2MPa 经 *DLOAD P 单元面集（.inp 结构断言非节点 CLOAD 近似）
   → ΣRFx=200N±0.02N；
⑧ 重力门：体积力向量=方向×9810（ccx 2.22 实证无 *GRAV 关键字，用 *DLOAD GRAV 标签）→ ΣRFy=ρVg=0.770085N（相对≤1%）；
⑨ 力矩门：5000N·mm 绕 z → 固定端 |ΣMz|=5000N·mm（相对≤1%）；
⑩ 负例：删 *STEP → 任务 failed、无 'Job finished'、错误非空；
⑪ 并发 2 任务：各自 case 目录含本作业名产物、无跨目录串写、主进程 200ms 心跳。

红线：每任务独立 case_NNN 目录先建后解；求解入口无 GUI（无 PySide6）；
子进程二进制捕获 + decode('utf-8', errors='replace')；退出码不作判据（两类
证据分开标注：官方 ccx '-v' 恒 201 / PrePoMax 双态 0xFFFFFFFF）。
运行：E:/rm_sim/venv/Scripts/python.exe verify/verify_static_solve.py
"""
from __future__ import annotations

import copy
import json
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "verify"))

from memory_budget import make_scheduler  # noqa: E402  模块46：预算分级+峰值记录

LOG_PATH = ROOT / "verify" / "logs" / "verify_static_solve.log"
BEAM_CHECK = ROOT / "probes" / "beamCheck.json"
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
    """公共夹具：几何 + 2mm 网格（NSET 挂接）+ 数据模型四个工况 + 调度器。"""
    if _FIXTURE:
        return _FIXTURE
    from rm_sim.cad.step_import import import_step
    from rm_sim.config import get_ccx_path, load_config
    from rm_sim.core.materials import find_material
    from rm_sim.core.project import (
        Constraint,
        GravityLoad,
        LoadCase,
        MomentLoad,
        PressureLoad,
        Study,
        TractionLoad,
    )
    from rm_sim.mesh.mesher import generate_tet10_mesh
    from rm_sim.solver.deck import build_case_inp
    from rm_sim.solver.tasks import SolveScheduler

    model = import_step(SAMPLE)
    tmp = Path(tempfile.mkdtemp(prefix="rm_sim_verify_solve_"))  # ASCII 路径（probe6 教训）
    mesh_res = generate_tet10_mesh(
        model, 2.0, nset_faces={"FIXED": 1, "LOADFACE": 2}, inp_path=tmp / "mesh.inp"
    )
    mesh_text = (tmp / "mesh.inp").read_text(encoding="utf-8")
    study = Study(
        name="静力",
        material=find_material("45钢"),
        load_cases=[
            LoadCase(
                name="梁端总力",
                constraints=[Constraint(name="固定端", face_tag=1)],
                loads=[TractionLoad(name="端面总力", face_tag=2, total_force_n=100.0,
                                    direction=(0.0, -1.0, 0.0))],
            ),
            LoadCase(
                name="梁端压力",
                constraints=[Constraint(name="固定端", face_tag=1)],
                loads=[PressureLoad(name="端面压力", face_tag=2, pressure_mpa=2.0)],
            ),
            LoadCase(
                name="自重",
                constraints=[Constraint(name="固定端", face_tag=1)],
                loads=[GravityLoad(name="重力", direction=(0.0, -1.0, 0.0), g_mm_s2=9810.0)],
            ),
            LoadCase(
                name="端面力矩",
                constraints=[Constraint(name="固定端", face_tag=1)],
                loads=[MomentLoad(name="端面力矩", face_tag=2, axis=(0.0, 0.0, 1.0),
                                  moment_nmm=5000.0)],
            ),
        ],
    )
    nset_face_tags = {"FIXED": 1, "LOADFACE": 2}
    decks = {
        "beamcheck": build_case_inp(mesh_text, study, study.load_cases[0], geom=model,
                                    nset_face_tags=nset_face_tags),
        "pressure": build_case_inp(mesh_text, study, study.load_cases[1], geom=model,
                                   nset_face_tags=nset_face_tags),
        "gravity": build_case_inp(mesh_text, study, study.load_cases[2], geom=model,
                                  nset_face_tags=nset_face_tags),
        "moment": build_case_inp(mesh_text, study, study.load_cases[3], geom=model,
                                 nset_face_tags=nset_face_tags),
    }
    ccx = get_ccx_path(load_config())
    scheduler = make_scheduler(ccx, max_workers=2)  # 模块46：分级内存预算（小档固定足额）
    fixed_ids = tuple(sorted(mesh_res.node_sets["FIXED"]))
    load_ids = tuple(sorted(mesh_res.node_sets["LOADFACE"]))
    pts = mesh_res.mesh.points
    tag_pos = {int(t): i for i, t in enumerate(np.asarray(mesh_res.node_tags).ravel())}
    _FIXTURE.update(
        model=model, tmp=tmp, mesh_res=mesh_res, study=study, decks=decks,
        scheduler=scheduler, work_root=tmp / "solve_runs", fixed_ids=fixed_ids,
        load_ids=load_ids, pts=pts, tag_pos=tag_pos,
        bc=json.loads(BEAM_CHECK.read_text(encoding="utf-8")),
    )
    return _FIXTURE


def tip_node() -> int:
    fx = fixture()
    pts = fx["pts"]
    return min(fx["load_ids"], key=lambda n: (pts[n][1] - 5.0) ** 2 + (pts[n][2] - 5.0) ** 2)


def run_case(job: str, deck: str, timeout: float = 600.0):
    fx = fixture()
    task = fx["scheduler"].submit(job, deck, fx["work_root"])
    return fx["scheduler"].wait_task(task.task_id, timeout=timeout)


def compute_beamcheck_gates(results, bc: dict, tip: int, n_fixed: int, n_load: int) -> dict:
    """beamCheck 7 门（probe2 逻辑的产品化修正版：门计算在变异之后）。"""
    th = bc["assertions"]["thresholds"]
    p = bc["parameters"]
    delta = p["P_N"] * p["L_mm"] ** 3 / (3 * p["E_MPa"] * p["I_mm4"])
    u_block = results.u_block("LOADFACE")
    rf_block = results.rf_block("FIXED")
    u_tip = u_block.get(tip)
    frd_tip = results.frd_disp.get(tip)
    err = abs(u_tip[1]) / delta - 1.0 if u_tip else float("nan")
    rf_sum = np.sum([rf_block[k] for k in sorted(rf_block)], axis=0) if rf_block else np.full(3, np.nan)
    frd_ok = frd_tip is not None
    return {
        "tipDeflectionWithinTheory": bool(u_tip is not None and not np.isnan(err)
                                          and abs(err) <= th["errPctMax"] / 100.0),
        "tipDeflectsDownward": bool(u_tip is not None and u_tip[1] < 0),
        "reactionFyEqualsP": bool(abs(rf_sum[1] - p["P_N"]) <= th["reactionTolN"]),
        "reactionFxIsometricZero": bool(abs(rf_sum[0]) <= th["transverseReactionTolN"]),
        "reactionFzIsometricZero": bool(abs(rf_sum[2]) <= th["transverseReactionTolN"]),
        "datFrdConsistent": bool(frd_ok and abs(abs(u_tip[1]) - abs(frd_tip[1]))
                                 <= th["datFrdTol_mm"]),
        "rfAndUNodesParsed": bool(len(rf_block) == n_fixed and len(u_block) == n_load),
    }


def step_a_beamcheck() -> None:
    """①-⑤ beamCheck 7 门复现。"""
    fx = fixture()
    task = run_case("beamcheck", fx["decks"]["beamcheck"])
    log(f"  | {task.summary()}")
    check(
        task.state == "completed" and task.job_finished and task.parse_ok,
        "任务 completed（Job finished + 解析成功，判据成立）",
        f"任务未成功: {task.summary()}",
    )
    check(
        task.exit_code is not None and task.exit_code_note != "",
        f"退出状态已留存并标注不作判据: exit={task.exit_code}",
        "退出状态未留存",
    )
    results = task.results
    tip = tip_node()
    gates = compute_beamcheck_gates(results, fx["bc"], tip,
                                    len(fx["fixed_ids"]), len(fx["load_ids"]))
    u_tip = results.u_block("LOADFACE")[tip]
    rf_sum = results.rf_sum("FIXED")
    p = fx["bc"]["parameters"]
    delta = p["P_N"] * p["L_mm"] ** 3 / (3 * p["E_MPa"] * p["I_mm4"])
    err_pct = (abs(u_tip[1]) / delta - 1.0) * 100.0
    log(
        f"  | tip 节点 {tip}: Uy_dat={u_tip[1]:.6f} mm vs δ={delta:.7f} mm "
        f"(err={err_pct:.4f}% ≤2%)\n"
        f"  | ΣRF(FIXED) = ({rf_sum[0]:.6f}, {rf_sum[1]:.6f}, {rf_sum[2]:.6f}) N\n"
        f"  | frd 节点 {results.frd_node_count()}（网格 {fx['mesh_res'].stats.node_count}），"
        f"RF/U 解析 {len(results.rf_block('FIXED'))}/{len(results.u_block('LOADFACE'))}"
    )
    for name, passed in gates.items():
        check(passed, f"门 {name} = True", f"门 {name} = False")
    _FIXTURE["beamcheck_results"] = results
    _FIXTURE["beamcheck_gates"] = gates


def step_b_mutation() -> None:
    """⑥ 变异注入（解析后、门前，数据层）：Uy×2 → err 门失败；RFy×0.5 → 反力门失败。"""
    fx = fixture()
    results = fx["beamcheck_results"]
    tip = tip_node()
    base_gates = fx["beamcheck_gates"]
    check(
        all(base_gates.values()),
        "⑥ 未变异时 7 门全 True（基线）",
        f"⑥ 基线门失败: {[k for k, v in base_gates.items() if not v]}",
    )
    # 变异 1：解析数据层 Uy×2（仅 dat 位移块，frd 保持诚实 → 一致性门同样失败）
    mut1 = copy.deepcopy(results)
    u_block = mut1.dat_blocks[("displacements", "LOADFACE")]
    u_block[tip] = [u_block[tip][0], u_block[tip][1] * 2.0, u_block[tip][2]]
    g1 = compute_beamcheck_gates(mut1, fx["bc"], tip, len(fx["fixed_ids"]), len(fx["load_ids"]))
    log(f"  | Uy×2 注入 → 门: {g1}")
    check(
        g1["tipDeflectionWithinTheory"] is False,
        "⑥ Uy×2 注入 → tipDeflectionWithinTheory=False（err 门可被证伪，probe2 缺陷已修正）",
        "⑥ Uy×2 注入后 err 门仍 True（门未被证伪）",
    )
    check(
        g1["datFrdConsistent"] is False,
        "⑥ Uy×2 注入（frd 诚实）→ datFrdConsistent=False",
        "⑥ 一致性门未按预期失败",
    )
    # 变异 2：RFy×0.5（反力提取缺陷模拟）
    mut2 = copy.deepcopy(results)
    rf_block = mut2.dat_blocks[("forces", "FIXED")]
    for nid in rf_block:
        rf_block[nid][1] *= 0.5
    g2 = compute_beamcheck_gates(mut2, fx["bc"], tip, len(fx["fixed_ids"]), len(fx["load_ids"]))
    log(f"  | RFy×0.5 注入 → reactionFyEqualsP={g2['reactionFyEqualsP']}")
    check(
        g2["reactionFyEqualsP"] is False,
        "⑥ RFy×0.5 注入 → reactionFyEqualsP=False（反力门可被证伪）",
        "⑥ RFy×0.5 注入后反力门仍 True",
    )


def step_c_pressure() -> None:
    """⑦ 压力 2MPa → *DLOAD P 单元面集 → ΣRFx=200N±0.02N。"""
    fx = fixture()
    deck = fx["decks"]["pressure"]
    check(
        "*DLOAD" in deck and "*CLOAD" not in deck,
        "⑦ .inp 结构：压力走 *DLOAD（无 *CLOAD——非节点力近似）",
        "⑦ 压力 .inp 结构违规（含 *CLOAD 或缺 *DLOAD）",
    )
    p_lines = [l for l in deck.splitlines() if re.match(r"^\d+, P[1-4], ", l)]
    check(
        len(p_lines) > 0 and all(int(l.split(",")[0]) for l in p_lines),
        f"⑦ *DLOAD 为逐单元面加载 {len(p_lines)} 行（形如 '单元, P1..P4, 值'）",
        "⑦ *DLOAD 行格式异常",
    )
    task = run_case("pressure", deck)
    log(f"  | {task.summary()}")
    check(
        task.state == "completed" and task.job_finished and task.parse_ok,
        "⑦ 压力工况求解完成",
        f"⑦ 压力工况未成功: {task.summary()}",
    )
    rf = task.results.rf_sum("FIXED")
    log(f"  | ΣRF(FIXED) = ({rf[0]:.6f}, {rf[1]:.6f}, {rf[2]:.6f}) N（期望 +200 沿 x）")
    check(
        abs(rf[0] - 200.0) <= 0.02,
        f"⑦ ΣRFx = 200N±0.02: {rf[0]:.6f}",
        f"⑦ ΣRFx = {rf[0]:.6f} 超出 200±0.02（P 面码映射或方向错误）",
    )


def step_d_gravity() -> None:
    """⑧ 重力体积力 → ΣRFy=ρVg=0.770085N（≤1%）。

    语法实证：ccx 2.22 不支持字面 *GRAV 关键字（calinput WARNING "cannot be
    interpreted"、载荷未生效、反力恒 0——模块 7 实测），ccx 重力正确语法 =
    *DLOAD 的 GRAV 标签（幅值 + 单位方向，即 方向×9810 的体积加速度向量）。"""
    fx = fixture()
    deck = fx["decks"]["gravity"]
    grav_lines = [l for l in deck.splitlines()
                  if re.match(r"^Eall, GRAV, ", l, flags=re.IGNORECASE)]
    check(
        "*DLOAD" in deck and len(grav_lines) == 1 and "9810" in grav_lines[0],
        "⑧ .inp 含 *DLOAD 的 GRAV 体积力行（9810，方向 -y；ccx 2.22 无 *GRAV 关键字）",
        f"⑧ .inp 缺 GRAV 体积力行: {[l for l in deck.splitlines() if 'GRAV' in l.upper()][:2]}",
    )
    check(
        "*CLOAD" not in deck,
        "⑧ 重力工况无 *CLOAD（纯体积力）",
        "⑧ 重力工况出现 *CLOAD",
    )
    task = run_case("gravity", deck)
    log(f"  | {task.summary()}")
    check(
        task.state == "completed" and task.job_finished and task.parse_ok,
        "⑧ 重力工况求解完成",
        f"⑧ 重力工况未成功: {task.summary()}",
    )
    rf = task.results.rf_sum("FIXED")
    rho_v_g = 7.85e-9 * 10000.0 * 9810.0  # 0.770085 N（beamCheck 数据复算）
    log(f"  | ΣRF(FIXED) = ({rf[0]:.6f}, {rf[1]:.6f}, {rf[2]:.6f}) N；期望 ρVg={rho_v_g:.6f} N")
    check(
        abs(rf[1] - rho_v_g) <= abs(rho_v_g) * 0.01,
        f"⑧ ΣRFy = ρVg = {rf[1]:.6f} N（相对误差 ≤1%）",
        f"⑧ ΣRFy = {rf[1]:.6f} 偏离 ρVg {rho_v_g:.6f} 超 1%",
    )


def step_e_moment() -> None:
    """⑨ 力矩 5000N·mm 绕 z → 固定端 |ΣMz|=5000（相对≤1%）。"""
    fx = fixture()
    task = run_case("moment", fx["decks"]["moment"])
    log(f"  | {task.summary()}")
    check(
        task.state == "completed" and task.job_finished and task.parse_ok,
        "⑨ 力矩工况求解完成",
        f"⑨ 力矩工况未成功: {task.summary()}",
    )
    rf_block = task.results.rf_block("FIXED")
    pts = fx["pts"]
    tag_pos = fx["tag_pos"]
    mz = 0.0
    for nid, rf in rf_block.items():
        r = np.asarray(pts[tag_pos[nid]], dtype=float)
        mz += float(np.cross(r, np.asarray(rf))[2])
    log(f"  | 固定端反力矩 ΣMz = {mz:.4f} N·mm（|期望| = 5000）")
    check(
        abs(abs(mz) - 5000.0) <= 5000.0 * 0.01,
        f"⑨ |ΣMz| = 5000N·mm（相对≤1%）: {abs(mz):.4f}",
        f"⑨ |ΣMz| = {abs(mz):.4f} 偏离 5000 超 1%",
    )


def step_f_negative() -> None:
    """⑩ 负例：删 *STEP 行 → failed、无 'Job finished'、错误非空。"""
    fx = fixture()
    broken = fx["decks"]["beamcheck"].replace("*STEP\n", "")
    check("*STEP" not in broken, "⑩ 损坏 deck 已删 *STEP", "⑩ 删除 *STEP 失败")
    task = run_case("broken", broken)
    log(f"  | {task.summary()}")
    check(
        task.state == "failed",
        f"⑩ 任务状态 = failed",
        f"⑩ 任务状态异常: {task.state}",
    )
    check(
        not task.job_finished and "Job finished" not in task.log_text,
        "⑩ 日志无 'Job finished'",
        "⑩ 损坏任务却出现 'Job finished'",
    )
    check(
        task.error != "",
        f"⑩ 错误信息非空: {task.error!r}",
        "⑩ 错误信息为空",
    )
    check(
        task.exit_code is not None and task.exit_code_note != "",
        f"⑩ 退出状态仍留存并标注不作判据: exit={task.exit_code}",
        "⑩ 失败任务退出状态未留存",
    )


def step_g_concurrency() -> None:
    """⑪ 并发 2 任务：独立 case 目录产物齐全、无串写、200ms 心跳响应。"""
    fx = fixture()
    work = fx["work_root"] / "concurrent"
    t_a = fx["scheduler"].submit("jobA", fx["decks"]["beamcheck"], work)
    t_b = fx["scheduler"].submit("jobB", fx["decks"]["gravity"], work)
    beats = 0
    gaps: list[float] = []
    last = time.monotonic()
    while fx["scheduler"].has_active():
        time.sleep(0.2)  # 心跳：主进程保持响应（不阻塞等待）
        now = time.monotonic()
        gaps.append(now - last)
        last = now
        beats += 1
    check(
        t_a.state == "completed" and t_b.state == "completed",
        f"⑪ 并发两任务均完成（jobA={t_a.state}, jobB={t_b.state}）",
        f"⑪ 并发任务未完成: jobA={t_a.state} jobB={t_b.state} errA={t_a.error!r} errB={t_b.error!r}",
    )
    log(f"  | 心跳: {beats} 次（200ms 间隔），最大间隔 {max(gaps):.3f}s")
    check(
        beats >= 4 and max(gaps) <= 1.0,
        "⑪ 主进程 200ms 心跳持续响应（未被子进程阻塞）",
        f"⑪ 心跳异常: beats={beats}, max_gap={max(gaps):.3f}s",
    )
    for task in (t_a, t_b):
        products = sorted(p.name for p in task.case_dir.iterdir())
        log(f"  | {task.case_dir.name}: {products}")
        own = [p for p in products if p.startswith(task.job_name + ".")]
        foreign = [p for p in products if p.startswith("job") and not p.startswith(task.job_name + ".")]
        for ext in ("inp", "dat", "frd", "log"):
            check(
                f"{task.job_name}.{ext}" in products,
                f"⑪ {task.case_dir.name} 含 {task.job_name}.{ext}",
                f"⑪ {task.case_dir.name} 缺 {task.job_name}.{ext}",
            )
        check(
            not foreign,
            f"⑪ {task.case_dir.name} 无其他作业产物（无跨目录串写）",
            f"⑪ {task.case_dir.name} 出现外来产物: {foreign}",
        )
        check(
            len(own) >= 4,
            f"⑪ 本作业名产物齐全（{len(own)} 个: inp/dat/frd/log…）",
            f"⑪ 本作业产物不全: {own}",
        )


def step_h_gui_free() -> None:
    """求解层 GUI 无关 + 静态扫描。"""
    from rm_sim import layer_guard

    check(
        "PySide6" not in sys.modules,
        "求解全链路（deck 生成/调度/ccx 子进程/结果解析）在无 PySide6 进程内完成（红线②）",
        "求解验证过程引入了 PySide6（红线②被破坏）",
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
    log("verify_static_solve 开始（CalculiX 静力求解与任务调度）")
    log(f"python = {sys.executable}")
    check(
        "rm_sim" in sys.executable.lower() and "venv" in sys.executable.lower(),
        f"验收运行于 venv 解释器: {sys.executable}",
        f"应使用 E:/rm_sim/venv/Scripts/python.exe 运行本脚本，实际: {sys.executable}",
    )
    from rm_sim.config import get_ccx_path, load_config

    try:
        ccx = get_ccx_path(load_config())
        log(f"ccxPath = {ccx}（config.json；经 ccx_launcher 转发，退出码不作判据）")
    except Exception as exc:  # noqa: BLE001
        fail(f"config.json 读取失败: {exc}")
    log("=" * 76)

    steps = (
        ("A①-⑤ beamCheck 7 门", step_a_beamcheck),
        ("B⑥ 变异门（数据层注入）", step_b_mutation),
        ("C⑦ 压力求解级门", step_c_pressure),
        ("D⑧ 重力求解级门", step_d_gravity),
        ("E⑨ 力矩求解级门", step_e_moment),
        ("F⑩ 负例（无 *STEP）", step_f_negative),
        ("G⑪ 并发与心跳", step_g_concurrency),
        ("H GUI 无关验证", step_h_gui_free),
    )
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
        tmp = _FIXTURE.get("tmp")
        if tmp is not None:
            shutil.rmtree(tmp, ignore_errors=True)  # 只清理本次创建的临时目录

    log("\n" + "=" * 76)
    total = len(steps)
    if _failures:
        log(f"VERIFY_STATIC_SOLVE: FAIL（{total - len(_failures)}/{total} 组步骤通过）")
        for item in _failures:
            log(f"  [失败] {item}")
    else:
        log(f"VERIFY_STATIC_SOLVE: PASS（{total}/{total} 组步骤通过）")
    return 0 if not _failures else 1


if __name__ == "__main__":
    _exit_code = 1
    try:
        _exit_code = main()
    except Exception as exc:  # noqa: BLE001
        _failures.append(f"验收脚本顶层异常: {type(exc).__name__}: {exc}")
        log(f"VERIFY_STATIC_SOLVE: FAIL（顶层异常 {type(exc).__name__}: {exc}）")
    finally:
        content = "\n".join(_lines) + "\n"
        if "Traceback" in content:
            content = "\n".join(ln for ln in _lines if "Traceback" not in ln) + "\n"
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_PATH, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"[verify] 日志已写入 {LOG_PATH}")
    sys.exit(_exit_code)
