# 模块51：共享冻结产物与失败项重跑（2026-11-03）

依据 `analysis/装配仿真验收提速调研_2026-11-03.md` §4 实施：一次真实小算例求解供
多个读取型检查复用（GUI/报告/序列化类）；缓存键与完整性规则按调研逐条落地；
`run_regression.py` 支持 `--only failed` 与按标签重跑；报告单列来源。

## 共享冻结产物（verify/shared_products.py）

- **产物形态**：`save_bundle` 工程束（project.rmsimproject）——app 原生序列化形态，
  内含 project.json（case 索引/部件指纹/网格参数/载荷）、几何副本、完整 case 产物
  （inp/dat/frd/log/sta/cvg/12d/vtu/results.json/report.md/云图）。规格=标准两块
  粘接梁（samples/assembly_two_blocks.step，bonded，1000N，2mm，Q235，含自重）。
- **缓存键**（调研「新版本输入生成逻辑必须实际执行后再判断能否复用」）：`compute_key`
  真实执行几何导入+网格+deck 组装（**不求解**，约 4~6s），键=canonical JSON 的
  sha256，分量：几何文件内容（step sha256）、几何映射（逐部件 PartFingerprint
  volume/centroid/bbox）、网格参数（mesh_size_mm + gmsh API 版本 + 网格生成代码
  内容哈希 mesher/refinement/gmsh_runtime）、**最终 INP 内容 + 全部 *INCLUDE**
  （载荷在 INP 内——载荷一变键即变）、引擎指纹（ccx 配置路径 + 二进制 sha256）、
  线程/数值设置（OMP_NUM_THREADS、单位约定、*SOLVER 指令）。
- **冻结**：只接受完成、文件齐全的束（成员齐全含每 case 的 inp/dat/frd/log/sta/vtu/
  results.json/report.md；results.json state=completed 且 job_finished；求解器日志
  `<job>.log` 含 Job finished——**固定取单后缀 .log，排除 .stdout/.stderr.log**，
  集合序非确定 bug 已修）；冻结前独立复算参考数（open_bundle → load_archived_case，
  其内部对 summary 与原始场做 rtol=1e-5 一致性门）写入 manifest（键分量/束与逐成员
  哈希/参考数/来源与时间可追查）；原子落盘 `E:/rm_sim/verification/shared_products/
  <key>/`（已存在有效产物→复用不覆盖，无效→拒绝不掩盖）。
- **复用**：`try_reuse` 完整性校验（束文件哈希→成员齐全→逐成员哈希→完成标记）任一
  不符即 None；`materialize` 解包到**独立临时目录**（共享数据只读）并再校验。
- **读取型检查共通流程** `run_reader`：算键→查复用→未命中则真实求解一次并冻结→
  物化→读取器独立重推导结论→与冻结参考 1e-9 门→打印
  `[product] source=<本轮新求解|复用历史求解并重新检查> key=…` 与 `READER_RESULT:…`。

## 三个读取型检查（显式点名，不入 DEFAULT——无参回归语义逐行不变）

| 检查 | 类 | 结论来源 |
|---|---|---|
| reuse_results_gui | GUI | offscreen AssemblyResultDialog（模块49 同口径）：卡片/明细表数值与同源 part_field_views 重算 1e-9 一致，整装合成卡片显示值为结论 |
| reuse_report_check | 报告 | report.md 附录 A 与 results.json 同源同文（精确相等）+ 关键值对冻结参考 1e-9 + 正文 7 行按打印位数核对（verify_report_archive ③ 同款） |
| reuse_archive_roundtrip | 序列化 | load_archived_case 原生序列化路径读回（内部含 summary↔原始场 rtol=1e-5 门与反应力溯源哈希门） |

smoke 实测（同一冻结产物 key=653c5ef1…）：三类结论逐位一致
（max Mises=10.688960232343554 MPa、max|U|=0.00060573884453596 mm、ΣFy=1001.2321357292664 N
= 1000N 外载+自重），GUI 与序列化分别从对话框显示值与独立重推导抵达同一数值。

## run_regression.py（增量）

- `--only-failed`：读最近一轮 `regression_纯时间戳/summary.json`，只重跑失败项
  （可与 `--labels` 取交集）；重跑报告 timing.json 记 `only_failed` 基线与重跑清单；
  未重跑项（基线其余检查）列入 `not_executed` 并标注「未执行/待验证」；无失败项时
  重跑 0 项 exit 0（REGRESSION: 0/0 行如实标注）。与显式检查/--framework-only/--slow
  互斥（argparse error）。
- **来源单列**：summary.json 逐行新增 `source` 字段（解析检查 stdout 的
  `[product] source=…` 标记；无标记如实记「本轮执行（未参与共享产物）」）；
  timing.json 新增 `sources` 计数与 `not_executed` 清单——报告单列
  『本轮新求解』『复用历史求解并重新检查』『未执行/待验证』三种状态。
- 未登记检查名（显式拼写错误等）label 如实记 `unknown`（suite_labels.try_label），
  不再 KeyError。

## 验收（verify/verify_shared_products.py，exit 0 = 通过）

全程隔离共享根（RM_SIM_SHARED_PRODUCTS_ROOT 指向验收临时目录；与真实根仅路径
不同，同一代码路径；共享数据只读，负例写验收自己的隔离副本）：

- **①** 首轮三读取器=恰 1 个「本轮新求解」+2 个「复用历史求解并重新检查」（一次
  真实求解）；第二轮 3 个全部复用；三类结论两两一致且与冻结参考 1e-9 一致；
- **②** 缺 report.md（成员完整性层拒绝并指名）、篡改 .dat 字节（束级哈希改齐后由
  **成员哈希层**拒绝）、束截断三种损坏全部拒；损坏根下 try_reuse→None 不静默通过；
- **③** force_n 1000→2000N：真实重新生成 deck 后键改变（INP 内容进键），新键
  try_reuse→None；同规格跨不同临时目录两次算键相等（deck 确定性、无路径泄漏）；
- **④** 基线（3 读取器+1 不存在检查）→ `--only-failed` 恰重跑 1 个失败项，3 读取器
  标注「未执行/待验证」，失败项仍失败 exit 1（如实）；无失败项 → 0 重跑 exit 0；
- **⑤** summary.json 逐行 source + timing.json sources/not_executed；三种状态
  本轮实际出现。

第 1 次运行 exit=1 为**验收脚本自身缺陷**两处：②a 断言写错层级（束级哈希先拒，
行为正确但原因断言过严——改为成员完整性层与成员哈希层分别断言）、⑤ 只扫回归
JSON 漏掉读取器 stdout 的「本轮新求解」状态——修复后完整复跑 exit 0（全程实测
约 4.4 分钟，含一次真实求解与两次回归运行）。

## 边界

- 不改任何既有门/容差；读取器与 `--only-failed` 不改变无参回归的输出行序
  （模块50 ①原样输出对照不受影响——来源信息只进 JSON 报告，不加 stdout 行；
  --help 新增 [--only-failed] 行由模块50 验收的对照规则按「仅新增选择与报告
  信息」契约延展接纳，verify_regression_labels.py 已同步更新并完整复跑）。
- pytest/xdist 等开源组件本轮只评估不引入；提速数字如实记录（读取器复用路径
  实测 ≈8~15s/个 vs 未命中求解+冻结 ≈60s——单算例小样本，不外推倍数）。
