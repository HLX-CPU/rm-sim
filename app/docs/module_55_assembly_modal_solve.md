# 模块 55：整装模态真实求解与理论核验（2026-11-03）

交接书 §5-B 续：modal 后端接入真实 ccx（config ccxPath=D:/rm_sim/tools/
ccx_launcher.exe），状态从 configured_not_computed 推进到
computed_with_evidence 并落历史；B2 框架门同步再推进一档。

## 1. 实现（`solver/advanced_modal.py` + `solver/modal.py`）

- `ModalBackend.engine_connected=True`（模块54 的能力位随真实接线翻转）；
  `run()`=`run_modal_solve`：prepared（模块54 编译产物）→ 真实 ccx 求解——
  求解器取 config ccxPath，二进制捕获、『Job finished』成败门、退出码不作
  判据（solver/tasks.py 与 modal.py 同口径）；run 工件携带 case/dat/frd/log、
  node_count、fixed_nsets。
- `collect()`=`collect_modal_results`：解析复用 modal.py 既有
  `parse_frequencies`/`parse_mode_shapes`（parse_frequencies 逐行 dict 新增
  `eigen_residual` 键=DAT 第 5 列特征值虚部——复模态残差口径，与 modal.py
  既有 |values[3]|≤1e-9 门同源；单零件链路 verify_modal_analysis 复跑
  exit 0 证实无回归）；振型按节点 id 升序组阵、逐阶 max 范数归一
  （同 modal.py 口径）；`validate_modal_results` 机判（频率正/振型
  (modes,n_nodes,3) 归一/残差<1e-8）；固定 NSET 边界判据与
  verify_modal_analysis:25 同口径（振型在固定节点幅值 <1e-8×峰值），
  INP 的 NSET 从 parse_mesh_inp 独立取得。
- 状态推进与历史：case 目录写 `verification.json`
  （state=computed_with_evidence + 频率/残差/边界比/耗时）并追加
  `run_history.json`（每条 run 记录 state=computed_with_evidence、
  previous_state=configured_not_computed）。准备包 manifest 保持
  prepared_not_computed 不改写（冻结输入包；运行状态在 case 证据与历史中
  如实标注——两层状态不混写）。
- GUI 三态（`advanced_dialog._update_solve_button`）：后端未注册→
  『尚未接通』；已注册且引擎已接通但工作台求解编排（提交/进度/取消 UI）
  尚未接线（`_solve_orchestration_ready()=False`，模块55 只接适配器层）→
  按钮仍禁用、文案『求解（后端已接通；工作台求解编排尚未接通，这里不会
  启动求解）』——不虚报可点求解。verify_workbench_ux ⑤ 与模块54 门
  （禁用+『尚未接通』断言）原语义保持，两门复跑 exit 0。

## 2. B2 框架门续推进（verify_advanced_framework.py:102 起，源内标记可追溯）

- 模块54 修订块基础上再推进：modal 断言 `engine_connected is True`
  （真实后端注册）；modal run/collect 的『尚未接通』断言移除（已接线，
  实跑证据由 verify_assembly_modal_solve.py 承载——framework 门禁进程
  不解算）；其余四类（harmonic/transient/mechanism/nonlinear）永远保持
  未注册抛 BackendNotConnected；register_backend 无效接口断言保留。
- 连带修订 verify_assembly_modal_compile.py（模块54 门）B2 块与 ③ 节
  （源内『模块55 B2 续』注释标记）：能力位断言推进为 True；modal
  run/collect 抛错断言替换为『已接线』断言 + 未注册 kind 的 run 仍抛
  『尚未接通』；基类 prepare 抛错断言保留。修订后复跑 exit 0。

## 3. 验收（`verify/verify_assembly_modal_solve.py`，exit 0 = 通过）

2026-11-03 实测 exit 0（日志 verify/logs/verify_assembly_modal_solve.log；
证据 E:/rm_sim/verification/assembly_modal_solve_20261103_210246_855594）：

- 夹具口径（如实）：串联两杆（samples/assembly_serial_rods.step，杆1
  x∈[0,100]、杆2 x∈[100,200]，各 10×10×100，样例本身不带材料——材料在
  工程侧按部件指定，make_assembly_samples.py 实读）。本夹具两杆同指定
  45钢（E=210000 MPa，ρ=7.85e-9 t/mm³）→ 等效均匀悬臂梁口径成立
  （L=串接总长 200mm）；每部件仍各出自己的材料/密度/截面卡。固定 x=0
  端面，杆间 x=100 界面粘接 *TIE（1 条，模块31 绑定带配方），modes=6。
- 全产品链路实跑：准备包 → prepare（22388 节点/12898 单元）→ run（真实
  ccx_launcher.exe，Job finished，exit 0，3.585s）→ collect。
- ① f1(FEM)=208.992900 Hz vs 等效悬臂梁理论 f1=208.879149 Hz
  （f1=1.875104068711961²/(2πL²)·√(EI/(ρA))，L=200，同
  verify_modal_analysis.py:10 公式）偏差 **0.054% ≤3%**；全部频率
  [208.99, 208.99, 1294.76, 1294.77, 3561.5, 3561.5] Hz（弯曲对简并，
  物理合理）。
- ② 特征值残差全部 <1e-8（max=0，n=6）；DAT 独立重解析与 collect 一致。
- ③ 固定 NSET（FIXED_FACE_1）振型幅值/峰值 max <1e-8（独立复算 +
  collect 内置判据双过）。
- ④ 每阶振型峰值=1（np.allclose）。
- ⑤ case 目录 modal.inp/dat/frd/log/verification.json/run_history.json/
  mesh.inp 齐全非空；verification 与历史条目 state=computed_with_evidence
  （previous_state=configured_not_computed）；准备包 manifest 保持
  prepared_not_computed。
- ⑥ verify_advanced_framework.py 复跑 exit 0（B2 续）+
  verify_assembly_modal_compile.py 复跑 exit 0（模块54 门同步修订后仍绿）。
  另：verify_workbench_ux.py、verify_modal_analysis.py 复跑 exit 0
  （advanced_dialog 与 parse_frequencies 变更的既有绿门回归）。

## 4. 范围与诚实声明

- 「特征值残差」口径=ccx DAT 特征值输出第 5 列（虚部）——ccx 频率步不输出
  Sturm 回代残差；该列即模态复数性残差，与 modal.py 既有实模态门同源，
  本次实测全部恰为 0。如需刚度-质量残差 ‖Kφ−λMφ‖ 需组装矩阵，属后续。
- 等效理论口径：两杆同材料使「等效均匀悬臂梁」精确成立（异材串联无统一
  解析式，任务书公式即为均匀梁式）。多部件多材料（如模块54 夹具的
  Q235+6061）的整装模态求解链路相同，但理论对照口径不同，未在本模块设门。
- 工作台求解按钮仍未接线编排（三态文案如实）；run/collect 的调用方当前
  是后端 API 与验收脚本。
