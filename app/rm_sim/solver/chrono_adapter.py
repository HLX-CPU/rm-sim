"""外部 PyChrono 进程及运动载荷→结构静力的可追溯映射。

子进程环境隔离（2026-11-03）：Chrono 固定使用独立 Python3.12 环境
（config chronoPython，默认 E:/rm_sim/chrono-env/python.exe，conda 官方
PyChrono 包）。主 venv 是 Python3.13（numpy cp313 二进制）——宿主进程的
PYTHONPATH 一旦经 os.environ 泄漏进 cp312 子进程，numpy 就会解析到
cp313 二进制 → ImportError（实测：PYTHONPATH=E:/rm_sim/venv/Lib/site-packages
时 chrono_scene 以 cp313 numpy 2.5.3 报错）。因此 run_chrono 的子进程 env
剥离全部 PYTHON* 变量并显式设置隔离开关，同时在场景进程启动时先做
ABI 预检：打印 numpy/pychrono 版本与已加载扩展的 ABI tag 写入日志，
与 chrono-env 解释器期望不符立即报错（退出码 3 → 专属错误信息）。
"""
from __future__ import annotations
import json
import math
import os
from pathlib import Path
import re
import subprocess
import numpy as np

from ..core.advanced import LoadTransferSpec


def chrono_isolated_env(python_dir):
    """构造 Chrono 子进程的隔离环境。

    剥离宿主进程全部 PYTHON* 变量（PYTHONPATH/PYTHONHOME/PYTHONUSERBASE…），
    只保留本函数显式设置的开关；其余系统变量原样继承（Windows DLL 加载
    需要 SYSTEMROOT/COMPUTERNAME 等）。PYTHONNOUSERSITE 禁用用户
    site-packages，PYTHONSAFEPATH 阻止把脚本目录/cwd 前插进 sys.path——
    子进程 sys.path 只来自 chrono-env 自身安装布局。
    """
    env={k:v for k,v in os.environ.items() if not k.upper().startswith("PYTHON")}
    env["PYTHONUTF8"]="1"
    env["PYTHONNOUSERSITE"]="1"
    env["PYTHONSAFEPATH"]="1"
    env["PATH"]=str(python_dir/"Library/bin")+os.pathsep+str(python_dir)+os.pathsep+env.get("PATH","")
    env["TEMP"]="E:/rm_sim/temp";env["TMP"]="E:/rm_sim/temp"
    return env


def chrono_expected_abi_tag(python,env,work):
    """向 chrono-env 解释器本体询问期望 ABI tag（EXT_SUFFIX，如
    .cp312-win_amd64.pyd → 'cp312'）。探针只读 stdlib，不导入 numpy。"""
    code=("import sys,sysconfig,json;"
          "print(json.dumps({'ext_suffix':sysconfig.get_config_var('EXT_SUFFIX'),"
          "'python':sys.version,'executable':sys.executable}))")
    proc=subprocess.run([str(python),"-X","utf8","-B","-c",code],cwd=str(work),env=env,
                        stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=60)
    if proc.returncode!=0:
        raise RuntimeError(f"chrono-env 解释器预检失败 exit={proc.returncode}: "
                           f"{proc.stderr.decode('utf-8',errors='replace')[:400]}")
    info=json.loads(proc.stdout.decode("utf-8"))
    m=re.search(r"cp\d{2,}",info.get("ext_suffix",""))
    if not m:
        raise RuntimeError(f"无法从 chrono-env EXT_SUFFIX 解析 ABI tag: {info}")
    return m.group(0),info


def abi_tags_in(paths):
    """从扩展模块文件名集合提取 ABI tag（cp312-win_amd64 等）；无 tag 的
    .pyd（如 pychrono 的 _core.pyd）不贡献 tag，其加载成功本身即 ABI 兼容证据。"""
    tags=set()
    for p in paths:
        m=re.search(r"(cp\d{2,})-win_amd64",Path(p).name)
        if m:tags.add(m.group(1))
    return tags


