"""重合界面两侧选面节点集保持部件归属，避免意外接地。"""
from assembly_check_utils import ROOT,evidence_dir


def main():
    from rm_sim.cad.assembly_import import import_assembly
    from rm_sim.mesh.mesher import generate_tet10_mesh
    from rm_sim.solver.deck import parse_mesh_inp
    work=evidence_dir("face_ownership")
    model=import_assembly(ROOT/"samples/assembly_two_blocks.step")
    try:
        a=next(f for f in model.faces if f.solid_tag==model.solids[0].tag and f.normal[1]>.999)
        b=next(f for f in model.faces if f.solid_tag==model.solids[1].tag and f.normal[1]<-.999)
        result=generate_tet10_mesh(model,3.,nset_faces={"SIDE_A":a.tag,"SIDE_B":b.tag},
               part_elsets={"PART1":model.solids[0].tag,"PART2":model.solids[1].tag},inp_path=work/"mesh.inp")
        mesh=parse_mesh_inp((work/"mesh.inp").read_text(encoding="utf-8"))
        nodes=[set(n for eid in mesh["elsets"][name] for n in mesh["elems"][eid]) for name in ("PART1","PART2")]
        sets=[set(result.node_sets[name]) for name in ("SIDE_A","SIDE_B")]
        assert sets[0] and sets[1] and not sets[0]&sets[1]
        assert sets[0]<=nodes[0] and not sets[0]&nodes[1]
        assert sets[1]<=nodes[1] and not sets[1]&nodes[0]
        print(f"VERIFY_FACE_OWNERSHIP: PASS 两侧节点数{list(map(len,sets))}，不混入相邻部件",flush=True)
    finally:model.close()


if __name__=="__main__":main()
