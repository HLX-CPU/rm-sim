# -*- coding: utf-8 -*-
"""验收脚本：结果云图点面溯源（模块39：结果视图点面 → 选中面卡片 + 局部极值）。

配方同 verify_viewport_gl.py（native windows GL + Qt Test 合成鼠标），
求解产物用 assembly_two_blocks 真实 ccx 求解（产品流 win._on_solve_static，
配方同 verify_gui_assembly）。GL 未就绪 = FAIL（本验收对象即 GL 结果视图交互）。

范围（B5）：产品 NSET 仅覆盖约束（FIXED）与非重力载荷（LOADFACE）面
（assembly_workflow.py:60-76 实读）；本功能主范围 = 约束/载荷面 100% 溯源，
其余边界面如实归入『无挂接定义』类（tag=-1，UI 明示，不虚报归属）；
连接贴合面 NSET 映射为可选扩展，本实现未启用（不改 convert/deck 数值链路，
红线保持，验收⑤不适用——如实记录）。证据更正：probes/pick_probe_log.txt:25
的 2556/2556=100.00% 是单零件网格对全部 6 个 occ 面的匹配，不等于产品
NSET 子集命中；本验收按产品 NSET 子集规则独立复算。

覆盖验收标准：
① 约束/载荷面（产品 NSET）覆盖的边界三角形 tag 归属率 100%：独立复算
   「角点节点集 ⊆ 面 NSET」规则与主窗口发布数组逐三角形一致——NSET 面
   （固定面 tag=3、载荷面 tag=10）上不存在未命中三角形；其余边界面 = -1；
② 同一屏幕位置几何视图与结果视图点击 NSET 面命中 tag 相同（tag=3）；
③ 点击挂接面 → 卡片列出该面约束/载荷名；点击无定义面 → 卡片显示
   『无挂接定义』（不虚报）；
④ 点击面卡片局部 Mises 极值与整场同源数组重算一致（|diff|≤1e-9 相对）；
⑤ 连接面扩展未实现（红线保持）——显式记录，不虚报。
全程 backend==gl_native_offscreen 断言；截图与日志落证据目录。

运行：E:/rm_sim/venv/Scripts/python.exe verify/verify_result_field_face_pick.py
日志：verify/logs/verify_result_field_face_pick.log。
"""
from __future__ import annotations

import logging
import os
import sys
import time
from pathlib import Path

os.environ.pop("QT_QPA_PLATFORM", None)

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "verify"))

LOG_PATH = ROOT / "verify" / "logs" / "verify_result_field_face_pick.log"
SAMPLE = ROOT / "samples" / "assembly_two_blocks.step"
GL_BACKEND = "gl_native_offscreen"

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


class _Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.INFO)
        self.records: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        msg = record.getMessage()
        self.records.append(msg)
        _lines.append("  | [log] " + msg)


CAPTURE = _Capture()

_WIN = None


def require_gl_backend(gate: str) -> bool:
    backend_value = _WIN.viewport.backend.value
    check(
        backend_value == GL_BACKEND,
        f"{gate} 前置：backend=={GL_BACKEND}",
        f"{gate} 前置失败：backend={backend_value}（期望 {GL_BACKEND}）",
    )
    return backend_value == GL_BACKEND


def expected_face_tags(win) -> tuple:  # noqa: ANN001
    """独立复算「角点节点集 ⊆ 面 NSET」归属规则（验收①基准；与实现同规独立写）。"""
    import numpy as np

    active = win._active_result
    row_of = {int(n): i for i, n in enumerate(np.asarray(active["node_ids"]).ravel())}
    mesh_result = win._solve_mesh_result
    face_rows: dict[int, set] = {}
    nset_faces = win._last_nset_face_tags or {}
    for name, occ_tag in nset_faces.items():
        gtags = (mesh_result.node_sets or {}).get(str(name), ()) if mesh_result else ()
        rows = {row_of[int(g)] for g in gtags if int(g) in row_of}
        if rows:
            face_rows.setdefault(int(occ_tag), set()).update(rows)
    tris = np.asarray(active["tris"], dtype=np.int64)
    exp = np.full(len(tris), -1, dtype=np.int64)
    for k, tri in enumerate(tris):
        corners = {int(tri[0]), int(tri[1]), int(tri[2])}
        hits = [t for t, rows in face_rows.items() if corners <= rows]
        if len(hits) == 1:
            exp[k] = hits[0]
        elif len(hits) > 1:
            exp[k] = min(hits)
    return exp, face_rows


