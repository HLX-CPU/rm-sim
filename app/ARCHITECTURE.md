# rm_sim 架构文档

状态：随开发持续更新（当前覆盖：模块 1 统一主入口与主窗口框架；模块 2 STEP 导入与三维选面；
模块 12 装配 STEP 导入与部件身份；模块 13 装配树与部件管理；模块 14 装配级约束与载荷定义；
模块 15 连接数据模型与物理假设文档；模块 16 装配 INP 组装与求解调度；模块 17 粘接连接求解验证；
模块 18 可分离接触与摩擦连接求解验证；模块 19 螺栓与销轴简化连接求解验证；
模块20真实装配结果集成；模块21装配端到端；模块22装配保存重算；模块23单零件模态；
模块24 Chrono最小场景；模块25单时刻运动载荷传递）。
本文档是目录约定与分层规则的唯一权威来源；后续模块开工前先读这里。

## 2026-11-02 接续实现与覆盖范围

本节反映接续后的实际实现；下文旧模块的「后续」描述应结合本节及module20—25设计说明读取。

- 统一入口：根 `main.py` / `启动仿真软件.bat`；GUI与`--batch`均经公开工作流函数。
- 静力准备/装配回收：`solver/assembly_workflow.py::prepare_analysis/finish_analysis`，
  供GUI与装配batch共用；旧零件三进程工作流保持兼容。
- 工程身份：先匹配部件几何指纹，再在部件域内匹配约束/载荷/双侧连接面；
  缺失或歧义时拒绝求解。保存各部件材料数值快照与单体STEP副本。
- 选面节点集通过gmsh面实体归属提取，避免坐标重合时混入其他部件。
- `fields.field_surface`按部件全部节点裁剪，二阶中点仍参与局部极值；
  新GUI表单、真实求解回收、部件选择与局部标注有offscreen运行证据。
- 界面力为两侧节点外推应力的面积积分近似，GUI/报告均标明来源与离散误差。
- `solver/modal.py`单独解析频率与FRD振型，不复用静力DAT判据；当前只支持单零件无预应力。
  振型显示标为归一化，模式数据和原始结果保存在modal_cases。
- `solver/chrono_scene.py`由独立Python3.12/PyChrono10进程运行；
  `chrono_adapter.py`调用并记录源哈希、时刻和坐标变换。GUI的「运动」菜单提供入口。
- 主环境固定依赖见根`requirements.txt`；Chrono环境和安装校验证据位于E:/rm_sim，
  不改变主环境的numpy2.5.3。未实现跌落冲击应力/塑性/损伤/双向耦合。
- 原钢铝杆1×MeshSize排除范围的局部最大应力门未满足；远场门不替代它。
  另有假设材料/允许泊松收缩的全节点解析补丁验证，详见module20。

新增验收：`verify_assembly_example.py`、`verify_assembly_project_roundtrip.py`、
`verify_gui_assembly.py`、`verify_face_ownership.py`、`verify_assembly_patch.py`、
`verify_modal_analysis.py`、`verify_modal_view.py`、`verify_chrono_load_transfer.py`、
`verify_extensions_gui.py`。每次原始证据保存在E:/rm_sim/verification。
最终执行结果与失败修正轨迹见根`验证结果_2026-11-02.json`和`交付报告.md`。

## 3. 分层详述

## 3.1 cad 层：STEP 导入与选面（模块 2）

入口 `cad/step_import.py::import_step(path) -> SolidModel`，导入即三检
（调研报告 §4 第一步；内部单位体系固定 **N-mm-MPa-t-s**，几何一律 mm）：

1. **单位检查**：`cad/units.py` 解析 STEP 文本单位声明（`SI_UNIT(.MILLI.,.METRE.)`、
   `SI_UNIT($,$,.METRE.)`、`CONVERSION_BASED_UNIT('INCH',…)` 引用图），矛盾声明报错、
   缺失声明按 mm 并告警；非 mm 时日志显示「源单位=<名>（×因子 换算到 mm）」。
   OCC 读行为不硬编码：首次导入用临时米制标定件实测（1×1×1 导出→声明补正为
   METRE→重导入看边长 1000=读时换算 / 1=原样读入），实测本环境为
   `occ_converted_to_mm`；「原样读入」分支由导入器 `occ.dilate` 自行缩放。
2. **实体门**：无 dim=3 实体（壳/片体）或实体体积≤0 → `NotSolidError`
   「非实体，无法网格化：…」，不产生任何后续对象。
3. **清单**：`SolidInfo(tag/体积/bbox/质心)`、`FaceInfo(tag/面积/单位法向/
   面上中心点/bbox/所属实体)`；`SolidModel.pick_face(点, 容差)` 返回最近面
   （getClosestPoint 距离，默认容差 1e-6×对角线，超差 PickError），
   供约束/载荷挂接。选面tag与 probes/probe1_log.txt 一致（x=0→tag1、x=100→tag2）。

样例：`samples/beam_100x10x10.step`（`python -m rm_sim.cad.make_beam_sample` 重新生成，
PYTHONPATH 需含 app/）。验收：`verify/verify_step_import.py`。

### 3.1.1 装配导入与部件身份（模块 12）

`cad/assembly_import.py::import_assembly(path) -> AssemblyModel`，与
`import_step` 共用三检入口 `_import_model`（单位三检/NotSolidError/
非 mm 换算沿用；**位置原样保留**，不搬移不对齐）：

- **部件**：每个 (3,tag) 实体 = 一个部件；不 fuse、不 boolean。
- **部件身份 = 名称 + 指纹**：名称解析 STEP PRODUCT（单件 SDR 链 / OCC
  装配 CDSR+NAUO 链，失败回退 `Part<tag>`，仅供人读）；指纹
  `core/part_identity.PartFingerprint`（体积/质心/bbox 角点，face_identity
  同模式；匹配 rel_tol=1e-6 ×对角线，重导入门 1e-9）。
- **每部件表面清单**：`FaceInfo.solid_tag` 分组；face_owner 语义（固化，
  探针实证）：多实体共享面按实体 tag 递增序后写优先；OCC 读入不合并
  不同 B-rep 的重合面（样例均为独占归属 6+6 / 7+7+3）。
- 单实体 STEP → parts=1，`AssemblyModel` 是 `SolidModel` 子类（零件模块
  API 零破坏）；`import_step` 仍返回纯 `SolidModel`。
- 样例：`samples/assembly_two_blocks.step`（两叠块 20×20×20，质心差
  (0,20,0)）与 `samples/assembly_lap_pin.step`（搭接双板+通孔+销，3 部件，
  几何规格 v1 固化在 `cad/make_assembly_samples.py`，供「螺栓与销轴」
  功能引用）。验收：`verify/verify_assembly_import.py`。

## 3.2 solver 层：约束/载荷 → CalculiX 语义转换器（模块 5）

`solver/convert.py`（纯 numpy/stdlib；occ 面几何以 duck-typing 传入，
不 import cad 层——层间经入口编排）。调研报告 §7 语义红线在此落地：

| 载荷 | 转换产物 | 红线 |
|---|---|---|
| 压力 | `PressureDLoadRef(kind=element_face_set, entries=(单元索引,局部面索引))` | 必须是加载面所属**单元面集**的 *DLOAD P，禁止按质心面积折算节点 CLOAD 近似 |
| 面力 | `CloadPlan`（总力均分到面节点） | *CLOAD；等效面压=总力/面积仅作核对视图 |
| 力矩 | `MomentCouplePlan`（f_i∝轴×r_i） | 对轴矩精确守恒（ΣM·轴=M）；合力/非轴矩残差随结果报告 |
| 重力 | `gravity_vector = 单位方向×g` | *GRAV |

`build_face_element_map(points, tets, face_infos, distance_fn)`：体积网格
边界面（probe8 配方+局部面索引）按 occ 面 bbox 唯一归类（歧义用
`SolidModel.point_to_face_distance` 裁决）；`SolidModel.volume_mesh()` 提供
演示/自检用体积网格（正式网格模块交付后由其接管）。实际 .inp 文本写出
（中性面号→CalculiX P1..P4 映射）与求解级数值门属于求解模块。

### 3.2.1 装配级挂接（模块 14）

约束/四类载荷挂接目标 = **(part_id, 面指纹)**（`part_id=""`=旧版单零件全局
语义，逐字段向后兼容）；re-resolve（`project_workflow.reresolve_project`）
对 part_id 非空的挂接**只在该部件面清单内** resolve（`part_scoped_faces`，
每部件复用 core.face_identity.resolve_face）；指纹不匹配该部件任何面/部件
无法定位 → face_tag=0 + is_confirmed=False + 显式警告，绝不静默跨部件贴面。
Constraint.kind 新增 "bonded"（内部连接）：**不构成接地**——is_solvable 只数
外部固定（kind="fixed" 且已挂接），装配体至少一个外部约束；bonded 求解实现
（接触/绑定）属后续模块，deck 现阶段仅注释。deck NSET 选择泛化为按 face_tag
解析（`_nset_for_tag`：legacy FIXED/LOADFACE 文本不变，装配流部件前缀
FIXED_P1/LOAD_P2，无命中/冲突显式 DeckError）；压力单元面集名带部件前缀
`PRESS_{part}_FACE_{tag}`。重力写法 = **\*DLOAD 的 GRAV 标签**（ccx 2.22 无
*GRAV 独立关键字，convert/deck 文档与实现一致，模块 14 文本断言看门）。

### 3.2.2 连接数据模型（模块 15）

`core/connections.py`：ConnectionBase（name + **两部件面指纹对**
side_a/side_b = ConnectionSide(part_id, FaceFingerprint)）+ 四类连接——
BondedConnection（粘接）/ ContactFrictionConnection（可分离接触+Coulomb
μ∈[0,1] 默认 0.3）/ BoltConnection（简化螺栓：不建杆实体，预紧 F_p>0 +
耦合区半径，默认 8.8 级 0.7·As·σ0.2）/ PinConnection（销-板孔壁零间隙绑定，
IGNORED_EFFECTS=挤压/间隙/销弯曲柔度；销贯穿多板=每板一条、共用销侧引用）。
校验：同一部件自身/空 part_id/缺面指纹为固有拒绝；不存在 part_id 为工程
语境拒绝（validate_connection_in_project）。模板=参数化默认值
（CONNECTION_TEMPLATES，instantiate_template 可覆盖、非法值拒绝）；
`_RESERVED_KINDS=("elastic",)` 弹性连接类型位本期有意推迟（无实现）。
挂 `Project.connections`（默认空，旧工程缺字段兼容）；全部注册
store._REGISTRY。**物理假设权威文档 = app/docs/connections.md**（四节：
物理假设/适用范围/不模拟项/验证算例指针，验收 ③ 正则看门）；求解语义
转换（*TIE/接触/预紧/销绑定链）属后续模块。

### 3.2.3 装配 INP 组装与求解调度（模块 16）

