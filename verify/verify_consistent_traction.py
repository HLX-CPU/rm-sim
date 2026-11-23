"""Analytic quadratic-face loads and real CCX patch test on a locally refined mesh."""
import json,os
from pathlib import Path
from dataclasses import replace
import numpy as np
from assembly_check_utils import ROOT,evidence_dir


def main():
    import gmsh
    from rm_sim import gmsh_runtime
    from rm_sim.cad.assembly_import import import_assembly,sync_parts_from_model
    from rm_sim.cad.mesh_targets import targets_for_part
    from rm_sim.core.project import Project,Material,LoadCase,Constraint,TractionLoad,PressureLoad
    from rm_sim.core.mesh_controls import MeshControl
    from rm_sim.core.face_identity import fingerprint_from_face
    from rm_sim.core.store import to_dict,from_dict
    from rm_sim.solver.traction import consistent_traction
    from rm_sim.solver.assembly_workflow import prepare_analysis,finish_analysis
    from rm_sim.solver.deck import build_case_inp
    from rm_sim.solver.tasks import SolveScheduler
    from rm_sim.config import get_ccx_path,load_config
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import QCoreApplication,QEvent
    from rm_sim.gui.setup_dialog import SetupDialog
    os.environ.setdefault('QT_QPA_PLATFORM','offscreen');app=QApplication([])
    ids=list(range(101,111));xyz=((0,0,0),(1,0,0),(0,1,0),(0,0,1),(.5,0,0),(.5,.5,0),(0,.5,0),(0,0,.5),(.5,0,.5),(0,.5,.5))
    mesh={'nodes':dict(zip(ids,xyz)),'elems':{701:ids}}
    load=TractionLoad('quadratic face',face_tag=1,total_force_n=60.,direction=(0,0,-1),distribution='consistent_area')
    selected=[ids[i] for i in (0,1,2,4,5,6)]
    plan,meta=consistent_traction(load,mesh,selected)
    expected={n:(0. if n in ids[:3] else -20.) for n in selected}
    assert all(abs(f[2]-expected[n])<1e-10 for n,f in zip(plan.node_ids,plan.forces))
    assert abs(meta['area_mm2']-.5)<1e-12
    # A shared interface is external only within the selected owning part.
    shared={'nodes':{**mesh['nodes'],201:(0,0,-1),208:(0,0,-.5),209:(0,.5,-.5),210:(.5,0,-.5)},
            'elems':{701:ids,702:[101,103,102,201,107,106,105,208,209,210]}}
    own,_=consistent_traction(load,shared,selected,element_ids=[701])
    assert np.allclose(own.forces,plan.forces)
    from rm_sim.solver.convert import ConversionError
    try:consistent_traction(load,shared,selected)
    except ConversionError:pass
    else:raise AssertionError('Global interior face was accepted as external')
    assert from_dict(to_dict(load)).distribution=='consistent_area'
    old=to_dict(load);old.pop('distribution');assert from_dict(old).distribution=='equal_nodes'
    work=evidence_dir('consistent_traction');source=work/'block.step'
    with gmsh_runtime.session():
        gmsh.model.add('traction_patch');gmsh.model.occ.addBox(0,0,0,10,10,20)
        gmsh.model.occ.synchronize();gmsh.write(str(source));gmsh.model.remove()
    model=import_assembly(source);project=Project(project_dir=str(work));sync_parts_from_model(project,model)
    try:
        project.study.material=Material(name='Analytic nu=0',e_modulus_mpa=100000.,poisson=0.,density_t_mm3=7.85e-9)
        part=project.parts[0];part.material_confirmed=True
        fixed=next(f for f in model.faces if f.normal[2]<-.999);top=next(f for f in model.faces if f.normal[2]>.999)
        def ref(f,name):return dict(name=name,part_id=part.part_id,face_tag=f.tag,face=fingerprint_from_face(f))
        constraint=Constraint(**ref(fixed,'fixed'))
        force=TractionLoad(**ref(top,'uniform traction'),total_force_n=100.,direction=(0,0,1),distribution='consistent_area')
        _,_,edges=targets_for_part(project,model,part.part_id)
        edge=min((fp for tag,fp in edges if abs(fp.center[2]-20)<1e-8),key=lambda fp:fp.center[0])
        project.study.mesh_size_mm=3.
        project.study.mesh_controls=[MeshControl('nonuniform top edge','edge',part.part_id,.6,4,edge=edge)]
        project.study.load_cases=[LoadCase(name='traction patch',constraints=[constraint],loads=[force])]
        prepared=prepare_analysis(project,model,work/'mesh.inp',auto_mesh=False)
        nset=next(k for k,v in prepared['nset_faces'].items() if v==top.tag)
        p,info=consistent_traction(force,prepared['mesh'],prepared['mesh']['nsets'][nset])
        moment=sum((np.cross(prepared['mesh']['nodes'][n],f) for n,f in zip(p.node_ids,p.forces)),np.zeros(3))
        assert np.allclose(p.forces.sum(axis=0),[0,0,100],atol=1e-9)
        assert np.allclose(moment,[500,-500,0],atol=1e-8),moment
        legacy_center=np.mean([prepared['mesh']['nodes'][n] for n in p.node_ids],axis=0)
        legacy_moment=np.cross(legacy_center,[0,0,100])
        assert np.linalg.norm(legacy_moment-moment)>5.,'Fixture does not distinguish nodal density bias'
        scheduler=SolveScheduler(get_ccx_path(load_config()),max_workers=1);records=[];fields=[]
        for name in ('traction','pressure'):
            if name=='pressure':
                project.study.load_cases=[LoadCase(name='same uniform pressure',constraints=[constraint],
                    loads=[PressureLoad(**ref(top,'tension pressure'),pressure_mpa=-1.)])]
                prepared['deck']=build_case_inp((work/'mesh.inp').read_text(encoding='utf-8'),project.study,
                    project.study.load_cases[0],geom=model,nset_face_tags=prepared['nset_faces'],mesh=prepared['mesh'])
            task=scheduler.submit(name,prepared['deck'],work/'cases')
            try:scheduler.wait_task(task.task_id,timeout=60)
            finally:
                if task.state in ('pending','running'):
                    scheduler.cancel(task.task_id);scheduler.wait_task(task.task_id,timeout=15)
            assert task.state=='completed',task.summary()
            done=finish_analysis(project,task,prepared);f=done['fields'];fields.append(f)
            assert np.allclose(f.s_mises,1.,rtol=2e-5,atol=1e-6),(f.s_mises.min(),f.s_mises.max())
            z=np.array([prepared['mesh']['nodes'][n][2] for n in f.node_ids])
            assert np.allclose(f.u[:,2],z/100000.,rtol=2e-5,atol=1e-9)
            assert np.allclose(f.rf_sum,[0,0,-100],atol=.001)
            records.append(dict(case=str(task.case_dir),max_mises=float(f.s_mises.max()),max_u=f.max_u_mag()))
        assert np.allclose(fields[0].u,fields[1].u,rtol=2e-5,atol=1e-9)
        dialog=SetupDialog(project,model)
        dialog.kind.setCurrentText('面力')
        assert dialog.traction_distribution.currentData()=='consistent_area'
        dialog.close();dialog.deleteLater();QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete)
        (work/'verification.json').write_text(json.dumps(dict(analytic_tri6=True,
            consistent_moment_nmm=moment.tolist(),equal_nodes_moment_nmm=legacy_moment.tolist(),
            mesh=prepared['mesh_result'].stats.__dict__,cases=records,full_field_stress_1mpa=True,
            pressure_oracle_same_mesh=True,legacy_mode_preserved=True,new_gui_defaults_consistent=True),ensure_ascii=False,indent=2),encoding='utf-8')
    finally:model.close();app.shutdown()
    print('VERIFY_CONSISTENT_TRACTION: PASS',work,flush=True)


if __name__=='__main__':main()
