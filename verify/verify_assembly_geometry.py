"""Exact analytic overlap/gap controls and unchanged source entities."""
import json
from assembly_check_utils import ROOT,evidence_dir


def main():
    import gmsh
    from rm_sim.cad.assembly_import import import_assembly
    from rm_sim.cad.assembly_checks import check_assembly_geometry
    work=evidence_dir('assembly_geometry')
    model=import_assembly(ROOT/'samples/assembly_two_blocks.step')
    model._ensure_loaded();gmsh.model.setCurrent(model._name)
    original=set(gmsh.model.occ.getEntities())
    rows=[]
    for shift,expected,gap in [(0.,0.,0.),(1.,0.,1.),(-1.,400.,0.)]:
        result=check_assembly_geometry(model,{2:(0.,shift,0.)})
        row=result['pairs'][0]
        assert result['failed_pairs']==0,result
        assert abs(row['intersection_mm3']-expected)<1e-6,row
        assert abs(row['distance_mm']-gap)<1e-6,row
        assert set(gmsh.model.occ.getEntities())==original,'Transient check changed source entities'
        rows.append(result)
    model.close()
    (work/'verification.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')
    print('VERIFY_ASSEMBLY_GEOMETRY: PASS',work,flush=True)


if __name__=='__main__':main()
