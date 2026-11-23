"""Analytic planar/curved common areas and visible OCC failure handling."""
import json,math
from assembly_check_utils import ROOT,evidence_dir


def main():
    import gmsh
    from rm_sim import gmsh_runtime
    from rm_sim.cad.assembly_import import import_assembly
    from rm_sim.cad.assembly_checks import check_assembly_geometry,find_contact_faces
    work=evidence_dir('contact_candidates')
    model=import_assembly(ROOT/'samples/assembly_two_blocks.step')
    model._ensure_loaded();gmsh.model.setCurrent(model._name)
    before=set(gmsh.model.occ.getEntities())
    planar=find_contact_faces(model)
    assert not planar['errors'] and len(planar['candidates'])==1,planar
    assert abs(planar['candidates'][0]['common_area_mm2']-400)<1e-6
    assert set(gmsh.model.occ.getEntities())==before
    original=gmsh.model.occ.getDistance
    gmsh.model.occ.getDistance=lambda *a:(-1.,0.,0.,0.,0.,0.,0.)
    try:
        failed=check_assembly_geometry(model)
        assert failed['failed_pairs']==1 and failed['pairs'][0]['classification']=='check_failed'
    finally:gmsh.model.occ.getDistance=original
    assert set(gmsh.model.occ.getEntities())==before
    model.close()
    sample=work/'coaxial_cylinders.step'
    with gmsh_runtime.session():
        gmsh.model.add('curved_common_area')
        core=gmsh.model.occ.addCylinder(0,0,0,0,0,10,5)
        outer=gmsh.model.occ.addCylinder(0,0,0,0,0,10,8)
        tool=gmsh.model.occ.copy([(3,core)])
        gmsh.model.occ.cut([(3,outer)],tool)
        gmsh.model.occ.synchronize();gmsh.write(str(sample));gmsh.model.remove()
    model=import_assembly(sample)
    curves=find_contact_faces(model,planar_only=False)
    assert not curves['errors'] and len(curves['candidates'])==1,curves
    assert abs(curves['candidates'][0]['common_area_mm2']-2*math.pi*5*10)<1e-5
    assert curves['candidates'][0]['geometry_type']=='curved'
    assert not find_contact_faces(model,planar_only=True)['candidates']
    model.close()
    (work/'verification.json').write_text(json.dumps({'planar':planar,'curved':curves,'failed_distance':failed},ensure_ascii=False,indent=2),encoding='utf-8')
    print('VERIFY_CONTACT_CANDIDATES: PASS',work,flush=True)


if __name__=='__main__':main()