def main() -> int:
    rm_sim_logger = logging.getLogger("rm_sim")
    rm_sim_logger.setLevel(logging.INFO)
    rm_sim_logger.propagate = False
    rm_sim_logger.addHandler(CAPTURE)

    log("=" * 76)
    log("verify_result_field_face_pick 开始（结果云图点面溯源）")
    log(f"python = {sys.executable}")
    check(
        "rm_sim" in sys.executable.lower() and "venv" in sys.executable.lower(),
        f"验收运行于 venv 解释器: {sys.executable}",
        f"应使用 E:/rm_sim/venv/Scripts/python.exe 运行本脚本，实际: {sys.executable}",
    )
    log("=" * 76)

    from assembly_check_utils import evidence_dir

    evidence = evidence_dir("result_field_face_pick")
    log(f"证据目录: {evidence}")

    import numpy as np
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QMessageBox

    from rm_sim.cad.assembly_import import import_assembly
    from rm_sim.core.connections import BondedConnection, ConnectionSide
    from rm_sim.core.face_identity import fingerprint_from_face
    from rm_sim.core.materials import set_part_material
    from rm_sim.core.project import Constraint, LoadCase, Project, TractionLoad
    from rm_sim.gui.main_window import MainWindow
    from rm_sim.gui.viewport import ViewportBackend

    app = QApplication.instance() or QApplication(["verify_result_field_face_pick"])
    warnings: list[str] = []
    QMessageBox.warning = lambda *a, **k: warnings.append(str(a[2:]))
    QMessageBox.information = lambda *a, **k: warnings.append(str(a[2:]))

    win = MainWindow()
    _WIN = win  # noqa: F841 - require_gl_backend 使用
    globals()["_WIN"] = win
    win.resize(1240, 800)
    win.show()
    for _ in range(20):
        app.processEvents()

    backend = win.ensure_viewport()
    check(
        backend is ViewportBackend.GL,
        f"GL 视口就绪: backend={backend.value}",
        f"GL 视口未就绪: backend={backend.value}"
        f"（last_error={win.viewport.last_error or '（无）'}）",
    )
    if backend is not ViewportBackend.GL:
        _finish()
        return _exit_code()

    model = import_assembly(SAMPLE)
    win.solid_model = model
    # project_dir 必须预设（配方同 verify_gui_assembly）：否则 _on_solve_static
    # 弹模态目录选择对话框阻塞无人值守验收
    win.project = Project(name="result_field_face_pick_demo", project_dir=str(evidence))
    win.project.study.geometry_file = str(SAMPLE)
    win.show_assembly()
    set_part_material(win.project, "p1", "Q235")
    set_part_material(win.project, "p2", "Q235")
    face3 = model.face_by_tag(3)   # 部件1 y=0 面（固定，产品 NSET: FIXED_FACE_3）
    face10 = model.face_by_tag(10)  # 部件2 y=40 面（面力，产品 NSET: LOADFACE_FACE_10）
    face1 = model.face_by_tag(1)   # 部件1 x=0 面（无挂接定义，自由边界面）
    face4 = model.face_by_tag(4)   # 部件1 贴合界面 y=20（连接面——按 B5 不入产品 NSET）
    face9 = model.face_by_tag(9)   # 部件2 贴合界面 y=20（同上）
    win.project.study.load_cases = [
        LoadCase(
            name="工况1",
            constraints=[
                Constraint(name="底面固定", face_tag=3,
                           face=fingerprint_from_face(face3), part_id="p1")
            ],
            loads=[
                TractionLoad(name="顶面总力", face_tag=10,
                             face=fingerprint_from_face(face10), part_id="p2",
                             total_force_n=1000.0, direction=(0.0, -1.0, 0.0))
            ],
        )
    ]
    # 装配连接性门（deck 层）要求 p2 经连接接地：界面粘接 p1↔p2。
    # 连接贴合面按 B5 不入产品 NSET——正好验证其三角形归入无定义类
    win.project.connections.append(
        BondedConnection(
            name="界面粘接",
            side_a=ConnectionSide("p1", fingerprint_from_face(face4)),
            side_b=ConnectionSide("p2", fingerprint_from_face(face9)),
        )
    )
    win._refresh_project_views()
    log(f"  | NSET 计划: 固定={face3.tag} 载荷={face10.tag}（两部件装配流 → FIXED_FACE_3/LOADFACE_FACE_10）")

    # ---------- 真实求解（产品流，配方同 verify_gui_assembly） ----------
    win._on_solve_static()
    deadline = time.monotonic() + 900
    while win._last_archive is None and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.02)
    check(
        win._last_archive is not None and win._solve_task is not None
        and win._solve_task.state == "completed",
        f"真实求解完成（task={getattr(win._solve_task, 'state', None)}，"
        f"archive={bool(win._last_archive)}）",
        f"求解超时或失败: state={getattr(win._solve_task, 'state', None)} warnings={warnings[:3]}",
    )
    if not (win._last_archive and getattr(win._solve_task, "state", "") == "completed"):
        _finish()
        return _exit_code()
    check(not warnings, f"求解过程无警告弹窗（{warnings[:2]}）", f"求解出现警告: {warnings[:3]}")
    check(
        require_gl_backend("求解后"),
        "求解完成后仍为 GL 后端", "求解后后端异常",
    )
    nset_faces = dict(win._last_nset_face_tags)
    log(f"  | 产品 NSET→occ tag: {nset_faces}")
    check(
        set(nset_faces.values()) == {3, 10},
        f"① 产品 NSET 覆盖固定面3与载荷面10（实际 {sorted(nset_faces.values())}）",
        f"① 产品 NSET 异常: {nset_faces}",
    )

    # ---------- ① tag 归属率 100%（独立复算「角点 ⊆ NSET」规则） ----------
    if require_gl_backend("①"):
        tags = win._result_face_tags
        check(
            tags is not None and win.viewport._gl_face_tags is not None,
            "① 结果云图已附着逐三角形 occ tag（_result_face_tags/_gl_face_tags）",
            "① 结果云图未附着面 tag（溯源缺失）",
        )
        if tags is not None:
            exp, face_rows = expected_face_tags(win)
            check(
                np.array_equal(np.asarray(tags), exp),
                f"① 独立复算与发布数组逐三角形一致（{len(tags)} 个三角形，"
                f"定义面 {int((exp >= 0).sum())} / 无定义 {int((exp < 0).sum())}）",
                "① 归属数组与独立复算不一致（存在虚报或漏归属）",
            )
            active = win._active_result
            tris = np.asarray(active["tris"], dtype=np.int64)
            for occ_tag, rows in sorted(face_rows.items()):
                on_face = np.array(
                    [
                        {int(a), int(b), int(c)} <= rows
                        for a, b, c in tris
                    ]
                )
                n_on = int(on_face.sum())
                n_miss = int((on_face & (np.asarray(tags) != occ_tag)).sum())
                check(
                    n_on > 0 and n_miss == 0,
                    f"① NSET 面 tag={occ_tag}：{n_on} 个边界三角形全部命中（未命中 {n_miss}，"
                    f"归属率 100%）",
                    f"① NSET 面 tag={occ_tag} 存在未命中三角形（{n_miss}/{n_on}）",
                )
            check(
                int((np.asarray(tags) < 0).sum()) > 0,
                f"① 其余边界面归入无定义类 tag=-1（{int((np.asarray(tags) < 0).sum())} 个，"
                f"如实归入不虚报）",
                "① 无定义类三角形缺失（归属过宽，虚报）",
            )
            try:
                win.grab().save(str(evidence / "1_result_view_tags.png"))
                ok("① 结果视图截图已存证据目录: 1_result_view_tags.png")
            except Exception as exc:  # noqa: BLE001
                fail(f"① 截图失败: {type(exc).__name__}: {exc}")

    widget = win.viewport.current_widget

    def aim_camera(face) -> None:  # noqa: ANN001
        n = np.asarray(face.normal, dtype=float)
        c = np.asarray(face.center, dtype=float)
        win.viewport.set_camera(tuple(c + 300.0 * n), tuple(c), (0.0, 0.0, 1.0))
        for _ in range(5):
            app.processEvents()

    def click_at(pos) -> bool:  # noqa: ANN001
        win.viewport.last_pick = None
        QTest.mouseClick(
            widget, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(*pos)
        )
        for _ in range(5):
            app.processEvents()
        return True

    # ---------- ② 同一屏幕位置：几何视图 vs 结果视图命中相同 tag ----------
    if require_gl_backend("②") and widget is not None:
        win.show_assembly()  # 回几何视图（重置结果视图状态）
        for _ in range(5):
            app.processEvents()
        check(
            not win._result_view_active,
            "② 几何视图：结果溯源卡片模式已关闭",
            "② 几何视图仍处于结果卡片模式",
        )
        aim_camera(face3)
        pos = win.viewport.project_point(face3.center)
        check(pos is not None, "② 固定面中心投影在视口内", "② 投影失败")
        if pos is not None:
            click_at(pos)
            geo_tag = win.viewport.last_pick
            check(
                geo_tag == 3,
                f"② 几何视图点击命中 tag={geo_tag}（期望 3）",
                f"② 几何视图命中异常: {geo_tag}",
            )
            win.select_result_part("装配合成")  # 结果视图（同一相机下重设）
            for _ in range(5):
                app.processEvents()
            aim_camera(face3)  # 同一相机：两视图同一屏幕位置
            click_at(pos)
            res_tag = win.viewport.last_pick
            check(
                res_tag == geo_tag == 3,
                f"② 同一屏幕位置结果视图命中 tag={res_tag}（几何视图 {geo_tag}，相同）",
                f"② 两视图命中不一致: 几何={geo_tag} 结果={res_tag}",
            )
            check(
                win.viewport._gl_face_tags is not None
                and int(win.viewport._gl_face_tags.max()) >= 3,
                "② 结果视图 cell_data 附着 occ tag（溯源生效）",
                "② 结果视图无面 tag 数据",
            )

    # ---------- ③ 卡片：挂接面列定义名 / 无定义面显示『无挂接定义』 ----------
    if require_gl_backend("③"):
        aim_camera(face3)
        pos = win.viewport.project_point(face3.center)
        if pos is not None and click_at(pos):
            payload = win.properties.face_card.last_payload
            check(
                payload is not None and payload.get("tag") == 3 and payload.get("is_result_view"),
                f"③ 点击挂接面3 → 卡片为结果溯源模式（payload tag={payload and payload.get('tag')}）",
                f"③ 卡片异常: {payload}",
            )
            if payload:
                check(
                    "底面固定" in (payload.get("definitions_text") or ""),
                    f"③ 卡片列出该面约束名（{payload.get('definitions_text')!r}）",
                    f"③ 卡片定义名缺失: {payload.get('definitions_text')!r}",
                )
                check(
                    "顶面总力" not in (payload.get("definitions_text") or ""),
                    "③ 卡片不虚列其他面的定义（载荷面10 的定义不在面3 卡片中）",
                    f"③ 卡片虚报定义: {payload.get('definitions_text')!r}",
                )
            try:
                win.grab().save(str(evidence / "3_defined_face_card.png"))
                ok("③ 挂接面卡片截图已存证据目录: 3_defined_face_card.png")
            except Exception as exc:  # noqa: BLE001
                fail(f"③ 截图失败: {type(exc).__name__}: {exc}")
        aim_camera(face1)
        pos = win.viewport.project_point(face1.center)
        if pos is not None and click_at(pos):
            payload = win.properties.face_card.last_payload
            check(
                payload is not None and payload.get("undefined")
                and int(payload.get("tag", 0)) == -1,
                f"③ 点击无定义面1 → 卡片 tag=-1 undefined（{payload}）",
                f"③ 无定义面卡片异常: {payload}",
            )
            check(
                "无挂接定义" in win.properties.face_card.title_text,
                f"③ 卡片标题显示『无挂接定义』（{win.properties.face_card.title_text!r}）",
                f"③ 卡片标题异常: {win.properties.face_card.title_text!r}",
            )
            check(
                win.viewport.highlight_face_tag is None
                or int(win.viewport.highlight_face_tag or 0) <= 0,
                "③ 无定义面点击不产生归属高亮（不虚报）",
                f"③ 无定义面被高亮: {win.viewport.highlight_face_tag}",
            )
            try:
                win.grab().save(str(evidence / "3_undefined_face_card.png"))
                ok("③ 无定义面卡片截图已存证据目录: 3_undefined_face_card.png")
            except Exception as exc:  # noqa: BLE001
                fail(f"③ 截图失败: {type(exc).__name__}: {exc}")

    # ---------- ④ 局部 Mises 极值与整场同源数组重算一致 ----------
    if require_gl_backend("④"):
        aim_camera(face3)
        pos = win.viewport.project_point(face3.center)
        if pos is not None and click_at(pos):
            payload = win.properties.face_card.last_payload
            gl = win.viewport._gl_mesh
            check(
                payload is not None and gl is not None
                and payload.get("local_max_mises") is not None,
                "④ 卡片含面内局部 Mises 极值",
                f"④ 卡片缺局部极值: {payload}",
            )
            if payload and gl is not None and payload.get("local_max_mises") is not None:
                # pyvista 0.49：三角连接表属性为 regular_faces（旧 .cells 已移除）
                cells = (np.asarray(gl.regular_faces, dtype=np.int64)
                         if hasattr(gl, "regular_faces") and gl.regular_faces is not None
                         else np.asarray(gl.cells, dtype=np.int64).reshape(-1, 4)[:, 1:])
                ftags = np.asarray(gl.cell_data["face_tag"], dtype=np.int64)
                scalars = np.asarray(gl.point_data["scalars"], dtype=float)
                mask = ftags == 3
                check(
                    bool(mask.any()),
                    f"④ 结果表面含面3 三角形 {int(mask.sum())} 个",
                    "④ 结果表面无面3 三角形",
                )
                if mask.any():
                    nodes = np.unique(cells[mask].ravel())
                    m = float(np.max(scalars[nodes]))
                    card_v = float(payload["local_max_mises"])
                    diff = abs(card_v - m) / max(abs(m), 1e-30)
                    check(
                        diff <= 1e-9,
                        f"④ 卡片局部极值与整场同源数组重算一致"
                        f"（卡片 {card_v:.10g} vs 重算 {m:.10g}，|diff|={diff:.3g}≤1e-9 相对）",
                        f"④ 局部极值不一致: 卡片 {card_v!r} vs 重算 {m!r}（|diff|={diff:.3g}）",
                    )
                    whole = float(np.max(scalars))
                    log(f"  | [info] 面3 局部 max={m:.6g} MPa；整场 max={whole:.6g} MPa")
            try:
                win.grab().save(str(evidence / "4_local_extreme_card.png"))
                ok("④ 局部极值卡片截图已存证据目录: 4_local_extreme_card.png")
            except Exception as exc:  # noqa: BLE001
                fail(f"④ 截图失败: {type(exc).__name__}: {exc}")

    # ---------- ⑤ 连接面扩展：未实现（红线保持），显式记录 ----------
    log(
        "[RECORD] ⑤ 连接贴合面 NSET 映射为可选扩展，本实现未启用：需在 mesher/deck "
        "链路为粘接主从面发 NSET，将解除『不改 convert/deck 数值链路』红线并需单列验收；"
        "本验收范围内产品 NSET（约束/载荷面）100% 溯源，其余边界面如实归入无定义类。"
    )
    check(
        win._last_nset_face_tags and all(
            str(k).startswith(("FIXED", "LOADFACE")) for k in win._last_nset_face_tags
        ),
        f"⑤ 产品 NSET 仅约束/载荷类（{sorted(win._last_nset_face_tags)}），红线未解除",
        f"⑤ NSET 出现非约束/载荷类（扩展被静默启用？）: {win._last_nset_face_tags}",
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
    _finish()
    return _exit_code()


def _finish() -> None:
    log("\n" + "=" * 76)
    if _failures:
        log("VERIFY_RESULT_FIELD_FACE_PICK: FAIL")
        for item in _failures:
            log(f"  [失败] {item}")
    else:
        log("VERIFY_RESULT_FIELD_FACE_PICK: PASS（NSET 溯源 100% + 双视图同 tag + 卡片定义/极值）")


def _exit_code() -> int:
    return 1 if _failures else 0


if __name__ == "__main__":
    _exit = 1
    try:
        _exit = main()
    except Exception as exc:  # noqa: BLE001
        fail(f"VERIFY_RESULT_FIELD_FACE_PICK: FAIL（顶层异常 {type(exc).__name__}: {exc}）")
        _exit = 1
    finally:
        _finish()
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_PATH, "w", encoding="utf-8") as f:
            f.write("\n".join(_lines) + "\n")
        print(f"[verify] 日志已写入 {LOG_PATH}")
    sys.exit(_exit)