def check_chrono_environment(work,expected_tag,chrono_root):
    """ABI 预检判定（在任何结果解析之前调用）：
    报告缺失 / 带导入错误 / ABI tag 与 chrono-env 期望不符 / numpy 或
    pychrono 模块越出 chrono-env 目录 → RuntimeError。"""
    report_path=Path(work)/"chrono_env_report.json"
    if not report_path.is_file():
        raise RuntimeError(f"PyChrono ABI 预检报告缺失（子进程可能在导入前崩溃），"
                           f"详见 {Path(work)/'chrono_stderr.log'}")
    report=json.loads(report_path.read_text(encoding="utf-8"))
    if report.get("error"):
        raise RuntimeError(f"PyChrono 环境预检失败: {report['error']}，"
                           f"详见 {Path(work)/'chrono_stderr.log'}")
    tags=set(report.get("abi_tags") or [])
    if tags!={expected_tag}:
        raise RuntimeError(f"PyChrono 子进程 ABI tag 与 chrono-env 期望不符: "
                           f"期望 {expected_tag}，实际 {sorted(tags) or '无'}；报告 {report_path}")
    root=os.path.normcase(str(Path(chrono_root).resolve()))+os.sep
    for key in ("numpy_file","numpy_extension_file","pychrono_file","pychrono_core_file"):
        if key not in report:continue
        actual=os.path.normcase(str(Path(report[key]).resolve()))
        if not actual.startswith(root):
            raise RuntimeError(f"PyChrono 子进程 {key} 越出 chrono-env 目录: "
                               f"{report[key]}（期望位于 {root} 下）；报告 {report_path}")
    return report


def run_chrono(output_dir):
    from ..config import load_config
    python=Path(load_config().get("chronoPython","E:/rm_sim/chrono-env/python.exe"))
    if not python.is_file():
        raise RuntimeError(f"PyChrono 独立运行环境不存在: {python}")
    work=Path(output_dir).resolve();work.mkdir(parents=True,exist_ok=True)
    env=chrono_isolated_env(python.parent)
    expected_tag,interpreter_info=chrono_expected_abi_tag(python,env,work)
    (work/"chrono_interpreter.json").write_text(json.dumps(
        {"expected_abi_tag":expected_tag,**interpreter_info},ensure_ascii=False,indent=2),encoding="utf-8")
    proc=subprocess.run([str(python),"-X","utf8","-B",str(Path(__file__).with_name("chrono_scene.py")),"--output",str(work)],
                        cwd=work,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=180)
    (work/"chrono_stdout.log").write_bytes(proc.stdout);(work/"chrono_stderr.log").write_bytes(proc.stderr)
    # ABI 预检判定先行：环境问题报环境错误（预检退出码 3 / 报告缺失 / tag 不符），
    # 通过后才把非零退出码当作场景执行失败处理。
    check_chrono_environment(work,expected_tag,python.parent)
    if proc.returncode!=0:
        raise RuntimeError(f"PyChrono 执行失败 exit={proc.returncode}，详见 {work/'chrono_stderr.log'}")
    path=work/"chrono_results.json"
    result=json.loads(path.read_text(encoding="utf-8"))
    from ..report.chrono_report import write_chrono_report
    report=write_chrono_report(result,work)
    return {"result_path":str(path),"result":result,**report}


def transfer_load(source_json,project_dir,step_source,sample_index=0):
    """梁示例传递（既有入口，签名/行为兼容）：固定 90° 旋转向 + 标准梁端面
    均分总力。参数化映射见 map_transfer_sample / write_transfer_provenance
    （LoadTransferSpec 驱动，模块57）。"""
    from .project_workflow import new_solve_project
    mapping=map_transfer_sample(source_json,DEFAULT_TRANSFER_SPEC,sample_index)
    mapped=mapping["mapped_force_n"];magnitude=float(np.linalg.norm(mapped))
    if not np.isfinite(magnitude) or magnitude<=0:raise ValueError("映射载荷必须是非零有限向量")
    result=new_solve_project(project_dir,step_source,force_n=magnitude,
                             direction=tuple(float(x) for x in mapped/magnitude))
    provenance={**mapping["provenance"],
                "application":"标准梁 x 最大端面均分总力",
                "scope":"单向、单时刻准静态传递；无瞬态冲击、模态叠加或双向耦合", "structural":result}
    write_transfer_provenance(project_dir,provenance)
    return provenance


