"""GUI-free process entry for preparation and collection; never edit source CAD."""
from pathlib import Path
import hashlib
import json
import shutil
import sys
import time
import traceback
from .prepared_store import write_json,save_prepared,load_prepared


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()


def run(spec):
    from ..core.store import from_dict,to_dict
    root=Path(spec['work'])
    def progress(phase):
        record={'phase':phase,'operation':spec['operation'],'worker_pid':__import__('os').getpid(),'at':time.time()}
        with (root/'phase.events.jsonl').open('a',encoding='utf-8') as events:
            events.write(json.dumps(record,ensure_ascii=False)+'\n')
        write_json(root/'phase.json',record)
        print(phase,flush=True)
    if spec['operation']=='prepare':
        from ..cad.assembly_import import import_assembly
        from .assembly_workflow import prepare_analysis
        progress('snapshotting')
        source=Path(spec['source'])
        before=digest(source)
        snapshot=root/'geometry.step'
        with source.open('rb') as src,snapshot.open('xb') as dst:
            shutil.copyfileobj(src,dst)
        if digest(source)!=before or digest(snapshot)!=before:
            raise ValueError('几何文件在准备期间发生变化，请重新导入后计算')
        project=from_dict(spec['project'])
        project.study.geometry_file=str(snapshot)
        project.geometry_provenance={**project.geometry_provenance,'task_geometry_source':str(source),
                                     'task_geometry_sha256':before,'task_geometry_snapshot':str(snapshot)}
        progress('loading_geometry')
        model=import_assembly(snapshot)
        try:
            prepared=prepare_analysis(project,model,root/'mesh.inp',size=spec.get('size'),
                auto_mesh=spec.get('auto_mesh',True),mesh_kwargs=spec.get('mesh_kwargs'),progress=progress,
                mesh_only=spec.get('mesh_only',False))
            progress('saving_preparation')
            save_prepared(root,prepared,project)
        finally:model.close()
        progress('prepared')
    elif spec['operation']=='collect':
        from .assembly_workflow import finish_analysis
        from .tasks import SolveTask,STATE_COMPLETED
        from .results import load_results
        progress('loading_preparation')
        project,prepared=load_prepared(root)
        solver=spec['solver']
        case=Path(solver['case_dir'])
        geometry=case/'geometry.step'
        if digest(geometry)!=project.geometry_provenance['task_geometry_sha256']:
            raise ValueError('本工况几何快照与准备记录不符，不能整理结果')
        project.study.geometry_file=str(geometry)
        task=SolveTask(task_id=solver['task_id'],job_name=solver['job_name'],case_dir=case,
            inp_path=case/(solver['job_name']+'.inp'),state=STATE_COMPLETED,job_finished=solver['job_finished'],
            exit_code=solver['exit_code'],exit_code_note=solver['exit_code_note'],resource_usage=solver['resource_usage'])
        if not task.job_finished:
            raise ValueError('求解器未正常结束，不能整理为有效结果')
        progress('reading_results')
        task.results=load_results(task.dat_path,task.frd_path)
        task.parse_ok=True
        completed=finish_analysis(project,task,prepared,progress=progress)
        from .display_store import save_display
        save_display(root,completed,prepared,progress=progress)
        task_meta=case/(task.job_name+'.task.json')
        previous=json.loads(task_meta.read_text(encoding='utf-8'))
        write_json(task_meta,{**previous,'state':'completed','parse_ok':True,'collection_completed':True,
                             'solver_diagnostics':completed['summary']['solver_diagnostics']})
        # Publish only after fields, report and project index are complete.
        write_json(root/'completed.json',{'project':to_dict(project),'summary':completed['summary']})
        progress('collected')
    else:
        raise ValueError('未知后台任务阶段')


def main():
    path=Path(sys.argv[1]);spec=json.loads(path.read_text(encoding='utf-8'))
    try:
        run(spec)
        return 0
    except Exception as exc:
        write_json(Path(spec['work'])/'error.json',{'error':str(exc),'exception_type':type(exc).__name__,
                                                   'operation':spec['operation']})
        traceback.print_exc()
        return 2


if __name__=='__main__':raise SystemExit(main())
