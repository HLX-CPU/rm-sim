# 模块 16 设计说明：装配 INP 组装与求解调度

日期：2026-11-02 ｜ 状态：已完成，验收 `verify/verify_assembly_solve.py` exit 0
（①-⑥ + 附加卡族实跑；全程真实 ccx 2.22）。

## 范围

- `mesh/mesher.py`：`generate_tet10_mesh` 新增 `part_elsets`（{ELSET 名: occ
  实体 tag} → 每实体 C3D10 单元 tag 集，写 *ELSET 并回填
  `MeshResult.element_sets`）与 `remove_duplicate_nodes`（共节点粘接模式：
  `gmsh.model.mesh.removeDuplicateNodes()` 合并贴合界面重合节点）。装配整体
  在同一 gmsh 会话内网格化——多实体节点号天然不重、免重映射。
- `solver/deck.py`：
  - `parse_mesh_inp` 增加对 *ELSET 块的解析（附加键 `elsets`，向后兼容）；
    *NODE 分支排除 *NODE PRINT/FILE/OUTPUT（解析完整 deck 实证踩坑）。
  - `build_case_inp` 重构为共享卡构建器 `_boundary_cards`/`_load_cards`
    （文本逐字不变——verify_static_solve/report_archive/roundtrip 回归佐证）。
  - 新增 `build_assembly_case_inp`：每部件 *MATERIAL/*SOLID SECTION
    （ELSET=PART1/PART2，材料经 `part_material` 引用）；连接对象 → 连接卡；
    载荷/边界复用模块 14 语义（红线不变）。
- 调度沿用 `solver/tasks.py`（case_NNN 先建后解、二进制捕获 decode
  errors=replace、成败='Job finished'+解析成功、退出码不作判据）。

## ccx 2.22 连接卡语法（实跑为准——本轮探针实证，勿凭文档记忆）

| 卡族 | 实跑结论 |
|---|---|
| *TIE | `*TIE, NAME=...`（NAME 必需，缺失报 "tie name is lacking"）；数据行 slave, master |
| *SURFACE | TYPE=ELEMENT 数据行 = 「**ELSET 名, S 面码**」——**S1..S4**（P 码被拒绝）；ELSET 必须已定义（裸单元号也被拒绝）；面号与 *DLOAD 的 P1..P4 同一编号体系（模块 7 实证），仅标签前缀不同 |
| 接触 | *SURFACE INTERACTION 内 **必须有 *SURFACE BEHAVIOR**（缺 → "no PRESSURE-OVERCLOSURE" fatal）+ *FRICTION μ；*CONTACT PAIR, TYPE=SURFACE TO SURFACE, INTERACTION=...；非线性步给 *STATIC 增量行（0.1, 1.0）——**增量行必须跟在 *STATIC 之后**（写成替换关键字 → "No analysis was selected" → ccx 段错误 0xC0000005，实证） |
| 螺栓 | ***KINEMATIC COUPLING 不被 ccx 2.22 识别**（calinput fatal，card image "CKINEMATICCOUPLING"）→ 按 ask「以实跑为准」用 ccx 原生等价 ***RIGID BODY,NSET=...,REF NODE=...** + 预紧力对（两侧参考节点 ±F_p·各自面外法向，自平衡 ΣF=0；预紧轴不能用心差——贴合面质心重合 → NaN 实证） |
| 共节点 | gmsh 4.15 API 名 = **removeDuplicateNodes**（removeAllDuplicates 不存在）；合并后 INP 必须经 **gmsh.write 导出**——手写 getElements 连接表会中节点错位 → nonpositive jacobian（实证）；共节点 deck 无连接卡，deck 校验两侧面确有共享节点 |

## 关键决策与证据

1. **按部件 face_map 消歧干涉面**：贴合部件的界面面 bbox 重合（模块 14 已知
   限制）——`_part_face_maps` 把提取域限定为「部件自己的 ELSET 单元子集 +
   部件自己的面清单」，界面三角只可能命中自己部件的面（两侧各 118 面实测）。
2. **ELSET 写单元号不是行下标**：`_surface_cards` 曾写行下标 → ccx 静默绑到
   错误单元、ΣRF 失衡（42/20/56）——修复后 ΣRFy=99.999999 N（物理门）。
   与模块 7「NSET 写节点 tag」同族的下标/编号坑。
3. **物理门**：含 *TIE 装配 -100N 经界面传力 → ΣRFy on PART1_FIXED
   = 99.999999 N（结果解析器复算）。
4. **红线①回归**：并发装配+零件任务同 work_root —— case_001/case_002 独立
   目录，装配 deck（PART1）与梁 deck（LOADFACE）互不串写，全套产物各自齐全。

## 验收对应（全部在本次实际运行通过，verify_assembly_solve.log）

| 验收 | 结果 |
|---|---|
| ① deck 结构 | PART1(1591)/PART2(1570) 不交且并集=Eall(3161)；节点 5709 无重复、单元引用全存在；MAT_p1(E=210000,ν=0.3,ρ=7.85e-9)/MAT_p2(E=68900,ν=0.33,ρ=2.7e-9) 与部件材料逐字段一致 + *SOLID SECTION 对应 |
| ② 连接卡门 | 含 *TIE deck：卡存在断言 + ccx 接受（Job finished）+ ΣRFy=99.999999 N |
| ③ 界面面集 | C1_SA/C1_SB 引用的 ELSET 全部已定义；面码 ∈ {S1..S4}；118+118 单元分属 p1/p2 界面区 |
| ④ 调度 | case_001 先建后解；.inp/.dat/.frd/.sta/.log 全套非空；parse_ok |
| ⑤ 负例 | *TIE 引用 NO_SUCH_SURF → failed、无 Job finished、错误含原因 |
| ⑥ 并发 | 装配+零件同 work_root 并发完成，独立目录无串写 |
| ⑦ 附加 | 接触/螺栓（*RIGID BODY+预紧对）/销（*TIE）/共节点（无卡）产品路径各真实 ccx 一跑全过 |

## 回归（本次实际运行，全部 exit 0）

verify_mesh_generation（mesher 新参数门）/ verify_load_definition /
verify_main_entry / verify_step_import / verify_assembly_import /
verify_assembly_tree / verify_connection_model / verify_static_solve（真实
ccx，build_case_inp 重构门）/ verify_report_archive（真实 ccx）/
verify_project_roundtrip（真实 ccx 三进程）。

## 未做 / 留给后续模块

- 装配工作流接入 `--batch`/GUI 求解菜单（当前入口为验收脚本直调 deck+调度）；
  re-resolve 后连接侧面的自动定位（`_resolve_conn_face` 已就绪，工作流编排属后续）。
- 接触收敛鲁棒性（当前 LINEAR 软刚度 + 固定增量在演示算例可解；复杂接触需
  自适应增量与接触阻尼调参）。
- 弹性连接（预留位）与详细螺接（杆实体/预紧过程）。
