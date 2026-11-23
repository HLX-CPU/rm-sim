# 模块 27：整装自动连接与规模闸门

> 2026-11-02 14:23 接续核验：本模块的自动候选不等于整装求解通过。
> 下文旧流水线的19.21 GiB记录不能归因于网格解析；23.6万节点完整准备峰值
> 复测为1.10 GiB，CCX峰值仍未知。默认材料和固定位置已修正。
> 最新事实、失败产物与剩余工作见 `analysis/DS接续核验与修复进展_2026-11-02.md`。

状态：已交付（2026-11-02）。对应代码：`cad/connection_draft.py`、
`cad/draft_job.py`、`core/connections.py#connections_from_draft`、
`gui/connection_draft_dialog.py`、`solver/scale_guard.py`。
验收：`verify/verify_connection_draft.py`（默认回归）、
`verify/verify_assembly_auto_connections.py`（整装，慢速手动跑）。

## 1. 解决什么问题

整装 20 实体如果靠界面逐对手动配连接，需要点几十次且极易漏配；而
`build_assembly_case_inp` 的连接性门（模块 17）会直接拒绝含悬浮部件的装配，
所以「连接配不全」= 「根本算不了」。本模块把几何检查串成一条链路，一次产出
全部贴合面候选，用户只在审核表里确认。

## 2. 算法（全部复用已实证函数，不新写几何算法）

```
draft_assembly_connections(model, project, planar_only=False, min_area_mm2=0.05)
  1. 实体两两组合 → bbox 预筛（gmsh.model.getBoundingBox，不相交直接跳过）
  2. assembly_checks.check_assembly_geometry(pairs=预筛后) → 保留 zero_distance / interference
  3. assembly_checks.find_contact_faces(pairs=贴合对, planar_only, min_area_mm2)
     → OCC 面求交的精确公共面补丁
  4. 每个补丁 → 一条候选连接（按公共面积降序，单对超过 max_patches_per_pair 截断并记录丢弃面积）
  5. 零距离但无补丁 → point_or_edge（点/线接触，不建连接，交人工判断）
```

整装实测（`assembly_thread_partition.step`，20 实体）：

| 阶段 | 数量 |
|---|---|
| 实体对组合 | 190 |
| bbox 预筛后进入 OCC | 30 |
| 几何贴合（zero_distance） | 22 |
| 公共面补丁 → 候选连接 | 151（销轴类 134、粘接类 17） |
| 点/线接触（未建连接） | 0 |
| 几何/补丁错误 | 0 |
| 部件覆盖 | 20 / 20 |
| 耗时 | 约 12 分钟（单进程 OCC） |

拓扑摘要：4 根螺纹销钉与定心体各 32 个补丁（128 条），其余为定心各体之间、
壳体与端片、6 个轴承与定心孔（各 1 条）、钩与端片。

## 3. 推荐类型与方向（可解释、可覆盖）

| 规则 | 推荐 | 依据 |
|---|---|---|
| 部件名含 轴承/bearing（且不含销/轴） | 粘接 | 滚动体与游隙不建模，按整体钢件近似 |
| 部件名含 销/销钉/轴/pin/shaft | 简化销轴 | 零间隙绑定（`PinConnection`） |
| 其余贴合 | 粘接 | 界面不分离、不滑移；学生级静力最常用且最容易收敛 |

**不给「可分离接触」做默认**：整装界面数量大，接触非线性在单步静力里几乎不可能
收敛；需要接触语义的对由用户在审核表里显式改。

**从面（slave）方向**：模块 19 的 588 条 gentiedmpc 实证——从面必须是销面、主面
是孔壁，反用会得到 "no tied MPC" 的绑定失效。因此含销/轴的部件对固定取销侧为
slave；其余贴合面取面积较小的一侧作 slave（网格通常更细，投影更容易落在主面内）。

## 4. 跨进程安全：面指纹而不是 occ tag

