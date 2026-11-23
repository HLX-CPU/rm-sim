# -*- coding: utf-8 -*-
"""连接复核决策闭环——批处理路径（GUI 无关层，solver）。

决策单元 = 逐连接（39 补丁 / 22 部件对）：requires_review 是布尔汇总、
untied_nodes/reported_missed_nodes 是节点计数——节点数只作关联证据字段，
不据此自动决策（模块47）。两条入口：

- build_review_records(project, diagnostics)：工程自身最近一次求解的逐连接
  复核记录（needs_review / 决策 / 关联节点数），供 GUI 复核对话框与批处理；
- build_review_records_from_case(case_dir, job, ...)：从整装检查证据目录
  重建逐连接记录——.nam/.log 诊断分类（含 SPC 自由度已固定编号单列）、
  INP「** 绑定带」逐连接证据（从面总节点/可绑节点/贴合区外最近距离）、
  warning_attribution.json（逐连接未绑定归属）与 independent_check.json
  （合计口径）。合计口径交叉核对：逐连接 reported_missed_nodes 合计 ==
  检查记录的 untied_nodes（不一致→cross_check.ok=False，如实呈现，不凑数）。

apply_review_decisions(project, decisions)：批处理写回（枚举校验、原位替换/
删除，见 core.connections.apply_connection_decision）。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from ..core.connections import (REVIEW_DECISIONS, undecided_connections,
                                apply_connection_decision)

_BAND_RE = re.compile(
    r"\*\* 绑定带「(?P<name>[^」]+)」(?P<band>[0-9.eE+-]+) mm："
    r"从面 A (?P<slave_total>\d+) 节点里 (?P<tieable>\d+) 个"
    r"投影进主面并参与绑定（最大间距 (?P<gap_inside>[0-9.eE+-]+) mm，"
    r"位置容差 (?P<tolerance>[0-9.eE+-]+) mm）"
    r"(?:，其余 (?P<outside>\d+) 个在贴合区外、不参与绑定"
    r"（到主面最近 (?P<gap_lo>[0-9.eE+-]+)—(?P<gap_hi>[0-9.eE+-]+) mm，"
    r"因此不会再有未绑定警告，但也不能算全贴合）)?；"
    r"主面 B (?P<facets>\d+) 面片")


def project_review_records(project, diagnostics: dict | None = None,
                           inp_text: str | None = None) -> list[dict]:
    """工程自身逐连接复核记录（GUI 复核对话框与批处理共用）。

    inp_text 给定时（最近求解的 INP），按连接序号合并「** 绑定带」证据
    （从面总节点/可绑节点/贴合区外最近距离）；无绑定带注释（band=0 或
    共节点模式）→ bond_band=None 如实呈现。
    """
    coverage = {}
    for index, item in enumerate(diagnostics.get('connections') or [], start=1):
        coverage[index] = item
    bands = parse_bond_band_comments(inp_text) if inp_text else []
    records = []
    for index, conn in enumerate(project.connections, start=1):
        item = coverage.get(index, {})
        records.append({
            "index": index,
            "name": conn.name,
            "type": type(conn).__name__,
            "part_pair": [conn.side_a.part_id, conn.side_b.part_id],
            "needs_review": bool(getattr(conn, "needs_review", False)),
            "decision": getattr(conn, "review_decision", "pending"),
            "decided_at": getattr(conn, "review_decided_at", ""),
            "reported_missed_nodes": item.get("reported_missed_nodes"),
            "slave_surface_nodes": item.get("slave_surface_nodes"),
            "dof_inactive_on_surface": item.get("dof_inactive_on_surface"),
            "bond_band": bands[index - 1] if index - 1 < len(bands) else None,
            "review_evidence": dict(getattr(conn, "review_evidence", {}) or {}),
        })
    return records


def build_review_records(project, diagnostics: dict | None = None) -> dict:
    """工程复核包：逐连接记录 + 未决策清单（求解入口拦截的同一口径）。"""
    records = project_review_records(project, diagnostics)
    undecided = undecided_connections(project)
    return {"records": records,
            "decision_enum": list(REVIEW_DECISIONS),
            "undecided": [r["index"] for r in records if r["decision"] == "pending"
                          and r["needs_review"]],
            "undecided_names": [c.name for c in undecided]}


def parse_bond_band_comments(inp_text: str) -> list[dict]:
    """INP「** 绑定带」注释按出现顺序解析（顺序 = 连接序号 C1..Cn）。"""
    bands = []
    for match in _BAND_RE.finditer(inp_text):
        bands.append({
            "name": match.group("name"),
            "band_mm": float(match.group("band")),
            "slave_total": int(match.group("slave_total")),
            "tieable": int(match.group("tieable")),
            "gap_inside_max_mm": float(match.group("gap_inside")),
            "position_tolerance_mm": float(match.group("tolerance")),
            "outside": int(match.group("outside")) if match.group("outside") else 0,
            "gap_outside_range_mm": ([float(match.group("gap_lo")), float(match.group("gap_hi"))]
                                     if match.group("gap_lo") else None),
            "master_facets": int(match.group("facets")),
        })
    return bands


def build_review_records_from_case(case_dir, job: str = "assembly_full", *,
                                   part_names: dict | None = None,
                                   existing_decisions: dict | None = None) -> dict:
    """从整装检查证据目录重建逐连接复核记录（决策枚举默认 pending）。

    证据来源（全部只读）：
    - {job}.inp「** 绑定带」注释（逐连接：从面总节点/可绑节点/贴合区外最近距离）；
    - warning_attribution.json（逐连接未绑定节点归属与从面节点数，可能缺席）；
    - independent_check.json（合计口径：reported/untied/SPC 分类）；
    - {job}.log + {job}_WarnNodeMissTiedContact.nam（诊断分类重读，SPC 编号单列）。
    existing_decisions: {连接名或序号: decision}——已有决策回填，缺省 pending。
    """
    from .diagnostics import _read_node_set, tie_warning_kinds

    case_dir = Path(case_dir)
    inp = case_dir / f"{job}.inp"
    log = case_dir / f"{job}.log"
    nam = next(iter(case_dir.glob(f"{job}_WarnNodeMissTiedContact*.nam")), None)
    inp_text = inp.read_text(encoding="utf-8", errors="replace")
    bands = parse_bond_band_comments(inp_text)

    missed = _read_node_set(nam) if nam is not None else set()
    kinds = tie_warning_kinds(log) if log.is_file() else {'no_face': set(), 'too_far': set(),
                                                         'dof_inactive': set()}
    untied = missed & (kinds['no_face'] | kinds['too_far'])
    dof_inactive = sorted(missed - untied)

    attribution = {}
    attribution_path = case_dir.parent.parent / "warning_attribution.json"
    if not attribution_path.is_file():
        attribution_path = case_dir / "warning_attribution.json"
    if attribution_path.is_file():
        data = json.loads(attribution_path.read_text(encoding="utf-8"))
        for row in data.get("connections") or []:
            attribution[int(row["connection"])] = row
    independent = {}
    independent_path = case_dir.parent.parent / "independent_check.json"
    if not independent_path.is_file():
        independent_path = case_dir / "independent_check.json"
    if independent_path.is_file():
        independent = json.loads(independent_path.read_text(encoding="utf-8"))

    existing_decisions = existing_decisions or {}
    records = []
    for index, band in enumerate(bands, start=1):
        row = attribution.get(index, {})
        key = existing_decisions.get(band["name"], existing_decisions.get(str(index), "pending"))
        records.append({
            "index": index,
            "name": band["name"],
            "type": "BondedConnection",
            "part_pair": _part_pair_of(band["name"], part_names),
            "needs_review": bool(row.get("untied_nodes", 0) > 0),
            "decision": key if key in REVIEW_DECISIONS else "pending",
            "decided_at": "",
            "reported_missed_nodes": int(row.get("untied_nodes", 0)),
            "slave_surface_nodes": (int(row["slave_nodes"]) if row.get("slave_nodes") is not None
                                    else band["slave_total"]),
            "dof_inactive_on_surface": None,
            "bond_band": band,
        })
    total_reported = sum(r["reported_missed_nodes"] for r in records)
    reference_untied = independent.get("untied_nodes")
    cross_check = {
        "per_connection_sum": total_reported,
        "reference_untied_nodes": reference_untied,
        "ok": ((reference_untied == total_reported) if isinstance(reference_untied, int)
               else "unknown"),
        "note": "逐连接合计口径 == 检查记录 untied_nodes；同一节点可能属于多条连接时"
                "按归属表逐连接计数（attribution 口径），不与 SPC 编号混算。"
                "参照记录读不到时 ok 如实记 unknown，不凑数。",
    }
    return {"case_dir": str(case_dir), "job": job,
            "decision_enum": list(REVIEW_DECISIONS),
            "records": records,
            "part_pairs": len({tuple(sorted(r["part_pair"])) for r in records if all(r["part_pair"])}),
            "totals": {"reported_missed_nodes": len(missed),
                       "untied_nodes": len(untied),
                       "dof_inactive_nodes": len(dof_inactive),
                       "dof_inactive_node_numbers": dof_inactive,
                       "untied_node_numbers": sorted(untied)[:500]},
            "warning_kinds": {"no_face": len(kinds['no_face'] & missed),
                              "too_far": len(kinds['too_far'] & missed),
                              "dof_inactive": len(kinds['dof_inactive'] & missed)},
            "cross_check": cross_check,
            "sources": {"inp": str(inp), "log": str(log), "nam": str(nam) if nam else None,
                        "warning_attribution": str(attribution_path) if attribution else None,
                        "independent_check": str(independent_path) if independent else None}}


def _part_pair_of(band_name: str, part_names: dict | None) -> list:
    """绑定带注释名「A ↔ B #k」→ 部件对（draft part_names 反查 part_id，查不到给原名）。"""
    if "↔" not in band_name:
        return [None, None]
    left, right = band_name.split("↔", 1)
    left = left.strip()
    right = right.strip()
    if "#" in right:
        right, _ = right.rsplit("#", 1)
        right = right.strip()
    names = {v: k for k, v in (part_names or {}).items()}
    return [names.get(left, left), names.get(right, right)]


