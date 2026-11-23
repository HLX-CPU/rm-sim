# 模块50：验收耗时分解与套件标签（2026-11-03）

依据 `analysis/装配仿真验收提速调研_2026-11-03.md` §7-1 建议实施顺序第 1 项：
**加验收耗时分解和依赖/套件标签，保留原完整验收入口及所有标准**。标签
`fast_config / real_small / reliability / full` 对照调研 §4 分层表，**只用于选择
与报告**，不改变任何既有门、容差与原完整验收入口。pytest/xdist 等开源组件本轮
只评估不引入（调研 §5 口径）。

## 改动

- `verify/suite_labels.py`（新建共享件）：四类标签定义（`LABELS`）、42 项检查
  （run_regression.py DEFAULT 40 + SLOW 2）的**逐项主标签+归类理由**（`CHECK_LABELS`）、
  `parse_labels`（逗号分隔解析、未知标签 ValueError）、`select_by_labels`
  （保序过滤；未登记名不属于任何标签）、`breakdown`（按标签聚合耗时）、
  `coverage_report`（全集划分核查）。口径显式且可机检：
  - **fast_config**（17 项）：不启动求解引擎(ccx)——文案/表单/工程保存/依赖/
    几何与网格解析类；允许真实 gmsh/OCC/Qt（调研 §4 行 1 检查方式即「不启动引擎」）；
  - **real_small**（18 项）：启动真实 ccx 小算例求解（材料、载荷、连接、INP 编译、
    结果解析）；**五门（module_46 口径）全部属 real_small**；
  - **reliability**（5 项）：调度器/取消/线程/内存保护/进程生命周期与守卫故障路径
    （可控夹具，检查对象=运行行为；含真实小算例者逐项注明）；
  - **full**（2 项）：仅显式完整回归/--slow 才跑的整装级检查（SLOW 清单两项目）。
  每检查恰有一个主标签，四类构成全集的干净划分——选择结果因此可精确断言。
- `verify/run_regression.py`（增量）：
  - `--labels` 选择参数：逗号分隔标签过滤当前选择范围（显式点名清单或 DEFAULT）；
    未知标签、过滤后零命中 → argparse error exit 2（零命中提示 full 仅覆盖 --slow
    清单）。无 `--labels` 时选择行为与改前逐行一致；
  - 逐检查输出行追加标签注记：`name [label]: PASS exit=0 N.NNs`（其余行格式不变）；
  - `summary.json` 每行新增 `label` 字段（既有字段与「列表型 JSON」结构不变）；
  - 新增 `timing.json`（每检查秒数行、wall_seconds、labels_selected、by_label 聚合、
    sum_check_seconds）与收尾两行 `TIMING:`/`TIMING_JSON:` 输出（追加在原
    REGRESSION 行之后——原有行序保持在前）。
  - `--framework-only` 与 `--labels` 互斥（新参数组合的守卫，原行为不变）。
- `verify/verify_memory_budget_guard.py`（增量，门与判据零改动）：轮墙钟
  `wall_seconds`、逐门 `label` 字段、每轮每门耗时分解表（日志 §2 表同口径打印 +
  `timing_breakdown.json` + `verification.json.timing_breakdown`）；
  另修正两处对 run_regression.py 的行号引用字符串（env 行 41→57，模块45 脚本的
  DEFAULT 引用 12-19→13-20）——纯引用修正，两脚本逻辑未动。

## 验收（`verify/verify_regression_labels.py`，exit 0 = 通过；日志 → verify/logs/）

- **① 完整回归入口原语义不变**：改前固定样例（`verify/fixtures/
  regression_labels_prefix/`，2026-11-03 改前实拍：带参 advanced_framework、
  --framework-only、--help、DEFAULT/SLOW 清单快照、无参全量 40/40 PASS stdout）
  vs 改后实跑——归一化**只剥本变更允许的新增**（证据目录时间戳→`<EVID>`、
  秒数→`<T>s`、行内标签注记、TIMING/TIMING_JSON 行）后**逐字节一致**；改后无参
  全量实拍（`regression_labels_postchange/noarg_stdout.txt`，40/40 PASS）同规则
  与改前逐字节一致；--help 保留改前全部行、新增 3 行均与 --labels 相关。
