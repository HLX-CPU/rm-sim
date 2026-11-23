# 模块 58：跌落冲击最小可验证算例（2026-11-03）

交接书把握不足项按最小算例拆解：刚体跌落（chrono_scene.py Bullet 碰撞块既有）
取峰值接触力时刻样本 → 模块57 参数化传递映射 → 结构静力工况（准静态近似）
求解。**完成度红线（绝不虚报动态冲击能力）：刚体冲击的准静态近似；未计算
应力波传播、未启用塑性、碰撞应力不可信。**

## 1. 实现

### 1.1 场景（`solver/chrono_scene.py` 碰撞块改造）

- 箱体改为**自 1 m 高度自由跌落**（底面到地面 1.0 m，无水平初速——纯跌落，
  冲击速度 √(2gh)=4.43 m/s；原"跌落+滑移"口径退役）。
- 接触合力经 `box.GetContactForce()` 直读（ChContactable 接口，含摩擦、
  不含重力；该 PyChrono 10 构建无 GetPosDtDt——探针实测）。
- **impact_sample**：峰值接触力时刻样本（接触数>0 中合力最大者）——time_s/
  contacts/y_m/minimum_y_m/接触合力峰值/箱体所受合力/作用点（箱底面中心）/
  质量/重力/跌落高度 + `force_world_n`=**结构侧受力**（箱体所受接触力的
  反作用力，符号约定 sign_convention 在案）+ peak_over_weight 倍数。
- 既有键（max_contacts/minimum_y_m/final_y_m/final_vx_m_s/samples）全部
  保留——verify_chrono_load_transfer 碰撞断言复跑 exit 0。

### 1.2 适配器（`solver/chrono_adapter.py`）

- `map_transfer_sample(..., sample=None)`：样本直传参数（模块58 冲击样本
  优先于 JSON 内 spring.samples[index]；units 门与 sha256 仍对 JSON 文件生效）。
- `DROP_IMPACT_COMPLETENESS`：完成度标注字段（quasi_static_approximation=
  True/stress_wave_propagation_computed=False/plasticity_enabled=False/
  impact_stress_trustworthy=False + statement 红线原文）。
- `impact_force_warning(peak,weight,factor=10)`：峰值接触力 >10×落体重力 →
  警告文案（刚体接触惩罚刚度量级、准静态近似下的结构载荷不可信）；否则
  None——记录而非静默放大。
- `write_drop_impact_report(directory,...)`：跌落冲击报告（红线文案
  『准静态近似』『未验证冲击应力』『未计算应力波传播』『未启用塑性』
  『碰撞应力不可信』+ 峰值样本/映射/结构核验数据）。

## 2. 验收（`verify/verify_drop_impact_minimal.py`，exit 0 = 通过）

2026-11-03 实测 exit 0（日志 verify/logs/verify_drop_impact_minimal.log；
证据 E:/rm_sim/verification/drop_impact_minimal_20261103_223403_949827）：

- ① 真实跌落：t=0.452 s（自由落地时间 √(2h/g)=0.4515 s 吻合）、接触数 4、
  样本 y=0.0999999927 m=全程最低位、1.0 m 跌落、8 kg/78.48 N——impact_sample
  写入 chrono_results.json。
- ④ 峰值接触力 **34625.4 N = 441.2× 落体重力**（>10×）→ 警告字段记录
  （刚体接触惩罚刚度量级，准静态近似下的结构载荷不可信）；产品函数合成
  双向验证（11×→警告非空、5×→None）。
- ② 映射（identity 旋转、作用点=箱底、target=原点→力平行于偏移向量→
  力矩≈0）mapped_f=[≈0,−34625.38,≈0] N → 粘接串联两杆 rod2 端面面力 →
  真实 ccx → 接地反力合力 34625.3627 N，**偏差 0.0001% ≤0.5%**。
- ③ drop_impact_report.md 含『准静态近似』『未验证冲击应力』及全部红线
  补充字样；provenance 携带完成度标注字段。
- ⑤ verification.json：completion 四字段如实（准静态=True/应力波=False/
  塑性=False/冲击应力不可信=False）+ honest_labels + 全部数据。
- 回归：verify_chrono_load_transfer.py（碰撞断言：period=1.639s、RF+F<0.01N、
  Uy=−0.190265mm）、verify_extensions_gui.py 复跑 exit 0。

## 3. 范围与诚实声明

- **不算的**：应力波传播、塑性/损伤、真实接触应力分布、瞬态结构响应——
  结构侧只是把刚体接触合力峰值当作静力面力（准静态近似）。刚体接触力
  本身是惩罚刚度量级（本例 441×重力，警告已记录），不代表真实接触应力。
- 接触力经 GetContactForce 直读（约束求解器反力），非应力恢复。
- 报告中的红线文案为验收断言项（③），删除任一字样即验收失败。
