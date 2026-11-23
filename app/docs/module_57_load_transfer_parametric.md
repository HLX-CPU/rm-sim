# 模块 57：运动载荷传递参数化（2026-11-03）

transfer_load 由硬编码梁示例+固定 90° 旋转向参数化为 LoadTransferSpec 驱动
（core/advanced.py:95-103：rotation/translation_mm/source_point_mm/
target_point_mm），映射复用 advanced_history.transfer_wrench（含作用点平移项）。
既有梁示例入口签名/行为零破坏（两道 chrono 既有门复跑 exit 0）。

## 1. 实现（`solver/chrono_adapter.py`）

- **DEFAULT_TRANSFER_SPEC**：既有 90° 映射（绕 Z 正转，Chrono -X → 结构 -Y）
  以 LoadTransferSpec 表达（source/target=原点、平移=0 → 平移项恰为零）——
  梁示例路径经同一参数化代码，数值逐位不变。
- **map_transfer_sample(source_json, spec, sample_index=0)**（不求解）：
  - units 门 m-kg-s-N（其他值 ValueError 拒绝，既有口径保留）；
  - 样本 force_world_n 必需；moment_world_nm/source_point_m 可选（既有场景
    样本无此两字段→力矩取零、作用点回退 spec.source_point_mm；优先级
    样本 > spec，回放口径记入 provenance.application_point_source）；
    Chrono 作用点 m→mm ×1000；
  - 映射 = transfer_wrench(force, moment, spec.rotation, spec.translation_mm,
    source_point, spec.target_point_mm)：结构力=r@F（N），结构力矩=
    r@(1000·m)+(r@source+t−target)×(r@F)（N·m→N·mm，作用点平移项）；
  - 返回映射向量 + provenance 核心（source_sha256/rotation/translation_mm/
    source_point_mm/target_point_mm/time_s/源与映射向量/
    spec_target_part_id）。
- **write_transfer_provenance(project_dir, provenance)**：load_transfer.json
  落盘（既有字段模式保持，新增字段为加法）。
- **transfer_load**（既有签名）：内部改走 map_transfer_sample(DEFAULT_TRANSFER_SPEC)
  + new_solve_project（结构侧不变）；provenance 既有键全保留 + 加法新键。

## 2. 验收（`verify/verify_load_transfer_parametric.py`，exit 0 = 通过）

2026-11-03 实测 exit 0（日志 verify/logs/verify_load_transfer_parametric.log；
证据 E:/rm_sim/verification/load_transfer_parametric_20261103_221548_898164）：

- ① 合成 Chrono JSON（F=[3,−4,5] N、p=[0.05,0.02,−0.03] m、m=[0.5,−0.25,1]
  N·m）+ spec（90°Z 旋转、t=(10,0,0)、target=(200,5,5)）：映射力 [4,3,5]=R@F、
  幅值 7.07106781187 不变；映射力矩 [580,1410,190]=R@(1000m)+(Rp+t−target)×(R@F)
  （手算一致，≤1e-9）；样本作用点优先口径生效。
- ② units='N-mm-s-t' → ValueError『动力学源单位不明，拒绝映射』。
- ③ 端到端：run_chrono 真实场景（弹簧样本 t=0.101s，source_force=[−9.5498,
  0,0] N，作用点=质量块位置 x_m m→mm）→ spec 映射（mapped_f=[0,−9.5498,0] N、
  mapped_m=[−47.75,0,1909.96] N·mm 含平移项）→ 粘接串联两杆装配（rod2 端面
  TractionLoad+MomentLoad）→ 真实 ccx 静力 → 接地反力合力 9.549813 N =
  |映射力| 9.549813 N，**偏差 0.0000% ≤0.5%**。
- ④ provenance JSON：source_sha256 与来源文件一致；rotation/time_s/
  translation_mm/source_point_mm/target_point_mm 全在案；回放复算
  mapped_force=R@source_force、mapped_moment 含平移项（≤1e-9）；
  structural 指向真实 case（INP/Job finished 日志在案）。
- ⑤ spec 校验负例（advanced_validation 既有规则实跑）：缺 mechanism 引用、
  『传递源刚体不存在』、『结构载荷目标部件不存在』、『载荷变换须为右手
  正交旋转矩阵』各负例均拒绝。

## 3. 范围与诚实声明

- 样本力矩/作用点字段（moment_world_nm/source_point_m）为本模块新增的可选
  约定：既有 chrono_scene 场景样本不含（力矩取零、作用点走 spec）——Chrono
  侧真实力矩输出的采集属后续场景扩展。
- 结构侧力偶经既有 MomentLoad（面等效节点力偶）施加；接地反力**合力**门
  不受力偶影响（自平衡）。反力矩的逐项守恒核验未设门（等效节点力偶对轴
  矩守恒已由模块5/7 门承载）。
- 参数化映射的结构侧组装当前由调用方完成（verify 脚本用产品载荷类拼装）；
  GUI/工作台的 spec 编辑与批处理 action 属后续接续点（transfer_load 既有
  batch/GUI 入口不受影响）。

## 4. 回归（2026-11-03 实测 exit 0）

verify_chrono_load_transfer.py（重构后 legacy 路径：period=1.639s、RF+F
残差<0.01N、Uy=−0.190265mm、provenance rotation/sha256/mapped_force 断言
全过）、verify_extensions_gui.py（主窗口 Chrono/映射/模态入口真实执行）。