- **② 每门耗时落 JSON 且与抽查 wall clock 一致**：
  - run_regression 抽查（advanced_framework×2，重复名=原清单透传语义）：Σ检查秒
    1.99s = 内墙钟 1.99s（timing.json 与 summary.json 行级/聚合精确一致），
    外部墙钟 2.15s（差=解释器启动+证据目录开销 ≤5s 口径）；
  - 五门套件（④ 本次实跑）：3 轮×5 门每门秒数齐全（调研 §2 表同口径），轮
    wall_seconds ∈ [max(门秒)−0.5, max(门秒)+15]，Σ轮墙钟 1000.1s ≤ 外部套件
    墙钟 1006.5s+10，timing_breakdown 矩阵与 rounds 逐门一致。
- **③ 四类标签可按选择参数取子集且选择结果可断言**：42 项被四标签干净划分
  （17/18/5/2，无未知名/重复/空组）；五门∈real_small、SLOW∈full；实跑
  `main_entry static_solve --labels fast_config` 只跑 main_entry、
  `--labels real_small` 只跑 static_solve（互为对方标签，确证过滤真实发生）；
  `--labels bogus_label` → exit 2、`--labels full`（无点名）→ exit 2 并提示 --slow。
- **④ 原五门套件完整重跑 exit 0**：`verify_memory_budget_guard.py` 原样重跑
  （3 轮×5 门并发 + 守卫单测，门内检查与判据零改动）——exit 0，外部墙钟 1007s，
  证据 `E:/rm_sim/verification/memory_budget_guard_20261103_150506_138440`
  （failures=[]；逐门峰值 0.27~1.45GiB、无 0xC0000005）。

## 本轮实测耗时（如实记录，不外推）

- 无参全量回归（改后实跑，40 项）：wall=652.35s（检查合计 652.33s）；
  分解 fast_config=17 项/29.5s、real_small=18 项/592.68s（五门 contact 门 324.7s
  占全局 ~50%）、reliability=5 项/30.15s——「每轮每门秒数」口径下长门一目了然。
- 五门套件完整重跑每轮每门秒数（轮内五门并发，轮 wall=错峰+最长门）：

  | 轮次 | static_solve | assembly_results | bonded | bolt_pin | contact_friction | 轮wall |
  |---|---:|---:|---:|---:|---:|---:|
  | round1 | 8.7 | 37.0 | 17.8 | 36.9 | 329.9 | 333.9 |
  | round2 | 8.6 | 37.4 | 17.6 | 37.3 | 329.3 | 333.3 |
  | round3 | 8.9 | 37.2 | 17.9 | 37.2 | 328.8 | 332.8 |

  与调研 §2 留档（同表 2026-11-03 早间实测 contact 329.3/327.5/329.5）同量级，
  本轮如实复测；**本轮无提速改动，不报告提速倍数**。

## 边界

- 标签只用于选择与报告：不改门、不改容差、不删检查；DEFAULT/SLOW 清单逐项等于
  改前快照（fixtures/regression_labels_prefix/default_slow_lists.json）。
- 第 1 次验收运行 exit=1：唯一失败项为**验收脚本自身缺陷**——①e 顺序核对正则
  未适配改后『name [label]:』行格式（`^(\S+):` 漏配标签注记），功能本身无缺陷
  （同运行 ①/②/③/④ 全部 PASS、五门套件已完整重跑 exit 0，证据
  regression_labels_20261103_144125_013341 / memory_budget_guard_20261103_144125_089475）。
  按脚本自身有错条款修复正则（标签组改为可匹配两种样例格式）后完整复跑 exit 0，
  全程 ≈16.8 分钟（含五门套件 1007s 完整重跑）。
- ①e 的无参对照样例由开发者在本会话实跑捕获（改前 14:15 / 改后 14:30，同机同环境
  OMP_NUM_THREADS=2/PYTHONUTF8=1），验收脚本核对样例存在性、与 DEFAULT 清单的
  顺序绑定与 stderr 为空，缺失即 FAIL——不虚造样例。
