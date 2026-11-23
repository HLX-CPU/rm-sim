# 串行求解预算与验证程序退出

2026-11-02，接续DS的36项回归中两项失败。

## 内存保护监控实际限制

`SolveScheduler`自动预算仍为可用内存的60%除以最大并发数，Windows内核限制仍为
预算的90%。运行时改为监控实际内核限制的98%，避免原生分配器已无法继续分配，
却因为峰值尚未达到公开预算的98%而一直等待。全局预算没有提高，低内存保护仍保留。

任务JSON增加`stop_reason`：主动触及限制并停止为`memory_limit`；若进程自行异常退出、
峰值接近限制，记录`memory_limit_near_exit`，文案明确具体退出原因未确认。
不会仅据内存接近就把访问冲突断言为内存不足。

`verify_bonded_connection.py`顺序提交并等待单任务，改用最大并发1，避免为不存在的
第二个并发任务预留一半内存。保留原理论位移、反力与网格收敛门；每档网格使用自己的
模型和面引用。成功和失败case永久保留；超时停止本次进程树并报告证据目录。

## 显式关闭验证程序的Qt资源

`verify_tie_policy.py`先关闭并删除其对话框、处理DeferredDelete，再释放Gmsh模型，
最后在解释器退出前调用当前PySide6提供的`QApplication.shutdown()`。
成功标记在这些步骤之后打印，正常退出码仍是验收条件；未使用`os._exit`。

复现中旧程序20次有7次在清理后异常退出，调整后同样20次均exit0。
证据针对这条验证程序路径，不代表所有桌面/VTK/OCC退出组合均已验收；尚无完整原生栈。
依据：[QObject.deleteLater](https://doc.qt.io/qtforpython-6/PySide6/QtCore/QObject.html)、
[QCoreApplication](https://doc.qt.io/qtforpython-6/PySide6/QtCore/QCoreApplication.html)。

## 验证入口

- `probes/probe_frozen_deck_budget.py`：同一输入哈希、6/10GiB预算对照，保存所有原始文件。
- `probes/probe_tie_policy_exit.py`：冻结脚本，多次独立进程运行，记录真实退出码和输出。
- `verify_scheduler_lifecycle.py`：分配被拒后仍等待的进程夹具，核查内核限额监控。
- `verify_bonded_connection.py`、`verify_tie_policy.py`及默认回归。

C#测试进程以同目录原子重命名发布PID，防止取消测试读到刚创建的空文件；
只有正式PID文件存在才表示编号已写完。原始取消、关闭和无关进程存活门不变。

只读`probes/probe_quadratic_gravity_balance.py`另核查整装自重与RF定义，
本模块阶段尚未接入产品报表的自动反力修正；后续实现见
`module_34_reaction_semantics.md`。完整阶段结果见analysis中的对应报告。
