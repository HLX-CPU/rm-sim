"""真实 main.py --batch 装配全链路与粘接对照；独立复算输入/结果。"""
import json
import traceback
from pathlib import Path
from assembly_check_utils import ROOT, evidence_dir, batch, check_physics


def main():
    work = evidence_dir("assembly_example")
    print(f"证据目录: {work}", flush=True)
    base = dict(action="assembly_new_solve", step_source=str(ROOT/"samples/assembly_two_blocks.step"),
                force_n=1000., mesh_size_mm=2., gravity=True)
    contact = batch(work, "contact", project_dir=str(work/"contact"), friction_mu=.3, **base)
    bonded = batch(work, "bonded", project_dir=str(work/"bonded"), connection="bonded", **base)
    a = check_physics(contact["solve"]["case_dir"], 1000.)
    b = check_physics(bonded["solve"]["case_dir"], 1000.)
    assert abs(a[1]-b[1])/a[1] <= .001
    ctx = json.loads((Path(contact["solve"]["case_dir"])/"results.json").read_text(encoding="utf-8"))
    assert len(ctx["assembly"]["parts"]) == 2
    assert all(x["material"]["name"] == "Q235" for x in ctx["assembly"]["parts"])
    report = Path(contact["solve"]["report_md"]).read_text(encoding="utf-8")
    assert "ContactFrictionConnection" in report and "假设" in report and "p1" in report and "p2" in report
    assert "--batch" in (ROOT/"examples/README.md").read_text(encoding="utf-8")
    assert ctx["equilibrium"]["residual"] < .1
    result = {"status": "PASS", "evidence": str(work), "contact": contact, "bonded": bonded}
    (work/"verification.json").write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    print("VERIFY_ASSEMBLY_EXAMPLE: PASS",flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        raise SystemExit(1)
