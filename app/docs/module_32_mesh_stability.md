# 网格高阶优化器的崩溃与可恢复处理

模块 32（2026-11-02）。入口 `mesh/mesher.py`、`solver/workflow.py`；
证据见 `analysis/整装真实求解与网格稳定性_2026-11-02.md` 第 3 节。

## 现象

20 实体整装、4 mm/curvature12/min0.4 配方，同一份代码同一份输入：

* 两次成功（133810 单元、235908 节点、负 Jacobian 0、minSICN 1e-4—6e-4）；
* 两次失败，进程直接被 C++ 运行时终止：

```
terminate called after throwing an instance of 'std::runtime_error'
  what():  Failed to reach critical value in pass 1 for measure(s): ScaledJac
```

异常在 gmsh 的 netgen 高阶优化器内部抛出，native 层 `terminate`，
Python 的 `try/except` 接不住；进程死掉后没有任何网格文件可用。

## 为什么会崩、为什么不能绕开

`optimize("HighOrder")` 里的 netgen 高阶优化是**唯一**能消掉这些负单元的一步：

| 做法 | 结果 |
|---|---|
| 直接生成二阶，只做 `HighOrderElastic + HighOrderFastCurving` | 88 个负 Jacobian、minSICN=−0.374，网格不可用 |
| 一阶网格先 `optimize("Netgen")` → `setOrder(2)` → 保顶点两步 | 74 个负 Jacobian；自动升级配方后仍 18 个，拒绝求解 |
| 原链（二阶生成 + Elastic + HighOrder） | 质量合格（0 负 Jacobian），但 netgen 偶发发散崩溃 |

固定单线程（`General.NumThreads=1`、`Mesh.MaxNumThreads1D/2D/3D=1`）**没有**
消除崩溃，说明不是线程竞争，而是 netgen 自身在薄壁/销孔这类小特征上的数值发散。
单线程的价值是让同一输入走同一条路径、失败可复现、日志可定位；代价是网格阶段
约慢 20%（125—162 s）。

## 本轮实现：进程级可恢复

1. `mesh/mesher.py` 保留原链（Elastic + HighOrder），并在生成前固定单线程；
   注释写明为什么不能删 netgen 这一步、为什么 Python 接不住。
2. `solver/workflow.py`：
   * 后台准备进程以非零码退出且**没有** `error.json` 时，读 stderr 尾部，
     按固定文案识别 `mesh_optimizer_crash`，错误信息写明「网格高阶优化器崩溃
     （gmsh netgen: Failed to reach critical value in pass 1）」+ 退出码 +
     stderr 日志路径；
   * 识别到该类崩溃时自动重试，最多 3 次；每次把加密配方改保守一档
     （curvature×0.85、min_size×1.2，网格尺寸不变），并把「崩溃文案、日志路径、
     前后配方」追加到 `prepare_attempts.jsonl`；
   * 其它错误（几何变化、材料缺失、内存超预算等）不重试，直接失败；
   * 取消请求优先于重试。
3. GUI 无需特殊处理：准备在后台线程 + 独立进程里，崩溃只让本次任务失败，
   窗口保持可用，任务状态里带分类后的错误与日志路径。

## 未完成（阶段 2 剩余）

本节为原阶段记录。后续局部网格、质量表和手动重试已接入，见module_35_local_mesh_quality.md；
自动重试也已改为保留旧快照和日志。原生崩溃具体元素定位仍不能由现有文案确定。

* 按面/边/部件的局部加密（网格尺寸场）尚未实现；
* 崩溃后除了自动换配方，没有界面级的“重试/换配方/放弃”选择；
* 没有把崩溃定位到具体面或元素（只有 netgen 的 measure 名）；
* 网格质量报告（负 Jacobian/minSICN 分布、最差单元位置）还没有进 GUI。

## 依据

* gmsh 4.15 的 `optimize("HighOrder")`/netgen 高阶优化行为（实测日志见
  `E:\rm_sim\verification\current_assembly_solve2_run.log`、
  `current_assembly_solve3_run.log`）。
* 两段式与保顶点方案的对比记录：`meshcheck_run.log`、`mesh_two_stage.log`、
  `mesh_single.log`。
