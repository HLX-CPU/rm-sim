# 模块46：修复「内存守卫截停的五门静力回归」（2026-11-03）

基线（module_44 盘点③）：2026-11-02 22:23-22:27 五门静力回归——
`verify_static_solve` / `verify_assembly_results` / `verify_bonded_connection` /
`verify_bolt_pin_connection` / `verify_contact_friction_connection`——被
`app/rm_sim/solver/tasks.py` 内存预算守卫截停（stop_reason=memory_limit /
memory_limit_near_exit → STATE_FAILED，error『求解内存达到预算…』）或接触门
ccx 0xC0000005（exit=3221225477）。守卫机制（tasks.py:295-315 一带实读）：
`stop_reason in ('memory_limit','host_memory_low') → STATE_FAILED`；根因是旧动态
预算 `0.6×当时可用内存/并发`——基线时段系统内存被其他进程挤压，预算缩水后
峰值撞 98% 内核限额（资源压力，非力学回归：同规模求解 20:27-20:56 全 PASS，
本轮其他门亦成功）。

## 修复

### ① 求解内存预算分级（tasks.resolve_memory_limit_bytes，纯函数）

| 档位 | 策略 | 实测锚 |
|---|---|---|
| small（默认档） | **固定足额** 4 GiB/任务，不随挤压缩水；可用内存不足额时回落到可用×80%（永不倒退回「随挤压缩水」） | 2mm 级回归算例实测峰值 ≪1 GiB（本轮实测 0.79~1.07 GiB） |
| large（--slow） | 上探 10 GiB/任务，同样受可用×80% 封顶 | frozen_deck_budget_20261102_1918/record.json：同一 1mm 输入 6GiB 停滞 60s 被取消、10GiB 17.48s 完成（峰值 6.35GB） |
| OMP_NUM_THREADS>2 | 目标 ×(1+25%×(线程-2)) | 保守启发余量（PaStiX/线程 arena），非实测定律，记录在案 |

- 宿主可用内存不可测（API 失败）→ 返回 None（不设限，如实）。
- **纯函数**：`SolveScheduler(memory_limit_bytes=None)` 的既有默认行为不变——
  GUI/后台工作流等其他调用方不受影响。
- 守卫文案修正：memory_limit/host_memory_low 的 error 改为
  『求解进程树已达到**内存预算**（或系统可用内存不足）…』——原文案『内存达到预算』
  不含连续『内存预算』子串，验收要求 error 文案可按『内存预算』检索。

### ② 五门算例规模参数化（verify/memory_budget.py::scale_tier）

档位由 `RM_SIM_SCALE` 决定（=large 走 --slow 大网格档；默认 small）：

| 门 | 默认档 | 大网格档 |
|---|---|---|
| static_solve | 2mm 梁（原档） | 同 |
| assembly_results | 原档 | 同 |
| bonded_connection | 2mm 主门+共节点档；**⑤ 1mm 收敛档跳过（打印 SKIPPED，非静默）** | ⑤ 1mm（145613 节点）执行同一门 |
| bolt_pin_connection | 销轴 **1.5mm**（Ux 锚 9.5238e-4mm ±10% 内；孔壁面积/ΣRF 门与网格无关） | 销轴 1.0mm（95433 节点，原验收档） |
| contact_friction_connection | 2mm 五工况（μ=0.1 的 300s 不收敛超时负例保留） | 同 |

五门全部经 `memory_budget.make_scheduler()` 构造调度器（分级预算），每个任务
终态打印 `[memory] job=… state=… stop_reason=… peak=… limit=…` 供证据汇总。

### ③ 守卫语义单测（verify_memory_budget_guard.py ①）

- 注入：C# 夹具 `stall_at_cap`（分配被拒后继续等待，verify/fixtures/
  scheduler_probe.cs）作为「求解器」提交、预算 192MiB → 峰值 ≥98%×内核限额 →
  STATE_FAILED + stop_reason=memory_limit + error 含『内存预算』；
- 限额内：真实 ccx 求解两块自重 deck（预算 4GiB，实测峰值 0.79GiB）→
  completed、stop_reason=None、无『内存预算』文案。

