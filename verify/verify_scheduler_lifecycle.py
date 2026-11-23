"""Real Windows processes: live bounded logging, child cancellation, memory cap.

The C# fixture is NOT solver evidence. It tests the OS runner only; separate
static_solve regression verifies real CalculiX results.
"""
import datetime
import json
from pathlib import Path
import subprocess
import time
import tracemalloc
from assembly_check_utils import ROOT, evidence_dir


def until(predicate, timeout=10):
    end = time.monotonic()+timeout
    while time.monotonic()<end:
        if predicate(): return
        time.sleep(.05)
    raise AssertionError('condition timed out')


def build_probe(work):
    binary = Path('D:/rm_sim/tools/verification')/work.name/'scheduler_probe.exe'
    binary.parent.mkdir(parents=True, exist_ok=False)
    compiler = Path('C:/Windows/Microsoft.NET/Framework64/v4.0.30319/csc.exe')
    build = subprocess.run([str(compiler),'/nologo','/target:exe',f'/out:{binary}',
                            str(ROOT/'verify/fixtures/scheduler_probe.cs')],capture_output=True)
    (work/'build.log').write_bytes(build.stdout+build.stderr)
    assert build.returncode == 0, build.stdout+build.stderr
    return binary


def main():
    from rm_sim.solver.tasks import SolveScheduler, MAX_LOG_TAIL_BYTES
    from rm_sim.solver.process_memory import process_memory
    work = evidence_dir('scheduler_lifecycle')
    binary = build_probe(work)
    scheduler = SolveScheduler(binary,max_workers=1,memory_limit_bytes=192*1024**2)
    evidence = {}
    tracemalloc.start()
    task = scheduler.submit('flood','fixture, not a solver deck',work/'cases')
    until(lambda:(task.case_dir/'flood.ready').exists())
    log = task.case_dir/'flood.log'
    until(lambda:log.exists() and log.stat().st_size > 30*1024**2)
    assert task.state == 'running', 'live log must exist BEFORE completion'
    (task.case_dir/'flood.release').write_text('continue',encoding='ascii')
    scheduler.wait_task(task.task_id,timeout=20)
    _, peak = tracemalloc.get_traced_memory();tracemalloc.stop()
    assert peak < 12*1024**2, peak
    assert len(task.log_text) <= MAX_LOG_TAIL_BYTES+10
    assert task.job_finished and not task.parse_ok and task.state == 'failed'
    assert '结果解析失败' in task.error
    evidence['flood'] = {'python_peak_traced_bytes':peak,'log_bytes':log.stat().st_size,
                         'tail_characters':len(task.log_text),'state':task.state,'job_finished':task.job_finished}
    unrelated = subprocess.Popen([str(binary),'unrelated'],cwd=work,
                                 stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    try:
        task = scheduler.submit('spawn','fixture',work/'cases')
        until(lambda:(task.case_dir/'spawn.leaf.pid').exists())
        leaf = int((task.case_dir/'spawn.leaf.pid').read_text())
        assert process_memory(leaf) is not None
        scheduler.cancel(task.task_id)
        scheduler.wait_task(task.task_id,timeout=15)
        until(lambda:process_memory(leaf) is None)
        assert task.state == 'cancelled' and task.process.poll() is not None
        assert unrelated.poll() is None, 'cancellation touched an unrelated process'
        state=json.loads((task.case_dir/'spawn.task.json').read_text(encoding='utf-8'))
        assert state['state']=='cancelled' and state['exit_code'] is not None
        evidence['cancel'] = {'state':task.state,'leaf_pid':leaf,'leaf_stopped':True,'unrelated_alive':True}
        orphan = scheduler.submit('orphan','fixture',work/'cases')
        scheduler.wait_task(orphan.task_id,timeout=15)
        orphan_leaf = int((orphan.case_dir/'orphan.leaf.pid').read_text())
        until(lambda:process_memory(orphan_leaf) is None)
        assert orphan.state=='failed'
        evidence['orphan']={'state':orphan.state,'child_stopped_after_launcher_exit':True}
    finally:
        unrelated.terminate();unrelated.wait(timeout=10)
    task = scheduler.submit('allocate','fixture',work/'cases')
    scheduler.wait_task(task.task_id,timeout=20)
    assert task.state=='failed' and task.resource_usage['peak_job_memory_bytes'] > 64*1024**2
    assert task.resource_usage['peak_job_memory_bytes'] <= 192*1024**2
    evidence['memory_cap'] = task.resource_usage
    started = time.monotonic()
    stalled = scheduler.submit('stall_at_cap', 'fixture', work/'cases')
    scheduler.wait_task(stalled.task_id, timeout=8)
    assert stalled.state == 'failed' and stalled.stop_reason == 'memory_limit', stalled.summary()
    assert not stalled.job_finished and stalled.process.poll() is not None
    evidence['native_allocation_stall_stopped'] = {'seconds':time.monotonic()-started,
        'stop_reason':stalled.stop_reason,'resource_usage':stalled.resource_usage}
    deferred = SolveScheduler(binary, max_workers=1, defer_result_parse=True)
    task = deferred.submit('finished_with_error', 'fixture', work/'cases')
    deferred.wait_task(task.task_id, timeout=15)
    assert task.state == 'failed' and task.job_finished and not task.parse_ok
    assert task.diagnostics['solver_errors']['count'] == 1 and 'ERROR' in task.error
    assert '*ERROR' not in task.log_text, 'The error must be detected outside the bounded log tail'
    evidence['finished_with_error_rejected'] = task.diagnostics
    (work/'verification.json').write_text(json.dumps(evidence,indent=2),encoding='utf-8')
    print('VERIFY_SCHEDULER_LIFECYCLE: PASS',work,flush=True)


if __name__ == '__main__': main()
