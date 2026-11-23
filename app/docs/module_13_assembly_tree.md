# 模块 13 设计说明：装配树与部件管理

日期：2026-11-02 ｜ 状态：已完成，验收 `verify/verify_assembly_tree.py` exit 0（①-⑦，本机有交互桌面会话，交互段真实 GL 执行）。

## 范围

- `core/project.py`：`Part` 实体（part_id/名称/源引用/solid_index/solid_tag/
  几何指纹/材料引用/可见性）+ `Project.parts` 列表（单部件=1 条向后兼容）+
  `migrate_legacy_parts`（旧版工程打开自动迁移，幂等）+ `assembly_tree_payload`
  （装配树 JSON 结构，GUI/selfcheck/验收共用）。
- `core/store.py`：`Part`、`PartFingerprint` 注册 `_REGISTRY`；
  `load_project` 自动迁移旧版单部件工程。
- `core/project_bundle.py`：`open_bundle` 同一迁移（GUI 打开工程的实际路径）。
- `core/materials.py`：`part_material`（material_name 非空→库解析，空→继承
  研究材料）+ `set_part_material`（先库校验后写入，只动目标部件）。
- `cad/assembly_import.py`：`sync_parts_from_model`（AssemblyModel → 工程
  parts，part_id=p1..pN，既有部件按 solid_index 继承材料引用/可见性）。
- `assembly_demo.py`（**新增顶层 GUI 无关文件**，已登记
  `layer_guard.GUI_FREE_ROOT_FILES`）：`build_demo_assembly_project` 构建
  2 部件演示工程（p1=Q235、p2=6061），--selfcheck 与验收共用。
- GUI：`panels.py` 工况树 → **装配树**（装配节点=部件/材料/可见性，双击部件
  →隔离、双击装配节点→恢复全部）；`viewport.py` `show_geometry_parts`
  （每点部件序标量，复用 `show_geometry` 同一 GL/Agg 链路，新增 `tess=` 预计算
  细分参数）；`main_window.py` `show_assembly(isolate_part_id=None)` + 树信号
  接线 + 导入 STEP 统一走 `import_assembly`/部件同步。
- `main.py --selfcheck`：新增 **[6/6] 装配树数据模型** 步骤（生成两叠块样例 →
  构建 2 部件演示工程 → 打印 `装配树={...}` JSON），原五步编号改为 [1/6]..[5/6]，
  既有标记串（窗口构建成功/渲染后端=agg_fallback/selfcheck PASS）不变。

## 关键决策与证据

1. **Part 身份与字段**：part_id（工程内唯一，p1/p2…）+ fingerprint
   （PartFingerprint，模块 12 的几何指纹）为匹配身份；name 仅供人读；
   source=源 STEP 路径、solid_index=源内实体序（MSB 文档序，与 occ tag
   升序对应，模块 12 实证）、solid_tag=运行期缓存（0=未解析）。
2. **材料引用语义**：`material_name=""` → 继承研究材料（旧版单部件语义的
   逐字段等价保留）；非空 → 材料库名（`find_material` 解析，未知抛 KeyError，
   不静默回退）。`set_part_material` 只写目标部件（验收⑥ per-part 隔离：
   改 p1=TC4 后 p2 仍 6061、研究材料仍 45钢，失败设置零污染）。
3. **旧版迁移**（验收②）：仅当「无 parts 且 study.geometry_file 非空」时迁移
   为 1 条（p1/零件/source=几何路径/fingerprint=None 待重导入补齐/
   material_name=""继承研究材料/visible=True），工程其余字段不动；幂等。
   `store.load_project` 与 `bundle open_bundle` 两条打开路径都调用
   （GUI 打开的是束，只迁 store 会在真实打开路径漏迁）。
4. **视口按部件着色/隔离**（评审采纳：不留零回归新 GL 行为）：三角形→部件
   归属按 FaceInfo.solid_tag（模块 12 固化的 face_owner 语义）；每点取所属
   三角部件序（点不跨部件共享——不同 B-rep 不合并且点不共享，模块 12 实证），
   `show_geometry(point_data={"part": …}, scalar_name="part", tess=过滤后细分)`
   字面复用同一 GL/Agg 链路。验收⑦实测（本机交互桌面，GL 就绪）：
   着色截图 std=85.30、两部件形心投影像素 max|ΔRGB|=89（两部件两色）；
   隔离截图 std=112.06、parts_shown=1、与着色画面像素不同；skip 分支按
   verify_viewport_gl.py 约定（GL 未就绪 → 显式打印 [SKIPPED] + 摘要
   「SKIPPED: 无交互桌面会话」+ exit 0 + Agg 降级路径信息性自检）。
5. **统一入口**：GUI「导入 STEP…」改走 `import_assembly`（AssemblyModel 仍是
   SolidModel，单实体回退 show_geometry）——零件工作流（网格/求解/归档）
   零改动，回归 verify_step_import/verify_project_roundtrip/verify_relaunch
   全部 PASS 佐证。

## 验收对应（全部在本次实际运行通过，verify_assembly_tree.log）

| 验收 | 结果 |
|---|---|
| ① round-trip | 2 部件全部字段（含 PartFingerprint 四元组、visible=False）逐字段相等；原子写无 .rm_tmp 残留 |
| ② 旧版迁移 | store 打开与 bundle 打开两路径均 parts=[1 条]（p1/零件/继承 45钢），其余字段不变，幂等 |
| ③ 未知 __type__ | parts 节内 NoSuchPart 与顶层未知类型均 StoreError |
| ④ selfcheck | exit 0；装配树 JSON 2 部件（Q235/6061、visible true、volume 8000）+「窗口构建成功」+「渲染后端=agg_fallback」 |
| ⑤ 静态扫描 | assembly_demo.py 已登记 GUI_FREE_ROOT_FILES；扫描 0 违规；core 链路无 PySide6 |
| ⑥ 材料隔离 | p1→TC4 后 p2/研究材料不变；未知材料/部件 KeyError；隔离状态 round-trip 保真 |
| ⑦ 交互段 | GL 就绪：着色 std=85.30 + 两色（ΔRGB=89）、隔离 std=112.06 + parts_shown=1 + 画面像素不同；PNG 均存在 |

## 回归（本次实际运行，全部 exit 0）

verify_main_entry / verify_step_import / verify_assembly_import /
verify_material_library / verify_load_definition / verify_project_roundtrip
（A/B/C 三进程真实求解闭环）/ verify_relaunch / verify_viewport_gl。

## 未做 / 留给后续模块

- 部件级约束/载荷挂接与多材料 deck（*SOLID SECTION 按部件 ELSET）——求解器
  当前仍用研究级单一材料。
- 装配树右键菜单/重命名/部件属性编辑对话框（当前：双击隔离/恢复 + core API）。
- 指纹 re-resolve：工程重开时按 part_matches_fingerprint 对部件重定位
  （挂接点：solver.project_workflow.reresolve_project 扩展）。