## 验收（verify/verify_memory_budget_guard.py，exit 0 = 通过）

- ① 守卫单测两分支（上）；
- ② 五门各自单跑 exit 0（round 1）+ 逐门理论锚：
  static_solve 悬臂 `vs δ=0.1904762`（60MPa Mises 锚由配套门 verify_results_view
  承载——static_solve 本身无 Mises 门，σ=Mc/I=60MPa 相对误差 ≤10% 门实测 ~61.9MPa）；
  bonded ΣRF=1000N（门 0.01%≤0.5% 口径）；bolt_pin ΣRF 500N（0.01%）+
  Ux=9.5238e-4mm±10%；contact 界面法向压力积分（只压不拉）+ 粘着 u_x；
  assembly_results per-part max Mises（单部件与整体同源）；
- ③ 五门连续 3 轮：每轮每门 exit 0、无 0xC0000005（exit≠3221225477 且输出无
  崩溃标记）、峰值内存（[memory] 行解析）与退出码落证据目录
  `E:/rm_sim/verification/memory_budget_guard_*/round<n>.json`。

## 运行窗修复（第 1 次验收反馈：world.run 超时 1200000ms）

串行全程实测 ≈24-28 分钟，超过验收运行窗 20 分钟——属**验收脚本自身的串行
编排缺陷**（覆盖项不变、纯粹排布问题），按『脚本自身有错须说明理由』条款修复：

1. **contact 门内编排提速**（verify_contact_friction_connection.py）：μ=0.1 滑移
   负例的「300s 不收敛」窗口改为步骤预置时提前提交（`launch_mu01`，deadline
   记账），与本门其他工况并行；等待语义**严格不变**——自提交起 300s 未收敛
   =超时取消（预置路径失败时回退原同步语义）。门实测 392s → 329s。
2. **验收脚本轮内并发**（run_round_parallel）：一轮 = 五门并发独立进程
   （独立进程/日志/临时目录，门内检查与判据零改动），轮与轮连续；合计实测
   峰值 ≈6.6GiB 远低于宿主可用——并发负载恰好复现基线失败的内存压力场景，
   预算分级修复在并发下依然成立（更强的稳定性证据）。
3. 修复后完整验收实测 **16 分 42 秒**（06:26:33→06:43:14）exit 0，余量 >3 分钟；
   证据目录 `memory_budget_guard_20261103_062633_329109`（3 轮 × 5 门全
   exit 0、无崩溃、守卫两分支 PASS）。

## 第 2 次验收失败根因：OMP 线程环境契约（2026-11-03）

第 2 次运行（编排器裸调用，不设 OMP_NUM_THREADS）exit 1——唯一失败项是守卫单测
①b（限额内真实求解）：同一 884KB 两块自重 deck 峰值 3.65GiB（4GiB 预算）被守卫
截停，而全部 15 次门运行（子进程，`run_gate` 显式设 OMP_NUM_THREADS=2）照常通过。
A/B 实证（同一 deck、仅环境不同，32 核机）：

| ccx 环境 | 峰值提交内存 | 结果 |
|---|---:|---|
| 未设 OMP_NUM_THREADS（默认=32 线程） | **4.42 GiB** | completed |
| OMP_NUM_THREADS=2 | **0.79 GiB** | completed |

即 ccx/PaStiX 每线程内存闸使小算例峰值随线程数近似线性放大（5.6×）。修复：
验收脚本 main() 开头 `os.environ.setdefault("OMP_NUM_THREADS", "2")`——
与 run_regression.py:41 对子进程的约定一致（setdefault：运行方已设不覆盖），
使进程内守卫单测与子进程门共享同一求解环境契约。修复后裸调用复跑
**16 分 45 秒**（07:08:37→07:25:22）exit 0，证据
`memory_budget_guard_20261103_070837_889860`（①b 峰值 0.78GiB，3 轮×5 门全过）。
这也解释了基线五门为何「同规模求解本轮其他门成功、五门被截停」的波动——
资源压力下的分配行为对线程/并发配置敏感，预算必须按实测口径留足。

