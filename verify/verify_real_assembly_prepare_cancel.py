"""Machine-local 20-body CAD: cross-process identity, responsive preparation cancel.

This does not submit CCX or validate full-assembly strength. Kept outside the
portable default suite because the user-provided analysis copy is machine-local.
"""
import os
os.environ['QT_QPA_PLATFORM']='offscreen'
from pathlib import Path
from dataclasses import replace
import hashlib
import json
import sys
import time
from assembly_check_utils import ROOT,evidence_dir,hashes


def main():
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication,QMessageBox
    from rm_sim.cad.assembly_import import import_assembly,sync_parts_from_model
    from rm_sim.core.project import Project,Material,LoadCase,Constraint,GravityLoad
    from rm_sim.core.connections import apply_connection_draft
    from rm_sim.core.face_identity import fingerprint_from_face
    from rm_sim.gui.main_window import MainWindow
    from rm_sim.solver.process_memory import process_memory
    directory=Path('E:/rm_sim/verification/quick_release_assembly_20261102_1020')
    source=directory/'assembly_thread_partition.step'
    sha=hashlib.sha256(source.read_bytes()).hexdigest()
    work=evidence_dir('real_assembly_prepare_cancel')
    model=import_assembly(source)
    labels={r['tag']:r['name'] for r in json.loads((directory/'partition.json').read_text(encoding='utf-8'))['part_mapping']}
    model.parts=tuple(replace(p,name=labels[p.tag]) for p in model.parts)
    app=QApplication.instance() or QApplication([]);win=MainWindow()
    win.project=Project(name='20-body cancellation integration',project_dir=str(work));win.solid_model=model
    sync_parts_from_model(win.project,model)
    for p in win.project.parts:
        steel=any(w in p.name for w in ('销钉','轴承'))
        m=(Material(name='钢（假设弹性）') if steel else Material(name='6061（参考弹性）',e_modulus_mpa=68900.,poisson=.33,density_t_mm3=2.7e-9))
        p.material_name=m.name;p.material_snapshot=m;p.material_confirmed=True
    draft=json.loads(Path('E:/rm_sim/verification/draft_qr_asm_named_20261102_120005/draft.json').read_text(encoding='utf-8'))
    draft['candidates']=[c for c in draft['candidates'] if c['common_area_mm2']>=1]
    applied=apply_connection_draft(win.project,model,draft)
    assert applied['added']==39 and not applied['errors'],applied
    fixed=[]
    for p in win.project.parts:
        if 'NEW快拆' not in p.name:continue
        faces=[f for f in model.faces if f.solid_tag==p.solid_tag and f.normal[1]<-.999999]
        bottom=min(f.center[1] for f in faces)
        fixed.extend(Constraint(name='壳体底面理想固定（测试假设）',face_tag=f.tag,
            face=fingerprint_from_face(f),part_id=p.part_id) for f in faces if abs(f.center[1]-bottom)<1e-5)
    win.project.study.load_cases=[LoadCase(name='自重准备测试，非强度验证',constraints=fixed,loads=[GravityLoad(name='自重')])]
    win.project.study.mesh_size_mm=3.
    warnings=[];QMessageBox.warning=lambda *a,**kw:warnings.append(str(a[2]))
    beats=[];timer=QTimer();timer.timeout.connect(lambda:beats.append(time.monotonic()));timer.start(10)
    def spin(condition,timeout=30):
        end=time.monotonic()+timeout
        while time.monotonic()<end:
            app.processEvents()
            if condition():return
            time.sleep(.005)
        raise AssertionError(win._solve_task.summary())
    try:
        start=time.monotonic();win._on_solve_static();submitted=time.monotonic()-start
        task=win._solve_task
        spin(lambda:task.phase=='meshing' or task.state=='failed')
        assert task.state!='failed',(task.summary(),warnings)
        started=time.monotonic();win._solve_cancel_button.click()
        spin(lambda:task.state=='cancelled' and win._solve_cancel_button is None)
        cancelled=time.monotonic()-started
        assert submitted<.3 and cancelled<3,(submitted,cancelled)
        events=[json.loads(s) for s in (task.work/'phase.events.jsonl').read_text(encoding='utf-8').splitlines()]
        assert 'meshing' in {e['phase'] for e in events}
        for pid in {r['worker_pid'] for r in events}:spin(lambda:process_memory(pid) is None,5)
        assert not (work/'cases').exists() and not win.project.cases and not warnings
        assert hashlib.sha256(source.read_bytes()).hexdigest()==sha
        gaps=[b-a for a,b in zip(beats,beats[1:])]
        assert len(beats)>10 and max(gaps)<.35,max(gaps)
        (work/'verification.json').write_text(json.dumps({'bodies':20,'connections':39,'supports':[c.part_id for c in fixed],
            'submitted_s':submitted,'cancelled_s':cancelled,'max_heartbeat_gap_s':max(gaps),
            'source_sha256':sha,'workflow':str(task.work),'ccx_submitted':False,'scope':'preparation and cancellation only'},
            ensure_ascii=False,indent=2),encoding='utf-8')
        print('VERIFY_REAL_ASSEMBLY_PREPARE_CANCEL: PASS',work,flush=True)
    finally:
        timer.stop()
        if win._solve_task and win._solve_task.state in ('pending','running'):
            win._cancel_current_solve();spin(lambda:win._solve_task.state not in ('pending','running'),15)
        win.close();model.close()


if __name__=='__main__':main()