`build_assembly_case_inp`（solver/deck.py）：装配整体在**一个 gmsh 会话**
内网格化（多实体节点号天然不重、免重映射；mesher 新增 `part_elsets` →
ELSET=PART1/PART2 与 `MeshResult.element_sets`，`remove_duplicate_nodes` →
共节点粘接模式，**gmsh 4.15 API=removeDuplicateNodes**）；每部件
*MATERIAL/*SOLID SECTION（part_material 引用）；连接卡语法 **以 ccx 2.22
实跑为准**（实证记录于 module_16 文档）：*TIE, NAME=（NAME 必需）+
*SURFACE TYPE=ELEMENT「ELSET 名, **S 面码**」（P 码被拒；面号与 *DLOAD
P1..P4 同一编号体系）；接触=*SURFACE INTERACTION+*FRICTION+*SURFACE
BEHAVIOR（必需）+*CONTACT PAIR+增量行（必须跟在 *STATIC 之后）；螺栓=
***KINEMATIC COUPLING 不被 ccx 2.22 识别 → 用 *RIGID BODY**+预紧力对
（各自面外法向 ±F_p，自平衡）。贴合面干涉消歧 = 按部件 face_map
（部件单元子集+部件面清单，`_part_face_maps`）。**ELSET 写单元号非行下标**
（错位 → ΣRF 失衡实证，与模块 7 NSET 写 tag 同族坑）。载荷/边界复用
模块 14 卡构建器（`_boundary_cards`/`_load_cards`，legacy 文本不变）；
调度沿用 solver/tasks.py（红线①②③不变）。物理门：-100N 经 *TIE →
ΣRFy=99.999999 N。

### 3.2.5 可分离接触与摩擦求解验证（模块 18）

五工况真实 ccx（两叠块，下块底面固定）：① 压缩 ΣRFy=1000N（≤0.1N）+
界面法向压力积分 Σ(−σyy)·A=999.99N±10% 且 ≥0；② 仅重力 ΣRFy=2ρV_mesh·g
（0.88%，V_mesh=角点四面体和）；③ 夹紧+侧向摩擦粘着 ΣRFx/ΣRFy 精确 +
u_x=4.09e-4≤1e-3；④ μ 判别对（300N）：μ=0.3 粘着 1.23e-3≤3e-3（切向罚
柔量随载荷线性标定）vs μ=0.1 **300s 不收敛（持续滑移）→ 超时取消**；
⑤ 可分离性（0.1mm 间隙几何+预定位移 *BOUNDARY 注入）：0.05mm 张开反力
0.0N、0.12mm 闭合 42423.76N≈k_series·0.02±10%。**LINEAR 罚刚度标定
1000→1e6 MPa/mm**（罚柔量吞反力实证，`_CONTACT_LINEAR_STIFFNESS_MPA_MM`）；
**frd_to_vtu 多增量支持**（非线性步按增量写 {base}.{inc}.vtu → 取终态）。
物理假设/门限权威文档 = connections.md §接触摩擦（只传压不传拉/库仑摩擦/
小滑动/罚刚度依赖；不模拟粘着磨损塑性压痕）。

### 3.2.6 螺栓与销轴简化连接求解验证（模块 19）

螺栓：耦合区刚体连接到参考点（**\*KINEMATIC COUPLING 不被 ccx 2.22 识别**
——两次探针实证 → *RIGID BODY）+ 预紧力对（自平衡 ΣRF_ground=0 实测）；
预紧+500N 上拉 → 接地反力 −500.0000N（0.01%），残余夹紧 1500N=平衡闭环
（FORC@从面含刚体 MPC 混合力——记录为非清洁量不设门）。销轴（评审补齐
几何：搭接双板 Ø8 孔 + 销 r=4）：孔壁面积 occ 解析 125.6637mm²±1%、绑定
面节点全部 r=4 圆柱面（防无孔埋入假实现）；500N 拉过销 ΣRF 0.01% +
板1 x=40 纯轴段 |Ux|=9.4959e-4（0.29%，1mm）；销缺失 → 连接性门。
**销绑定 tie 方向实证**：从面=销全长侧面（0 条投影失败）；从面=孔壁 →
gentiedmpc 全部失败（588 条实证）——方向不可反（deck.py 注释固化）。
假设/门限权威文档 = connections.md §螺栓/§销轴。

### 3.2.4 粘接连接求解验证（模块 17）

串联双杆算例（`samples/assembly_serial_rods.step`，杆钢 E=210000 + 杆铝
E=70000 串联，1000N）——*TIE（默认）与共节点（removeDuplicateNodes）双
实现的求解级物理验证：δ=0.01908529 vs 串联理论 0.0190476（0.198%）；ΣRF
偏差 ≤0.01%；**界面传力=从面应力积分 Σσxx·A**（ccx RF/FORC 不含 *TIE MPC
力——实证 ±5.4N 残差）；界面两侧轴向应力（SXX）差 1.38%（≤15%；Mises 受
从面约束剪切扰动 37.6%——首跑标定豁免，module_17 留档）；2mm vs 1mm δ 差
0.079%；判别力：杆2 误配为钢 δ 减半被门咬（per-part 材料证明）。**装配
连接性门**（build_assembly_case_inp）：部件拓扑断开（无外部固定/连接边）
→ DeckError 拒绝——ccx 对奇异体系仍 'Job finished' 但产出 Ux≈-8.6e10 mm
垃圾解（实证），deck 层拒绝更安全。物理假设权威文档 = connections.md §粘接
（无滑移无分离/胶层刚性/线弹性 + 门限与算例指针）。

## 3.3 mesh 层：自动网格与 INP 导出（模块 6）

`mesh/mesher.py::generate_tet10_mesh(geom, mesh_size_mm=2.0, nset_faces=None,
inp_path=None) -> MeshResult`（geom=cad.SolidModel，duck-typing 避免 mesh→cad
静态依赖）：

- **网格**：gmsh 3D 二阶四面体 C3D10；`MeshSizeMin=Max=mesh_size`（probe1
  均匀网格配方）+ `ElementOrder=2`；档位 `MESH_PRESETS` 粗 4 / 中 2（默认）/ 细 1。
- **INP 三坑规避**（config.json gmshInpExportNote，probe2 实证）：
  1. gmsh 原始输出（SaveAll=1）经 `_filter_mesh_inp` 只保留 *NODE 与
     *ELEMENT,TYPE=C3D10——T3D3/CPS6 丢弃（ccx zero-thickness 坑回归门）；
  2. 面 NSET **按节点坐标自建**（面平面距离 ≤1e-6 + 面 bbox，probe1 容差），
     不依赖物理组 ELSET；重建 `*ELSET,ELSET=Eall`；
  3. C3D10 节点序沿用 gmsh 自身 .inp 连接表（probe1→probe2→ccx beamCheck
     实证；**换单元类型/阶次/网格器必须重定基线**）。
- **质量统计**（进 `MeshStats.report_lines()` 与 `report/mesh_report.py::
  mesh_stats_section`）：负 Jacobian 单元数（`minDetJac<0`；本版 gmsh 4.15
  无 "minJacob" 名——实测确认）、min/mean SICN（`minSICN`）。
- 基线（标准梁 2mm）：6481 C3D10 / 11222 节点 / FIXED·LOADFACE 各 153 节点
  （probes/sets.json，±5）；换单元类型/阶次/网格器必须重定基线。
  **NSET 必须写 gmsh 节点 tag（getNodes 的 tags），不能写数组下标**——
  下标 0 起且与 tag 差 1，非法节点 id 0 令 ccx 内存破坏挂死（模块 7 实证）；
  模块 6 验收 ④ 已加「NSET 节点坐标落在挂接面上」的值校验防回归。

## 3.4 solver 层：静力求解与任务调度（模块 7）

- `solver/deck.py::build_case_inp(mesh_inp_text, study, load_case, geom,
  nset_face_tags)`：完整分析 .inp = 网格段（模块 6）+ *MATERIAL,*ELASTIC,
  *DENSITY,*SOLID SECTION,*BOUNDARY（理想固定）,*STEP,*STATIC,*CLOAD（面力
  set 均分 / 力矩逐节点力偶）,*DLOAD（压力逐单元面 P1..P4；**重力=GRAV 标签**
  ——ccx 2.22 实证无 *GRAV 关键字）,*NODE PRINT RF,U,*NODE FILE,*EL FILE,
  *END STEP。载荷经 solver.convert；nset_face_tags 校验载荷挂接面与网格
  NSET 一致（防静默挂错面）。
- `solver/results.py`：.dat（*NODE PRINT 块）/ .frd（DISP 定宽列）解析器
  （probe2 实证配方产品化）→ `SolveResults`（门计算的数据层输入）。
- `solver/tasks.py::SolveScheduler`：线程池调度（提交/运行/完成/失败/取消）；
  每任务 **case_NNN 目录先建后解**（红线①：ccx 按 cwd+作业名写 .dat/.frd/
  .sta/.cvg/.12d，共享 cwd 会互相覆盖）；ccx 子进程**二进制捕获**+
  decode("utf-8", errors="replace")（probe6：text=True 在 byte 0xa1 丢 stdout），
  读线程排空管道；取消 = taskkill 语义需杀进程树（launcher 的孙进程
  ccx_dynamic 握管道会令 terminate 失效——实测）；留存 输入/日志/退出状态/
  原始结果。**成败判定 = 日志含 "Job finished" 且结果解析成功**；退出码不作
  判据，两类证据分开标注（官方 ccx '-v' 恒 201 / PrePoMax 双态 0xFFFFFFFF）。
- 求解级数值门（verify_static_solve.py）：beamCheck 7 门（阈值取
  probes/beamCheck.json，实测 err=0.0117%、ΣRFy=99.999991、dat-frd 4e-7）；
  压力 ΣRFx=200N±0.02（**P1..P4 面码映射实证确认**）；重力 ΣRFy=ρVg=0.7676
  （0.32%）；力矩 |ΣMz|=5000.0000；变异注入在解析后、门前的数据层
  （Uy×2 证伪 err 门、RFy×0.5 证伪反力门——修正 probe2 先算门后注入的缺陷）。
- `solver/ccx2para.py`（结果辅解析，模块 8）：ccx2paraview .frd→.vtu
  （numpy 2.5.3 复数特征值 monkeypatch，probe3 配方；Converter 按基名写 CWD
  → chdir 守卫）；`solver/fields.py::build_result_fields(node_ids, results,
  frd_path) -> ResultFields`：**U/S_Mises/RF 三场**（行序=节点 tag 升序）；
  RF 全节点数组自由节点填 0（真值在 rf_table）。
- 结果显示表面 = **体积网格边界面**（mesh_data.boundary_faces_corners，
  角点三角）——与三场行同源对齐；cad 表面细分的节点与场行不同源，禁止混用
  （节点数 1285 vs 11222，模块 8 实测）。
- 结果渲染：`report/plot_aggs.render_field_png`（变形缩放 + 放大倍数/单位
  标注，probe8 配方；⑥ 同量位移模门：渲染标注 max|U| vs .frd 独立解析
  max|U| ≤1e-3mm，实测差 0）；GUI 求解菜单的 von Mises/位移云图/支反力表/
  极值面板（gui/result_dialogs.py）。
- `solver/archive.py::archive_case(project_dir, ..., tip_node)`（模块 9）：
  求解完成后把 results.json（全精度机器数据）与 Markdown 计算报告
  （模型/材料/网格统计含质量指标/工况/结果/反力平衡/超屈服判定/云图 PNG 嵌入/
  附录 A 全精度 JSON——与 results.json 同源同文）写入 **工程目录
  cases/case_NNN/**（调度 work_root 必须为 project_dir/cases，红线①一致）。
  正文数值打印位数固定（report.markdown_report.BODY_FORMATS），验收按
  |正文值 − results.json| ≤ 0.5×10^(−位数) 分层核对。GUI 求解菜单含
  「打开最近计算报告 / 打开最近产物目录」（os.startfile）。

## 4. 入口与退出码

`app/rm_sim/main.py`：

| 模式 | 命令 | 行为 | 退出码 |
|---|---|---|---|
| GUI | `python main.py` | 构建主窗口、显示、进入事件循环 | Qt exec 返回码 |
| 自检 | `python main.py --selfcheck` | 5 步无头自检（见下） | 0 = 通过；2 = 有失败项 |
| 冒烟 | `python main.py --smoke` | 主窗口显示完成后 QTimer 自动退出（offscreen 下视口走懒加载+Agg 降级；可重启验证 verify_relaunch.py） | 0 = 通过 |
| 帮助 | `python main.py -h` | 参数说明 | 0 |

`--selfcheck` 六步：① 依赖导入并打印版本（numpy 必须 = 2.5.3，硬门）；
② 读 config.json 并检查 ccxPath 文件存在；③ GUI 无关层静态扫描；
④ 主窗口构建（菜单/装配树/属性面板/状态栏/日志面板/视口宿主 pending）并打印
「窗口构建成功」；⑤ 视口创建/降级，打印 `渲染后端=<backend>`，并用 demo 立方体
网格驱动降级画布真实出图；⑥ 装配树数据模型（生成两叠块样例 → 2 部件演示
工程 → 打印 `装配树={...}` JSON，含部件/材料引用/可见性/几何指纹）。
任何一步失败都会把原因合并打印为 `selfcheck FAIL: …`（不抛 traceback）。

stdout 契约（供脚本断言）：包含字面量「窗口构建成功」与
「渲染后端=agg_fallback」（offscreen 环境）。管道输出强制 UTF-8
（`sys.stdout.reconfigure`），交互控制台保持系统编码。

## 5. 配置

- 来源：`E:/rm_sim/config.json`；环境变量 `RM_SIM_CONFIG` 可覆盖路径；
  `--config` 参数仅 selfcheck 使用。
- `rm_sim/config.py`：`load_config()` / `get_ccx_path()`，失败抛 `ConfigError`，
  不静默吞错。ccxPath 期望值：`D:/rm_sim/tools/ccx_launcher.exe`。
- 注意（config.json 内 ccxVersionCheckNote）：官方 CalculiX 对 `-v` 按设计
  exit 201，ccx_launcher.exe 已把「唯一参数为 -v」的 201 归一化为 0；
  `solver/calculix.py::probe_solver` 目前只做路径探测，不发起新进程。

## 6. 视口与渲染降级（GL 硬约束）

状态机：`PENDING →(ensure_viewport)→ GL("gl_native_offscreen") | AGG_FALLBACK("agg_fallback")`，
一次性决策、不回退重试。

- **懒加载**：`MainWindow` 构建期零 GL 资源；首次 `showEvent` 后经
  `QTimer.singleShot(0, ensure_viewport)` 创建，也可由菜单
  「视图→创建/刷新视口」或 `show_mesh()`/`show_geometry()` 显式触发。
- **try 周期**：`ViewportHost.ensure_viewport()` 用**同一个 try** 包住
  「工厂构造 → 控件安装 → 首帧渲染」。证据：`probes/probe4_log.txt` 显示
  offscreen 平台在 QtInteractor **构造期**即抛
  `RenderWindowUnavailable`；而"构造成功但首帧 paint 崩溃"只有首帧也在 try 内
  才能兜住（验收 ④ 的两处注入分别覆盖这两个相位）。
- **GL 配方**（`default_gl_factory`，`probes/probe4b_ui_native.py` 已验证，
  `verify/verify_viewport_gl.py` 常设回归门）：native windows 平台（不要设置
  `QT_QPA_PLATFORM=offscreen`）+ `QtInteractor.ren_win.SetOffScreenRendering(1)`；
  首帧 = `render()` + `screenshot(return_img=True)` 帧缓冲回读校验。
  pyvista/pyvistaqt 全部延迟到工厂函数内导入，导入失败同样走降级。
  **GL 部件必须入布局**（不能以 parent 判断，实测停 100×30）；离屏渲染窗口
  不自动跟随部件尺寸，`ViewportHost._sync_gl_size()` 在安装后/resize/投影/
  拾取/截图前显式 `ren_win.SetSize` 同步。
- **降级目标**（`probes/probe8_ui_headless_fallback.py` 已验证）：Agg 画布
  `AggFallbackCanvas` = 三角形/MeshData → matplotlib Agg PNG → QPixmap，
  零 GL / 零窗口系统 / 会话无关，无人值守批处理可用。
- **注入点**：`ViewportHost.set_factory()` 替换视口工厂，工厂契约
  `factory(parent) -> GLViewportHandle(widget, render_first_frame, close, plotter)`；
  验收脚本用它做构造期/首帧期故障注入。
- 交互会话（无 offscreen 环境变量）下若 GL 仍失败（如无桌面会话），同样降级
  Agg —— 与 config.json `uiHeadlessNote` 的部署约束一致。
- GL 后端的网格/几何上屏是 best-effort：失败只记日志，不推翻已建立视口。
- **按部件着色/隔离**（模块 13）：`ViewportHost.show_geometry_parts(model,
  visible_part_tags)`——三角形→部件按 FaceInfo.solid_tag，每点部件序标量
  （"part"）经 `show_geometry(point_data=…, tess=过滤后细分)` 复用同一
  GL/Agg 链路；`MainWindow.show_assembly(isolate_part_id=None)` 为产品入口
  （装配树双击部件=隔离、双击装配节点=恢复全部）。GL 行为回归门：
  verify_assembly_tree.py ⑦（截图存在 + std>5 + 两部件两色）。

### 选面交互（GL，与 cad 纯几何 pick 分工）

- 产品链路：`ViewportHost` 在 GL 部件上装 eventFilter（不消费事件，orbit 照常）
  → 左键按下 → `vtkCellPicker`（Qt 左上原点坐标需翻转成 VTK 左下原点）
  → 命中单元的 `cell_data["face_tag"]`（occ 面 tag）→ 红色高亮该面全部三角形
  → `face_picked` 信号（tag；未命中 emit(-1) 并清高亮）→ 主窗口日志/状态栏。
- 几何上屏：`cad.SolidModel.tessellate_faces()`（显示用 2D 细分，带 tri→occ面tag，
  提取后清网格不影响几何查询）→ GL `PolyData(cell_data["face_tag"], point_data 云图)`
  / Agg 三角形出图。`show_geometry(model, point_data={"von_mises": …}, scalar_name=…)`
  支持云图着色（色标为 vtkActor2D，在 GetActors2D 集合里）。
- 与 `cad.SolidModel.pick_face(点)`（纯几何 getClosestPoint API，模块 2）分开
  验证：前者 verify_viewport_gl.py（合成鼠标事件），后者 verify_step_import.py。

## 7. 数据模型与持久化（core 层）

- `core/store.py`：JSON 持久化，schema `rm_sim.project@1`；dataclass 树带
  `__type__` 标记双向转换；写盘 = 先写 `*.rm_tmp` 再 `os.replace` 原子替换。
  未知 `__type__` / schema 不符抛 `StoreError`。
- **装配部件（模块 13）**：`Part`（part_id/名称/source/solid_index/solid_tag
  运行缓存/PartFingerprint 指纹/material_name 材料引用/visible 可见性）挂
  `Project.parts`（单部件=1 条向后兼容）；旧版单部件工程（无 parts 键）在
  `load_project` 与 `open_bundle` 两条打开路径自动迁移为 1 条（幂等，其余
  字段不动）。材料引用：material_name 空=继承研究材料、非空=库名
  （`core/materials.py::part_material/set_part_material`，per-part 隔离）。
  装配树 JSON=`core/project.py::assembly_tree_payload`。
- GUI「文件」菜单的新建/打开/保存已接通该层；工况树已扩展为装配树
  （部件/材料/可见性，双击隔离/恢复，`gui/panels.py`）。

## 8. 后续模块扩展点

| 模块 | 挂接点 |
|---|---|
| STEP 导入与选面 | ✅ 已实现：`cad/`（import_step 三检 + pick_face + tessellate_faces）；GUI「文件→导入 STEP…」 |
| 装配导入与部件身份 | ✅ 已实现：`cad/assembly_import.py`（import_assembly 共用三检入口 + 部件身份=PRODUCT名+几何指纹 `core/part_identity.py` + 每部件表面清单）；样例 two_blocks/lap_pin |
| 装配树与部件管理 | ✅ 已实现：`core/project.py`（Part/Project.parts/旧版迁移/assembly_tree_payload）+ `core/store.py` 注册 + `core/materials.py`（part_material/set_part_material per-part 隔离）+ `cad/assembly_import.py::sync_parts_from_model` + `assembly_demo.py`（顶层 GUI 无关，已登记 layer_guard）+ GUI 装配树/按部件着色隔离（`panels.py`/`viewport.py`/`main_window.py`）+ selfcheck [6/6] |
| 装配级约束与载荷 | ✅ 已实现：挂接目标 (part_id, 面指纹)（core/project.py + `project_workflow.part_scoped_faces` 装配感知 re-resolve）+ bonded 内部连接不接地 + is_solvable 外部约束语义 + deck 按部件 NSET/压力集合名前缀（convert.py/deck.py；docstring *DLOAD GRAV 措辞已修正）|
| 连接数据模型 | ✅ 已实现：`core/connections.py`（ConnectionBase 面指纹对 + 粘接/接触摩擦/螺栓/销轴四类 + 模板默认值/覆盖 + 校验拒绝自身/未知部件）+ Project.connections + store 注册；物理假设权威文档 `app/docs/connections.md`（四节看门）；弹性连接类型位预留推迟 |
| 装配 INP 组装与求解调度 | ✅ 已实现：`build_assembly_case_inp`（PARTn ELSET/每部件材料截面/连接卡——语法 ccx 2.22 实跑实证，module_16 文档）+ mesher `part_elsets`/`remove_duplicate_nodes`（共节点）+ 调度沿用 tasks.py；物理门 ΣRFy=99.999999 N 经 *TIE |
| 粘接连接求解验证 | ✅ 已实现：串联双杆算例（`samples/assembly_serial_rods.step`）双实现（*TIE/共节点）δ 门 0.198%、ΣRF ≤0.01%、界面传力（从面应力积分）1000.1N、SXX 差 1.38%、网格 0.079%、误配判别、断带 → 装配连接性门（DeckError）；假设/门限文档 connections.md §粘接 |
| 可分离接触与摩擦求解验证 | ✅ 已实现：五工况真实 ccx（压缩/重力/摩擦粘着/μ 判别对/可分离性 0.1mm 间隙预定位移）+ μ=0 对照；LINEAR 罚刚度标定 1e6（deck.py）+ frd_to_vtu 多增量终态；假设/门限文档 connections.md §接触摩擦 |
| 螺栓与销轴简化连接求解验证 | ✅ 已实现：螺栓（耦合区刚体连接+预紧力对——*KINEMATIC COUPLING 不被 ccx 2.22 识别实证→*RIGID BODY；自平衡/残余夹紧门）+ 销轴（评审补齐几何：搭接双板 Ø8 孔+销 r=4 零间隙 *TIE——孔壁面积/绑定面圆柱断言/ΣRF/Ux@40 门）+ 销缺失连接性门负例；假设/门限文档 connections.md §螺栓/§销轴 |
| 材料库与零件材料设置 | ✅ 已实现：`core/materials.py`（内置 8 种 + 自定义 materials.json + 校验）；GUI「材料→选择/管理材料…」 |
| 约束与载荷定义 | ✅ 已实现：`core/project.py` 多态模型 + `solver/convert.py` 语义转换器（压力=单元面集红线）；GUI 工况树展示；面挂接 UI 属选面交互后续完善 |
| 自动网格与 INP 导出 | ✅ 已实现：`mesh/mesher.py`（C3D10/三坑规避/质量统计）+ GUI「网格→生成网格并导出 INP…」；基线 6481/11222/153±5 |
| 三维显示/选面交互 | ✅ 已实现：`show_geometry` 云图 + 左键拾取高亮（verify_viewport_gl.py 回归门） |
| 自动网格 | `mesh/`（gmsh/meshio → MeshData）；`Study.mesh_file` |
| 静力求解与任务调度 | ✅ 已实现：`solver/deck.py+tasks.py+results.py`（完整 .inp + 调度 + 解析 + beamCheck 7 门/压力/重力/力矩门）；GUI「求解→运行静力分析…」 |
| 结果云图与数值查看 | ✅ 已实现：`solver/ccx2para.py+fields.py`（U/S_Mises/RF 三场）+ `render_field_png` 标注出图 + 求解菜单云图/支反力表/极值面板 |
| 工程保存修改工况重算闭环 | ✅ 已实现：`.rmsimproject` 束（`core/project_bundle.py`）+ 面指纹（`core/face_identity.py`）+ 工作流编排（`solver/project_workflow.py`）+ 批处理入口（`main.py --batch` + `rm_sim/batch.py`）；GUI 文件菜单束保存/打开/另存 |
| 计算报告 | `report/` 扩展（Agg 渲染已就绪） |
| 工程闭环 | `core/` 实体扩展 + `verify/verify_project_roundtrip.py` |
| 启动冒烟 | ✅ 已实现：`verify/verify_relaunch.py`（offscreen 两次启动 + 可重启证明 + 心跳冒烟约定） |

新增实体务必在 `core/store.py::_REGISTRY` 注册；新增 GUI 无关层顶层文件务必
登记 `layer_guard.GUI_FREE_ROOT_FILES`。

## 9. 验证体系

```bat
:: 验收：统一主入口与主窗口框架（exit 0 = 通过；日志 → verify\logs\verify_main_entry.log）
QT_QPA_PLATFORM=offscreen E:\rm_sim\venv\Scripts\python.exe verify\verify_main_entry.py

:: 验收：STEP 导入与三维选面（无 GUI 进程；日志 → verify\logs\verify_step_import.log）
E:\rm_sim\venv\Scripts\python.exe verify\verify_step_import.py

:: 验收：装配 STEP 导入与部件身份（无 GUI 进程；日志 → verify\logs\verify_assembly_import.log）
E:\rm_sim\venv\Scripts\python.exe verify\verify_assembly_import.py

:: 验收：装配树与部件管理（core 步骤无 GUI；交互段 GL 就绪才执行，否则按约定 SKIPPED；日志 → verify\logs\verify_assembly_tree.log）
E:\rm_sim\venv\Scripts\python.exe verify\verify_assembly_tree.py

:: 验收：装配级约束与载荷定义（无 GUI 进程；日志 → verify\logs\verify_assembly_loads.log）
E:\rm_sim\venv\Scripts\python.exe verify\verify_assembly_loads.py

:: 验收：连接数据模型与物理假设文档（无 GUI 进程；日志 → verify\logs\verify_connection_model.log）
E:\rm_sim\venv\Scripts\python.exe verify\verify_connection_model.py

:: 验收：装配 INP 组装与求解调度（真实 ccx；无 GUI 进程；日志 → verify\logs\verify_assembly_solve.log）
E:\rm_sim\venv\Scripts\python.exe verify\verify_assembly_solve.py

:: 验收：粘接连接求解验证（串联双杆真实 ccx；无 GUI 进程；日志 → verify\logs\verify_bonded_connection.log）
E:\rm_sim\venv\Scripts\python.exe verify\verify_bonded_connection.py

:: 验收：可分离接触与摩擦连接求解验证（五工况真实 ccx；无 GUI 进程；日志 → verify\logs\verify_contact_friction_connection.log）
E:\rm_sim\venv\Scripts\python.exe verify\verify_contact_friction_connection.py

:: 验收：螺栓与销轴简化连接求解验证（真实 ccx；无 GUI 进程；日志 → verify\logs\verify_bolt_pin_connection.log）
E:\rm_sim\venv\Scripts\python.exe verify\verify_bolt_pin_connection.py

:: 验收：视口上屏状态机与中文标题（GL 需桌面会话；日志 → verify\logs\verify_viewport_state.log；
:: 截图证据 → E:\rm_sim\verification\viewport_state_*；--selfcheck 仅跑 main.py --selfcheck 复跑门）
E:\rm_sim\venv\Scripts\python.exe verify\verify_viewport_state.py

:: 验收：点面交互核心（选中面卡片/悬停反馈/误点撤销；GL 需桌面会话；
:: 日志 → verify\logs\verify_face_pick_card.log；截图 → E:\rm_sim\verification\face_pick_card_*）
E:\rm_sim\venv\Scripts\python.exe verify\verify_face_pick_card.py

:: 验收：点面直连工况表单（非模态停靠面板 + A/B 点面回填 + B 面守卫 + 指纹一致；
:: 含三绿色门 subprocess 复跑；日志 → verify\logs\verify_face_pick_to_form.log；
:: 截图 → E:\rm_sim\verification\face_pick_to_form_*）
E:\rm_sim\venv\Scripts\python.exe verify\verify_face_pick_to_form.py

:: 验收：结果云图点面溯源（真实 ccx 求解 two_blocks + NSET 归属独立复算 + 局部极值；
:: GL 需桌面会话；日志 → verify\logs\verify_result_field_face_pick.log；
:: 截图 → E:\rm_sim\verification\result_field_face_pick_*）
E:\rm_sim\venv\Scripts\python.exe verify\verify_result_field_face_pick.py

:: 验收：工作流导航与首启引导（7 步流程条 + 占位页三入口 + 示例工程 + --smoke 复跑；
:: offscreen 含真实求解；日志 → verify\logs\verify_workflow_navigation.log；
:: 截图 → E:\rm_sim\verification\workflow_navigation_*）
E:\rm_sim\venv\Scripts\python.exe verify\verify_workflow_navigation.py

:: 验收：现代视觉主题与中文化收口（QSS accent 像素 + 对话框按钮中文 + 部件名
:: 兜底/F2 重命名束往返 + 结果菜单 + --selfcheck 复跑；
:: 日志 → verify\logs\verify_theme_and_naming.log；截图 → E:\rm_sim\verification\theme_and_naming_*）
E:\rm_sim\venv\Scripts\python.exe verify\verify_theme_and_naming.py

:: 验收：求解期间可用性与结果仪表卡片（busy 分类 + 真实取消 + 仪表卡片红线/同源/
:: 安全系数 + lifecycle 门修订语义复跑；offscreen 含真实求解；
:: 日志 → verify\logs\verify_busy_state_and_dashboard.log；
:: 截图 → E:\rm_sim\verification\busy_state_and_dashboard_*）
E:\rm_sim\venv\Scripts\python.exe verify\verify_busy_state_and_dashboard.py

:: 验收：高级分析工作台可理解化（禁进程模式 + validate 状态列 + 类型分组 +
:: 命名工况下拉 + 诚实求解按钮；offscreen；日志 → verify\logs\verify_workbench_ux.log；
:: 截图 → E:\rm_sim\verification\workbench_ux_*）
E:\rm_sim\venv\Scripts\python.exe verify\verify_workbench_ux.py
```

verify_main_entry 覆盖：① selfcheck 子进程（exit 0 + 标记串）；② 版本导入 +
numpy=2.5.3 硬门；③ config/ccxPath；④ 视口构造期/首帧期故障注入双降级；
⑤ GUI 无关层毒化子进程；⑥ GUI 无关层静态扫描。日志不得含 traceback（脚本内
兜底过滤）。

verify_step_import 覆盖：① mm 标准梁 bbox/体积/实体数/表面清单；② 米制 STEP
负例（源单位=m + 换算后 100×10×10 + 日志「源单位=m」）；③ 壳体负例
（非实体，无法网格化）；④ pick 两端面（面积/法向/tag）+ 内部点容差拒绝；
⑤ 坏文件（垃圾文本 + 截断 STEP）含原因报错；⑥ 全程无 PySide6 进程 + 静态扫描。

verify_material_library 覆盖：① 内置库 8 种 + 关键值文献门（结构钢 ±2%、
6061/7075/TC4 ±3%）；② 自定义库增删改 round-trip 逐字段相等（临时文件）+
同名遮蔽内置 + 重名拒绝；③ 非法输入 10 例（负 E、ν≥0.5、ν≤0 等）校验拒绝、
库文件字节级不变；④ 标准梁选 45 钢 → solver_material_dict 与
probes/beamCheck.json parameters 逐字段一致（含工程保存/重开往返）；
⑤ 全程无 PySide6 进程 + 静态扫描。

verify_load_definition 覆盖：① 四类载荷 JSON 序列化/工程往返逐字段相等；
② 面力 100N/100mm² → 等效面压 1.0MPa（1e-9）+ 总力均分 CLOAD（ΣF 精确）；
③ 压力 2MPa → 单元面集引用 + 等效合力 200N 沿面法向；④ 力矩 5000N·mm 绕 z →
等效节点力偶 ΣMz=5000（1e-6，非轴矩/合力残差如实报告）；⑤ 重力向量；
⑥ is_solvable 全场景 + 9 例校验负例；⑦ 固定面 tag 反查面几何；
⑧ 语义红线（引用 kind=element_face_set、单元面数>0、无节点力字段、
*GRAV 向量、全 occ 面覆盖）。

verify_report_archive 覆盖（真实求解两次）：① case 目录含 job.inp/.dat/.frd/
日志（Job finished）/results.json；② job.inp 结构（*STATIC/*ELASTIC E=210000/
*CLOAD 总力 100N 1e-9/*BOUNDARY/*NSET,NSET=FIXED）；③ 分层数值核对（附录与
results.json 同文 + 关键值 1e-9 门；正文 7 项按打印位数核对）；④ 报告内容
（45钢/100N -y/网格统计与网格功能一致/min SICN/负 Jacobian/反力平衡/超屈服
判定/云图嵌入）；⑤ case_001/case_002 递增且 case_001 文件哈希不变；
GUI 无关 + 静态扫描。

verify_project_roundtrip 覆盖（本模块收尾门，A/B/C 三进程全程 subprocess 经
main.py --batch 无 GUI API，不经内存对象）：① 进程A 新建→导入→45钢→固定/
载荷面→网格 2mm→求解→保存束；② 进程B 重开逐字段相等（100N/45钢/MeshSize
2.0）+ 面指纹 re-resolve 命中面与保存值 1e-6 一致（面积/形心/法向）；③ 进程B
改 200N 重算 → case_002 新建、Uy≈2×旧值且对 δ=0.3809524 ≤2%、ΣRFy=200±0.02、
旧 case 产物仍在；④ 进程C 再重开当前工况 200N + 历史两 case 可读；⑤ 指纹篡改
（面积 +5%）→ is_confirmed=False + 警告非空 + 拒绝求解；⑥ 三进程 exit 0。

verify_relaunch 覆盖（整机启动冒烟与可重启，11 项 verify 中的收尾门）：
QT_QPA_PLATFORM=offscreen 连续启动 main.py --smoke 两次，每次 120s 内 exit 0、
输出含「主窗口已显示」「渲染后端=agg_fallback」（offscreen 懒加载+降级路径，
崩在 createPlatformOpenGLContext 即判主窗口不合规——冒烟约定不可改）、无未捕获
traceback、ccxPath 已加载；第二次冒烟时间戳晚于第一次（可重启证明）。

verify_results_view 覆盖（真实求解产物）：① U/S_Mises/RF 三场齐全且节点
数=网格节点数（11222±5）；② max_mises 与 vtu S_Mises 峰值一致（1e-6）+ 梁
理论 ≤10%（实测 61.905 MPa，3.18%）；③ 端部 Uy vs .dat ≤1e-4mm（4e-7）；
④ 支反力表 ΣFy=100±0.01N 且逐节点复算一致；⑤ offscreen 标注出图（PNG 存在、
std>5、含放大倍数与单位标注）；⑥ 同量位移模门：渲染标注 max|U| vs .frd
独立解析 max|U| ≤1e-3mm（实测差 0）；GUI 无关 + 静态扫描。

verify_static_solve 覆盖：①-⑤ beamCheck 7 门（真实 ccx 2.22 运行，阈值取
probes/beamCheck.json）；⑥ 变异注入（解析后、门前数据层：Uy×2 / RFy×0.5 各证伪
对应门）；⑦ 压力 ΣRFx=200N±0.02（*DLOAD P 单元面集结构断言）；⑧ 重力
ΣRFy=ρVg±1%（*DLOAD GRAV 标签——ccx 2.22 无 *GRAV 关键字，实证）；⑨ 力矩
固定端 |ΣMz|=5000±1%；⑩ 负例删 *STEP → failed；⑪ 并发 2 任务独立 case 目录
+ 200ms 心跳；红线①②与退出码证据标注。

verify_mesh_generation 覆盖（标准梁 2mm，probe1/probe2 基线）：① .inp 仅
C3D10（T3D3/CPS6/C3D4 坑回归门）+ ELSET=Eall + NSET 存在；② 单元 6481∈
[5000,9000]、节点 11222≥9000；③ 节点 bbox 100×10×10（1e-6）；④ FIXED/
LOADFACE NSET 各 153 节点（probes/sets.json ±5；换元/换阶/换网格器须重定）；
⑤ 4mm 单元数 < 0.5×2mm + 档位定义；⑥ 统计与 .inp 实际一致（含 10 节点连接表）；
⑦ 质量门（负 Jacobian=0、min SICN≥0.1，2mm/4mm 双档）；⑧ 无 GUI 进程 +
静态扫描。

verify_viewport_gl 覆盖（**11 个 verify 中唯一允许 skip 的**）：GL 常设回归门——
native windows + SetOffScreenRendering(1) 配方（评审 blocker5）。有桌面会话：
① GL 截图 PNG 存在/std>5/含云图色标；② Qt Test 合成鼠标点击 x=100 端面投影
位置 → occ tag=2 + 高亮激活（与 cad.pick_face 分开验证）；③ 日志行
「渲染后端=gl_native_offscreen」。无桌面/GL 失败：exit 0 + 摘要
「SKIPPED: 无交互桌面会话」+ 日志写明原因（skip 必须打印而非静默）；
**GL 路径变更后须在有桌面机器上重跑**。人工验收清单见
app/docs/module_03_viewport_gl.md。

## 10. 环境与版本基线

- Python：`E:/rm_sim/venv`（3.13.3）；新依赖装入该 venv，
  `pip --cache-dir E:/rm_sim/pipcache`。
- 版本（2026-11-02 实测）：PySide6 6.12.0 / pyvista 0.49.2 / pyvistaqt 0.13.1 /
  vtk 9.7.1 / gmsh 4.15.2 / meshio 5.3.5 / **numpy 2.5.3（验收硬门）** /
  matplotlib 3.11.2。
- 已知平台事实（probes/ 基线 + 模块 2 实测，勿凭记忆推翻）：
  - Qt offscreen 平台无 `createPlatformOpenGLContext`（probe4_log.txt）；
  - GL 交互配方必须 native 平台 + `SetOffScreenRendering(1)`（probe4b）；
  - 全部 GL 路径需要交互桌面会话，真会话 0 未验证（config uiHeadlessNote）；
  - Agg 出图会话无关（probe8）；
  - gmsh 4.15.2 STEP round-trip bbox 精确（probe1_log.txt）；
  - gmsh STEP 导出固定写 `SI_UNIT(.MILLI.,.METRE.)`；OCC 读入自动把文件单位
    换算到 mm（模块 2 标定实测，运行时仍会重新标定，不依赖该结论）；
  - `gmsh.model.getClosestPoint` 返回 2 元组（坐标+参数坐标），不含距离（4.15 实测）。

## 后续静力后台流程（模块29，2026-11-02）

主窗口通过 `solver/workflow.py` 提交静力任务，`workflow_job.py` 在独立进程准备CAD/网格和整理结果，
CCX使用独立进程树；重阶段顺序执行。跨进程使用数值包和工程数据模型，不传递Gmsh/Qt对象。
`prepared_store.py`保存准备数据，`display_store.py`保存已验证结果的显示数组；GUI不重新解析整个INP/FRD。
固定/加载面使用指纹，材料冻结数值，源STEP和各case的几何快照核对哈希。

任务、事件、日志和取消由GUI无关层管理；窗口只轮询状态和展示数组。`solved_unparsed`是内部中间终态，
不能当作完整分析完成。网格预览不要求受力定义，正式求解仍有材料/约束/质量检查。
详情、边界和验证脚本见 `docs/module_29_background_static_workflow.md`。其他分析入口及全部窗口操作的后台化尚未完成。

## 绑定预处理（模块30，2026-11-02）

新绑定显式ADJUST=NO；旧项目缺字段迁移为旧行为YES。自动草稿统一同一部件对的从件，
deck按主从件和调整策略合并所有网格面，保留每连接的原始面集供诊断。完整日志ERROR扫描
采用有界内存，即使求解器输出结束标记也拒绝含ERROR的结果。详情见 `docs/module_30_tie_preprocessing.md`。

## 绑定带（模块31，2026-11-02）

`*TIE` 用整张 CAD 面时，求解器默认搜索半径（实测 0.004024 mm）小于两套独立网格的
曲面离散差，且 CAD 面远大于真实贴合区——整装实测 3496 个未绑定警告。`solver/bond_band.py`
按实测几何挑**从面节点**：只有投影落在主面内、法向距离≤带宽的节点写进
`*SURFACE, TYPE=NODE`，`*TIE` 的位置容差逐组按实测间距给出（上界 0.07；共面贴合不写）、
`ADJUST=NO` 不移动节点；
主面保留整张 CAD 面的单元面，同一部件对的补丁合并成一个 TIE（节点并集+单元面并集）。
逐连接在 INP 里记录从面总节点/可绑节点/贴合区外节点数与距离。整装实测：39 条连接
17194 个从面节点中 12437 个（72.3%）参与绑定，未绑定警告 3496→28（0.27%，逐连接可归因）。
详情见 `docs/module_31_bond_band.md`；整装求解复核见
`analysis/整装真实求解与网格稳定性_2026-11-02.md`。

## 回归运行时收尾（模块33，2026-11-02）

求解器运行监控改用实际Job Object内核限额，防止分配被拒后仍停滞；状态区分主动
内存保护停止与接近限额的异常退出。串行粘接验证不预留第二并发预算，case持久保留。
Qt绑定策略验证显式清理对话框、模型和应用，再由正常退出码验收。
见 `docs/module_33_runtime_regression.md`；完整软件仍未验收完成。

## 反力口径（模块34，2026-11-02）

`solver/nodal_loads.py`只根据当次最终INP和网格重建一致节点载荷，密度不取当前可变工程。
`fields.py`保留DAT原始RF，扣除约束节点已有的CLOAD/GRAV/压力载荷后提供支反力。
单步小变形静力以外的范围明确标为未完成修正；不据此生成平衡结论。
归档和后台显示包携带两组数值、版本和输入哈希。历史加载先校验原摘要，旧工况仅在内存
重算，原文件不改写；历史列表标注归档口径。细节见 `docs/module_34_reaction_semantics.md`。

## 局部网格和质量（模块35，2026-11-02）

Study保存MeshControl，cad/mesh_targets按所属部件内的面/边指纹匹配；mesh/refinement
建立尺寸场。mesher保存质量分布和最差单元，准备包及case报告传递记录，GUI展示数值。
失败后手动重试建立新任务；自动重试先归档旧快照及日志，释放独占创建路径。
边界和验证见 `docs/module_35_local_mesh_quality.md`。

## 视口上屏状态机与中文标题（模块36，2026-11-03）

评审 high-1/high-2 修复（「修复：视口结果视图残留叠加」）。GL 后端三条上屏路径
（几何 `_show_geometry_gl` / 网格 `_show_mesh_gl` / 云图 `_show_field_gl`）统一走
「先清后上」状态机：每轮先移除上一轮标题文本/色标/主体 mesh actor
（句柄保存于 `_text_actor`/`_scalar_bar_actor`/`_mesh_actor`，另有 GetActors2D
残留清扫兜底），连续换视图不再叠加。注意：VTK 9.7 中 vtkCornerAnnotation
`IsA("vtkTextActor")=False`（不是其子类，实测），计数/清扫必须两类都认；
2D actor 盘点接口 = `ViewportHost.scene_2d_actor_state()`（口径同
`scalar_bar_visible` 的 GetActors2D 遍历）。

中文标题（评审 high-1）：`add_text(font_file=CJK_FONT_FILE)` 指向
C:/Windows/Fonts/msyh.ttc，且必须先调 `_ensure_vtk_freetype_backend()`——
实测 VTK 9.7.1 默认文本后端（GetDefaultBackend()=0，不在 -1/1/2 枚举内）
**忽略 SetFontFile**（设了字体渲染像素逐位不变、CJK 照打 glyph 告警），
把 vtkTextRenderer 默认后端强制为 FreeType(1) 后字体文件才真正生效
（probe_font*/probe_backend* 实测：msyh/simhei/arial 三种位图互异、0 条告警；
RenderString(FreeType) 下 msyh.ttc「云图」66×35 非空字形 vs 默认 2×32 空白）。
色标标题/标签同步挂字体（属性 getter 实测名为 GetTitleTextProperty/
GetLabelTextProperty/GetAnnotationTextProperty，GetTitleProperty 不存在）。
云图色标 label：『von Mises [MPa]』『|U| [mm]』『振型幅值 [归一化]』。

