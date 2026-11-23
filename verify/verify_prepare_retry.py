"""网格高阶优化器崩溃：分类 + 进程级重试的确定性验证（不启动 gmsh/CCX）。"""
from __future__ import annotations

import json
import math
import shutil
import sys
import tempfile
from pathlib import Path

from assembly_check_utils import ROOT, evidence_dir

sys.path.insert(0, str(ROOT / 'app'))

CRASH_TEXT = """Traceback (most recent call last):
terminate called after throwing an instance of 'std::runtime_error'
  what():  Failed to reach critical value in pass 1 for measure(s): ScaledJac
"""


def main() -> None:
    from rm_sim.solver.workflow import (WorkflowScheduler, WorkflowTask, WorkerCrashed,
                                        classify_worker_crash)

    work = evidence_dir('prepare_retry')
    record = {}

    # A. 分类：真实崩溃文案 → mesh_optimizer_crash；普通错误 → None
    assert classify_worker_crash(CRASH_TEXT) == 'mesh_optimizer_crash'
    assert classify_worker_crash('ValueError: 网格内存不足') is None
    assert classify_worker_crash('') is None
    record['classify'] = {'crash': classify_worker_crash(CRASH_TEXT),
                          'ordinary': classify_worker_crash('ValueError: x')}

    scheduler = WorkflowScheduler(Path('ccx-not-used.exe'))
    task_dir = work/'task1';task_dir.mkdir()
    task = WorkflowTask(task_id='t1', work=task_dir)
    spec = {'work': str(task_dir), 'size': 4.0,
            'mesh_kwargs': {'curvature': 12.0, 'min_size_mm': 0.4},
            'project': {'study': {'mesh_size_mm': 4.0}}}
    base = dict(spec['mesh_kwargs'])

    # B. 崩溃一次 → 自动重试，配方变保守，记录 attempts
    calls = []

    def flaky_worker(task, spec, operation):
        calls.append(dict(spec['mesh_kwargs']))
        if len(calls) == 1:
            raise WorkerCrashed('mesh_optimizer_crash', '模拟 netgen 崩溃', task.work / 'prepare.stderr.log')
        return None

    scheduler._worker = flaky_worker
    used = scheduler._prepare_with_retry(task, spec)
    assert len(calls) == 2, calls
    assert used['mesh_kwargs']['curvature'] < base['curvature'], used
    assert used['mesh_kwargs']['min_size_mm'] > base['min_size_mm'], used
    attempts = [json.loads(line) for line in
                (task_dir / 'prepare_attempts.jsonl').read_text(encoding='utf-8').splitlines() if line.strip()]
    assert len(attempts) == 1 and attempts[0]['kind'] == 'mesh_optimizer_crash', attempts
    assert spec['mesh_kwargs'] == base, '不得就地修改调用方的 spec'
    record['retry'] = {'attempts': len(calls), 'recorded': attempts,
                       'next_mesh_kwargs': used['mesh_kwargs'],
                       'phase': task.phase}
    assert task.phase.startswith('prepare_retry_'), task.phase

    # C. 非网格类崩溃 → 不重试，立即失败
    task2 = WorkflowTask(task_id='t2', work=work/'task2');task2.work.mkdir()
    count = []

    def bad_worker(task, spec, operation):
        count.append(1)
        raise WorkerCrashed(None, '后台进程退出码 99', task.work / 'prepare.stderr.log')

    scheduler._worker = bad_worker
    try:
        scheduler._prepare_with_retry(task2, dict(spec))
    except WorkerCrashed as exc:
        record['no_retry'] = {'attempts': len(count), 'error': exc.detail}
    else:
        raise AssertionError('非网格崩溃被重试了')
    assert len(count) == 1, count

    # D. 连续网格崩溃达到上限 → 停止并保留最后错误
    task3 = WorkflowTask(task_id='t3', work=work/'task3');task3.work.mkdir()
    count3 = []

    def always_crash(task, spec, operation):
        count3.append(1)
        raise WorkerCrashed('mesh_optimizer_crash', '模拟 netgen 崩溃', task.work / 'prepare.stderr.log')

    scheduler._worker = always_crash
    try:
        scheduler._prepare_with_retry(task3, dict(spec))
    except WorkerCrashed as exc:
        record['exhausted'] = {'attempts': len(count3), 'error': exc.detail}
    else:
        raise AssertionError('连续崩溃没有停止')
    assert len(count3) == scheduler.PREPARE_ATTEMPTS, (len(count3), scheduler.PREPARE_ATTEMPTS)

    (work / 'verification.json').write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
    print('VERIFY_PREPARE_RETRY: PASS', work, flush=True)


if __name__ == '__main__':
    main()
