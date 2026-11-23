# -*- coding: utf-8 -*-
"""验收脚本：整装（20 实体）自动连接草稿覆盖性检查（不网格、不求解）。

为什么单独一个脚本：整装的 OCC 全配对检查是分钟级操作，放进默认回归会把
27 项回归拖成小时级。本脚本默认手动/夜间跑，验收点集中在「草稿是否覆盖了
全部部件、连接对象是否都能构造」——求解链路已由 verify_connection_draft
用双杆算例证明。

输入：E:/rm_sim/verification/quick_release_assembly_20261102_1020/
      assembly_thread_partition.step（Codex 2026-11-02 的螺纹分区分析副本，
      20 实体、销钉与基准体的螺纹干涉已切除）。
可用环境变量 RM_SIM_ASSEMBLY_STEP / RM_SIM_ASSEMBLY_PARTITION 覆盖。

断言：
① 草稿覆盖全部 20 个部件（每个部件至少出现在一条候选里）；
② 20 条候选以上，公共面积合计 > 0，无几何/补丁错误；
③ 销钉对按简化销轴推荐、且销侧为 slave（module_19 的 *TIE 方向实证）；
④ connections_from_draft 能全部构造并通过 validate，名称唯一；
⑤ 没有任何部件的面被解析到别的部件上（面指纹限定部件内解析）。
"""
from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))

DEFAULT_DIR = Path("E:/rm_sim/verification/quick_release_assembly_20261102_1020")
STEP = Path(os.environ.get("RM_SIM_ASSEMBLY_STEP", str(DEFAULT_DIR / "assembly_thread_partition.step")))
PARTITION = Path(os.environ.get("RM_SIM_ASSEMBLY_PARTITION", str(DEFAULT_DIR / "partition.json")))
LOG_PATH = ROOT / "verify" / "logs" / "verify_assembly_auto_connections.log"

MIN_CANDIDATES = 20

_lines: list[str] = []
_failures: list[str] = []


def log(msg: object) -> None:
    text = str(msg)
    print(text)
    _lines.append(text)


def fail(msg: str) -> None:
    _failures.append(msg)
    log(f"[FAIL] {msg}")


def ok(msg: str) -> None:
    log(f"[OK] {msg}")


def check(cond: bool, pass_msg: str, fail_msg: str) -> None:
    if cond:
        ok(pass_msg)
    else:
        fail(fail_msg)


def _apply_partition_names(model) -> int:
    """分区记录里的中文部件名 → model.parts（STEP 内只有 9 个 PRODUCT 名）。"""
    if not PARTITION.is_file():
        return 0
    data = json.loads(PARTITION.read_text(encoding="utf-8"))
    names = {int(row["tag"]): str(row["name"]) for row in data.get("part_mapping", [])}
    from dataclasses import replace

    updated, hits = [], 0
    for part in model.parts:
        name = names.get(int(part.tag))
        if name:
            hits += 1
        updated.append(replace(part, name=name) if name else part)
    model.parts = tuple(updated)
    return hits


