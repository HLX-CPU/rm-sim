# -*- coding: utf-8 -*-
"""装配配合面 → 连接草稿（只产几何证据，不写工程数据）。

整装 20 实体手工逐对配连接不可行（inspection_dialog 一次只能带入一对），
本模块把已验证的几何检查串成一条链路，输出「哪两个部件的哪两个面贴合、
公共面积多大、建议按哪种连接近似」，由 GUI 让用户一次审核全部采用。

流程（全部复用已实证函数，不另写几何算法）：
1. bbox 预筛：明显分离的实体对直接跳过，不做 OCC 布尔；
2. assembly_checks.check_assembly_geometry → 只保留 zero_distance / interference
   （正间隙 = 不接触，点/线接触也会在此被判为 zero_distance 但无公共面）；
3. assembly_checks.find_contact_faces → 精确公共面补丁（OCC 求交，曲面可选）；
4. 补丁 → 候选连接，按公共面积降序，超出上限的对按面积截断并记录被丢弃面积。

红线：
- 公共面积只说明几何抵接，不说明力学连接；本模块只给候选，不自动写入工程；
- 推荐类型是可解释的默认值（销轴/轴承/一般贴合），用户在对话框可逐条改；
- 不在这里判断接触能否收敛，也不做强度/预紧/螺纹的物理声明。
"""
from __future__ import annotations

import itertools
import logging

from .. import gmsh_runtime
from .assembly_checks import check_assembly_geometry, find_contact_faces

log = logging.getLogger("rm_sim.cad.connection_draft")

# 推荐类型关键词（中文名优先，英文名兜底）。只影响默认值，不阻止用户改。
_SLIDER_WORDS = ("销", "销钉", "轴", "pin", "shaft")
_BEARING_WORDS = ("轴承", "bearing")

# 判定为「贴合对」的几何分类
JOINT_CLASSES = ("zero_distance", "interference")


def _text_of(*names) -> str:
    return " ".join(str(n or "") for n in names).lower()


def _is_slider(*names) -> bool:
    text = _text_of(*names)
    return any(word in text for word in _SLIDER_WORDS)


def _is_bearing(*names) -> bool:
    text = _text_of(*names)
    return any(word in text for word in _BEARING_WORDS)


def recommend_kind(name_a: str, name_b: str, face_area_a: float, face_area_b: float) -> str:
    """候选连接的推荐类型（用户可在对话框覆盖）。

    - 轴承两侧：按绑定简化（滚动体/游隙不建模，结果里必须写明）；
    - 含销/轴：按简化销轴（零间隙绑定，从面=销侧，方向由 choose_side 决定）；
    - 其余贴合：按粘接（界面不分离、不滑移）。

    不给「可分离接触」做默认：整装界面数量大，接触非线性在单步静力里
    几乎不可能收敛；需要接触语义的对由用户在对话框里显式改。
    """
    if _is_bearing(name_a, name_b) and not _is_slider(name_a, name_b):
        return "bonded"
    if _is_slider(name_a, name_b):
        return "pin"
    _ = (face_area_a, face_area_b)
    return "bonded"


def choose_side(name_a: str, name_b: str, face_area_a: float, face_area_b: float) -> tuple[str, str]:
    """返回 (slave 侧标签, master 侧标签)，取值为 "a" 或 "b"。

    ccx 的 *TIE 方向按 module_19 实证固定：从面（slave）= 销面，主面（master）=
    孔壁；反用会得到 gentiedmpc「no tied MPC」的绑定失效。因此含销/轴时
    销侧必须做 slave。其余贴合面取面积较小的一侧作 slave（网格通常更细，
    投影更容易落在 master 内部）。
    """
    if _is_slider(name_a) and not _is_slider(name_b):
        return "a", "b"
    if _is_slider(name_b) and not _is_slider(name_a):
        return "b", "a"
    if float(face_area_a) <= float(face_area_b):
        return "a", "b"
    return "b", "a"


