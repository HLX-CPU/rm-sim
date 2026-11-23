# 模块 4 设计说明：材料库与零件材料设置

日期：2026-11-02 ｜ 状态：已完成，`verify/verify_material_library.py` exit 0（5/5）。

## 范围

- `core/materials.py`（GUI 无关新模块）：内置材料库 + 自定义库持久化
  （materials.json）+ 字段校验 + 零件指定材料 + 求解材料属性导出。
- `core/project.py`：`Material` 增加 `yield_strength_mpa: float | None`
  （可选字段，旧工程文件加载自动补默认值；灰铸铁等脆性材料按抗拉强度近似）。
- GUI：菜单「材料(&A)→选择/管理材料…」（`gui/material_dialog.py`）：
  表格列出 内置+自定义，内置只读，自定义增/改/删（表单 → core 校验，
  校验失败弹窗显示原因，不崩溃）。

## 关键决策

1. **单一 Material 表示**：库与数据模型共用 `core.project.Material`，不另造
   LibraryMaterial；store 往返对新增可选字段天然兼容（缺字段→默认值）。
2. **单位体系钉死 N-mm-MPa-t-s**：E/屈服 MPa、ρ t/mm³，字段名即单位含义
   （`density_t_mm3`），从库定义到求解导出无隐式换算（调研报告 §7）。
3. **内置库只读 + 同名遮蔽**：`all_materials()` = 非冲突内置 + 自定义；
   用户想改 Q235 就自定义同名 Q235 覆盖；`remove/update` 只作用于自定义库，
   对内置名操作报「自定义材料不存在（内置材料不可删除/修改）」。
4. **校验先于持久化**：`add/update/save` 都先 `validate_material`
   （E>0、0<ν<0.5、ρ>0、屈服>0 或空、名非空；类型不符也拒绝——
   `"210000"` 字符串会被拒），非法输入绝不落盘（验收 ③ 断言库文件字节级不变）。
   加载时也逐条校验（防手改 materials.json 损坏）。
5. **自定义库位置**：默认 `E:/rm_sim/materials.json`（应用数据目录，与
   config.json 同级），`RM_SIM_MATERIALS` 环境变量可覆盖；所有 API 接受显式
   `path` 参数——验收 ②③ 全部走临时文件，不触碰真实库；真实库文件只在
   GUI 增删改时懒创建。
6. **求解导出键名对齐 beamCheck**：`solver_material_dict` →
   `{"E_MPa", "nu", "rho_t_mm3"}`；前两者与 probes/beamCheck.json parameters
   逐字段相等（验收 ④），ρ 在 beamCheck 无对应字段故单独断言 7.85e-9±2%。
   屈服强度不进弹性求解段（*PLASTIC 属后续塑性模块）。

## 验收对应（全部在本次实际运行通过，verify/logs/verify_material_library.log）

| 验收 | 结果 |
|---|---|
| ① 内置 ≥7 + 关键值 | 8 种（Q235/Q345/45钢/6061/7075/TC4/HT250/尼龙PA6）；Q235 E=210000/ν=0.3/ρ=7.85e-9（±2% 内）；6061=68900、7075=71700、TC4=110000（±3% 内） |
| ② round-trip | 新增 2 条→逐字段相等；更新 E/屈服→相等；删除→仅剩目标；同名遮蔽内置；重名拒绝 |
| ③ 非法输入 | 负 E/E=0/ν=0.5/ν=0/ν<0/ν>0.5/负 ρ/负屈服/空名/E 类型错误 共 10 例全部拒绝，库文件字节级不变 |
| ④ 45钢 → 求解属性 | `{'E_MPa': 210000.0, 'nu': 0.3, 'rho_t_mm3': 7.85e-09}`；E_MPa/nu 与 beamCheck parameters 逐字段相等；工程保存/重开往返后一致 |

## 未做 / 留给后续模块

- 屈服强度进入求解（*PLASTIC，塑性模块）；温度相关材料、塑性曲线、失效准则。
- 材料库 UI 的导入/导出（CSV/Excel）与单位显示切换。
- 按实体指定材料（装配体模块；当前 Study 级单材料）。
