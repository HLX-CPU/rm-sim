"""Actual Qt worker inspection, candidate-to-editor ownership, and cancellation."""
import os
os.environ['QT_QPA_PLATFORM']='offscreen'
import time,json
from pathlib import Path
from assembly_check_utils import ROOT,evidence_dir


def main():
    from PySide6.QtWidgets import QApplication,QMessageBox,QPushButton
    from rm_sim.gui.main_window import MainWindow
    from rm_sim.gui.inspection_dialog import AssemblyInspectionDialog
    from rm_sim.gui.setup_dialog import SetupDialog
    from rm_sim.cad.assembly_import import import_assembly,sync_parts_from_model
    from rm_sim.core.project import Project
    work=evidence_dir('geometry_inspection_gui');print('证据目录:',work,flush=True)
    app=QApplication.instance() or QApplication([]);warnings=[]
    QMessageBox.warning=lambda *a:warnings.append(str(a[2:]))
    win=MainWindow();win.project=Project(project_dir=str(work))
    win.solid_model=import_assembly(ROOT/'samples/assembly_two_blocks.step')
    sync_parts_from_model(win.project,win.solid_model)
    chosen=[]
    def setup_exec(dialog):
        a=dialog.face_a.currentData();b=dialog.face_b.currentData()
        assert a[0]!=b[0]
        assert dialog.kind.currentText()=='可分离接触与摩擦'
        assert not win.project.connections,'Candidate selection automatically created a joint'
        dialog.add_definition()
        assert dialog.changed
        chosen.append([a,b]);return 1
    def inspection_exec(dialog):
        assert dialog.contact_table.rowCount()==1
        dialog.use_selected();return 1
    SetupDialog.exec=setup_exec;AssemblyInspectionDialog.exec=inspection_exec
    win._on_inspect_geometry()
    assert win._inspection_process is not None and not win.menuBar().isEnabled()
    deadline=time.monotonic()+90;heartbeats=0
    while win._inspection_process is not None and time.monotonic()<deadline:
        app.processEvents();time.sleep(.01);heartbeats+=1
    assert win._inspection_process is None and not warnings,warnings
    assert len(chosen)==1 and len(win.project.connections)==1
    assert win.menuBar().isEnabled()
    report=next(work.glob('geometry_checks/*/inspection.json'))
    raw=json.loads(report.read_text(encoding='utf-8'))
    assert raw['status']=='completed' and raw['geometry']['interference_count']==0
    # Killing only this newly-created read-only worker must leave a cancellation
    # receipt and must never present a missing result as a completed check.
    win._on_inspect_geometry()
    cancel=win._inspection_cancel_button
    assert cancel is not None
    cancel.click()
    deadline=time.monotonic()+20
    while win._inspection_process is not None and time.monotonic()<deadline:
        app.processEvents();time.sleep(.01)
    assert win._inspection_process is None and win.menuBar().isEnabled()
    receipts=list(work.glob('geometry_checks/*/cancelled.json'));assert len(receipts)==1
    assert not warnings,warnings
    win.solid_model.close();win.close()
    (work/'verification.json').write_text(json.dumps({'qt_event_loops':heartbeats,'candidate_faces':chosen,
        'report':str(report),'cancellation_receipt':str(receipts[0])},ensure_ascii=False,indent=2),encoding='utf-8')
    print('VERIFY_GEOMETRY_INSPECTION_GUI: PASS',flush=True)


if __name__=='__main__':main()
