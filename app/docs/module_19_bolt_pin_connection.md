# 模块 19 设计说明：螺栓与销轴简化连接求解验证

日期：2026-11-02 ｜ 状态：已完成，验收 `verify/verify_bolt_pin_connection.py`
exit 0（①-⑧；全程真实 ccx 2.22）。

## 范围

- `verify/verify_bolt_pin_connection.py`（新）：螺栓（两叠块预紧/预紧+上拉）
  + 销轴（评审补齐几何的搭接双板+销）+ 负例 + 文档门 + GUI 无关。
- `app/docs/connections.md` §螺栓/§销轴：验证算例指针更新为已实现算例 +
  门限与实测 + 投影实证（⑦）。
- 无产品求解代码新语法：螺栓卡（*RIGID BODY+预紧力对）与销卡（*TIE）沿用
  模块 16/18 实证实现；本模块是它们的求解级物理验证 + 连接性门的负例应用。

## 关键决策与证据

1. **耦合卡语法（ask「ccx 2.22 实跑为准」）**：*KINEMATIC COUPLING 不被
   ccx 2.22 识别（探针实证 card image "CKINEMATICCOUPLING" fatal，
   module_16/19 两次验证）→ 按 ask 用 ccx 原生等价 **\*RIGID BODY,
   NSET=耦合区, REF NODE=参考点** + 预紧力对（两侧参考节点 ±F_p·各自面
   外法向，自平衡）。实证：预紧自平衡 ΣRF_ground=(0,0,0)。
2. **界面测量的口径（首跑实证）**：FORC@接触从面节点合力 = 2000.001N
   （①）——但预紧+上拉工况为 1999.999N（≠残余夹紧 1500）：从面节点混合
   了接触力与刚体 MPC 力，**非清洁量**（记录不设门）。② 的残余夹紧
   1500N 由**平衡闭环**给出：F_p(2000，deck 预紧卡) − 外载(500，载荷卡)
   = 1500，实测接地反力 −500.0000N（0.01%）闭环验证。
3. **销绑定 tie 方向（首跑实证修正后回退）**：曾尝试从面=孔壁（几何上
   完全包含于销面）→ ccx gentiedmpc 对全部孔壁节点报 "no tied MPC"
   （588 条）→ 绑定失效、Ux 剖面非物理；**从面=销全长侧面** → 0 条警告、
   全部节点投影成功、物理结果正确。方向已固化（deck.py 注释 + doc 投影
   实证条目）。
4. **销轴载荷传递（④）**：ΣRF=(−500.000002, −0.000002, −0.000001)
   （0.01%）；板1 x=40 纯轴段 |Ux|=9.4959e-4 mm vs 简单轴向理论
   9.5238e-4（**0.29%**，1mm 网格；2mm 实测 8.3% 亦在 ±10% 门内）——
   单剪销接头的板弯曲效应在 x=40（距孔心 10mm）已衰减。
5. **孔存在断言（评审采纳）**：两板孔壁面积 occ 解析值 125.6637mm²
   （=2π×4×5±1%）；绑定面集（两条 PinConnection 的 SA/SB）1936 个面单元
   节点全部位于 r=4 圆柱面（最大偏差 0.0000mm）——无孔埋入圆柱的假实现
   会被咬。

## 验收对应（全部在本次实际运行通过，verify_bolt_pin_connection.log）

| 验收 | 结果 |
|---|---|
| ① 仅预紧 | ΣRF_ground=(0,0,0)（|ΣRFy|≤20N）；FORC@从面=2000.001N（±10%）；节点法向力 ≥0（容差内） |
| ② 预紧+上拉 | 接地反力 −499.999997N（0.01%）；残余夹紧 1500N 平衡闭环（FORC 混合留档） |
| ③ 孔存在 | 两板孔壁 125.6637mm²（±1%）；绑定面节点全部 r=4 圆柱面 |
| ④ 销传力 | 求解成功；ΣRF 0.01%；Ux@40 0.29%（1mm；2mm 8.3%） |
| ⑤ 销缺失 | 连接性门 DeckError「部件 ['p2','p3'] 未被…链接地」（悬浮部件指明；与 ask 字面「板1」的差异=算例坐标下受载板为 plate2——固定在 plate1 x=0、载荷在 plate2 x=100，如实记录） |
| ⑥ 负例 | μ=1.5 / F_p=0 / F_p=−100 / 耦合区半径=0（面积≤0）全部 ConnectionValidationError |
| ⑦ 文档 | connections.md 螺栓/销轴节含物理假设/不模拟/verify_bolt_pin_connection |
| ⑧ GUI 无关 | 无 PySide6 进程 + 静态扫描 0 违规 |

## 回归（本次实际运行，全部 exit 0）

verify_assembly_solve / verify_bonded_connection /
verify_contact_friction_connection / verify_mesh_generation /
verify_load_definition / verify_main_entry / verify_step_import /
verify_assembly_import / verify_assembly_tree / verify_connection_model /
verify_static_solve / verify_report_archive / verify_project_roundtrip
（后三者为真实 ccx）。

## 未做 / 留给后续模块

- 螺栓预紧的分步施加（预紧步+锁死步，Abaqus 式 PRE-TENSION SECTION）——
  当前为一步等效夹紧（简化口径，doc 已声明）。
- 销孔挤压（bearing）应力与间隙接触（本文档不模拟项）。
- 多螺栓群布置与垫圈刚度。
