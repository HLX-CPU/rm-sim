"""装配验收公共运行器；所有计算输入、原始求解产物及输出持久保留。"""
from pathlib import Path
import datetime
import json
import os
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

# 默认用当前解释器；需要指定另一套环境时设 RM_SIM_PYTHON。
PYTHON = Path(os.environ.get("RM_SIM_PYTHON", sys.executable))
# 原始开发约定为 E:/rm_sim/verification；可用 RM_SIM_VERIFICATION_ROOT 迁移。
EVIDENCE_ROOT = Path(os.environ.get("RM_SIM_VERIFICATION_ROOT", ROOT / "verification"))


def evidence_dir(name):
    path = EVIDENCE_ROOT / (name + "_" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f"))
    path.mkdir(parents=True, exist_ok=False)
    return path


def batch(work, name, **spec):
    result = work / f"{name}_result.json"
    spec["result_path"] = str(result)
    sp = work / f"{name}_spec.json"
    sp.write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")
    env = dict(os.environ, PYTHONUTF8="1", OMP_NUM_THREADS="2", TEMP=str(EVIDENCE_ROOT / "tmp"), TMP=str(EVIDENCE_ROOT / "tmp"))
    Path(env["TEMP"]).mkdir(parents=True, exist_ok=True)
    proc = subprocess.run([str(PYTHON), "-X", "utf8", "-B", str(ROOT/"main.py"), "--batch", str(sp)],
                          cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=900)
    (work / f"{name}_stdout.log").write_bytes(proc.stdout)
    (work / f"{name}_stderr.log").write_bytes(proc.stderr)
    if proc.returncode != 0:
        raise AssertionError(f"{name} exit={proc.returncode}: {proc.stdout.decode('utf-8', errors='replace')[-1500:]}")
    data = json.loads(result.read_text(encoding="utf-8"))
    assert data["batch_ok"], data
    print(f"[OK] {name}: 独立进程 exit=0", flush=True)
    return data


def hashes(directory):
    import hashlib
    return {p.relative_to(directory).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in Path(directory).rglob("*") if p.is_file()}


def check_physics(case_dir, expected_force):
    """从原始输入/结果独立计算外力、自重、反力与从面压应力积分。"""
    import numpy as np
    import re
    from rm_sim.solver.deck import parse_mesh_inp
    from rm_sim.solver.results import load_results
    from rm_sim.solver.ccx2para import frd_to_vtu, read_vtu_point_data

    case_dir = Path(case_dir)
    inp = next(case_dir.glob("*.inp"))
    text = inp.read_text(encoding="utf-8")
    mesh = parse_mesh_inp(text)
    dat, frd = inp.with_suffix(".dat"), inp.with_suffix(".frd")
    results = load_results(dat, frd)
    fixed = re.findall(r"\*NODE PRINT, NSET=([^\n]+)\nRF", text)
    rf = {}
    for name in fixed:
        rf.update(results.rf_block(name))
    actual = np.sum(list(rf.values()), axis=0)
    volumes = {}
    for name in ("PART1", "PART2"):
        volume = 0.0
        for eid in mesh["elsets"][name]:
            pts = np.array([mesh["nodes"][n] for n in mesh["elems"][eid][:4]])
            volume += abs(float(np.dot(pts[1]-pts[0], np.cross(pts[2]-pts[0], pts[3]-pts[0]))))/6
        volumes[name] = volume
    gravity = "GRAV" in text
    expected = expected_force + (sum(volumes.values()) * 7.85e-9 * 9810 if gravity else 0)
    assert abs(actual[1]-expected)/expected < .01, (actual, expected)
    assert np.linalg.norm(actual[[0,2]]) < .01
    assert "Job finished" in inp.with_suffix(".log").read_text(encoding="utf-8", errors="replace")
    for suffix in (".inp", ".dat", ".frd", ".log"):
        assert inp.with_suffix(suffix).stat().st_size > 0
    data = read_vtu_point_data(frd_to_vtu(frd))
    s = data["S"]
    node_ids = sorted(mesh["nodes"])
    pos = {n: i for i,n in enumerate(node_ids)}
    face_nodes = ((0,2,1),(0,1,3),(1,2,3),(0,3,2))
    pressure_total, minimum, count = 0., float("inf"), 0
    for name, ids in mesh["elsets"].items():
        match = re.fullmatch(r"C1_SA_(\d)", name)
        if not match:
            continue
        for eid in ids:
            nodes = [mesh["elems"][eid][i] for i in face_nodes[int(match[1])]]
            p = np.array([mesh["nodes"][n] for n in nodes])
            area = np.linalg.norm(np.cross(p[1]-p[0],p[2]-p[0]))/2
            pressure = -np.mean([s[pos[n],1] for n in nodes])
            pressure_total += area*pressure
            minimum = min(minimum, pressure)
            count += 1
    upper_weight = volumes["PART2"]*7.85e-9*9810 if gravity else 0
    assert count > 0
    assert abs(pressure_total-(expected_force+upper_weight))/(expected_force+upper_weight) < .1
    assert minimum >= -.01, minimum
    print(f"[OK] 原始产物复算 RFy={actual[1]:.9f}, 外力+自重={expected:.9f}, 界面积分={pressure_total:.6f}, minP={minimum:.6f}",flush=True)
    return actual
