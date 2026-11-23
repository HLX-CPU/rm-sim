# 模块 54：整装模态输入编译与结果契约（2026-11-03）

交接书 §5-B 指定接续点的「不调引擎部分」：modal 适配器 prepare()（输入编译）
+ RESULTS['modal'] 结果 schema 机判校验器 + B2 框架门同步修订。
run()/collect() 仍不接引擎（BackendNotConnected）；UI 保持『未计算』、不出现
频率数字。求解链路（静力五门）零改动；单零件模态旧入口（solver/modal.py）
零改动、其验证范围声明不变。

## 1. 实现

### 1.1 适配器与能力位（`solver/advanced_modal.py` + `advanced_plan.py`）

- `AdvancedBackend` 新增能力位 `engine_connected: bool = False`（类属性，
  文档化契约）：仅当 run/collect 已接真实引擎时为 True。只注册了 prepare
  （输入编译）的适配器保持 False——GUI 求解按钮与各门禁据此判定「可否求解」，
  不能只看适配器是否注册。
- `ModalBackend(AdvancedBackend)`：`prepare()`=输入编译（可用）；
  `run()/collect()` 继承基类 → `BackendNotConnected('…尚未接通')`；
  `engine_connected=False`。经 `advanced_plan._register_default_backends()`
  （尾部局部导入防循环、查重后注册）+ advanced_modal 模块尾部补注册实现
  双向安全——两种导入顺序都保证 `backend_for('modal')` 可用
  （两种顺序均实测 PASS）。
- 其余四类（harmonic/transient/mechanism/nonlinear）保持未注册 →
  `backend_for` 抛 BackendNotConnected；`register_backend` 无效接口/
  重复注册断言不变。

### 1.2 prepare()=输入编译（`compile_modal_input`）

读准备包（`load_preparation_package` 既有校验链：manifest 版本/状态/逐文件
哈希/plan 一致性，advanced_plan.py 实读）→ 校验：

- 存在已启用 modal 研究（modes∈[1,30]，同已验证单零件链路 modal.py 口径）；
- 连接类型预检（fail fast）：接触（ContactFrictionConnection）/螺栓
  （BoltConnection）显式拒绝——线性特征值无可分离界面/无预紧；仅粘接
  （tie=*TIE 卡 / merge=共节点）与销轴零间隙绑定（*TIE）支持；
- 每部件材料密度>0（模态需要质量）；至少一个已挂接几何面的固定约束。

