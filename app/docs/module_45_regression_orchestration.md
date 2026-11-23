# 模块45：修复「回归编排无参调用」（2026-11-03）

2026-11-02 基线回归（44/53 门，见 module_44）9 个失败脚本中 2 个归类「编排无参调用」：

- `verify/verify_full_assembly_solve.py`——带参只读复核工具，用法 `<case_dir>`（脚本
  用法行），无参被调用时 argparse 报错退出 2，未做任何复核；
- `verify/verify_workflow_bundle_relocation.py`——带参迁移复核工具，`sys.argv[1]`
  取后台工作流完成记录，无参调用 IndexError，未做任何复核。

两脚本均**不在** `verify/run_regression.py` 的 DEFAULT 40 项清单内（DEFAULT 含
assembly_solve 不含这两者，`run_regression.py:12-19` 实读核对）——基线失败来自
显式点名的基线运行，不是默认回归。

## 修复内容

两工具各增加**无参自检模式**（带参模式行为不变，见下「③ 对照」）：

1. `verify/orchestration_cases.py`（新建共享件）：
   - `find_latest_assembly_case()`——只读定位最近整装归档 case（E:/rm_sim/verification
     下 case 目录须 `<job>.inp/.dat/.frd/.log/.vtu` 齐全、`Job finished`、`*DLOAD GRAV`、
     `ELSET=PART<n>`；按 .frd mtime 取最新；排除 `orchestration_fixture` 标记目录——
     那是 ③ 对照固定样例，不算「最近归档」）；
   - `find_latest_workflow_record()`——只读定位最近可用的后台工作流完成记录
     （`*/verification.json` 含 `complete_work` 且 completed.json 与旧工程 cases/ 在盘）；
   - `ensure_minimal_assembly_case()` / `ensure_workflow_record()`——现做回退：最小
     两块装配（assembly_two_blocks，界面粘接+底面固定；前者加自重走
     prepare_analysis+SolveScheduler，后者加面力走 WorkflowScheduler 真实
     prepare/solve/collect 产出 completed.json）。幂等：固定根已有完整产物则复用。
   - `RM_SIM_NOARG_EVIDENCE_ROOT` 环境变量：仅供验收脚本把证据根指向空目录，
     真实演练「无归档→现做」回退路径。
2. `verify_full_assembly_solve.py`：`case_dir` 改 `nargs='?'`；无参时定位→复核门按
   任务书口径收紧为 **ΣF/ΣM ≤0.5%**（带参默认 2%/1% 不变），复核记录自动落
   `full_assembly_solve_noarg_*` 证据目录；定位/现做失败→非零退出如实报告。
3. `verify_workflow_bundle_relocation.py`：无参时定位→（无记录）现场跑最小后台
   工作流产出完成记录→迁移复核；失败→非零退出如实报告。

## 验收（`verify/verify_regression_orchestration.py`，exit 0 = 通过）

- **① 无参自检**：两脚本显式点名无参运行（PYTHONUTF8=1、OMP_NUM_THREADS=2）各自
  exit 0，stdout 声明所用 case 目录/完成记录与结论（`VERIFY_*: PASS`）；
- **② 复核门**：无参运行落盘的 review 记录 ΣF/ΣM ≤0.5%、`displacements_finite`、
  `no_runaway_part`（位移有界）；绑定警告**记录在案不阻断**——静态核查 ok 判定
  =checks 全真且 checks 不含 `requires_review`（`untied_within_tolerance` 是 0.5%
  比例筛查门）；真实整装实测（快拆 20 实体自重 case，28 个未绑定警告在案、
  requires_review=True）：ΣF 残差 0.004%、ΣM 0.006%、max|U|=1.23e-4 mm 有界，
  PASS——与既有实测 ≤0.06% 口径一致；
- **③ 带参对照**：固定样例（orchestration_fixture_2block_grav 自重 case；既有后台
  工作流完成记录 background_workflow_20261102_222343_962990/verification.json）
  带参输出与**修复前预拍**（verify/fixtures/orchestration_prefix_*，2026-11-03 修复
  前实测）逐字节一致（迁移工具按时间戳证据目录归一化 + verification.json 语义
  字段相等：旧目录零读取、case 哈希数、rf_sum_n）；
- **④ 回退演练**：空证据根下两脚本无参运行真实现做（一次直接两块自重求解 +
  一次两块后台工作流）后复核 PASS，复核门同样 ≤0.5%；
- **⑤ 口径记录**：两脚本当前不在 run_regression.py DEFAULT（在清单=False×2）——
  **是否扩入由用户确认后执行，未自动扩**。

## 边界

- 带参模式字节级不变（③ 对照证明）；未改任何产品（app/）代码；未改
  run_regression.py。
- 无参模式会读 E:/rm_sim/verification 全量 case 目录（只读）；回退路径会启动真实
  ccx / 后台工作流进程（仅在无归档可用时）。
- 「诚实失败」分支（定位失败且最小求解也失败→非零退出）按代码路径实现，验收未
  注入求解失败场景（不破坏 config/求解器）；其触发条件与打印内容见工具源码。
