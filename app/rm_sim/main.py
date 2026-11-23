# -*- coding: utf-8 -*-
"""统一入口实现：GUI 启动与 --selfcheck 无头自检。

退出码约定：
- GUI 模式：Qt 事件循环退出码；
- --selfcheck：0 = 全部通过，2 = 存在失败项（原因逐条打印，不抛 traceback）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REQUIRED_NUMPY = "2.5.3"  # 验收硬约束（numpy 必须 = 2.5.3）


def _setup_stdio() -> None:
    """管道输出统一 UTF-8（验收脚本按 utf-8 解码）；交互控制台保持系统编码。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            if not stream.isatty():
                stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rm_sim", description="RoboMaster 仿真软件统一主入口"
    )
    parser.add_argument(
        "--selfcheck",
        action="store_true",
        help="无头自检：依赖版本 / 配置 / GUI 无关层扫描 / 窗口构建 / 视口降级 / 装配树数据模型",
    )
    parser.add_argument(
        "--smoke",
        action="store_true",
        help="整机启动冒烟：主窗口显示完成后 QTimer 自动退出（可重启验证）",
    )
    parser.add_argument(
        "--batch",
        metavar="SPEC.json",
        default=None,
        help="批处理入口：按 spec 执行无 GUI 工程工作流，结果写入 spec.result_path",
    )
    parser.add_argument(
        "--config",
        default=None,
        help="config.json 路径（默认仓库根 config.json，可用环境变量 RM_SIM_CONFIG 覆盖）；"
        "当前仅 --selfcheck 使用，GUI 工程配置属于后续模块",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    _setup_stdio()
    args = build_parser().parse_args(
        argv if argv is not None else sys.argv[1:]
    )
    if args.batch:
        from .batch import run_batch_spec

        code, result = run_batch_spec(args.batch)
        print(f"批处理完成 exit={code}: "
              + json.dumps(result, ensure_ascii=False)[:400])
        return code
    if args.smoke:
        return run_smoke()
    if args.selfcheck:
        return run_selfcheck(args.config)
    return launch_gui()


# ---------------------------------------------------------------- 启动冒烟
def run_smoke() -> int:
    """整机启动冒烟（--smoke）：主窗口显示完成后 QTimer 自动退出。

    视口走主入口的懒加载 + 降级路径：offscreen 平台下 GL 不可用是
    probes/probe4_log.txt 实证，MainWindow 自行降级 agg_fallback；
    若崩在 createPlatformOpenGLContext 即主窗口实现不合规（冒烟约定不可改）。
    """
    import datetime as _dt

    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from .config import get_ccx_path, load_config
    from .gui.main_window import MainWindow

    print(f"冒烟时间戳={_dt.datetime.now().isoformat(timespec='milliseconds')}")
    app = QApplication.instance() or QApplication(sys.argv)
    win = MainWindow()
    win.show()
    for _ in range(20):
        app.processEvents()
    win.ensure_viewport()  # 懒加载：offscreen → GL 失败 → Agg 降级（probe4/probe8 配方）
    print("主窗口已显示")
    print(f"渲染后端={win.viewport.backend.value}")
    try:
        ccx = get_ccx_path(load_config())
        print(f"ccxPath={ccx}（config.json 已加载）")
    except Exception as exc:
        print(f"ccxPath 加载失败: {type(exc).__name__}: {exc}")
        win.close()
        return 3
    QTimer.singleShot(200, app.quit)  # 显示完成后 QTimer 自动退出
    rc = app.exec()
    win.close()
    print(f"冒烟完成 exit={rc}")
    return 0 if rc == 0 else rc


# ---------------------------------------------------------------- GUI 启动
def launch_gui() -> int:
    if os.environ.get("QT_QPA_PLATFORM", "").strip().lower() == "offscreen":
        print(
            "[警告] QT_QPA_PLATFORM=offscreen：GL 视口将不可用并自动降级 agg_fallback"
            "（证据: probes/probe4_log.txt）"
        )
    from PySide6.QtWidgets import QApplication

    from .gui.main_window import MainWindow
    from .gui.theme import apply_theme

    app = QApplication.instance() or QApplication(sys.argv)
    apply_theme(app)  # 模块41：现代视觉主题（浅色+RM accent+卡片化+等宽日志）
    win = MainWindow()
    win.resize(1280, 800)
    win.show()
    return app.exec()


# ---------------------------------------------------------------- selfcheck
def run_selfcheck(config_path: str | None = None) -> int:
    print(f"rm_sim selfcheck: python={sys.version.split()[0]} platform={sys.platform}")
    print(
        f"QT_QPA_PLATFORM={os.environ.get('QT_QPA_PLATFORM', '<未设置，native windows>')}"
    )
    failures: list[str] = []

    # [1/7] 依赖导入与版本（验收 ②）
    try:
        import matplotlib
        import gmsh
        import meshio
        import numpy
        import pyvista
        import pyvistaqt
        import PySide6

        print(
            f"[1/7] 依赖导入: PySide6={PySide6.__version__} pyvista={pyvista.__version__} "
            f"pyvistaqt={pyvistaqt.__version__} gmsh={gmsh.__version__} "
            f"meshio={meshio.__version__} numpy={numpy.__version__} "
            f"matplotlib={matplotlib.__version__}"
        )
        if numpy.__version__ != REQUIRED_NUMPY:
            failures.append(f"numpy 版本必须为 {REQUIRED_NUMPY}，实际 {numpy.__version__}")
    except Exception as exc:
        failures.append(f"依赖导入失败: {type(exc).__name__}: {exc}")

    # [2/7] 配置读取与求解器路径（验收 ③）
    try:
        from .config import config_path as default_config_path, get_ccx_path, load_config

        shown = Path(config_path) if config_path else default_config_path()
        cfg = load_config(config_path)
        ccx = get_ccx_path(cfg)
        exists = Path(ccx).is_file()
        print(f"[2/7] 配置读取: {shown} ccxPath={ccx} 文件存在={exists}")
        if not exists:
            failures.append(f"ccxPath 文件不存在: {ccx}")
    except Exception as exc:
        failures.append(f"配置读取失败: {type(exc).__name__}: {exc}")

    # [3/7] GUI 无关层静态扫描（数据模型/持久化/网格/求解/报告禁止 GUI/GL 依赖）
    try:
        from . import layer_guard

        bad = layer_guard.scan_forbidden_gui_imports(Path(__file__).resolve().parent)
        if bad:
            failures.append("GUI 无关层存在禁止导入: " + "; ".join(bad))
            print(f"[3/7] GUI 无关层扫描: {len(bad)} 处违规")
        else:
            print(
                "[3/7] GUI 无关层扫描: core/mesh/solver/report/cad 及顶层 GUI 无关模块"
                "无 PySide6/pyvista/vtk/rm_sim.gui/matplotlib-Qt 导入"
            )
    except Exception as exc:
        failures.append(f"GUI 无关层扫描失败: {type(exc).__name__}: {exc}")

    # [4/7] 主窗口构建（不触碰 GL）
    app = None
    win = None
    try:
        from PySide6.QtWidgets import QApplication

        from .gui.main_window import MainWindow
        from .gui.viewport import ViewportBackend

        app = QApplication.instance() or QApplication(["rm_sim-selfcheck"])
        win = MainWindow()
        if win.viewport.backend is not ViewportBackend.PENDING:
            failures.append("视口应在懒加载时机（首次显示）才创建，构建期即已创建")
        else:
            print(
                "[4/7] 窗口构建成功: 菜单(文件/网格/求解/结果/视图/材料/工况/高级/帮助)、装配树、"
                "属性面板、状态栏、日志面板、视口宿主(pending)"
            )
    except Exception as exc:
        failures.append(f"窗口构建失败: {type(exc).__name__}: {exc}")

    # [5/7] 视口创建 + 降级 + Agg 出图（验收 ①）
    try:
        if win is None or app is None:
            raise RuntimeError("窗口未构建，无法测试视口")
        win.resize(1280, 800)
        win.show()
        for _ in range(10):
            app.processEvents()
        backend = win.ensure_viewport()
        print(f"渲染后端={backend.value}")
        if (
            os.environ.get("QT_QPA_PLATFORM", "").strip().lower() == "offscreen"
            and backend.value != "agg_fallback"
        ):
            failures.append("offscreen 平台下视口应为 agg_fallback（GL 硬约束被破坏）")
        from .mesh.mesh_data import demo_mesh

        info = win.show_mesh(demo_mesh(), title="rm_sim selfcheck demo（Agg 降级画布）")
        print(f"[5/7] 降级画布出图: {info}")
        if "png_bytes=" in info:
            nbytes = int(info.split("png_bytes=")[1].split()[0])
            if nbytes < 1000:
                failures.append(f"Agg 出图字节数异常偏小: {nbytes}")
    except Exception as exc:
        failures.append(f"视口/出图失败: {type(exc).__name__}: {exc}")

    # [6/7] 装配树数据模型（模块 13；验收 ④）：2 部件/各材料/可见性 → JSON 打印
    try:
        import shutil
        import tempfile

        from .assembly_demo import assembly_tree_payload, build_demo_assembly_project
        from .cad.make_assembly_samples import make_two_blocks_step

        tmpdir = tempfile.mkdtemp(prefix="rm_sim_selfcheck_asm_")
        try:
            step_path = Path(tmpdir) / "assembly_two_blocks.step"
            make_two_blocks_step(step_path)
            project, model = build_demo_assembly_project(step_path)
            try:
                payload = assembly_tree_payload(project)
                print("装配树=" + json.dumps(payload, ensure_ascii=False))
                asm = payload["assembly"]
                if asm["part_count"] != 2:
                    failures.append(f"装配树应为 2 部件，实际 {asm['part_count']}")
                mats = {p["material"] for p in asm["parts"]}
                if not {"Q235", "6061"} <= mats:
                    failures.append(f"装配树部件材料缺失（期望含 Q235/6061）: {sorted(mats)}")
                if not all(p["visible"] for p in asm["parts"]):
                    failures.append("装配树可见性异常（演示部件应全部可见）")
                if any(p["volume_mm3"] in (None, 0) for p in asm["parts"]):
                    failures.append("装配树部件几何指纹缺失（volume_mm3 为空）")
            finally:
                model.close()
            print("[6/7] 装配树数据模型: 2 部件（材料引用/可见性/几何指纹）构建成功")
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)  # 只清理本次创建的临时目录
    except Exception as exc:
        failures.append(f"装配树自检失败: {type(exc).__name__}: {exc}")
    finally:
        try:
            if win is not None:
                win.close()
        except Exception:
            pass

    # [7/7] 装配结果对话框（模块 20）：部件选择 + 逐部件极值（offscreen 可构建）
    try:
        from PySide6.QtWidgets import QDialog

        from .gui.result_dialogs import AssemblyResultDialog

        app = QApplication.instance()
        if app is None or win is None:
            raise RuntimeError("窗口未构建，无法测试装配结果对话框")
        parts = {
            "装配合成": {"nodes": 120, "max_u": 0.019085, "max_mises": 12.5},
            "PART1": {"nodes": 60, "max_u": 0.019048, "max_mises": 10.1},
            "PART2": {"nodes": 60, "max_u": 0.019085, "max_mises": 12.5},
        }
        dlg = AssemblyResultDialog(parts, parent=win)
        if not isinstance(dlg, QDialog):
            raise RuntimeError("AssemblyResultDialog 构建失败")
        dlg.close()
        print("装配结果对话框构建成功（部件选择 3 项 + 逐部件极值表，offscreen）")
        print("[7/7] 装配结果对话框构建成功")
    except Exception as exc:
        failures.append(f"装配结果对话框自检失败: {type(exc).__name__}: {exc}")

    if failures:
        print("selfcheck FAIL: " + " | ".join(failures))
        return 2
    print("selfcheck PASS")
    return 0
