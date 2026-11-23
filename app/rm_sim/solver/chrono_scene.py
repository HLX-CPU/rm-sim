"""由独立 PyChrono Python 执行的最小动力学算例，SI 单位；无 GUI。

启动先做 ABI 预检（environment_report）：导入 numpy/pychrono，把版本、
扩展模块路径与已加载扩展的 ABI tag 打印进 stdout（落入 chrono_stdout.log）
并写 chrono_env_report.json；任何导入失败以退出码 3 结束，父进程
（chrono_adapter.check_chrono_environment）据此立即报错。预检 regex 与
chrono_adapter 各自独立——本脚本被异构解释器（cp312）直接执行，不能回
import 主包。"""
from __future__ import annotations
import argparse
import json
import math
import os
import re
from pathlib import Path
import sys

_dll_handle = None
if os.name == "nt":
    library = Path(sys.executable).parent/"Library/bin"
    if library.is_dir():
        _dll_handle=os.add_dll_directory(str(library))


def environment_report(output):
    """ABI 预检：先导入 numpy/pychrono 并留档（版本/扩展路径/ABI tag/sys.path）。

    返回 (report, error)。报告无论成败都落盘 chrono_env_report.json——
    失败场景的现场（到底加载了哪个 ABI 的 numpy）必须可追查。
    """
    import importlib.machinery
    import sysconfig
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    report={"stage":"abi_precheck","python":sys.version,"python_executable":sys.executable,
            "ext_suffix":sysconfig.get_config_var("EXT_SUFFIX"),
            "extension_suffixes":list(importlib.machinery.EXTENSION_SUFFIXES),
            "sys_path":sys.path}
    error=None
    try:
        import numpy
        umath=importlib.import_module("numpy._core._multiarray_umath")
        report["numpy_version"]=numpy.__version__
        report["numpy_file"]=str(Path(numpy.__file__).resolve())
        report["numpy_extension_file"]=str(Path(umath.__file__).resolve())
    except Exception as exc:  # ImportError/DLL load failure——报告现场后交父进程裁决
        error=f"numpy 导入失败: {type(exc).__name__}: {exc}"
    if error is None:
        try:
            import pychrono as c
            core=importlib.import_module("pychrono._core")
            report["pychrono_version"]=getattr(c,"__version__","unknown")
            try:
                from importlib.metadata import version as _dist_version
                report["pychrono_version"]=f"{report['pychrono_version']} (dist {_dist_version('pychrono')})"
            except Exception:
                pass
            report["pychrono_file"]=str(Path(c.__file__).resolve())
            report["pychrono_core_file"]=str(Path(core.__file__).resolve())
        except Exception as exc:
            error=f"pychrono 导入失败: {type(exc).__name__}: {exc}"
    tags=set()
    for module in list(sys.modules.values()):
        f=getattr(module,"__file__",None)
        if f:
            m=re.search(r"(cp\d{2,})-win_amd64",Path(f).name)
            if m:tags.add(m.group(1))
    report["abi_tags"]=sorted(tags)
    if error:report["error"]=error
    (output/"chrono_env_report.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    return report,error


def simulate(output):
    import pychrono as c
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    def vec(x,y,z):return c.ChVector3d(x,y,z)
    def fixed(system):
        body=c.ChBody();body.SetFixed(True);system.AddBody(body);return body
    def system(gravity):
        s=c.ChSystemNSC();s.SetGravitationalAcceleration(vec(0,gravity,0));return s

    # 单摆：均匀刚杆、转动铰链，与刚体杆的小角度周期理论对比。
    s=system(-9.81);ground=fixed(s);rod=c.ChBody();mass=2.;length=1.;theta=.1
    rod.SetMass(mass);rod.SetInertiaXX(vec(mass/12,.001,mass/12))
    rod.SetPos(vec(.5*math.sin(theta),-.5*math.cos(theta),0));rod.SetRot(c.QuatFromAngleZ(theta));s.AddBody(rod)
    hinge=c.ChLinkLockRevolute();hinge.Initialize(ground,rod,c.ChFramed(vec(0,0,0),c.ChQuaterniond(1,0,0,0)));s.AddLink(hinge)
    pendulum=[];crossings=[];previous=theta
    for i in range(4000):
        s.DoStepDynamics(.001);p=rod.GetPos();a=math.atan2(p.x,-p.y);t=s.GetChTime()
        if previous<0<=a:crossings.append(t)
        previous=a
        if i%10==0:pendulum.append({"time_s":t,"angle_rad":a,"radius_m":math.hypot(p.x,p.y)})
    if len(crossings)<2:raise RuntimeError("摆运动未完成足够的周期")
    period=crossings[1]-crossings[0]
    theory=2*math.pi*math.sqrt(2*length/(3*9.81))

    # 恒角速度电机，真实约束反解后的角速度与角度。
    s=system(0);ground=fixed(s);rotor=c.ChBody();rotor.SetMass(1);rotor.SetInertiaXX(vec(.1,.1,.1));s.AddBody(rotor)
    motor=c.ChLinkMotorRotationSpeed();motor.Initialize(rotor,ground,c.ChFramed())
    motor.SetSpeedFunction(c.ChFunctionConst(2.));s.AddLink(motor)
    for i in range(1000):s.DoStepDynamics(.001)
    rotation=rotor.GetRot();angle=2*math.atan2(rotation.e3,rotation.e0)
    speed=rotor.GetAngVelParent().z

    # 受棱柱关节约束的弹簧阻尼质量块。输出世界 X 向弹簧力供结构映射。
    s=system(0);ground=fixed(s);body=c.ChBody();body.SetMass(5);body.SetInertiaXX(vec(.01,.01,.01));body.SetPos(vec(1.1,0,0));s.AddBody(body)
    slider=c.ChLinkLockPrismatic();slider.Initialize(ground,body,c.ChFramed(vec(1.1,0,0),c.QuatFromAngleY(math.pi/2)));s.AddLink(slider)
    spring=c.ChLinkTSDA();spring.Initialize(ground,body,False,vec(0,0,0),vec(1.1,0,0))
    spring.SetRestLength(1.);spring.SetSpringCoefficient(1000.);spring.SetDampingCoefficient(5.);s.AddLink(spring)
    spring_samples=[]
    for i in range(1000):
        s.DoStepDynamics(.001)
        x=body.GetPos().x;vx=body.GetPosDt().x
        # 使用真实状态与已设系数计算作用在质量块上的世界向量；GetForce 同时留档互核。
        force=-(1000*(x-1)+5*vx)
        spring_samples.append({"time_s":s.GetChTime(),"x_m":x,"vx_m_s":vx,"force_world_n":[force,0.,0.],
                               "chrono_link_force_n":spring.GetForce()})
    alpha=5/(2*5);wd=math.sqrt(1000/5-alpha*alpha)
    x_theory=1+.1*math.exp(-alpha)*(math.cos(wd)+alpha/wd*math.sin(wd))

    # 地面、摩擦与碰撞：箱体自 1 m 高度自由跌落（模块58 冲击最小算例；
    # 无水平初速——纯跌落，冲击速度=√(2gh)）。接触合力经 GetContactForce
    # 直读；峰值接触力时刻样本（接触数>0）写入 impact_sample。
    s=system(-9.81);s.SetCollisionSystemType(c.ChCollisionSystem.Type_BULLET)
    material=c.ChContactMaterialNSC();material.SetFriction(.4);material.SetRestitution(0)
    floor=c.ChBodyEasyBox(10,.1,10,1000,False,True,material);floor.SetFixed(True);floor.SetPos(vec(0,-.05,0));s.AddBody(floor)
    drop_height=1.0
    box=c.ChBodyEasyBox(.2,.2,.2,1000,False,True,material);box.SetPos(vec(0,.1+drop_height,0));s.AddBody(box)
    box_mass=box.GetMass();box_weight=box_mass*9.81
    contacts=[];peak=0;min_y=.1+drop_height
    impact=None;peak_force=0.
    for i in range(2000):
        s.DoStepDynamics(.001);p=box.GetPos();n=s.GetNumContacts();peak=max(peak,n);min_y=min(min_y,p.y)
        cf=box.GetContactForce()  # 箱体所受接触合力（N，含摩擦；重力不计入）
        cf_mag=math.sqrt(cf.x*cf.x+cf.y*cf.y+cf.z*cf.z)
        if n>0 and cf_mag>peak_force:
            peak_force=cf_mag
            impact={"time_s":s.GetChTime(),"contacts":n,"y_m":p.y,
                    "minimum_y_m":min_y,"contact_force_peak_n":cf_mag,
                    "contact_force_on_body_n":[cf.x,cf.y,cf.z],
                    "source_point_m":[p.x,p.y-.1,p.z],  # 箱底面中心（接触作用点）
                    "body_mass_kg":box_mass,"body_weight_n":box_weight,
                    "drop_height_m":drop_height}
        if i%10==0:contacts.append({"time_s":s.GetChTime(),"y_m":p.y,"vx_m_s":box.GetPosDt().x,"contacts":n})
    if impact is not None:
        # 结构受力=箱体所受接触力的反作用力（符号约定：force_world_n 供映射为
        # 结构载荷直接使用；刚体接触力是惩罚刚度量级，绝不当真实冲击应力）。
        impact["sign_convention"]="force_world_n=结构侧受力（箱体所受接触力的反作用力）"
        impact["force_world_n"]=[-v for v in impact["contact_force_on_body_n"]]
        impact["peak_over_weight"]=impact["contact_force_peak_n"]/box_weight if box_weight>0 else None
    result={"engine":"Project Chrono / PyChrono", "version":getattr(c,"__version__", "unknown"),
            "units":"m-kg-s-N", "python":sys.executable,
            "pendulum":{"period_s":period,"theory_period_s":theory,"samples":pendulum},
            "motor":{"command_rad_s":2.,"actual_rad_s":speed,"angle_rad":angle},
            "spring":{"mass_kg":5.,"stiffness_n_m":1000.,"damping_n_s_m":5.,"x_at_1s_m":body.GetPos().x,
                      "theory_x_at_1s_m":x_theory,"samples":spring_samples},
            "collision":{"max_contacts":peak,"minimum_y_m":min_y,"final_y_m":box.GetPos().y,
                         "final_vx_m_s":box.GetPosDt().x,"drop_height_m":drop_height,
                         "body_weight_n":box_weight,
                         "peak_contact_force_n":impact["contact_force_peak_n"] if impact else 0.,
                         "impact_sample":impact,"samples":contacts},
            "scope":"刚体最小场景；不计算碰撞冲击应力、材料塑性或真实机器人多体系统。"}
    path=output/"chrono_results.json";path.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({"result_path":str(path),"pendulum_period_s":period,"spring_x_m":body.GetPos().x,
                      "contacts":peak,"motor_speed":speed},ensure_ascii=False))


if __name__=="__main__":
    parser=argparse.ArgumentParser();parser.add_argument("--output",required=True);args=parser.parse_args()
    report,error=environment_report(args.output)
    print("CHRONO_ENV_REPORT "+json.dumps(report,ensure_ascii=False),flush=True)
    if error:
        print(f"CHRONO_ENV_PRECHECK_FAILED {error}",flush=True)
        sys.exit(3)
    simulate(args.output)
