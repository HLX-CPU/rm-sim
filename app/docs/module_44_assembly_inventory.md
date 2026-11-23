# 模块44：装配现状盘点与基线快照（2026-11-03）

接续上一轮中断的装配体开发。任务纪律：**先盘点、不推倒重做**——本模块只读盘点
工作区半成品与遗留问题，产出后续装配功能的验收基线 JSON；**不修改任何求解链路代码**。

## 产出

- 验收脚本：`verify/verify_assembly_inventory.py`（exit 0 = 通过；日志 → `verify/logs/verify_assembly_inventory.log`）
- 盘点 JSON：`E:/rm_sim/verification/assembly_inventory_<时间戳>/assembly_inventory.json`

## 只读自证（两条断言）

1. **禁进程断言**：`subprocess.Popen` / `os.system` / `os.spawn*` / `os.exec*` /
   `os.startfile` 全部拦截；仅放行 `git` 只读子命令（status/diff/rev-parse/log，
   全局选项 `-c` 等成对跳过后校验子命令白名单）。任何其他进程（CAD/网格/求解器/子
   Python）立即抛 `ForbiddenProcess`。
2. **写盘断言**：运行前后各取一次 `git status --porcelain=v1 -z` 快照并断言一致——
   脚本自身只写 verify/logs/（gitignore 内）与项目树外的证据目录 JSON。
   （若并行工作流同时在项目树写文件，快照差异会随失败信息一并打印以供判责。）

## 盘点内容与基线数字（2026-11-03 04:10 实测）

### ① 工作区未提交改动（git 只读实测）

跟踪修改 23 + 删除 1 + 未跟踪 140 ≈ 164 条路径；`git diff --numstat` 实测
**+982/−335（24 个跟踪文件）**。装配链路代码：core/connections.py +174/−4、
solver/deck.py +148/−11、solver/tasks.py +183/−75、solver/assembly_workflow.py
+86/−68（改动行数 154，与任务书『+154』口径一致——任务书按 `--stat` 变更行计）、
solver/fields.py、history.py、mesher.py、materials.py 等；未跟踪新建装配代码 22 个
（bond_band/connection_draft/diagnostics/nodal_loads/traction/workflow 等）。
任务书引用口径（27 文件、+1468/−368）与本脚本实测并列记录于 JSON `task_claim`，
不互相冒充：`git diff` 不含未跟踪文件，且工作区随后续提交演进。

### ② 自动连接草稿遗留

来源：`E:/rm_sim/verification/draft_qr_asm_named_20261102_120005/draft.json` 与
`E:/rm_sim/verification/current_assembly_solve7_c12_20261102_1935/`（20 实体快拆整装
自重求解）。实测：**151 候选 → 39 补丁（最终求解连接数）/ 22 部件对**，覆盖 20/20
部件；`independent_check.json`：报告编号 46，**requires_review（真未绑定）28**；
**另有 18 个『DOF 已被 SPC 固定而跳过绑定』的诊断号单列**（求解器正常行为，不与 28
混算）：707, 710, 712, 713, 11905–11911, 11926–11932。分类细目 no_face=6、
too_far=22、dof_inactive=18；盘点脚本用 `solver/diagnostics.py` 对 .nam/.log 只读
复算并与 independent_check 交叉核对。整装仅自重工况（约 2.177N），不能当强度结论。
证据不可读时 JSON 如实写 `unknown`（不编造）。

### ③ 9 个基线失败回归脚本（2026-11-02 基线，44/53 门通过）

来源：dwfrun-88bb5b72 逐个实跑全部 verify 脚本（日志 22:23–22:27）→ 其续跑
dwfrun-0944b8fb 的 ask#6（基线结果核验员）完成归类；运行库以 mode=ro 只读核对。
9/9 逐个『失败原因归类 + 证据路径』：

| 脚本 | 归类 | 证据 |
|---|---|---|
| verify_assembly_results.py | 内存守卫截停 | verify/logs/verify_assembly_results.log |
| verify_bolt_pin_connection.py | 内存守卫截停（仅销轴段） | verify/logs/verify_bolt_pin_connection.log |
| verify_bonded_connection.py | 内存守卫截停（仅 1mm 收敛点） | verify/logs/verify_bonded_connection.log |
| verify_contact_friction_connection.py | ccx 0xC0000005（exit=3221225477） | verify/logs/verify_contact_friction_connection.log |
| verify_static_solve.py | 内存守卫 + 0xC0000005 混合 | verify/logs/verify_static_solve.log |
| verify_chrono_load_transfer.py | chrono 环境导入（cp313 numpy 污染 3.12 chrono-env） | chrono_20261102_222504_775873/motion/chrono_stderr.log |
| verify_extensions_gui.py | chrono 环境导入（同上） | extensions_gui_20261102_222529_258699/chrono_20261102_222529/chrono_stderr.log |
| verify_full_assembly_solve.py | 编排无参调用（缺 case_dir） | 脚本用法行静态核查 |
| verify_workflow_bundle_relocation.py | 编排无参调用（缺 argv[1]） | 脚本 sys.argv[1] 静态核查 |

即 **2 编排无参调用、2 chrono 环境导入、5 内存守卫/ccx 0xC0000005**；核验结论：无一
指向力学/建模回归（5 个资源型门上一轮全部 PASS）。这些是**基线事实**，修复与否由
后续装配功能模块按此基线对照，本模块不预改任何脚本。

## 边界

本模块不修改任何 `app/` 代码与既有验收脚本；不改工作区半成品；不启动 CAD/网格/
求解器；不清理历史证据。9 个基线失败是否需要修复、以何种顺序修复，属后续装配
功能模块的决策范围。
