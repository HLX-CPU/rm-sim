# -*- coding: utf-8 -*-
"""整装模态输入编译与结果契约（交接书 §5-B 接续点：不调引擎的部分）。

prepare()：读准备包（load_preparation_package 既有校验链 advanced_plan.py）→
校验几何/材料/边界（固定边界用工程约束面 NSET）→ 网格化并生成 CalculiX
*FREQUENCY 输入卡片（把已验证的单零件链路 solver/modal.py 推广到多部件：
多材料密度行 + 粘接 *TIE 边界，语法同既有实跑实证卡型）。
run()/collect() 未接引擎：保持 BackendNotConnected；全程零求解进程
（gmsh/OCC 为进程内库，非求解进程）。

当前编译范围（如实）：单零件与多部件装配；连接仅支持粘接（tie=*TIE 卡 /
merge=共节点）与销轴零间隙绑定（*TIE）——接触（*CONTACT PAIR+*FRICTION）
与螺栓（*RIGID BODY+预紧力对）属非线性/预紧卡型，线性特征值模态不支持，
显式拒绝。结果 schema = advanced_plan.RESULTS['modal']
（frequency_hz / normalized_mode_shapes / eigen_residuals）；归一化口径与
已验证单零件链路一致（逐阶振型最大位移点范数 = 1）。
"""
from __future__ import annotations

import datetime
import hashlib
import json
import re
import subprocess
from pathlib import Path

import numpy as np

from .advanced_plan import AdvancedBackend, RESULTS

RESULT_CONTRACT = ("frequency_hz", "normalized_mode_shapes", "eigen_residuals")
EIGEN_RESIDUAL_LIMIT = 1e-8
NORMALIZATION_TOLERANCE = 1e-6


def modal_results_payload(frequency_hz, mode_shapes, eigen_residuals) -> dict:
    """按 RESULTS['modal'] 契约组装结果 dict（collect 复用；数值转 float）。"""
    return {"frequency_hz": [float(v) for v in frequency_hz],
            "normalized_mode_shapes": np.asarray(mode_shapes, dtype=float).tolist(),
            "eigen_residuals": [float(v) for v in eigen_residuals]}