# 明确坐标变换：绕 Z 正转90°；Chrono -X 映射为结构 -Y（既有梁示例口径，
# 现以 LoadTransferSpec 表达——source/target 均为原点，平移项为零）。
DEFAULT_TRANSFER_SPEC=LoadTransferSpec(rotation=(0.,-1.,0., 1.,0.,0., 0.,0.,1.))


def map_transfer_sample(source_json,spec,sample_index=0,*,sample=None):
    """LoadTransferSpec 驱动的运动载荷→结构载荷映射（不求解，模块57）。

    - Chrono 样本（units 门 m-kg-s-N）：force_world_n 必需；moment_world_nm/
      source_point_m 可选（既有场景样本无此两字段→力矩取零、作用点回退
      spec.source_point_mm；优先级：样本字段 > spec，回放口径随 provenance
      application_point_source 记录）。sample 直传（模块58 跌落冲击样本）
      优先于 JSON 内 spring.samples[sample_index]——units 门与 sha256 仍对
      JSON 文件生效。
    - 映射复用 advanced_history.transfer_wrench：结构力 = r@F（N）；结构力矩
      = r@(1000·m) + (r@source+t−target)×(r@F)（N·m→N·mm，含作用点平移项）。
    - 返回 dict：mapped_force_n/mapped_moment_nmm/... + provenance 核心字段
      （source_sha256/rotation/translation/source_point/target_point/time_s/
      源与映射向量——structural 求解结果由调用方补充后经
      write_transfer_provenance 落盘）。
    """
    from .advanced_history import transfer_wrench
    source=Path(source_json).resolve();data=json.loads(source.read_text(encoding="utf-8"))
    if data.get("units")!="m-kg-s-N":raise ValueError("动力学源单位不明，拒绝映射")
    if sample is None:
        sample=data["spring"]["samples"][int(sample_index)]
    force=np.asarray(sample["force_world_n"],dtype=float)
    moment=np.asarray(sample.get("moment_world_nm",[0.,0.,0.]),dtype=float)
    if "source_point_m" in sample:
        source_point=np.asarray(sample["source_point_m"],dtype=float)*1000.  # m→mm
        point_source="sample"
    else:
        source_point=np.asarray(spec.source_point_mm,dtype=float)
        point_source="spec"
    mapped_f,mapped_m=transfer_wrench(force,moment,spec.rotation,spec.translation_mm,
                                      source_point,spec.target_point_mm)
    magnitude=float(np.linalg.norm(mapped_f))
    if not np.isfinite(magnitude) or magnitude<=0:raise ValueError("映射载荷必须是非零有限向量")
    rotation=np.asarray(spec.rotation,dtype=float).reshape(3,3)
    import hashlib
    return {"mapped_force_n":mapped_f,"mapped_moment_nmm":mapped_m,
            "source_force_n":force,"source_moment_nm":moment,
            "application_point_mm":source_point,"application_point_source":point_source,
            "time_s":sample["time_s"],
            "provenance":{"source_path":str(source),
                          "source_sha256":hashlib.sha256(source.read_bytes()).hexdigest(),
                          "rotation":rotation.tolist(),
                          "translation_mm":[float(v) for v in spec.translation_mm],
                          "source_point_mm":source_point.tolist(),
                          "target_point_mm":[float(v) for v in spec.target_point_mm],
                          "time_s":sample["time_s"],
                          "source_force_n":force.tolist(),
                          "source_moment_nm":moment.tolist(),
                          "mapped_force_n":mapped_f.tolist(),
                          "mapped_moment_nmm":mapped_m.tolist(),
                          "application_point_mm":source_point.tolist(),
                          "application_point_source":point_source,
                          "spec_target_part_id":spec.target_part_id}}


def write_transfer_provenance(project_dir,provenance):
    """provenance（含 structural 求解结果）落盘 load_transfer.json（既有口径）。"""
    p=Path(project_dir)/"load_transfer.json"
    p.write_text(json.dumps(provenance,ensure_ascii=False,indent=2),encoding="utf-8")
    return p


