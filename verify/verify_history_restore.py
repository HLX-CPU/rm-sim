"""Relocated saved projects restore raw fields through real Qt actions, read-only."""
import os
os.environ['QT_QPA_PLATFORM']='offscreen'
from pathlib import Path
import json
import shutil
import numpy as np
from assembly_check_utils import ROOT, evidence_dir, hashes


def main():
    from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox
    from rm_sim.gui.main_window import MainWindow
    from rm_sim.gui.result_dialogs import CaseHistoryDialog
    from rm_sim.solver.history import load_archived_case
    app=QApplication.instance() or QApplication([])
    work=evidence_dir('history_restore');print('证据目录:',work,flush=True)
    warnings=[];QMessageBox.warning=lambda *a: warnings.append(str(a[2:]))
    summaries=[]
    for example in ['part_beam','assembly_contact']:
        directory=work/example;directory.mkdir()
        bundle=directory/'relocated.rmsimproject'
        shutil.copy2(ROOT/'examples'/example/'project.rmsimproject',bundle)
        QFileDialog.getOpenFileName=lambda *a,**k:(str(bundle),'')
        win=MainWindow();win._on_open_project();app.processEvents()
        assert not warnings,warnings
        assert win._act_show_mises.isEnabled() and win._act_open_report.isEnabled()
        assert win._result_fields is not None
        # The archived mesh must be used even when the original working path is gone.
        win.project.study.mesh_file='Z:/nonexistent/original_mesh.inp'
        before=hashes(Path(win.project.project_dir)/'cases')
        record=win.project.cases[-1]
        raw=load_archived_case(win.project,record.name)
        assert np.isclose(raw['fields'].max_u_mag(),record.max_u_mag_mm)
        dialog=CaseHistoryDialog(win.project,win)
        selected=[]
        def choose(name):selected.append(name);win._load_history_case(name)
        dialog.case_selected.connect(choose)
        dialog._open_selected();app.processEvents()
        assert selected==[record.name]
        assert '历史结果' in win.result_label.text()
        if example=='assembly_contact':
            assert set(win._part_views)=={'PART1','PART2'}
            win.select_result_part('PART2')
            assert len(win._active_result['points'])<len(win._result_fields.node_ids)
        assert before==hashes(Path(win.project.project_dir)/'cases'),'Restoring rewrote raw artifacts'
        summary=raw['archive']['results_json']
        ctx=json.loads(summary.read_text(encoding='utf-8'));ctx['results']['max_u_mag_mm']*=2
        summary.write_text(json.dumps(ctx,ensure_ascii=False),encoding='utf-8')
        try:load_archived_case(win.project,record.name)
        except ValueError as exc:assert 'disagrees' in str(exc)
        else:raise AssertionError('Tampered historical summary was accepted')
        from rm_sim.core.project_bundle import open_bundle,BundleError
        altered=hashes(Path(win.project.project_dir)/'cases')
        try:open_bundle(bundle,win.project.project_dir)
        except BundleError as exc:assert '已有不同内容' in str(exc)
        else:raise AssertionError('Reopen overwrote modified working files')
        assert altered==hashes(Path(win.project.project_dir)/'cases')
        win.invalidate_results()
        assert win._last_archive is None and not win._act_open_report.isEnabled()
        summaries.append({'example':example,'case':record.name,'raw_files_unchanged_during_restore':True,
                          'tamper_rejected':True,'max_u_mm':raw['fields'].max_u_mag()})
        dialog.close();win.solid_model.close();win.close()
    import zipfile
    with zipfile.ZipFile(bundle) as z:meta=json.loads(z.read('project.json'))
    meta['geometry']['bundled']='../escape.step'
    meta['part_geometries']={}
    bad=work/'bad_path.rmsimproject'
    with zipfile.ZipFile(bad,'w') as z:
        z.writestr('project.json',json.dumps(meta));z.writestr('../escape.step','forbidden')
    try:open_bundle(bad,work/'path_check')
    except BundleError as exc:assert '非法路径' in str(exc)
    else:raise AssertionError('Traversal path was accepted')
    assert not (work/'escape.step').exists()
    (work/'verification.json').write_text(json.dumps(summaries,ensure_ascii=False,indent=2),encoding='utf-8')
    print('VERIFY_HISTORY_RESTORE: PASS',flush=True)


if __name__=='__main__':main()
