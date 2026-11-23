"""原始模态 DAT/FRD 对照 Euler-Bernoulli 第一频率与全部振型边界。"""
import json
import math
import subprocess
import numpy as np
from assembly_check_utils import ROOT,evidence_dir,PYTHON


def main():
    from rm_sim.solver.modal import beam_modal,parse_frequencies,parse_mode_shapes
    from rm_sim.solver.deck import parse_mesh_inp
    work=evidence_dir("modal")
    print("证据目录:",work,flush=True)
    result=beam_modal(work,ROOT/"samples/beam_100x10x10.step",6)
    frequencies=parse_frequencies(result["case_dir"]+"/modal.dat")
    theory=1.875104068711961**2/(2*math.pi*100**2)*math.sqrt(210000*(10*10**3/12)/(7.85e-9*100))
    first=frequencies[0]["frequency_hz"]
    assert abs(first-theory)/theory<.03,(first,theory)
    assert abs(frequencies[1]["frequency_hz"]-first)/first<.001
    mesh=parse_mesh_inp((__import__('pathlib').Path(result["case_dir"])/"modal.inp").read_text(encoding="utf-8"))
    shapes=parse_mode_shapes(result["case_dir"]+"/modal.frd")
    for shape in shapes:
        peak=max(np.linalg.norm(v) for v in shape.values())
        assert peak>0
        assert max(np.linalg.norm(shape[n]) for n in mesh["nsets"]["FIXED"])/peak<1e-8
    npz=np.load(result["npz"])
    assert npz["shapes"].shape==(6,len(mesh["nodes"]),3)
    assert np.allclose(np.linalg.norm(npz["shapes"],axis=2).max(axis=1),1)
    (work/"verification.json").write_text(json.dumps({"status":"PASS","first_hz":first,"theory_hz":theory,"result":result},ensure_ascii=False,indent=2),encoding="utf-8")
    print(f"VERIFY_MODAL_ANALYSIS: PASS first={first:.6f}Hz theory={theory:.6f}Hz err={abs(first-theory)/theory:.3%}",flush=True)


if __name__=="__main__":main()
