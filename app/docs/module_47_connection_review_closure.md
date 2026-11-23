# 模块47：连接复核收口——requires_review 决策闭环（2026-11-03）

自动连接草稿遗留复核欠账（快拆整装 39 补丁/22 部件对，28 个未绑定从面节点，
另有 18 个 SPC 自由度已固定诊断号）按**逐连接**决策收口。决策单元 F1：
`requires_review` 是布尔汇总、`untied_nodes`/`reported_missed_nodes` 是节点计数
（diagnostics.py 实读复核）——28 是节点数非连接数，节点数只作关联证据字段，
不据此自动决策，也不按节点数伪造决策。

## 数据模型（core/connections.py）

- `ConnectionBase` 新字段：`needs_review`（最近一次求解该连接从面出现未绑定
  节点，由 finish_analysis 诊断 coverage 回写）、`review_decision`
  （pending/keep_bonded/switch_contact/remove）、`review_decided_at`、
  `review_evidence`（关联节点数 + 最近求解 case/job）。全带默认值 → 旧工程
  JSON 兼容（store.from_dict 逐字段拾取）。
- `REVIEW_DECISIONS`/`REVIEW_DECISION_LABELS`：pending=未决策 /
  keep_bonded=保留粘接 / switch_contact=改为可分离接触 / remove=删除连接。
- `apply_connection_decision(project, conn, decision)`：枚举校验；
  switch_contact 原位替换为 ContactFrictionConnection（μ=0.3，两侧面指纹对
  保留，复核字段随迁）；remove 从工程删除（返回原连接供落档）。
- `undecided_block_message(project)`：needs_review 且 pending 的连接 →
  中文拦截文案（求解入口与 GUI/批处理同一口径）。

## 求解入口拦截（不静默通过）

`prepare_analysis`（非 mesh_only）在固定面校验后调用
`undecided_block_message`：存在未决策复核项 → `ValueError`（含『未决策』与
连接名清单）。首次求解不受影响（无回写标记）；旧工程字段缺省不拦截。

## 诊断回写与报告（solver/assembly_workflow.py + diagnostics.py + report）

- finish_analysis：coverage 逐连接 `reported_missed_nodes>0` → 连接
  `needs_review=True` + `review_evidence['last_solve']`（case/job/从面节点数/
  未绑定数/SPC 数）；回写后重建 assembly.connections 快照（报告/结果 JSON
  携带复核状态）。
- `collect_tie_diagnostics` 新增 `dof_inactive_node_numbers` /
  `untied_node_numbers`（有界列表）。
- 报告：连接警告节新增「SPC 自由度已固定诊断编号（N 个）：[...]」单列行
  （不与未绑定行混算）；新增「连接复核决策」节逐条呈现 needs_review 连接的
  决策与关联节点数。

## 批处理 + GUI 双路径（solver/connection_review.py + gui/connection_review_dialog.py）

- 批处理：`build_review_records`（工程自身）/`build_review_records_from_case`
  （整装检查证据目录：INP「** 绑定带」逐连接证据——从面总节点/可绑节点/
  贴合区外最近距离；warning_attribution.json 逐连接归属；independent_check.json
  合计口径；.nam/.log 重读 SPC 编号单列）。合计口径交叉核对：逐连接合计 ==
  检查记录 untied_nodes，读不到如实 `unknown`。`apply_review_decisions`
  批处理写回（单条失败记录不中断）。
- GUI：「连接复核…」入口在自动连接草稿对话框（`AssemblyConnectionDraftDialog`
  新增可选 project 参数，main_window 传入）——`ConnectionReviewDialog`
  逐连接列表（needs_review/决策徽标）+ 证据面板（绑定带/关联节点数）+
  三枚举决策按钮，写回与批处理同口径；未决策计数实时呈现。

## connections.md 同步

新增「连接复核决策闭环（模块47）」节：needs_review 回写语义、未决策拦截、
决策枚举（界面标签与 `REVIEW_DECISION_LABELS` 逐字一致）、证据展示、合计
口径与 SPC 单列；并写明决策不改变物理假设本身（改可分离接触即适用
§可分离接触+摩擦 全部假设与限制）。

## 验收（verify/verify_connection_review_closure.py，exit 0 = 通过）

① 真实整装证据（current_assembly_solve7_c12_20261102_1935，只读）：39 条
记录 / 部件对去重 22 / 决策枚举默认 pending（无人决策不伪造）/ 每条含
reported_missed_nodes+slave_surface_nodes / 逐连接合计 28 == 检查记录
untied_nodes / SPC 18 号单列且与未绑定编号零交集 / 绑定带证据 39/39；
② 三块部分搭接夹具（band=0 → 两连接均出现未绑定警告）：保留粘接 + 改接触
批处理写回 → 重求解：拦截清单空、C2 绑定警告消失（接触段 σ_nn≤0 由
*CONTACT PAIR 施加；净法向力压缩正口径 +297.35N ≥−1e-6 N，张力部分 0.038N
为接触边界角点平均伪差，留档）、平衡残差 0.0000% ≤0.5%、束保存/重开往返
保留复核字段、决策重置 pending 后再次拦截（拦截随状态）；
③ 求解入口负例：needs_review 未决策 → ValueError 含『未决策』+连接名；
④ 真实 case 归档 results.json（报告 ctx）注入重读 SPC 编号重建报告 →
md 含「SPC 自由度已固定诊断编号（18 个）」单列行，与「46 个不同编号」
未绑定行分开；连接复核决策节呈现；
⑤ connections.md diff 可见且枚举标签与实现一致。
GUI 路径：offscreen `ConnectionReviewDialog`（副本工程）列表徽标/证据面板/
决策写回/未决策清零。

## 边界

- 拦截只针对「出现过未绑定警告的连接」（needs_review），首次求解与无警告
  连接不受影响；现有验收（tie_diagnostics/connection_draft/report_archive/
  connection_model/bond_band 复跑全过）不受影响。
- 真实整装 39 条的决策仍是 pending——决策由使用者做出，本模块提供机制与
  记录口径，不代替决策。