def validate_modal_results(payload, *, modes=None, n_nodes=None,
                           rigid_modes: int = 0, rigid_freq_tol: float = 1e-2) -> None:
    """机判校验器：payload 必须严格满足 RESULTS['modal'] 契约，否则 ValueError。

    - frequency_hz：长度=模态数的有限正数数组（负/零特征值拒绝）；
    - normalized_mode_shapes：形状 (modes, n_nodes, 3) 的有限数组，逐阶最大
      位移点范数 = 1（|max-1|≤1e-6，与 solver/modal.py 归一口径一致）；
    - eigen_residuals：长度=模态数，全部 |值| < 1e-8。
    modes / n_nodes 未给定时以数组实际维度为准；给定时必须一致。
    rigid_modes（模块56 free_free）：前 rigid_modes 阶为刚体模态——频率只需
    |f|≤rigid_freq_tol（自由悬浮求解噪声口径，实测 0~5.7e-3 Hz）、残差豁免
    <1e-8 门（如实记录原值）；弹性阶仍严格正频率+残差<1e-8。默认 0 保持
    既有严格语义（模块54 负例语义不变）。
    """
    if not isinstance(payload, dict):
        raise ValueError("模态结果契约不满足：payload 必须是 dict")
    if set(payload.keys()) != set(RESULT_CONTRACT):
        raise ValueError(f"模态结果契约不满足：键必须恰为 {list(RESULT_CONTRACT)}，"
                         f"实际 {sorted(payload.keys())}")
    freq = np.asarray(payload["frequency_hz"], dtype=float)
    if freq.ndim != 1 or freq.size == 0:
        raise ValueError(f"模态结果契约不满足：frequency_hz 必须是一维非空数组，实际形状 {freq.shape}")
    if not np.all(np.isfinite(freq)):
        raise ValueError("模态结果契约不满足：frequency_hz 含 NaN/Inf")
    count = int(freq.size)
    if modes is not None and count != int(modes):
        raise ValueError(f"模态结果契约不满足：频率数组长度 {count} ≠ 模态数 {modes}")
    rigid_modes = max(0, min(int(rigid_modes), count))
    if rigid_modes:
        head = freq[:rigid_modes]
        over = [int(i + 1) for i, v in enumerate(head) if abs(float(v)) > rigid_freq_tol]
        if over:
            raise ValueError(f"模态结果契约不满足：前 {rigid_modes} 阶应为刚体模态"
                             f"（|f|≤{rigid_freq_tol} Hz），越界阶次 {over}")
        if np.any(freq[rigid_modes:] <= 0):
            raise ValueError("模态结果契约不满足：弹性阶特征值必须为正")
    elif np.any(freq <= 0):
        raise ValueError(f"模态结果契约不满足：特征值必须为正（负/零特征值拒绝），实际 {freq[freq <= 0].tolist()}")
    try:
        shapes = np.asarray(payload["normalized_mode_shapes"], dtype=float)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"模态结果契约不满足：normalized_mode_shapes 无法转为数组: {exc}") from exc
    if shapes.ndim != 3 or shapes.shape[2] != 3:
        raise ValueError(f"模态结果契约不满足：振型矩阵形状必须是 (modes, n_nodes, 3)，实际 {shapes.shape}")
    if shapes.shape[0] != count or (modes is not None and shapes.shape[0] != int(modes)):
        raise ValueError(f"模态结果契约不满足：振型阶数 {shapes.shape[0]} ≠ 频率数 {count}")
    if n_nodes is not None and shapes.shape[1] != int(n_nodes):
        raise ValueError(f"模态结果契约不满足：振型节点数 {shapes.shape[1]} ≠ 网格节点数 {n_nodes}")
    if not np.all(np.isfinite(shapes)):
        raise ValueError("模态结果契约不满足：振型矩阵含 NaN/Inf")
    if shapes.shape[0]:
        peaks = np.linalg.norm(shapes, axis=2).max(axis=1)
        bad = [int(i + 1) for i, p in enumerate(peaks) if abs(float(p) - 1.0) > NORMALIZATION_TOLERANCE]
        if bad:
            raise ValueError(f"模态结果契约不满足：归一化振型逐阶最大位移点范数应为 1，"
                             f"越界阶次 {bad}（容差 {NORMALIZATION_TOLERANCE}）")
    resid = np.asarray(payload["eigen_residuals"], dtype=float)
    if resid.ndim != 1 or resid.shape[0] != count:
        raise ValueError(f"模态结果契约不满足：eigen_residuals 长度 {resid.shape} ≠ 模态数 {count}")
    if not np.all(np.isfinite(resid)):
        raise ValueError("模态结果契约不满足：eigen_residuals 含 NaN/Inf")
    elastic_over = [int(i + 1) for i, v in enumerate(resid)
                    if i >= rigid_modes and abs(float(v)) >= EIGEN_RESIDUAL_LIMIT]
    if elastic_over:
        raise ValueError(f"模态结果契约不满足：弹性阶特征值残差必须全部 < {EIGEN_RESIDUAL_LIMIT}，"
                         f"越界阶次 {elastic_over}（值 {resid[elastic_over].tolist()}）")


def _reject_unsupported_connections(project) -> None:
    """模态线性特征值只支持 *TIE 粘接（tie/merge）与销轴零间隙绑定；fail fast。"""
    from ..core.connections import BoltConnection, ContactFrictionConnection
    bad = [c.name for c in project.connections
           if isinstance(c, (ContactFrictionConnection, BoltConnection))]
    if bad:
        raise ValueError(f"整装模态编译不支持接触/螺栓连接（{bad}）：接触可分离、螺栓含预紧力对，"
                         "线性特征值模态不支持这两类卡型——请先移除或改粘接")


