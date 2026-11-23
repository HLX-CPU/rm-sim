# 模块 12 设计说明：装配 STEP 导入与部件身份

日期：2026-11-02 ｜ 状态：已完成，验收 `verify/verify_assembly_import.py` exit 0（6/6）。

## 范围

- `app/rm_sim/cad/assembly_import.py`（GUI 无关，cad 层）：`import_assembly(path) ->
  AssemblyModel`；每个 (3,tag) 实体 = 一个部件；部件身份 = 名称 + 几何指纹；
  每部件表面清单；位置原样保留（不搬移/不对齐）；非 mm 源单位换算沿用。
- `app/rm_sim/core/part_identity.py`（GUI 无关，core 层）：`PartFingerprint`
  （体积/质心/bbox 角点）+ 匹配/一致性函数，与 `core/face_identity.py` 同一模式。
- `app/rm_sim/cad/make_assembly_samples.py`：装配样例生成器（`python -m
  rm_sim.cad.make_assembly_samples`，PYTHONPATH 需含 app/）。
- 样例 `samples/assembly_two_blocks.step`、`samples/assembly_lap_pin.step`。
- `cad/step_import.py`：仅做**纯提取重构**——三检入口抽为 `_import_model(path, cls)`，
  `import_step` 行为/日志逐字不变（verify_step_import 回归 6/6 佐证）；
  `cad/__init__.py` 增补导出。

## 关键决策与证据（本会话实测，gmsh 4.15.2 / OCC）

1. **统一入口复用**。`import_assembly` 走与 `import_step` 完全相同的
   `_import_model`（单位门在文本层 detect_source_unit、实体门/清单在
   `_load_geometry`）——单位三检、NotSolidError、`occ_converted_to_mm`
   标定与 dilate 分支零分叉；位置一律原样保留。
2. **部件身份 = 名称 + 指纹**。名称解析纯文本层（与 units.py 同风格）：
   leaf 部件名经两条引用链可达 PRODUCT——链A 单件
   `SHAPE_DEFINITION_REPRESENTATION(PDS, ABSR)`；链B OCC 装配
   `CONTEXT_DEPENDENT_SHAPE_REPRESENTATION(REPRESENTATION_RELATIONSHIP, PDS)`
   → `NEXT_ASSEMBLY_USAGE_OCCURRENCE(parent_PD, child_PD)` → child_PD → PRODUCT。
   gmsh 导出的装配含 1 根 + N 叶 PRODUCT（本样例名形如
   "Open CASCADE STEP translator 7.8 1.1/1.2"）；解析失败回退 `Part<tag>`，
   **名称仅供人读，指纹才是匹配身份**（同名不保证唯一）。
   MSB 文档序 ↔ occ 实体 tag 升序一一对应（探针实证：(3,1)=先构造的下块）。
3. **指纹**（core/part_identity.py）：体积（相对 max 两侧）+ 质心/bbox 角点
   （逐轴 ≤ rel_tol × 模型对角线，rel_tol=1e-6，与面指纹一致）；
   同文件重导入门 `fingerprints_equal(rel_tol=1e-9)` 逐分量混合容差
   （坐标按绝对、体积按相对）。实测重导入 max|Δ|=0（bit 级一致）。
4. **face_owner 语义（首次实现时固化）**：`step_import._load_geometry` 对每个
   实体取 `getBoundary(oriented=False)` 声明边界面，面被多实体共享时按实体
   tag 递增序后写优先（最后声明者胜）。本模块实测：OCC 读入**不合并**不同
   B-rep 的重合面——两叠块 12 面 6+6、搭接+销 17 面 7+7+3，全部独占归属
   （每面恰属一个实体）；验收 ② 按该语义断言并集=全表面、无重无漏。
5. **样例几何（「螺栓与销轴」功能引用，首次实现时固化；该功能开发时可在
   make_assembly_samples.py 调整重生成）**：
   - two_blocks：下块 (0,0,0)-(20,20,20)、上块 (0,20,0)-(20,40,20)，沿 +y 叠放
     → 质心差 (0,20,0)（实测精确 0 偏差）。
   - lap_pin：下板 x∈[0,40] 40×30×8、上板 x∈[20,60] 40×30×8（搭接区
     x∈[20,40]）、通孔轴 z 中心 (30,15) 直径 10 贯穿 z∈[0,16]、销独立实体
     等径 r=5 z∈[0,16]。体积解析值：板 8971.681469…、销 1256.637061…
     （OCC 实测一致到 ~1e-11 相对）。
   注意：功能要求原文「几何规格见『螺栓与销轴』功能」，该规格文档在仓库中
   尚不存在（调研报告仅有两板螺接算例的规划），故本模块固化上述 v1 规格。
6. **向后兼容**：单实体 STEP → parts=1；`AssemblyModel` 是 `SolidModel`
   子类，pick_face/tessellate_faces/face_by_tag/volume_mesh 全部原样；
   `import_step` 仍返回纯 `SolidModel`（回归实测）。GUI/求解工作流
   （gui/main_window、solver/project_workflow）本次零改动。

## 验收对应（全部在本次实际运行通过，verify_assembly_import.log）

| 验收 | 结果 |
|---|---|
| ① 两实体→部件数 2；体积/质心/bbox 与构造 1e-6；质心差 (0,20,0)±1e-6 | 体积 7999.999999999998、质心精确、bbox max\|Δ\|=1e-7（OCC 公差膨胀）、质心差精确 (0,20,0)；A2 米制装配（声明 METRE）换算后同一套值成立且日志含「源单位=m」 |
| ② 表面清单归属 | 两叠块 6+6、搭接+销 7+7+3；每面恰属一个部件、并集=全表面；逐面面积与构造一致（1e-6 相对）；通孔同轴 (30,15) |
| ③ 重复导入指纹一致 | 两个样例全部部件 max\|Δ\|=0（1e-9 门），名称一致 |
| ④ 单实体向后兼容 | parts=1、isinstance SolidModel、pick_face 可用；import_step 仍返回 SolidModel 且多实体清单 2/12 不变 |
| ⑤ 壳体/损坏负例 | 壳体→NotSolidError「非实体，无法网格化」；垃圾/截断→StepImportError 含原因 |
| ⑥ GUI 无关 | 全程无 PySide6 + layer_guard 静态扫描通过；main.py --selfcheck PASS（exit 0） |

## 未做 / 留给后续模块

- 装配部件的材料按部件挂接、工况树展示部件（GUI 接入属后续）。
- 部件指纹进 `.rmsimproject` 持久化与 re-resolve（挂接点：core/store 注册 +
  core/part_identity.part_matches_fingerprint）。
- 「螺栓与销轴」接触/预紧语义（本模块只交付几何样例与部件身份）。
