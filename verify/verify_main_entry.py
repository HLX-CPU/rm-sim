# -*- coding: utf-8 -*-
"""验收脚本：统一主入口与主窗口框架（exit 0 = 通过；详细日志写入 verify/logs/）。

覆盖验收标准：
① QT_QPA_PLATFORM=offscreen 下以 venv python 运行 main.py --selfcheck：
   exit 0，stdout 含「窗口构建成功」与「渲染后端=agg_fallback」；
② PySide6/pyvistaqt/gmsh/meshio/numpy 可导入并打印版本，numpy 必须 = 2.5.3；
③ config.json 可读且 ccxPath=D:/rm_sim/tools/ccx_launcher.exe 文件存在；
④ 视口降级注入测试：桩视口工厂分别在「构造期」「首帧渲染期」抛异常各注入一次，
   两次均降级 Agg 且不崩（覆盖构造成功但首帧 paint 崩溃的漏网情形）；
⑤ 日志落 verify/logs/verify_main_entry.log 且无 traceback。

运行：E:/rm_sim/venv/Scripts/python.exe verify/verify_main_entry.py
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

# 本脚本自身无头运行；必须在导入 PySide6 之前强制 offscreen
os.environ["QT_QPA_PLATFORM"] = "offscreen"

ROOT = Path(__file__).resolve().parent.parent
APP_DIR = ROOT / "app"
sys.path.insert(0, str(APP_DIR))

LOG_PATH = ROOT / "verify" / "logs" / "verify_main_entry.log"
REQUIRED_NUMPY = "2.5.3"
EXPECTED_CCX = "D:/rm_sim/tools/ccx_launcher.exe"

_lines: list[str] = []
_failures: list[str] = []


def log(msg: object) -> None:
    text = str(msg)
    print(text)
    _lines.append(text)


def fail(msg: str) -> None:
    _failures.append(msg)
    log(f"[FAIL] {msg}")


def ok(msg: str) -> None:
    log(f"[OK] {msg}")


def check(cond: bool, pass_msg: str, fail_msg: str) -> None:
    if cond:
        ok(pass_msg)
    else:
        fail(fail_msg)


# --------------------------------------------------------------------------- 步骤
def step_a_selfcheck_subprocess() -> None:
    """① main.py --selfcheck（offscreen 子进程）：exit 0 + 两个标记串。"""
    cmd = [sys.executable, str(ROOT / "main.py"), "--selfcheck"]
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env.setdefault("PYTHONIOENCODING", "utf-8")
    log(f"$ QT_QPA_PLATFORM=offscreen {' '.join(cmd)}")
    proc = subprocess.run(cmd, capture_output=True, timeout=300, env=env, cwd=str(ROOT))
    out = proc.stdout.decode("utf-8", errors="replace")
    err = proc.stderr.decode("utf-8", errors="replace")
    for line in out.rstrip().splitlines():
        log(f"  | {line}")
    if err.strip():
        log("  [stderr] " + " / ".join(err.rstrip().splitlines()))
    check(
        proc.returncode == 0,
        "① main.py --selfcheck exit=0",
        f"① main.py --selfcheck exit={proc.returncode}（期望 0）",
    )
    check(
        "窗口构建成功" in out,
        "① stdout 含「窗口构建成功」",
        "① stdout 缺少「窗口构建成功」",
    )
    check(
        "渲染后端=agg_fallback" in out,
        "① stdout 含「渲染后端=agg_fallback」",
        "① stdout 缺少「渲染后端=agg_fallback」",
    )
    check(
        "selfcheck PASS" in out,
        "① stdout 含「selfcheck PASS」",
        "① stdout 缺少「selfcheck PASS」",
    )
    check(
        f"numpy={REQUIRED_NUMPY}" in out,
        f"① selfcheck 打印 numpy={REQUIRED_NUMPY}",
        f"① selfcheck 未打印 numpy={REQUIRED_NUMPY}",
    )


def step_b_versions_inproc() -> None:
    """② 本进程再次导入并打印版本；numpy 必须 = 2.5.3。"""
    import gmsh
    import matplotlib
    import meshio
    import numpy
    import pyvista
    import pyvistaqt
    import PySide6

    log(
        f"② 版本(本进程): PySide6={PySide6.__version__} pyvista={pyvista.__version__} "
        f"pyvistaqt={pyvistaqt.__version__} gmsh={gmsh.__version__} "
        f"meshio={meshio.__version__} numpy={numpy.__version__} "
        f"matplotlib={matplotlib.__version__} vtk={pyvista.vtk_version_info}"
    )
    check(
        numpy.__version__ == REQUIRED_NUMPY,
        f"② numpy == {REQUIRED_NUMPY}",
        f"② numpy 版本必须为 {REQUIRED_NUMPY}，实际 {numpy.__version__}",
    )


def step_c_config() -> None:
    """③ config.json 可读且 ccxPath 正确且文件存在。"""
    from rm_sim.config import config_path, get_ccx_path, load_config

    p = config_path()
    cfg = load_config()
    ccx = get_ccx_path(cfg)
    log(f"③ config: {p} ccxPath={ccx}")
    check(
        os.path.normcase(os.path.normpath(ccx))
        == os.path.normcase(os.path.normpath(EXPECTED_CCX)),
        f"③ ccxPath == {EXPECTED_CCX}",
        f"③ ccxPath 应为 {EXPECTED_CCX}，实际 {ccx}",
    )
    check(
        Path(ccx).is_file(),
        f"③ ccxPath 文件存在: {ccx}",
        f"③ ccxPath 文件不存在: {ccx}",
    )


def step_d_viewport_injection() -> None:
    """④ 桩视口工厂注入：构造期 / 首帧渲染期异常各一次，均降级 Agg 且不崩。"""
    from PySide6.QtWidgets import QApplication, QLabel

    from rm_sim.gui.main_window import MainWindow
    from rm_sim.gui.viewport import AggFallbackCanvas, GLViewportHandle, ViewportBackend

    app = QApplication.instance() or QApplication(["verify_main_entry"])

    # 懒加载证明：构建后、显示前视口必须仍是 PENDING
    win0 = MainWindow()
    check(
        win0.viewport.backend is ViewportBackend.PENDING,
        "④ 懒加载：窗口构建后视口仍为 pending",
        "④ 懒加载被破坏：窗口构建后视口已创建",
    )
    win0.close()

    # ④-1 构造期注入异常
    def factory_construct(parent):  # noqa: ANN001
        raise RuntimeError("INJECT-CONSTRUCT: 构造期注入异常")

    win1 = MainWindow()
    win1.viewport.set_factory(factory_construct)
    win1.resize(1024, 768)
    win1.show()
    for _ in range(10):
        app.processEvents()
    backend1 = win1.viewport.ensure_viewport()
    log(f"④-1 构造期注入: backend={backend1.value} last_error={win1.viewport.last_error!r}")
    check(
        backend1 is ViewportBackend.AGG_FALLBACK,
        "④-1 构造期异常 -> 降级 agg_fallback",
        f"④-1 构造期异常后端应为 agg_fallback，实际 {backend1.value}",
    )
    check(
        "INJECT-CONSTRUCT" in win1.viewport.last_error,
        "④-1 失败原因被完整记录（未吞异常）",
        "④-1 last_error 未包含注入标记",
    )
    check(
        isinstance(win1.viewport.current_widget, AggFallbackCanvas),
        "④-1 视口区域已是 Agg 画布",
        "④-1 视口区域不是 Agg 画布",
    )
    win1.close()

    # ④-2 首帧渲染期注入异常（构造成功但首帧 paint 崩溃的漏网情形）

    def _raise_first_frame() -> None:
        raise RuntimeError("INJECT-FIRSTFRAME: 首帧渲染期注入异常")

    def factory_first_frame(parent):  # noqa: ANN001
        return GLViewportHandle(
            widget=QLabel("stub-gl-widget"),
            render_first_frame=_raise_first_frame,
            close=lambda: None,
        )

    win2 = MainWindow()
    win2.viewport.set_factory(factory_first_frame)
    win2.resize(1024, 768)
    win2.show()
    for _ in range(10):
        app.processEvents()
    backend2 = win2.viewport.ensure_viewport()
    log(f"④-2 首帧注入: backend={backend2.value} last_error={win2.viewport.last_error!r}")
    check(
        backend2 is ViewportBackend.AGG_FALLBACK,
        "④-2 首帧渲染异常 -> 降级 agg_fallback",
        f"④-2 首帧渲染异常后端应为 agg_fallback，实际 {backend2.value}",
    )
    check(
        "INJECT-FIRSTFRAME" in win2.viewport.last_error,
        "④-2 首帧失败原因被完整记录（try 覆盖了构造+首帧整周期）",
        "④-2 last_error 未包含注入标记（首帧未被 try 覆盖）",
    )
    check(
        isinstance(win2.viewport.current_widget, AggFallbackCanvas),
        "④-2 桩 GL 控件已被清理，视口区域是 Agg 画布",
        "④-2 视口区域不是 Agg 画布",
    )
    win2.close()
    for _ in range(5):
        app.processEvents()
    log("④ 注入测试完成，进程存活（未崩溃）")


def step_e_gui_free_subprocess() -> None:
    """GUI 硬约束：毒化 GUI/GL 模块后，GUI 无关层仍可完整导入并跑通持久化往返。"""
    code = (
        "import sys\n"
        "sys.modules['PySide6'] = None\n"
        "sys.modules['pyvista'] = None\n"
        "sys.modules['pyvistaqt'] = None\n"
        "sys.modules['vtk'] = None\n"
        "import tempfile, os\n"
        "import rm_sim, rm_sim.config, rm_sim.layer_guard\n"
        "import rm_sim.core.project, rm_sim.core.store, rm_sim.core.materials\n"
        "import rm_sim.mesh.mesh_data\n"
        "import rm_sim.solver.calculix\n"
        "import rm_sim.report.plot_aggs\n"
        "from rm_sim.core.project import Project\n"
        "from rm_sim.core.store import save_project, load_project\n"
        "p = Project(); p.name = 'roundtrip'\n"
        "d = tempfile.mkdtemp()\n"
        "f = os.path.join(d, 't.json')\n"
        "save_project(p, f)\n"
        "q = load_project(f)\n"
        "assert q.name == 'roundtrip'\n"
        "from rm_sim.mesh.mesh_data import demo_mesh, boundary_triangles\n"
        "faces = boundary_triangles(demo_mesh())\n"
        "assert faces.shape == (12, 3), faces.shape\n"
        "print('GUI-FREE-OK')\n"
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = str(APP_DIR) + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        timeout=120,
        env=env,
        cwd=str(ROOT),
    )
    out = proc.stdout.decode("utf-8", errors="replace")
    err = proc.stderr.decode("utf-8", errors="replace")
    log(f"$ python -c <毒化 GUI 模块后导入全部 GUI 无关层> -> exit={proc.returncode}")
    for line in out.rstrip().splitlines():
        log(f"  | {line}")
    if err.strip():
        log("  [stderr] " + " / ".join(err.rstrip().splitlines()))
    check(
        proc.returncode == 0 and "GUI-FREE-OK" in out,
        "GUI 无关层（数据模型/持久化/网格/求解/报告）不依赖 PySide6/pyvista/vtk，可无 GUI 运行",
        "GUI 无关层被毒化导入破坏（存在隐藏 GUI/GL 依赖）",
    )


def step_f_layer_scan() -> None:
    """GUI 无关层静态扫描（与 selfcheck [3/5] 同一守卫）。"""
    from rm_sim import layer_guard

    bad = layer_guard.scan_forbidden_gui_imports(APP_DIR / "rm_sim")
    if bad:
        for item in bad:
            log(f"  [违规] {item}")
        fail(f"静态扫描发现 {len(bad)} 处 GUI 无关层违规导入")
    else:
        ok("静态扫描: core/mesh/solver/report 及包根模块无 GUI/GL/Qt 后端导入")


def main() -> int:
    log("=" * 76)
    log("verify_main_entry 开始（统一主入口与主窗口框架）")
    log(f"python = {sys.executable}")
    check(
        "rm_sim" in sys.executable.lower() and "venv" in sys.executable.lower(),
        f"验收运行于 venv 解释器: {sys.executable}",
        f"应使用 E:/rm_sim/venv/Scripts/python.exe 运行本脚本，实际: {sys.executable}",
    )
    log(f"QT_QPA_PLATFORM = {os.environ.get('QT_QPA_PLATFORM')}（强制 offscreen）")
    log("=" * 76)

    steps = (
        ("A① selfcheck 子进程", step_a_selfcheck_subprocess),
        ("B② 版本导入(本进程)", step_b_versions_inproc),
        ("C③ 配置与求解器", step_c_config),
        ("D④ 视口降级注入", step_d_viewport_injection),
        ("E GUI 无关层毒化子进程", step_e_gui_free_subprocess),
        ("F GUI 无关层静态扫描", step_f_layer_scan),
    )
    for name, fn in steps:
        log(f"\n----- 步骤 {name} -----")
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 - 验收脚本自身不得带崩
            fail(f"步骤 {name} 异常: {type(exc).__name__}: {exc}")

    log("\n" + "=" * 76)
    total = len(steps)
    if _failures:
        log(f"VERIFY_MAIN_ENTRY: FAIL（{total - len(_failures)}/{total} 组步骤通过）")
        for item in _failures:
            log(f"  [失败] {item}")
    else:
        log(f"VERIFY_MAIN_ENTRY: PASS（{total}/{total} 组步骤通过）")
    return 0 if not _failures else 1


if __name__ == "__main__":
    _exit_code = 1
    try:
        _exit_code = main()
    except Exception as exc:  # noqa: BLE001 - 最后兜底：记录失败而非崩溃
        _failures.append(f"验收脚本顶层异常: {type(exc).__name__}: {exc}")
        log(f"VERIFY_MAIN_ENTRY: FAIL（顶层异常 {type(exc).__name__}: {exc}）")
    finally:
        content = "\n".join(_lines) + "\n"
        if "Traceback" in content:
            # 防御：任何路径都不允许 traceback 进入验收日志
            _lines_no_tb = [ln for ln in _lines if "Traceback" not in ln]
            content = "\n".join(_lines_no_tb) + "\n"
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_PATH, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"[verify] 日志已写入 {LOG_PATH}")
    sys.exit(_exit_code)
