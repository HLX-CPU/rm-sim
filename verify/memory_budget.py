# -*- coding: utf-8 -*-
"""五门静力回归的内存预算分级接入件（模块46）。

- scale_tier()：RM_SIM_SCALE 环境变量决定档位——'large' = 大网格档（--slow），
  其余 = 默认档（跑得过回归机的小算例档）。
- make_scheduler()：按档位经 tasks.resolve_memory_limit_bytes 解析预算
  （小算例固定足额、大网格上探、受可用内存 80% 封顶），并返回带峰值内存
  记录的调度器——每个任务终态时向 stdout 打印一行
  `[memory] job=… state=… stop_reason=… peak=… limit=…`，
  供 verify_memory_budget_guard.py 汇总「退出码+峰值内存」证据。

预算语义与实测锚见 app/rm_sim/solver/tasks.py::resolve_memory_limit_bytes 与
app/docs/module_46_memory_budget_tiers.md。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "app") not in sys.path:
    sys.path.insert(0, str(ROOT / "app"))


def scale_tier() -> str:
    """档位：RM_SIM_SCALE=large → 'large'（大网格档，走 --slow）；否则 'small'。"""
    return "large" if os.environ.get("RM_SIM_SCALE", "").strip().lower() == "large" else "small"


def _fmt(bytes_value) -> str:
    if bytes_value is None:
        return "unlimited"
    return f"{float(bytes_value) / 1024 ** 3:.2f}GiB"


class _RecordingScheduler:
    """SolveScheduler 薄包装：wait_task 终态时打印一行峰值内存记录。"""

    def __init__(self, scheduler, limit_bytes):
        self._scheduler = scheduler
        self.memory_limit_bytes = limit_bytes
        self.records: list[dict] = []

    def __getattr__(self, name):
        return getattr(self._scheduler, name)

    def wait_task(self, task_id, timeout: float = 600.0, poll: float = 0.1):
        task = self._scheduler.wait_task(task_id, timeout=timeout, poll=poll)
        usage = task.resource_usage or {}
        peak = usage.get("peak_job_memory_bytes")
        record = {"job": task.job_name, "state": task.state,
                  "stop_reason": task.stop_reason, "peak_bytes": peak,
                  "limit_bytes": self.memory_limit_bytes,
                  "peak_kernel_limit_bytes": usage.get("job_memory_limit_bytes")}
        self.records.append(record)
        print(f"[memory] job={task.job_name} state={task.state} "
              f"stop_reason={task.stop_reason} peak={_fmt(peak)} "
              f"limit={_fmt(self.memory_limit_bytes)}", flush=True)
        return task


def make_scheduler(ccx_path, max_workers: int = 1, tier: str | None = None, **kwargs):
    """按档位解析内存预算并构造带峰值记录的 SolveScheduler。

    并发降档（2026-11-04）：解析出的每任务预算低于启动尖峰安全线
    （tasks.SPIKE_SAFE_MIN_GIB，ccx 启动期瞬时提交 4.1~4.9GB，见
    app/docs/module_46_memory_budget_tiers.md 复标定节）而并发 >1 时，
    减少并发保住预算，而不是让预算缩水到尖峰以下（那必然被守卫截停）。
    解析返回 None（可用内存不可测，不设限）时不降档。
    """
    from rm_sim.solver.tasks import (
        SPIKE_SAFE_MIN_GIB,
        SolveScheduler,
        resolve_memory_limit_bytes,
    )

    tier = tier or scale_tier()
    workers = int(max_workers)
    degraded_from = None
    floor = int(SPIKE_SAFE_MIN_GIB * 1024 ** 3)
    while workers > 1:
        resolved = resolve_memory_limit_bytes(workers, tier=tier)
        if resolved is None or resolved >= floor:
            break
        degraded_from = degraded_from or workers
        workers -= 1
    limit = resolve_memory_limit_bytes(workers, tier=tier)
    note = (f"（由 {degraded_from} 降档：每任务预算须 ≥ 尖峰安全线 "
            f"{SPIKE_SAFE_MIN_GIB:.1f}GiB）") if degraded_from else ""
    print(f"[memory] 档位={tier} 并发={workers}{note} 预算={_fmt(limit)}/任务", flush=True)
    scheduler = SolveScheduler(ccx_path, max_workers=workers,
                               memory_limit_bytes=limit, **kwargs)
    return _RecordingScheduler(scheduler, limit)