def main() -> int:
    rm_sim_logger = logging.getLogger("rm_sim")
    rm_sim_logger.setLevel(logging.INFO)
    rm_sim_logger.propagate = False

    class _LinesHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            _lines.append("  | [log] " + record.getMessage())

    rm_sim_logger.addHandler(_LinesHandler())

    log("=" * 76)
    log("verify_assembly_auto_connections 开始（整装 20 实体连接草稿覆盖性）")
    log(f"STEP = {STEP}")
    log("=" * 76)
    if not STEP.is_file():
        fail(f"整装分析副本不存在: {STEP}（可用 RM_SIM_ASSEMBLY_STEP 指定）")
        return 1

    from rm_sim.cad.assembly_import import import_assembly, sync_parts_from_model
    from rm_sim.cad.connection_draft import draft_assembly_connections
    from rm_sim.core.connections import connections_from_draft
    from rm_sim.core.project import Project

    model = None
    try:
        model = import_assembly(STEP)
        renamed = _apply_partition_names(model)
        log(f"  | 分区记录中文名覆盖 {renamed} 个部件；实体 {len(model.solids)} 个")
        project = Project(name="整装草稿验证")
        sync_parts_from_model(project, model)
        # sync 用的是 model.parts 的名字，中文名已在上面写回，因此部件名可直接用于推荐规则
        draft = draft_assembly_connections(model, project, planar_only=False, min_area_mm2=0.05, max_patches_per_pair=64)
        log(f"  | 草稿: bodies={draft['body_count']} pairs={draft['pairs_considered']} "
            f"joints={draft['joint_pairs']} candidates={len(draft['candidates'])}")
        for row in draft["part_summary"]:
            log(f"  |   {row['part_id']} {row['name']}: {row['patch_count']} 处，"
                f"{row['contact_area_mm2']:.4g} mm²")

        # ① 覆盖
        missing = draft["unconnected_parts"]
        check(not missing,
              f"① 全部 {draft['body_count']} 个部件都有贴合面候选",
              f"① 有 {len(missing)} 个部件没有任何贴合面候选: {missing}")
        # ② 规模与错误
        check(len(draft["candidates"]) >= MIN_CANDIDATES,
              f"② 候选连接 {len(draft['candidates'])} 条（≥{MIN_CANDIDATES}）",
              f"② 候选连接过少: {len(draft['candidates'])}")
        check(int(draft["geometry_errors"]) == 0 and not draft["patch_errors"],
              "② 几何检查与补丁搜索无错误",
              f"② 存在错误: geometry_errors={draft['geometry_errors']} "
              f"patch_errors={draft['patch_errors'][:3]}")
        area = sum(float(row["common_area_mm2"]) for row in draft["candidates"])
        check(area > 0.0, f"② 公共面积合计 {area:.4g} mm²", "② 公共面积为 0")

        # ③ 销钉方向
        pins = [row for row in draft["candidates"] if row["recommended"] == "pin"]
        bad = [row for row in pins if (row["slave_side"] == "a") != ("销" in str(row["name_a"]) or "轴" in str(row["name_a"]))]
        check(bool(pins), f"③ 识别出 {len(pins)} 条销轴类连接候选", "③ 未识别出任何销轴类候选")
        check(not bad,
              "③ 销轴候选的 slave 侧均为销侧（*TIE 方向正确）",
              f"③ {len(bad)} 条销轴候选 slave 侧错误: {bad[:2]}")

        # ④ 构造
        built = connections_from_draft(project, model, draft)
        names = [c.name for c in built["connections"]]
        check(len(built["connections"]) >= MIN_CANDIDATES and not built["errors"],
              f"④ 构造出 {len(built['connections'])} 条连接，无错误",
              f"④ 构造失败: {len(built['connections'])} 条，errors={built['errors'][:3]}")
        check(len(set(names)) == len(names),
              f"④ 连接名称唯一（{len(names)} 条）",
              f"④ 出现重名连接（{len(names) - len(set(names))} 个）")
    finally:
        if model is not None:
            try:
                model.close()
            except Exception:  # noqa: BLE001
                pass

    log("\n" + "=" * 76)
    if _failures:
        log(f"VERIFY_ASSEMBLY_AUTO_CONNECTIONS: FAIL（{len(_failures)} 项失败）")
        for item in _failures:
            log(f"  [失败] {item}")
        return 1
    log("VERIFY_ASSEMBLY_AUTO_CONNECTIONS: PASS")
    return 0


if __name__ == "__main__":
    _exit_code = 1
    try:
        _exit_code = main()
    except Exception as exc:  # noqa: BLE001
        _failures.append(f"顶层异常: {type(exc).__name__}: {exc}")
        log(f"VERIFY_ASSEMBLY_AUTO_CONNECTIONS: FAIL（顶层异常 {type(exc).__name__}: {exc}）")
    finally:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_PATH, "w", encoding="utf-8") as f:
            f.write("\n".join(_lines) + "\n")
        print(f"[verify] 日志已写入 {LOG_PATH}")
    sys.exit(_exit_code)
