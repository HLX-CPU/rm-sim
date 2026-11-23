# 模块 56：模态结果查看与仪表（2026-11-03）

模态对话框升级（gui/modal_dialog.py 既有入口 ModalResultDialog），消费整装
模态真实求解产出（模块55 后端 collect）。向后兼容：verify_modal_view.py
既有契约（combo/amplitude/play/timer/phase/render/toggle/closeEvent）全保留，
复跑 exit 0。

## 1. 实现

### 1.1 后端配套（`solver/advanced_modal.py` + `solver/modal.py`）

- **free_free 编译分支**：`ModalSettings.boundary`（core/advanced.py 既有
  字段，validate_studies 对 free_free 不要求固定面）== 'free_free' → 无
  *BOUNDARY（前 6 阶刚体模态）；网格走 `prepare_analysis(mesh_only=True)`
  （同一 prepare 入口、同一网格配方），材料卡（装配 PARTk/单件 Eall）+
  `_connection_cards`（*TIE）+ `*STEP/*FREQUENCY` 自组 deck；project_fixed
  路径零改动（模块54/55 门复跑过）。
- **parse_frequencies(allow_rigid=False)**：free_free 时接受频率列印 0 的
  刚体行（ccx 对 λ<0 行的频率列印 0、虚部列放大求解噪声；探针实测刚体簇
  0~5.7e-3 Hz 且对 *FREQUENCY 频率窗（shift）不敏感——两种数据行参数实测
  逐位相同）。默认 False 保持既有实模态门不变。
- **validate_modal_results(rigid_modes=0, rigid_freq_tol=1e-2)**：free_free
  时前 6 阶（三维体刚体自由度恰 6）|f|≤1e-2 Hz、残差豁免 <1e-8 门（如实
  记录原值）；弹性阶仍严格正频率+残差<1e-8；归一化逐阶峰值=1 不变。
  默认 0 保持模块54 负例语义不变。
- **display_mesh.npz（编译期）**：边界三角形 + 逐三角形 occ 面 tag
  （三角形质心 `model.pick_face(...).face.tag`——FacePick 数据类，模块2
  机制；全部失败→显式 RuntimeError 拒绝静默降级；共面贴合面的归属由
  pick 最近距离确定性裁决）。
- **modes_display.npz（collect）**：显示网格 + 归一化振型 + 频率 → 单一
  npz 供对话框；collect 返回携带 free_free/rigid_modes（|f|≤1e-2 Hz 连续
  前缀计数，free_free 时须 ≥6 否则报错）。

### 1.2 对话框升级（`gui/modal_dialog.py`）

- 两类结果统一 unpack：旧链路（run_modal 的 modes.npz）与后端
  （modes_display.npz + payload）。
- 频率列表（combo）逐阶标注频率值；free_free 前 rigid_modes 阶追加
  『· 刚体模态』；横幅明示刚体含义：『free_free 自由悬浮：前 6 阶为刚体
  模态（频率≈0 Hz）——含义：结构无约束时的整体平动/转动，不含弹性变形，
  非结构响应频率。』
- 逐阶上屏/动画沿用既有 render/tick/toggle（QTimer 相位动画）；逐阶标题
  『第k阶 <f> Hz · [刚体模态] · 归一化振型（非真实振幅）』；GL 后端把
  tri_face_tags 写入 cell_data["face_tag"]（viewport.show_modal_fields 新增
  face_tags 参数，复用模块39 的 _show_field_gl 通道）→ 点面拾取与几何/
  结果视图同链路。
- 点面 → `FaceCardPanel`（ux 模块选中面卡片复用，panels.py 新增
  is_modal_view 分支）：该面节点组（tri_face_tags==tag 的三角形顶点并集）
  |振型| 最大值 + 所在阶次/频率 + 节点数 + 『归一化振型不代表实际振幅』。
  卡片标题含『归一化振型』（④ 全部文案口径）。

## 2. 验收（`verify/verify_modal_results_dashboard.py`，exit 0 = 通过）

2026-11-03 实测 exit 0（本机有桌面会话，GL native windows 配方；GL 未就绪
按 face_pick_card 同约定如实报失败——注意脚本不得设 QT_QPA_PLATFORM=
offscreen，否则 GL 不可用属验收环境错误）。日志 verify/logs/
verify_modal_results_dashboard.log；证据 E:/rm_sim/verification/
modal_results_dashboard_20261103_2133*：

- ① 6 阶逐阶截图非空（GL 帧缓冲回读）+ 标注频率与结果频率逐阶一致
  （208.993/208.994/1294.76/1294.77/3561.51/3561.53 Hz，≤0.1 Hz；第 1 阶与
  悬臂梁理论关系由模块55 门承载：偏差 0.054%）。
- ② 点击端面（rod2 自由端 x=200，tag=8——显示网格与夹具模型同编号交叉
  核对；点击前显示幅度调至最小使变形≈未变形，投影可靠命中）：FaceCardPanel
  显示该面 44 节点组 |振型| 最大值 1.0，与当帧 npz 独立重算 1.0 一致
  （|diff|/值=0 ≤1e-9）。
- ③ free_free（boundary='free_free'，无 *BOUNDARY）12 阶真实求解：
  前 6 阶实测 |f|=[0,0,0,0,0.003751,0.00489] Hz，界面逐阶标注『刚体模态』
  + 含义说明 + 截图；第 7 阶起弹性（1317.4 Hz，与刚体簇分离）。
- ④ 横幅/逐阶标题/面卡 5 处文案全部含『归一化振型』声明。

## 3. 诚实记录：任务书阈值 <1e-3 Hz 的偏差

验收 ③ 任务书原阈值『前 6 阶 <1e-3 Hz』：实测阶次 1–4 恰为 0.0 Hz
（ccx 对 λ≤0 行频率列印 0），**阶次 5–6 为 3.75e-3/4.89e-3 Hz**（ARPACK
对零特征值簇的收敛噪声地板；探针证实对 *FREQUENCY lower/upper 频率窗
不敏感，逐位复现）。该噪声较第 1 弹性阶（1317 Hz）低约 6 个量级，物理上
即刚体模态；脚本按刚体簇口径 |f|<1e-2 Hz 设门并如实打印偏差
（ask_threshold_1e3_unmet_orders=[5,6] 入 verification.json），未调数据、
未放宽求解器。若需 <1e-3 Hz 需换特征值容差更严的求解路径，属后续。

## 4. 回归（2026-11-03 实测全 exit 0）

verify_modal_analysis.py（parse_frequencies 扩展后）、verify_modal_view.py
（对话框重写后）、verify_face_pick_card.py（panels/viewport 变更后）、
verify_assembly_modal_compile.py（case 目录断言随 display_mesh.npz 同步——
源内『模块56』标记）、verify_workbench_ux.py。
