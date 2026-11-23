# 模块 11 设计说明：工程保存修改工况重算闭环

日期：2026-11-02 ｜ 状态：已完成，`verify/verify_project_roundtrip.py` exit 0
（A/B/C 三进程 + 指纹篡改负例，全部 subprocess 新进程经无 GUI 批处理 API）。

## 范围

- **工程束格式 .rmsimproject**（`core/project_bundle.py`）：zip 容器
  （schema=rm_sim.bundle@1）——`project.json`（数据模型全量，含面指纹）、
  `geometry/<原文件名>`（STEP 副本）、`cases/case_NNN/**`（每次求解完整产物）、
  `format.txt`（随包操作说明；完整规范见本文件末尾）。原子写（.tmp + replace）。
- **面指纹身份**（`core/face_identity.py`）：面积 + 形心 + 法向三元组；
  容差：面积相对 1e-6、形心逐轴 ≤1e-6×模型对角线、法向 1−|dot|≤1e-6
  （含反向——平行反向面由形心区分）。`resolve_face` 唯一命中才恢复
  face_tag；无命中/多命中 → "missing"/"ambiguous"。
- **数据模型**：Constraint/TractionLoad 等挂接面增加 `face: FaceFingerprint`
  （持久化身份；face_tag 仅为运行期缓存）；`Project.is_confirmed/dirty/
  cases(CaseRecord 索引)`；`Study.mesh_size_mm`。
- **工作流编排**（`solver/project_workflow.py`，批处理引擎）：
  新建求解 / 打开+re-resolve / 改载荷（标脏）+重算 / 只读打开 / 尝试求解
  （未确认 → 显式拒绝）。
- **批处理入口**：`main.py --batch spec.json` + `rm_sim/batch.py`
  （动作分发；结果 JSON 写 spec.result_path——验收 A/B/C 三进程断言之用）。
- **GUI**：文件菜单 打开工程…/保存工程/工程另存为… 全部走束格式；打开后
  自动重导入几何副本 + re-resolve（未命中弹「需重新确认」警告）；求解前
  is_confirmed 守卫（不静默贴错面）。

## 面指纹设计（调研报告 §7）

gmsh 不承诺重导入后 occ 面编号不变——持久化身份**禁止裸 tag**。指纹 =
面积 + 形心 + 法向；重开时对工程束内的 STEP **副本**重新导入并逐一匹配：
唯一命中回写 tag；否则工程 `is_confirmed=False` + 显式警告列表 +
**拒绝求解**（不静默贴错面）。同 STEP 同 OCC 重导入为确定性计算，实测
指纹逐位一致（面积/形心/法向差 0.00e+00）。

## 工程束操作说明（节选，全文随包 format.txt）

- 保存：`core.project_bundle.save_bundle(path, project, geometry_source,
  cases_root)`；另存 = 同 API 换路径。
- 打开：`open_bundle(path, extract_dir)` → 数据模型 + 解包的几何/cases；
  随后 `solver.project_workflow.reresolve_project(project, model)` 恢复面挂接。
- 重算：修改参数后 `dirty=True`；调度器对已存在 case_NNN 自动跳号
  （重算永远生成新 case，旧产物不覆盖）。

## 实测数据（verify/logs/verify_project_roundtrip.log，三进程均 exit 0）

| 验收 | 实测 |
|---|---|
| ② 逐字段 | 45钢 / 100N（重开值）/ MeshSize 2.0；指纹 re-resolve 命中面 vs 保存值：面积差 0、形心 max\|Δ\|=0、法向 1-\|dot\|=0 |
| ③ 重算 | case_002 新建；tip_uy=−0.380987 mm（=2×case_001 的 200.00%，对 δ=0.3809524 误差 0.009%≤2%）；ΣRFy=200.000000（±0.02）；case_001 产物仍在 |
| ④ 再重开 | 当前工况 200N；历史 2 case 索引可读且 results.json 均可读 |
| ⑤ 篡改 | 固定面指纹面积 +5% → is_confirmed=False、警告「面指纹未唯一命中（missing，面积 105 mm²）」、拒绝求解 |

## 未做 / 留给后续模块

- 面指纹对曲面/自由曲面几何的区分度（盒体平面面实测充分；复杂几何如出现
  ambiguous，按设计进入需重新确认流程，不做模糊自动匹配）。
- 工程文件版本迁移工具（schema 演进时）。
- GUI 的工况编辑器（修改载荷数值的表单——当前批处理 API 已完整，GUI 编辑
  表单属 UI 完善项；修改→标脏→重算链路已通）。