验收：`verify/verify_viewport_state.py`——GL 连续上屏 几何→网格→Mises云图→
位移云图 4 次，独立遍历 GetActors2D 计数（不依赖被测实现，另交叉核对）：
每步文本 actor ≤1、终态色标恰 1 且标题含单位、上一张标题串不复现；
截图 4 张落 E:/rm_sim/verification/viewport_state_* 供人工目检；
末尾复跑 main.py --selfcheck（offscreen）门 exit 0 + 「窗口构建成功」；
`--selfcheck` 参数仅跑该快门。GL 未就绪 = FAIL（本门对象是状态机本身；
与 verify_viewport_gl 同理不进 offscreen 默认回归）。

## 点面交互核心（模块37，2026-11-03）

把『点面』从 tag 数字升级为可见语义（选中面卡片 / 悬停反馈 / 误点撤销）。

- **悬停反馈**：`ViewportHost.eventFilter` 增加 MouseMove 分支——节流
  （时间 <60ms 且位移 <3px 跳过；拖拽 `buttons()!=NoButton` 不拾取）调用
  vtkCellPicker，命中面**变化**时 emit `hover_face_changed(tag/-1)` →
  `MainWindow._on_face_hover` → QToolTip（部件名/面号/面积 mm²/法向），
  文本存档 `MainWindow.last_face_tooltip`（验收断言口）。
