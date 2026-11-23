# 模块 8 设计说明：结果云图与数值查看

日期：2026-11-02 ｜ 状态：已完成，`verify/verify_results_view.py` exit 0（7/7，
全部对真实 ccx 求解产物断言）。

## 范围

- `solver/ccx2para.py`：ccx2paraview 3.2.0 的 .frd→.vtu 桥（numpy 2.5.3
  复数特征值 monkeypatch，probe3 配方；Converter 按文件基名写 CWD → chdir
  守卫串行化）；meshio 读回 point_data（键名别名归一 DISP/STRESS/S_Mises/FORC）；
  `mises_from_components` 供 S 分量独立复算。
- `solver/fields.py::build_result_fields(node_ids, results, frd_path) ->
  ResultFields`：**U / S_Mises / RF 三场**（行序 = 节点 tag 升序）。
  主解析 .dat（RF/U 分块），辅解析 .frd→vtu（U/S_Mises）；RF 全节点数组
  自由节点填 0（占位语义，真值在 rf_table）。
- 渲染：`report/plot_aggs.render_field_png`——标量云图 + 可选变形缩放 +
  **放大倍数与单位标注**（调研报告 §4），纯 Agg（probe8 配方，零 GL）。
- GUI：求解完成后自动回收三场并显示 von Mises 云图；求解菜单新增
  von Mises 云图 / 位移云图（变形缩放倍数输入）/ 支反力合力表 / 结果极值面板
  （gui/result_dialogs.py）；GL 后端同路径（warp + add_text 标注）。

## 关键决策与实证

1. **结果显示表面 = 体积网格边界面**（`mesh_data.boundary_faces_corners`，
   probe8 角点三角配方 + 位置索引），与三场行同源对齐；**禁止**用 cad 表面
   细分（节点 1285 ≠ 场 11222，实测混用即 broadcast 错误）。
2. **⑥ 同量位移模门（修正 probe8 的混量比法）**：渲染路径标注的 max|U|
   矢量模 vs 从 .frd 独立直解析（solver.results 定宽列解析器，不经
   ccx2paraview/meshio）的 max|U| 矢量模——同量纲同定义，实测差 **0.00e+00**
   （两条独立提取链：ccx2paraview→vtu→meshio vs 自研 frd 解析器）；
   beamCheck tipUxyz 复算 |U|=0.1910625 为端部节点值，全域 max=0.191076 与
   probe8 基线一致。
3. **节点 tag vs 位置索引**延续模块 7 的教训：三场行序 = 节点 tag 升序；
   任何按位置的访问必须经 tag→位置映射（fields 内部 pos_of，verify 的
   tag_pos）。③（场 vs .dat 4e-7）与 ④（RF 场 vs rf_table 逐节点一致）
   实证对齐无误。
4. **von Mises 双源校核**：数据模型 S_Mises（vtu 直读）与 S 分量独立复算
   （mises_from_components）峰值逐位一致（61.905007 MPa），对梁理论
   σ=Mc/I=60MPa 误差 3.18%（probe3 门 ≤10%）。

## 实测数据（verify/logs/verify_results_view.log + beam_displaced_annotated.png）

| 验收 | 实测 |
|---|---|
| ① 三场 | U (11222,3)、S_Mises (11222,)、RF (11222,3)+rf_table 153 节点 |
| ② von Mises | 61.905007 MPa，与 vtu 峰值差 0（1e-6），对理论 3.18%（≤10%） |
| ③ 端部 Uy | 场 −0.190494 vs .dat −0.190494，差 4.00e-07 mm |
| ④ 支反力 | ΣFy=99.999991（±0.01）；合力=逐节点求和逐元素一致 |
| ⑤ 标注出图 | PNG 103863 bytes，std=31.41；标注含「放大 100×」「单位: mm」 |
| ⑥ 同量位移模 | 差 0.00e+00 mm（≤1e-3） |

## 未做 / 留给后续模块

- GL 后端的矢量箭头绘制（支反力矢量当前以表格/极值面板呈现；GL 箭头 actor
  留结果查看增强）。
- 计算报告文档生成（report 层消费 extreme_lines/stats 的文档化输出）。
- 多工况结果对比、云图图例字体等展示细化。
