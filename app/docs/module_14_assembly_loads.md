# 模块 14 设计说明：装配级约束与载荷定义

日期：2026-11-02 ｜ 状态：已完成，验收 `verify/verify_assembly_loads.py` exit 0（①-⑥，含零件模块门复跑）。

## 范围

- `core/project.py`：Constraint/LoadBase 挂接目标扩展为 **(part_id, 面指纹)**
  （`part_id: str = ""`，空=旧版单零件全局语义，逐字段向后兼容）；
  Constraint 新增 **kind="bonded"（内部连接）**——部件间粘接/绑定的语义声明，
  不构成接地；`is_solvable` 语义扩展：**只有外部固定（kind="fixed" 且已挂接）
  才构成接地**——装配体至少一个外部约束，仅内部连接不可解。
  四类载荷/约束 summary 带「［部件 pX］」标签（装配树/日志展示）。
- `solver/convert.py`：**顺手修正 docstring**（本轮亲读核实）：「重力 → *GRAV」
  改为「*DLOAD 的 GRAV 标签（ccx 2.22 实证无 *GRAV 独立关键字）」——与
  deck.py 实现一致，防后续照抄错误关键字；同时更新段落标题与 gravity_vector
  docstring。PressureDLoadRef/CloadPlan/MomentCouplePlan 增加 part_id 字段，
  压力单元面集名带部件前缀 `PRESS_{part}_FACE_{tag}`（safe_part_label）。
- `solver/deck.py`：NSET 选择泛化为**按 face_tag 解析**（`_nset_for_tag`）——
  legacy {"FIXED","LOADFACE"} 文本逐字不变；装配流可用部件前缀 NSET
  （{"FIXED_P1","LOAD_P2"}）；无命中/映射冲突/缺 NSET 显式 DeckError（不静默
  选错集合）；bonded 约束进注释、不产生 *BOUNDARY；per-part 重力（part_id
  非空）显式 DeckError（需部件 ELSET，属后续模块——不做静默范围改变）。
- `solver/project_workflow.py`：`part_scoped_faces(project, model, part_id)`
  （part_id→该部件 occ 面清单；未知部件 KeyError、越界 WorkflowError——绝不
  静默回退全模型）；`reresolve_project` 装配感知：part_id 非空时指纹只在
  **该部件**面清单内 resolve（每部件复用 core.face_identity.resolve_face）；
  未命中/部件无法定位 → face_tag=0 + is_confirmed=False + 显式警告。

## 关键决策与证据

1. **part_id="" 向后兼容**：旧工程 JSON 缺字段 → 默认 ""；re-resolve 走全模型
   面清单（行为不变）；deck 走 legacy NSET 名（文本逐字不变）——回归
   verify_load_definition/verify_static_solve/verify_report_archive/
   verify_project_roundtrip 全部 PASS 佐证。
2. **bonded = 内部连接语义**：kind 白名单 ("fixed","bonded")；bonded 必须指明
   part_id（内部连接只存在于装配部件间）；is_solvable 表达式不变（只数
   kind=="fixed"）但语义升级为「外部约束才接地」——仅 bonded（即使已挂接）
   → False，加一个外部 fixed → True（验收③实测，含多工况组合 [False,True]）。
   接触/绑定求解实现属后续模块，deck 现阶段对 bonded 只写注释行。
3. **不串件证据**（验收②，两叠块装配实测）：p1 x=0 面与 p2 x=20 面分别转换——
   面节点集互斥（44/45 节点 0 交集）且各自落在所属部件包围盒内；压力单元面集
   PRESS_p1_FACE_1（66 面）/ PRESS_p2_FACE_8（68 面）互斥（0 共享单元）且
   所属单元质心分别位于 p1/p2 空间域；部件前缀 NSET FIXED_P1(280 节点)/
   LOAD_P2(280 节点) 互斥、节点全部位于各自挂接面上；deck 文本 *BOUNDARY 只引
   FIXED_P1、*CLOAD 只引 LOAD_P2、*NODE PRINT 按部件 NSET。
4. **已知限制（如实记录）**：贴合部件的**干涉面**（两叠块 y=20 贴合面）在
   全模型 face_map 中 bbox 重合 → 归类歧义；本模块的转换/断言均用非干涉面。
   干涉面的按部件归属（face_map 带 solid_tag 约束）属接触/绑定模块。
5. **验尸记录**：verify 开发期修了 3 处自身错误——压力集合名断言大小写
   （PRESS_p1_ 而非 PRESS_P1_）、单元质心计算（tets 行是节点索引，需经
   mesh.points 取坐标）、LOAD_P2 节点 y 边界用了开区间（面边界节点恰在
   y=20/40）。实现本身无改动。

## 验收对应（全部在本次实际运行通过，verify_assembly_loads.log）

| 验收 | 结果 |
|---|---|
| ① round-trip | 约束（fixed/bonded）+ 四类载荷 (part_id,指纹) 序列化与工程往返逐字段相等 |
| ② 按部件转换 | 节点集/单元面集互斥且按部件 bbox 断言；deck *BOUNDARY→FIXED_P1、*CLOAD→LOAD_P2、NODE PRINT 按部件 |
| ③ is_solvable | 仅 bonded=False、+外部 fixed=True、未挂接 fixed=False、多工况 [False,True]、坏 kind/bonded 缺 part_id 拒绝 |
| ④ 指纹不匹配 | 载荷挂 p2 但指纹是 p1 的面 → is_confirmed=False+警告指明部件 p2+face_tag=0；未知 part_id→KeyError；deck face_tag=0→DeckError 指明载荷 |
| ⑤ 语义门 | 装配挂接下：面力 ΣF=(0,-100,0)N 精确均分、压力合力=p·A 纯沿法向、力矩 ΣM·轴=5000（1e-6）；零件模块门 verify_load_definition.py 复跑 PASS（8/8） |
| ⑥ docstring | convert.py 无「重力 → *GRAV」措辞、含「*DLOAD 的 GRAV 标签 + 无 *GRAV 独立关键字」；deck.py 实现行 Eall, GRAV 与之一致；gravity_vector=方向×g 数值断言 |

## 回归（本次实际运行，全部 exit 0）

verify_load_definition / verify_main_entry / verify_step_import /
verify_assembly_import / verify_assembly_tree / verify_static_solve（真实 ccx）
/ verify_report_archive（真实 ccx）/ verify_project_roundtrip（真实 ccx 三进程）。

## 未做 / 留给后续模块

- bonded 的求解实现（*TIE/接触）与干涉面按部件 face_map（face_map 带
  solid_tag 约束）。
- per-part 重力（需部件 ELSET；deck 现阶段显式拒绝 part_id 非空的重力）。
- per-part 材料的 *SOLID SECTION 分 ELSET 写出（deck 仍为全局单一材料）。
- GUI 载荷/约束编辑对话框的部件下拉（数据模型与树摘要已就绪）。
