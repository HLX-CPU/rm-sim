"""单零件、无预应力的线弹性固有频率与振型；独立于静力 DAT 解析。"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
import numpy as np


def parse_frequencies(path, *, allow_rigid: bool = False):
    """解析 DAT 特征值表。allow_rigid（模块56 free_free）：接受频率列印 0 的
    刚体模态行（自由悬浮的特征值≈0，ccx 对 λ<0 行的频率列印 0、虚部列放大
    求解噪声）——默认 False 保持既有实模态门不变。"""
    result=[]
    active=False
    for line in Path(path).read_text(encoding="utf-8",errors="replace").splitlines():
        if "E I G E N V A L U E" in line:
            active=True
        if "P A R T I C I P A T I O N" in line:
            break
        parts=line.split()
        if active and len(parts)==5 and parts[0].isdigit():
            values=[float(v.replace("D","E")) for v in parts[1:]]
            if not np.all(np.isfinite(values)):
                raise ValueError("特征值表含非有限数值")
            if allow_rigid:
                if values[2] > 1e-2 and abs(values[3]) > 1e-9:
                    raise ValueError("非实模态（弹性阶虚部非零），当前线弹性模态查看不支持")
            elif values[2]<=0 or not np.all(np.isfinite(values)) or abs(values[3])>1e-9:
                raise ValueError("非正或复数模态，当前线弹性模态查看不支持")
            # eigen_residual = DAT 第 5 列特征值虚部（复模态残差口径，与
            # |values[3]| 门同源）；模块 55 结果契约 eigen_residuals 取此列。
            result.append({"mode":int(parts[0]),"eigenvalue":values[0],"omega_rad_s":values[1],
                           "frequency_hz":values[2],"eigen_residual":values[3]})
    if not result:
        raise ValueError("DAT 没有可解析的固有频率")
    return result


def parse_mode_shapes(path):
    modes=[]
    current=None
    for line in Path(path).open(encoding="utf-8",errors="replace"):
        if line.startswith(" -4"):
            current={} if "DISP" in line else None
        elif line.startswith(" -3") and current is not None:
            modes.append(current);current=None
        elif line.startswith(" -1") and current is not None:
            current[int(line[3:13])]=[float(line[13+12*i:25+12*i]) for i in range(3)]
    return modes


def run_modal(project, model, modes=6):
    from .assembly_workflow import prepare_analysis
    from ..config import get_ccx_path,load_config
    from ..mesh.mesh_data import boundary_faces_corners
    from ..report.plot_aggs import render_field_png

    if len(project.parts)>1 or project.connections:
        raise ValueError("当前模态模块已验证范围为单零件、无接触及无预应力；装配模态尚未验证")
    if not 1<=int(modes)<=30:
        raise ValueError("模态数应在1到30之间")
    if project.study.material.density_t_mm3<=0:
        raise ValueError("模态计算需要正密度")
    root=Path(project.project_dir)/"modal_cases"
    root.mkdir(parents=True,exist_ok=True)
    index=1
    while (root/f"case_{index:03d}").exists():index+=1
    case=root/f"case_{index:03d}";case.mkdir()
    prepared=prepare_analysis(project,model,case/"mesh.inp")
    text=prepared["deck"].split("\n*STEP\n")[0]+f"\n*STEP\n*FREQUENCY\n{int(modes)}\n*NODE FILE\nU\n*END STEP\n"
    inp=case/"modal.inp";inp.write_text(text,encoding="utf-8")
    proc=subprocess.run([get_ccx_path(load_config()),"modal"],cwd=case,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=300)
    (case/"modal.log").write_bytes(proc.stdout+proc.stderr)
    if b"Job finished" not in proc.stdout+proc.stderr:
        raise RuntimeError(f"模态求解失败，日志: {case/'modal.log'}")
    frequencies=parse_frequencies(case/"modal.dat")
    raw_shapes=parse_mode_shapes(case/"modal.frd")
    if len(frequencies)!=int(modes) or len(raw_shapes)!=int(modes):
        raise ValueError("固有频率与振型数量不完整")
    mesh=prepared["mesh"]
    ids=sorted(mesh["nodes"])
    points=np.array([mesh["nodes"][n] for n in ids])
    rows={n:i for i,n in enumerate(ids)}
    tris=boundary_faces_corners(np.array([[rows[n] for n in mesh["elems"][e][:4]] for e in sorted(mesh["elems"])]))
    shapes=[]
    for shape in raw_shapes:
        if set(shape)!=set(ids):raise ValueError("振型节点与网格节点不一致")
        values=np.array([shape[n] for n in ids])
        scale=float(np.linalg.norm(values,axis=1).max())
        if scale<=0:raise ValueError("零振型")
        shapes.append(values/scale)
    shapes=np.asarray(shapes)
    amplitude=float(np.linalg.norm(points.max(axis=0)-points.min(axis=0)))*.05
    np.savez_compressed(case/"modes.npz",node_ids=np.array(ids),points=points,tris=tris,shapes=shapes)
    for k,mode in enumerate(shapes):
        png,_=render_field_png(points,tris,np.linalg.norm(mode,axis=1),title=f"模态{k+1}，{frequencies[k]['frequency_hz']:.4g} Hz（归一化振型，非实际振幅）",
                               unit="归一化",deform_u=mode,deform_scale=amplitude)
        (case/f"mode_{k+1:02d}.png").write_bytes(png)
    result={"case_dir":str(case),"modes":frequencies,"npz":str(case/"modes.npz"),
            "display_amplitude_mm":amplitude,"exit_code":proc.returncode,
            "scope":"单零件线弹性、固定边界、无预应力；静力载荷不进入模态。振型为归一化特征向量，非实际振幅。"}
    (case/"modal_results.json").write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    report=["# 模态分析报告","",result["scope"],"","| 阶次 | 频率 [Hz] |","|---|---|"]
    report += [f"| {v['mode']} | {v['frequency_hz']:.9g} |" for v in frequencies]
    report += ["",f"显示幅度 {amplitude:g} mm，仅为显示比例。原始输入和 DAT/FRD 留在本目录。"]
    (case/"report.md").write_text("\n".join(report),encoding="utf-8")
    return result


def beam_modal(project_dir, step_source, modes=6):
    from ..cad.step_import import import_step
    from ..core.project import Project,Constraint,LoadCase
    from ..core.face_identity import fingerprint_from_face
    from ..core.materials import find_material
    model=import_step(step_source)
    try:
        project=Project(name="标准梁模态",project_dir=str(Path(project_dir).resolve()))
        project.study.geometry_file=str(Path(step_source).resolve())
        project.study.material=find_material("45钢")
        face=min([f for f in model.faces if f.normal[0]<-.999],key=lambda f:f.center[0])
        project.study.load_cases=[LoadCase(constraints=[Constraint(name="固定端",face_tag=face.tag,face=fingerprint_from_face(face))])]
        return run_modal(project,model,modes)
    finally:model.close()
