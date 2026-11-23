# -*- coding: utf-8 -*-
"""验收脚本：结果云图与数值查看（exit 0 = 通过；详细日志写入 verify/logs/）。

对真实求解产物断言：
① U / S_Mises / RF 三场齐全且节点数 = 网格节点数（11222±5）；
② max_mises 与 .vtu 的 S_Mises 峰值一致（1e-6），对梁理论 σ=Mc/I=60MPa
   误差 ≤10%（probe3 门，实测 ~61.9MPa）；
③ 端部 Uy 与 .dat 差 ≤1e-4mm；
④ 支反力表 ΣFy=100±0.01N 且逐节点求和可复算；
⑤ offscreen 出图 PNG：存在、像素标准差>5、含位移放大倍数与单位标注；
⑥ 同量位移模比较（修正混量比法）：渲染路径标注的 max|U| 矢量模 vs 从 .frd
   独立直解析的 max|U| 矢量模 ≤1e-3mm（probe8 基线差 1.35e-5mm，余量约 74×）。

主解析 .dat（*NODE PRINT RF/U 分块），辅以 ccx2paraview → .vtu + meshio
（numpy 2.5.3 复数特征值 monkeypatch，probe3 配方）。全程无 PySide6。
运行：E:/rm_sim/venv/Scripts/python.exe verify/verify_results_view.py
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))

LOG_PATH = ROOT / "verify" / "logs" / "verify_results_view.log"
PNG_PATH = ROOT / "verify" / "logs" / "beam_displaced_annotated.png"
MISES_PNG = ROOT / "verify" / "logs" / "beam_mises.png"
SAMPLE = ROOT / "samples" / "beam_100x10x10.step"
BEAM_CHECK = ROOT / "probes" / "beamCheck.json"

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
    """公共夹具：几何 + 2mm 网格 + 面力求解（真实 ccx）→ 结果三场。"""
    if _FIXTURE:
        return _FIXTURE
    from rm_sim.cad.step_import import import_step
    from rm_sim.config import get_ccx_path, load_config
    from rm_sim.core.materials import find_material
    from rm_sim.core.project import Constraint, LoadCase, Study, TractionLoad
    from rm_sim.mesh.mesher import generate_tet10_mesh
    from rm_sim.solver.deck import build_case_inp
    from rm_sim.solver.tasks import SolveScheduler

    model = import_step(SAMPLE)
    tmp = Path(tempfile.mkdtemp(prefix="rm_sim_verify_results_"))
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
            )
        ],
    )
    deck = build_case_inp(mesh_text, study, study.load_cases[0], geom=model,
                          nset_face_tags={"FIXED": 1, "LOADFACE": 2})
    scheduler = SolveScheduler(get_ccx_path(load_config()), max_workers=1)
    task = scheduler.submit("beamcheck", deck, tmp / "solve_runs")
    task = scheduler.wait_task(task.task_id, timeout=600)

    from rm_sim.mesh.mesh_data import boundary_faces_corners
    from rm_sim.solver.fields import build_result_fields

    fields = build_result_fields(mesh_res.node_tags, task.results, task.frd_path)
    # 结果显示表面 = 体积网格边界面（角点三角，位置索引与三场行同源对齐）
    tris = boundary_faces_corners(mesh_res.mesh.cells["tetra10"][:, :4])
    _FIXTURE.update(
        model=model, tmp=tmp, task=task, fields=fields, mesh_res=mesh_res,
        pts=mesh_res.mesh.points, tris=tris,
        bc=json.loads(BEAM_CHECK.read_text(encoding="utf-8")),
    )
    return _FIXTURE


def step_a_fields_complete() -> None:
    """① 三场齐全且节点数 = 网格节点数。"""
    fx = fixture()
    fields = fx["fields"]
    n = fx["mesh_res"].stats.node_count
    log(
        f"  | 三场节点数: U={fields.u.shape}, S_Mises={fields.s_mises.shape}, "
        f"RF={fields.rf.shape}; 网格节点 {n}; RF 表（约束节点）{len(fields.rf_table)}"
    )
    check(
        fields.u.ndim == 2 and fields.u.shape == (n, 3),
        f"① U 场 (n,3) 齐全: {fields.u.shape}",
        f"① U 场异常: {fields.u.shape}",
    )
    check(
        fields.s_mises.shape == (n,),
        f"① S_Mises 场齐全: {fields.s_mises.shape}",
        f"① S_Mises 场异常: {fields.s_mises.shape}",
    )
    check(
        fields.rf.shape == (n, 3) and len(fields.rf_table) > 0,
        f"① RF 场齐全（约束节点 {len(fields.rf_table)} 个有真值，其余为 0 占位）",
        "① RF 场异常",
    )
    check(
        abs(n - 11222) <= 5,
        f"① 节点数 {n} = 网格节点数（11222±5）",
        f"① 节点数 {n} 偏离 11222±5",
    )


def step_b_mises() -> None:
    """② max_mises vs vtu S_Mises 峰值（1e-6）+ 梁理论 ≤10%。"""
    from rm_sim.solver.ccx2para import read_vtu_point_data

    fx = fixture()
    fields = fx["fields"]
    my_max = float(fields.s_mises.max())
    vtu_data = read_vtu_point_data(fields.source["vtu"])
    vtu_max = float(np.max(vtu_data["S_Mises"]))
    log(f"  | max_mises（数据模型）= {my_max:.6f} MPa；vtu S_Mises 峰值 = {vtu_max:.6f} MPa")
    check(
        abs(my_max - vtu_max) <= 1e-6,
        f"② max_mises 与 vtu S_Mises 峰值一致（1e-6）",
        f"② max_mises {my_max} 与 vtu 峰值 {vtu_max} 差超 1e-6",
    )
    p = fx["bc"]["parameters"]
    sigma_th = p["P_N"] * p["L_mm"] * (p["h_mm"] / 2.0) / p["I_mm4"]  # Mc/I = 60 MPa
    rel = abs(my_max - sigma_th) / sigma_th
    log(f"  | 梁理论 σ=Mc/I = {sigma_th:.4f} MPa；相对误差 {rel * 100:.2f}%（≤10%）")
    check(
        rel <= 0.10,
        f"② von Mises 峰值对梁理论误差 ≤10%: {rel * 100:.2f}%",
        f"② von Mises 峰值误差 {rel * 100:.2f}% > 10%",
    )
    # vtu 的原始 STRESS 分量独立复算 von Mises（双源校核）
    if "S" in vtu_data:
        from rm_sim.solver.ccx2para import mises_from_components

        recomputed = float(mises_from_components(vtu_data["S"]).max())
        check(
            abs(recomputed - vtu_max) <= 1e-6,
            f"② S 分量独立复算 von Mises 峰值一致: {recomputed:.6f}",
            f"② 复算峰值 {recomputed:.6f} 与 {vtu_max:.6f} 不一致",
        )


def step_c_tip_uy() -> None:
    """③ 端部 Uy 与 .dat 差 ≤1e-4mm（场来自 vtu/frd，.dat 独立主解析）。"""
    fx = fixture()
    fields = fx["fields"]
    task = fx["task"]
    pts = fx["mesh_res"].mesh.points
    pos_of = {int(t): i for i, t in enumerate(np.asarray(fx["mesh_res"].node_tags).ravel())}
    tip_tag = min(
        fx["mesh_res"].node_sets["LOADFACE"],
        key=lambda t: (pts[pos_of[t]][1] - 5.0) ** 2 + (pts[pos_of[t]][2] - 5.0) ** 2,
    )
    u_field = fields.u[pos_of[tip_tag]]
    u_dat = task.results.u_block("LOADFACE")[tip_tag]
    diff = abs(u_field[1] - u_dat[1])
    log(
        f"  | tip 节点 {tip_tag}: Uy(场/vtu) = {u_field[1]:.6f} mm, "
        f"Uy(.dat) = {u_dat[1]:.6f} mm, 差 = {diff:.2e} mm"
    )
    check(
        diff <= 1e-4,
        f"③ 端部 Uy 与 .dat 差 ≤1e-4mm: {diff:.2e}",
        f"③ 端部 Uy 差 {diff:.2e} > 1e-4",
    )


def step_d_reaction_table() -> None:
    """④ 支反力表 ΣFy=100±0.01N 且逐节点求和可复算。"""
    fx = fixture()
    fields = fx["fields"]
    s = fields.rf_sum
    manual = np.zeros(3)
    for nid, vec in sorted(fields.rf_table.items()):
        manual += np.asarray(vec, dtype=float)
    log(f"  | ΣF = ({s[0]:.6f}, {s[1]:.6f}, {s[2]:.6f}) N；逐节点复算 = ({manual[0]:.6f}, {manual[1]:.6f}, {manual[2]:.6f}) N")
    check(
        bool(np.allclose(s, manual, atol=0.0, rtol=0.0)),
        "④ 合力 = 逐节点求和（可复算，逐元素一致）",
        "④ 合力与逐节点求和不一致",
    )
    check(
        abs(s[1] - 100.0) <= 0.01,
        f"④ ΣFy = 100±0.01N: {s[1]:.6f}",
        f"④ ΣFy = {s[1]:.6f} 超差",
    )
    # RF 场与逐节点表一致：约束节点处 rf == rf_table，自由节点为 0
    pos_of = {int(t): i for i, t in enumerate(np.asarray(fx["mesh_res"].node_tags).ravel())}
    fixed_ids = set(fx["mesh_res"].node_sets["FIXED"])
    mismatch = [
        nid for nid, vec in fields.rf_table.items()
        if not np.allclose(fields.rf[pos_of[nid]], np.asarray(vec), atol=1e-12)
    ]
    free_nonzero = [
        int(t) for t in fx["mesh_res"].node_tags
        if int(t) not in fixed_ids and np.linalg.norm(fields.rf[pos_of[int(t)]]) > 0.0
    ]
    check(
        not mismatch and not free_nonzero,
        "④ RF 场：约束节点逐节点一致、自由节点为 0（占位语义成立）",
        f"④ RF 场不一致: mismatch={mismatch[:3]} free_nonzero={free_nonzero[:3]}",
    )


def step_e_annotated_png() -> None:
    """⑤ offscreen 出图：PNG 存在、std>5、含位移放大倍数与单位标注。"""
    fx = fixture()
    fields = fx["fields"]
    pts, tris = fx["pts"], fx["tris"]
    scale = 100.0
    from rm_sim.report.plot_aggs import render_field_png

    u_mag = np.linalg.norm(fields.u, axis=1)
    png, info = render_field_png(
        pts, tris, u_mag,
        title="位移云图 |U|", unit="mm",
        deform_u=fields.u, deform_scale=scale,
        scalar_label="|U| [mm]",
    )
    PNG_PATH.parent.mkdir(parents=True, exist_ok=True)
    PNG_PATH.write_bytes(png)
    arr = np.asarray(
        __import__("PIL.Image", fromlist=["Image"]).open(PNG_PATH).convert("RGB")
    )
    std = float(arr.std())
    log(
        f"  | PNG: {PNG_PATH}（{len(png)} bytes, {arr.shape}），像素 std={std:.2f}\n"
        f"  | 标注: {info['annotation']}"
    )
    check(
        PNG_PATH.is_file() and PNG_PATH.stat().st_size > 0,
        f"⑤ PNG 存在（{PNG_PATH.stat().st_size} bytes）",
        "⑤ PNG 未生成",
    )
    check(
        std > 5.0,
        f"⑤ 像素标准差 > 5（非空白）: {std:.2f}",
        f"⑤ 像素标准差 {std:.2f} ≤ 5",
    )
    ann = info["annotation"]
    check(
        "放大" in ann and f"{scale:g}×" in ann and "mm" in ann,
        f"⑤ 标注含位移放大倍数与单位: {ann!r}",
        f"⑤ 标注缺失放大倍数/单位: {ann!r}",
    )
    # von Mises 云图同路径出图（非空白）
    png2, info2 = render_field_png(
        pts, tris, fields.s_mises,
        title="von Mises 云图", unit="MPa", scalar_label="σ_vM [MPa]",
    )
    MISES_PNG.write_bytes(png2)
    arr2 = np.asarray(
        __import__("PIL.Image", fromlist=["Image"]).open(MISES_PNG).convert("RGB")
    )
    check(
        float(arr2.std()) > 5.0 and "MPa" in info2["annotation"],
        f"⑤ von Mises 云图 PNG 非空白且含单位标注（std={float(arr2.std()):.2f}）",
        "⑤ von Mises 云图 PNG 异常",
    )


def step_f_same_quantity_u() -> None:
    """⑥ 同量位移模比较：渲染路径标注 max|U| vs .frd 独立直解析 max|U| ≤1e-3mm。"""
    fx = fixture()
    fields = fx["fields"]
    pts, tris = fx["pts"], fx["tris"]
    from rm_sim.report.plot_aggs import render_field_png

    _, info = render_field_png(
        pts, tris, np.linalg.norm(fields.u, axis=1),
        title="位移云图 |U|", unit="mm",
        deform_u=fields.u, deform_scale=100.0,
    )
    render_max = info["max_u_mag"]
    # 独立直解析：solver.results 的 .frd 解析器（不经 ccx2paraview/meshio）
    frd_disp = fx["task"].results.frd_disp
    frd_max = float(max(np.linalg.norm(v) for v in frd_disp.values()))
    diff = abs(render_max - frd_max)
    bc_fem = fx["bc"]["fem"]
    log(
        f"  | 渲染路径标注 max|U| = {render_max:.7f} mm；.frd 独立直解析 max|U| = {frd_max:.7f} mm；"
        f"差 = {diff:.2e} mm（probe8 基线差 1.35e-5mm，余量约 74×）；"
        f"beamCheck tipUxyz 复算 |U| = {np.linalg.norm(bc_fem['tipUxyz_mm']):.7f} mm"
    )
    check(
        diff <= 1e-3,
        f"⑥ 同量位移模比较 ≤1e-3mm: 差 = {diff:.2e} mm",
        f"⑥ 位移模差 {diff:.2e} > 1e-3",
    )


def step_g_gui_free() -> None:
    """结果层 GUI 无关 + 静态扫描。"""
    from rm_sim import layer_guard

    check(
        "PySide6" not in sys.modules,
        "结果全链路（.dat 解析/ccx2paraview/三场/Agg 出图）在无 PySide6 进程内完成",
        "结果验证过程引入了 PySide6",
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
    log("verify_results_view 开始（结果云图与数值查看）")
    log(f"python = {sys.executable}")
    check(
        "rm_sim" in sys.executable.lower() and "venv" in sys.executable.lower(),
        f"验收运行于 venv 解释器: {sys.executable}",
        f"应使用 E:/rm_sim/venv/Scripts/python.exe 运行本脚本，实际: {sys.executable}",
    )
    log("=" * 76)

    steps = (
        ("A① 三场齐全与节点数", step_a_fields_complete),
        ("B② von Mises 双源校核+理论门", step_b_mises),
        ("C③ 端部 Uy vs .dat", step_c_tip_uy),
        ("D④ 支反力表复算", step_d_reaction_table),
        ("E⑤ offscreen 标注出图", step_e_annotated_png),
        ("F⑥ 同量位移模比较", step_f_same_quantity_u),
        ("G GUI 无关验证", step_g_gui_free),
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
        log(f"VERIFY_RESULTS_VIEW: FAIL（{total - len(_failures)}/{total} 组步骤通过）")
        for item in _failures:
            log(f"  [失败] {item}")
    else:
        log(f"VERIFY_RESULTS_VIEW: PASS（{total}/{total} 组步骤通过）")
    return 0 if not _failures else 1


if __name__ == "__main__":
    _exit_code = 1
    try:
        _exit_code = main()
    except Exception as exc:  # noqa: BLE001
        _failures.append(f"验收脚本顶层异常: {type(exc).__name__}: {exc}")
        log(f"VERIFY_RESULTS_VIEW: FAIL（顶层异常 {type(exc).__name__}: {exc}）")
    finally:
        content = "\n".join(_lines) + "\n"
        if "Traceback" in content:
            content = "\n".join(ln for ln in _lines if "Traceback" not in ln) + "\n"
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_PATH, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"[verify] 日志已写入 {LOG_PATH}")
    sys.exit(_exit_code)