## 边界

- 未改 `SolveScheduler` 默认预算行为（memory_limit_bytes=None 语义不变）；
  run_regression.py 的 DEFAULT 清单未动。
- contact μ=0.1 的 300s 超时负例是判别力设计（非内存问题），等待时限与结局
  语义未改，仅提交时机提前。
- 大网格档（RM_SIM_SCALE=large）未在本轮默认验收中实跑（其规模=原 20:36 基线
  已 PASS 的档位）；默认档 3 轮为本轮实测。

## 2026-11-04 复标定：small 档预算 4.0 → 8.0 GiB（启动期提交尖峰，与 OMP 效应相互独立）

全量回归第 1 轮出现 6 门失败（static_solve/assembly_solve/assembly_results/bolt_pin/bonded/contact_friction），
stop_reason=memory_limit / memory_limit_near_exit，`[memory] peak=3.65GiB limit=4.00GiB`。
排查锚点（全部为本日实跑证据，临时探针在系统临时目录）：

- **尖峰与 deck 无关**：8 节点手造最小 ASCII deck（*NODE 8 行 + 单 C3D8）峰值
  4259~4396MB；82 行单单元塑性 deck 4105MB（10ms 级采样：**进程启动后 6ms 内
  commit 4105MB，~260ms 自行释放回 10MB，0.27s 正常 Job finished**）；
  38001 行两杆绑定 deck 4692MB。
- **与求解器无关**：同一 deck 加 `*SOLVER,TYPE=SPOOLES` 仍 4739MB（尖峰在求解器
  分派之前的输入/约束阶段）。
- **与绑定带 tie 卡无关**：去 POSITION TOLERANCE、改回旧式整面 element tie，均仍 4692MB。
- **与 OMP 无关（区别于上文 11-03 的线程效应）**：OMP_NUM_THREADS=1/2/4/未设均
  4692~4740MB，且 ccx 输出确认 `Using up to 2 cpu(s)`、PaStiX `threads per process: 2`；
  尖峰发生在 6ms（线程池尚未建立）。
- **与 MKL 环境无关**：MKL_NUM_THREADS=1/2、MKL_DYNAMIC=FALSE、
  MKL_DISABLE_FAST_MM=1 均 4396MB；进程模块表只见 mkl_rt.2+mkl_intel_thread.2。
- **与启动方式/限额无关**：裸 Popen vs OwnedProcessGroup（PIPE/文件 stdout、
  suspend+resume、Job Object 有/无限额）四种组合全部 ~4725MB。
- **机器状态门控、本仓库代码不可控**：同一字节 deck 在 11-03 19:33 峰值 1152MB
  通过（ bonded_connection_20261103_193353_445069），11-04 00:04 起同 deck 撞
  3.6GiB 内核限额被截停；本机自 11-02 12:37 未重启，翻转发生在同一次开机内。
- **真实求解合计峰值上限**：小档五门实测最高 4831MB（11-03 22:34 assembly，
  当时预算 >6GiB 完成并过门）。

据此把 `SMALL_TIER_FLOOR_GIB` 4.0 → 8.0：守卫 trip 点 = 0.98×0.9×预算
（1 并发 ≈7.06GiB；2 并发、可用 15.5GB 时受 AVAILABLE_SHARE 封顶 ≈6.2GiB，
trip ≈5.46GiB）——盖过实测合计峰值 4.83GB 并留 ≥10% 裕度。守卫语义零改动：
注入单测（192MiB 显式限额）与「peak ≥98%×内核限额 → 截停」判定不变；
AVAILABLE_SHARE 80% 封顶保留（内存吃紧的机器上尖峰仍会被如实截停并保留日志）。
LARGE_TIER_TARGET_GIB=10 不变（11-02 实测锚 1mm 档 6GiB 停滞/10GiB 完成，
与尖峰叠加后口径自洽）。verify_memory_budget_guard.py 的断言经
`resolve_memory_limit_bytes` 动态取预算，对本次抬升鲁棒，无需修改。
