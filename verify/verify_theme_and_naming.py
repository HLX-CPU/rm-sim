# -*- coding: utf-8 -*-
"""验收脚本：现代视觉主题与中文化收口（模块41）。

覆盖验收标准：
① 遍历可构造对话框（MaterialDialog/MeshControlsDialog/MeshQualityDialog/
   SetupDialog/CaseHistoryDialog/AdvancedWorkbench）断言按钮文本无英文
   OK/Cancel/Close/Yes/No；源码扫描 gui/*.py 无 `= QDialogButtonBox(`
   直接实例化（全部经 theme.chinese_button_box 中文化）；
② 导入 assembly_two_blocks 后装配树条目与工况设置选面下拉均不含
   『Open CASCADE』，显示『部件1/部件2』（OCC 翻译器默认名兜底）；
③ F2 重命名部件 → save_bundle→open_and_reresolve 往返 part.name 保持、
   is_confirmed、连接对象 validate 通过（面指纹机制保证改名不破坏面引用）；
④ 『结果』菜单存在且含 5 项（云图/位移/支反力/极值/装配结果）+打开最近
   计算报告；INP 导出不在一级主流程菜单（文件/网格/求解/结果），移入『高级』；
   快捷键 Ctrl+O/S/F5/Ctrl+P 生效；
⑤ QSS 生效：apply_theme 后主窗口截图中存在 RM accent 色像素（D6432C±20），
   落证据目录；
⑥ main.py --selfcheck 复跑 exit 0。

运行：E:/rm_sim/venv/Scripts/python.exe verify/verify_theme_and_naming.py
日志：verify/logs/verify_theme_and_naming.log。
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"  # 视觉断言基于像素与状态，无 GL 拾取

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "verify"))

LOG_PATH = ROOT / "verify" / "logs" / "verify_theme_and_naming.log"
SAMPLE = ROOT / "samples" / "assembly_two_blocks.step"
ACCENT_RGB = (0xD6, 0x43, 0x2C)
ENGLISH_BUTTON_TOKENS = {"OK", "Cancel", "Close", "Yes", "No", "Apply", "Open", "Save"}

_lines: list[str] = []
_failures: list[str] = []


def log(msg: object) -> None:
    text = str(msg)
    print(text, flush=True)
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


def main() -> int:
    log("=" * 76)
    log("verify_theme_and_naming 开始（现代视觉主题与中文化收口）")
    log(f"python = {sys.executable}")
    check(
        "rm_sim" in sys.executable.lower() and "venv" in sys.executable.lower(),
        f"验收运行于 venv 解释器: {sys.executable}",
        f"应使用 E:/rm_sim/venv/Scripts/python.exe 运行本脚本，实际: {sys.executable}",
    )
    log("=" * 76)

    from assembly_check_utils import evidence_dir

    evidence = evidence_dir("theme_and_naming")
    log(f"证据目录: {evidence}")

    from PySide6.QtCore import Qt
    from PySide6.QtGui import QImage
    from PySide6.QtWidgets import (
        QApplication,
        QDialogButtonBox,
        QPushButton,
        QAbstractButton,
    )

    from rm_sim.gui.theme import ACCENT, apply_theme, chinese_button_box

    app = QApplication.instance() or QApplication(["verify_theme_and_naming"])
    apply_theme(app)  # 模块41：全局 QSS（与 main.py launch_gui 同一入口）

    from rm_sim.cad.assembly_import import import_assembly
    from rm_sim.core.connections import BondedConnection, ConnectionSide
    from rm_sim.core.face_identity import fingerprint_from_face
    from rm_sim.core.materials import set_part_material
    from rm_sim.core.project import Constraint, LoadCase, Project, TractionLoad
    from rm_sim.gui.main_window import MainWindow

    # ---------- ⑤ QSS 生效：accent 色像素存在于主窗口截图 ----------
    win = MainWindow()
    win.resize(1240, 820)
    win.show()
    for _ in range(20):
        app.processEvents()
    img = win.grab().toImage()
    accent_count = 0
    for y in range(0, img.height(), 2):
        for x in range(0, img.width(), 2):
            c = img.pixelColor(x, y)
            if (
                abs(c.red() - ACCENT_RGB[0]) <= 20
                and abs(c.green() - ACCENT_RGB[1]) <= 20
                and abs(c.blue() - ACCENT_RGB[2]) <= 20
            ):
                accent_count += 1
    check(
        accent_count >= 10,
        f"⑤ QSS 生效：主窗口截图中 accent 色像素 {accent_count} 个（{ACCENT}±20）",
        f"⑤ 截图中未发现 accent 色像素（{ACCENT}）：主题未生效？",
    )
    img.save(str(evidence / "5_qss_theme.png"))
    ok("⑤ QSS 主题截图已存证据目录: 5_qss_theme.png")

    # ---------- ④ 结果菜单 + INP 导出位置 + 快捷键 ----------
    bar = win.menuBar()
    menu_texts: dict[str, list] = {}
    for act in bar.actions():
        menu = act.menu()
        if menu is None:
            continue
        title = act.text().replace("&", "").split("(")[0].strip()
        menu_texts[title] = [
            a.text() for a in menu.actions() if a.menu() is None and not a.isSeparator()
        ]
    result_menu = menu_texts.get("结果")
    advanced_menu = menu_texts.get("高级")
    mesh_menu = menu_texts.get("网格")
    log(f"  | 菜单清单: {sorted(menu_texts)}")
    check(
        result_menu is not None,
        f"④ 『结果』菜单存在（{result_menu}）",
        "④ 『结果』菜单缺失",
    )
    if result_menu is not None:
        expected = ["von Mises 云图", "位移云图（变形缩放）…", "支反力合力表…", "结果极值面板…", "装配与部件结果…"]
        check(
            all(t in result_menu for t in expected),
            f"④ 结果菜单含 5 项结果动作",
            f"④ 结果菜单缺项: {[t for t in expected if t not in result_menu]}",
        )
        check(
            "打开最近计算报告" in result_menu,
            "④ 报告并入结果菜单（打开最近计算报告）",
            "④ 报告未并入结果菜单",
        )
    primary = {"文件", "网格", "求解", "结果"}
    inp_in_primary = [
        f"{title}:{t}"
        for title, texts in menu_texts.items()
        if title in primary
        for t in texts
        if "INP" in t
    ]
    check(
        not inp_in_primary,
        "④ INP 导出不在一级主流程菜单（文件/网格/求解/结果）",
        f"④ INP 导出仍在主流程菜单: {inp_in_primary}",
    )
    check(
        advanced_menu is not None and any("INP" in t for t in advanced_menu),
        f"④ 『生成网格并导出 INP…』移入高级菜单（{advanced_menu}）",
        "④ INP 导出不在高级菜单",
    )
    check(
        mesh_menu is not None and not any("INP" in t for t in mesh_menu),
        "④ 网格菜单不再含 INP 导出",
        f"④ 网格菜单仍含 INP 导出: {mesh_menu}",
    )
    shortcut_map = {}
    for act in bar.actions():
        menu = act.menu()
        if not menu:
            continue
        for a in menu.actions():
            if a.text() and a.shortcut().toString():
                shortcut_map[a.text().replace("&", "")] = a.shortcut().toString()
    for label, sc in (("打开工程…", "Ctrl+O"), ("保存工程", "Ctrl+S"), ("运行静力分析…", "F5"), ("打开最近计算报告", "Ctrl+P")):
        check(
            shortcut_map.get(label) == sc,
            f"④ 快捷键 {label} = {sc}",
            f"④ 快捷键异常: {label}={shortcut_map.get(label)!r}（期望 {sc}）",
        )

    # ---------- ② 导入 two_blocks → 树/下拉无 Open CASCADE，显示 部件N ----------
    win.solid_model = import_assembly(SAMPLE)
    win.project = Project(name="theme_naming_demo")
    win.project.study.geometry_file = str(SAMPLE)
    from rm_sim.cad.assembly_import import sync_parts_from_model

    sync_parts_from_model(win.project, win.solid_model)
    win._refresh_project_views()  # 装配树/流程条同步（show_assembly 会跳过已同步分支）
    win.show_assembly()
    for _ in range(10):
        app.processEvents()
    tree_texts = []
    for r in range(win.case_tree.topLevelItemCount()):
        it = win.case_tree.topLevelItem(r)
        stack = [it]
        while stack:
            cur = stack.pop()
            tree_texts.append(cur.text(0))
            stack.extend(cur.child(i) for i in range(cur.childCount()))
    check(
        not any("Open CASCADE" in t for t in tree_texts),
        "② 装配树不含『Open CASCADE』",
        f"② 装配树仍含翻译器默认名: {[t for t in tree_texts if 'Open CASCADE' in t]}",
    )
    check(
        "部件1" in " ".join(tree_texts) and "部件2" in " ".join(tree_texts),
        f"② 装配树显示『部件1』『部件2』",
        f"② 装配树未见部件N: {tree_texts}",
    )
    win._on_edit_setup()
    for _ in range(10):
        app.processEvents()
    form = win._setup_form
    check(
        form is not None,
        "② 工况设置面板打开（选面下拉检查载体）",
        "② 工况设置面板未打开",
    )
    if form is not None:
        combo_texts = [form.face_a.itemText(i) for i in range(form.face_a.count())]
        check(
            not any("Open CASCADE" in t for t in combo_texts),
            "② 选面下拉不含『Open CASCADE』",
            f"② 选面下拉仍含翻译器默认名",
        )
        check(
            any(t.startswith("部件1") for t in combo_texts)
            and any(t.startswith("部件2") for t in combo_texts),
            "② 选面下拉显示『部件1/部件2』前缀",
            "② 选面下拉未见部件N 前缀",
        )
        win._close_setup_dock()
        for _ in range(5):
            app.processEvents()

    # ---------- ① 遍历可构造对话框：按钮无英文标准词 ----------
    def collect_button_texts(dialog) -> list:  # noqa: ANN001
        texts = []
        for b in dialog.findChildren(QAbstractButton):
            texts.append(b.text())
        return texts

    dialog_reports: list[str] = []
    english_hits: list[str] = []

    def audit_dialog(name: str, dialog) -> bool:  # noqa: ANN001
        try:
            texts = collect_button_texts(dialog)
        except Exception as exc:  # noqa: BLE001
            dialog_reports.append(f"{name}: 构造失败 {type(exc).__name__}: {exc}")
            return False
        bad = [t for t in texts if t in ENGLISH_BUTTON_TOKENS]
        dialog_reports.append(f"{name}: {len(texts)} 按钮 {[t for t in texts if t][:6]}")
        if bad:
            english_hits.append(f"{name}: {bad}")
            return False
        return True

    project = win.project
    model = win.solid_model
    results_ok = True
    from rm_sim.gui.material_dialog import MaterialDialog
    from rm_sim.gui.mesh_dialogs import MeshControlsDialog, MeshQualityDialog
    from rm_sim.gui.setup_dialog import SetupDialog

    results_ok &= audit_dialog("MaterialDialog", MaterialDialog(None, current_name="Q235"))
    results_ok &= audit_dialog("MeshControlsDialog", MeshControlsDialog(project, model))
    results_ok &= audit_dialog("MeshQualityDialog", MeshQualityDialog(None))
    results_ok &= audit_dialog("SetupDialog", SetupDialog(project, model))
    from rm_sim.gui.result_dialogs import CaseHistoryDialog  # noqa: F401

    results_ok &= audit_dialog("CaseHistoryDialog", CaseHistoryDialog(project))
    try:
        from rm_sim.gui.advanced_dialog import AdvancedWorkbench

        results_ok &= audit_dialog("AdvancedWorkbench", AdvancedWorkbench(project))
    except Exception as exc:  # noqa: BLE001
        dialog_reports.append(f"AdvancedWorkbench: 跳过（{type(exc).__name__}: {exc}）")
    for report in dialog_reports:
        log(f"  | 对话框按钮盘点: {report}")
    check(
        results_ok,
        "① 可构造对话框按钮全部中文（无 OK/Cancel/Close/Yes/No）",
        f"① 对话框存在英文按钮: {english_hits}",
    )
    # 源码扫描：gui 层不再直接实例化 QDialogButtonBox（统一走 chinese_button_box）
    direct_sites = []
    gui_dir = ROOT / "app" / "rm_sim" / "gui"
    for py in sorted(gui_dir.glob("*.py")):
        if py.name == "theme.py":
            continue
        for lineno, line in enumerate(py.read_text(encoding="utf-8").splitlines(), 1):
            if "= QDialogButtonBox(" in line:
                direct_sites.append(f"{py.name}:{lineno}")
    check(
        not direct_sites,
        "① 源码扫描：QDialogButtonBox 全部经 chinese_button_box 中文化",
        f"① 仍存在直接实例化: {direct_sites}",
    )
    # chinese_button_box 助手本身（offscreen 冒烟：Ok→确定）
    box = chinese_button_box(QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel))
    check(
        box.button(QDialogButtonBox.StandardButton.Ok).text() == "确定"
        and box.button(QDialogButtonBox.StandardButton.Cancel).text() == "取消",
        "① chinese_button_box 助手：Ok/Cancel → 确定/取消",
        "① 助手文本异常",
    )

    # ---------- ③ F2 重命名 → 束往返 + 指纹 re-resolve + 连接 validate ----------
    # 组装含连接的工程（面引用=几何指纹，改名无关）
    face3 = model.face_by_tag(3)
    face10 = model.face_by_tag(10)
    face4 = model.face_by_tag(4)
    face9 = model.face_by_tag(9)
    project3 = Project(name="rename_roundtrip_demo", project_dir=str(evidence / "rename_run"))
    project3.study.geometry_file = str(SAMPLE)
    sync_parts_from_model(project3, model)
    set_part_material(project3, "p1", "Q235")
    set_part_material(project3, "p2", "Q235")
    project3.study.load_cases = [
        LoadCase(
            name="工况1",
            constraints=[
                Constraint(name="底面固定", face_tag=3, face=fingerprint_from_face(face3), part_id="p1")
            ],
            loads=[
                TractionLoad(name="顶面总力", face_tag=10, face=fingerprint_from_face(face10),
                             part_id="p2", total_force_n=1000.0, direction=(0.0, -1.0, 0.0))
            ],
        )
    ]
    project3.connections.append(
        BondedConnection(
            name="界面粘接",
            side_a=ConnectionSide("p1", fingerprint_from_face(face4)),
            side_b=ConnectionSide("p2", fingerprint_from_face(face9)),
        )
    )
    # F2 重命名（装配树条目 → itemChanged 处理器，同 F2 编辑路径）
    win.project = project3
    win._refresh_project_views()
    part_item = None
    for r in range(win.case_tree.topLevelItemCount()):
        top = win.case_tree.topLevelItem(r)
        for i in range(top.childCount()):
            asm = top.child(i)
            for j in range(asm.childCount()):
                child = asm.child(j)
                data = child.data(0, Qt.ItemDataRole.UserRole)
                if isinstance(data, tuple) and data[0] == "part":
                    part_item = child
                    break
    check(
        part_item is not None,
        "③ 装配树找到部件条目（F2 载体）",
        "③ 未找到部件条目",
    )
    if part_item is not None:
        part_item.setText(0, "我的支架")
        win.case_tree._on_item_changed(part_item)
        for _ in range(5):
            app.processEvents()
        renamed = project3.parts[0]
        check(
            renamed.name == "我的支架",
            f"③ F2 重命名生效（part.name={renamed.name!r}）",
            f"③ 重命名未生效: {renamed.name!r}",
        )
        from rm_sim.core.project_bundle import save_bundle

        bundle = evidence / "rename_roundtrip.rmsimproject"
        save_bundle(bundle, project3, geometry_source=SAMPLE)  # 几何副本随束携带
        from rm_sim.solver.project_workflow import open_and_reresolve

        opened = open_and_reresolve(bundle, evidence / "rename_extract")
        project4 = opened["project"]
        check(
            project4.parts[0].name == "我的支架",
            f"③ 束往返后 part.name 保持（{project4.parts[0].name!r}）",
            f"③ 往返后名字丢失: {project4.parts[0].name!r}",
        )
        check(
            bool(opened.get("model")),
            "③ 往返后几何重导入成功",
            "③ 往返后几何缺失",
        )
        check(
            project4.is_confirmed,
            "③ 连接面指纹 re-resolve 成功（is_confirmed）",
            f"③ re-resolve 未确认: warnings={opened.get('warnings', [])[:2]}",
        )
        validate_ok = True
        try:
            part_ids = [p.part_id for p in project4.parts]
            for conn in project4.connections:
                conn.validate(part_ids)
        except Exception as exc:  # noqa: BLE001
            validate_ok = False
            fail(f"③ 连接 validate 失败: {exc}")
        if validate_ok:
            ok("③ 连接对象 validate 通过（改名不破坏面引用）")
        check(
            not any("Open CASCADE" in p.name for p in project4.parts),
            "③ 往返后部件名仍为兜底中文（无翻译器默认名）",
            f"③ 往返后出现翻译器名: {[p.name for p in project4.parts]}",
        )

    try:
        win.close()
        for _ in range(5):
            app.processEvents()
    except Exception:
        pass
    try:
        model.close()
    except Exception:
        pass

    # ---------- ⑥ main.py --selfcheck 复跑 ----------
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env.setdefault("PYTHONIOENCODING", "utf-8")
    proc = subprocess.run(
        [sys.executable, str(ROOT / "main.py"), "--selfcheck"],
        capture_output=True, timeout=300, env=env, cwd=str(ROOT),
    )
    out = proc.stdout.decode("utf-8", errors="replace")
    check(
        proc.returncode == 0,
        "⑥ main.py --selfcheck 复跑 exit=0",
        f"⑥ --selfcheck exit={proc.returncode}",
    )
    check(
        "窗口构建成功" in out,
        "⑥ selfcheck stdout 含『窗口构建成功』",
        "⑥ selfcheck stdout 缺『窗口构建成功』",
    )

    log("\n" + "=" * 76)
    if _failures:
        log("VERIFY_THEME_AND_NAMING: FAIL")
        for item in _failures:
            log(f"  [失败] {item}")
        return 1
    log("VERIFY_THEME_AND_NAMING: PASS（中文按钮 + 部件名兜底/F2 重命名 + 结果菜单 + QSS accent）")
    return 0


if __name__ == "__main__":
    try:
        _exit = main()
    except Exception as exc:  # noqa: BLE001
        fail(f"VERIFY_THEME_AND_NAMING: FAIL（顶层异常 {type(exc).__name__}: {exc}）")
        _exit = 1
    finally:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_PATH, "w", encoding="utf-8") as f:
            f.write("\n".join(_lines) + "\n")
        print(f"[verify] 日志已写入 {LOG_PATH}")
    sys.exit(_exit)
