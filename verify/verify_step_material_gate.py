"""GUI STEP import must request an explicit material before preparing a solve."""
import os
os.environ['QT_QPA_PLATFORM']='offscreen'
from assembly_check_utils import ROOT,evidence_dir


def main():
    from PySide6.QtWidgets import QApplication,QFileDialog,QMessageBox
    from rm_sim.gui.main_window import MainWindow
    from rm_sim.core.materials import set_part_material
    from rm_sim.solver.assembly_workflow import prepare_analysis
    app=QApplication.instance() or QApplication([]);win=MainWindow()
    work=evidence_dir('step_material_gate');warnings=[]
    QMessageBox.warning=lambda *a:warnings.append(a)
    QFileDialog.getOpenFileName=lambda *a,**k:(str(ROOT/'samples/beam_100x10x10.step'),'')
    win._on_import_step();app.processEvents()
    assert not warnings,warnings
    assert len(win.project.parts)==1 and not win.project.parts[0].material_confirmed
    try:prepare_analysis(win.project,win.solid_model,work/'must_not_mesh.inp')
    except ValueError as exc:assert '缺失材料' in str(exc)
    else:raise AssertionError('STEP silently inherited placeholder steel')
    set_part_material(win.project,win.project.parts[0].part_id,'6061')
    assert win.project.parts[0].material_confirmed
    win.solid_model.close();win.close()
    (work/'verification.txt').write_text('STEP import -> material missing -> solve refused -> explicit material accepted\n',encoding='utf-8')
    print('VERIFY_STEP_MATERIAL_GATE: PASS',work,flush=True)


if __name__=='__main__':main()
