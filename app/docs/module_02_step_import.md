# 模块 2 设计说明：STEP 导入与三维选面

日期：2026-11-02 ｜ 状态：已完成，验收 `verify/verify_step_import.py` exit 0（6/6）。

## 范围

- `app/rm_sim/cad/`（GUI 无关新层）：STEP 导入 + 导入即三检（单位/实体/清单）
  + pick 选面接口。
- `app/rm_sim/gmsh_runtime.py`：gmsh 生命周期（引用计数，cad 与后续 mesh 层共用）。
- 样例 `samples/beam_100x10x10.step`（生成器 `cad/make_beam_sample.py`）。
- GUI：「文件→导入 STEP…」，源单位与部件清单写入日志面板/属性面板；
  `Study.geometry_file` 落库。

## 关键决策与证据

1. **单位换算不做硬编码假设**。探针实测（本会话，2026-11-02）：gmsh 4.15.2 导出
   STEP 固定写 `SI_UNIT(.MILLI.,.METRE.)`；把 0.1×0.01×0.01 文件的声明补正为
   `SI_UNIT($,$,.METRE.)` 后导入，bbox 直接得到 100×10×10 —— 即 OCC 读取时
   自动换算到 mm。导入器仍内置**运行时自标定**（首导时用 1×1×1 标定件实测
   「读时换算(边长1000) / 原样读入(边长1)」），两种行为都有对应分支：
   换算型只记录+显示源单位；原样型由导入器 `occ.dilate` 缩放。标定同时自检
   单位解析器（METRE 声明必须解析出 ×1000）。
2. **单位解析器**（`cad/units.py`，纯 regex 无 gmsh 依赖）：SI 带词头/无词头两种
   形式 + CONVERSION_BASED_UNIT 引用图（INCH=25.4×基准因子）；角度/质量单位跳过；
   矛盾声明报错；无声明按 mm 假设并告警。factor_to_mm：mm=1、m=1000、in=25.4。
3. **实体门**：`getEntities(3)` 为空 → `NotSolidError("非实体，无法网格化：…")`；
   单实体体积≤0 同样拦截。错误后不返回任何半成品对象（不进入后续流程）。
4. **表面清单与 pick**：面积=`occ.getMass(2,t)`；法向=质心最近点（getClosestPoint）
   的参数坐标喂 `model.getNormal` 再归一化；occ box 的面法向实测为外向
   （x=100 面 +x、x=0 面 −x，tag 与 probe1 一致：x=0→tag1、x=100→tag2）。
   pick=逐面最近距离取最小，默认容差 1e-6×对角线，实体内部点超差拒绝。
5. **gmsh 生命周期**：`gmsh_runtime` 引用计数；SolidModel 常驻一个槽（pick 零
   启动成本），会话被回收后 pick 按源文件自动重建。已知限制：gmsh 被回收后
   重建依赖源文件仍在原路径。
6. **GUI 无关**：cad 层只依赖 gmsh + numpy；`verify_step_import.py` 整个进程
   无 PySide6 跑通全链路（F11 三进程约束的活例）；layer_guard 已把 cad、
   gmsh_runtime 纳入扫描。

## 验收对应（全部在本次实际运行通过）

| 验收 | 结果（verify_step_import.log） |
|---|---|
| ① mm 梁 bbox 1e-6 / 体积 0.1% / 实体数 / 面清单 | bbox 实测 100.0000002 各向（容差内）、体积 10000.000000、实体 1、6 面 2×100+4×1000、法向单位且轴对齐 |
| ② 米制负例 | 源单位=m（`SI_UNIT($,$,.METRE.)`）、「源单位=m」入日志、换算后 bbox=100×10×10、体积 10000 |
| ③ 壳体负例 | NotSolidError「非实体，无法网格化：STEP 中没有三维实体（dim=3），只有 1 个面/壳/片体」 |
| ④ pick | (100,5,5)→tag2 面积 100 n·x=1.000000；(0,5,5)→tag1 面积 100 n·x=−1.000000；内部点 (50,5,5) 容差拒绝 |
| ⑤ 损坏 STEP | 垃圾文本与截断 STEP 均抛 StepImportError（含「原因: Exception: Could not read file…」+ gmsh 日志尾部） |
| ⑥（自加）GUI 无关 | 进程内无 PySide6 + layer_guard 扫描 cad 通过 |

## 未做 / 留给后续模块

- 视口内三维显示 STEP 与鼠标点选（挂接点：ViewportHost + SolidModel.faces）。
- 装配体多实体工作流（清单已支持多实体，交互/材料按实体挂接属后续模块）。
- gmsh 大模型导入的性能优化（当前同步导入，GUI 线程调用；大文件后续再改后台任务）。