- **选中面卡片**：`panels.FaceCardPanel` 挂 `PropertiesPanel` 顶部——
  部件名（`_face_part_name`：装配按 FaceInfo.solid_tag↔Part.solid_tag 匹配，
  单件/无部件数据回退『部件N』）、面积 mm²、法向、已挂定义数
  （`_face_definition_counts`：约束/载荷按 face_tag 跨全部工况遍历 +
  连接按面指纹 `face_matches_fingerprint` 任一侧匹配）；原始数值存
  `face_card.last_payload`（验收与 face_by_tag 逐值对照）。
- **误点撤销**：`MainWindow._face_pick_history` 栈——每次成功点中的面按序
  入栈（连续重复去重），**点空白只清高亮/卡片不清栈**；『撤销选面』按钮 +
  Ctrl+Z 快捷键（`_build_face_interactions`）同走 `undo_face_pick()`：
  弹出栈顶 `viewport.highlight_face` 重新高亮并刷卡片，空栈状态栏提示。
  验收序列 点7→点2→撤销→恢复2（栈顶=最近点中面）即该语义。
- **Agg 降级**：事件过滤同时装在 AggFallbackCanvas（画布+内嵌 label）——
  点击尝试 emit `agg_pick_attempted` → 状态栏『选面需三维视口（GL）』；
  `ensure_viewport` 降级分支状态栏消息含同名提示。

