# -*- coding: utf-8 -*-
"""CalculiX 求解任务调度器（GUI 无关层，solver 层）。

调研报告 §7：任务状态 提交/运行/完成/失败/取消；每任务保留 输入(.inp)/
日志({job}.log)/退出状态/原始结果(.dat/.frd/.sta/.cvg/.12d)。

红线①（独立工作目录）：每任务 case_NNN 目录**先建后解**，ccx 以
cwd=case_NNN 启动并按作业名写产物——probes/beamRun.* 全落 probes/ 目录实证
ccx 按 cwd+作业名写文件，并发共享 cwd 会互相覆盖产物。
红线②：本模块不 import PySide6（求解入口无 GUI 可调）。

子进程：**二进制捕获** + decode("utf-8", errors="replace")——probes/probe6
实证 text=True 在 byte 0xa1 处 UnicodeDecodeError 静默丢 stdout。

成败判定：日志无 ERROR、含 "Job finished" **且**结果解析成功。退出码不作判据，两类
证据分开标注（config.json ccxLauncherNote / probes/probe6_log.json）：
官方 ccx '-v' 恒 exit 201；PrePoMax 成败双态 0xFFFFFFFF。
"""
from __future__ import annotations

import itertools
import codecs
import json
import os
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from .results import SolveResults, load_results

STATE_PENDING = "pending"
STATE_RUNNING = "running"
STATE_COMPLETED = "completed"
STATE_FAILED = "failed"
STATE_CANCELLED = "cancelled"
STATE_SOLVED = 'solved_unparsed'
TERMINAL_STATES = (STATE_COMPLETED, STATE_FAILED, STATE_CANCELLED, STATE_SOLVED)

_JOB_FINISHED_MARK = "Job finished"
MAX_LOG_TAIL_BYTES = 1024 * 1024

# ---------------------------------------------------------------- 模块46：求解内存预算分级
# 依据：旧默认预算 = 0.6×当时可用内存/并发（_execute）——系统内存被其他进程挤压时
# （2026-11-02 22:2x 基线）预算缩水，五门静力回归被守卫截停
# （stop_reason=memory_limit / memory_limit_near_exit，contact 门 ccx 0xC0000005）。
# 大网格档实测锚（同一 1mm 粘接输入、仅预算不同）：
#   E:/rm_sim/verification/frozen_deck_budget_20261102_1918/record.json ——
#   公开 6GiB：停滞 60s 主动取消、未完成（峰值 5.84GB）；公开 10GiB：17.48s 完成
#   （峰值 6.35GB）。即大网格档预算过低不是"更安全"，而是必然停滞/崩溃。
SMALL_TIER_FLOOR_GIB = 8.0      # 小算例固定足额预算：原 4.0（模块46 标定「2mm 级实测峰值远低于此」）
                                # 已被 2026-11-04 本机实测证伪——ccx_dynamic.exe 启动期存在一次
                                # 瞬时提交尖峰（任何 deck 均触发，包括 8 节点手造最小 deck；
                                # 6ms 内 commit 4.1~4.7GB，~260ms 后自行释放，求解结果正常），
                                # 同一 deck 尖峰有无随机器状态翻转（11-03 19:33 tie2 峰值
                                # 1152MB 通过 vs 11-04 00:04 同字节 deck 撞 3.6GiB 内核限额
                                # 被守卫截停）。小档预算须盖过尖峰+真实求解（实测合计最高
                                # 4831MB，11-03 22:34 assembly），守卫 trip 点=0.98×0.9×预算。
LARGE_TIER_TARGET_GIB = 10.0    # 大网格档上探目标（实测锚：10GiB 完成 / 6GiB 停滞）
AVAILABLE_SHARE = 0.8           # 预算不超过当前可用内存的 80%（不吃满机器）
OMP_EXTRA_HEADROOM = 0.25       # OMP_NUM_THREADS>2 时每线程 +25% 谨慎余量（保守启发，非实测定律）
# ccx_dynamic.exe 启动期提交尖峰的安全每任务预算下限（GiB）：2026-11-04 本机实测，
# 任何 deck（含 8 节点手造最小算例）启动 6ms 内 commit 4.1~4.9GB、~260ms 自行释放、
# 求解结果正常；预算低于尖峰时内核限额拒绝分配 → 求解器挂起 → 守卫截停。
# 仅用于「并发降档」（每任务预算 < 此值且并发 >1 时减少并发，而非缩水预算），
# 不改变预算公式与守卫判定；显式小限额（守卫注入单测）不受影响。
SPIKE_SAFE_MIN_GIB = 6.0


