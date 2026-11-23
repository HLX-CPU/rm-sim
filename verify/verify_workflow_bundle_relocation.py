"""Relocate a real completed background-workflow project without reading its old directory.

无参自检模式（模块45 修复「编排无参调用」）：不带参数运行时，自动定位最近的
后台工作流完成记录（E:/rm_sim/verification/*/verification.json 含 complete_work，
且 completed.json 与旧工程 cases/ 仍在）；找不到时现场跑一次最小两块装配后台
工作流（真实 prepare/solve/collect）产出完成记录再迁移复核。带参模式行为不变。
无可用记录且最小工作流也失败时以非零退出如实报告。
"""
from pathlib import Path
import builtins
import io
import json
import sys
import numpy as np
from assembly_check_utils import evidence_dir,hashes


def main():
    from rm_sim.core.store import from_dict
    from rm_sim.core.project_bundle import save_bundle,open_bundle
    from rm_sim.solver.history import load_archived_case
    source_record=SOURCE_RECORD
    task=Path(json.loads(source_record.read_text(encoding='utf-8'))['complete_work'])
    complete=json.loads((task/'completed.json').read_text(encoding='utf-8'))
    project=from_dict(complete['project']);old=Path(project.project_dir).resolve()
    before=hashes(old/'cases');work=evidence_dir('workflow_bundle_relocation')
    bundle=save_bundle(work/'project.rmsimproject',project,geometry_source=project.study.geometry_file,cases_root=old/'cases')
    extracted=work/'relocated';opened=open_bundle(bundle,extracted);loaded=opened['project']
    loaded.project_dir=str(extracted);loaded.study.geometry_file=str(opened['geometry_path'])
    record=loaded.cases[0]
    assert (extracted/'cases'/record.name/'geometry.step').read_bytes()==opened['geometry_path'].read_bytes()
    original_builtin,original_io=builtins.open,io.open
    blocked=[]
    def guard(original):
        def checked(file,*a,**kw):
            if not isinstance(file,int):
                path=Path(file).resolve()
                if path.is_relative_to(old):
                    blocked.append(str(path))
                    raise AssertionError('restoration tried to read the old project directory: '+str(path))
            return original(file,*a,**kw)
        return checked
    try:
        builtins.open=guard(original_builtin);io.open=guard(original_io)
        restored=load_archived_case(loaded,record.name)
    finally:builtins.open,io.open=original_builtin,original_io
    assert not blocked
    for key in ('dat','frd','vtu'):
        assert Path(restored['fields'].source[key]).resolve().is_relative_to(extracted.resolve())
    assert np.allclose(restored['fields'].rf_sum,complete['summary']['rf_sum_n'],rtol=1e-5,atol=1e-6)
    assert hashes(old/'cases')==before
    for name,sha in before.items():
        assert hashes(extracted/'cases')[name]==sha
    (work/'verification.json').write_text(json.dumps({'bundle':str(bundle),'extracted':str(extracted),
        'source_record':str(source_record),'old_project_reads_blocked':True,'old_project_reads_attempted':blocked,
        'original_case_hashes':before,'rf_sum_n':restored['fields'].rf_sum.tolist()},ensure_ascii=False,indent=2),encoding='utf-8')
    print('VERIFY_WORKFLOW_BUNDLE_RELOCATION: PASS',work,flush=True)


if __name__=='__main__':
    if hasattr(sys.stdout,'reconfigure'):sys.stdout.reconfigure(encoding='utf-8',errors='replace')
    if len(sys.argv)>1:
        SOURCE_RECORD=Path(sys.argv[1])
        main()
    else:
        import orchestration_cases as oc
        try:
            record=oc.find_latest_workflow_record()
            if record is not None:
                print(f'[无参自检] 自动定位最近后台工作流完成记录={record}',flush=True)
            else:
                print('[无参自检] 未找到后台工作流完成记录，现场跑一次最小两块装配后台工作流……',flush=True)
                record,_solved=oc.ensure_workflow_record(oc.new_evidence_dir('workflow_record_noarg')/'verification.json')
                print(f'[无参自检] 已现场求解并产出完成记录={record}',flush=True)
        except Exception as exc:
            print(f'VERIFY_WORKFLOW_BUNDLE_RELOCATION: FAIL（无可用完成记录，且最小两块装配'
                  f'后台工作流也失败：{type(exc).__name__}: {exc}）',flush=True)
            raise SystemExit(1)
        SOURCE_RECORD=record
        main()
