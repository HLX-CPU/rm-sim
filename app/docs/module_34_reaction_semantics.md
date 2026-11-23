# 模块34：支反力口径与历史加载

CalculiX RF为外部节点力，包含约束反力和该节点上已施加的CLOAD/DLOAD。
依据：[官方2.22手册](https://www.dhondt.de/ccx_2.22.pdf#page=394)，6.11.5、7.98。
因此有载荷的支承节点不能直接把DAT RF称为纯支反力。

`solver/nodal_loads.py`重建当次最终INP的载荷，返回选定支承节点的载荷、全域合力与原点合矩。
密度来自INP的MATERIAL/DENSITY/SOLID SECTION，避免历史结果依赖当前材料。
C3D10自重采用四点体积积分，C3D4单点；压力在二次几何面采用九点三角积分，
包含中间节点的形函数、曲面法向与面积。CLOAD逐节点/节点集展开。

`fields.raw_rf_table`为DAT原值；`rf_table/rf/rf_sum`在支持时为原值减去相应节点已施加载荷。
`source.reaction`记录supported、quantity、version、输入原始字节及标准化文本SHA256、
原始RF合力、固定节点已施加载荷及全域合力/合矩。无法覆盖时保留原始数值并给出原因。

范围限定为单步小变形静力、C3D4/C3D10、平动CLOAD、恒定GRAV及均匀P1–P4。
幅值、多步、大变形、变换坐标、初始条件、非均匀密度/压力、其他DLOAD均拒绝修正。
重复同一单元/类型的DLOAD同样拒绝，以免猜测替换与叠加语义。不是通用INP求解器。

报告和平衡检查采用支持范围内的修正值，并保留原始RF；GUI同时列出两组值。
后台数值包携带原始表。历史加载校验INP哈希（若旧记录具有此字段）、FRD/VTU位移、
极值及原摘要；旧归档先按原始RF核验，再生成修正后的内存视图。原报告与求解文件不变。
旧列表值仍是归档时摘要，界面明确标注；打开旧报告看到原值是预期行为。

验证：`verify/verify_reaction_semantics.py`包含真实混合载荷CCX、直接在固定面施力、
曲面压力解析对照、原始数据包往返、Qt数值表、新旧归档只读恢复、摘要篡改拒绝与不支持载荷降级。
`probes/probe_history_reaction_readonly.py`用于已有整装与独立积分结果对照，全文件哈希检查只读。
这不证明绑定连接、峰值收敛、材料强度或2kg外载工况合格。