def _bounding_boxes(model) -> dict[int, tuple]:
    """实体 bbox：bbox 不相交的实体对不可能有公共面，先砍掉。"""
    import gmsh

    return {int(s.tag): tuple(gmsh.model.getBoundingBox(3, int(s.tag))) for s in model.solids}


def _prefilter_pairs(model, tolerance_mm: float) -> tuple[list[tuple[int, int]], dict]:
    boxes = _bounding_boxes(model)
    pairs: list[tuple[int, int]] = []
    for a, b in itertools.combinations(sorted(boxes), 2):
        ba, bb = boxes[a], boxes[b]
        if any(ba[i + 3] + tolerance_mm < bb[i] or bb[i + 3] + tolerance_mm < ba[i] for i in range(3)):
            continue
        pairs.append((a, b))
    return pairs, boxes


def _part_map(project, model) -> dict[int, object]:
    """solid tag → Part（用 solid_index 对齐，未知即报错，不静默错配）。"""
    solids = list(model.solids)
    mapping: dict[int, object] = {}
    for part in project.parts:
        index = int(getattr(part, "solid_index", -1))
        if not (0 <= index < len(solids)):
            raise ValueError(
                f"部件 {getattr(part, 'part_id', '?')} 的 solid_index={index} 超出模型实体数 {len(solids)}；"
                "请重新导入装配后再生成连接草稿"
            )
        tag = int(solids[index].tag)
        if tag in mapping:
            raise ValueError(f"多个部件指向同一实体 tag={tag}；工程部件清单与模型不一致")
        mapping[tag] = part
    missing = sorted({int(s.tag) for s in solids} - set(mapping))
    if missing:
        raise ValueError(f"模型有 {len(missing)} 个实体没有对应部件（tag {missing[:8]}）；请先同步装配部件清单")
    return mapping


