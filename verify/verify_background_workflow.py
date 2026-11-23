"""Real Qt heartbeat through prepare/solve/collect; cancellation preserves old cases."""
import os
os.environ['QT_QPA_PLATFORM']='offscreen'
import json
from pathlib import Path
import time
import shutil
import hashlib
import numpy as np
from assembly_check_utils import ROOT,evidence_dir,hashes,check_physics


def main():
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication,QMessageBox,QInputDialog,QFileDialog
    from rm_sim.gui.main_window import MainWindow
    from rm_sim.cad.assembly_import import import_assembly,sync_parts_from_model
    from rm_sim.core.project import Project,LoadCase,Constraint,TractionLoad
    from rm_sim.core.materials import set_part_material
    from rm_sim.core.face_identity import fingerprint_from_face
    from rm_sim.core.store import to_dict
    from rm_sim.solver.process_memory import process_memory
    work=evidence_dir('background_workflow')
    app=QApplication.instance() or QApplication([])
    warnings=[];QMessageBox.warning=lambda *a,**kw:warnings.append(str(a[2]))
    model=import_assembly(ROOT/'samples/beam_100x10x10.step')
    win=MainWindow();win.project=Project(name='Background workflow',project_dir=str(work))
    sync_parts_from_model(win.project,model);win.solid_model=model
    p=win.project.parts[0];set_part_material(win.project,p.part_id,'Q235')
    fixed=next(f for f in model.faces if f.normal[0]<-.999)
    load=next(f for f in model.faces if f.normal[0]>.999)
    win.project.study.mesh_size_mm=2.
    win.project.study.load_cases=[LoadCase(name='100N',constraints=[Constraint(name='fixed',face_tag=fixed.tag,
        face=fingerprint_from_face(fixed),part_id=p.part_id)],loads=[TractionLoad(name='force',face_tag=load.tag,
        face=fingerprint_from_face(load),part_id=p.part_id,total_force_n=100.,direction=(0.,-1.,0.))])]
    definition=win.project.study.load_cases[0]
    # Match the user flow: the model is visible before clicking Compute. First
    # GL/fallback initialization belongs to startup, not this workflow benchmark.
    win.show_assembly();app.processEvents()
    beats=[];timer=QTimer();timer.timeout.connect(lambda:beats.append(time.monotonic()));timer.start(10)
    def spin(condition,timeout=90):
        end=time.monotonic()+timeout
        while time.monotonic()<end:
            app.processEvents()
            if condition():return
            time.sleep(.005)
        raise AssertionError('workflow timed out: '+win._solve_task.summary())
    def finish(task):spin(lambda:task.state not in ('pending','running') and win._solve_cancel_button is None)
    def events(task):return [json.loads(s) for s in (task.work/'phase.events.jsonl').read_text(encoding='utf-8').splitlines()]
    def assert_workers_stopped(task):
        for pid in {r['worker_pid'] for r in events(task)}:
            spin(lambda:process_memory(pid) is None,timeout=5)
    try:
        start=time.monotonic();win._on_solve_static();submit_seconds=time.monotonic()-start
        assert submit_seconds<.3,submit_seconds
        task=win._solve_task;finish(task)
        assert task.state=='completed' and not warnings,(task.summary(),warnings)
        assert win._last_archive is not None and task.result is None,'old workflow must not retain display arrays'
        ev=events(task);phases={r['phase'] for r in ev}
        assert {'meshing','building_deck','reading_results','rendering_report'}<=phases,phases
        assert len({r['worker_pid'] for r in ev})==2,'preparation and collection must use different processes'
        assert_workers_stopped(task)
        assert task.solver_task.parse_ok and task.solver_task.results is None,'no duplicate FRD in orchestrator'
        assert 'mesh' not in win._prepared,'the GUI should consume numeric display data, not retain a duplicate parsed INP'
        gaps=[b-a for a,b in zip(beats,beats[1:])]
        assert len(beats)>30 and max(gaps)<.35, max(gaps)
        successful=task.case_dir;record=win.project.cases[0]
        assert (successful/'geometry.step').read_bytes()==(ROOT/'samples/beam_100x10x10.step').read_bytes()
        assert (successful/'guijob.workflow.json').is_file() and (successful/'guijob.phase.events.jsonl').is_file()
        original_fields=(win._result_fields.max_u_mag(),float(win._result_fields.s_mises.max()),win._result_fields.rf_sum.copy())
        assert np.linalg.norm(original_fields[2]-np.array([0.,100.,0.]))<.01
        # Display transport corruption must be rejected before it reaches a view.
        from rm_sim.solver.display_store import load_display
        negative=work/'corrupt_display';negative.mkdir()
        for name in ('display.json','display.npz'):shutil.copyfile(task.work/name,negative/name)
        with (negative/'display.npz').open('r+b') as stream:
            stream.seek(-1,2);byte=stream.read(1);stream.seek(-1,2);stream.write(bytes([byte[0]^1]))
        try:load_display(negative)
        except ValueError as exc:assert '校验失败' in str(exc),str(exc)
        else:raise AssertionError('corrupted display data was accepted')
        previous=hashes(successful);index=to_dict(win.project.cases)
        checks={'submit_seconds':submit_seconds,'heartbeat_count':len(beats),'max_heartbeat_gap_s':max(gaps),
                'complete_work':str(task.work),'worker_pids':sorted({r['worker_pid'] for r in ev})}
        # Cancel actual quadratic meshing, not a mocked delay.
        win.project.study.mesh_size_mm=.45;win._on_solve_static();task=win._solve_task
        spin(lambda:task.phase=='meshing');win._solve_cancel_button.click();finish(task)
        assert task.state=='cancelled' and to_dict(win.project.cases)==index
        assert hashes(successful)==previous;assert_workers_stopped(task)
        assert not (work/'cases/case_002').exists(),'cancelled preparation launched CCX'
        checks['cancel_prepare']=str(task.work)
        # Cancel during real Agg rendering/archiving after actual CCX finished.
        win.project.study.mesh_size_mm=2.;win._on_solve_static();task=win._solve_task
        spin(lambda:task.phase=='rendering_report');win._solve_cancel_button.click();finish(task)
        assert task.state=='cancelled' and to_dict(win.project.cases)==index
        assert task.solver_task.job_finished and task.solver_task.dat_path.stat().st_size>0
        assert hashes(successful)==previous;assert_workers_stopped(task)
        checks['cancel_collect']=str(task.work)
        # A cancelled mesh export cannot overwrite an existing user-selected file.
        target=work/'exported.inp';target.write_bytes(b'previous selected export\n')
        QInputDialog.getDouble=lambda *a,**kw:(.45,True)
        QFileDialog.getSaveFileName=lambda *a,**kw:(str(target),'')
        win._on_generate_mesh();task=win._solve_task
        spin(lambda:task.phase=='meshing');win._solve_cancel_button.click();finish(task)
        assert task.state=='cancelled' and target.read_bytes()==b'previous selected export\n'
        assert hashes(successful)==previous;assert_workers_stopped(task)
        # Material validation failure never starts a new solver case.
        count=len(list((work/'cases').iterdir()))
        win.project.parts[0].material_confirmed=False;win._on_solve_static();task=win._solve_task;finish(task)
        assert task.state=='failed' and '缺失材料' in task.error
        assert len(list((work/'cases').iterdir()))==count and win.menuBar().isEnabled()
        assert len(warnings)==1 and '缺失材料' in warnings[0],warnings
        win.project.parts[0].material_confirmed=True;warnings.clear()
        # Use the real retry menu after an actual validation failure. The new
        # task must preserve failed evidence and append a distinct solved case.
        from rm_sim.gui.mesh_dialogs import MeshControlsDialog
        failed_task=task;failed_hashes=hashes(failed_task.work)
        original_exec=MeshControlsDialog.exec
        MeshControlsDialog.exec=lambda self: self.DialogCode.Accepted
        QInputDialog.getItem=lambda *a,**kw:('当前设置',True)
        try:
            retry=next(a for menu in win.menuBar().actions() if menu.menu()
                       for a in menu.menu().actions() if a.text()=='重新运行上次失败任务…')
            retry.trigger();task=win._solve_task;finish(task)
        finally:MeshControlsDialog.exec=original_exec
        assert task.state=='completed' and task.work!=failed_task.work and not warnings
        assert hashes(failed_task.work)==failed_hashes
        assert len(list((work/'cases').iterdir()))==count+1
        index=to_dict(win.project.cases)
        checks['gui_retry_preserves_failed_evidence']=str(failed_task.work)
        # Mesh preview is a geometry operation; it does not confirm material or
        # require an artificial fixed support / load just to see a mesh.
        win.project.parts[0].material_confirmed=False
        win.project.study.load_cases=[]
        QInputDialog.getDouble=lambda *a,**kw:(2.,True)
        win._on_generate_mesh();task=win._solve_task;finish(task)
        assert task.state=='completed' and not warnings
        assert not win.project.parts[0].material_confirmed and not win.project.study.load_cases
        assert target.read_bytes()==(task.work/'mesh.inp').read_bytes()
        assert to_dict(win.project.cases)==index and win.project.project_dir==str(work)
        assert hashes(successful)==previous
        win._load_history_case(record.name)
        assert hashes(successful)==previous and win._history_case_name==record.name
        assert np.isclose(win._result_fields.max_u_mag(),original_fields[0],rtol=1e-6)
        assert np.isclose(float(win._result_fields.s_mises.max()),original_fields[1],rtol=1e-6)
        assert np.allclose(win._result_fields.rf_sum,original_fields[2],rtol=1e-6,atol=1e-8)
        # The new workflow also honours window close during native meshing.
        win.project.parts[0].material_confirmed=True
        win.project.study.load_cases=[definition];win.project.study.mesh_size_mm=.45
        win._on_solve_static();task=win._solve_task
        spin(lambda:task.phase=='meshing');win.close();finish(task)
        assert task.state=='cancelled' and win._close_after_solve
        assert_workers_stopped(task);assert hashes(successful)==previous
        checks['close_during_prepare']=str(task.work)
        (work/'verification.json').write_text(json.dumps(checks,indent=2),encoding='utf-8')
        print('VERIFY_BACKGROUND_WORKFLOW: PASS',work,flush=True)
    finally:
        timer.stop()
        if win._solve_task and win._solve_task.state in ('pending','running'):
            win._cancel_current_solve();spin(lambda:win._solve_task.state not in ('pending','running'),15)
        win.close();model.close()


if __name__=='__main__':main()
