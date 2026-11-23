"""Asynchronous static workflow with sequential process-isolated heavy stages."""
from __future__ import annotations
from dataclasses import dataclass,field
import datetime
import json
from pathlib import Path
import sys
import threading
import time
import uuid

from .prepared_store import write_json,load_prepared
from .tasks import SolveScheduler,STATE_SOLVED
from .process_group import OwnedProcessGroup


class _Cancelled(Exception):pass


#: gmsh netgen 高阶优化器崩溃的固定文案（native terminate，Python 接不住，
#: 只能靠后台进程边界把它变成「本次尝试失败」）。
_MESH_CRASH_SIGNATURES = ("Failed to reach critical value in pass 1 for measure(s)",
                          "Failed to reach critical value")
_MESH_CRASH_KIND = "mesh_optimizer_crash"


class WorkerCrashed(RuntimeError):
    """后台进程以非零码退出且没有写 error.json（典型：native 崩溃）。"""

    def __init__(self, kind, detail, log_path):
        super().__init__(detail)
        self.kind = kind
        self.detail = detail
        self.log_path = log_path


def classify_worker_crash(text):
    """从后台进程 stderr 尾部识别已知原生崩溃；未知返回 None。"""
    for signature in _MESH_CRASH_SIGNATURES:
        if signature in text:
            return _MESH_CRASH_KIND
    return None


def _tail(path, limit=8192):
    try:
        raw = Path(path).read_bytes()
    except OSError:
        return ""
    return raw[-limit:].decode("utf-8", errors="replace")


@dataclass
class WorkflowTask:
    task_id: str
    work: Path
    state: str='pending'
    phase: str='pending'
    error: str=''
    started_at: float=field(default_factory=time.monotonic)
    finished_at: float|None=None
    cancel_event: threading.Event=field(default_factory=threading.Event,repr=False)
    solver_task: object=None
    result: dict|None=None
    resources: dict=field(default_factory=dict)
    phase_history: list=field(default_factory=list)

    @property
    def case_dir(self):return self.solver_task.case_dir if self.solver_task is not None else self.work
    def summary(self):return f'{self.state}/{self.phase}: {self.error}; {self.work}'


