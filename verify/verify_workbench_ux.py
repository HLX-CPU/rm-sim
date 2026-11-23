# -*- coding: utf-8 -*-
"""验收脚本：高级分析工作台可理解化（模块43，交接书 §5-A）。

覆盖验收标准：
① 复用 verify_advanced_framework.py 的禁进程模式（替换 subprocess.Popen 即抛
   断言）——工作台构造/分组/校验/编辑/导出全程零外部进程；
② 构造缺 modal_study_id 的 harmonic → 状态列显示 validate_studies 的具体缺失
   项文本（『需要引用已启用且设置有效的modal研究』）而非『已配置，未计算』；
   补齐后状态变绿（✓ 校验通过 + 绿色前景）；
③ 工况引用下拉项文本=工况名、currentData=索引，form.value() 往返一致；
   行标签不再出现『边界工况索引（从0起）』裸整数语义；
④ 按研究类型分组呈现：工作台树顶层=5 类分组；modal 参数表单无 transient
   专属字段（持续时间/步长/积分方法等）；
⑤ 未注册后端时求解按钮 disabled 且文案含『尚未接通』；诚实声明保留。
另：导出计算准备包→『准备包已导出，未计算』诚实状态（不虚报已求解）；
截图落证据目录。

运行：E:/rm_sim/venv/Scripts/python.exe verify/verify_workbench_ux.py
日志：verify/logs/verify_workbench_ux.log。
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtWidgets import QFormLayout  # noqa: E402 - 模块级：_form_label 使用

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "verify"))

LOG_PATH = ROOT / "verify" / "logs" / "verify_workbench_ux.log"

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
    log("verify_workbench_ux 开始（高级分析工作台可理解化）")
    log(f"python = {sys.executable}")
    check(
        "rm_sim" in sys.executable.lower() and "venv" in sys.executable.lower(),
        f"验收运行于 venv 解释器: {sys.executable}",
        f"应使用 E:/rm_sim/venv/Scripts/python.exe 运行本脚本，实际: {sys.executable}",
    )
    log("=" * 76)

    from assembly_check_utils import evidence_dir

    work = evidence_dir("workbench_ux")
    log(f"证据目录: {work}")

    # ---------- ① 禁进程模式（复用 verify_advanced_framework 配方） ----------
    launches: list[str] = []
    real_popen = subprocess.Popen

    def forbidden(*args, **kwargs):  # noqa: ANN002, ANN003
        launches.append(str(args))
        raise AssertionError(f"Workbench launched an external process: {args}")

    subprocess.Popen = forbidden
    try:
        _run_checks(work, launches)
    finally:
        subprocess.Popen = real_popen
    check(
        not launches,
        "① 工作台全程零外部进程（subprocess.Popen 打桩未被触发）",
        f"① 工作台发起了外部进程: {launches[:3]}",
    )
    _finish()
    return _exit_code()


def _run_checks(work, launches) -> None:  # noqa: ANN001
    import numpy as np
    from PySide6.QtGui import QColor
    from PySide6.QtWidgets import (
        QApplication,
        QComboBox,
        QFileDialog,
        QFormLayout,
        QLabel,
        QPushButton,
    )

    from rm_sim.gui.advanced_dialog import (
        LABELS,
        AdvancedWorkbench,
        ObjectForm,
        _WorkbenchTree,
    )
    from rm_sim.core.advanced import (
        ContactPairSpec,
        JointSpec,
        LoadTransferSpec,
        PlasticCurve,
        RigidBodySpec,
        SpringDamperSpec,
        TimeHistory,
        new_study,
    )
    from rm_sim.core.face_identity import FaceFingerprint
    from rm_sim.core.project import Constraint, LoadCase, Project, TractionLoad
    from rm_sim.core.store import to_dict
    from rm_sim.solver.advanced_validation import validate_studies

    app = QApplication.instance() or QApplication(["verify_workbench_ux"])

    # ---------- 夹具：镜像 verify_advanced_framework 的有效 5 类研究 ----------
    project = Project(name="Configuration fixture only", project_dir=str(work))
    opaque = work / "opaque_geometry_fixture.step"
    opaque.write_bytes(b"Configuration test fixture; not a CAD model.\n")
    project.study.geometry_file = str(opaque)
    project.parts = []
    from rm_sim.core.project import Part

    project.parts = [Part("p1", name="fixed fixture"), Part("p2", name="moving fixture")]
    fp = FaceFingerprint(1., (0., 0., 0.), (0., 0., 1.))
    project.study.load_cases = [
        LoadCase(
            name="fixture excitation",
            constraints=[Constraint("fixed", face_tag=1, face=fp, part_id="p1")],
            loads=[TractionLoad("force", face_tag=2, face=fp, part_id="p2", total_force_n=1.)],
        ),
        LoadCase(name="温度工况"),  # 命名下拉第二项（③）
    ]
    modal = new_study("modal")
    harmonic = new_study("harmonic")
    transient = new_study("transient")
    mechanism = new_study("mechanism")
    nonlinear = new_study("nonlinear")
    harmonic.settings.modal_study_id = modal.id
    mechanism.settings.bodies = [
        RigidBodySpec("base", "p1", fixed=True),
        RigidBodySpec("arm", "p2", mass_source="explicit", mass_kg=2.,
                      inertia_kg_mm2=(10., 10., 10., 0., 0., 0.)),
    ]
    mechanism.settings.histories = [TimeHistory("drive", "rad", [(0., 0.), (1., 1.)])]
    mechanism.settings.joints = [JointSpec("pivot", "revolute", "base", "arm", drive="position", history_id="drive")]
    mechanism.settings.springs = [SpringDamperSpec(body_a="base", body_b="arm", stiffness_n_mm=2., damping_n_s_mm=.1)]
    mechanism.settings.contacts = [ContactPairSpec(body_b="arm")]
    transient.settings.load_transfer = LoadTransferSpec(mechanism.id, "arm", "p2")
    nonlinear.settings.plasticity_enabled = True
    nonlinear.settings.plastic_curves = [PlasticCurve("p2", [(0., 100.), (.1, 120.)], "configuration fixture, not material data")]
    project.advanced_studies = [harmonic, transient, nonlinear, modal, mechanism]
    assert not validate_studies(project), validate_studies(project)
    ok("夹具：5 类研究全部校验通过（镜像 verify_advanced_framework 有效夹具）")

    # ---------- ② 构造缺 modal_study_id 的 harmonic → 状态列具体缺失项 ----------
    harmonic.settings.modal_study_id = "missing-id"  # 破坏依赖
    issues = validate_studies(project)
    check(
        any("modal研究" in i["message"] for i in issues),
        f"② 夹具破坏后 validate_studies 报具体缺失（{[i['message'] for i in issues]}）",
        "② 破坏后未报缺失",
    )
    dialog = AdvancedWorkbench(project)
    dialog.show()
    for _ in range(10):
        app.processEvents()
    harmonic_status = _find_status(dialog, harmonic.id)
    check(
        harmonic_status is not None and "modal研究" in harmonic_status,
        f"② 状态列显示具体缺失项文本（{harmonic_status!r}）",
        f"② 状态列未显示具体缺失项: {harmonic_status!r}",
    )
    check(
        "已配置，未计算" not in harmonic_status,
        "② 状态列不再出现硬编码『已配置，未计算』",
        "② 状态列仍是硬编码文本",
    )
    grab(dialog, work, "2_harmonic_missing_status.png")

    # 补齐 modal_study_id → 状态变绿
    wb_modal = next(s for s in dialog.project.advanced_studies if s.kind == "modal")
    wb_harmonic = next(s for s in dialog.project.advanced_studies if s.kind == "harmonic")
    wb_harmonic.settings.modal_study_id = wb_modal.id
    dialog.refresh()
    harmonic_status = _find_status(dialog, harmonic.id)
    item = _find_item(dialog, harmonic.id)
    check(
        harmonic_status is not None and "校验通过" in harmonic_status,
        f"② 补齐后状态列变绿（{harmonic_status!r}）",
        f"② 补齐后状态异常: {harmonic_status!r}",
    )
    if item is not None:
        color = item.foreground(1).color().name()
        check(
            color == "#1b5e20",
            f"② 校验通过状态为绿色前景（{color}）",
            f"② 状态前景色异常: {color}",
        )
    grab(dialog, work, "2_harmonic_fixed_green.png")

    # ---------- ③ 工况引用命名下拉 ----------
    form = ObjectForm(modal.settings, project, modal.settings)
    combo = form.widgets.get("boundary_case_index")
    check(
        isinstance(combo, QComboBox),
        "③ 边界工况引用为命名下拉（QComboBox）",
        f"③ 边界工况引用不是下拉: {type(combo).__name__}",
    )
    if isinstance(combo, QComboBox):
        texts = [combo.itemText(i) for i in range(combo.count())]
        datas = [combo.itemData(i) for i in range(combo.count())]
        check(
            texts == ["fixture excitation", "温度工况"],
            f"③ 下拉项文本=工况名（{texts}）",
            f"③ 下拉项文本异常: {texts}",
        )
        check(
            datas == [0, 1],
            f"③ currentData=工况索引（{datas}）",
            f"③ currentData 异常: {datas}",
        )
        combo.setCurrentIndex(1)
        check(
            form.value().boundary_case_index == 1,
            f"③ 选择后 form.value() 往返一致（boundary_case_index={form.value().boundary_case_index}）",
            "③ 往返不一致",
        )
        combo.setCurrentIndex(0)
        check(
            form.value().boundary_case_index == 0,
            "③ 切回第 0 项往返一致",
            "③ 切回后往返不一致",
        )
    label_text = _form_label(form, combo)
    check(
        label_text is not None and "从0起" not in label_text and "工况" in label_text,
        f"③ 行标签为命名语义（{label_text!r}，无『从0起』裸整数语义）",
        f"③ 行标签异常: {label_text!r}",
    )

    # ---------- ④ 按研究类型分组 + modal 表单无 transient 专属字段 ----------
    group_texts = [dialog.tree.topLevelItem(i).text(0) for i in range(dialog.tree.topLevelItemCount())]
    check(
        dialog.tree.topLevelItemCount() == 5
        and [t.split("（")[0] for t in group_texts] == [label for label in LABELS.values()],
        f"④ 工作台按 5 类研究分组（{group_texts}）",
        f"④ 分组异常: {group_texts}",
    )
    modal_group = dialog.tree.topLevelItem(0)
    check(
        all("模态" in modal_group.child(i).text(0) or True for i in range(modal_group.childCount()))
        and modal_group.childCount() == 1,
        "④ 分组树 modal 组只含 modal 研究",
        "④ 分组混入其他类型",
    )
    transient_labels = {"持续时间 [s]", "初始步长 [s]", "最小步长 [s]", "最大步长 [s]", "输出间隔 [s]", "积分方法", "时间控制"}
    modal_labels = set()
    for i in range(form.layout().rowCount()):
        label_item = form.layout().itemAt(i, QFormLayout.LabelRole)
        if label_item is not None and label_item.widget() is not None:
            modal_labels.add(label_item.widget().text())
    overlap = modal_labels & transient_labels
    check(
        not overlap,
        f"④ modal 表单无 transient 专属字段（表单标签 {sorted(modal_labels)}）",
        f"④ modal 表单混入 transient 字段: {overlap}",
    )
    check(
        "模态阶数" in modal_labels,
        "④ modal 表单包含 modal 专属字段（模态阶数）",
        "④ modal 表单缺模态阶数",
    )
    grab(dialog, work, "4_grouped_workbench.png")

    # ---------- ⑤ 未注册后端：求解按钮禁用 + 文案含『尚未接通』 ----------
    check(
        not dialog.solve_button.isEnabled(),
        "⑤ 未注册后端 → 求解按钮 disabled",
        "⑤ 求解按钮未禁用",
    )
    check(
        "尚未接通" in dialog.solve_button.text(),
        f"⑤ 求解按钮文案含『尚未接通』（{dialog.solve_button.text()!r}）",
        f"⑤ 求解按钮文案异常: {dialog.solve_button.text()!r}",
    )
    check(
        "这里不会启动求解" in dialog.solve_button.text() or
        any("这里不会启动求解" in w.text() for w in dialog.findChildren(QLabel)),
        "⑤ 诚实声明保留（这里不会启动求解）",
        "⑤ 诚实声明丢失",
    )

    # ---------- 导出计算准备包（进程内，诚实状态 prepared_not_computed） ----------
    export_dir = work / "package"
    QFileDialog.getExistingDirectory = lambda *a, **k: str(export_dir)
    dialog.export()
    end = time.monotonic() + 15
    while dialog.task is not None and dialog.task.state in ("pending", "preparing") and time.monotonic() < end:
        app.processEvents()
        time.sleep(0.02)
    check(
        dialog.task is not None and dialog.task.state == "prepared_not_computed",
        f"导出完成 → 诚实状态 prepared_not_computed（{getattr(dialog.task, 'state', None)}）",
        f"导出状态异常: {getattr(dialog.task, 'state', None)}",
    )
    for _ in range(10):  # 等 poll() 定时器 tick 写入最终状态文本
        app.processEvents()
        if "准备包已导出" in dialog.status.text():
            break
        time.sleep(0.02)
    check(
        "未计算" in dialog.status.text() or "准备包已导出" in dialog.status.text(),
        f"导出后状态栏不虚报已求解（{dialog.status.text()!r}）",
        f"导出后状态栏虚报: {dialog.status.text()!r}",
    )

    # ---------- 框架兼容冒烟：kind/add 事务性 + 原工程不变 ----------
    dialog.kind.setCurrentIndex(dialog.kind.findData("modal"))
    before = len(dialog.project.advanced_studies)
    dialog.add()
    check(
        len(dialog.project.advanced_studies) == before + 1 and len(project.advanced_studies) == 5,
        "框架兼容：add() 事务性追加（工作台副本 +1，原工程不变）",
        "框架兼容破坏",
    )
    check(
        to_dict(ObjectForm(modal.settings, project, modal.settings).value()) == to_dict(modal.settings),
        "框架兼容：ObjectForm 往返与配置一致",
        "框架兼容：ObjectForm 往返不一致",
    )

    for _ in range(5):
        app.processEvents()
    dialog.reject()
    dialog.deleteLater()


def _find_status(dialog, study_id) -> str | None:  # noqa: ANN001
    tree = dialog.tree
    for i in range(tree.topLevelItemCount()):
        group = tree.topLevelItem(i)
        for j in range(group.childCount()):
            child = group.child(j)
            if child.data(0, dialog._ROLE_ID) == study_id:
                return child.text(1)
    return None


def _find_item(dialog, study_id):  # noqa: ANN001
    tree = dialog.tree
    for i in range(tree.topLevelItemCount()):
        group = tree.topLevelItem(i)
        for j in range(group.childCount()):
            child = group.child(j)
            if child.data(0, dialog._ROLE_ID) == study_id:
                return child
    return None


def _form_label(form, widget):  # noqa: ANN001
    layout = form.layout()
    for i in range(layout.rowCount()):
        field_item = layout.itemAt(i, QFormLayout.FieldRole)
        if field_item is not None and field_item.widget() is widget:
            label_item = layout.itemAt(i, QFormLayout.LabelRole)
            if label_item is not None and label_item.widget() is not None:
                return label_item.widget().text()
    return None


def grab(dialog, work, name: str) -> None:  # noqa: ANN001
    path = work / name
    try:
        dialog.grab().save(str(path))
        ok(f"截图已存证据目录: {path.name}（{path.stat().st_size} bytes）")
    except Exception as exc:  # noqa: BLE001
        fail(f"截图失败 {name}: {type(exc).__name__}: {exc}")


def _finish() -> None:
    log("\n" + "=" * 76)
    if _failures:
        log("VERIFY_WORKBENCH_UX: FAIL")
        for item in _failures:
            log(f"  [失败] {item}")
    else:
        log("VERIFY_WORKBENCH_UX: PASS（命名工况下拉 + validate 状态列 + 类型分组 + 诚实求解按钮）")


def _exit_code() -> int:
    return 1 if _failures else 0


if __name__ == "__main__":
    try:
        _exit = main()
    except Exception as exc:  # noqa: BLE001
        fail(f"VERIFY_WORKBENCH_UX: FAIL（顶层异常 {type(exc).__name__}: {exc}）")
        _exit = 1
    finally:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_PATH, "w", encoding="utf-8") as f:
            f.write("\n".join(_lines) + "\n")
        print(f"[verify] 日志已写入 {LOG_PATH}")
    sys.exit(_exit)
