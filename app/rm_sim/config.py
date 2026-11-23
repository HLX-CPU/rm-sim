# -*- coding: utf-8 -*-
"""配置读取（GUI 无关层）：默认仓库根 config.json，可用 RM_SIM_CONFIG 覆盖。

开发期原始工作区的 E:/rm_sim/config.json 仍作为兜底路径保留，
便于旧脚本与旧环境无缝迁移。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = REPO_ROOT / "config.json"
LEGACY_CONFIG_PATH = Path("E:/rm_sim/config.json")
CONFIG_ENV_VAR = "RM_SIM_CONFIG"


class ConfigError(RuntimeError):
    """config.json 缺失 / 不可读 / 结构不符。"""


def config_path() -> Path:
    """解析配置路径：RM_SIM_CONFIG > 仓库根 config.json > 旧工作区 config.json。"""
    env = os.environ.get(CONFIG_ENV_VAR, "").strip()
    if env:
        return Path(env)
    if DEFAULT_CONFIG_PATH.is_file():
        return DEFAULT_CONFIG_PATH
    if LEGACY_CONFIG_PATH.is_file():
        return LEGACY_CONFIG_PATH
    return DEFAULT_CONFIG_PATH


def load_config(path: str | Path | None = None) -> dict:
    """读取 config.json；失败抛 ConfigError（不静默吞错）。"""
    p = Path(path) if path is not None else config_path()
    if not p.is_file():
        raise ConfigError(
            f"配置文件不存在: {p}（可复制 config.example.json 为 config.json，"
            f"或用环境变量 {CONFIG_ENV_VAR} 指定路径）"
        )
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as exc:
        raise ConfigError(f"配置文件读取失败: {p}: {type(exc).__name__}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"配置文件顶层必须是 JSON 对象: {p}")
    return data


def get_ccx_path(cfg: dict) -> str:
    """取求解器路径字段；缺失/类型不符抛 ConfigError。"""
    value = cfg.get("ccxPath")
    if not isinstance(value, str) or not value.strip():
        raise ConfigError("config.json 缺少 ccxPath 字符串字段")
    return value
