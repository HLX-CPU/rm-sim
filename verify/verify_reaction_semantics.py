"""Real mixed loads, support-load correction, raw provenance and read-only legacy restore."""
from pathlib import Path
from dataclasses import replace
import json
import shutil
import os
import numpy as np
from assembly_check_utils import ROOT, evidence_dir, hashes


def main():
    import gmsh
    from rm_sim import gmsh_runtime
    from rm_sim.cad.assembly_import import import_assembly, sync_parts_from_model
    from rm_sim.core.project import Project, Constraint, LoadCase, PressureLoad, TractionLoad, GravityLoad
    from rm_sim.core.materials import set_part_material
    from rm_sim.core.face_identity import fingerprint_from_face
    from rm_sim.solver.assembly_workflow import prepare_analysis, finish_analysis
    from rm_sim.solver.tasks import SolveScheduler
    from rm_sim.solver.history import load_archived_case
    from rm_sim.solver.display_store import save_display,load_display
    from rm_sim.solver.nodal_loads import evaluate_nodal_loads,UnsupportedLoad
    from rm_sim.solver.fields import build_result_fields
    from rm_sim.config import get_ccx_path,load_config
    from PySide6.QtCore import QCoreApplication,QEvent
    from PySide6.QtWidgets import QApplication,QTableWidget
    from rm_sim.gui.result_dialogs import ReactionTableDialog
    os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
    app=QApplication.instance() or QApplication([])
    # Curved quadratic face: z=4*delta*u*(1-u-v), x=u,y=v.
    # Unit inward pressure integrates to (0,2*delta/3,1/2), including its curved edge.
    delta=.1
    unit_nodes={i+1:p for i,p in enumerate(((0,0,0),(1,0,0),(0,1,0),(0,0,1),
        (.5,0,delta),(.5,.5,0),(0,.5,0),(0,0,.5),(.5,0,.5),(0,.5,.5)))}
    unit_mesh={'nodes':unit_nodes,'elems':{1:list(range(1,11))},'elsets':{'EALL':[1]},'nsets':{}}
    curved=evaluate_nodal_loads('*STEP\n*STATIC\n*DLOAD\n1,P1,1\n*END STEP',unit_mesh,unit_nodes)
    assert np.allclose(curved['applied_total_n'],[0,2*delta/3,.5],atol=1e-12),curved
    work = evidence_dir('reaction_semantics'); source = work/'block.step'
    with gmsh_runtime.session():
        gmsh.model.add('reaction_block')
        gmsh.model.occ.addBox(0,0,0,10,10,20)
        gmsh.model.occ.synchronize(); gmsh.write(str(source)); gmsh.model.remove()
    model = import_assembly(source)
    try:
        project = Project(name='Reaction semantics',project_dir=str(work))
        sync_parts_from_model(project,model); part = project.parts[0]
        set_part_material(project,part.part_id,'Q235')
        project.study.geometry_file = str(source)
        fixed = next(f for f in model.faces if f.normal[2]<-.999)
        top = next(f for f in model.faces if f.normal[2]>.999)
        side = next(f for f in model.faces if f.normal[0]<-.999)
        def ref(name,face):
            return dict(name=name,part_id=part.part_id,face_tag=face.tag,face=fingerprint_from_face(face))
        project.study.load_cases = [LoadCase(name='pressure + traction + gravity + loaded support',
            constraints=[Constraint(**ref('fixed',fixed))], loads=[
                PressureLoad(**ref('side pressure',side),pressure_mpa=.5),
                TractionLoad(**ref('top force',top),total_force_n=100.,direction=(0.,0.,-1.)),
                TractionLoad(**ref('force on support',fixed),total_force_n=7.,direction=(0.,1.,0.)),
                GravityLoad(name='weight',direction=(0.,0.,-1.))])]
        prepared = prepare_analysis(project,model,work/'mesh.inp',size=2.5)
        scheduler = SolveScheduler(get_ccx_path(load_config()),max_workers=1)
        task = scheduler.submit('mixed_reaction',prepared['deck'],work/'cases')
        try: scheduler.wait_task(task.task_id,timeout=60)
        finally:
            if task.state in ('pending','running'):
                scheduler.cancel(task.task_id); scheduler.wait_task(task.task_id,timeout=15)
        assert task.state=='completed',task.summary()
        completed = finish_analysis(project,task,prepared)
        fields = completed['fields']; info = fields.source['reaction']
        weight = 10*10*20*7.85e-9*9810
        expected = np.array([-100.,-7.,100.+weight])
        assert info['supported'],info
        assert np.linalg.norm(fields.rf_sum-expected)<.005,(fields.rf_sum,expected)
        raw = np.sum(list(fields.raw_rf_table.values()),axis=0)
        assert abs(raw[1])<1e-5 and abs(fields.rf_sum[1]+7)<1e-5
        assert np.linalg.norm(raw-fields.rf_sum)>1.,'Fixture does not distinguish raw and corrected RF'
        for node,value in fields.raw_rf_table.items():
            assert np.allclose(value,task.results.rf_block(prepared['fixed_sets'][0])[node])
        ctx=json.loads(completed['archive']['results_json'].read_text(encoding='utf-8'))
        assert ctx['equilibrium']['residual']<.005 and ctx['reaction_processing']['supported']
        report=completed['archive']['report_md'].read_text(encoding='utf-8')
        assert '支反力 = DAT原始RF' in report and '原始RF合力' in report
        save_display(work/'',completed,prepared)
        displayed=load_display(work)
        assert np.allclose(displayed['fields'].rf_sum,fields.rf_sum)
        assert displayed['fields'].raw_rf_table==fields.raw_rf_table
        dialog=ReactionTableDialog(displayed['fields'])
        table=dialog.findChild(QTableWidget)
        assert table.columnCount()==7 and '支反力' in dialog.windowTitle()
        assert abs(float(table.item(table.rowCount()-1,2).text())+7)<1e-5
        assert abs(float(table.item(table.rowCount()-1,5).text()))<1e-5
        dialog.close();dialog.deleteLater()
        QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete)
        before=hashes(task.case_dir)
        restored=load_archived_case(project,task.case_dir.name)
        assert np.allclose(restored['fields'].rf_sum,fields.rf_sum)
        assert hashes(task.case_dir)==before
        # Model an old archive which stored DAT RF without any processing metadata.
        legacy=work/'cases/case_900'; shutil.copytree(task.case_dir,legacy)
        old=dict(ctx);old.pop('reaction_processing');old['results']={**old['results'],'rf_sum_n':raw.tolist()}
        (legacy/'results.json').write_text(json.dumps(old,ensure_ascii=False,indent=2),encoding='utf-8')
        project.cases.append(replace(project.cases[-1],name='case_900',rf_sum_n=tuple(raw)))
        before=hashes(legacy)
        migrated=load_archived_case(project,'case_900')
        assert migrated['context']['history_reaction_recalculated']
        assert np.allclose(migrated['fields'].rf_sum,expected,atol=.005)
        assert migrated['context']['archived_rf_sum_n']==raw.tolist()
        assert hashes(legacy)==before,'Legacy restore rewrote its artifacts'
        old['results']['rf_sum_n'][1]+=1
        (legacy/'results.json').write_text(json.dumps(old),encoding='utf-8')
        try: load_archived_case(project,'case_900')
        except ValueError as exc: assert 'disagrees' in str(exc)
        else: raise AssertionError('Legacy RF summary tamper accepted')
        unsupported=prepared['deck'].replace('*STEP','*AMPLITUDE, NAME=A\n0,0,1,1\n*STEP',1)
        try: evaluate_nodal_loads(unsupported,prepared['mesh'],fields.rf_table)
        except UnsupportedLoad: pass
        else: raise AssertionError('Unsupported amplitude was treated as verified')
        unverified=build_result_fields(fields.node_ids,task.results,task.frd_path,
            fixed_set=prepared['fixed_sets'],vtu_path=fields.source['vtu'],inp_text=unsupported)
        assert not unverified.source['reaction']['supported']
        assert np.allclose(unverified.rf_sum,raw)
        dialog=ReactionTableDialog(unverified)
        assert '节点外力' in dialog.windowTitle()
        dialog.close();dialog.deleteLater()
        QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete)
        record={'expected_reaction_n':expected.tolist(),'raw_rf_n':raw.tolist(),
                'corrected_reaction_n':fields.rf_sum.tolist(),'reaction_processing':info,
                'residual_n':ctx['equilibrium']['residual'],'new_and_legacy_restore_read_only':True,
                'raw_table_survives_display_pack':True,'legacy_tamper_rejected':True}
        (work/'verification.json').write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
    finally:
        model.close();app.shutdown()
    print('VERIFY_REACTION_SEMANTICS: PASS',work,flush=True)


if __name__=='__main__': main()
