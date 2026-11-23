# 模块 10 设计说明：整机启动冒烟与可重启验证

日期：2026-11-02 ｜ 状态：已完成，`verify/verify_relaunch.py` exit 0（两次启动均 2.1~2.2s）。

## 范围

- `rm_sim/main.py` 新增 `--smoke` 模式：主窗口显示 → 懒加载视口（offscreen →
  GL 失败 → Agg 降级，probe4/probe8 配方）→ 输出「主窗口已显示 /
  渲染后端=… / ccxPath=…（config.json 已加载）/ 冒烟时间戳=…」→
  `QTimer.singleShot(200, quit)` 自动退出。
- `verify/verify_relaunch.py`：offscreen 连续启动两次（各 120s 超时门），
  断言 exit 0、标记行、无未捕获 traceback、渲染后端=agg_fallback、
  ccxPath 已加载、第二次时间戳晚于第一次（可重启证明）。

## 约定（不可改）

- offscreen 下视口必须走主入口的懒加载 + Agg 降级路径（probes/probe4_log.txt
  实证 offscreen 无 createPlatformOpenGLContext）；若冒烟崩在
  createPlatformOpenGLContext，判主窗口实现不合规——不得改冒烟约定迁就。
- stderr 中的 QFontDatabase / "This plugin does not support …" 为 Qt 平台
  插件噪音（非崩溃），不计入失败。

## 实测（verify/logs/verify_relaunch.log）

| 项 | 实测 |
|---|---|
| 启动 #1 | exit 0，2.2s，时间戳 2026-11-02T03:42:36.308 |
| 启动 #2 | exit 0，2.1s，时间戳 2026-11-02T03:42:38.465（晚于 #1） |
| 标记 | 主窗口已显示 / 渲染后端=agg_fallback / ccxPath=…已加载 全部命中 |
| traceback | 无 |

## 未做 / 留给后续模块

- 冒烟时长/次数可参数化（当前固定两次，够交付约束「程序可重启」）。
- 崩溃转储收集（若未来出现崩溃类回归）。