后台作业（`cad/draft_job.py`，独立进程跑 OCC）与 GUI 各自 import 同一个 STEP，
occ tag 不承诺一致。草稿因此为每个候选面记录指纹（面积 + 形心 + 法向），应用时
按指纹解析：

- 指纹解析**限定在该候选所属部件内**（`_draft_face` 传入该部件的面清单）。
  原因：贴合界面两侧面积与形心常常完全相同（例如两杆串联的对接面），而指纹的
  法向比较用 `1-|n1·n2|`，平行反向也算匹配——全模型解析必然 ambiguous，
  部件内解析才唯一。这条与 `project_workflow.reresolve_project` 的挂载口径一致。
- 指纹不唯一命中 → 显式报错，绝不猜一个 tag 贴上去（会把连接建到错误的面）。

## 5. 规模与内存闸门（`solver/scale_guard.py`）

2026-11-02 实测事故：整装 3 mm 档网格 819 602 节点 / 492 610 单元，
`prepare_analysis` 里 `parse_mesh_inp` 把 99.5 MB 的 INP 解析成 Python dict
就常驻 **19.21 GiB**；随后 CCX 求解器启动还要再吃一份，31.2 GB 物理内存被吃光、
系统疯狂换页导致整机无响应。

该历史记录的阶段归因未得到复测支持，暂保留原保守上界作为硬闸门：

| 量 | 来源 | 用法 |
|---|---|---|
| Python 侧内存 | 实测标定 25.2 KB/节点（819602 节点 → 19.21 GiB） | `python_peak_gb(nodes)`，判 refuse |
| CCX 求解峰值 | **未标定**（`CCX_CALIBRATION` 为空） | 只返回 None，不编造数字 |

判定位置在 `prepare_analysis` 里 **read_text 之前**——此时只持有网格文本与统计
（内存还低），超限直接抛 ValueError 并给出「放大到约 X mm」的建议，网格文件保留。
GUI 侧再加一道：`refuse` → 严重警告框并禁止提交；`warn` → 询问后可继续。

阈值：Python 侧占用超过本机可用内存 85% → refuse，55% → warn。

## 6. 验收

`verify/verify_connection_draft.py`（双叠块 `assembly_serial_rods.step`）：

1. 草稿识别出 1 条贴合候选，公共面积 = 杆截面 100 mm²（OCC 求交，误差 0.0000%）；
2. 界面法向平行反向，推荐粘接，slave 取面积较小侧；
3. `connections_from_draft` 构造 1 条连接，validate 通过；重复应用去重（added=0、skipped=1）；
4. `prepare_analysis` 通过 face 指纹 re-resolve，deck 含 `*TIE`；
5. **真实 ccx 求解**：端部平均 Ux = 0.0193139 mm vs 串联双材料理论 0.0192757 mm，
   误差 **0.198%**（证明自动生成的连接真的在传力）；
6. 去掉全部连接 → 连接性门显式拒绝（不允许把悬浮件交给求解器）。

`verify/verify_assembly_auto_connections.py`（整装 20 实体，不网格不求解）：
覆盖 20/20 部件、候选 ≥ 20、无错误、销轴 slave 侧正确、连接对象全部可构造且名称唯一。
该脚本是分钟级 OCC 检查，不进默认回归，用 `run_regression.py --slow` 触发。

## 7. 已知限制

- 只识别**零距离贴合**；正间隙（例如设计留 0.1 mm 间隙的配合）不会被识别为连接。
- 点/线接触只能报告，无法自动建模。
- 一个公共面补丁生成一条连接（销钉-孔壁被螺纹切成几十个小面时会产生几十条
  `*TIE`）；若 CCX 因 MPC 过多失败，降级方案是每部件对只取最大面并在报告标注
  「界面传力受限」。
- 自动推荐只按部件名关键词，改名字就会改推荐；用户必须在审核表里确认。
- CCX 求解峰值内存尚未标定，因此「能不能算完」目前只能按 Python 侧与自由度软线
  给建议，不能给准确预算。
