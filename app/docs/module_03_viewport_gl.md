# 模块 3 设计说明：三维视口 GL 渲染与选面交互

日期：2026-11-02 ｜ 状态：已完成，`verify/verify_viewport_gl.py` exit 0（PASS，本机有交互桌面）。

## 范围与背景

评审 blocker5：native windows + `SetOffScreenRendering(1)` 配方此前仅
probes/probe4b_ui_native.py 人工跑过一次。本模块把该配方固化为**常设回归门**
`verify/verify_viewport_gl.py`，并交付产品级选面交互（与模块 2 的纯几何
`pick_face` API 分开验证）。

## 交付内容

- `ViewportBackend.GL` 更名 `gl_native_offscreen`（状态栏/日志用字符串，
  验收 ③ 直接断言该行）。
- `cad.SolidModel.tessellate_faces()`：显示用表面细分（2D 网格 → 三角形 +
  tri→occ面tag，提取后清网格，不影响几何 pick）。
- `ViewportHost.show_geometry(model, point_data, scalar_name)`：GL 走
  `PolyData(cell_data["face_tag"] + point_data 云图)`；Agg 走三角形出图
  （两条后端都能显示几何）。
- 产品选面链路：GL 部件 eventFilter（不消费事件，orbit 照常）→ 左键 →
  `vtkCellPicker`（Qt/VTK y 轴翻转）→ `cell_data["face_tag"]` → 红色高亮 →
  `face_picked` 信号 → 主窗口日志「选中面 tag=N」。
- 云图：`von_mises` 场由验收脚本按悬臂梁解析弯曲应力合成
  （`sigma=P(L−x)(y−5)/I`，峰值 60 MPa 与 probes/probe3 参考同源），
  明确标注为替代求解结果的演示场；求解模块交付后换成真实场。
- 截图/色标/相机/投影：`ViewportHost.screenshot / scalar_bar_visible /
  set_camera / project_point`。

## 本次实测发现并修复的 GL 回归点（已写入 ARCHITECTURE §6/§10）

1. **GL 部件必须显式加入布局**：不能用 `parent is None` 判断——QtInteractor
   创建时 parent 已是宿主，导致部件（连同离屏渲染窗口）停在 Qt 默认 100×30，
   投影/拾取坐标系全部错位。改为 `layout().indexOf(w) < 0` 判断。
2. **离屏渲染窗口不自动跟随部件尺寸**：`_sync_gl_size()` 在安装后、resize、
   投影、拾取、截图前显式 `ren_win.SetSize`。
3. **云图色标是 vtkActor2D**：在 `GetActors2D()` 集合，不在 `GetActors()`。
4. **拾取坐标 y 翻转**：Qt 左上原点 → VTK 左下原点（`h − y`）。

## skip 语义（验收 ④）

- 触发条件：Qt/GL 环境初始化失败，或 `ensure_viewport` 后 backend ≠
  `gl_native_offscreen`（offscreen 平台、无桌面会话、GL 构造/首帧失败）。
- 行为：exit 0；摘要行「VERIFY_VIEWPORT_GL: SKIPPED: 无交互桌面会话」；
  逐条打印 `[skip] 原因: …` 与 `[SKIPPED] …`（不静默）；附信息性 Agg 自检
  （失败仅告警）。skip 分支输出契约已用注入 `create_ui` 的方式单独验证。
- GL 就绪后的任何检查失败 = FAIL（exit 1），绝不转为 skip。

## 人工验收清单（在有交互桌面会话的机器上执行）

自动验收（verify_viewport_gl.py）覆盖渲染与合成点击；以下人手项测试真实
输入设备的交互手感与视觉正确性：

1. **启动显示**：桌面双击/运行 `python main.py` → 主窗口出现，状态栏
   「渲染后端=gl_native_offscreen」，视口区域非黑屏（无几何时为空场景）。
2. **导入上屏**：文件→导入 STEP… → 选择 `samples/beam_100x10x10.step` →
   梁体显示，云图（导入自动上屏为素色；云图由结果场驱动）与色标清晰可读。
3. **选面高亮**：左键单击 x=100 端面 → 该面变红；日志面板出现
   「视口点击 (x,y)：选中面 tag=2（occ 面）」与「选中面 tag=2」。
4. **高亮切换**：单击顶面 → 高亮切换到顶面；单击另一端 → 切到 tag=1。
5. **取消选中**：单击空白区域 → 红色高亮消失，状态栏「未命中表面」。
6. **orbit 不受扰**：按下并拖动 → 视角旋转流畅；点击（无拖动）不改变视角；
   拖动结束后的第一次单击拾取位置仍准确。
7. **缩放后拾取**：滚轮缩放/窗口拉伸后，重复第 3 项 → 仍命中正确面
   （渲染窗口尺寸同步生效）。
8. **多显示器/DPI 缩放**：在 125%/150% DPI 显示器上重复第 3 项（若命中偏移，
   记录 DPI 数值——高 DPI 映射属已知风险点）。
9. **退出干净**：关闭主窗口 → 进程正常退出，无 VTK/OpenGL 报错弹窗。

## 未做 / 留给后续模块

- 真实 FEM 结果场着色（等求解模块的 MeshData.point_data["von_mises"]）。
- 面信息面板联动（点选后在属性面板显示该面面积/法向/质心——数据已就绪）。
- 高 DPI 坐标映射专项验证（人工清单第 8 项如复现问题则立项修复）。
- 框选/多选面、隐藏/隔离部件。
