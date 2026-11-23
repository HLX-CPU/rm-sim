"""三个真实独立进程验证装配束、参数修改、原始case不变与连接指纹负例。"""
import json
import zipfile
import traceback
from pathlib import Path
from assembly_check_utils import ROOT, evidence_dir, batch, hashes, check_physics


def main():
    work = evidence_dir("assembly_roundtrip")
    print(f"证据目录: {work}",flush=True)
    a = batch(work, "A", action="assembly_new_solve", project_dir=str(work/"A"),
              step_source=str(ROOT/"samples/assembly_two_blocks.step"), gravity=False, force_n=1000.)
    old = hashes(Path(a["solve"]["case_dir"]))
    b = batch(work, "B", action="project_modify_resolve", bundle_path=a["bundle"],
              work_dir=str(work/"B"), new_force_n=2000.)
    assert not b["refused"] and b["confirmed"]
    assert len(b["reopened"]["parts"]) == 2
    assert b["reopened"]["connections"][0]["friction_mu"] == .3
    assert b["reopened"]["old_force_n"] == 1000.
    assert hashes(work/"B/cases/case_001") == old
    assert b["solve"]["case_name"] == "case_002"
    check_physics(b["solve"]["case_dir"], 2000.)
    assert abs(b["solve"]["rf_sum_n"][1]-2000.)/2000 < .0001
    c = batch(work, "C", action="project_open_readonly", bundle_path=a["bundle"], work_dir=str(work/"C"))
    assert c["confirmed"] and c["force_n"] == 2000. and c["n_cases"] == 2
    assert all(r["results_json_readable"] for r in c["cases"])
    assert c["connections"][0]["friction_mu"] == .3
    from rm_sim.cad.assembly_import import import_assembly
    from rm_sim.core.part_identity import part_matches_fingerprint
    from rm_sim.core.store import from_dict
    with zipfile.ZipFile(a["bundle"]) as z:
        saved = from_dict(json.loads(z.read("project.json"))["project"])
    for part in saved.parts:
        model = import_assembly(work/"C/geometry/parts"/(part.part_id+".step"))
        try:
            assert len(model.solids)==1
            assert part_matches_fingerprint(model.solids[0],part.fingerprint,50.0)
        finally:
            model.close()
    # 连接面指纹篡改，保留原 bundle 与全部证据。
    bad = work/"tampered.rmsimproject"
    with zipfile.ZipFile(a["bundle"]) as source, zipfile.ZipFile(bad,"w",compression=zipfile.ZIP_DEFLATED) as dest:
        for item in source.infolist():
            content = source.read(item.filename)
            if item.filename == "project.json":
                meta = json.loads(content)
                fp = meta["project"]["connections"][0]["side_a"]["face"]
                fp["area_mm2"] *= 1.05
                content = json.dumps(meta,ensure_ascii=False,indent=2).encode("utf-8")
            dest.writestr(item,content)
    t = batch(work,"tamper", action="project_try_solve", bundle_path=str(bad), work_dir=str(work/"tamper"))
    assert t["refused"] and not t["confirmed"] and t["warnings"]
    assert "连接" in t["reason"] and not (work/"tamper/cases/case_003").exists()
    (work/"verification.json").write_text(json.dumps({"status":"PASS","A":a,"B":b,"C":c,"tamper":t},ensure_ascii=False,indent=2),encoding="utf-8")
    print("VERIFY_ASSEMBLY_PROJECT_ROUNDTRIP: PASS",flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        raise SystemExit(1)