def resolve_memory_limit_bytes(max_workers, *, tier: str = "small",
                               omp_threads: int | None = None,
                               available_gb: float | None = None) -> int | None:
    """分级求解内存预算 [bytes]；宿主可用内存不可测时返回 None（不设限，如实）。

    - tier='small'（默认档）：小算例**固定足额**预算 SMALL_TIER_FLOOR_GIB/任务，
      不随其他进程挤压系统内存而缩水（基线失败根因）；可用内存不足额时回落到
      可用内存的 AVAILABLE_SHARE 份额（始终 ≤ 可用的 80%，绝不倒退到旧的
      「随挤压缩水」行为）。
    - tier='large'（--slow / RM_SIM_SCALE=large）：上探 LARGE_TIER_TARGET_GIB
      （实测锚：1mm 收敛档 10GiB 完成、6GiB 停滞），同样受 AVAILABLE_SHARE 封顶。
    - OMP_NUM_THREADS>2 时按每线程 +OMP_EXTRA_HEADROOM 放大目标（保守启发值，
      用于 PaStiX/线程 arena 的余量，记录在案、不冒充实测定律）。
    纯函数：不改变 SolveScheduler 既有默认行为（memory_limit_bytes=None 语义不变）。
    """
    workers = max(1, int(max_workers))
    if omp_threads is None:
        try:
            omp = int(os.environ.get("OMP_NUM_THREADS", "2") or "2")
        except ValueError:
            omp = 2
    else:
        omp = int(omp_threads)
    headroom = 1.0 + OMP_EXTRA_HEADROOM * max(0, omp - 2)
    available = available_gb if available_gb is not None else None
    if available is None:
        from .scale_guard import available_memory_gb
        available = available_memory_gb()
    if available is None:
        return None
    share = available * (1024 ** 3) * AVAILABLE_SHARE / workers
    if tier == "large":
        want = LARGE_TIER_TARGET_GIB * (1024 ** 3) * headroom
    else:
        want = SMALL_TIER_FLOOR_GIB * (1024 ** 3) * headroom
    limit = min(want, share)
    return int(max(limit, 1))


class SchedulerError(RuntimeError):
    """调度器配置/使用错误。"""


@dataclass
class SolveTask:
    """一次求解任务（含全部留存物：输入/日志/退出状态/原始结果）。"""

    task_id: int
    job_name: str
    case_dir: Path
    inp_path: Path
    state: str = STATE_PENDING
    exit_code: int | None = None
    exit_code_note: str = ""
    log_text: str = ""
    error: str = ""
    job_finished: bool = False
    parse_ok: bool = False
    results: SolveResults | None = None
    started_at: float | None = None
    finished_at: float | None = None
    cancel_event: threading.Event = field(default_factory=threading.Event, repr=False)
    process: subprocess.Popen | None = field(default=None, repr=False)
    resource_usage: dict = field(default_factory=dict)
    diagnostics: dict = field(default_factory=dict)
    stop_reason: str | None = None

    @property
    def dat_path(self) -> Path:
        return self.case_dir / f"{self.job_name}.dat"

    @property
    def frd_path(self) -> Path:
        return self.case_dir / f"{self.job_name}.frd"

    def summary(self) -> str:
        return (
            f"task#{self.task_id} [{self.job_name}] state={self.state} "
            f"exit={self.exit_code} job_finished={self.job_finished} parse_ok={self.parse_ok} "
            f"error={self.error!r}"
        )


