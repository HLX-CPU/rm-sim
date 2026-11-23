"""Evidence ledger for the current real-model milestone; never a full-goal pass."""
from pathlib import Path
import json
import hashlib
import sys
import numpy as np
from assembly_check_utils import ROOT
from rm_sim.solver.results import parse_dat
from rm_sim.solver.deck import parse_mesh_inp


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    work=Path(sys.argv[1]); native=json.loads((work/'native/native_export.json').read_text(encoding='utf-8'))
    sweep=json.loads((work/'strength_v1/sweep.json').read_text(encoding='utf-8'))
    convergence=json.loads((work/'strength_v1/convergence/convergence.json').read_text(encoding='utf-8'))
    pose=json.loads((work/'closure_v2/manifest.json').read_text(encoding='utf-8'))
    current_sources=[{'path':native['Source'],'sha256':sha(native['Source']),'expected':native['HashBefore'].lower()}]
    current_sources += [{'path':p['Source'],'sha256':sha(p['Source']),'expected':p['HashBefore'].lower()} for p in native['Parts']]
    assert all(r['sha256']==r['expected'] for r in current_sources)
    raw=[]
    all_rows=sweep['rows']+[r for r in convergence['rows'] if r['mesh_mm']!=.8]
    assert len(all_rows)==44
    for row in all_rows:
        directory=Path(row['case_dir']);text=(directory/'hook.inp').read_text(encoding='utf-8')
        mesh=parse_mesh_inp(text)
        force=np.zeros(3);cload=False
        for line in text.splitlines():
            line=line.strip()
            if not line or line.startswith('**'):continue
            if line.startswith('*'):
                cload=line.upper().split(',')[0]=='*CLOAD'
            elif cload:
                target,dof,value=[s.strip() for s in line.split(',')[:3]]
                count=1 if target.isdigit() else len(mesh['nsets'][target.upper()])
                force[int(dof)-1]+=float(value)*count
        rf=np.sum(list(parse_dat(directory/'hook.dat')[('forces','FIXED')].values()),axis=0)
        residual=float(np.linalg.norm(force+rf))
        assert residual<.01
        assert 'Job finished' in (directory/'hook.log').read_text(encoding='utf-8',errors='replace')
        raw.append({'case_dir':str(directory),'cload_sum_n':force.tolist(),'rf_sum_n':rf.tolist(),'residual_n':residual,
                    'sha256':{p.name:sha(p) for p in [directory/'hook.inp',directory/'hook.dat',directory/'hook.frd',directory/'hook.log']}})
    names=['main_entry','assembly_import','material_library','gui_assembly','history_restore','native_import',
           'assembly_geometry','project_roundtrip','assembly_project_roundtrip','report_archive','step_material_gate',
           'chrono_load_transfer','extensions_gui']
    latest={}
    for path in sorted(Path('E:/rm_sim/verification').glob('regression_20261102_*/summary.json')):
        for row in json.loads(path.read_text(encoding='utf-8')):
            if row['check'] in names:latest[row['check']]=row
    assert set(latest)==set(names)
    for row in latest.values():
        assert row['exit_code']==0,row
        row['stdout_sha256']=sha(row['stdout']);row['stderr_sha256']=sha(row['stderr'])
    source_files=[]
    for directory in ['app','verify','tools']:
        source_files += [p for p in (ROOT/directory).rglob('*') if p.suffix in ['.py','.ps1'] and '__pycache__' not in p.parts]
    source_files += list((ROOT/'probes').glob('quick_release_*.py'))
    result={'objective':'让整套软件满足实际使用要求','goal_achieved':False,
            'status':'real_model_milestone_with_failed_stress_convergence',
            'original_sources_unchanged':current_sources,'latest_scoped_regressions':latest,
            'real_solver_runs':raw,'real_solver_count':44,'closure_poses':pose,
            'stress_convergence':convergence,'gui_actual_hook_history':json.loads((work/'strength_v1/gui_history_actual_hooks.json').read_text(encoding='utf-8')),
            'original_steel_aluminum_peak_gate':'not_met; this milestone does not replace it',
            'not_verified_or_implemented':['Actual complete-assembly force/contact solve','Actual spring return and insertion trajectory',
                'Real material strength and fastener compliance','Converged hook peak stress','General CAD mechanism editor',
                'Assembly modal and forced vibration','Impact stress, plasticity and damage','Native desktop GL interaction',
                'Portable installation package'],
            'source_sha256':{str(p.relative_to(ROOT)):sha(p) for p in sorted(source_files)}}
    target=ROOT/'实际使用验收证据_快拆结构_2026-11-02.json'
    target.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'path':str(target),'solver_runs':len(raw),'regressions':len(latest),
                      'stress_convergence':convergence['status'],'goal_achieved':False},ensure_ascii=False))


if __name__=='__main__':main()
