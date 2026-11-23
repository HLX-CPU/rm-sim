# 模块 17 设计说明：粘接连接求解验证

日期：2026-11-02 ｜ 状态：已完成，验收 `verify/verify_bonded_connection.py`
exit 0（①-⑨；全程真实 ccx 2.22，串联双杆算例）。

## 范围

- `cad/make_assembly_samples.py`：新增 `make_serial_rods_step` →
  `samples/assembly_serial_rods.step`（杆1 钢 x∈[0,100]、杆2 铝 x∈[100,200]，
  各 10×10×100mm，x=100 界面；几何不含材料——材料按部件在工程/验证侧指定）。
- `app/docs/connections.md` §粘接：物理假设补「假设 4（线弹性范围）」；
  验证算例指针更新为已实现的串联双杆算例 + 全套门限（⑧）。
- `solver/deck.py`：build_assembly_case_inp 新增**装配连接性门**（⑦）。
- `verify/verify_bonded_connection.py`（新）。
- 无产品求解代码新语法（*TIE/共节点双实现均沿用模块 16，本模块是其求解级
  物理验证）。

## 首跑实证驱动的两个关键修正（诚实记录）

1. **③ 界面传力的测量口径**：ccx 2.22 的 RF/FORC **不含 *TIE MPC 力**——
   实测从面/主面节点力合力均为 ±5.4N（求解残差量级），远非 1000N。故 ③
   「从面侧节点力合力」用**从面应力积分 Σσxx·A**（面力梯形积分，物理上即
   界面传力）：实测 1000.1212 N（0.012%）。证据：dat RF@IFC_SLV=+5.3862/
   RF@IFC_MST=−5.3862（ antisym 残差）+ 从面 SXX 积分 1000.12。
2. **⑦ 断带负例的实现口径**：ccx 2.22 对断带（杆2 悬浮）体系**仍
   'Job finished'**（exit=0）但产出 **Ux≈−8.6e10 mm 垃圾解**（有限值非 NaN）
   ——「任务 failed」无法依赖 ccx 自然给出。故在 build_assembly_case_inp 加
   **装配连接性门**：每个部件必须经外部固定（fixed 且已挂接）或任一连接边
   （拓扑连通性 BFS——所有连接类型均计边）接地；断开 → DeckError 显式拒绝
   （错误指明悬浮部件 p2 与原因）。比把垃圾结果交给用户安全；口径变化如实
   记录于 verify docstring 与 connections.md。注意（不在此拒）：接触可分离、
   简化螺栓无杆实体——拓扑连通不保证传力方向可解。

## 验收对应（全部在本次实际运行通过，verify_bonded_connection.log）

| 验收 | 结果 |
|---|---|
| ① δ 门 + 判别力 | tie-2mm 端部 Ux=0.01908529 vs 0.0190476（0.198%≤2%）；杆2 误配为钢 δ=0.00953194 vs 误配理论 0.0095238（0.085%）且距正确理论 50.0%（门咬；per-part 材料正确） |
| ② ΣRF | (−1000.000020, 0.000003, 0.000004) N——偏差 ≤0.01% |
| ③ 界面传力 | 从面应力积分 Σσxx·A=1000.1212 N（±1% 内；66 面单元） |
| ④ 界面两侧 | SXX 从面 9.9732 / 主面 10.1131 MPa，差 1.38%（≤15%）；Mises 12.96/8.08（差 37.6%）——首跑标定豁免条款留档（*TIE 从面约束剪切扰动，轴向 SXX 为有效对照量） |
| ⑤ 网格不敏感 | δ(2mm)=0.01908529 vs δ(1mm)=0.01907022，差 0.0790%（≤1%） |
| ⑥ 双实现 | tie deck 含 *TIE；merge deck 无 *TIE 且 153 个 x=100 面节点被两部件共享（removeDuplicateNodes），两种模式 δ 均过门（0.198%） |
| ⑦ 断带负例 | 连接性门 → DeckError「部件 ['p2'] 未被外部固定或任何连接边链接地…刚度阵奇异」+ 首跑垃圾解证据留档 |
| ⑧ 文档 | connections.md 粘接节含 verify_bonded_connection / 0.0190476 / 2% / 1000N / 15% / 线弹性 / assembly_serial_rods |
| ⑨ GUI 无关 | 无 PySide6 进程 + 静态扫描 0 违规 |

## 回归（本次实际运行，全部 exit 0）

verify_mesh_generation / verify_load_definition / verify_main_entry /
verify_step_import / verify_assembly_import / verify_assembly_tree /
verify_connection_model / verify_assembly_solve（含连接性门后的接触/螺栓
deck 仍过——门只拒拓扑断开）/ verify_static_solve / verify_report_archive /
verify_project_roundtrip（后三者为真实 ccx）。

## 未做 / 留给后续模块

- 接触摩擦连接的求解级验证算例（斜面滑移/分离）——接触卡语法已在模块 16
  实跑通过，物理门属后续。
- 螺栓/销连接的求解级验证（预紧传力门、双剪销传力链门）。
- 连接性门的 GUI 呈现（拒绝原因进装配树/弹窗——deck 层错误已带明确原因）。
