# 模块23：单零件模态与归一化振型

`solver/modal.py` 使用真实CalculiX `*FREQUENCY`；单独解析模态DAT与每个FRD的DISP块，
不套用静力反力解析器。必须提供正密度、固定边界。当前范围为单零件、无接触、无预应力，
静载不进入特征值计算；装配/预应力模态明确拒绝。

统一入口：「求解→单零件模态分析」。阶次选择、归一化振型和慢放动画由
`gui/modal_dialog.py` 提供；使用专用「归一化」标签，不把特征向量标成实际毫米振幅。
原始输入、日志、DAT/FRD、modes.npz、逐阶PNG、频率表保留于工程目录modal_cases/case_NNN。

标准100×10×10mm梁、45钢：首阶833.1429Hz；Euler-Bernoulli理论835.516594Hz，误差0.284%。
`verify_modal_analysis.py`核对首阶、方截面双重弯曲频率、全部振型固定边界与归一化。
`verify_modal_view.py`核对阶次切换、动画两相位图像差异与关闭时停止定时器；
`verify_extensions_gui.py`经过真实主窗口入口构建结果对话框。
