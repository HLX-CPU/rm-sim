# 模块35：局部网格、质量定位与失败重试

网格菜单新增全局/局部网格设置、网格质量与差单元位置、重新运行上次失败任务。
设置作用于部件、面或边，保存目标尺寸与面/边过渡距离，多个设置取较细目标。
尺寸是网格器目标，实际边长还受曲率、几何与优化影响。

## 身份和尺寸场

Study.mesh_controls保存MeshControl，旧工程缺字段时为空。部件沿用已有身份；面在所属部件
内匹配FaceFingerprint；边匹配曲线类型、长度、几何重心及33个参数采样点的坐标范围。
采样范围不是精确几何包围盒。缺失或歧义会要求重新确认，禁止静默使用旧tag。

实测Gmsh 4.15的Distance场会扩大采样对象的运行时容差包围盒：12mm直边由约±1e-7mm
变为约±0.006mm，不能作为持久化身份。曲线采样坐标不随该框变化，重复划分和工程束
解包重开已验证。`cad/mesh_targets.py`负责目标解析，不改原几何文件。

`mesh/refinement.py`使用Distance/Threshold、MathEval/Restrict与Min场，作用域限制在
所属实体及边界。MeshSizeMin同时允许局部目标，避免旧min=max截断局部场。
生成后移除本次字段并恢复临时选项，防止下次无局部设置仍受旧场影响。
依据：[官方手册](https://gmsh.info/doc/texinfo/gmsh.html#Gmsh-mesh-size-fields)、
[官方t10示例GitHub镜像](https://github.com/live-clones/gmsh/blob/master/tutorials/python/t10.py)。
复用现有Gmsh依赖，没有安装额外软件。

## 质量和恢复

每次生成记录SICN分位数、低于0/.01/.05/.1的数量、非正Jacobian数及最差100个单元。
编号为导出INP的真实单元号，部件来自PARTn；定位坐标是四角点平均，不是二次体积质心。
mesh.quality.json、准备数据包、case/results.json和报告附录保存记录，正文列最差20个，
GUI列最差100个。质量分布提示不能代替应力收敛验收；既有质量硬门没有放宽。
不支持为旧归档补造质量结论。

失败后若已产生质量文件，GUI仍可查看。手动重试允许改网格设置，选择当前或保守曲率
配方，建立独立任务；旧任务与case保留。原生优化器尚未返回质量时，不能定位崩溃单元，
ScaledJac文案本身也没有单元号。

原有自动重试留下geometry.step，下一次worker独占创建会因文件存在失败。现在将失败
尝试的快照、日志、阶段记录及已产生的准备文件移至prepare_attempt_NN，再启动新进程。
最多3次、只对已知优化器崩溃自动重试，其他失败与取消规则保留。没有根治Gmsh崩溃。

## 验证

- verify_local_mesh_controls.py：真实Gmsh局部性、尺寸场清理、工程束重开、缺失目标拒绝、
  Qt表与设置取消；模拟首次崩溃后运行真实准备进程，检查快照/日志保留。
- verify_background_workflow.py：真实验证失败后触发GUI重试菜单，新case与旧失败文件哈希。
- probe_actual_hook_local_mesh.py：真实分析钩体基准/局部网格对比，仅网格、不求解应力。

钩体峰值收敛及新增整装2kg外载仍未完成，整套软件还需继续验收。
