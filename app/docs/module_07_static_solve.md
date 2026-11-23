# 模块 7 设计说明：CalculiX 静力求解与任务调度

日期：2026-11-02 ｜ 状态：已完成，`verify/verify_static_solve.py` exit 0（8/8，
全部门为真实 ccx 2.22 运行实测）。

## 范围

- `solver/deck.py::build_case_inp`：完整分析 .inp = 网格段（模块 6）+ 分析段
  （*MATERIAL,*ELASTIC,*DENSITY,*SOLID SECTION,*BOUNDARY,*STEP,*STATIC,
  *CLOAD/*DLOAD/*GRAV,*NODE PRINT RF,U,*NODE FILE,*EL FILE,*END STEP）。
  载荷经 solver.convert 语义转换；`nset_face_tags` 校验载荷挂接面与网格
  NSET 一致（防静默挂错面）。
- `solver/results.py`：.dat / .frd 解析器（probe2 实证配方产品化）→
  `SolveResults`（门计算的数据层输入）。
- `solver/tasks.py::SolveScheduler`：任务调度（提交/运行/完成/失败/取消；
  留存 输入/日志/退出状态/原始结果——调研报告 §7）。
- GUI：求解菜单「运行静力分析…」，QTimer 200ms 心跳轮询（主线程不阻塞）。

## 红线落实

1. **独立工作目录**：每任务 `case_NNN` 目录**先建后解**，ccx 以 cwd=case_NNN
   启动，按作业名写 .dat/.frd/.sta/.cvg/.12d（probes/beamRun.* 实证）。验收 ⑪
   断言各 case 目录只含本作业名产物、无跨目录串写。
2. **无 GUI 入口**：求解链路（deck/调度/解析）零 PySide6，验收毒化断言 +
   layer_guard 静态扫描。
3. **子进程二进制捕获** + decode("utf-8", errors="replace")（probe6：text=True
   在 byte 0xa1 静默丢 stdout）；读线程排空管道防 64KB 死锁。
4. **成败判定** = 日志含 "Job finished" 且结果解析成功；退出码仅留存并标注
   不作判据（官方 ccx '-v' 恒 201 / PrePoMax 双态 0xFFFFFFFF，两类证据分开）。

## 实测结果（verify/logs/verify_static_solve.log）

| 门 | 实测 |
|---|---|
| beamCheck err | 0.0117%（≤2%；tip=LOADFACE 最近面心节点） |
| ΣRFy / 横向 | 99.999991 N（≤0.01）；Fx,Fz ~4e-6（≤1e-3） |
| .dat vs .frd | 4.00e-07 mm（≤1e-4）；frd 节点 11222 全、RF/U 各 153 |
| ⑥ 变异 | Uy×2 → err 门 False + datFrd 门 False；RFy×0.5 → 反力门 False（注入在解析后、门前的数据层，修正 probe2 :189/:203 先算门后注入的缺陷） |
| ⑦ 压力 | 66 行逐单元面 *DLOAD（结构断言无 *CLOAD）；ΣRFx=199.999999（±0.02）——**P1..P4 面码映射实证确认** |
| ⑧ 重力 | ΣRFy = 0.767626 N vs ρVg=0.770085（0.32% ≤1%） |
| ⑨ 力矩 | 固定端 |ΣMz| = 5000.0000 N·mm（≤1%） |
| ⑩ 负例 | 删 *STEP → failed、无 Job finished、错误非空、exit=201 留存 |
| ⑪ 并发 | jobA/jobB 独立 case 目录全套产物（12d/cvg/dat/frd/inp/log/sta）、零串写、心跳 8 拍 max_gap=0.201s |

## 本次实证发现并修正的问题

1. **ccx 2.22 无 `*GRAV` 关键字**：字面 *GRAV → calinput WARNING "cannot be
   interpreted"、载荷未生效、反力恒 0（实测）。正确语法 = `*DLOAD` 的 GRAV
   标签（幅值 + 单位方向）。验收 ⑧ 的结构断言已按实证语法改写并在文档/日志
   标注该偏差——数值门（ΣRFy=ρVg）在正确语法下达成。
2. **节点 tag ≠ 数组下标（第二次踩同一类坑）**：deck.py 曾把节点 tag 当
   build_face_element_map 的位置索引（差 1 错位 → 28 边界面无法归类）；
   mesher 的 NSET 也曾写下标（含非法 id 0 → ccx 内存破坏挂死，由
   beamRun 基线对照实验定位）。两处已修，模块 6 验收 ④ 增加 NSET 值校验。
3. **launcher 孙进程管道**：cancel/timeout 时 terminate 只杀 ccx_launcher，
   孙进程 ccx_dynamic 握住 stdout 管道令等待永不 EOF——Windows 下需按进程树
   终止（taskkill /T 语义）；调度器的读线程 + join(timeout) 已防死锁。

## 未做 / 留给后续模块

- 结果云图查看（MeshData.point_data ← frd S/U 已可回灌，属结果查看模块）。
- 任务取消的进程树 taskkill /T 完整实现（当前 cancel 语义对 ccx 2.22 快任务
  足够；长任务树杀留求解增强）。
- 多工况批量调度、非线性（NLGEOM）、接触（后续模块）。