def draft_assembly_connections(
    model,
    project,
    *,
    planar_only: bool = False,
    min_area_mm2: float = 0.05,
    max_patches_per_pair: int = 64,
    tolerance_mm: float = 1e-5,
) -> dict:
    """生成整装连接草稿（几何证据）。调用方负责把结果交给用户审核后再写工程。"""
    if not project.parts:
        raise ValueError("连接草稿需要工程部件清单（project.parts 为空）")

    part_of_tag = _part_map(project, model)
    labels = {tag: (getattr(part, "name", "") or getattr(part, "part_id", "")) for tag, part in part_of_tag.items()}

    model._ensure_loaded()
    with gmsh_runtime.session():
        pairs, boxes = _prefilter_pairs(model, tolerance_mm)
        log.info("连接草稿：%d 个实体，bbox 预筛后 %d 对进入 OCC 检查", len(boxes), len(pairs))
        geometry = check_assembly_geometry(model, pairs=pairs, tolerance_mm=tolerance_mm)
        joints = [(int(r["a"]), int(r["b"])) for r in geometry["pairs"] if r.get("classification") in JOINT_CLASSES]
        log.info("连接草稿：贴合/干涉对 %d 对，开始搜索公共面补丁", len(joints))
        patches = find_contact_faces(
            model,
            pairs=joints,
            distance_tolerance_mm=tolerance_mm,
            min_area_mm2=min_area_mm2,
            planar_only=planar_only,
        )
        # 跨进程安全：后台作业与 GUI 各自 import 同一 STEP，occ tag 不承诺一致，
        # 因此草稿必须带面指纹（面积+形心+法向），应用时按指纹重解析 tag。
        fingerprints: dict[int, dict] = {}
        for row in patches["candidates"]:
            for raw in (row["face_a"], row["face_b"]):
                tag = int(raw)
                if tag in fingerprints:
                    continue
                face = model.face_by_tag(tag)
                normal = list(face.normal)
                fingerprints[tag] = {
                    "area_mm2": float(face.area_mm2),
                    "centroid": [float(x) for x in face.center],
                    "normal": [float(x) for x in normal],
                }

    grouped: dict[tuple[int, int], list[dict]] = {}
    for row in patches["candidates"]:
        grouped.setdefault((int(row["a"]), int(row["b"])), []).append(row)

    candidates: list[dict] = []
    truncated: list[dict] = []
    seen: set[tuple] = set()
    for (a, b), rows in sorted(grouped.items()):
        rows.sort(key=lambda r: -float(r["common_area_mm2"]))
        keep, drop = rows[:max_patches_per_pair], rows[max_patches_per_pair:]
        if drop:
            truncated.append({
                "part_a": part_of_tag[a].part_id, "part_b": part_of_tag[b].part_id,
                "dropped_patches": len(drop), "dropped_area_mm2": float(sum(r["common_area_mm2"] for r in drop)),
            })
        for row in keep:
            key = (a, b, int(row["face_a"]), int(row["face_b"]))
            if key in seen:
                continue
            seen.add(key)
            name_a, name_b = labels[a], labels[b]
            area_a, area_b = float(row["face_area_a_mm2"]), float(row["face_area_b_mm2"])
            slave, master = choose_side(name_a, name_b, area_a, area_b)
            candidates.append({
                "part_a": part_of_tag[a].part_id,
                "part_b": part_of_tag[b].part_id,
                "solid_a": a,
                "solid_b": b,
                "name_a": name_a,
                "name_b": name_b,
                "face_a": int(row["face_a"]),
                "face_b": int(row["face_b"]),
                "common_area_mm2": float(row["common_area_mm2"]),
                "face_area_a_mm2": area_a,
                "face_area_b_mm2": area_b,
                "geometry_type": row.get("geometry_type", "planar"),
                "normal_a": list(row.get("normal_a") or []),
                "normal_b": list(row.get("normal_b") or []),
                "fingerprint_a": fingerprints.get(int(row["face_a"])),
                "fingerprint_b": fingerprints.get(int(row["face_b"])),
                "recommended": recommend_kind(name_a, name_b, area_a, area_b),
                "slave_side": slave,
                "master_side": master,
            })

    covered = {c["part_a"] for c in candidates} | {c["part_b"] for c in candidates}
    part_summary = []
    for tag in sorted(part_of_tag):
        part = part_of_tag[tag]
        mine = [c for c in candidates if c["part_a"] == part.part_id or c["part_b"] == part.part_id]
        part_summary.append({
            "part_id": part.part_id, "name": labels[tag], "solid_tag": tag,
            "patch_count": len(mine), "contact_area_mm2": float(sum(c["common_area_mm2"] for c in mine)),
        })

    joint_rows = [r for r in geometry["pairs"] if r.get("classification") in JOINT_CLASSES]
    no_patch = []
    for row in joint_rows:
        a, b = int(row["a"]), int(row["b"])
        if (a, b) not in grouped:
            no_patch.append({
                "part_a": part_of_tag[a].part_id, "part_b": part_of_tag[b].part_id,
                "name_a": labels[a], "name_b": labels[b],
                "distance_mm": row.get("distance_mm"), "intersection_mm3": row.get("intersection_mm3"),
                "classification": row.get("classification"),
                "note": "几何零距离/干涉但没有公共面补丁：点或线接触，需人工判断是否建模",
            })

    from ..core.connections import consistent_draft_directions
    return consistent_draft_directions({
        "body_count": len(boxes),
        "pairs_considered": len(pairs),
        "joint_pairs": len(joints),
        "candidates": candidates,
        "part_summary": part_summary,
        "covered_parts": sorted(covered),
        "unconnected_parts": sorted({p.part_id for p in project.parts} - covered),
        "point_or_edge": no_patch,
        "truncated": truncated,
        "geometry_errors": geometry.get("failed_pairs", 0),
        "patch_errors": patches.get("errors", []),
        "scope": "几何公共面证据；不构成力学连接，未做接触收敛/强度/预紧声明。",
    })
