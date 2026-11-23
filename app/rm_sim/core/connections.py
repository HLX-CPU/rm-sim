# -*- coding: utf-8 -*-
"""连接数据模型（GUI 无关层，core）：ConnectionBase + 四类连接 + 模板。

装配连接 = 两部件的面指纹对（side_a/side_b 各为 ConnectionSide(part_id,
FaceFingerprint)）。四类连接（物理假设/适用范围/不模拟项的权威文档见
app/docs/connections.md，本模块 docstring 只做摘要）：

- BondedConnection 粘接：两侧面绑定一体，无相对滑移/分离，默认保留网格坐标。
- ContactFrictionConnection 可分离接触+摩擦：法向可分离（不传拉力），
  切向 Coulomb 摩擦 μ ∈ [0,1]。
- BoltConnection 简化螺栓：**不建螺栓杆实体**——预紧力 F_p（N，>0）+
  耦合区半径 r（mm，>0，垫片等效耦合区）。
- PinConnection 简化销轴：销部件与板孔壁**零间隙绑定**（忽略挤压/间隙/
  销弯曲柔度，见 IGNORED_EFFECTS）。数据模型为「销侧-板侧」一对引用；
  销贯穿多板时为每块板声明一条 PinConnection（共用销侧引用），
  全部绑定后销成为板间传力路径。

模板 = 参数化默认值（钢-钢 μ=0.3、M8/M10 8.8 级预紧等），instantiate_template
可逐参数覆盖，非法值被 validate 拒绝。弹性连接（elastic）类型位**本期有意
推迟**（_RESERVED_KINDS 预留，见 DevOutcome notes 与 connections.md）。

与模块 14 的 Constraint(kind="bonded") 关系：后者是工况树上的轻量内部连接
语义声明（is_solvable 不接地）；本体系是结构化连接对象（含参数与文档），
后续接触/绑定求解模块以本体系为准。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .face_identity import FaceFingerprint


class ConnectionValidationError(ValueError):
    """连接对象校验失败（消息列出全部问题）。"""


# 预留类型位：本期有意推迟（不做占位实现，仅保留命名空间与文档说明）
_RESERVED_KINDS = ("elastic",)


@dataclass
class ConnectionSide:
    """连接面侧：部件引用 + 该部件上的面指纹（跨会话身份，重开 re-resolve）。"""

    part_id: str
    face: FaceFingerprint


@dataclass
class ConnectionBase:
    """连接基类：name + 两部件面指纹对。

    validate(part_ids=None)：
    - 固有校验（无需工程语境）：名称非空、两侧 part_id 非空、
      **同一部件自身连接拒绝**（side_a.part_id == side_b.part_id）；
    - part_ids 给定时（工程语境）：两侧 part_id 必须存在于工程部件列表
      （不存在的 part_id 拒绝）。
    """

    name: str
    side_a: ConnectionSide
    side_b: ConnectionSide

    KIND: str = "connection"  # 子类覆盖（展示/派发用）

    # ---- 连接复核决策闭环（模块47）----
    # needs_review 由最近一次求解诊断回写（finish_analysis）：该连接的从面
    # 出现过未绑定节点（reported_missed_nodes>0）。review_decision 是人工
    # 复核决策；'pending' = 未决策，存在未决策且 needs_review 的连接时求解
    # 入口拒绝（不静默通过）。三个已决策枚举：保留粘接 / 改可分离接触 / 删除。
    needs_review: bool = False
    review_decision: str = "pending"
    review_decided_at: str = ""
    review_evidence: dict = field(default_factory=dict)

    def validate(self, part_ids=None) -> None:  # noqa: ANN001
        errors: list[str] = []
        if not isinstance(self.name, str) or not self.name.strip():
            errors.append(f"连接名不能为空: {self.name!r}")
        sides = (("side_a", self.side_a), ("side_b", self.side_b))
        for label, side in sides:
            if not isinstance(side, ConnectionSide):
                errors.append(f"{label} 必须是 ConnectionSide，实际 {type(side).__name__}")
                continue
            if not isinstance(side.part_id, str) or not side.part_id.strip():
                errors.append(f"{label}.part_id 不能为空（连接必须引用两个部件）")
            if not isinstance(side.face, FaceFingerprint):
                errors.append(f"{label}.face 必须是 FaceFingerprint（面指纹对）")
        if (
            isinstance(self.side_a, ConnectionSide)
            and isinstance(self.side_b, ConnectionSide)
            and self.side_a.part_id
            and self.side_a.part_id == self.side_b.part_id
        ):
            errors.append(
                f"连接不能引用同一部件自身: side_a={self.side_a.part_id!r} == "
                f"side_b={self.side_b.part_id!r}"
            )
        if part_ids is not None:
            known = set(part_ids)
            for label, side in sides:
                if isinstance(side, ConnectionSide) and side.part_id and side.part_id not in known:
                    errors.append(
                        f"{label}.part_id={side.part_id!r} 不存在（工程部件: {sorted(known)}）"
                    )
        if errors:
            raise ConnectionValidationError("连接校验失败: " + "; ".join(errors))

    def summary(self) -> str:
        summary = (
            f"{self.KIND} {self.name}: {self.side_a.part_id} ↔ {self.side_b.part_id}"
        )
        if hasattr(self, 'adjust_nodes'):
            summary += '；绑定调整节点' if self.adjust_nodes else '；保留网格节点位置'
        return summary


@dataclass
class BondedConnection(ConnectionBase):
    """粘接：两侧面绑定一体（无相对滑移/分离）。

    mode（模块 16）：
    - "tie"（默认）：求解经 *TIE 卡（ccx 2.22 实跑实证语法）；
    - "merge"：共节点模式——网格阶段用 gmsh removeDuplicateNodes 合并贴合
      界面重合节点（模块 6 红线：NSET/连接表写 gmsh tag），deck 不出连接卡。
    adjust_nodes：仅 tie 模式使用；默认 False（ADJUST=NO），True 会把从面
    节点投影到主面，可能改变高阶单元质量。旧工程缺此字段时保留旧行为 True。
    物理假设摘要见 app/docs/connections.md §粘接。
    """

    KIND: str = "bonded"
    mode: str = "tie"
    adjust_nodes: bool = False

    def validate(self, part_ids=None) -> None:  # noqa: ANN001
        super().validate(part_ids)
        if self.mode not in ("tie", "merge"):
            raise ConnectionValidationError(
                f"粘接连接 mode 必须为 'tie'（*TIE 卡）或 'merge'（共节点），实际 {self.mode!r}"
            )
        if not isinstance(self.adjust_nodes, bool):
            raise ConnectionValidationError("adjust_nodes 必须为布尔值")


@dataclass
class ContactFrictionConnection(ConnectionBase):
    """可分离接触 + 摩擦：法向可分离（不传拉力）、切向 Coulomb 摩擦 μ。

    friction_mu ∈ [0,1]（0=理想光滑无摩擦；钢-钢典型 0.3，模板默认）。
    """

    KIND: str = "contact_friction"
    friction_mu: float = 0.3

    def validate(self, part_ids=None) -> None:  # noqa: ANN001
        super().validate(part_ids)
        errors: list[str] = []
        mu = self.friction_mu
        if not isinstance(mu, (int, float)) or isinstance(mu, bool) or not (0.0 <= float(mu) <= 1.0):
            errors.append(f"摩擦系数 μ 必须满足 0 ≤ μ ≤ 1，实际 {mu!r}")
        if errors:
            raise ConnectionValidationError("接触摩擦连接校验失败: " + "; ".join(errors))


@dataclass
class BoltConnection(ConnectionBase):
    """简化螺栓：耦合区 + 预紧力 F_p，**不建螺栓杆实体**（不模拟钉杆弯曲/
    螺纹/螺母滑移，见 connections.md §螺栓）。

    pretension_n：预紧力 F_p [N]，>0（M8 8.8 级典型 ~16 kN，模板默认）；
    coupling_radius_mm：预紧耦合区半径 [mm]，>0（垫片等效半径，模板默认
    取公称直径）。
    """

    KIND: str = "bolt"
    pretension_n: float = 16000.0
    coupling_radius_mm: float = 8.0

    def validate(self, part_ids=None) -> None:  # noqa: ANN001
        super().validate(part_ids)
        errors: list[str] = []
        fp = self.pretension_n
        if not isinstance(fp, (int, float)) or isinstance(fp, bool) or float(fp) <= 0.0:
            errors.append(f"预紧力 F_p 必须为正数 [N]，实际 {fp!r}")
        r = self.coupling_radius_mm
        if not isinstance(r, (int, float)) or isinstance(r, bool) or float(r) <= 0.0:
            errors.append(f"耦合区半径必须为正数 [mm]，实际 {r!r}")
        if errors:
            raise ConnectionValidationError("螺栓连接校验失败: " + "; ".join(errors))


@dataclass
class PinConnection(ConnectionBase):
    """简化销轴：销部件与板孔壁**零间隙绑定**；声明忽略挤压（bearing）、
    间隙、销弯曲柔度（IGNORED_EFFECTS）。

    数据模型为「销侧-板侧」一对引用；销贯穿多板时为每块板声明一条
    PinConnection（共用销侧引用）→ 全部绑定后销成为板间传力路径
    （几何样例 samples/assembly_lap_pin.step：销 p3 ↔ 板 p1/p2 孔壁）。
    """

    KIND: str = "pin"
    adjust_nodes: bool = False
    IGNORED_EFFECTS = ("挤压（bearing）", "间隙（配合缝隙）", "销弯曲柔度")

    def validate(self, part_ids=None) -> None:
        super().validate(part_ids)
        if not isinstance(self.adjust_nodes, bool):
            raise ConnectionValidationError("adjust_nodes 必须为布尔值")

    def ignored_effects(self) -> tuple[str, ...]:
        """本连接显式声明忽略的物理效应（进计算报告「模型假设」节）。"""
        return tuple(self.IGNORED_EFFECTS)


# --------------------------------------------------------------------------- 模板
@dataclass(frozen=True)
class ConnectionTemplate:
    """连接模板：参数化默认值（钢-钢 μ=0.3 等），instantiate_template 可覆盖。"""

    key: str
    cls_name: str  # 连接类名（_REGISTRY 同名）
    label: str
    defaults: dict = field(default_factory=dict)
    description: str = ""


CONNECTION_TEMPLATES: list[ConnectionTemplate] = [
    ConnectionTemplate(
        key="bonded_steel_steel", cls_name="BondedConnection", label="粘接（钢-钢）",
        defaults={"adjust_nodes": False},
        description="钢-钢粘接/焊缝等效：两侧面绑定一体，无相对滑移/分离",
    ),
    ConnectionTemplate(
        key="contact_steel_steel", cls_name="ContactFrictionConnection", label="可分离接触+摩擦（钢-钢）",
        defaults={"friction_mu": 0.3},
        description="钢-钢干摩擦典型值 μ=0.3（文献范围 0.15~0.5 中值）；法向可分离不传拉力",
    ),
    ConnectionTemplate(
        key="bolt_m8_88", cls_name="BoltConnection", label="简化螺栓 M8 8.8 级",
        defaults={"pretension_n": 16000.0, "coupling_radius_mm": 8.0},
        description="M8 8.8 级：F_p≈0.7·As·σ0.2≈16 kN（As=36.6mm²）；耦合区半径=公称直径",
    ),
    ConnectionTemplate(
        key="bolt_m10_88", cls_name="BoltConnection", label="简化螺栓 M10 8.8 级",
        defaults={"pretension_n": 26000.0, "coupling_radius_mm": 10.0},
        description="M10 8.8 级：F_p≈0.7·As·σ0.2≈26 kN（As=58mm²）；耦合区半径=公称直径",
    ),
    ConnectionTemplate(
        key="pin_steel_zero_clearance", cls_name="PinConnection", label="简化销轴（零间隙绑定）",
        defaults={"adjust_nodes": False},
        description="销部件与板孔壁零间隙绑定；忽略挤压/间隙/销弯曲柔度",
    ),
]


_CONNECTION_CLASSES: dict[str, type] = {
    cls.__name__: cls
    for cls in (BondedConnection, ContactFrictionConnection, BoltConnection, PinConnection)
}


def get_template(key: str) -> ConnectionTemplate:
    """模板 key → 模板；未知抛 KeyError。"""
    for t in CONNECTION_TEMPLATES:
        if t.key == key:
            return t
    raise KeyError(f"未知连接模板: {key!r}（可用: {[t.key for t in CONNECTION_TEMPLATES]}）")


def instantiate_template(
    template_key: str,
    name: str,
    side_a: ConnectionSide,
    side_b: ConnectionSide,
    part_ids=None,  # noqa: ANN001
    **overrides,
) -> ConnectionBase:
    """模板 + 覆盖参数 → 连接实例（非法值被 validate 拒绝，不静默修正）。

    未知覆盖参数抛 KeyError（防拼错参数静默失效）；实例化后立即 validate
    （part_ids 给定时同时校验部件存在性）。类型派发用本模块类表；
    store._REGISTRY 的注册见 core/store.py（持久化层）。
    """
    tpl = get_template(template_key)
    cls = _CONNECTION_CLASSES.get(tpl.cls_name)
    if cls is None:
        raise ConnectionValidationError(f"模板 {tpl.key} 的连接类型不可用: {tpl.cls_name}")
    params = dict(tpl.defaults)
    unknown = set(overrides) - set(params)
    if unknown:
        raise KeyError(
            f"模板 {tpl.key} 没有可覆盖参数 {sorted(unknown)}（可覆盖: {sorted(params)}）"
        )
    params.update(overrides)
    conn = cls(name=name, side_a=side_a, side_b=side_b, **params)
    conn.validate(part_ids)
    return conn


def validate_connection_in_project(project, conn) -> None:  # noqa: ANN001
    """工程语境校验：部件存在性（project.parts）+ 固有校验（自身拒绝等）。"""
    conn.validate(part_ids=[p.part_id for p in project.parts])


def part_faces_for(project, model, part_id: str) -> list:  # noqa: ANN001
    """(part_id) → 该部件的 occ 面清单（duck-typing；模块 14/15/16 共用）。

    part_id 空 → 全模型面清单（旧版单零件语义）；非空 → project.part_by_id
    定位部件（solid_index → occ 实体 tag → FaceInfo.solid_tag 过滤）。
    未知 part_id / solid_index 越界 → KeyError（显式，绝不静默回退全模型面清单
    ——防跨部件贴错面）。
    """
    if not part_id:
        return list(model.faces)
    part = project.part_by_id(part_id)  # 未知部件 → KeyError（显式）
    solids = list(getattr(model, "solids", []))
    idx = int(getattr(part, "solid_index", -1))
    if not (0 <= idx < len(solids)):
        raise KeyError(
            f"部件 {part_id!r} 无法在当前模型中定位（solid_index={idx}，模型实体数 {len(solids)}）"
        )
    solid_tag = int(solids[idx].tag)
    return [f for f in model.faces if f.solid_tag == solid_tag]


# --------------------------------------------------------------------------- 草稿
DRAFT_KINDS = ("bonded", "contact", "pin")


def consistent_draft_directions(draft: dict) -> dict:
    """Keep one slave part per pair, including cached drafts made by older versions.

    The largest common patch supplies the original recommended direction. Reversing
    the direction on adjacent patches of the same pair creates cyclic MPCs along
    their common edges. Input rows/order/indices and geometry evidence are retained.
    This acts on a draft only; saved/manual connection objects are never reversed.
    """
    rows = list(draft.get('candidates') or [])
    choices = {}
    for row in sorted(rows, key=lambda r: -float(r.get('common_area_mm2') or 0)):
        if not row.get('part_a') or not row.get('part_b'):
            continue
        slave = str(row.get('slave_side', 'a'))
        if slave not in ('a', 'b'):
            continue  # The normal validation reports the malformed row.
        pair = tuple(sorted((str(row['part_a']), str(row['part_b']))))
        choices.setdefault(pair, str(row[f'part_{slave}']))
    normalized = []
    for row in rows:
        item = dict(row)
        if row.get('part_a') and row.get('part_b') and str(item.get('slave_side', 'a')) in ('a', 'b'):
            pair = tuple(sorted((str(row['part_a']), str(row['part_b']))))
            slave = 'a' if choices[pair] == str(row['part_a']) else 'b'
            item.update(slave_side=slave, master_side='b' if slave == 'a' else 'a')
        normalized.append(item)
    return {**draft, 'candidates': normalized, 'direction_policy': 'one_slave_part_per_pair_largest_patch'}


def _draft_face(row: dict, side: str, faces_of_part: dict, diag: float):  # noqa: ANN001
    """草稿行 → 当前模型的面（优先按指纹在该部件内重解析，跨进程安全）。

    为什么要「限定部件」：贴合界面两侧面积/形心常常完全相同（例如两杆串联的
    对接面），而指纹的法向比较用 1-|n1·n2|，平行反向也算匹配——全模型解析会
    得到 ambiguous，part 内解析才唯一。这条与 project_workflow 的 re-resolve
    口径一致（挂接目标 = part_id + 面指纹）。

    后台作业与 GUI 各自 import 同一 STEP，occ tag 不承诺一致，因此草稿带面
    指纹；指纹不唯一命中时显式报错，绝不猜一个 tag 贴上去。
    """
    from .face_identity import FaceFingerprint, resolve_face

    part_id = str(row[f"part_{side}"])
    faces = faces_of_part.get(part_id)
    if faces is None:
        raise ValueError(f"草稿部件 {part_id!r} 不在当前工程部件清单中")
    data = row.get(f"fingerprint_{side}")
    if not data:
        return None
    fp = FaceFingerprint(
        area_mm2=float(data["area_mm2"]),
        centroid=tuple(float(x) for x in data["centroid"]),
        normal=tuple(float(x) for x in data["normal"]),
    )
    tag, status = resolve_face(faces, fp, diag)
    if status != "ok":
        raise ValueError(f"部件 {part_id} 上的面指纹未唯一命中（{status}）——几何可能已变化，请重新识别配合面")
    return int(tag)


def connections_from_draft(project, model, draft, *, accepted=None, kinds=None) -> dict:  # noqa: ANN001
    """连接草稿（cad.connection_draft 的候选）→ 连接对象列表（不改工程）。

    accepted：候选索引集合（None = 全部采用）；kinds：{索引: "bonded"|"contact"|"pin"}
    覆盖草稿推荐类型。每个公共面补丁生成一条连接——销钉-孔壁被螺纹切成几十个
    小面时，合成一条会丢掉大部分传力路径（见 connection_draft 模块说明）。

    去重按 (part_id, 面指纹) 双向比较：同一对同一面已有连接就跳过，重复生成
    会导致 deck 里重复 *TIE 卡（ccx 允许但会重复约束同一节点）。
    kind 非法/面 tag 失效 → 记入 errors 并继续，不中断整批（用户能看到哪几条失败）。
    """
    from .face_identity import fingerprint_from_face

    import numpy as np

    candidates = consistent_draft_directions(draft)['candidates']
    lo, hi = model.overall_bbox
    diag = float(np.linalg.norm(np.asarray(hi, dtype=float) - np.asarray(lo, dtype=float)))
    accepted_set = None if accepted is None else {int(i) for i in accepted}
    kind_of = {int(k): str(v) for k, v in (kinds or {}).items()}
    part_ids = [p.part_id for p in project.parts]
    solids = list(model.solids)
    faces_of_part: dict[str, list] = {}
    for part in project.parts:
        index = int(getattr(part, "solid_index", -1))
        if 0 <= index < len(solids):
            solid_tag = int(solids[index].tag)
            faces_of_part[part.part_id] = [f for f in model.faces if f.solid_tag == solid_tag]

    existing: set[tuple] = set()
    for conn in project.connections:
        existing.add((conn.side_a.part_id, conn.side_a.face, conn.side_b.part_id, conn.side_b.face))
        existing.add((conn.side_b.part_id, conn.side_b.face, conn.side_a.part_id, conn.side_a.face))

    built: list = []
    errors: list[dict] = []
    skipped = 0
    counters: dict[tuple, int] = {}
    for index, row in enumerate(candidates):
        if accepted_set is not None and index not in accepted_set:
            continue
        try:
            tag_a = _draft_face(row, "a", faces_of_part, diag)
            tag_b = _draft_face(row, "b", faces_of_part, diag)
            face_a = model.face_by_tag(int(tag_a if tag_a is not None else row["face_a"]))
            face_b = model.face_by_tag(int(tag_b if tag_b is not None else row["face_b"]))
            sides = {
                "a": ConnectionSide(str(row["part_a"]), fingerprint_from_face(face_a)),
                "b": ConnectionSide(str(row["part_b"]), fingerprint_from_face(face_b)),
            }
            slave = str(row.get("slave_side", "a"))
            if slave not in ("a", "b"):
                raise ValueError(f"草稿 slave_side 非法: {slave!r}")
            side_a, side_b = sides[slave], sides["b" if slave == "a" else "a"]
            key = (side_a.part_id, side_a.face, side_b.part_id, side_b.face)
            if key in existing:
                skipped += 1
                continue
            kind = kind_of.get(index, row.get("recommended", "bonded"))
            if kind not in DRAFT_KINDS:
                raise ValueError(f"连接类型 {kind!r} 不支持（可用 {DRAFT_KINDS}）")
            counters_key = tuple(sorted((side_a.part_id, side_b.part_id)))
            counters[counters_key] = counters.get(counters_key, 0) + 1
            name = f"{row.get('name_a', side_a.part_id)} ↔ {row.get('name_b', side_b.part_id)} #{counters[counters_key]}"
            if kind == "bonded":
                conn = BondedConnection(name=name, side_a=side_a, side_b=side_b)
            elif kind == "contact":
                conn = ContactFrictionConnection(name=name, side_a=side_a, side_b=side_b, friction_mu=0.3)
            else:
                conn = PinConnection(name=name, side_a=side_a, side_b=side_b)
            conn.validate(part_ids)
            existing.add(key)
            existing.add((side_b.part_id, side_b.face, side_a.part_id, side_a.face))
            built.append(conn)
        except Exception as exc:  # noqa: BLE001 - 单条失败不中断整批，但必须显式记录
            errors.append({"index": index, "name_a": row.get("name_a"), "name_b": row.get("name_b"),
                           "face_a": row.get("face_a"), "face_b": row.get("face_b"),
                           "error": f"{type(exc).__name__}: {exc}"})
    return {"connections": built, "skipped": skipped, "errors": errors}


def apply_connection_draft(project, model, draft, *, accepted=None, kinds=None) -> dict:  # noqa: ANN001
    """连接草稿 → 写入 project.connections（调用方负责 invalidate/保存）。

    返回 {added, skipped, errors}；errors 非空时仍写入成功的部分——整装几十条
    连接里有一两条面失效不应让用户重做全部，但失败项必须显示出来。
    """
    result = connections_from_draft(project, model, draft, accepted=accepted, kinds=kinds)
    project.connections.extend(result["connections"])
    return {"added": len(result["connections"]), "skipped": result["skipped"], "errors": result["errors"]}


# ---------------------------------------------------------------- 连接复核决策闭环（模块47）
REVIEW_DECISIONS = ("pending", "keep_bonded", "switch_contact", "remove")
REVIEW_DECISION_LABELS = {
    "pending": "未决策",
    "keep_bonded": "保留粘接",
    "switch_contact": "改为可分离接触",
    "remove": "删除连接",
}


def undecided_connections(project) -> list:
    """needs_review（上次求解报告过未绑定从面节点）且尚未决策的连接。"""
    return [c for c in project.connections
            if bool(getattr(c, "needs_review", False))
            and getattr(c, "review_decision", "pending") == "pending"]


def undecided_block_message(project) -> str | None:
    """存在未决策连接时给出中文拦截文案；无则 None（供求解入口校验）。"""
    undecided = undecided_connections(project)
    if not undecided:
        return None
    names = "；".join(f"「{c.name}」({type(c).__name__})" for c in undecided)
    return (f"存在 {len(undecided)} 条未决策的连接复核项（上次求解报告了未绑定从面节点）：{names}。"
            "请先逐条复核决策（保留粘接 / 改为可分离接触 / 删除连接）后再求解；"
            "节点计数只是关联证据，不据此自动决策。")


def apply_connection_decision(project, connection, decision: str, *,
                              evidence: dict | None = None,
                              decided_at: str | None = None) -> dict:
    """逐连接复核决策：写回连接字段并按枚举落地。

    - keep_bonded：保留粘接，连接原样保留（复核闭合，警告如有则呈现为已复核）；
    - switch_contact：原位替换为 ContactFrictionConnection（μ 默认 0.3，两侧
      面指纹对原样保留），复核字段随迁；
    - remove：从 project.connections 删除（返回 removed 连接供落档）。
    非法决策枚举拒绝；返回 {'decision', 'removed', 'replacement'}。
    """
    if decision not in REVIEW_DECISIONS:
        raise ConnectionValidationError(
            f"连接复核决策枚举非法: {decision!r}（可用: {list(REVIEW_DECISIONS)}）")
    if decision == "pending":
        raise ConnectionValidationError("'pending' 是未决策状态，不能作为决策写入")
    if connection not in project.connections:
        raise ConnectionValidationError(f"连接不在工程中: {connection.name!r}")
    stamp = decided_at or __import__("datetime").datetime.now().isoformat(timespec="seconds")
    connection.review_decision = decision
    connection.review_decided_at = stamp
    if evidence is not None:
        connection.review_evidence = dict(evidence)
    removed = replacement = None
    if decision == "switch_contact":
        replacement = ContactFrictionConnection(
            name=connection.name, side_a=connection.side_a, side_b=connection.side_b,
            friction_mu=0.3, needs_review=connection.needs_review,
            review_decision=decision, review_decided_at=stamp,
            review_evidence=dict(connection.review_evidence))
        index = project.connections.index(connection)
        project.connections[index] = replacement
    elif decision == "remove":
        removed = connection
        project.connections.remove(connection)
    return {"decision": decision, "removed": removed, "replacement": replacement}
