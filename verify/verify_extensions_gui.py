"""通过主窗口实际扩展入口验证模态对话框、Chrono 图表与载荷传递。"""
import os
os.environ['QT_QPA_PLATFORM']='offscreen'
from pathlib import Path
import json
from assembly_check_utils import ROOT,evidence_dir


def main():
    from PySide6.QtWidgets import QApplication,QDialog,QFileDialog,QInputDialog,QMessageBox,QLabel
    from PySide6.QtCore import QTimer
    from rm_sim.gui.main_window import MainWindow
    from rm_sim.cad.assembly_import import import_assembly,sync_parts_from_model
    from rm_sim.core.project import Project,Constraint
    from rm_sim.core.face_identity import fingerprint_from_face
    work=evidence_dir('extensions_gui')
    app=QApplication.instance() or QApplication([])
    win=MainWindow();warnings=[];infos=[];dialogs=[]
    QMessageBox.warning=lambda *a,**k:warnings.append(str(a))
    QMessageBox.information=lambda *a,**k:infos.append(str(a))
    QFileDialog.getExistingDirectory=lambda *a,**k:str(work)
    def close_active():
        widget=app.activeModalWidget()
        assert isinstance(widget,QDialog)
        dialogs.append(widget.windowTitle())
        widget.close()
    QTimer.singleShot(0,close_active)
    win._on_chrono_demo()
    assert not warnings,warnings
    assert hasattr(win,'_chrono_source') and Path(win._chrono_source).is_file()
    QFileDialog.getOpenFileName=lambda *a,**k:(win._chrono_source,'JSON')
    win._on_chrono_transfer()
    assert not warnings and infos,(warnings,infos)
    assert list(work.glob('transfer_*/load_transfer.json'))
    model=import_assembly(ROOT/'samples/beam_100x10x10.step');win.solid_model=model
    win.project=Project(project_dir=str(work/'modal'))
    win.project.study.geometry_file=str(model.path);sync_parts_from_model(win.project,model)
    face=next(f for f in model.faces if f.normal[0]<-.999)
    win.project.study.load_cases[0].constraints=[Constraint(name='固定',part_id='p1',face_tag=face.tag,face=fingerprint_from_face(face))]
    QInputDialog.getInt=lambda *a,**k:(3,True)
    QTimer.singleShot(0,close_active)
    win._on_modal_analysis()
    assert not warnings,warnings
    assert len(dialogs)==2 and any('模态' in x for x in dialogs) and any('Chrono' in x for x in dialogs)
    win.close();model.close()
    (work/'verification.json').write_text(json.dumps({'status':'PASS','dialogs':dialogs,'infos':infos},ensure_ascii=False,indent=2),encoding='utf-8')
    print('VERIFY_EXTENSIONS_GUI: PASS 主窗口Chrono、映射、模态入口均真实执行',flush=True)


if __name__=='__main__':main()
