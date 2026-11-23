# 模块 1 设计说明：统一主入口与主窗口框架

日期：2026-11-02 ｜ 状态：已完成，验收 `verify/verify_main_entry.py` exit 0。

## 范围

- `main.py` 统一入口（根目录）+ `python -m app` 等效入口。
- `app/rm_sim` 包骨架：core（数据模型/持久化）、mesh、solver、report 四层
  GUI 无关；gui 层含 PySide6 主窗口（菜单/工况树/属性面板/状态栏/日志面板）
  与 pyvista 视口（懒加载 + Agg 降级）。
- `app/ARCHITECTURE.md` 目录约定与分层规则；本设计文档。

## 关键决策

1. **包位置与引导**：主包放 `app/rm_sim`（约定"新代码放 app/"）。根 `main.py`
   与 `app/__main__.py` 都是薄壳，只把 `app\` 插入 `sys.path` 后调
   `rm_sim.main:main()`，保证两种启动方式行为一致、入口逻辑单点维护。
2. **视口工厂契约**（`gui/viewport.py`）：`factory(parent) -> GLViewportHandle`
   （widget / render_first_frame / close / plotter）。`set_factory()` 是验收
   注入点；`ensure_viewport()` 用**同一个 try** 包住「构造 → 安装 → 首帧」，
   直接对应 probes/probe4_log.txt 的证据（offscreen 下构造期抛
   RenderWindowUnavailable）与"构造成功但首帧 paint 崩溃"的漏网情形（验收 ④
   的首帧注入）。首帧 = render + screenshot 帧缓冲回读，确保首帧真的画过。
3. **GL 配方照抄已验证探针**：native windows 平台 + `ren_win.SetOffScreenRendering(1)`
   （probe4b_ui_native.py 第 44 行）；pyvista/pyvistaqt 延迟导入，导入失败也降级。
4. **Agg 降级照抄 probe8 配方**：`boundary_triangles`（角点三角去重计次）→
   `report/plot_aggs.render_mesh_png`（MPLBACKEND=Agg 先于 pyplot 导入）→
   QPixmap。降级画布在 selfcheck 里用 demo 立方体（12 tetra4 → 12 边界三角）
   真实出图一次（png_bytes 断言 > 1000），不是"看起来能画"。
5. **GUI 无关层强制**：`layer_guard.py` ast 静态扫描（selfcheck [3/5] + verify
   步骤 F）+ verify 步骤 E 的毒化子进程（sys.modules 注入 None 后导入全层并跑
   持久化往返）。`rm_sim/main.py`（入口胶水）允许 import gui，不在扫描范围，
   这是有意为之并在 ARCHITECTURE.md §2 注明。
6. **退出码/输出契约**：selfcheck 0/2；stdout 字面量「窗口构建成功」、
   「渲染后端=agg_fallback」；管道输出 reconfigure 为 UTF-8，验收脚本按 utf-8
   解码（errors=replace），避免 cp936 管道编码歧义。
7. **持久化**：dataclass + `__type__` 标记 JSON，schema `rm_sim.project@1`，
   原子写（.rm_tmp + os.replace）。写盘临时文件只由本模块创建/清理，不触碰
   仓库既有文件。

## 与验收标准的对应

| 验收 | 落点 |
|---|---|
| ① selfcheck exit 0 + 两标记 | `rm_sim/main.py::run_selfcheck`；verify 步骤 A |
| ② 版本导入 + numpy=2.5.3 | selfcheck [1/5]；verify 步骤 A/B |
| ③ config + ccxPath | `config.py`；selfcheck [2/5]；verify 步骤 C |
| ④ 双相位故障注入 | `ViewportHost.set_factory`；verify 步骤 D |
| ⑤ 日志无 traceback | verify 脚本 finally 兜底 + 日志内容过滤 |

## 未做 / 留给后续模块

- STEP 导入、自动网格、求解调度、报告文档生成（菜单项已占位禁用）。
- GL 视口的人机交互（旋转/选面）属后续模块；本模块 GL 路径仅保证
  "构造+首帧"在交互会话可用（probe4b 配方）与网格 best-effort 上屏。
- 真会话 0（无桌面）运行未测试，与 config.json uiHeadlessNote 一致；
  该场景走 Agg 降级，会话无关。
