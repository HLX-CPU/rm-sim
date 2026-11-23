# 模块 59：塑性变形最小可验证算例（2026-11-03）

把握不足、拆最小算例如实标注：CalculiX *PLASTIC 双线性模型（Q235 屈服
235 MPa，切线模量取常用估值 **E/100=2100 MPa，未经材料试验标定**）。
**完成度红线：塑性参数未经材料试验标定；损伤（DamageSettings）本轮不做
——损伤分析未完成。** 几何线性（小应变）口径，未启用 NLGEOM。

## 1. 实现（`solver/plastic_minimal.py` 新建）

- `material_block(..., enabled)`：*MATERIAL/*ELASTIC + *PLASTIC（enabled 门）。
  *PLASTIC 表（等效应变,真实应力）优先取 PlasticCurve.points（同序直用），
  否则双线性估值表 (σ_y,0)→(σ_y+E_t·0.5, 0.5)（末段斜率=E_t）。
  **回归保护（③）**：`NonlinearSettings.plasticity_enabled=False` → 不生成
  *PLASTIC 卡（实测断言双向：False 无卡 / True 有卡）。
- `build_single_element_inp`：1×1×1 mm C3D8 单轴（侧面自由、最小刚体约束），
  四步：拉至 1.5×σ_y → **自由卸载** → 再加载两级（均在塑性段）。
  关键教训（首跑实测）：卸载若强压 u=0，总应变 0 与残余塑性应变矛盾 →
  ~−11750 MPa 压应力 → 反向塑性流动 PEEQ 翻倍——非物理。修正为
  `*BOUNDARY, OP=NEW` 且不再约束加载面（自由弹回，σ→0，u_resid=ε_p·L）。
- `build_cantilever_inp`：10×10×100 悬臂梁结构化 C3D8R 网格（40×4×4，减缩
  积分——C3D8 全积分弯曲剪切锁死实测使极限载荷偏高 18.5%），根部全固定，
  自由端 u_y=−1.5 mm 位移控制，*NSET 数据行 ≤8 项/行（CalculiX ≤16 项
  限制实测）。
- DAT 解析：`parse_dat_blocks`（*EL PRINT/*NODE PRINT 块按增量输出）+
  `step_end_blocks(..., steps=N)`——DAT time=总时间，步 k 步末块 time≈k；
  任一步缺失返回 []（不静默取错块）。
- `write_plasticity_report`：红线文案（『塑性参数未经材料试验标定』
  『损伤分析未完成』）+ 两算例结果 + 负例结论。

## 2. 验收（`verify/verify_plasticity_minimal.py`，exit 0 = 通过）

2026-11-03 实测 exit 0（日志 verify/logs/verify_plasticity_minimal.log；
证据 E:/rm_sim/verification/plasticity_minimal_20261103_230124_549536）：

- ① σ1=352.5 MPa=1.5×屈服（精确）；**自由卸载后 PEEQ=0.05595238 vs 理论
  ε_p=ε_total−σ/E=0.055952381（偏差 0.000% ≤2%）**；再加载塑性段两级之间
  割线刚度 2079.21 vs 切线模量 2100（偏差 0.990% ≤5%）；σ_a=399.01、
  σ_b=440.594 与各向同性硬化理论式 σ=[σ_y+E_t·ε]/(1+E_t/E) 逐位一致。
- ② 极限载荷 P_FEM=593.246 N vs 塑性铰理论 P=M_p/L=587.5 N
  （M_p=σ_y·b·h²/4=58750 N·mm）**偏差 0.978% ≤10%**；根部 max
  PEEQ=0.00139>0（塑性铰证据）。
- ③ 负例双向：plasticity_enabled=False → INP 无 *PLASTIC（有 *ELASTIC）；
  =True → *PLASTIC 在案。
- ④ 报告含『塑性参数未经材料试验标定』『损伤分析未完成』。
- ⑤ INP/DAT/log 复制进证据目录 + verification.json（completion 标注 +
  两算例全部数据）。

## 3. 范围与诚实声明

- 双线性各向同性硬化（CalculiX *PLASTIC 默认）——无循环塑性/随动硬化
  （Chaboche 等），卸载-再加载的包辛格效应不可模拟。
- 切线模量 E/100 为常用估值：**未经材料试验标定**，不可用于寿命/成形评估。
- 损伤（DamageSettings：ductile_scalar/failure_plastic_strain/fracture_
  energy）本轮不实现，未生成任何损伤卡/损伤变量输出——『损伤分析未完成』。
- 几何线性小应变；大变形/ NLGEOM 未启用。
- 悬臂梁极限载荷对照在 u=−1.5 mm（0.015L）塑性铰充分发展处取值；
  更大位移下硬化使载荷继续爬升（偏离 EPP 理论，如实记录不外推）。
