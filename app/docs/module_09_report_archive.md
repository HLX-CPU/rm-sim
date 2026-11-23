# 模块 9 设计说明：计算报告与求解产物归档

日期：2026-11-02 ｜ 状态：已完成，`verify/verify_report_archive.py` exit 0（6/6，
真实求解两次）。

## 范围

- `solver/archive.py::archive_case(project_dir, job_name, task, study,
  load_case, mesh_result, mesh_stats, fields, results, tip_node,
  geometry_file)`：求解完成后把 **results.json（全精度）+ report.md +
  mises.png + displaced.png** 写入 调度器创建的 case_NNN 目录（工程目录
  `cases/case_NNN/`，work_root 必须为 project_dir/cases——与调度红线①一致，
  目录先建后解，ccx 产物就地留存，不做二次拷贝）；校验 case 目录归属与
  任务状态。
- `report/markdown_report.py`：纯 dict 输入的报告生成器
  （build_report_md / build_results_json）。ctx 由 archive 组装：
  材料 / 网格统计（含 min SICN、负 Jacobian 计数）/ 工况（summary 表）/
  结果（端部 Uy、max|U|、max Mises、ΣF 分量）/ 反力平衡（面力工况）/
  超屈服判定（max Mises vs 屈服，线弹性提示）/ 云图嵌入 / 附录 A。
- `core/project.py`：`Project.project_dir`（工程目录；求解归档 cases/case_NNN
  落点；GUI 在生成网格时设为网格所在目录）。
- GUI：求解完成后自动归档 + 日志报告路径；求解菜单「打开最近计算报告 /
  打开最近产物目录」（os.startfile）。

## 数值一致性分层（验收 ③ 的核心设计）

- **附录 A**：`build_results_json(ctx)` 的全精度 JSON 文本——报告附录与
  results.json 文件**同源同文**（ctx 先定全量再一次性落盘，杜绝两份漂移）；
  1e-9 门打在附录关键值上（tip_uy/max_u_mag/max_mises，实测差 0）。
- **正文**：人类可读数值按 `BODY_FORMATS` 固定打印位数（Uy/max|U|/ΣF 6 位、
  max Mises/min SICN 4 位）；验收按 |正文值 − results.json| ≤
  0.5×10^(−打印位数) 核对——实测 7 项全过（例：0.191076 vs
  0.19107596389589615 差 3.61e-08 ≤ 5e-07）。这修正了「0.190532 vs
  0.1905315 差 5e-7」按全精度门误爆的问题。
- **反力平衡**：面力工况输出 |ΣRF + ΣF_applied| 残差（ΣRF = −ΣF_applied
  为平衡条件）；重力/压力的平衡分别由模块 7 验收 ⑧/⑦ 数值门覆盖。

## 实测数据（verify/logs/verify_report_archive.log）

| 验收 | 实测 |
|---|---|
| ① case 内容 | casesolve.{inp,dat,frd,log} + results.json + report.md + 2×png + .sta/.cvg/.12d/.vtu；日志含 Job finished |
| ② inp 结构 | *STATIC/*ELASTIC(210000)/*CLOAD(总力 100N 均分，1e-9)/*BOUNDARY/*NSET,NSET=FIXED |
| ③ 分层核对 | 附录同文 + 1e-9 门；正文 7 项按位数核对全过 |
| ④ 报告内容 | 45钢、100N -y、网格统计（6481/11222/负 Jacobian 0）、min SICN 0.3056、反力平衡、超屈服判定（未超）、云图嵌入 |
| ⑤ 两次求解 | case_001→case_002 递增；case_001 12 个文件哈希跨求解不变 |

## 未做 / 留给后续模块

- 报告的 PDF/HTML 导出与模板定制（当前 Markdown 直出）。
- 工程闭环（工程文件保存/重开后 cases 索引、结果对比）属后续模块。
- 报告中的单位制切换、多工况合并报告。
