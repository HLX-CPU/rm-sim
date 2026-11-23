# 模块 5 设计说明：约束与载荷定义

日期：2026-11-02 ｜ 状态：已完成，`verify/verify_load_definition.py` exit 0（8/8）。

## 范围

- `core/project.py`：约束/载荷多态数据模型（重写模块 1 的骨架）：
  - `Constraint(kind="fixed", face_tag)`：固定区域，挂接 occ 面 tag。
    **简化假设=理想固定**（必须随结果告知用户）：挂接面全部节点三向平动
    位移为零——不含转动自由度、不含螺栓预紧/接触柔性，刚度高于真实；
  - 载荷四类：`TractionLoad`（面力，总力+方向，语义=**总力均分到面**，
    调研报告 §4：防止「总力 vs 每面力」重复施力歧义）、`PressureLoad`
    （压力，沿面法向，正值压向面内=CalculiX P 约定）、`MomentLoad`
    （力矩，面/轴+大小）、`GravityLoad`（重力，g 方向可配，mm/s²）；
  - `validate()` 逐类校验（负总力/零方向/零压力/零力矩/零 g/负 face_tag/
    未知约束类型/空名均拒绝）+ `LoadCase.is_solvable()`。
- `core/store.py`：注册表加入四类载荷（`__type__` 多态序列化，工程
  保存/重开自动还原具体类型）。
- `solver/convert.py`：**语义红线落地**（调研报告 §7）：
  - 压力 → `PressureDLoadRef(kind=element_face_set)`——加载面所属**单元面集**
    （(单元索引, 中性局部面索引 0..3)，单元面数>0），**不是节点力列表**；
    .inp 的 *DLOAD P 面码映射（P1..P4）由求解模块 deck writer 完成；
  - 面力 → `CloadPlan`（*CLOAD，总力均分到面节点；等效面压=总力/面积
    仅作核对视图，不写 *DLOAD）；
  - 力矩 → `MomentCouplePlan`（f_i=(M/S)·(m̂×r_i)，ΣM·轴=M 精确守恒；
    合力与非轴矩残差随结果如实报告）；
  - 重力 → `gravity_vector = 单位方向×g`（*GRAV）。
  - `build_face_element_map`：体积网格边界面（probe8 配方+局部面索引追踪）
    按 occ 面 bbox 唯一归类，歧义时用 `SolidModel.point_to_face_distance`
    裁决——这是「压力挂到单元面而非节点」的几何基础。
- `cad/step_import.py`：`face_by_tag`（约束/载荷反查面几何）、
  `point_to_face_distance`（归类裁决）、`volume_mesh`（演示/自检用体积网格，
  提取后清网格；正式网格模块交付后由其接管）。
- GUI：工况树以 `summary()` 展示新模型（含「理想固定（未挂接）」假设标注）。

## 实测数据（verify/logs/verify_load_definition.log）

- ② 等效面压 = 1.0 MPa 精确（100/100）；CLOAD 12 节点均分，ΣF=(0,−100,0) 精确；
- ③ 压力 2MPa → 14 个单元面引用；等效合力 (−200,0,0) N 精确（沿 −外法向）；
- ④ ΣMz = 5000.000000 N·mm（1e-6 内）；非轴矩残差 My=−215 N·mm 与合力残差
  1.49e-13 N 如实随结果报告（12 节点网格非对称所致，求解模块可用更密网格收敛）；
- ⑥ is_solvable：空/仅载荷/仅重力/未挂接约束 → False；已挂接固定 → True；
- ⑧ 压力引用 kind=element_face_set、14>0、无节点力字段；*GRAV=(0,−9810,0)；
  全部 6 个 occ 面 >0 单元面覆盖。

## 语义边界（诚实声明）

- 理想固定是简化假设（结果偏刚），后续接触/螺栓模块才引入柔性施加。
- 力矩线性分布的非轴矩残差依赖网格对称性；对轴矩恒精确。
- is_solvable 只做静力可解性（约束）判断；求解级数值门（真实 CalculiX 运行、
  单元面码映射、.inp 写出）属于「CalculiX静力求解与任务调度」模块。
- 层间关系：convert.py 不 import cad（occ 面信息 duck-typing + distance_fn
  回调由入口编排注入）；cad.volume_mesh 返回 mesh.MeshData（共享数据结构模块）。

## 未做 / 留给后续模块

- .inp 文本写出、求解任务调度、结果回收（下一模块）。
- 约束/载荷的 GUI 编辑器（当前工况树只读展示；挂接操作走选面交互联动，
  属性面板编辑属后续 UI 模块）。
- 接触/螺栓约束、轴承载荷、远程力矩等扩展类型。