# 模块58 跌落冲击最小算例：完成度红线（绝不虚报动态冲击能力）。
DROP_IMPACT_COMPLETENESS={
    "quasi_static_approximation":True,
    "stress_wave_propagation_computed":False,
    "plasticity_enabled":False,
    "impact_stress_trustworthy":False,
    "statement":"刚体冲击的准静态近似；未计算应力波传播、未启用塑性、碰撞应力不可信",
}


def impact_force_warning(peak_force_n,weight_n,factor=10.0):
    """峰值接触力异常（>factor×落体重力）→ 警告文案；否则 None（模块58④：
    记录警告字段而非静默放大）。"""
    peak=float(peak_force_n);weight=float(weight_n)
    if weight<=0 or not (math.isfinite(peak) and math.isfinite(weight)):
        return None
    if peak>factor*weight:
        return (f"峰值接触力 {peak:.6g} N 超过 {factor:g}×落体重力（{factor*weight:.6g} N）："
                "刚体接触为惩罚刚度量级，准静态近似下的结构载荷不可信，仅作量级参考")
    return None


def write_drop_impact_report(directory,mapping,structural,impact_sample,warning):
    """跌落冲击最小算例报告（红线文案：准静态近似/未验证冲击应力）。"""
    d=Path(directory);d.mkdir(parents=True,exist_ok=True)
    f=mapping["mapped_force_n"];m=mapping["mapped_moment_nmm"]
    rf=structural.get("rf_sum_n")
    lines=[ "# 跌落冲击最小可验证算例报告","",
        "## 完成度红线（如实标注，不虚报动态冲击能力）","",
        f"- **{DROP_IMPACT_COMPLETENESS['statement']}**",
        "- 刚体冲击的准静态近似：把接触峰值力时刻的刚体接触合力，经参数化传递映射为结构静力工况。",
        "- 未计算应力波传播；未启用塑性；未验证冲击应力；碰撞应力不可信——刚体接触力是惩罚刚度量级，不代表真实接触应力。",
        "", "## 峰值接触力时刻样本（Chrono Bullet）","",
        f"- 时刻 {impact_sample['time_s']:.6g} s，接触数 {impact_sample['contacts']}，箱体质心高度 {impact_sample['y_m']:.6g} m（最低 {impact_sample['minimum_y_m']:.6g} m）。",
        f"- 接触合力峰值 {impact_sample['contact_force_peak_n']:.6g} N；落体重力 {impact_sample['body_weight_n']:.6g} N"
        f"（倍数 {impact_sample['peak_over_weight']:.4g}×）。",
        f"- 警告字段：{warning if warning else '无（峰值接触力未超过 10× 落体重力）'}。",
        "", "## 参数化传递映射（LoadTransferSpec）","",
        f"- 结构力 [{f[0]:.6g}, {f[1]:.6g}, {f[2]:.6g}] N；结构力矩 [{m[0]:.6g}, {m[1]:.6g}, {m[2]:.6g}] N·mm。",
        f"- rotation={mapping['provenance']['rotation']}; translation_mm={mapping['provenance']['translation_mm']}; "
        f"source_point_mm={mapping['provenance']['source_point_mm']}; target_point_mm={mapping['provenance']['target_point_mm']}。",
        f"- 来源 sha256：{mapping['provenance']['source_sha256']}；时刻 {mapping['provenance']['time_s']:.6g} s。",
        "", "## 结构静力核验","",
        f"- 接地反力合力 {np.linalg.norm(np.asarray(rf,dtype=float)):.6g} N vs 映射力 {np.linalg.norm(f):.6g} N"
        f"（偏差 {abs(float(np.linalg.norm(np.asarray(rf,dtype=float)))-float(np.linalg.norm(f)))/float(np.linalg.norm(f)):.4%}，门 ±0.5%）。",
        "- 该反力仅验证准静态传力路径；冲击的动态效应（应力波/惯性放大/局部损伤）不在本算例范围内。",
    ]
    path=d/"drop_impact_report.md"
    path.write_text("\n".join(lines),encoding="utf-8")
    return path