class WorkflowScheduler:
    def __init__(self,ccx_path):
        self.ccx_path=Path(ccx_path)
        self._tasks={}

    def submit(self,project,source,*,size=None,mesh_only=False,mesh_kwargs=None,export_path=None,face_fingerprints=None):
        from ..core.store import to_dict,from_dict
        from ..core.materials import part_material
        from dataclasses import replace
        work=Path(project.project_dir)/'tasks'/('task_'+datetime.datetime.now().strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:8])
        work.mkdir(parents=True,exist_ok=False)
        snapshot=from_dict(to_dict(project))
        for case in snapshot.study.load_cases:
            for owner in [*case.constraints,*case.loads]:
                if getattr(owner,'face_tag',0)>0 and getattr(owner,'face',None) is None:
                    if not face_fingerprints or owner.face_tag not in face_fingerprints:
                        raise ValueError('固定或加载面的身份未保存，请重新选面后计算')
                    owner.face=face_fingerprints[owner.face_tag]
        for part in snapshot.parts:
            if part.material_name and (not mesh_only or part.material_confirmed):
                part.material_snapshot=replace(part_material(project,project.part_by_id(part.part_id)))
        task=WorkflowTask(task_id=work.name,work=work)
        spec={'work':str(work.resolve()),'source':str(Path(source).resolve()),'project':to_dict(snapshot),
              'size':size,'mesh_kwargs':mesh_kwargs,'auto_mesh':True,'mesh_only':mesh_only,
              'export_path':str(export_path) if export_path is not None else None}
        write_json(work/'request.json',spec)
        self._tasks[task.task_id]=task
        self._write(task)
        threading.Thread(target=self._run,args=(task,spec),daemon=True,name=task.task_id).start()
        return task

    def cancel(self,task_id):self._tasks[task_id].cancel_event.set()

    def _write(self,task,*,state=None):
        write_json(task.work/'workflow.json',{'state':state or task.state,'phase':task.phase,'error':task.error,
            'started_at':task.started_at,'finished_at':task.finished_at,'phase_history':task.phase_history,
            'resources':task.resources,'case_dir':str(task.case_dir)})

    def _phase(self,task,phase):
        if task.phase!=phase:
            task.phase=phase
            task.phase_history.append({'phase':phase,'elapsed':time.monotonic()-task.started_at})
        self._write(task)

    def _check_cancel(self,task):
        if task.cancel_event.is_set():raise _Cancelled()

    def _worker(self,task,spec,operation):
        from .scale_guard import available_memory_gb
        self._check_cancel(task)
        request=task.work/(operation+'.json')
        write_json(request,{**spec,'operation':operation})
        available=available_memory_gb()
        limit=int(min(8.,available*.55)*1024**3) if available is not None else None
        group=None
        with (task.work/(operation+'.stdout.log')).open('xb') as out,(task.work/(operation+'.stderr.log')).open('xb') as err:
            try:
                group=OwnedProcessGroup([sys.executable,'-X','utf8','-B','-m','rm_sim.solver.workflow_job',str(request.resolve())],
                    cwd=Path(__file__).resolve().parents[2],stdout=out,stderr=err,memory_limit_bytes=limit)
                while group.process.poll() is None:
                    self._check_cancel(task)
                    task.resources[operation]=group.accounting()
                    peak=task.resources[operation].get('peak_job_memory_bytes')
                    remaining=available_memory_gb()
                    if ((limit is not None and peak is not None and peak >= .98*limit)
                            or (remaining is not None and remaining<2)):
                        raise RuntimeError('后台阶段内存达到预算，已停止本次任务；输入和日志保留')
                    phase=task.work/'phase.json'
                    if phase.exists():
                        try:
                            state=json.loads(phase.read_text(encoding='utf-8'))
                            if state.get('operation')==operation:self._phase(task,state['phase'])
                        except (OSError,ValueError,KeyError):pass
                    time.sleep(.05)
                task.resources[operation]=group.accounting()
                self._check_cancel(task)
                if group.process.returncode!=0:
                    error=task.work/'error.json'
                    if error.exists():
                        detail=json.loads(error.read_text(encoding='utf-8'))['error']
                        raise RuntimeError(detail)
                    stderr_log=task.work/(operation+'.stderr.log')
                    tail=_tail(stderr_log)
                    kind=classify_worker_crash(tail)
                    if kind==_MESH_CRASH_KIND:
                        detail=('网格高阶优化器崩溃（gmsh netgen: Failed to reach critical '
                                f'value in pass 1; 退出码 {group.process.returncode}）')
                    else:
                        detail=f'后台进程退出码 {group.process.returncode}'
                        if tail.strip():
                            detail+='；stderr 末尾：'+tail.strip().splitlines()[-1][:200]
                    raise WorkerCrashed(kind,detail,stderr_log)
            finally:
                if group is not None:
                    try:
                        if group.process.poll() is None:group.terminate()
                        group.process.wait(timeout=10)
                    finally:group.close()

    #: 准备阶段最多尝试次数（含首次）。netgen 高阶优化崩溃是数值发散，
    #: 换更保守的加密配方重试通常能绕开；其余错误不重试。
    PREPARE_ATTEMPTS = 3

    def _archive_attempt_files(self, task, operation, attempt):
        """Preserve failed preparation evidence and free exclusive snapshot paths."""
        archive=task.work/f'{operation}_attempt_{attempt:02d}'
        archive.mkdir(exist_ok=False)
        for name in (operation+'.json',operation+'.stdout.log',operation+'.stderr.log',
                     'error.json','phase.json','phase.events.jsonl','geometry.step','mesh.inp',
                     'mesh.quality.json','deck.inp','prepared.npz','prepared.json'):
            for source in (task.work/name,task.work/(name+'.tmp')):
                if source.is_file():source.replace(archive/source.name)
        return archive

    def _perturb_mesh_recipe(self, spec, attempt):
        """崩溃后换配方：曲率加密降一档、最小尺寸放大（网格尺寸本身不变）。"""
        from .assembly_workflow import (AUTO_MESH_CURVATURE, AUTO_MESH_MIN_SIZE_CAP,
                                        AUTO_MESH_MIN_SIZE_RATIO)
        current = dict(spec.get('mesh_kwargs') or {})
        size = spec.get('size') or (spec.get('project') or {}).get('study', {}).get('mesh_size_mm') or 2.0
        curvature = float(current.get('curvature') or AUTO_MESH_CURVATURE)
        min_size = float(current.get('min_size_mm')
                         or min(AUTO_MESH_MIN_SIZE_CAP, float(size)*AUTO_MESH_MIN_SIZE_RATIO))
        updated = dict(spec)
        updated['mesh_kwargs'] = {**current,
            'curvature': max(2.0, curvature*(0.85**attempt)),
            'min_size_mm': min_size*(1.2**attempt),
            'high_order_optimize': True}
        return updated

    def _prepare_with_retry(self, task, spec):
        attempt = 0
        while True:
            attempt += 1
            try:
                self._worker(task, spec, 'prepare')
                return spec
            except WorkerCrashed as crash:
                if (crash.kind != _MESH_CRASH_KIND or task.cancel_event.is_set()
                        or attempt >= self.PREPARE_ATTEMPTS):
                    raise
                updated = self._perturb_mesh_recipe(spec, attempt)
                archive=self._archive_attempt_files(task,'prepare',attempt)
                with (task.work/'prepare_attempts.jsonl').open('a', encoding='utf-8') as stream:
                    stream.write(json.dumps({'attempt': attempt, 'kind': crash.kind,
                                             'error': crash.detail, 'log': str(archive/crash.log_path.name),
                                             'evidence_dir':str(archive),
                                             'crashed_mesh_kwargs': spec.get('mesh_kwargs'),
                                             'next_mesh_kwargs': updated['mesh_kwargs']},
                                            ensure_ascii=False)+'\n')
                spec = updated
                self._phase(task, f'prepare_retry_{attempt}')

    def _run(self,task,spec):
        scheduler=None
        outcome='failed'
        try:
            task.state='running'
            self._phase(task,'snapshotting')
            spec=self._prepare_with_retry(task,spec)
            if spec['mesh_only']:
                self._phase(task,'loading_mesh')
                project,prepared=load_prepared(task.work,parse_mesh=False)
                if spec.get('export_path'):
                    import shutil
                    target=Path(spec['export_path'])
                    temporary=target.with_name(target.name+'.'+task.task_id+'.tmp')
                    shutil.copyfile(task.work/'mesh.inp',temporary)
                    self._check_cancel(task)
                    temporary.replace(target)
                    project.study.mesh_file=str(target)
                self._check_cancel(task)
                task.result={'project':project,'prepared':prepared,'mesh_only':True}
            else:
                self._phase(task,'solving')
                scheduler=SolveScheduler(self.ccx_path,max_workers=1,defer_result_parse=True)
                task.solver_task=scheduler.submit_file('guijob',task.work/'deck.inp',Path(spec['project']['project_dir'])/'cases')
                import shutil
                shutil.copyfile(task.work/'geometry.step',task.solver_task.case_dir/'geometry.step')
                while task.solver_task.state in ('pending','running'):
                    if task.cancel_event.is_set():scheduler.cancel(task.solver_task.task_id)
                    self._write(task)
                    time.sleep(.05)
                self._check_cancel(task)
                solved=task.solver_task
                if solved.state!=STATE_SOLVED:raise RuntimeError(solved.error or solved.summary())
                task.resources['solve']=solved.resource_usage
                self._phase(task,'reading_results')
                self._worker(task,{**spec,'solver':{'case_dir':str(solved.case_dir),'task_id':solved.task_id,
                    'job_name':solved.job_name,'job_finished':solved.job_finished,'exit_code':solved.exit_code,
                    'exit_code_note':solved.exit_code_note,'resource_usage':solved.resource_usage}},'collect')
                self._phase(task,'loading_display')
                from .scale_guard import available_memory_gb
                package_bytes=sum((task.work/name).stat().st_size for name in ('prepared.npz','display.npz'))
                display_budget=4*package_bytes+128*1024**2
                available=available_memory_gb()
                if available is not None and display_budget>.75*available*1024**3:
                    raise RuntimeError('结果已保存，但当前内存不足以安全载入界面；请查看工况目录中的报告')
                from ..core.store import from_dict
                from .display_store import load_display
                complete=json.loads((task.work/'completed.json').read_text(encoding='utf-8'))
                project=from_dict(complete['project'])
                data=load_display(task.work,lambda:self._check_cancel(task))
                _,prepared=load_prepared(task.work,parse_mesh=False)
                self._check_cancel(task)
                task.result={'project':project,'prepared':prepared,'display':data,'surfaces':data['surfaces'],
                             'summary':complete['summary'],'mesh_only':False}
                solved.state='completed';solved.parse_ok=True
                scheduler._write_status(solved)
            outcome='completed'
            self._phase(task,'completed')
        except _Cancelled:
            outcome='cancelled';task.error='用户取消，本次后台任务已停止；输入和日志保留'
        except Exception as exc:
            outcome='failed';task.error=str(exc)
            import traceback
            (task.work/'workflow_error.log').write_text(traceback.format_exc(),encoding='utf-8')
        finally:
            try:
                if scheduler is not None and scheduler.has_active():
                    scheduler.cancel(task.solver_task.task_id)
                    scheduler.wait_task(task.solver_task.task_id,timeout=15)
                task.finished_at=time.monotonic()
                self._write(task,state=outcome)
                if task.solver_task is not None:
                    import shutil
                    shutil.copyfile(task.work/'workflow.json',task.case_dir/(task.solver_task.job_name+'.workflow.json'))
                    events=task.work/'phase.events.jsonl'
                    if events.exists():
                        shutil.copyfile(events,task.case_dir/(task.solver_task.job_name+'.phase.events.jsonl'))
            except Exception as exc:
                outcome='failed';task.error=f'任务记录未完成：{exc}'
                try:self._write(task,state=outcome)
                except OSError:pass
            finally:
                if outcome!='completed':task.result=None
                # Publish the terminal state only after the final records exist.
                task.state=outcome