class SolveScheduler:
    """线程池式求解调度器：submit 立即返回，ccx 在工作线程中运行，可取消。"""

    def __init__(self, ccx_path: str | Path, max_workers: int = 2, *, memory_limit_bytes: int | None = None,
                 defer_result_parse: bool = False) -> None:
        self.ccx_path = Path(ccx_path)
        if not self.ccx_path.is_file():
            raise SchedulerError(f"ccx 可执行文件不存在: {self.ccx_path}")
        self.max_workers = int(max_workers)
        # 并发降档（仅默认限额路径）：memory_limit_bytes=None 时每任务预算 =
        # 0.6×可用内存/并发；低于启动尖峰安全线时降并发（保预算），不缩水预算。
        # 显式限额（守卫注入单测 192MiB 等）不降档——那正是守卫语义的测试载体。
        self.workers_degraded_from: int | None = None
        if memory_limit_bytes is None and self.max_workers > 1:
            from .scale_guard import available_memory_gb
            avail = available_memory_gb()
            if avail is not None:
                workers = self.max_workers
                while workers > 1 and (0.6 * avail / workers) < SPIKE_SAFE_MIN_GIB:
                    workers -= 1
                if workers != self.max_workers:
                    self.workers_degraded_from = self.max_workers
                    self.max_workers = workers
        self.memory_limit_bytes = memory_limit_bytes
        self.defer_result_parse = bool(defer_result_parse)
        self._tasks: dict[int, SolveTask] = {}
        self._lock = threading.RLock()
        self._seq = itertools.count(1)
        self._sem = threading.BoundedSemaphore(self.max_workers)

    # ---------------------------------------------------------------- 提交
    def submit(self, job_name: str, inp_text: str, work_root: str | Path) -> SolveTask:
        return self._submit(job_name,work_root,inp_text=inp_text)

    def submit_file(self, job_name: str, inp_file: str | Path, work_root: str | Path) -> SolveTask:
        """Copy an already prepared deck without holding its full text in memory."""
        return self._submit(job_name,work_root,inp_file=Path(inp_file))

    def _submit(self, job_name, work_root, *, inp_text=None, inp_file=None):
        """提交任务：case_NNN 目录先建后解（红线①），.inp 按作业名写入 case 目录。

        work_root 下已存在的 case_NNN（如工程束解包带来的历史 case）会被跳过——
        重算永远生成新 case，旧 case 产物不被覆盖。
        """
        job = str(job_name).strip()
        if not job or any(ch in job for ch in ' "/\\<>|:*?'):
            raise SchedulerError(f"作业名非法（将作为文件名基名）: {job_name!r}")
        work_root = Path(work_root)
        with self._lock:
            while True:
                n = next(self._seq)
                case_dir = work_root / f"case_{n:03d}"
                if not case_dir.exists():
                    break
        case_dir.mkdir(parents=True, exist_ok=False)  # 先建后解；竞态立即暴露
        inp_path = case_dir / f"{job}.inp"
        if inp_file is None:
            inp_path.write_text(inp_text, encoding="utf-8")
        else:
            shutil.copyfile(inp_file,inp_path)
        task = SolveTask(task_id=n, job_name=job, case_dir=case_dir, inp_path=inp_path)
        with self._lock:
            self._tasks[n] = task
        self._write_status(task)
        threading.Thread(target=self._run, args=(task,), daemon=True,
                         name=f"rm-solve-{n}").start()
        return task

    # ---------------------------------------------------------------- 查询
    def get(self, task_id: int) -> SolveTask:
        with self._lock:
            return self._tasks[int(task_id)]

    def all_tasks(self) -> list[SolveTask]:
        with self._lock:
            return list(self._tasks.values())

    def has_active(self) -> bool:
        return any(t.state in (STATE_PENDING, STATE_RUNNING) for t in self.all_tasks())

    def wait_task(self, task_id: int, timeout: float = 600.0, poll: float = 0.1) -> SolveTask:
        """阻塞等待单个任务进入终态（供无 GUI 调用方；GUI 用 200ms 轮询）。"""
        deadline = time.monotonic() + timeout
        task = self.get(task_id)
        while task.state not in TERMINAL_STATES:
            if time.monotonic() > deadline:
                raise TimeoutError(f"任务 {task_id} 超时未完成（{timeout}s）")
            time.sleep(poll)
        return task

    # ---------------------------------------------------------------- 取消
    def cancel(self, task_id: int) -> None:
        """请求取消：运行中→终止进程；排队中→不再启动。终态任务无操作。"""
        task = self.get(task_id)
        task.cancel_event.set()

    # ---------------------------------------------------------------- 运行
    def _set_state(self, task: SolveTask, state: str) -> None:
        with self._lock:
            task.state = state
        self._write_status(task)

    def _run(self, task: SolveTask) -> None:
        with self._sem:
            if task.cancel_event.is_set():
                task.error = "用户取消了排队任务，未启动求解器"
                self._set_state(task, STATE_CANCELLED)
                return
            self._set_state(task, STATE_RUNNING)
            task.started_at = time.monotonic()
            try:
                self._execute(task)
            except Exception as exc:  # noqa: BLE001 - 任何异常都转为 failed 状态
                task.error = f"{type(exc).__name__}: {exc}"
                with (task.case_dir / f"{task.job_name}.log").open('a',encoding='utf-8') as output:
                    output.write('\n[scheduler error] '+task.error+'\n')
                self._set_state(task, STATE_FAILED)
            finally:
                task.finished_at = time.monotonic()
                self._write_status(task)

    def _execute(self, task: SolveTask) -> None:
        from .process_group import OwnedProcessGroup
        from .scale_guard import available_memory_gb
        available = available_memory_gb()
        limit = self.memory_limit_bytes
        if limit is None and available is not None:
            limit = int(.6 * available * 1024**3 / self.max_workers)
        log_path = task.case_dir / f"{task.job_name}.log"
        errors = []
        group = None
        stop_reason = None
        with log_path.open('x',encoding='utf-8',buffering=1) as output:
            write_lock = threading.Lock()
            def drain(pipe, stream):
                decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')
                try:
                    with (task.case_dir / f"{task.job_name}.{stream}.log").open('xb') as raw:
                        first = True
                        for chunk in iter(lambda:pipe.read1(65536),b''):
                            raw.write(chunk)
                            raw.flush()
                            decoded = decoder.decode(chunk)
                            with write_lock:
                                if first and stream == 'stderr':
                                    output.write('\n[stderr]\n')
                                first = False
                                output.write(decoded)
                                output.flush()
                        with write_lock:
                            output.write(decoder.decode(b'',final=True))
                except Exception as exc:
                    errors.append(f'{stream} 日志写入失败: {exc}')
                finally:
                    pipe.close()
            try:
                group = OwnedProcessGroup([str(self.ccx_path),task.job_name],cwd=str(task.case_dir),
                                          memory_limit_bytes=limit)
                proc = task.process = group.process
                task.resource_usage = group.accounting()
                threads = [threading.Thread(target=drain,args=(pipe,stream),daemon=True)
                           for pipe,stream in ((proc.stdout,'stdout'),(proc.stderr,'stderr'))]
                for thread in threads:
                    thread.start()
                last_status = 0.
                while proc.poll() is None:
                    if task.cancel_event.is_set():
                        stop_reason = 'cancelled'
                        group.terminate()
                        break
                    now = time.monotonic()
                    if now-last_status >= .5:
                        task.resource_usage = group.accounting()
                        peak = task.resource_usage.get('peak_job_memory_bytes')
                        # The Job Object enforces 90% of the public budget. Watch
                        # that actual limit: native allocators can stall below the
                        # public budget after the kernel starts denying allocations.
                        effective_limit = task.resource_usage.get('job_memory_limit_bytes') or limit
                        if effective_limit is not None and peak is not None and peak >= .98*effective_limit:
                            stop_reason = 'memory_limit'
                            group.terminate()
                            break
                        remaining = available_memory_gb()
                        if remaining is not None and remaining < 2:
                            stop_reason = 'host_memory_low'
                            group.terminate()
                            break
                        self._write_status(task)
                        last_status = now
                    time.sleep(.05)
                proc.wait(timeout=10)
                task.exit_code = int(proc.returncode)
                task.resource_usage = group.accounting()
                # Close the job before joining readers, so an abandoned child cannot
                # keep the pipes (or an actual solver) alive after the launcher exits.
                group.close()
                for thread in threads:
                    thread.join(timeout=10)
                if any(thread.is_alive() for thread in threads):
                    errors.append('求解器退出后日志管道未关闭')
                if stop_reason:
                    output.write(f'\n[scheduler stop: {stop_reason}]\n')
            finally:
                if group is not None:
                    try:
                        if group.process.poll() is None:
                            group.terminate()
                            group.process.wait(timeout=10)
                    finally:
                        group.close()
        task.log_text = _read_tail(log_path)
        from .diagnostics import collect_tie_diagnostics
        task.diagnostics = collect_tie_diagnostics(task.case_dir,task.job_name)
        task.exit_code_note = (
            "退出码不作判据（两类证据分开标注）：官方 ccx '-v' 恒 exit 201"
            "（config ccxLauncherNote）；PrePoMax 成败双态 0xFFFFFFFF"
            "（probes/probe6_log.json）。判据=日志无 ERROR、含 'Job finished' 且结果解析成功。"
        )
        task.job_finished = _contains_marker(log_path,_JOB_FINISHED_MARK.encode('ascii'))
        kernel_limit = task.resource_usage.get('job_memory_limit_bytes')
        if (stop_reason is None and not task.job_finished and kernel_limit is not None
                and task.resource_usage.get('peak_job_memory_bytes',0) >= .98*kernel_limit):
            stop_reason = 'memory_limit_near_exit'
        task.stop_reason = stop_reason
        if stop_reason == 'cancelled':
            task.error = '用户取消，已停止本次求解进程树；输入与日志已保留'
            self._set_state(task,STATE_CANCELLED)
            return
        if stop_reason in ('memory_limit','host_memory_low'):
            task.error = '求解进程树已达到内存预算（或系统可用内存不足），已停止本次进程树；日志已保留'
            self._set_state(task,STATE_FAILED)
            return
        if stop_reason == 'memory_limit_near_exit':
            task.error = (f'求解器未正常完成，退出码 {task.exit_code}；峰值提交内存接近内核作业限额。'
                          '内存压力可能相关，具体退出原因尚未确认；输入和日志已保留')
            self._set_state(task, STATE_FAILED)
            return
        if errors:
            task.error = '；'.join(errors)
            self._set_state(task,STATE_FAILED)
            return
        if task.diagnostics.get('has_solver_errors') or task.diagnostics.get('solver_errors', {}).get('read_error'):
            evidence = task.diagnostics['solver_errors']
            samples = evidence.get('samples') or []
            detail = samples[0]['message'] if samples else evidence.get('read_error', '')
            task.error = f"求解日志含 {evidence['count']} 条 ERROR 或日志检查失败；结果不接受。{detail}"
            self._set_state(task, STATE_FAILED)
            return
        if not task.job_finished:
            task.error = "日志无 'Job finished'（求解未正常结束）"
            self._set_state(task, STATE_FAILED)
            return
        if self.defer_result_parse:
            self._set_state(task,STATE_SOLVED)
            return
        try:
            task.results = load_results(task.dat_path, task.frd_path)
            task.parse_ok = True
        except Exception as exc:  # noqa: BLE001
            task.error = f"结果解析失败: {type(exc).__name__}: {exc}"
            self._set_state(task, STATE_FAILED)
            return
        self._set_state(task, STATE_COMPLETED)

    def _write_status(self, task: SolveTask) -> None:
        data = dict(state=task.state,job_name=task.job_name,exit_code=task.exit_code,
                    job_finished=task.job_finished,parse_ok=task.parse_ok,error=task.error,
                    pid=task.process.pid if task.process is not None else None,
                    resource_usage=task.resource_usage,started_at=task.started_at,finished_at=task.finished_at,
                    stop_reason=task.stop_reason)
        data['solver_diagnostics'] = task.diagnostics
        target = task.case_dir/f'{task.job_name}.task.json'
        temporary = target.with_suffix('.json.tmp')
        temporary.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
        temporary.replace(target)


def _read_tail(path):
    with Path(path).open('rb') as stream:
        stream.seek(0,2)
        stream.seek(max(0,stream.tell()-MAX_LOG_TAIL_BYTES))
        return stream.read().decode('utf-8',errors='replace')


def _contains_marker(path,marker):
    with Path(path).open('rb') as stream:
        previous = b''
        for chunk in iter(lambda:stream.read(65536),b''):
            combined = previous+chunk
            if marker in combined:
                return True
            previous = combined[-max(1,len(marker)-1):]
    return False