def apply_review_decisions(project, decisions: list[dict], *,
                           decided_at: str | None = None) -> dict:
    """批处理写回：decisions = [{index 或 name, decision, evidence?}]。

    按 index（1 基，对应当前 project.connections 顺序）或连接名定位；逐条
    apply_connection_decision（枚举校验/原位替换/删除）。返回 {applied, removed,
    switched, errors}——错误逐条记录不中断整批。
    """
    applied = switched = removed_count = 0
    errors = []
    for row in decisions:
        try:
            decision = row["decision"]
            conn = None
            if row.get("index") is not None and 1 <= int(row["index"]) <= len(project.connections):
                conn = project.connections[int(row["index"]) - 1]
                if row.get("name") and conn.name != row["name"]:
                    raise ValueError(f"序号 {row['index']} 处连接名不符: {conn.name!r} ≠ {row['name']!r}")
            else:
                conn = next(c for c in project.connections if c.name == row["name"])
            before_count = len(project.connections)
            result = apply_connection_decision(project, conn, decision,
                                               evidence=row.get("evidence"),
                                               decided_at=decided_at)
            applied += 1
            if result["removed"] is not None:
                removed_count += 1
            if result["replacement"] is not None:
                switched += 1
            assert len(project.connections) in (before_count, before_count - 1)
        except Exception as exc:  # noqa: BLE001 - 单条失败记录不中断整批
            errors.append({"row": row, "error": f"{type(exc).__name__}: {exc}"})
    return {"applied": applied, "switched": switched, "removed": removed_count, "errors": errors}
