"""Actual Qt QProcess -> read-only SolidWorks export -> body identity -> persistence."""
import os
os.environ['QT_QPA_PLATFORM']='offscreen'
import time
import json
import hashlib
from pathlib import Path
from assembly_check_utils import ROOT,evidence_dir


def main():
    from PySide6.QtWidgets import QApplication,QFileDialog,QMessageBox
    from rm_sim.gui.main_window import MainWindow
    from rm_sim.core.project_bundle import save_bundle,open_bundle
    from rm_sim.core.materials import set_part_material
    from rm_sim.solver.assembly_workflow import prepare_analysis
    source=Path(r'F:\机械组文件\英雄——西南石油泓龙\快拆结构.SLDASM')
    work=evidence_dir('native_import');print('证据目录:',work,flush=True)
    before=hashlib.sha256(source.read_bytes()).hexdigest()
    app=QApplication.instance() or QApplication([])
    warnings=[];QMessageBox.warning=lambda *a: warnings.append(str(a[2:]))
    QFileDialog.getOpenFileName=lambda *a,**k:(str(source),'')
    QFileDialog.getExistingDirectory=lambda *a,**k:str(work)
    win=MainWindow();win._on_import_native()
    assert win._native_process is not None and not win.menuBar().isEnabled()
    heartbeats=0;deadline=time.monotonic()+300
    while win._native_process is not None and time.monotonic()<deadline:
        app.processEvents();time.sleep(.02);heartbeats+=1
    assert win._native_process is None,'Native conversion still running (not stopped automatically)'
    assert not warnings,warnings
    assert win.menuBar().isEnabled() and heartbeats>10
    assert len(win.project.parts)==20
    assert all('body' in p.name for p in win.project.parts)
    missing=[p for p in win.project.parts if not p.material_confirmed]
    assert len(missing)==10
    assert all(p.material_snapshot.yield_strength_mpa is None for p in win.project.parts if p.material_confirmed)
    try:prepare_analysis(win.project,win.solid_model,work/'must_not_mesh.inp')
    except ValueError as exc:assert '缺失材料' in str(exc)
    else:raise AssertionError('Unknown material inherited steel silently')
    set_part_material(win.project,missing[0].part_id,'45钢')
    assert missing[0].material_confirmed and len([p for p in win.project.parts if not p.material_confirmed])==9
    bundle=save_bundle(work/'native.rmsimproject',win.project,geometry_source=win.project.study.geometry_file)
    opened=open_bundle(bundle,work/'roundtrip')['project']
    assert opened.geometry_provenance==win.project.geometry_provenance
    assert [p.material_confirmed for p in opened.parts]==[p.material_confirmed for p in win.project.parts]
    assert hashlib.sha256(source.read_bytes()).hexdigest()==before
    record=next(work.glob('native_*/native_export.json'))
    report=json.loads(record.read_text(encoding='utf-8'))
    assert report['RebuildSucceeded'] and report['PreferencesRestored']
    assert all(p['HashBefore']==p['HashAfter'] for p in report['Parts'])
    (work/'verification.json').write_text(json.dumps({'status':'PASS','qt_event_loops':heartbeats,
        'body_count':20,'unassigned_materials_initially':10,'native_record':str(record),
        'source_unchanged':True},ensure_ascii=False,indent=2),encoding='utf-8')
    win.solid_model.close();win.close()
    print('VERIFY_NATIVE_IMPORT: PASS',flush=True)


if __name__=='__main__':main()