然后走装配统一准备流（`assembly_workflow.prepare_analysis`：reresolve 指纹
确认、约束面→NSET 映射、gmsh C3D10 网格、`build_assembly_case_inp` 多材料
密度行+连接卡），把 `*STEP` 之前的前置段（网格+每部件 *MATERIAL/*ELASTIC/
*DENSITY/*SOLID SECTION + *TIE + *BOUNDARY）与模态步拼接：

```
*STEP
*FREQUENCY
<modes>
*NODE FILE
U
*END STEP
```

与已验证单零件链路（modal.py:61）完全同构。防御守卫：前置段含
`*STATIC/*CLOAD/*DLOAD/*CONTACT PAIR/*RIGID BODY/*FRICTION` 任一即拒绝
（静力载荷不进模态；防连接卡型混入）。产物落 `<准备包>/modal_compile/
case_NNN/`（mesh.inp + mesh.quality.json + modal.inp + prepare_manifest.json；
case 号递增，不覆盖），摘要含 deck_sha256、逐部件 E/ν/ρ、固定 NSET、tie
卡数、bond_mode、契约键与 `solved=False`/`engine_connected=False`。
准备包本体与 manifest 不改写。

### 1.3 结果契约机判校验器（RESULTS['modal']，advanced_plan.py:15 实读锚）

`validate_modal_results(payload, *, modes=None, n_nodes=None)`：键必须恰为
`frequency_hz / normalized_mode_shapes / eigen_residuals`；频率长度=模态数、
有限且全为正（负/零特征值拒绝）；振型矩阵形状 (modes, n_nodes, 3)、有限、
逐阶最大位移点范数=1（|max−1|≤1e-6，与 modal.py 归一口径一致）；残差长度=
模态数且全部 |值|<1e-8。任一违反 ValueError 带具体阶次/数值。
`modal_results_payload(...)` 供未来 collect 组装。

### 1.4 GUI（`advanced_dialog.py::_backend_ready`）

改为「已注册 **且** engine_connected」：modal 适配器已注册但引擎未接通 →
求解按钮仍禁用、文案仍『尚未接通』（诚实声明不变）；状态列仍是
validate_studies 的『✓ 校验通过（未计算）』——UI 无任何频率数字来源
（collect 未接通，prepare 只写输入）。

## 2. B2 框架门同步（verify_advanced_framework.py:102-104，修订可追溯）

评审实读证实：原门断言 `backend_for('modal')` 必抛 BackendNotConnected
（'Missing adapter silently selected a fallback'），与「注册含可用 prepare
的 modal 适配器」直接冲突。修订（源内注释 `模块54 B2 修订` 标记，:102 起）：

- modal：断言适配器已注册 + `type(adapter).prepare is not
  AdvancedBackend.prepare`（编译输入已接线）+ `engine_connected is False`
  + `run/collect` 仍抛 BackendNotConnected；
- 其余四类逐一断言未注册抛 BackendNotConnected（原语义保持，改逐类循环）；
- `register_backend('modal', object())` 无效接口断言保留（:105-107 原语义，
  isinstance 先于重复注册检查，异常类型不变）。

## 3. 验收（`verify/verify_assembly_modal_compile.py`，exit 0 = 通过）

2026-11-03 实测 exit 0（日志 verify/logs/verify_assembly_modal_compile.log；
证据 E:/rm_sim/verification/assembly_modal_compile_20261103_204912*）：

- ① 两块粘接装配（Q235+6061）→ prepare 输出 INP：`*FREQUENCY/6` 卡、
  两部件 *DENSITY 行（7.85e-09 / 2.7e-09，与工程材料值逐字段一致）、
  `*SOLID SECTION, ELSET=PART1/PART2, MATERIAL=MAT_p1/MAT_p2`、约束面 NSET
  `FIXED_FACE_3` 进 *BOUNDARY 且网格段含该 *NSET、
  `*TIE, NAME=C1_TIE, ADJUST=NO, POSITION TOLERANCE=0.07`（语法同既有实跑
  实证卡型）；*STATIC/*CLOAD/*DLOAD/*CONTACT PAIR/*RIGID BODY/*FRICTION
  零残留；16057 节点 / 9810 单元；
- ② 机判：合法合成样例（(5,37,3) 归一振型+正频率+零残差）通过；14 形态
  坏数据（残差 1e-4/少一阶/第三维=4/节点数不符/模态数不符/负特征值/零
  特征值/频率 NaN/振型 NaN/未归一化/缺键/多余键/空数组/非 dict）全部拒绝；
- ③ run/collect/基类 prepare 抛 BackendNotConnected 且文案含『尚未接通』；
- ④ Popen/os.system/spawn*/exec* 全禁毒下编译（零进程）+ case 目录无
  .dat/.frd/.log 等求解产物 + 工作台求解按钮禁用且文案含『尚未接通』+
  状态列『✓ 校验通过（未计算）』无 Hz；
- ⑤ verify_advanced_framework.py 修订后复跑 exit 0（修订标记与 git diff
  在验收日志中回显）；verify_workbench_ux.py 复跑 exit 0（_backend_ready
  接能力位后 ⑤ 原语义保持——按钮禁用+『尚未接通』）。

## 4. 范围与诚实声明

- 编译范围：粘接（tie/merge）与销轴 *TIE；接触/螺栓模态显式拒绝。
- prepare 只编译输入，不产生任何频率/振型/残差；run/collect/引擎接线、
  模态结果查看（装配）属后续接续点。
- 单零件模态 GUI 旧入口（run_modal）声明「装配模态尚未验证」的检查保留
  不动；本模块提供的是框架侧整装编译契约，二者合流属后续。
