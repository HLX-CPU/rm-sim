"""真实 PyChrono 运动、接触与源哈希约束的单时刻结构载荷链路。"""
import hashlib
import json
import math
from pathlib import Path
import numpy as np
from assembly_check_utils import ROOT,evidence_dir,batch


def main():
    work=evidence_dir("chrono")
    print("证据目录:",work,flush=True)
    scene=batch(work,"scene",action="chrono_demo",output_dir=str(work/"motion"))
    data=scene["result"]
    pendulum=data["pendulum"]
    assert abs(pendulum["period_s"]/pendulum["theory_period_s"]-1)<.03
    assert max(abs(s["radius_m"]-.5) for s in pendulum["samples"])<.001
    motor=data["motor"]
    assert abs(motor["actual_rad_s"]-2)<.001
    assert abs(motor["angle_rad"]-2)<.02
    spring=data["spring"]
    assert abs(spring["x_at_1s_m"]-spring["theory_x_at_1s_m"])<.005
    for sample in spring["samples"]:
        t=sample["time_s"];alpha=.5;wd=math.sqrt(200-alpha*alpha)
        theoretical=1+.1*math.exp(-alpha*t)*(math.cos(wd*t)+alpha/wd*math.sin(wd*t))
        assert abs(sample["x_m"]-theoretical)<.003
        expected=-(1000*(sample["x_m"]-1)+5*sample["vx_m_s"])
        assert abs(sample["force_world_n"][0]-expected)<1e-8
        assert abs(abs(sample["chrono_link_force_n"])-abs(expected))<1.,sample
    collision=data["collision"]
    assert collision["max_contacts"]>0
    assert collision["minimum_y_m"]>.08
    assert abs(collision["final_y_m"]-.1)<.02
    assert abs(collision["final_vx_m_s"])<.1
    result=batch(work,"transfer",action="chrono_load_transfer",source_json=scene["result_path"],
                 project_dir=str(work/"structure"),step_source=str(ROOT/"samples/beam_100x10x10.step"),sample_index=0)
    assert result["source_sha256"]==hashlib.sha256(Path(scene["result_path"]).read_bytes()).hexdigest()
    assert np.allclose(np.array(result["rotation"])@result["source_force_n"],result["mapped_force_n"])
    case=Path(result["structural"]["solve"]["case_dir"])
    from rm_sim.solver.results import load_results
    files=next(case.glob("*.inp"))
    raw=load_results(files.with_suffix(".dat"),files.with_suffix(".frd"))
    assert np.linalg.norm(raw.rf_sum("FIXED")+np.array(result["mapped_force_n"]))<.01
    applied=result["mapped_force_n"][1]
    theory=applied*100**3/(3*210000*(10*10**3/12))
    actual=result["structural"]["solve"]["tip_uy_mm"]
    assert abs(actual/theory-1)<.02
    (work/"verification.json").write_text(json.dumps({"status":"PASS","scene":scene,"transfer":result,"beam_theory_uy_mm":theory},ensure_ascii=False,indent=2),encoding="utf-8")
    print(f"VERIFY_CHRONO_LOAD_TRANSFER: PASS period={pendulum['period_s']:.6g}s, RF+F residual<.01N, Uy={actual:.8g}mm",flush=True)


if __name__=="__main__":main()
