# -*- coding: utf-8 -*-
"""工程持久化（GUI 无关层）：JSON 读写 + 原子替换写盘。

序列化格式：dataclass 树带 ``__type__`` 标记，schema 版本 ``rm_sim.project@1``；
后续模块扩展实体时在 _REGISTRY 注册即可，旧文件按缺字段默认值兼容。
"""
from __future__ import annotations

import dataclasses
import datetime as _dt
import json
import os
import typing
from pathlib import Path

from .connections import (
    BondedConnection,
    BoltConnection,
    ConnectionSide,
    ContactFrictionConnection,
    PinConnection,
)
from .face_identity import FaceFingerprint
from .mesh_controls import MeshControl, EdgeFingerprint
from .advanced import ADVANCED_TYPES
from .part_identity import PartFingerprint
from .project import (
    CaseRecord,
    Constraint,
    GravityLoad,
    LoadCase,
    Material,
    MomentLoad,
    Part,
    PressureLoad,
    Project,
    Study,
    TractionLoad,
    migrate_legacy_parts,
)

SCHEMA = "rm_sim.project@1"

_REGISTRY = {
    cls.__name__: cls
    for cls in (
        FaceFingerprint,
        MeshControl,
        EdgeFingerprint,
        PartFingerprint,
        Material,
        Part,
        ConnectionSide,
        BondedConnection,
        ContactFrictionConnection,
        BoltConnection,
        PinConnection,
        CaseRecord,
        Constraint,
        TractionLoad,
        PressureLoad,
        MomentLoad,
        GravityLoad,
        LoadCase,
        Study,
        Project,
    ) + ADVANCED_TYPES
}
_HINTS: dict[type, dict] = {}


class StoreError(RuntimeError):
    """工程文件读写失败。"""


def _hints(cls: type) -> dict:
    if cls not in _HINTS:
        _HINTS[cls] = typing.get_type_hints(cls)
    return _HINTS[cls]


def to_dict(obj):  # noqa: ANN001
    """dataclass / list / tuple / 标量 → 可 JSON 化结构。"""
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {
            "__type__": type(obj).__name__,
            **{f.name: to_dict(getattr(obj, f.name)) for f in dataclasses.fields(obj)},
        }
    if isinstance(obj, (list, tuple)):
        return [to_dict(x) for x in obj]
    if isinstance(obj, dict):
        return {str(k): to_dict(v) for k, v in obj.items()}
    return obj


def _coerce(cls: type, name: str, value):  # noqa: ANN001
    """JSON 容器无类型：按字段注解把 list 还原成 tuple 等定形结构。"""
    if value is None:
        return None
    origin = typing.get_origin(_hints(cls).get(name, object))
    if origin is tuple and isinstance(value, list):
        return tuple(value)
    return value


def from_dict(data):  # noqa: ANN001
    """to_dict 的逆操作；未知 __type__ 抛 StoreError。"""
    if isinstance(data, dict) and "__type__" in data:
        type_name = data["__type__"]
        cls = _REGISTRY.get(type_name)
        if cls is None:
            raise StoreError(f"未知数据类型: {type_name}")
        kwargs = {}
        for f in dataclasses.fields(cls):
            if f.name in data:
                kwargs[f.name] = _coerce(cls, f.name, from_dict(data[f.name]))
        # Old projects relied on CalculiX's default ADJUST=YES. Preserve their
        # recorded behaviour; new connections explicitly default to no movement.
        if cls in (BondedConnection, PinConnection) and 'adjust_nodes' not in data:
            kwargs['adjust_nodes'] = True
        return cls(**kwargs)
    if isinstance(data, list):
        return [from_dict(x) for x in data]
    return data


def save_project(project: Project, path: str | Path) -> Path:
    """保存工程（先写 .rm_tmp 再原子替换，避免半截文件）。"""
    p = Path(path)
    payload = {
        "schema": SCHEMA,
        "app_version": _app_version(),
        "saved_at": _dt.datetime.now().isoformat(timespec="seconds"),
        "project": to_dict(project),
    }
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".rm_tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp, p)
    except Exception:
        try:
            tmp.unlink()  # 仅清理本次创建的临时文件
        except OSError:
            pass
        raise
    return p


def load_project(path: str | Path) -> Project:
    """读取工程；不存在/schema 不符/损坏抛 StoreError。"""
    p = Path(path)
    if not p.is_file():
        raise StoreError(f"工程文件不存在: {p}")
    try:
        with open(p, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except (OSError, ValueError) as exc:
        raise StoreError(f"工程文件读取失败: {p}: {type(exc).__name__}: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("schema") != SCHEMA:
        raise StoreError(f"工程文件 schema 不符（期望 {SCHEMA}）: {p}")
    proj = from_dict(payload.get("project"))
    if not isinstance(proj, Project):
        raise StoreError(f"工程文件缺少 project 节: {p}")
    # 旧版单部件工程（无 parts 键）→ parts=[1 条]；幂等，无 parts/无几何不动作
    migrate_legacy_parts(proj)
    return proj


def _app_version() -> str:
    try:
        from .. import __version__

        return __version__
    except Exception:  # pragma: no cover - 包未初始化等极端情况
        return "0.0.0"