def _write_display_mesh(case, mesh, model) -> Path:
    """显示用网格包（模块56 点面卡片）：边界三角形 + 逐三角形 occ 面 tag
    （三角形质心 pick_face，模块2 机制；PickError→-1 无归属，如实降级）。"""
    from ..mesh.mesh_data import boundary_faces_corners
    ids = sorted(mesh["nodes"])
    rows = {n: i for i, n in enumerate(ids)}
    points = np.array([mesh["nodes"][n] for n in ids], dtype=float)
    tris = boundary_faces_corners(np.array(
        [[rows[n] for n in mesh["elems"][e][:4]] for e in sorted(mesh["elems"])], dtype=np.int64))
    tags = np.empty(len(tris), dtype=np.int64)
    for i, tri in enumerate(tris):
        centroid = points[tri].mean(axis=0)
        try:
            pick = model.pick_face((float(centroid[0]), float(centroid[1]), float(centroid[2])))
            tags[i] = int(pick.face.tag)  # FacePick(face, distance, closest_point)
        except Exception:
            tags[i] = -1
    path = Path(case) / "display_mesh.npz"
    if len(tris) and int((tags == -1).sum()) == len(tris):
        raise RuntimeError("显示网格面归属全部失败（pick_face 无一命中）——点面卡片不可用，拒绝静默降级")
    np.savez_compressed(path, node_ids=np.asarray(ids, dtype=np.int64), points=points,
                        tris=np.asarray(tris, dtype=np.int64), tri_face_tags=tags)
    return path


