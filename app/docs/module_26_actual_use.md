# 实际模型导入、几何检查及历史恢复

`cad/native_import.py` 和 `tools/export_native_assembly.ps1` 为本机可选原生装配转换入口。
SolidWorks 只读打开，内存重建，组件实例/单件几何/变换/材料标签/源哈希留档，恢复导出偏好。
不保存源文件，只关闭本次打开的装配；仅在本次启动SW时退出该应用。
需要本机安装的SolidWorks 2026 Interop，不是可移机转换器。
GUI通过QProcess运行转换，完成后在主线程导入STEP；转换中阻止退出以等待源文档释放。

`Part.material_confirmed` 保留旧工程默认兼容语义；新GUI STEP导入将部件标为待指定。
原生导入中的6061标签只能带入明确标注的线弹性参考快照，屈服留空；未知材料阻止准备求解。
`Project.geometry_provenance` 随工程保存，并在统一静力归档中写入结果与报告。

`cad/assembly_checks.py` 在临时OCC副本中测距、做实体交集，记录几何干涉体积与逐对错误。
零距离不等于有效面接触或锁止；检查不自动建立力学连接。该后端尚无一般用户GUI菜单。

`solver/history.py` 从归档自己的INP、DAT、FRD和既有VTU读取，核对场与摘要，不重新转换写文件。
GUI工程重开自动恢复最近历史结果，「历史结果与工况对比」切换已完成case。
常驻标签标记历史，不将历史云图与当前修改工况混淆；修改定义后报告入口随云图一起失效。
解包拒绝越界路径和不同已有内容，GUI每次生成新的工作目录。

本轮入口与证据：

- `verify/verify_native_import.py`：当前用户SLDASM的真实Qt异步转换、20实体唯一识别、材料门与源文件不变。
- `verify/verify_step_material_gate.py`：普通STEP导入材料未指定时拒绝求解准备。
- `verify/verify_history_restore.py`：移位工程恢复、原始文件哈希不变、篡改拒绝、越界路径/覆盖拒绝。
- `verify/verify_assembly_geometry.py`：双块间隙/接触/400mm³交叠解析控制，源实体不变。
- 根目录《实际使用验收进展_快拆结构_2026-11-02.md》：授权的几何修订、44次真实计算、未收敛应力及全目标缺口。

这些验证不能替代整装接触求解、应力收敛、实物强度或原生桌面GL验收。
