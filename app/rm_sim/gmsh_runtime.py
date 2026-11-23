# -*- coding: utf-8 -*-
"""gmsh 运行时管理（GUI 无关层，cad/mesh 共用）。

gmsh C API 全进程只能 initialize 一次：这里用引用计数管理生命周期——
每个持有 gmsh 模型的对象（如 cad.SolidModel）占一个引用槽；
计数归零即 finalize，下次 acquire 重新初始化。所有方法线程安全（RLock）。
"""
from __future__ import annotations

import atexit
import threading
from contextlib import contextmanager

_lock = threading.RLock()
_inited = False
_users = 0


def _init_once() -> None:
    global _inited
    import gmsh

    gmsh.initialize()
    gmsh.option.setNumber("General.Terminal", 0)  # 不向控制台刷 gmsh 自身日志
    gmsh.option.setNumber("General.Verbosity", 2)  # logger 只留警告/错误（供失败原因）
    _inited = True


def acquire() -> None:
    """进入 gmsh API 使用区（可嵌套）。"""
    global _inited, _users
    with _lock:
        if not _inited:
            _init_once()
        _users += 1


def release() -> None:
    """离开 gmsh API 使用区；计数归零时 finalize（模型随之销毁，由持有方负责重建）。"""
    global _inited, _users
    with _lock:
        _users = max(0, _users - 1)
        if _users == 0 and _inited:
            import gmsh

            gmsh.finalize()
            _inited = False


@contextmanager
def session():
    """with 用法：保证区间内 gmsh 已初始化，退出时按引用计数回收。"""
    acquire()
    try:
        yield
    finally:
        release()


def is_initialized() -> bool:
    with _lock:
        return _inited


@atexit.register
def _cleanup_at_exit() -> None:
    global _inited
    with _lock:
        if _inited:
            try:
                import gmsh

                gmsh.finalize()
            except Exception:
                pass
            _inited = False