def compile_modal_input(package, *, progress=None) -> dict:
    """准备包 → *FREQUENCY 输入编译（零求解进程）。返回 prepared dict 供 run() 使用。

    有固定约束 → 固定路径（约束面 NSET + *BOUNDARY，模块54/55 口径）；
    无固定约束 → free_free 自由悬浮路径（无 *BOUNDARY，前 6 阶刚体模态，
    模块56）。两条路径同出一套网格/材料/连接机制。"""
    from .advanced_plan import load_preparation_package

    if progress: progress("load_package")
    loaded = load_preparation_package(package)
    project = loaded["project"]
    study = next((s for s in project.advanced_studies if s.enabled and s.kind == "modal"), None)
    if study is None:
        raise ValueError("准备包中没有已启用的 modal 研究，无法编译模态输入")
    modes = int(study.settings.modes)
    if not 1 <= modes <= 30:
        raise ValueError(f"模态数应在1到30之间，实际 {modes}")
    _reject_unsupported_connections(project)
    geometry = Path(project.study.geometry_file) if project.study.geometry_file else None
    if geometry is None or not geometry.is_file():
        raise ValueError(f"准备包几何快照缺失: {geometry}")

    from ..core.materials import part_material, solver_material_dict
    for part in project.parts:
        material = part_material(project, part)
        if not (material.density_t_mm3 and float(material.density_t_mm3) > 0):
            raise ValueError(f"部件 {part.part_id}（{part.name}）材料 {material.name} 密度非正：模态计算需要质量")
    # 模块56：free_free 由 modal 研究设置显式声明（ModalSettings.boundary，
    # core/advanced.py 既有字段；validate_studies 对 free_free 不要求固定面）。
    # project_fixed → 固定路径（约束面 NSET）；free_free → 无 *BOUNDARY，
    # 前 6 阶刚体模态。载荷不进入模态（两条路径一致）。
    free_free = (study.settings.boundary == "free_free")

    out_root = Path(package) / "modal_compile"
    out_root.mkdir(exist_ok=True)
    index = 1
    while (out_root / f"case_{index:03d}").exists():
        index += 1
    case = out_root / f"case_{index:03d}"
    case.mkdir()

    if progress: progress("import_geometry")
    from ..cad.assembly_import import import_assembly
    model = import_assembly(geometry)
    try:
        assembly = len(project.parts) > 1
        if progress: progress("meshing")
        from .assembly_workflow import prepare_analysis
        if free_free:
            # mesh_only：跳过静力工况校验（free_free 无外部固定面是合法模态工况）；
            # 网格配方与固定路径完全一致（同一 prepare_analysis 入口）。
            prepared = prepare_analysis(project, model, case / "mesh.inp", mesh_only=True, progress=progress)
            from .deck import parse_mesh_inp, _connection_cards
            mesh_text = (case / "mesh.inp").read_text(encoding="utf-8")
            mesh = parse_mesh_inp(mesh_text)
            conn_cards, preload_cload, has_contact = _connection_cards(project, model, mesh)
            if has_contact or preload_cload:
                raise ValueError("free_free 编译不支持接触/预紧卡型（连接类型预检已拦，此处防御复核）")
            lines = [mesh_text.rstrip("\n"), "", "** ===== 部件材料与截面（free_free 模态：密度行必需） ====="]
            if assembly:
                for k, part in enumerate(project.parts, start=1):
                    elset_name = f"PART{k}"
                    if elset_name not in mesh["elsets"] or not mesh["elsets"][elset_name]:
                        raise ValueError(f"网格段缺少部件 ELSET {elset_name}（装配网格须以 part_elsets 生成）")
                    m = part_material(project, part)
                    mat = solver_material_dict(m)
                    mname = "MAT_" + re.sub(r"[^A-Za-z0-9_]", "_", part.part_id or f"p{k}")
                    lines += [f"** 部件 {part.part_id}（{part.name}）材料 {m.name}",
                              f"*MATERIAL, NAME={mname}", "*ELASTIC",
                              f"{mat['E_MPa']:.12g}, {mat['nu']:.12g}", "*DENSITY",
                              f"{mat['rho_t_mm3']:.12g}",
                              f"*SOLID SECTION, ELSET={elset_name}, MATERIAL={mname}"]
            else:
                m = part_material(project, project.parts[0]) if project.parts else None
                if m is None:
                    raise ValueError("free_free 单零件模态需要材料")
                mat = solver_material_dict(m)
                mname = "MAT_" + re.sub(r"[^A-Za-z0-9_]", "_", project.parts[0].part_id or "p1")
                lines += [f"** 材料 {m.name}", f"*MATERIAL, NAME={mname}", "*ELASTIC",
                          f"{mat['E_MPa']:.12g}, {mat['nu']:.12g}", "*DENSITY",
                          f"{mat['rho_t_mm3']:.12g}",
                          f"*SOLID SECTION, ELSET=Eall, MATERIAL={mname}"]
            lines += conn_cards
            lines += ["**", "** free_free：无 *BOUNDARY——前 6 阶为刚体模态（整体平动/转动）",
                      "*STEP", "*FREQUENCY", f"{modes}", "*NODE FILE", "U", "*END STEP", ""]
            deck = "\n".join(lines)
            fixed_sets, nset_face_tags = [], {}
        else:
            prepared = prepare_analysis(project, model, case / "mesh.inp", progress=progress)
            deck_static = prepared["deck"]
            if "\n*STEP\n" not in deck_static:
                raise RuntimeError("deck 缺少 *STEP 分隔，无法改造为模态步")
            pre_step = deck_static.split("\n*STEP\n")[0]
            # 静态步残留守卫（防御性）：前置段不得含静态载荷/非线性/预紧卡型——
            # 模态输入 = 网格 + 材料（含密度）+ 连接卡（*TIE）+ 边界 + *FREQUENCY 步。
            for token in ("*STATIC", "*CLOAD", "*DLOAD", "*CONTACT PAIR", "*RIGID BODY", "*FRICTION"):
                if token in pre_step:
                    raise ValueError(f"模态输入前置段含不支持/不属于模态的卡 {token}；"
                                     "当前编译范围=粘接（*TIE/共节点）+固定边界")
            deck = pre_step + f"\n*STEP\n*FREQUENCY\n{modes}\n*NODE FILE\nU\n*END STEP\n"
            fixed_sets = list(prepared["fixed_sets"])
            nset_face_tags = {k: int(v) for k, v in prepared["nset_faces"].items()}
            mesh = prepared["mesh"]
        inp_path = case / "modal.inp"
        inp_path.write_text(deck, encoding="utf-8")

        parts_info = []
        for k, part in enumerate(project.parts, start=1):
            material = part_material(project, part)
            mat = solver_material_dict(material)
            parts_info.append({"part_id": part.part_id, "name": part.name,
                               "elset": f"PART{k}" if assembly else "Eall",
                               "material": material.name, "e_mpa": float(mat["E_MPa"]),
                               "nu": float(mat["nu"]), "rho_t_mm3": float(mat["rho_t_mm3"]),
                               "density_line": f"{mat['rho_t_mm3']:.12g}"})
        tie_cards = len(re.findall(r"^\*TIE[,\s]", deck, flags=re.MULTILINE))
        merge_mode = bool(project.connections) and all(
            getattr(c, "mode", "tie") == "merge" for c in project.connections)
        node_count = len(mesh["nodes"])
        display_path = _write_display_mesh(case, mesh, model)
        summary = {
            "version": "modal-compile@1",
            "created": datetime.datetime.now().isoformat(timespec="seconds"),
            "package": str(package),
            "study_id": study.id,
            "modes": modes,
            "inp": str(inp_path),
            "deck_sha256": hashlib.sha256(inp_path.read_bytes()).hexdigest(),
            "node_count": node_count,
            "n_nodes": node_count,
            "element_count": int(prepared["mesh_result"].stats.element_count),
            "parts": parts_info,
            "assembly": bool(assembly),
            "free_free": bool(free_free),
            "fixed_nsets": list(fixed_sets),
            "nset_face_tags": dict(nset_face_tags),
            "display_mesh": str(display_path),
            "tie_cards": tie_cards,
            "bond_mode": "tie" if tie_cards else ("merge" if merge_mode else "none"),
            "result_contract": list(RESULTS["modal"]),
            "engine_connected": False,
            "solved": False,
            "scope": ("输入编译完成；未求解；无频率/振型/残差结果。静力载荷不进入模态。"
                      + ("free_free：无固定约束，前 6 阶为刚体模态。" if free_free else "")),
        }
        (case / "prepare_manifest.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        if progress: progress("compiled")
        return summary
    finally:
        model.close()


class ModalBackend(AdvancedBackend):
    """模态适配器：prepare=输入编译（模块54）；run/collect=真实 ccx 求解与
    契约解析（模块55 接通）。engine_connected=True——能力位随真实后端接线翻转，
    GUI 求解按钮与各门禁据此判定；工作台求解编排（提交/进度/取消 UI）尚未接线，
    按钮文案如实区分（advanced_dialog._update_solve_button）。"""

    engine_connected = True

    def prepare(self, package, *, progress=None):
        return compile_modal_input(package, progress=progress)

    def run(self, prepared, cancel_event=None, progress=None):
        return run_modal_solve(prepared, cancel_event=cancel_event, progress=progress)

    def collect(self, run, *, progress=None):
        return collect_modal_results(run, progress=progress)


def run_modal_solve(prepared, cancel_event=None, progress=None) -> dict:
    """prepared（compile_modal_input 产物）→ 真实 ccx 求解（模态步）。

    求解器路径与成败判定沿用既有约定：config ccxPath（ccx_launcher.exe 透传）、
    二进制捕获、『Job finished』为成败门（solver/tasks.py 与 modal.py 同口径）；
    退出码不作判据。返回 run 工件 dict 供 collect 解析。
    """
    from ..config import get_ccx_path, load_config

    if cancel_event is not None and cancel_event.is_set():
        raise RuntimeError("模态求解已取消")
    inp = Path(prepared["inp"])
    case = inp.parent
    modes = int(prepared["modes"])
    if progress: progress("solving")
    started = datetime.datetime.now()
    proc = subprocess.run([get_ccx_path(load_config()), inp.stem], cwd=str(case),
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=900)
    (case / "modal.log").write_bytes(proc.stdout + proc.stderr)
    log_text = (proc.stdout + proc.stderr).decode("utf-8", errors="replace")
    job_finished = "Job finished" in log_text
    if not job_finished:
        raise RuntimeError(f"模态求解失败（日志无 Job finished），详见 {case / 'modal.log'}")
    if cancel_event is not None and cancel_event.is_set():
        raise RuntimeError("模态求解已取消")
    return {"case_dir": str(case), "inp": str(inp),
            "dat": str(case / "modal.dat"), "frd": str(case / "modal.frd"),
            "log": str(case / "modal.log"), "job": inp.stem,
            "exit_code": int(proc.returncode), "job_finished": True,
            "modes": modes, "node_count": int(prepared["node_count"]),
            "fixed_nsets": list(prepared["fixed_nsets"]),
            "free_free": bool(prepared.get("free_free")),
            "display_mesh": prepared.get("display_mesh"),
            "study_id": prepared.get("study_id"), "package": prepared.get("package"),
            "started": started.isoformat(timespec="seconds"),
            "elapsed_s": round((datetime.datetime.now() - started).total_seconds(), 3)}


def collect_modal_results(run, *, progress=None) -> dict:
    """run 工件 → RESULTS['modal'] 契约解析 + 独立核验证据 + 历史记录。

    解析复用 modal.py 既有 parse_frequencies/parse_mode_shapes；机判
    validate_modal_results（频率正、振型归一、残差<1e-8；free_free 时前 6 阶
    为刚体模态——|f|≤1e-2 Hz 宽容口径、残差如实记录）；固定 NSET 边界判据与
    verify_modal_analysis 同口径（振型在固定节点幅值 <1e-8×峰值；free_free
    无固定 NSET 则跳过）。状态推进：configured_not_computed →
    computed_with_evidence，落 case 目录 verification.json + run_history.json
    （状态字段如实标注）+ modes_display.npz（查看仪表数据包，模块56）。
    """
    if not run.get("job_finished"):
        raise RuntimeError("run 未完成（无 Job finished），无结果可解析")
    case = Path(run["case_dir"])
    modes = int(run["modes"])
    free_free = bool(run.get("free_free"))
    rigid_modes = min(6, modes) if free_free else 0  # 三维体刚体自由度恰 6 个
    if progress: progress("parsing")
    from .modal import parse_frequencies, parse_mode_shapes
    frequencies = parse_frequencies(run["dat"], allow_rigid=free_free)
    raw_shapes = parse_mode_shapes(run["frd"])
    if len(frequencies) != modes or len(raw_shapes) != modes:
        raise ValueError(f"固有频率与振型数量不完整：频率 {len(frequencies)}、振型 {len(raw_shapes)}，期望 {modes}")
    node_ids = sorted(raw_shapes[0])
    if any(set(shape) != set(node_ids) for shape in raw_shapes):
        raise ValueError("振型节点与网格节点不一致")
    shapes = np.array([[shape[n] for n in node_ids] for shape in raw_shapes], dtype=float)
    n_nodes = int(shapes.shape[1])
    if n_nodes != int(run["node_count"]):
        raise ValueError(f"振型节点数 {n_nodes} ≠ 网格节点数 {run['node_count']}")
    scale = np.linalg.norm(shapes, axis=2).max(axis=1)
    if np.any(scale <= 0):
        raise ValueError("零振型")
    normalized = shapes / scale[:, None, None]
    freqs = [float(f["frequency_hz"]) for f in frequencies]
    residuals = [float(f["eigen_residual"]) for f in frequencies]
    payload = modal_results_payload(freqs, normalized, residuals)
    validate_modal_results(payload, modes=modes, n_nodes=n_nodes, rigid_modes=rigid_modes)

    # 刚体模态前缀计数（界面标注口径）：|f| ≤ 1e-2 Hz 的连续前缀（模块56）。
    rigid_lead = 0
    for f in freqs:
        if abs(f) <= 1e-2:
            rigid_lead += 1
        else:
            break
    if free_free and rigid_lead < 6:
        raise ValueError(f"free_free 结果前 6 阶应为刚体模态（|f|≤1e-2 Hz），实际仅 {rigid_lead} 阶满足")

    if progress: progress("verifying")
    from .deck import parse_mesh_inp
    mesh = parse_mesh_inp(Path(run["inp"]).read_text(encoding="utf-8"))
    fixed_peak_ratios = []
    missing_nsets = []
    for name in run["fixed_nsets"]:
        ids = mesh["nsets"].get(name)
        if not ids:
            missing_nsets.append(name)
            continue
        rows = [node_ids.index(n) for n in ids if n in set(node_ids)]
        for mode in normalized:
            peak = float(np.linalg.norm(mode, axis=1).max())
            fixed_peak_ratios.append(float(np.linalg.norm(mode[rows], axis=1).max()) / peak)
    if missing_nsets:
        raise ValueError(f"网格缺少固定 NSET: {missing_nsets}")
    fixed_ratio_max = max(fixed_peak_ratios) if fixed_peak_ratios else 0.0
    if fixed_ratio_max >= 1e-8:
        raise ValueError(f"固定 NSET 上振型幅值/峰值 = {fixed_ratio_max:.3g} ≥ 1e-8（边界未固定？）")

    # 查看仪表数据包（模块56）：编译期显示网格（点/面片/逐三角形 occ tag）
    # + 本运行归一化振型/频率 → 单一 npz 供 ModalResultDialog 消费。
    display_npz = case / "modes_display.npz"
    if run.get("display_mesh") and Path(run["display_mesh"]).is_file():
        display = np.load(run["display_mesh"])
        np.savez_compressed(display_npz, node_ids=display["node_ids"], points=display["points"],
                            tris=display["tris"], tri_face_tags=display["tri_face_tags"],
                            shapes=normalized, frequencies_hz=np.asarray(freqs, dtype=float))
        display.close()
    else:
        display_npz = None

    verification = {
        "state": "computed_with_evidence",
        "status": "PASS",
        "created": datetime.datetime.now().isoformat(timespec="seconds"),
        "study_id": run.get("study_id"),
        "package": run.get("package"),
        "job": run["job"],
        "modes": modes,
        "free_free": free_free,
        "rigid_modes": rigid_lead,
        "frequencies_hz": freqs,
        "eigen_residuals": residuals,
        "eigen_residual_max": max(abs(v) for v in residuals),
        "fixed_nsets": list(run["fixed_nsets"]),
        "fixed_peak_ratio_max": fixed_ratio_max,
        "node_count": n_nodes,
        "exit_code": run["exit_code"],
        "elapsed_s": run["elapsed_s"],
        "display_npz": str(display_npz) if display_npz else None,
        "result_contract": list(RESULT_CONTRACT),
        "scope": ("整装粘接模态（*TIE）；单时刻线性特征值；静力载荷不进入模态；"
                  "振型为归一化特征向量非实际振幅。"
                  + ("free_free：前 6 阶为刚体模态（整体平动/转动，频率≈0）。" if free_free else "")),
    }
    verification_path = case / "verification.json"
    verification_path.write_text(json.dumps(verification, ensure_ascii=False, indent=2), encoding="utf-8")

    history_path = case / "run_history.json"
    history = json.loads(history_path.read_text(encoding="utf-8")) if history_path.is_file() else []
    history.append({"run_id": f"{run['job']}_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}",
                    "recorded": verification["created"], "state": "computed_with_evidence",
                    "previous_state": "configured_not_computed", "job": run["job"],
                    "modes": modes, "free_free": free_free, "rigid_modes": rigid_lead,
                    "first_hz": freqs[0], "frequencies_hz": freqs,
                    "eigen_residual_max": verification["eigen_residual_max"],
                    "fixed_peak_ratio_max": fixed_ratio_max,
                    "exit_code": run["exit_code"], "elapsed_s": run["elapsed_s"]})
    history_path.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")
    if progress: progress("collected")
    return {"state": "computed_with_evidence", "payload": payload,
            "verification_path": str(verification_path), "history_path": str(history_path),
            "history_entry": history[-1], "frequencies_hz": freqs, "eigen_residuals": residuals,
            "fixed_peak_ratio_max": fixed_ratio_max, "case_dir": str(case),
            "free_free": free_free, "rigid_modes": rigid_lead,
            "display_npz": str(display_npz) if display_npz else None}


# 注册（双向安全）：advanced_plan 尾部的 _register_default_backends 在
# 「先导入 advanced_plan」时完成注册；本模块先被导入时其尚未执行完，
# 由这里在类定义完成后补注册（两种导入顺序都保证 backend_for('modal') 可用）。
from . import advanced_plan as _plan  # noqa: E402

if "modal" not in _plan._BACKENDS:
    _plan.register_backend("modal", ModalBackend())
