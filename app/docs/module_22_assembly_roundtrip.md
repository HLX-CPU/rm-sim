# 模块22：装配工程保存、重开与重算

- `.rmsimproject` 保存装配STEP、逐部件STEP副本、部件指纹、各部件材料数值快照、
  双侧连接面指纹、载荷及全部历史case。
- 重开先按体积/质心/bbox匹配部件，再在该部件范围内匹配连接面和边界面。
  不唯一匹配→需重新确认、警告、拒绝求解；不凭旧solid_index静默贴面。
- 材料数值随束保存，外部自定义材料库变化不改变已保存工程的材料。
- 逐部件STEP使用[Gmsh可见实体导出选项](https://gmsh.info/doc/texinfo/gmsh.html#index-Geometry_002eOCCExportOnlyVisible)，
  保留全局位置；导出后恢复可见性和选项。测试会重新导入每个副本核对实体数与指纹。

`verify_assembly_project_roundtrip.py`：A创建、求解1000N并保存；B新进程重开、逐字段
核对并改为2000N重算；C再重开读取两case。旧case所有文件SHA256不变。
实测RFy=2000.00000046N，界面压应力积分1999.979502N。
独立篡改连接面指纹面积+5%时拒绝求解，保留原工程及负例。

验证原始产物在 `E:/rm_sim/verification/assembly_roundtrip_*`。
