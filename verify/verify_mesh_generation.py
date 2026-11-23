# -*- coding: utf-8 -*-
"""验收脚本：自动网格划分与 INP 导出（exit 0 = 通过；详细日志写入 verify/logs/）。

覆盖验收标准（标准梁 MeshSize=2mm，probes/probe1+probe2 实证基线）：
① .inp 仅 C3D10，无 T3D3/CPS6/C3D4 行（gmshInpExportNote 三坑回归门）；
② 单元数 5000~9000（probe 基线 6481）、节点数 ≥9000（probe 基线 11222）；
③ 节点 bbox 还原 100×10×10（1e-6）；
④ 固定/加载端面 NSET 各含 153 节点（probes/sets.json 实证，容差 ±5；
   换单元类型/阶次/网格器必须重定基线）；
⑤ MeshSize=4mm 重划后单元数 < 2mm 的 0.5 倍（加密趋势反向门）；
⑥ 统计函数返回值与 .inp 实际一致；
⑦ 质量门：负 Jacobian 单元数=0、min SICN≥0.1（C3D10 标准梁）。

运行：E:/rm_sim/venv/Scripts/python.exe verify/verify_mesh_generation.py
本脚本全程无 GUI；导出的 2mm INP 留在 verify/logs/beam_mesh_2mm.inp 供查。
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

LOG_PATH = ROOT / "verify" / "logs" / "verify_mesh_generation.log"
INP_PATH = ROOT / "verify" / "logs" / "beam_mesh_2mm.inp"
SAMPLE = ROOT / "samples" / "beam_100x10x10.step"
SETS_JSON = ROOT / "probes" / "sets.json"

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
    """公共夹具：2mm 网格（导出 INP）+ 4mm 网格（加密趋势对照）。"""
    if _FIXTURE:
        return _FIXTURE
    from rm_sim.cad.step_import import import_step
    from rm_sim.mesh.mesher import generate_tet10_mesh

    model = import_step(SAMPLE)
    res2 = generate_tet10_mesh(
        model, 2.0, nset_faces={"FIXED": 1, "LOADFACE": 2}, inp_path=INP_PATH
    )
    res4 = generate_tet10_mesh(model, 4.0)
    _FIXTURE.update(model=model, res2=res2, res4=res4)
    return _FIXTURE


def parse_inp(text: str) -> dict:
    """解析 CalculiX INP 的 mesh 部分：节点/单元/NSET/ELSET。"""
    mode: str | None = None
    cur_set = ""
    out = {"nodes": {}, "elems": {}, "nsets": {}, "elsets": {}, "elem_types": []}
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("*"):
            upper = s.upper()
            mode = None
            if upper.startswith("*NODE"):
                mode = "NODE"
            elif upper.startswith("*ELEMENT"):
                mode = "ELEM"
                for part in s.split(",")[1:]:
                    key, _, val = part.partition("=")
                    if key.strip().lower() == "type":
                        out["elem_types"].append(val.strip().upper())
            elif upper.startswith("*NSET"):
                mode, cur_kind = "SET", "NSET"
                for part in s.split(",")[1:]:
                    key, _, val = part.partition("=")
                    if key.strip().upper() == "NSET":
                        cur_set = val.strip()
            elif upper.startswith("*ELSET"):
                mode, cur_kind = "SET", "ELSET"
                for part in s.split(",")[1:]:
                    key, _, val = part.partition("=")
                    if key.strip().upper() == "ELSET":
                        cur_set = val.strip()
            else:
                mode = None
            continue
        if not s:
            continue
        if mode == "NODE":
            parts = [p for p in s.split(",") if p.strip()]
            out["nodes"][int(parts[0])] = tuple(float(x) for x in parts[1:4])
        elif mode == "ELEM":
            parts = [p for p in s.split(",") if p.strip()]
            out["elems"][int(parts[0])] = [int(x) for x in parts[1:]]
        elif mode == "SET":
            ids = [int(x) for x in s.split(",") if x.strip()]
            out["nsets" if cur_kind == "NSET" else "elsets"].setdefault(cur_set, []).extend(ids)
    return out


def step_a_pit_regression() -> None:
    """① 三坑回归门：.inp 仅 C3D10，无 T3D3/CPS6/C3D4；ELSET=Eall；NSET 存在。"""
    fx = fixture()
    check(
        fx["res2"].inp_path is not None and Path(fx["res2"].inp_path).is_file(),
        f"① INP 已导出: {fx['res2'].inp_path}",
        "① INP 未导出",
    )
    text = Path(fx["res2"].inp_path).read_text(encoding="utf-8")
    upper = text.upper()
    for bad in ("T3D3", "C3D4", "CPS6"):
        check(
            bad not in upper,
            f"① .inp 无 {bad} 行（坑回归门）",
            f"① .inp 含 {bad}（zero-thickness 坑回归！）",
        )
    parsed = parse_inp(text)
    check(
        set(parsed["elem_types"]) == {"C3D10"} and len(parsed["elem_types"]) == 1,
        f"① *ELEMENT 类型仅 C3D10（1 个块）: {parsed['elem_types']}",
        f"① *ELEMENT 类型异常: {parsed['elem_types']}",
    )
    check(
        len(parsed["nodes"]) > 0 and "*NODE" in upper,
        "① *NODE 块存在",
        "① 缺少 *NODE 块",
    )
    check(
        len(parsed["elsets"].get("Eall", [])) == len(parsed["elems"]),
        f"① *ELSET,ELSET=Eall 含全部 {len(parsed['elems'])} 个单元",
        "① ELSET=Eall 与单元集不一致",
    )
    check(
        "FIXED" in parsed["nsets"] and "LOADFACE" in parsed["nsets"],
        "① *NSET,NSET=FIXED 与 *NSET,NSET=LOADFACE 存在（按节点坐标自建，不依赖物理组）",
        "① 缺少端面 NSET",
    )


def step_b_counts() -> None:
    """② 单元数 5000~9000、节点数 ≥9000（probe 基线 6481/11222）。"""
    fx = fixture()
    stats = fx["res2"].stats
    check(
        5000 <= stats.element_count <= 9000,
        f"② 单元数 {stats.element_count} ∈ [5000, 9000]（probe 基线 6481）",
        f"② 单元数 {stats.element_count} 超出 [5000, 9000]",
    )
    check(
        stats.node_count >= 9000,
        f"② 节点数 {stats.node_count} ≥ 9000（probe 基线 11222）",
        f"② 节点数 {stats.node_count} < 9000",
    )


def step_c_node_bbox() -> None:
    """③ 节点 bbox 还原 100×10×10（1e-6）。"""
    fx = fixture()
    text = Path(fx["res2"].inp_path).read_text(encoding="utf-8")
    nodes = parse_inp(text)["nodes"]
    pts = np.asarray([nodes[k] for k in sorted(nodes)], dtype=float)
    lo = pts.min(axis=0)
    hi = pts.max(axis=0)
    extents = hi - lo
    log(
        f"  | .inp 节点 bbox: ({lo[0]:.6g},{lo[1]:.6g},{lo[2]:.6g})-"
        f"({hi[0]:.6g},{hi[1]:.6g},{hi[2]:.6g}), {len(nodes)} 节点"
    )
    check(
        bool(np.all(np.abs(extents - np.array([100.0, 10.0, 10.0])) <= 1e-6)),
        f"③ 节点 bbox = 100×10×10（1e-6）: {extents}",
        f"③ 节点 bbox 超差: {extents}",
    )
    check(
        bool(np.all(np.abs(lo) <= 1e-6)),
        f"③ bbox 最小角在原点（1e-6）: {lo}",
        f"③ bbox 最小角超差: {lo}",
    )


def step_d_nsets() -> None:
    """④ 固定/加载端面 NSET 各 153 节点（±5，probes/sets.json 基线）+ 坐标值校验。"""
    fx = fixture()
    text = Path(fx["res2"].inp_path).read_text(encoding="utf-8")
    parsed = parse_inp(text)
    nsets = parsed["nsets"]
    nodes = parsed["nodes"]
    baseline = json.loads(SETS_JSON.read_text(encoding="utf-8"))
    expect_fixed = len(baseline["fixedNodes"])
    expect_load = len(baseline["loadFaceNodes"])
    log(
        f"  | NSET 节点数: FIXED={len(nsets.get('FIXED', []))}, "
        f"LOADFACE={len(nsets.get('LOADFACE', []))}；probes/sets.json 基线: "
        f"{expect_fixed}/{expect_load}"
    )
    for name, expect in (("FIXED", expect_fixed), ("LOADFACE", expect_load)):
        got = len(nsets.get(name, []))
        check(
            abs(got - expect) <= 5,
            f"④ NSET {name} = {got} 节点（基线 {expect} ±5）",
            f"④ NSET {name} = {got}，偏离基线 {expect}±5",
        )
    # 值校验（模块 7 实证教训：个数对但 id 错位/含 0 会令 ccx 挂死）：
    # FIXED 节点应落在 x≈0 平面，LOADFACE 节点在 x≈100 平面，且 id ≥ 1
    for name, plane_x in (("FIXED", 0.0), ("LOADFACE", 100.0)):
        ids = nsets.get(name, [])
        bad_id = [i for i in ids if i < 1]
        off_plane = [
            i for i in ids
            if abs(nodes[i][0] - plane_x) > 1e-6 or i not in nodes
        ]
        check(
            not bad_id and not off_plane,
            f"④ NSET {name} 节点 id 合法且全部位于 x={plane_x:g} 平面（值校验）",
            f"④ NSET {name} 含非法/偏面节点: bad_id={bad_id[:5]} off_plane={off_plane[:5]}",
        )
    # 与 probes/sets.json 的节点 id 集合一致（同配方应逐 id 相同）
    check(
        sorted(nsets.get("FIXED", [])) == sorted(baseline["fixedNodes"])
        and sorted(nsets.get("LOADFACE", [])) == sorted(baseline["loadFaceNodes"]),
        "④ NSET 节点 id 集合与 probes/sets.json 逐 id 一致",
        "④ NSET 节点 id 集合与基线不同（配方漂移，须重定基线）",
    )
    # 换基线提示（验收原文）：换单元类型/阶次/网格器必须重定基线
    log("  | 注：基线对应 C3D10/order2/gmsh 4.15.2/MeshSizeMin=Max=2；换元/换阶/换网格器须重定")


def step_e_refinement_trend() -> None:
    """⑤ MeshSize=4mm → 单元数 < 2mm 的 0.5 倍。"""
    fx = fixture()
    n2 = fx["res2"].stats.element_count
    n4 = fx["res4"].stats.element_count
    log(f"  | 2mm: {n2} 单元; 4mm: {n4} 单元（阈值 < {n2 * 0.5:g}）")
    check(
        n4 < 0.5 * n2,
        f"⑤ 加密趋势反向门：4mm 单元数 {n4} < 0.5×2mm（{0.5 * n2:g}）",
        f"⑤ 4mm 单元数 {n4} 未小于 0.5×2mm（{0.5 * n2:g}）",
    )
    from rm_sim.mesh.mesher import DEFAULT_MESH_SIZE_MM, MESH_PRESETS

    check(
        MESH_PRESETS == {"coarse": 4.0, "medium": 2.0, "fine": 1.0}
        and DEFAULT_MESH_SIZE_MM == 2.0,
        f"⑤ 档位 粗4/中2(默认)/细1: {MESH_PRESETS}",
        f"⑤ 档位定义异常: {MESH_PRESETS}",
    )


def step_f_stats_match_inp() -> None:
    """⑥ 统计函数返回值与 .inp 实际一致。"""
    fx = fixture()
    stats = fx["res2"].stats
    text = Path(fx["res2"].inp_path).read_text(encoding="utf-8")
    parsed = parse_inp(text)
    check(
        stats.element_count == len(parsed["elems"]),
        f"⑥ stats.element_count == .inp 单元数: {stats.element_count}",
        f"⑥ 单元数不一致: stats={stats.element_count} inp={len(parsed['elems'])}",
    )
    check(
        stats.node_count == len(parsed["nodes"]),
        f"⑥ stats.node_count == .inp 节点数: {stats.node_count}",
        f"⑥ 节点数不一致: stats={stats.node_count} inp={len(parsed['nodes'])}",
    )
    check(
        stats.element_type == "C3D10" and set(parsed["elem_types"]) == {"C3D10"},
        "⑥ stats.element_type 与 .inp 单元类型一致（C3D10）",
        "⑥ 单元类型不一致",
    )
    check(
        stats.mesh_size_mm == 2.0 and stats.order == 2,
        f"⑥ stats 记录 MeshSize={stats.mesh_size_mm:g}mm / order={stats.order}",
        "⑥ stats 尺寸/阶次记录异常",
    )
    # C3D10 连接表逐单元 10 节点
    bad_conn = [eid for eid, conn in parsed["elems"].items() if len(conn) != 10]
    check(
        not bad_conn,
        "⑥ 全部 C3D10 连接表为 10 节点",
        f"⑥ {len(bad_conn)} 个单元连接表节点数≠10，例: {bad_conn[:5]}",
    )


def step_g_quality_gate() -> None:
    """⑦ 质量门：负 Jacobian=0、min SICN≥0.1。"""
    fx = fixture()
    stats = fx["res2"].stats
    log(
        f"  | 质量: 负 Jacobian 单元 {stats.negative_jacobian_count} 个, "
        f"min SICN {stats.min_sicn:.4f}, mean SICN {stats.mean_sicn:.4f}"
        "（getElementQualities: minDetJac/minSICN）"
    )
    check(
        stats.negative_jacobian_count == 0,
        "⑦ 负 Jacobian 单元数 = 0",
        f"⑦ 存在 {stats.negative_jacobian_count} 个负 Jacobian 单元",
    )
    check(
        stats.min_sicn >= 0.1,
        f"⑦ min SICN = {stats.min_sicn:.4f} ≥ 0.1",
        f"⑦ min SICN {stats.min_sicn:.4f} < 0.1",
    )
    res4 = fx["res4"].stats
    check(
        res4.negative_jacobian_count == 0 and res4.min_sicn >= 0.1,
        f"⑦ 4mm 档同样过质量门（{res4.element_count} 单元, min SICN {res4.min_sicn:.4f}）",
        f"⑦ 4mm 档质量门失败: neg={res4.negative_jacobian_count}, minSICN={res4.min_sicn:.4f}",
    )


def step_h_gui_free() -> None:
    """网格层 GUI 无关：本进程全程未导入 PySide6；静态扫描。"""
    from rm_sim import layer_guard

    check(
        "PySide6" not in sys.modules,
        "网格全链路（生成/质量/过滤/INP 导出/NSET）在无 PySide6 进程内完成",
        "网格验证过程引入了 PySide6",
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
    log("verify_mesh_generation 开始（自动网格划分与 INP 导出）")
    log(f"python = {sys.executable}")
    check(
        "rm_sim" in sys.executable.lower() and "venv" in sys.executable.lower(),
        f"验收运行于 venv 解释器: {sys.executable}",
        f"应使用 E:/rm_sim/venv/Scripts/python.exe 运行本脚本，实际: {sys.executable}",
    )
    log("基线：probes/probe1（6481 C3D10 / 11222 节点）+ probes/sets.json（153/153 节点）")
    log("=" * 76)

    steps = (
        ("A① 三坑回归门（仅 C3D10）", step_a_pit_regression),
        ("B② 单元/节点数", step_b_counts),
        ("C③ 节点 bbox", step_c_node_bbox),
        ("D④ 端面 NSET 基线", step_d_nsets),
        ("E⑤ 加密趋势反向门", step_e_refinement_trend),
        ("F⑥ 统计与 INP 一致", step_f_stats_match_inp),
        ("G⑦ 质量门", step_g_quality_gate),
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

    log("\n" + "=" * 76)
    total = len(steps)
    if _failures:
        log(f"VERIFY_MESH_GENERATION: FAIL（{total - len(_failures)}/{total} 组步骤通过）")
        for item in _failures:
            log(f"  [失败] {item}")
    else:
        log(f"VERIFY_MESH_GENERATION: PASS（{total}/{total} 组步骤通过）")
    return 0 if not _failures else 1


if __name__ == "__main__":
    _exit_code = 1
    try:
        _exit_code = main()
    except Exception as exc:  # noqa: BLE001
        _failures.append(f"验收脚本顶层异常: {type(exc).__name__}: {exc}")
        log(f"VERIFY_MESH_GENERATION: FAIL（顶层异常 {type(exc).__name__}: {exc}）")
    finally:
        content = "\n".join(_lines) + "\n"
        if "Traceback" in content:
            content = "\n".join(ln for ln in _lines if "Traceback" not in ln) + "\n"
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_PATH, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"[verify] 日志已写入 {LOG_PATH}")
    sys.exit(_exit_code)