验收：`verify/verify_face_pick_card.py`（Qt Test 合成鼠标，配方同
verify_viewport_gl）：① MouseMove→QToolTip 含部件名与『mm²』；② 点击
tag=2→卡片面积/法向与 face_by_tag(2) 逐值一致（|diff|≤1e-6 相对）+ 已挂
定义数 1/1/1；③ 点 tag=7→点 tag=2→撤销→highlight_face_tag==2 且可见
（梁样例仅 6 面无 tag=7，用 samples/assembly_two_blocks）；④ 点空白→卡片
未选中（并复演 误点空白→Ctrl+Z 恢复）；⑤ set_factory 抛异常注入→agg_fallback
+ 状态栏降级提示 + Agg 点击提示；⑥①-④每门断言 backend==gl_native_offscreen，
截图（win.grab）落 E:/rm_sim/verification/face_pick_card_*。GL 未就绪 = FAIL。

## 点面直连工况表单（模块38，2026-11-03）

视口选面直接写入约束/载荷/连接目标，定义列表仅作汇总确认（双击回填编辑后延）。

- **路线决策（先探针后择路）**：Qt Test 合成点击在模态对话框打开时仍可直达
  视口（synthetic 直投不经模态过滤，probe_modal 实测 captured=[2]），但**真实
  用户输入被 ApplicationModal 阻断**（Qt 语义）——故选首选路线：非模态停靠面板；
  模态包装保留给候选面流程。
- **结构**：`setup_dialog.SetupFormWidget`（QWidget，全部控件与逻辑）+
  `SetupDialog(QDialog)` 薄包装（`__getattr__` 转发表单）。**红线 B3 兼容**：
  构造器签名 `(project, model, selected_tag=None, parent=None)`、
  face_a/face_b 的 itemData=(part_id,tag)、kind/mu/material/value/
  traction_distribution、exec()/changed 语义全部保留——三个绿色门按原语义
  复跑 PASS：verify_gui_assembly（表单全链路+真实求解）、
  verify_consistent_traction（traction_distribution 默认值）、
  verify_geometry_inspection_gui（`SetupDialog.exec` 类级 monkeypatch——
  `_edit_candidate_faces` 仍走模态 exec）。
- **主窗口入口**（`_on_edit_setup`）改为非模态 `QDockWidget("工况设置
  （点三维视口选面）")`（与属性面板 tabify）：`viewport.face_picked →
  form.on_viewport_face_picked`（第一次点→A、第二次点→B，之后持续更新 B；
  `_next_pick_target` 游标，选中面预填时从 B 起）；下拉
  currentIndexChanged → `viewport.highlight_face` 反向高亮 3D；面板关闭
  （Close 事件经 MainWindow.eventFilter 检测——visibilityChanged 会被 tab
  切换误触发）→ 断开接线，form.changed 则 invalidate_results+刷新；几何
  更换（导入）时强制关旧面板。
- **消灭静默默认**（评审实测 face_b 静默 ('p1',1)）：A/B 下拉首项占位
  `("", -1)`（元组形状保持扫描安全）；A 面未选或连接类 B 面未选时
  `add_definition` 拒绝并弹中文提示（『连接 B 面未选择：请点三维视口中的
  第二面…不再默认第一部件第一个面』），definitions 计数不变；表单状态行
  『已选：部件X 面N（面积 … mm²）』+ 视口点选提示（下一次点击目标）。

验收：`verify/verify_face_pick_to_form.py`（GL 合成鼠标 + 三门 subprocess
复跑）：① 点部件1面→face_a=('p1',tag)、点部件2面→face_b=('p2',tag)
（初始占位断言）；② B 未选点添加→计数不变+中文提示；③ 粘接添加后
side_a/side_b 面指纹==fingerprint_from_face(点击面) 且 part_id 同步；
④ 下拉→3D 高亮、视口点击→表单同步（双向）；⑤ 三绿色门复跑 exit 0。
solver/convert/deck 数值链路零改动。

## 结果云图点面溯源（模块39，2026-11-03）

结果视图也支持点面：点击结果云图上的面 → 复用选中面卡片显示该面挂接的
约束/载荷定义与面内局部极值。

- **归属规则** = 「边界三角形角点节点集 ⊆ 面 NSET 节点集」（模块 8 结论的
  产品化）：`MainWindow._compute_result_face_tags(tris)` 逐三角形归属——
  产品 NSET 来自约束（FIXED）与非重力载荷（LOADFACE）（assembly_workflow
  `_prepare`，实读 60-76 行），经 `_last_nset_face_tags`（NSET 名→occ tag）
  映射；NSET 节点集优先 `MeshResult.node_sets`（求解会话），历史结果恢复
  路径退回 `prepared['mesh']` 的 *NSET 解析；行映射用
  `_active_result['node_ids']`（field_surface 的 points 行 ↔ gmsh tag）。
  未归属边界面 = -1（『无挂接定义』类，UI 明示不虚报）；多命中（共享边
  退化）取最小 occ tag 并记日志。
- **viewport**：`show_result_fields(..., face_tags=...)` → `_show_field_gl`
  把 tag 写入 `pd.cell_data["face_tag"]` 并保存 `_gl_face_tags`（此前云图
  路径硬置 None 无溯源）——结果视图拾取/高亮/卡片与几何视图同链路；
  `_handle_click` 对 tag<=0（无定义面命中）不高亮、emit(-1)，与空点
  （last_pick=None）可区分；`highlight_face` 增加 tag<=0 与 None/0d 数组
  守卫（numpy 2.x 对 0d 比较抛 ValueError）。
- **卡片**：结果视图模式下 payload 附加 `definitions_text`（该面约束/载荷
  名跨工况遍历）与 `local_max_mises`（面三角形节点并集上的 Mises max，
  与整场同源数组）；`FaceCardPanel` 显示『挂接定义：…』『面内 Mises 极值』；
  tag=-1 点击 → 卡片标题『选中面：无挂接定义』。
- **范围（B5）**：产品 NSET（约束/载荷面）100% 溯源；连接贴合面 NSET 映射
  为可选扩展未启用（需 mesher/deck 为粘接主从面发 NSET，将解除『不改
  convert/deck 数值链路』红线并单列验收）。证据更正：probes/pick_probe_log
  .txt:25 的 2556/2556=100% 是单零件网格对全部 6 个 occ 面的匹配，不等于
  产品 NSET 子集命中。

验收：`verify/verify_result_field_face_pick.py`（assembly_two_blocks 真实
ccx 求解产品流，配方同 verify_gui_assembly——project_dir 必须预设否则
_on_solve_static 弹模态目录对话框；p2 需经连接接地否则装配连接性门拒绝）：
① 独立复算归属规则与发布数组逐三角形一致、NSET 面（3/10）各 244 三角形
0 未命中、其余 2454 个 -1；② 同一屏幕位置几何/结果视图命中 tag 相同；
③ 挂接面卡片列约束名（不虚列他面定义）、无定义面卡片『无挂接定义』；
④ 局部极值与整场同源数组重算一致（实测 |diff|=0≤1e-9 相对）；⑤ 扩展未
实现的显式记录 + NSET 名单核对（仅 FIXED_/LOADFACE_ 前缀）。

## 工作流导航与首启引导（模块40，2026-11-03）

解决『首屏空视口劝退』（评审 high：违反使用体验要求 §2 当前步骤/缺少
输入/下一步始终可见）。

- **7 步流程条**：`panels.WorkflowBar`（导入→材料→约束载荷→网格→求解→
  结果→报告）置于主窗口中央区顶部。完成态由工程状态推导
  （`MainWindow._workflow_step_states`）：导入=solid_model 非空；材料=全部
  部件 material_name 非空且 confirmed（缺失→**error 标红**+『未指定材料：
  部件名』）；约束载荷=存在 face_tag>0 的约束/载荷；网格=study.mesh_file
  存在或 _solve_mesh_result；求解=project.cases 或 _last_archive；结果=
  _result_fields/_active_result；报告=_last_archive.report_md 存在。未完成
  步按钮直接显示『缺少：…』提示；点击 `step_clicked(序号)` →
  `_on_workflow_step_clicked` 直达对应对话框/动作（空工程点约束载荷→指引
  『请先导入 STEP』；几何就绪→非模态工况设置面板）；刷新点=_refresh_project
  _views + 显示包装器 + _on_show_mises/_displaced。
- **首启占位页**：`panels.EmptyStatePanel`（三按钮：导入 STEP / 打开示例
  工程 / 打开使用说明）作为 `QStackedWidget` 页 0（页 1=视口）——几何上屏
  （show_* 包装器/select_result_part）切到视口页，新建工程切回空状态页。
  `_on_open_example_project` 打开 `examples/<name>/project.rmsimproject`
  束（默认 part_beam；文件菜单『打开示例工程』子菜单可选
  part_beam/assembly_contact——modal_beam/chrono_demo 无束不列），
  复用 `_open_bundle_path`（自 _on_open_project 提取共用）；
  `_on_open_usage_docs` = os.startfile(使用说明.md)（帮助菜单同款入口）。
- **属性面板**：『几何（未导入）』行内 `set_inline_import` 挂『导入 STEP…』
  按钮（import_requested→_on_import_step），几何导入后自动移除。
- offscreen 渲染下截图中文显示为方块属环境字体枚举限制（GL 桌面截图
  正常，见模块 36/39 证据）；本模块验收全部为程序化状态断言。

验收：`verify/verify_workflow_navigation.py`（offscreen；真实求解
two_blocks 点亮全部步骤）：① 新建空工程→占位页 3 按钮+行内导入按钮+
7 步全 todo（hint 含『缺少』）；② 点示例工程→parts≥1+第 1 步 done+中央
切视口页+行内按钮消失；③ 清空材料→材料步 error+『未指定材料』；④ 点
约束载荷：空工程→指引『请先导入 STEP』、几何就绪→工况设置面板；点材料
→MaterialDialog（exec 打桩）；使用说明→os.startfile 打桩捕获；⑤ two_blocks
真实求解后 7 步全 done；⑥ main.py --smoke 复跑 exit 0+『主窗口已显示』。

## 现代视觉主题与中文化收口（模块41，2026-11-03）

消除『外国软件破解版』观感（评审 high-5/F3）。

- **QSS 主题**：`gui/theme.py`（ACCENT=#D6432C RM 红橙 + 浅色底 + 面板
  卡片化 + QPlainTextEdit 等宽字体 + 菜单/表格/输入框焦点样式），
  `apply_theme(app)` 在 `main.py launch_gui` 应用；首启占位页主按钮
  （导入 STEP）以 accent 实底展示（截图像素断言载体）。
