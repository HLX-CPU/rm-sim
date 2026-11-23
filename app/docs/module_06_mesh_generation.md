# 模块 6 设计说明：自动网格划分与 INP 导出

日期：2026-11-02 ｜ 状态：已完成，`verify/verify_mesh_generation.py` exit 0（8/8，
逐项命中 probes/probe1 + probes/sets.json 基线）。

## 范围

- `mesh/mesher.py`（GUI 无关层）：`generate_tet10_mesh(geom, mesh_size_mm=2.0,
  nset_faces=None, inp_path=None) -> MeshResult`——gmsh 3D 二阶四面体 C3D10、
  质量统计、三坑规避 INP 导出、按节点坐标自建面 NSET。
- `MeshStats`（统计接口）：单元/节点数、类型、MeshSize、**负 Jacobian 单元数、
  min/mean SICN**；`report_lines()` 供报告层 `report/mesh_report.py::
  mesh_stats_section` 与 GUI 日志复用（质量缺口补齐）。
- GUI：网格菜单启用「生成网格并导出 INP…」（尺寸输入 + 保存对话框 +
  从工况面挂接推导 NSET + 统计入日志）。

## 关键决策（全部有 probes 实证）

1. **复刻 probe1 网格配方**：`Mesh.MeshSizeMin=Max=mesh_size`（均匀网格）+
   `Mesh.ElementOrder=2` → 标准梁 2mm 实测 **6481 C3D10 / 11222 节点**，
   与 probe1/beamCheck 基线完全一致；档位粗 4 / 中 2（默认）/ 细 1。
2. **三坑规避**（config.json gmshInpExportNote）：
   - 坑 1：`Mesh.SaveAll=1` 下 gmsh 会写 1D T3D3 / 2D CPS6 块 → ccx
     zero-thickness 报错。`_filter_mesh_inp` 只保留 *NODE 与
     *ELEMENT,TYPE=C3D10（probe2 过滤器的产品化版本，大小写无关），
     其余关键字块全丢；
   - 坑 2：物理组只出 ELSET → 面 NSET **按节点坐标自建**（平面距离 ≤1e-6
     且在面 bbox 内，probe1 容差），并重建 `*ELSET,ELSET=Eall`（probe2 的
     10 id/行格式）；
   - 坑 3：C3D10 节点序沿用 gmsh 自身 .inp 连接表（probe1→probe2→ccx
     beamCheck 链路实证）——**仅对本网格实证，换单元类型/阶次/网格器必须
     重定基线**（验收 ④ 同款警示）。
3. **一致性自检内建**：过滤后单元 id 集与提取网格逐一对账
   （不一致即 MeshGenerationError），保证统计与 .inp 永远同源（验收 ⑥）。
4. **质量指标**：`getElementQualities(minSICN / minDetJac)`。实测本版
   gmsh 4.15.2 **没有 "minJacob" 名**（一次性探针确认）——负 Jacobian 计数用
   `minDetJac<0`（Jacobian 行列式，语义等价）。质量门：标准梁 2mm
   负 Jacobian=0、min SICN=0.3056（≥0.1）；4mm 档 0.6324 同过。
5. **层间关系**：mesher 以 duck-typing 接收 cad.SolidModel（不 import cad）；
   `volume_mesh`/`MeshData` 为共享数据结构；报告层 `mesh_stats_section`
   duck-typing 接收统计对象。

## 实测数据（verify/logs/verify_mesh_generation.log + beam_mesh_2mm.inp）

| 验收 | 结果 |
|---|---|
| ① 坑回归门 | 无 T3D3/C3D4/CPS6；*ELEMENT 仅 C3D10 一个块；*ELSET,ELSET=Eall 含全部 6481 单元；FIXED/LOADFACE NSET 存在 |
| ② 数量 | 6481 ∈ [5000,9000]；11222 ≥ 9000（与 probe 基线一致） |
| ③ 节点 bbox | (0,0,0)-(100,10,10)，各向 1e-6 内 |
| ④ NSET 基线 | FIXED=153、LOADFACE=153（sets.json 基线 ±5 内，实际精确相等） |
| ⑤ 加密趋势 | 4mm → 944 单元 < 3240.5（0.5×6481）；档位定义正确 |
| ⑥ 统计一致 | element/node/type/size 与 .inp 解析逐项相等；连接表全 10 节点 |
| ⑦ 质量门 | 负 Jacobian 0；min SICN 0.3056 ≥ 0.1（4mm 档同过） |

## 未做 / 留给后续模块

- 求解模块把 NSET/ELSET/Eall/材料/工况转换成完整 .inp 并调度 ccx。
- 局部加密（孔边/圆角尺寸场）、网格参数持久化到工程（当前每次输入尺寸）。
- 多实体网格与按实体 ELSET 拆分（装配体模块）。
- 曲面几何（非盒体）的边界面归类歧义裁决已实现（distance_fn），
  但基线仅对盒体实证——新几何类型上线时按 ④ 的警示重定基线。
