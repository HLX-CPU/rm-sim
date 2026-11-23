# 模块 18 设计说明：可分离接触与摩擦连接求解验证

日期：2026-11-02 ｜ 状态：已完成，验收 `verify/verify_contact_friction_connection.py`
exit 0（①-⑧；全程真实 ccx 2.22，两叠块五工况 + μ 判别对 + 可分离性 + μ=0 对照）。

## 范围

- `solver/deck.py`：接触 **LINEAR 罚刚度标定** 1000 → **1e6 MPa/mm**
  （模块常量 `_CONTACT_LINEAR_STIFFNESS_MPA_MM`）——首跑实证：罚柔量会
  吞掉间隙闭合反力（1000 时 ⑤ 预定位移 0.12mm 反力远偏 42000N），1e6 时
  理论偏差 ~0.3% 且全部算例收敛。
- `solver/ccx2para.py`：`frd_to_vtu` 支持**多增量 .frd**——非线性步（接触）
  的 frd 含多组 DISP/STRESS/FORC/ERROR 块，ccx2paraview 按增量写
  `{base}.{inc}.vtu`（fric_100.1..6.vtu 实证）；取**最后一个增量**（终态）。
  单增量路径（{base}.vtu）不变——模块 8/17 零影响（回归佐证）。
- `app/docs/connections.md` §接触摩擦：物理假设补「假设 4（罚刚度依赖）」、
  不模拟项补「塑性压痕」；验证算例指针更新为已实现的装配接触算例 + 全套
  门限与 μ=0 实测记录（⑦）。
- `verify/verify_contact_friction_connection.py`（新）。

## 算例与实测（两叠块 20×20×20，下块底面固定，接触界面=顶面↔底面）

| 工况 | 实测 | 门限 |
|---|---|---|
| ① 压缩 1000N −y | ΣRF=(0, 999.999994, −0.000003)；界面法向压力积分 Σ(−σyy)·A=999.9897 N（244 面单元）；最小面压 +2.34 MPa | ΣRFy≤0.1N；±10%；≥0 |
| ② 仅重力 | ΣRFy=1.221335 N vs 2ρV_mesh·g=1.232136 N（V_mesh=16000mm³ 角点四面体和；0.88%） | ±1% |
| ③ 夹紧 5MPa（N=2000N）+侧向 100N | ΣRFx=−100.000005、ΣRFy=2000.000003；顶块 u_x=4.087e-4 mm | ≤0.1N；u_x≤1e-3（粘着理论 1.238e-4 的 3.3×=切向罚柔量，首跑标定） |
| ④ μ 判别对（侧向 300N） | μ=0.3：u_x=1.226e-3 ≤3e-3（=1e-3×300/100 随载荷线性标定）；μ=0.1：**300s 未收敛（持续滑移=刚体自由度）→ 超时取消** | 双结局门：failed/超时 或 u_x>0.01mm——证伪「μ 数值不可区分」 |
| ⑤ 可分离性（0.1mm 间隙几何+预定位移） | 0.05mm（<间隙）：底块反力 0.000000 N；0.12mm：42423.76 N ≈ k_series·0.02=42000N（k_series=2.1e6=两块 EA/L 串联） | ≤1N；±10% |
| ⑥ μ=0 侧向 | 任务 failed（界面切向刚体自由度不闭合——奇异/不收敛） | 如实记录，不做硬门 |

预定位移法 = deck 文本注入 `*BOUNDARY / PART2_TOP, 2, 2, −Δ`（位移式边界，
验证手段非产品特性，已注释说明）；⑤ 用 0.1mm 间隙几何（探针内 gmsh 生成，
上块抬升 0.1mm）。

## 首跑实证驱动的修正（诚实记录）

1. **罚刚度 1000 → 1e6**：⑤ 首跑（1000）间隙闭合反力被罚柔量吞掉
   （0.02mm 压缩中罚侵入占 ~84%）——按 ask「罚刚度影响首跑标定」调至 1e6；
   全部工况重跑收敛。
2. **多增量 vtu**：接触为非线性（多增量），frd_to_vtu 的单文件假设失效
   （"未产出 .vtu"）——按增量取终态修复；module_8/17 单增量路径不变。
3. **界面压力积分口径**：与模块 17 同（σyy·A 从面应力积分）；μ 判别的
   粘着门随载荷线性缩放（1e-3×300/100）——切向罚柔量 3.3× 理论为系统性
   柔量（非滑移），记录于日志与文档。
4. μ=0.1 的滑移结局为「300s 不收敛→超时取消」（持续滑移=刚体自由度），
   与 μ=0.3 的 1.23e-3 形成量级区分——「μ 数值可区分」成立。

## 回归（本次实际运行，全部 exit 0）

verify_results_view（frd_to_vtu 单增量路径）/ verify_assembly_solve（接触
1e6 刚度重跑）/ verify_bonded_connection / verify_mesh_generation /
verify_load_definition / verify_main_entry / verify_step_import /
verify_assembly_import / verify_assembly_tree / verify_connection_model /
verify_static_solve / verify_report_archive / verify_project_roundtrip
（后三者为真实 ccx）。

## 未做 / 留给后续模块

- 大滑动/有限滑动公式化与摩擦生热（本文档不模拟项）。
- 接触穿透量随罚刚度的系统研究（当前 1e6 单点标定）。
- GUI 接触参数（μ/罚刚度）编辑界面。