- **中文化收口**：`theme.chinese_button_box(box, ok=/cancel=/close=/yes=/no=)`
  统一 QDialogButtonBox 标准按钮中文（默认映射 确定/取消/关闭/是/否）；
  全部 4 处直接实例化站点（material_dialog Close、mesh_dialogs Ok|Cancel+
  Close、advanced_dialog Ok|Cancel、main_window Ok|Cancel）重构经助手；
  源码扫描看门：gui/*.py（theme.py 除外）无 `= QDialogButtonBox(`。
- **菜单重组**：新增『结果(&T)』菜单收拢 von Mises 云图/位移云图/支反力
  合力表/结果极值面板/装配与部件结果 + 打开最近计算报告（报告并入结果，
  `_report_action` 别名保留使能逻辑）；新增『高级(&G)』菜单收拢 高级分析
  工作台 + 生成网格并导出 INP…（移出网格菜单，主流程菜单不含 INP 导出）；
  selfcheck ④ 菜单清单字符串同步为新菜单集。
- **快捷键**：`_add_action(..., shortcut=)`——Ctrl+N/O/S/I、F5 运行静力
  分析、Ctrl+P 打开最近计算报告。
- **部件名兜底**：`cad/assembly_import.py::display_part_name(raw, index)`——
  空名或命中 OCC 翻译器默认名模式（Open CASCADE|STEP translator，忽略
  大小写）→『部件N』（1 起）；应用于 PartInfo 构造与 sync_parts_from_model
  ——装配树/选面下拉/卡片均不再出现翻译器内部名。面引用走 part_id+几何
  指纹，改名无关。
- **F2 重命名**：`CaseTreePanel` 部件条目 ItemIsEditable + EditKeyPressed
  触发；`_loading` 守卫重入；`part_renamed(part_id, 新名)` →
  `MainWindow._on_part_renamed`（空名回滚；**itemChanged 信号栈内禁止同步
  clear 树——shiboken 重入删除，QTimer.singleShot(0) 延迟刷新**）。

验收：`verify/verify_theme_and_naming.py`：①可构造对话框（Material/
MeshControls/MeshQuality/Setup/CaseHistory/AdvancedWorkbench）按钮全中文
+ 源码扫描无直接实例化 + 助手冒烟；②two_blocks 导入后树与下拉无
『Open CASCADE』、显示部件1/部件2；③F2 改名→save_bundle(geometry_source)
→open_and_reresolve 往返 name 保持+is_confirmed+连接 validate；④结果菜单
5 项+报告并入+INP 在高级/不在主流程菜单+快捷键；⑤accent 像素 839 个落
证据目录；⑥--selfcheck exit 0。

## 求解期间可用性与结果仪表卡片（模块42，2026-11-03）

- **busy 分类（评审 high：menuBar 一刀切）**：`_set_solve_busy` 重写——
  QAction 分两类（`_act_modifying`：新建/打开/保存/另存为/导入 STEP/SW/
  示例工程/工况 3 动作/全局与局部网格设置/重新运行失败任务/高级工作台/
  INP 导出/材料 2 动作/运动 2 动作/模态分析；`_act_viewing`：结果菜单
  7 动作/历史结果/网格质量/视口/帮助/退出），busy 时修改类禁用、查看类
  开放（进入时快照基线、结束时恢复——完成分支随后的结果动作启用不受
  影响）；求解入口自身有重复提交守卫不参与分类。取消按钮链路不变
  （状态栏常驻『取消计算』→ `_cancel_current_solve`）。
- **绿色门 B1 修订**：verify_solver_gui_lifecycle.py:37/40 的 menuBar 断言
  改为『busy 时修改类 disabled 且查看类 enabled；结束后修改类恢复』
  （门内以『模块42 B1 修订』注释标记，随提交可追溯；修订前旧语义与分类
  直接冲突——禁用 QMenuBar 部件会连同全部子动作禁用）。
- **结果仪表卡片**：`panels.SolveDashboardCard` 置属性区顶部（选中面卡片
  之上）：最大 von Mises [MPa]、最大 |U| [mm]（同源 `_result_fields`——
  s_mises.max()/max_u_mag()，与极值面板同一数据对象）、最小安全系数
  （=min(各部件屈服/该部件最大 Mises)，数据取 results context 的
  assembly.parts[].material.yield_strength_mpa；单零件回退 yield_check；
  无屈服→『—』）+ 可信度徽章（已验证/待复核（连接待检查）/不可信
  （日志含错误））。`_result_context` 在求解完成与历史恢复时暂存。
- **红线**：无求解证据（含 configured_not_computed / prepared_not_computed
  一切未完成态）卡片显示『未计算/未验证』且不出现任何数值——卡片状态
  仅由 `_result_fields is not None and _result_context` 驱动；
  invalidate_results 即回未计算。

验收：`verify/verify_busy_state_and_dashboard.py`（offscreen；真实求解
two_blocks）：①busy 分类断言（修改类 20 个禁用/查看类 11 个开放）+取消
按钮点击真实取消（state=cancelled）+结束后恢复；②取消后卡片
『未计算/未验证』无任何数值；③求解后卡片 max_mises/max_u 与
s_mises.max()/max_u_mag() 逐值一致（|diff|=0≤1e-9 相对）+ ExtremesDialog
收到同一 _result_fields（同源）+ 安全系数手算一致（21.98530024，Q235
235 MPa，|diff|=0）；④无屈服材料→安全系数『—』；⑤lifecycle 门修订语义
复跑 exit 0 + 修订注释标记；全部状态截图落证据目录。

## 高级分析工作台可理解化（模块43，2026-11-03，交接书 §5-A）

工作台从数据结构直译（裸表格+10 平铺按钮+状态列硬编码『已配置，未计算』）
改为可理解界面。

- **命名工况下拉**：ObjectForm 对 boundary_case_index/load_case_index 生成
  QComboBox——项文本=工况名、currentData=索引（空工程占位『（尚未定义
  工况）』=-1）；LABEL 更新为『模态边界工况』『激励工况』（消灭裸整数
  语义）。往返与配置一致（to_dict 相等）。
- **状态列接 validate_studies 实际结论**：refresh() 每次实时计算
  validate_studies(project)——issue.path 前缀=study.id 映射到研究行：
  有缺失→『⚠ 』+具体缺失项文本（如『需要引用已启用且设置有效的modal研究』，
  红色）；无缺失→『✓ 校验通过（未计算）』（绿色 #1b5e20）；停用→『已停用』
  灰色；全局 issue（path='studies'）进状态栏。校验按钮（check）刷新摘要
  +状态列。硬编码『已配置，未计算』已消灭。
- **按研究类型分组**：工作台表格改为 `_WorkbenchTree`（QTreeWidget）——
  顶层=5 类分组（LABELS 序），组内只列该类研究；ObjectDialog 参数表单按
  设置数据类生成（modal 表单无 transient 专属字段——数据类天然隔离）。
- **诚实声明与求解按钮**：构造声明『当前高级后端尚未接通；这里不会启动
  求解』保留；新增求解按钮——未注册后端时 disabled 且文案含『尚未接通』
  （`_backend_ready` 经 advanced_plan.backend_for 探测）；导出完成后状态
  『准备包已导出，未计算：…』（prepared_not_computed 不虚报）。
- **兼容面保留**（verify_advanced_framework 全绿）：project/changed/task/
  kind 属性、refresh/selected/add/edit/toggle/rename/remove/check/export/
  poll/cancel_preparation/apply/done、ObjectForm.widgets[name]/.value()
  往返、事务性 deepcopy（工作台副本 +1 原工程不变）。

验收：`verify/verify_workbench_ux.py`（禁进程模式复用 verify_advanced
_framework 配方）：①工作台全程零外部进程（Popen 打桩未触发）；②缺
modal_study_id 的 harmonic→状态列具体缺失文本、补齐后绿色（前景
#1b5e20）；③命名工况下拉文本=currentData=索引往返一致、行标签无『从
0起』；④5 类分组+modal 表单无 transient 字段；⑤求解按钮禁用+『尚未
接通』+诚实声明保留；导出→prepared_not_computed 不虚报；框架兼容冒烟。

## 装配现状盘点与基线快照（模块44，2026-11-03）

接续中断的装配体开发前的**只读盘点**：不修改任何求解链路代码，产出后续装配
功能的验收基线 JSON（`E:/rm_sim/verification/assembly_inventory_*/`）。

- 验收脚本 `verify/verify_assembly_inventory.py`（exit 0 = 通过；日志
  verify/logs/）只读运行：**禁进程断言**（Popen/os.system/os.spawn*/os.exec*/
  os.startfile 全拦截，仅放行 git status/diff/rev-parse/log）+ **写盘断言**
  （运行前后 git status 快照一致）。
- 盘点三块（2026-11-03 实测基线）：① 工作区未提交改动 git 实测 +982/−335
  （24 跟踪文件）+ 140 未跟踪，任务书口径（27/+1468/−368）并列记录不冒充；
  ② 连接草稿遗留 151 候选→39 补丁/22 部件对、requires_review 28 个真未绑定、
  18 个 SPC 自由度已固定诊断号单列（707,710,712,713,11905–11911,11926–11932），
  用 solver/diagnostics 对整装 case_001 的 .nam/.log 只读复算交叉核对；
  ③ 2026-11-02 基线 44/53 门通过，9 个失败脚本逐个归类（2 编排无参调用、
  2 chrono 环境导入、5 内存守卫/ccx 0xC0000005）+证据路径，经 CLI 运行库
  ask#6 记录（mode=ro）核对。细节见 `docs/module_44_assembly_inventory.md`。

## 回归编排无参调用修复（模块45，2026-11-03）

盘点③中 2 个「编排无参调用」失败脚本的修复（带参模式行为不变）：

- `verify/orchestration_cases.py`（共享件）：只读定位最近整装归档 case
  （.inp/.dat/.frd/.log/.vtu 齐全 + Job finished + GRAV + PART elsets，按 .frd
  mtime 最新；`orchestration_fixture` 标记目录=③对照样例不参与定位）与最近后台
  工作流完成记录；无归档时现做最小两块装配（自重→直接 prepare/SolveScheduler；
  完成记录→WorkflowScheduler 真实 prepare/solve/collect）。`RM_SIM_NOARG_
  EVIDENCE_ROOT` 仅供验收把证据根指向空目录演练回退。
- `verify_full_assembly_solve.py` 无参：定位/现做后按 **ΣF/ΣM ≤0.5%** 复核
  （带参默认 2%/1% 不变），记录落 `full_assembly_solve_noarg_*`；真实整装
  （28 警告在案）ΣF 0.004%/ΣM 0.006% PASS——绑定警告记录在案不阻断
  （ok=checks 全真，checks 不含 requires_review）。
- `verify_workflow_bundle_relocation.py` 无参：定位/现做完成记录后迁移复核，
  语义与带参一致（旧目录零读取+哈希+rf_sum_n）。
- 验收 `verify/verify_regression_orchestration.py`：①无参各自 exit 0+stdout 声明
  case/记录与结论；②ΣF/ΣM ≤0.5%+位移有界+警告不阻断（静态+记录）；③带参输出
  与修复前预拍（verify/fixtures/orchestration_prefix_*）一致；④空证据根真实现做
  回退演练；⑤两脚本不在 run_regression.py DEFAULT（:13-20）——是否扩入待用户
  确认，未自动扩。细节见 `docs/module_45_regression_orchestration.md`。

## 求解内存预算分级与五门静力回归修复（模块46，2026-11-03）

盘点③中 5 个「内存守卫/ccx 0xC0000005」失败门的修复：

- **预算分级**：`tasks.resolve_memory_limit_bytes(max_workers, tier=…)` 纯函数——
  small 档固定足额 4GiB/任务（不随系统内存挤压缩水，不足额回落到可用×80%）、
  large 档上探 10GiB（实测锚 frozen_deck_budget_20261102_1918：同一 1mm 输入
  6GiB 停滞/10GiB 17.5s 完成）、OMP>2 每线程 +25% 谨慎余量；不可测→None（不设限）。
  **SolveScheduler 默认行为不变**（memory_limit_bytes=None 原样）。守卫文案改为
  含『内存预算』连续子串（验收可检索）。
- **规模参数化**：`verify/memory_budget.py::scale_tier()`（RM_SIM_SCALE）——默认档
  跑得过回归机（bonded ⑤ 1mm 档跳过打印 SKIPPED、bolt_pin 销 1.5mm），大网格档
  （--slow）执行 bonded 1mm/bolt_pin 1.0mm 原档；五门经 `make_scheduler` 接分级
  预算并打印 `[memory] job=… peak=… limit=…` 峰值证据行。
- **守卫单测**：C# 夹具 stall_at_cap 注入（峰值≥98%×内核限额）→ STATE_FAILED +
  error 含『内存预算』；真实 ccx 限额内（4GiB/峰值 0.79GiB）→ completed 不触发。
- 验收 `verify/verify_memory_budget_guard.py`：①两分支单测；②五门各自单跑
  exit 0+逐门理论锚（δ=0.1904762、60MPa Mises 由 results_view 承载、ΣRF 0.01%、
  预紧反力、接触只压不拉、部件应力同源）；③五门连续 3 轮无 0xC0000005，
  每轮退出码+峰值内存落 memory_budget_guard_* 证据目录。
  细节见 `docs/module_46_memory_budget_tiers.md`。

## 连接复核收口：requires_review 决策闭环（模块47，2026-11-03）

整装连接遗留复核欠账按**逐连接**决策收口（决策单元 F1：requires_review 是
布尔汇总、untied/reported 是节点计数——节点数只作关联证据，不据此自动决策）：

- `ConnectionBase` 复核字段（needs_review/review_decision/review_decided_at/
  review_evidence，默认值兼容旧工程）+ `REVIEW_DECISIONS` 枚举
  （pending/keep_bonded/switch_contact/remove）+ `apply_connection_decision`
  （switch_contact 原位替换为接触连接、remove 删除）+
  `undecided_block_message`。
- 求解入口拦截：`prepare_analysis` 存在 needs_review 且 pending 的连接 →
  ValueError 中文提示，不静默通过；首次求解与无警告连接不受影响。
- 回写：finish_analysis 把诊断 coverage 逐连接未绑定数>0 回写 needs_review
  （含 last_solve 证据），并重建 assembly.connections 快照；
  `collect_tie_diagnostics` 增 SPC/未绑定编号列表（有界）；
  报告新增「SPC 自由度已固定诊断编号」单列行与「连接复核决策」节。
- 批处理+GUI：`solver/connection_review.py`（工程记录/整装证据目录重建——
  INP 绑定带逐连接证据+attribution 归属+independent_check 合计交叉核对，
  读不到如实 unknown；apply_review_decisions 批处理写回）；
  `gui/connection_review_dialog.py`（草稿对话框「连接复核…」入口）。
- 验收 `verify/verify_connection_review_closure.py`：真实 39 补丁/22 部件对
  记录（合计 28 交叉核对、SPC 18 号单列）、夹具决策后重解（拦截空/改接触
  警告消失/平衡 ≤0.5%/界面不传拉净法向力口径/束往返保留决策）、未决策
  拦截负例、报告 SPC 单列、connections.md 同步。
  细节见 `docs/module_47_connection_review_closure.md`。

## 整装外载工况补账（模块48，2026-11-03）

真实快拆整装（20 实体）此前仅自重工况；本模块补外载工况并验证载荷线性律。
工程配方复用 `probes/quick_release_full_assembly_static.py`（分区 STEP + 按名
参考材料 + 连接草稿 ≥1mm² 过滤 39 补丁 + NEW壳体底面 -Y 理想固定）；网格一次
准备（size=4.0/curvature=8/min=0.4 → 70804 单元/128804 节点、0 负 Jacobian），
四个工况复用同一解析网格与绑定带建 deck（`build_assembly_case_inp` 复用路径），
逐工况真实 ccx + `finish_analysis` 归档；预算走 `memory_budget` large 档
（实测峰值 4.05GiB / 10GiB 预算）。

- 工况：1g/3g/5g 重力倍率 + 100N 假设外载（≈2kg×5g 包络，沿 -X 施加于 NEW壳体
  侧面；假设工况随 provenance 记录）。
- 实测门：位移比 1:3:5（两两偏差 ≤1.2e-06）；接地反力 ΣRFy=2.176955/6.530865/
  10.884775 N（残差 0.0056%≤0.5%）；100N 工况 ΣRF=[100.000212, 2.163733,
  0.014558]（矢量残差 0.0197%≤0.5%，修正口径）。
- 历史：4 个 case 写入 project.cases（红线① work_root=project_dir/cases），
  `load_archived_case`（verify_history_restore 机制）逐工况读回 max|U|/rf_sum
  一致；束保存/重开后仍可读回。
- 连接复核（模块47 回写）：needs_review 连接集合与逐连接未绑定合计以**本运行
  诊断**自一致口径核对（12 条/合计 54、唯一节点 42、SPC 18 单列）——不得硬编码
  其他网格的检查记录值（DS c12 网格为 28，节点集合随网格变化）。
- 验收 `verify/verify_assembly_load_scaling.py`（exit 0 = 通过；含 4 处脚本侧
  修复记录：外载面须进首次 prepare 工况否则无 NSET、②反力矢量符号、
  review 回写自一致口径、verification.json 记录本运行诊断总量）。

## 装配与部件结果仪表收口（模块49，2026-11-03）

『装配与部件结果』对话框升级为部件仪表卡片：每部件 max Mises [MPa] / max |U|
[mm] / 质量 [kg] / 材料名 + 整装合成卡片（accent 边框、置顶）；数值与
`solver.fields.part_field_views` 同源（卡片渲染所依据的浮点随 Qt property
`value`/`unit` 暴露，供验收同源核对——模块42 卡片口径）。

- `AssemblyResultDialog` 新增可选参 `parts_meta`（{部件名: {mass_kg, material}}，
  质量=部件体积×材料密度，`main_window` 调用点从归档 ctx assembly.parts 取）与
  `report_path`（『打开诊断（计算报告）』按钮，无报告时禁用）；无结果时整表只出
  『未计算』状态（红线：无数值）。开发者术语出清：『部件 ELSET 单元节点并集』
  『solver.fields.part_field_views』等脚注改为人话口径说明（『…口径见计算报告』）。
- **B4 API 兼容**：保留 `_table/_combo`（行1列1 = max |U| 数值文本，
  verify_gui_assembly.py:84 门原语义复跑 exit 0，未修订）。
- 验收 `verify/verify_assembly_dashboard.py`：真实两块装配求解产物载入后卡片与
  part_field_views 同源重算一致（|diff|≤1e-9 相对）、明细表切换部件后文本一致
  （.6g 展示精度口径）、单位齐全、质量=ρ×体积手算 ≤0.5%、空结果『未计算』无数值、
  全部可见文本无开发者术语正则断言、截图与空态截图落证据目录、
  verify_gui_assembly 复跑 + main.py --selfcheck 契约回归全过。

## 验收耗时分解与套件标签（模块50，2026-11-03）

调研 §7-1 第 1 项落地：标签 `fast_config/real_small/reliability/full`（对照调研
§4 分层表）**只用于选择与报告**，不改变任何既有门、容差与原完整验收入口。

- `verify/suite_labels.py`（共享件）：42 项检查（DEFAULT 40+SLOW 2）逐项主标签+归类
  理由，干净划分 17/18/5/2——fast_config=不启动 ccx（可真实 gmsh/OCC/Qt）、
  real_small=真实 ccx 小算例（五门全属此类）、reliability=调度/取消/线程/内存保护/
  生命周期守卫、full=仅 --slow 的整装档两项。
- `verify/run_regression.py`（增量）：`--labels` 标签选择（未知/零命中 exit 2）、
  逐检查行标签注记 `name [label]: PASS exit=N N.NNs`、summary.json 行级 `label`、
  `timing.json`（每检查秒数+wall_seconds+by_label 聚合）+ 收尾 TIMING 两行（原
  REGRESSION 行保持在先）。无 `--labels` 时行为与改前逐行一致。
- `verify/verify_memory_budget_guard.py`（增量，门与判据零改动）：轮 wall_seconds、
  逐门 label、每轮每门耗时分解（§2 表同口径打印 + timing_breakdown.json +
  verification.json）。
- 验收 `verify/verify_regression_labels.py`：①改前/改后输出归一化（仅剥时间戳/
  秒数/标签注记/TIMING 行）逐字节一致（带参/--framework-only/--help/清单快照/
  无参全量 40 项）；②耗时落 JSON 且与抽查 wall clock 一致（抽查 Σ1.99s=内墙钟，
  外部 2.15s；套件每轮每门秒数+轮墙钟一致性）；③标签子集选择可断言（划分/归属/
  实跑互相滤除/未知与零命中 exit 2）；④原五门套件完整重跑 exit 0（1007s）。
  本轮实测：无参全量 wall=652.35s（contact 门 324.7s 占 ~50%）；套件 3 轮每轮
  contact≈329s 为长门——如实记录，本轮无提速改动。细节见
  `docs/module_50_regression_labels.md`。

## 共享冻结产物与失败项重跑（模块51，2026-11-03）

调研 §4 落地：一次真实小算例求解供多个读取型检查复用（GUI/报告/序列化类）。

- `verify/shared_products.py`：缓存键=几何文件内容+几何映射（逐部件 PartFingerprint）
  +网格参数（size+gmsh 版本+网格代码内容哈希）+**最终 INP 与全部 INCLUDE 内容**
  （载荷在 INP 内——载荷一变键即变）+引擎指纹（ccx 路径+二进制 sha）+线程/数值
  设置；try 复用前真实执行几何导入+网格+deck 组装（不求解）再算键。冻结只接受
  完成、文件齐全（成员齐全+completed+Job finished）且哈希匹配的束，参考数由
  load_archived_case 独立复算入 manifest（来源/时间可追查，原子落盘不覆盖）；
  复用先完整性校验（束哈希→成员→逐成员哈希），共享数据只读、独立临时目录解包。
- 三个读取型检查（显式点名，不入 DEFAULT——无参语义不变）：reuse_results_gui
  （offscreen 仪表对话框显示值=结论）、reuse_report_check（报告附录同源同文+
  正文打印位数核对）、reuse_archive_roundtrip（load_archived_case 原生读回）；
  结论与冻结参考 1e-9 门统一在 run_reader 执行；未命中时自行真实求解一次并冻结。
- `run_regression.py`：`--only-failed` 只重跑最近证据失败项（可与 --labels 交集，
  无失败项 0 重跑 exit 0）；报告来源单列——summary.json 逐行 `source`（解析
  `[product] source=` 标记）+ timing.json `sources` 计数与 `not_executed`
  （「未执行/待验证」），三种状态：本轮新求解/复用历史求解并重新检查/未执行待验证。
- 验收 `verify/verify_shared_products.py`（隔离共享根演练）：①一次求解→三类
  读取器复用且结论一致（实测三类逐位一致）；②不完整/哈希不匹配/截断全拒负例；
  ③载荷变更键变不可整份复用+算键确定性；④--only-failed 只重跑失败项+来源标注
  正确+无失败项 0 重跑；⑤来源单列三状态实际出现。细节见
  `docs/module_51_shared_products.md`。

## Chrono 子进程环境隔离与 ABI 预检（模块53，2026-11-03）

修复两门（verify_chrono_load_transfer/verify_extensions_gui）的根因：宿主
`PYTHONPATH`（本机 `E:\Codex\python-packages`，含 cp313 numpy）经
`os.environ` 泄漏进 chrono-env（cp312）子进程 → numpy 解析到 cp313 二进制 →
stderr ImportError（原 env=dict(os.environ,…) 只补 PATH；Nov-2 晚间证据
chrono_20261102_222504 已是同一失败）。

- `solver/chrono_adapter.py::chrono_isolated_env`：子进程 env 剥离宿主全部
  `PYTHON*` 变量，显式设 PYTHONUTF8=1/PYTHONNOUSERSITE=1/PYTHONSAFEPATH=1，
  PATH 前缀 chrono-env 不变，其余系统变量原样继承。
- ABI 预检：`chrono_expected_abi_tag` 向 chrono-env 解释器本体实问 EXT_SUFFIX
  （→cp312，落盘 chrono_interpreter.json）；场景进程启动先跑
  `chrono_scene.environment_report`——导入 numpy/pychrono，把版本、扩展路径、
  已加载扩展 ABI tag 与 sys.path 打印 `CHRONO_ENV_REPORT` 行进
  chrono_stdout.log 并写 chrono_env_report.json；导入失败 exit 3+
  `CHRONO_ENV_PRECHECK_FAILED`（ImportError 进结构化报告不再裸奔 stderr）。
  `check_chrono_environment` 在解析任何结果前判定：报告缺失/带 error/tag≠期望/
  numpy·pychrono 模块越出 chrono-env 目录 → 各自 RuntimeError。pychrono 的
  `_core.pyd` 文件名无 ABI tag，其兼容性由导入成功本身证明。
- venv 路线实测不可行：pip 在 cp313 解析到 `pychrono 1.1.0`（10 kB 纯 Python
  占位包，非 Project Chrono）——以 chrono-env 为准，主 venv 未装 pychrono，
  主环境 numpy 2.5.3 不变。
- 平台怪癖（实测）：chrono-env python 从 MSYS/git-bash 直接启动会挂起
  （0.3s CPU 静止）；一切 Chrono 子进程必须经 Windows 父进程（venv python）
  spawn——产品链路与验收均如此。
- 场景算例/单位/锚点/`run_chrono` 返回结构/config `chronoPython` 零改动。
- 验收：`verify/verify_chrono_env_isolation.py`（exit 0；日志 verify/logs/）——
  ①a env 清洗单元；①b 宿主注入恶意 PYTHONPATH（cp313 site-packages）后产品
  路径仍加载 cp312 numpy 2.5.4 且完整场景跑通（单摆 1.639s）；③
  CHRONO_ENV_REPORT 落日志；②a 恶意直跑秒级 exit 3+毒性现场留档；②b 伪造
  报告四形态负例；④ 两门单跑复跑 exit 0 + 全部子进程 stderr 无 ImportError。
  细节见 `docs/module_53_chrono_env_isolation.md`。

## 整装模态输入编译与结果契约（模块54，2026-11-03）

交接书 §5-B 接续点的「不调引擎部分」。modal 适配器 prepare()（输入编译）
+ RESULTS['modal']（advanced_plan.py:15：frequency_hz/normalized_mode_shapes/
eigen_residuals）结果 schema 机判校验器；run/collect 仍不接引擎，
UI 保持『未计算』无频率数字。

- `solver/advanced_modal.py`：`compile_modal_input` 读准备包
  （load_preparation_package 既有校验链）→ 校验（已启用 modal 研究
  modes∈[1,30]；接触/螺栓连接显式拒绝——线性特征值不支持；每部件密度>0；
  至少一个已挂接固定约束面）→ `prepare_analysis` 统一准备流（约束面 NSET
  映射、gmsh C3D10、多材料密度行+粘接 *TIE）→ 取 deck `*STEP` 前置段
  （防御守卫：含 *STATIC/*CLOAD/*DLOAD/*CONTACT PAIR/*RIGID BODY/*FRICTION
  即拒绝）拼接 `*STEP/*FREQUENCY/<modes>/*NODE FILE U/*END STEP`（与已验证
  单零件链路 modal.py:61 同构）。产物 `<准备包>/modal_compile/case_NNN/`
  （modal.inp+prepare_manifest.json 摘要含 deck_sha256/逐部件 E ν ρ/固定
  NSET/tie 卡数；准备包本体不改写）。`validate_modal_results` 机判：键恰为
  契约三键、频率长度=模态数且有限为正（负/零特征值拒绝）、振型
  (modes,n_nodes,3) 逐阶最大位移点范数=1（同 modal.py 归一口径）、残差
  全部 <1e-8。
- 能力位（B2 修订的落点）：`AdvancedBackend.engine_connected=False`
  （类属性契约）——`ModalBackend` 注册（prepare 可用）但保持 False，
  run/collect 继承基类抛『尚未接通』；`advanced_dialog._backend_ready`
  改为「已注册且 engine_connected」→ modal 求解按钮仍禁用、文案仍
  『尚未接通』（verify_workbench_ux ⑤ 原语义保持）。注册经
  advanced_plan 尾部 `_register_default_backends`（防循环+查重）与
  advanced_modal 尾部补注册双向安全。
- B2 框架门同步（verify_advanced_framework.py:102-104 评审实读证实冲突）：
  修订为 modal「已注册+prepare 已覆写+engine_connected=False+run/collect
  仍抛尚未接通」，其余四类未注册断言与 register_backend 无效接口断言保留
  （源内 `模块54 B2 修订` 注释标记，修订 diff 随提交可追溯）。
- 验收：`verify/verify_assembly_modal_compile.py`（exit 0；日志 verify/logs/）
  ——①两块粘接装配（Q235+6061）prepare 输出 *FREQUENCY/6 + 两部件密度行与
  工程值逐字段一致 + PART 截面卡 + 约束面 NSET 进 *BOUNDARY +
  `*TIE, NAME=C1_TIE, ADJUST=NO, POSITION TOLERANCE=0.07`（实跑实证卡型）
  + 静态/接触卡型零残留；②合法合成样例过、14 形态坏数据全拒；③run/collect
  抛『尚未接通』；④禁进程毒下编译零进程+无求解产物+UI『未计算』无 Hz；
  ⑤framework 门复跑 exit 0 + workbench_ux 门复跑 exit 0。
  细节见 `docs/module_54_assembly_modal_compile.md`。

## 整装模态真实求解与理论核验（模块55，2026-11-03）

§5-B 续：modal 后端接入真实 ccx（config ccxPath=ccx_launcher.exe），
状态 configured_not_computed → computed_with_evidence 并落历史。

- `ModalBackend.engine_connected=True`（模块54 能力位随真实接线翻转）；
  `run_modal_solve`：二进制捕获+『Job finished』门（tasks/modal.py 同口径，
  退出码不作判据）；`collect_modal_results`：复用 modal.py
  parse_frequencies/parse_mode_shapes（前者逐行 dict 新增 eigen_residual 键
  =DAT 第 5 列特征值虚部——复模态残差口径，与既有 |values[3]|≤1e-9 门
  同源）→ 逐阶 max 范数归一 → validate_modal_results 机判 → 固定 NSET
  边界判据（<1e-8×峰值，INP NSET 由 parse_mesh_inp 独立取得）。case 目录
  落 verification.json（computed_with_evidence）+ run_history.json
  （追加式历史，state/previous_state 如实标注）；准备包 manifest 保持
  prepared_not_computed 不改写。
- GUI 三态（_update_solve_button）：未注册→『尚未接通』；已接通但工作台
  编排未接线（_solve_orchestration_ready=False）→ 禁用+『求解（后端已接通；
  工作台求解编排尚未接通，这里不会启动求解）』——不虚报可点求解；
  workbench_ux ⑤ 与模块54 门断言原语义保持。
- B2 续：verify_advanced_framework.py modal 断言推进为 engine_connected
  is True（run/collect『尚未接通』断言移除——实跑证据由验收脚本承载，
  framework 门禁进程不解算），其余四类永远保持未注册断言；
  verify_assembly_modal_compile.py（模块54 门）B2 块/③ 节连带修订
  （源内『模块55 B2 续』标记），复跑 exit 0。
- 验收 `verify/verify_assembly_modal_solve.py`（exit 0；日志 verify/logs/）：
  串联两杆绑定装配（两杆同指定 45钢 → 等效均匀悬臂梁 L=200 口径，样例
  本身不带材料）固定端模态 f1=208.992900 Hz vs 理论 208.879149 Hz
  偏差 0.054%≤3%；残差全 <1e-8（实测全 0）；固定 NSET 幅值比 <1e-8；
  振型峰值=1；产物齐全 + 状态如实双层标注；framework/compile/
  workbench_ux/modal_analysis 四门复跑 exit 0。细节见
  `docs/module_55_assembly_modal_solve.md`。

## 模态结果查看与仪表（模块56，2026-11-03）

模态对话框升级（gui/modal_dialog.py 既有入口 ModalResultDialog），消费
模块55 整装模态真实求解产出；verify_modal_view 既有契约全保留（复跑
exit 0）。

- 后端配套：free_free 编译分支（`ModalSettings.boundary` 既有字段显式声明；
  无 *BOUNDARY，网格走 prepare_analysis(mesh_only=True) 同一配方，材料卡+
  `_connection_cards` *TIE 自组 deck；project_fixed 路径零改动）；
  parse_frequencies(allow_rigid=) 接受刚体行（ccx 对 λ≤0 行频率列印 0、
  虚部列放大噪声——探针实测刚体簇 0~5.7e-3 Hz 且对 *FREQUENCY 频率窗
  不敏感）；validate_modal_results(rigid_modes=/rigid_freq_tol=1e-2) 前苑
  6 阶刚体宽容口径（默认 0 语义不变）；编译期 display_mesh.npz（边界
  三角形+逐三角形 occ tag，质心 pick_face().face.tag，全失败显式拒绝）+
  collect 期 modes_display.npz（显示网格+归一化振型+频率单一数据包）。
- 对话框：频率列表逐阶标注频率；free_free 前 6 阶标注『刚体模态』+ 横幅
  明示含义（整体平动/转动，非弹性变形）；逐阶标题含频率与『归一化振型
  （非真实振幅）』；show_modal_fields 新增 face_tags → GL cell_data 同链路
  拾取；点面 → FaceCardPanel（ux 选中面卡片复用，panels.py 新增
  is_modal_view 分支）显示该面节点组 |振型| 最大值+『归一化振型不代表
  实际振幅』。
- 验收 `verify/verify_modal_results_dashboard.py`（exit 0；GL native 配方，
  脚本不得设 offscreen）：①6 阶逐阶截图非空+标注频率≤0.1 Hz（208.993…/
  3561.53）；②点击端面（tag=8 交叉核对）→卡片 44 节点组 |振型| max=1.0
  与当帧重算 |diff|/值=0≤1e-9；③free_free 12 阶：前 6 阶实测
  [0,0,0,0,3.75e-3,4.89e-3] Hz 逐阶标注刚体+含义、第 7 阶起弹性 1317 Hz；
  任务书 <1e-3 Hz 阈值在阶次 5–6 未达（ARPACK 零簇噪声地板，偏差如实记录
  入 verification.json，门按 |f|<1e-2 Hz 刚体簇口径执行——详见
  module_56 文档 §3）；④横幅/逐阶标题/面卡 5 处文案含『归一化振型』。
  回归：modal_analysis/modal_view/face_pick_card/assembly_modal_compile/
  workbench_ux/assembly_modal_solve 六门复跑 exit 0。细节见
  `docs/module_56_modal_results_dashboard.md`。

## 运动载荷传递参数化（模块57，2026-11-03）

transfer_load 由硬编码梁示例+固定 90° 旋转向参数化为 LoadTransferSpec 驱动
（core/advanced.py:95-103 rotation/translation_mm/source_point_mm/
target_point_mm），既有梁示例入口签名/行为零破坏（两道 chrono 门复跑
exit 0）。

- `chrono_adapter.map_transfer_sample(source_json, spec, sample_index)`：units
  门 m-kg-s-N 保留；映射复用 advanced_history.transfer_wrench——结构力=r@F、
  结构力矩=r@(1000·m)+(r@source+t−target)×(r@F)（N·m→N·mm 作用点平移项）；
  样本可选字段 moment_world_nm/source_point_m（既有场景样本不含→力矩零/
  作用点回退 spec.source_point_mm，优先级记入 provenance
  application_point_source；Chrono 点 m→mm ×1000）。
- DEFAULT_TRANSFER_SPEC=既有 90° 映射的 spec 表达（source/target=原点→平移项
  为零）——梁示例 transfer_load 经同一参数化代码数值逐位不变；provenance
  既有键全保留+加法新键（sha256/rotation/time_s/translation/source/target/
  源与映射向量/spec_target_part_id）；write_transfer_provenance 落盘。
- 验收 `verify/verify_load_transfer_parametric.py`（exit 0；日志 verify/logs/）：
  ①合成 JSON（F/作用点/力矩）映射与手算 ≤1e-9+幅值不变；②units 负例拒绝；
  ③端到端 run_chrono 真实弹簧样本→参数化映射→粘接串联两杆 rod2 端面
  TractionLoad+MomentLoad→真实 ccx→接地反力合力=|映射力| 偏差 0.0000%
  ≤0.5%；④provenance 回放复算（R@source_force 与平移项力矩 ≤1e-9）；
  ⑤spec 校验负例（mechanism 引用缺失/源刚体不存在/目标部件不存在/非正交
  旋转）全拒。样本力矩/作用点字段为本模块可选约定（chrono_scene 侧真实
  力矩采集属后续）；反力矩逐项守恒核验未设门（模块5/7 已承载）。细节见
  `docs/module_57_load_transfer_parametric.md`。

## 跌落冲击最小可验证算例（模块58，2026-11-03）

把握不足项拆最小算例：刚体跌落（chrono_scene Bullet 块既有）→ 峰值接触力
时刻样本 → 模块57 参数化传递映射 → 结构静力（准静态近似）。**完成度红线
（绝不虚报动态冲击能力）：刚体冲击的准静态近似；未计算应力波传播、未启用
塑性、碰撞应力不可信。**

- 场景改造（chrono_scene.py）：箱体改自 **1 m 自由跌落**（无水平初速）；接触
  合力 `box.GetContactForce()` 直读（该 PyChrono 10 构建无 GetPosDtDt——探针
  实测）；impact_sample=峰值接触力时刻样本（接触数>0，y=全程最低位）写入
  chrono_results.json，`force_world_n`=结构侧受力（箱体接触力反作用力，
  符号约定在案）；既有键全保留（chrono_load_transfer 碰撞断言复跑 exit 0）。
- 适配器：map_transfer_sample 新增 sample 直传参数（units 门/sha256 仍对
  JSON 生效）；DROP_IMPACT_COMPLETENESS 完成度标注；impact_force_warning
  （峰值>10×落体重力→警告文案，记录而非静默放大）；
  write_drop_impact_report（红线文案『准静态近似』『未验证冲击应力』等）。
- 验收 `verify/verify_drop_impact_minimal.py`（exit 0；日志 verify/logs/）：
  ①t=0.452s（√(2h/g) 吻合）/4 接触/y=全程最低/1m 跌落样本在案；②映射
  mapped_f≈[0,−34625,0]N → 粘接串联两杆端面面力 → 真实 ccx → 接地反力
  34625.3627N 偏差 0.0001%≤0.5%；③报告红线字样断言；④真实峰值 441.2×
  重力触发警告+合成 11×/5× 双向验证；⑤verification.json completion 四字段
  如实。回归：chrono_load_transfer/extensions_gui 复跑 exit 0。细节见
  `docs/module_58_drop_impact_minimal.md`。

## 塑性变形最小可验证算例（模块59，2026-11-03）

把握不足拆最小算例：CalculiX *PLASTIC 双线性（Q235 屈服 235 MPa，切线模量
常用估值 **E/100=2100 MPa——未经材料试验标定**）。**完成度红线：塑性参数
未经材料试验标定；损伤（DamageSettings）本轮不做——损伤分析未完成。**
几何线性小应变，未启用 NLGEOM。`solver/plastic_minimal.py` 新建。

- 材料卡 `material_block(..., enabled)`：*PLASTIC 表优先取
  PlasticCurve.points（等效应变,真实应力，与 CalculiX 同序直用），否则
  双线性估值表；**回归保护：NonlinearSettings.plasticity_enabled=False →
  不生成 *PLASTIC 卡（③双向断言）**。
- 算例①（1×1×1 C3D8 单轴，四步）：拉至 1.5×屈服 → **自由卸载**（*BOUNDARY,
  OP=NEW 清加载面约束——首跑教训：强压 u=0 与残余塑性应变矛盾 → 反向塑性
  流动 PEEQ 翻倍，非物理）→ 再加载两级（塑性段）。实测：σ1=352.5 精确；
  卸载后 PEEQ=0.05595238 vs 理论 ε_p=ε_total−σ/E **偏差 0.000%≤2%**；塑性段
  两级间割线 2079.21 vs E_t=2100 **0.990%≤5%**；σ_a/σ_b=399.01/440.594 与
  各向同性硬化理论式逐位一致。
- 算例②（10×10×100 悬臂梁 C3D8R 40×4×4 减缩积分——C3D8 全积分弯曲锁死
  实测 +18.5%）：端部 u_y=−1.5mm 位移控制，P_FEM=593.246 N vs 塑性铰理论
  P=M_p/L=587.5 N（M_p=σ_y·b·h²/4）**偏差 0.978%≤10%**，根部 PEEQ>0。
- DAT 解析：parse_dat_blocks（*EL PRINT 按增量输出，DAT time=总时间）+
  step_end_blocks（步 k 步末块 time≈k；任一缺失返回 [] 不静默取错块）；
  *NSET 数据行 ≤8 项/行（CalculiX ≤16 项限制实测）。
- 验收 `verify/verify_plasticity_minimal.py`（exit 0；日志 verify/logs/；
  证据 plasticity_minimal_20261103_230124_549536）。报告含『塑性参数未经
  材料试验标定』『损伤分析未完成』（④）。范围：无循环塑性/随动硬化
  （包辛格效应不可模拟）；切线模量未标定不可用于寿命/成形评估。细节见
  `docs/module_59_plasticity_minimal.md`。
