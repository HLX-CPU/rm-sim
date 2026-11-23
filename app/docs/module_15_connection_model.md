# 模块 15 设计说明：连接数据模型与物理假设文档

日期：2026-11-02 ｜ 状态：已完成，验收 `verify/verify_connection_model.py` exit 0（①-⑤，首次运行即通过）。

## 范围

- `core/connections.py`（新，GUI 无关层 core，layer_guard 递归扫描自动覆盖）：
  - `ConnectionBase`：name + **两部件面指纹对**（side_a/side_b 各为
    `ConnectionSide(part_id, FaceFingerprint)`）；`validate(part_ids=None)`——
    固有校验（名称/两侧 part_id 非空/同一部件自身拒绝/面指纹类型）+
    工程语境校验（part_ids 给定时不存在 part_id 拒绝）。
  - 四类连接：`BondedConnection`（粘接，无附加参数）、
    `ContactFrictionConnection`（可分离接触+Coulomb 摩擦 μ∈[0,1]，默认 0.3）、
    `BoltConnection`（简化螺栓：**不建螺栓杆实体**，预紧力 F_p>0 + 耦合区
    半径>0）、`PinConnection`（销部件与板孔壁**零间隙绑定**，
    `IGNORED_EFFECTS=("挤压（bearing）","间隙（配合缝隙）","销弯曲柔度")`）。
  - 模板：`CONNECTION_TEMPLATES`（bonded_steel_steel / contact_steel_steel
    μ=0.3 / bolt_m8_88 F_p=16kN / bolt_m10_88 F_p=26kN / pin_steel_zero_clearance）
    + `instantiate_template(key, name, side_a, side_b, part_ids, **overrides)`——
    默认值可逐参数覆盖，未知覆盖参数/未知模板 KeyError，非法值 validate 拒绝。
  - `_RESERVED_KINDS = ("elastic",)`：**弹性连接类型位本期有意推迟**（无占位
    实现/模板，仅预留命名空间，见 notes）。
- `core/project.py`：`Project.connections: list[ConnectionBase]`（默认空，
  旧工程缺字段→空列表，无需迁移）。
- `core/store.py`：ConnectionSide + 四类连接注册 `_REGISTRY`。
- `app/docs/connections.md`（新）：四节物理假设权威文档（粘接/接触摩擦/
  螺栓/销轴），每节 物理假设/适用范围/不模拟项/验证算例指针；
  通用约定（面指纹对、re-resolve、与模块 14 Constraint(kind="bonded")
  的语义关系、弹性连接预留）写在文档头部。

## 关键决策与证据

1. **面指纹对 = 两侧 ConnectionSide**：与模块 14 的 (part_id, face) 挂接语义
   一致；持久化走 `__type__` 注册（ConnectionSide 也注册——嵌套结构完整还原）。
2. **同一部件自身拒绝是固有校验**（不需要工程语境）；part_id 存在性是工程
   语境校验（`validate_connection_in_project` 以 project.parts 为准）。
3. **销轴的数据模型口径**：PinConnection 为「销侧-板侧」一对引用；销贯穿
   两板时声明两条（销↔板1、销↔板2，共用销侧引用），全部零间隙绑定后销成为
   板间传力路径——与几何样例 samples/assembly_lap_pin.step（p3 销 ↔ p1/p2
   孔壁）对应。「销部件与两板孔壁零间隙绑定」是连接方案级描述，落在
   connections.md §销轴。物理上销-孔绑定而非接触 → 孔壁压力是绑定反力而非
   真实挤压分布（假设 4 显式声明）。
4. **螺栓不建杆实体**：预紧以耦合区（垫片等效半径，默认=公称直径）力系施加
   ——F_p 默认按 8.8 级 0.7·As·σ0.2 估算（M8: 16kN / M10: 26kN），模板可覆盖；
   施加机制与模块 14 力矩节点力偶门同源（文档指针已注明）。
5. **验证算例指针的诚实口径**：每个指针分两类——本期已过的 verify（标注
   「本期已过」）与规划中的算例（标注「规划（接触与绑定求解模块）」），
   不把规划项写成已验证。

## 验收对应（全部在本次实际运行通过，verify_connection_model.log）

| 验收 | 结果 |
|---|---|
| ① round-trip + 注册 | 四类连接 to_dict/from_dict 逐字段相等（含面指纹对嵌套）；ConnectionSide+四类在 _REGISTRY；工程 save/load 多态还原 ['BondedConnection','ContactFrictionConnection','BoltConnection','PinConnection'] |
| ② 校验拒绝 | 自身连接（p1↔p1）/不存在 part_id（p999）/空 part_id/缺面指纹均 ConnectionValidationError，信息指明原因；合法对照通过 |
| ③ 物理假设文档 | 四节齐全；每节含「物理假设」「不模拟」「适用范围」「验证算例指针」；假设描述 3/3/4/4 条（≥2） |
| ④ 模板 | 默认 μ=0.3、F_p=16000N/r=8mm 实例化；覆盖 μ=0.5、F_p=30000N 生效；μ=1.5/-0.1、F_p=0/-100、r=0 拒绝；未知覆盖参数/未知模板 KeyError；elastic 预留位断言 |
| ⑤ GUI 无关 | 无 PySide6 进程 + layer_guard 扫描 0 违规（含 connections.py） |

## 回归（本次实际运行，全部 exit 0）

verify_main_entry / verify_load_definition / verify_assembly_tree /
verify_step_import / verify_assembly_import / verify_project_roundtrip
（真实 ccx 三进程，store/project 注册与新字段门）。

## 未做 / 留给后续模块

- 连接的求解语义转换（*TIE / *CONTACT / 预紧力系 / 销绑定链）——本文档
  「验证算例指针」中的规划项；干涉面按部件 face_map 同属该模块。
- 弹性连接（elastic）：类型位已预留（_RESERVED_KINDS），实现与模板按
  调研报告 §4「分布耦合、弹性支撑」在后续模块落地。
- GUI 连接管理对话框/装配树连接节点展示（数据模型与校验已就绪）。
