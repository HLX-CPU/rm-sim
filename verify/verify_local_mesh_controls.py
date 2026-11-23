"""Real Gmsh locality, persistent target resolution, quality IDs and background mesh flow."""
from pathlib import Path
from copy import deepcopy
import json
import os
import time
import numpy as np
from assembly_check_utils import ROOT,evidence_dir,hashes


def main():
    os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
    import gmsh
    from rm_sim import gmsh_runtime
    from rm_sim.cad.assembly_import import import_assembly,sync_parts_from_model
    from rm_sim.cad.mesh_targets import targets_for_part,resolve_controls
    from rm_sim.core.project import Project
    from rm_sim.core.mesh_controls import MeshControl
    from rm_sim.core.face_identity import fingerprint_from_face
    from rm_sim.core.store import to_dict,from_dict
    from rm_sim.core.project_bundle import save_bundle,open_bundle
    from rm_sim.solver.project_workflow import reresolve_project
    from rm_sim.solver.assembly_workflow import prepare_analysis
    from rm_sim.solver.prepared_store import save_prepared,load_prepared
    from rm_sim.solver.workflow import WorkflowScheduler
    from rm_sim.config import get_ccx_path,load_config
    from rm_sim.gui.mesh_dialogs import MeshControlsDialog,MeshQualityDialog
    from PySide6.QtWidgets import QApplication,QTableWidget
    from PySide6.QtCore import QCoreApplication,QEvent
    app=QApplication([]);work=evidence_dir('local_mesh_controls')
    source=work/'two_blocks.step'
    with gmsh_runtime.session():
        gmsh.model.add('local_controls')
        gmsh.model.occ.addBox(0,0,0,12,12,12);gmsh.model.occ.addBox(30,0,0,12,12,12)
        gmsh.model.occ.synchronize();gmsh.write(str(source));gmsh.model.remove()
    model=import_assembly(source);project=Project(project_dir=str(work))
    sync_parts_from_model(project,model);project.study.mesh_size_mm=3.
    before=hashes(work)
    scheduler=WorkflowScheduler(get_ccx_path(load_config()));task=None
    try:
        part=min(project.parts,key=lambda p:model.solids[p.solid_index].center_of_mass[0])
        solid,faces,edges=targets_for_part(project,model,part.part_id)
        face=min(faces,key=lambda f:f.center[0]);edge=edges[0][1]
        measures={};counts={}
        def mesh(label,controls):
            project.study.mesh_controls=controls
            result=prepare_analysis(project,model,work/(label+'.inp'),size=3,auto_mesh=False,mesh_only=True)
            m=result['mesh_result'];q=m.quality
            assert not m.stats.negative_jacobian_count and q['nonpositive_jacobian_count']==0
            text=(work/(label+'.inp')).read_text(encoding='utf-8')
            from rm_sim.solver.deck import parse_mesh_inp
            parsed=parse_mesh_inp(text)
            assert all(e['element'] in parsed['elems'] for e in q['worst_elements'])
            for e in q['worst_elements']:
                exported=parsed['elems'][e['element']]
                assert np.allclose(np.mean([parsed['nodes'][n] for n in exported[:4]],axis=0),e['center_mm'],atol=1e-10)
                assert e['element'] in parsed['elsets'][e['part']]
            qpath=work/(label+'.quality.json')
            assert json.loads(qpath.read_text(encoding='utf-8'))==q
            counts[label]={name:len(ids) for name,ids in m.element_sets.items()}
            corners=m.mesh.points[m.mesh.cells['tetra10'][:,:4]]
            centers=corners.mean(axis=1)
            sizes=np.mean([np.linalg.norm(corners[:,i]-corners[:,j],axis=1)
                           for i,j in ((0,1),(0,2),(0,3),(1,2),(1,3),(2,3))],axis=0)
            measures[label]={'near':float(np.median(sizes[centers[:,0]<2.])),
                             'far':float(np.median(sizes[(centers[:,0]>10)&(centers[:,0]<12)]))}
            return result
        base=mesh('base',[])
        face_c=MeshControl('left face','face',part.part_id,.8,3,face=fingerprint_from_face(face))
        refined=mesh('face',[face_c]);assert measures['face']['near']<.6*measures['base']['near'],measures
        assert measures['face']['far']>1.5*measures['face']['near'],measures
        edge_c=MeshControl('edge','edge',part.part_id,.8,3,edge=edge)
        edge_result=mesh('edge',[edge_c])
        assert edge_result['mesh_result'].stats.element_count>base['mesh_result'].stats.element_count
        part_c=MeshControl('part','part',part.part_id,1.,3)
        part_result=mesh('part',[part_c])
        own=f'PART{project.parts.index(part)+1}'
        other=next(k for k in counts['base'] if k!=own)
        assert counts['part'][own]>5*counts['base'][own],counts
        assert abs(counts['part'][other]-counts['base'][other])<.25*counts['base'][other],counts
        reset=mesh('reset',[])
        assert reset['mesh_result'].stats.element_count==base['mesh_result'].stats.element_count,'Background field leaked'
        project.study.mesh_controls=[face_c,edge_c,part_c]
        restored=from_dict(to_dict(project));assert restored.study.mesh_controls==project.study.mesh_controls
        save_bundle(work/'portable.rmsimproject',project,geometry_source=source)
        bundle=open_bundle(work/'portable.rmsimproject',work/'relocated')
        loaded=bundle['project'];other_model=import_assembly(bundle['geometry_path'])
        try:
            assert not reresolve_project(loaded,other_model)
            assert len(resolve_controls(loaded,other_model,3))==3
        finally:other_model.close()
        broken=deepcopy(project);broken.study.mesh_controls[0].face=type(face_c.face)(999.,(0,0,0),(1,0,0))
        assert reresolve_project(broken,model) and not broken.is_confirmed
        # A quality UI displays actual INP element IDs; settings are transactional.
        dialog=MeshControlsDialog(project,model)
        dialog.controls[0].size_mm=.7;dialog.reject()
        assert project.study.mesh_controls[0].size_mm==.8
        quality_dialog=MeshQualityDialog(refined['mesh_result'].quality)
        table=quality_dialog.findChild(QTableWidget)
        assert table.rowCount()==len(refined['mesh_result'].quality['worst_elements'])
        assert int(table.item(0,0).text())==refined['mesh_result'].quality['worst_elements'][0]['element']
        for d in (dialog,quality_dialog):d.close();d.deleteLater()
        QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete)
        # Persist local identities through the real isolated preparation worker.
        project.study.mesh_controls=[face_c]
        from rm_sim.solver.workflow import WorkerCrashed
        import shutil
        real_worker=scheduler._worker;injected=[]
        def crash_after_snapshot(task,spec,operation):
            if not injected:
                injected.append(True)
                shutil.copyfile(source,task.work/'geometry.step')
                (task.work/'prepare.stderr.log').write_text('Injected fixture: Failed to reach critical value in pass 1 for measure(s): ScaledJac',encoding='utf-8')
                raise WorkerCrashed('mesh_optimizer_crash','injected first-attempt crash',task.work/'prepare.stderr.log')
            return real_worker(task,spec,operation)
        scheduler._worker=crash_after_snapshot
        task=scheduler.submit(project,source,mesh_only=True,size=3)
        deadline=time.monotonic()+60
        while task.state in ('pending','running') and time.monotonic()<deadline:
            app.processEvents();time.sleep(.01)
        assert task.state=='completed',task.summary()
        q=task.result['prepared']['mesh_result'].quality
        assert q['recipe']['local_controls'][0]['name']=='left face'
        assert task.result['project'].study.mesh_controls==[face_c]
        archived=task.work/'prepare_attempt_01'
        assert (archived/'geometry.step').read_bytes()==source.read_bytes()
        assert 'Injected fixture' in (archived/'prepare.stderr.log').read_text(encoding='utf-8')
        assert hashes(work)[source.name]==before[source.name]
        (work/'verification.json').write_text(json.dumps(dict(counts=counts,median_corner_edge_mm=measures,
            background_quality=q,source_unchanged=True,portable_controls_resolved=True,
            missing_target_rejected=True,settings_cancel_preserves_project=True,
            simulated_crash_then_real_worker=True,failed_attempt_preserved=str(archived)),ensure_ascii=False,indent=2),encoding='utf-8')
    finally:
        if task is not None and task.state in ('pending','running'):
            scheduler.cancel(task.task_id)
            while task.state in ('pending','running'):time.sleep(.02)
        model.close();app.shutdown()
    print('VERIFY_LOCAL_MESH_CONTROLS: PASS',work,flush=True)


if __name__=='__main__':main()
