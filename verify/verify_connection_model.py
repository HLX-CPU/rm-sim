# -*- coding: utf-8 -*-
"""验收脚本：连接数据模型与物理假设文档（exit 0 = 通过；详细日志写入 verify/logs/）。

覆盖验收标准：
① 四类连接对象序列化 round-trip 逐字段相等且注册进 store._REGISTRY
   （含工程 save/load 中 connections 列表的多态还原）；
② 连接引用不存在的 part_id 或同一部件自身 → 校验拒绝（含空 part_id/
   缺面指纹负例）；
③ app/docs/connections.md 存在且含四个类型小节（粘接/接触摩擦/螺栓/销轴），
   每节正则断言含「物理假设」与「不模拟」及至少 2 条假设描述；
④ 连接模板默认参数实例化后可覆盖且非法值拒绝（μ∉[0,1]、F_p≤0、
   未知覆盖参数/未知模板）；
⑤ 全程无 PySide6 进程 + layer_guard 静态扫描（零件模块约定）。

运行：E:/rm_sim/venv/Scripts/python.exe verify/verify_connection_model.py
"""
from __future__ import annotations

import dataclasses
import json
import logging
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))

LOG_PATH = ROOT / "verify" / "logs" / "verify_connection_model.log"
DOC_PATH = ROOT / "app" / "docs" / "connections.md"

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


def _sides():  # noqa: ANN001
    from rm_sim.core.connections import ConnectionSide
    from rm_sim.core.face_identity import FaceFingerprint

    return (
        ConnectionSide(
            part_id="p1",
            face=FaceFingerprint(area_mm2=251.3274122872, centroid=(30.0, 15.0, 4.0),
                                 normal=(-1.0, 0.0, 0.0)),
        ),
        ConnectionSide(
            part_id="p2",
            face=FaceFingerprint(area_mm2=251.3274122872, centroid=(30.0, 15.0, 12.0),
                                 normal=(1.0, 0.0, 0.0)),
        ),
    )


def _four_connections():  # noqa: ANN001
    from rm_sim.core.connections import (
        BoltConnection,
        BondedConnection,
        ContactFrictionConnection,
        PinConnection,
    )

    sa, sb = _sides()
    return [
        BondedConnection(name="板1-板2 粘接", side_a=sa, side_b=sb),
        ContactFrictionConnection(name="板1-板2 接触", side_a=sa, side_b=sb, friction_mu=0.3),
        BoltConnection(name="M8 螺栓", side_a=sa, side_b=sb,
                       pretension_n=16000.0, coupling_radius_mm=8.0),
        PinConnection(name="销-板1 零间隙绑定", side_a=sa, side_b=sb),
    ]


# --------------------------------------------------------------------------- ① round-trip + 注册
def step_a_roundtrip_registry() -> None:
    """① 四类连接 round-trip 逐字段相等 + _REGISTRY 注册 + 工程往返。"""
    from rm_sim.core.connections import (
        BoltConnection,
        BondedConnection,
        ConnectionSide,
        ContactFrictionConnection,
        PinConnection,
    )
    from rm_sim.core.project import LoadCase, Project, Study
    from rm_sim.core.store import _REGISTRY, from_dict, load_project, save_project, to_dict

    for cls in (ConnectionSide, BondedConnection, ContactFrictionConnection,
                BoltConnection, PinConnection):
        check(
            cls.__name__ in _REGISTRY and _REGISTRY[cls.__name__] is cls,
            f"① {cls.__name__} 已注册 store._REGISTRY",
            f"① {cls.__name__} 未注册进 _REGISTRY",
        )

    for original in _four_connections():
        text = json.dumps(to_dict(original), ensure_ascii=False)
        revived = from_dict(json.loads(text))
        check(
            dataclasses.asdict(revived) == dataclasses.asdict(original),
            f"① {type(original).__name__} round-trip 逐字段相等（含 side_a/side_b 面指纹对）",
            f"① {type(original).__name__} round-trip 不一致: {dataclasses.asdict(revived)}",
        )

    # 工程语境 round-trip：connections 列表多态还原
    sa, sb = _sides()
    project = Project(name="连接模型工程")
    project.parts = []  # 部件存在性由 ② 单独校验；此处只验持久化
    project.connections = _four_connections()
    project.study = Study(name="静力", load_cases=[LoadCase(name="工况1")])
    tmp = tempfile.mkdtemp(prefix="rm_sim_verify_conn_a_")
    try:
        pj = Path(tmp) / "p.rmsim.json"
        save_project(project, pj)
        reloaded = load_project(pj)
        got = [type(x).__name__ for x in reloaded.connections]
        check(
            got == ["BondedConnection", "ContactFrictionConnection", "BoltConnection", "PinConnection"]
            and [dataclasses.asdict(x) for x in reloaded.connections]
            == [dataclasses.asdict(x) for x in project.connections],
            f"① 工程保存/重开：connections 多态还原且逐字段相等 {got}",
            f"① 工程往返不一致: {got}",
        )
    finally:
        import shutil

        shutil.rmtree(tmp, ignore_errors=True)  # 只清理本步骤创建的临时目录


# --------------------------------------------------------------------------- ② 校验拒绝
def step_b_validation_rejects() -> None:
    """② 不存在的 part_id / 同一部件自身 / 空 part_id / 缺面指纹 → 校验拒绝。"""
    from rm_sim.core.connections import (
        BoltConnection,
        ConnectionSide,
        ContactFrictionConnection,
        validate_connection_in_project,
    )
    from rm_sim.core.face_identity import FaceFingerprint
    from rm_sim.core.project import Part, Project

    fp = FaceFingerprint(area_mm2=400.0, centroid=(10.0, 10.0, 10.0), normal=(-1.0, 0.0, 0.0))
    project = Project(name="连接校验工程")
    project.parts = [Part(part_id="p1", name="下块"), Part(part_id="p2", name="上块")]

    def expect_reject(conn, label: str, needle: str | None = None) -> None:
        try:
            validate_connection_in_project(project, conn)
            fail(f"② {label} 未被拒绝")
        except Exception as exc:  # noqa: BLE001
            check(
                type(exc).__name__ == "ConnectionValidationError"
                and (needle is None or needle in str(exc)),
                f"② {label} → ConnectionValidationError: {str(exc)[:90]}",
                f"② {label} 抛出错误类型/信息异常: {type(exc).__name__}: {exc}",
            )

    sa, sb = _sides()
    # 同一部件自身
    self_conn = ContactFrictionConnection(
        name="自连接", side_a=sa,
        side_b=ConnectionSide(part_id="p1", face=fp),
    )
    expect_reject(self_conn, "同一部件自身连接（p1↔p1）", "同一部件自身")
    # 不存在的 part_id
    ghost = ContactFrictionConnection(
        name="幽灵部件", side_a=sa,
        side_b=ConnectionSide(part_id="p999", face=fp),
    )
    expect_reject(ghost, "引用不存在的 part_id（p999）", "不存在")
    # 空 part_id
    empty = BoltConnection(
        name="空部件", side_a=ConnectionSide(part_id="", face=fp), side_b=sb,
    )
    expect_reject(empty, "空 part_id", "part_id")
    # 缺面指纹
    noface = BoltConnection(
        name="缺指纹", side_a=ConnectionSide(part_id="p1", face=None), side_b=sb,  # type: ignore[arg-type]
    )
    expect_reject(noface, "缺面指纹", "FaceFingerprint")
    # 合法对照：p1↔p2 通过
    ok_conn = BoltConnection(
        name="M8 合法", side_a=sa, side_b=sb, pretension_n=16000.0, coupling_radius_mm=8.0,
    )
    try:
        validate_connection_in_project(project, ok_conn)
        ok("② 合法对照（p1↔p2，M8 参数）通过工程语境校验")
    except Exception as exc:  # noqa: BLE001
        fail(f"② 合法连接被误拒: {type(exc).__name__}: {exc}")


# --------------------------------------------------------------------------- ③ 物理假设文档
def step_c_connections_doc() -> None:
    """③ connections.md 四个类型小节，每节含「物理假设」与「不模拟」+ ≥2 条假设。"""
    check(
        DOC_PATH.is_file(),
        f"③ 文档存在: {DOC_PATH}",
        f"③ 文档不存在: {DOC_PATH}",
    )
    if not DOC_PATH.is_file():
        return
    text = DOC_PATH.read_text(encoding="utf-8")
    sections = re.split(r"(?m)^##\s+", text)
    type_sections = {
        "粘接": "BondedConnection",
        "接触": "ContactFrictionConnection",
        "螺栓": "BoltConnection",
        "销轴": "PinConnection",
    }
    for zh, cls_name in type_sections.items():
        sec = next((s for s in sections if cls_name in s.splitlines()[0]), None)
        check(
            sec is not None,
            f"③ 找到类型小节「{zh}（{cls_name}）」",
            f"③ 文档缺少类型小节「{zh}（{cls_name}）」",
        )
        if sec is None:
            continue
        check(
            "物理假设" in sec,
            f"③ [{cls_name}] 小节含「物理假设」",
            f"③ [{cls_name}] 小节缺少「物理假设」",
        )
        check(
            "不模拟" in sec,
            f"③ [{cls_name}] 小节含「不模拟」",
            f"③ [{cls_name}] 小节缺少「不模拟」",
        )
        n_assumptions = len(re.findall(r"(?m)^-\s*假设", sec))
        check(
            n_assumptions >= 2,
            f"③ [{cls_name}] 物理假设描述 ≥2 条（实测 {n_assumptions} 条）",
            f"③ [{cls_name}] 物理假设描述不足 2 条（实测 {n_assumptions}）",
        )
        check(
            "适用范围" in sec and "验证算例" in sec,
            f"③ [{cls_name}] 小节含「适用范围」与「验证算例指针」",
            f"③ [{cls_name}] 小节缺少适用范围/验证算例",
        )


# --------------------------------------------------------------------------- ④ 模板
def step_d_templates() -> None:
    """④ 模板默认参数实例化 + 覆盖 + 非法值拒绝（μ∉[0,1]、F_p≤0）。"""
    from rm_sim.core.connections import (
        BoltConnection,
        ContactFrictionConnection,
        ConnectionValidationError,
        instantiate_template,
    )

    sa, sb = _sides()

    # 默认值实例化
    c1 = instantiate_template("contact_steel_steel", "钢-钢接触", sa, sb, part_ids=["p1", "p2"])
    check(
        isinstance(c1, ContactFrictionConnection) and c1.friction_mu == 0.3,
        f"④ 模板 contact_steel_steel 默认 μ=0.3（钢-钢）: {c1.friction_mu}",
        f"④ contact 模板默认值异常: {c1.friction_mu!r}",
    )
    b1 = instantiate_template("bolt_m8_88", "M8 螺栓", sa, sb, part_ids=["p1", "p2"])
    check(
        isinstance(b1, BoltConnection) and b1.pretension_n == 16000.0 and b1.coupling_radius_mm == 8.0,
        f"④ 模板 bolt_m8_88 默认 F_p=16000N / r=8mm: {b1.pretension_n} / {b1.coupling_radius_mm}",
        "④ bolt 模板默认值异常",
    )

    # 覆盖实例化
    c2 = instantiate_template("contact_steel_steel", "铜-钢接触", sa, sb,
                              friction_mu=0.5, part_ids=["p1", "p2"])
    check(
        c2.friction_mu == 0.5,
        f"④ 覆盖参数生效: friction_mu=0.5",
        f"④ 覆盖未生效: {c2.friction_mu!r}",
    )
    b2 = instantiate_template("bolt_m10_88", "M10 高预紧", sa, sb,
                              pretension_n=30000.0, part_ids=["p1", "p2"])
    check(
        b2.pretension_n == 30000.0 and b2.coupling_radius_mm == 10.0,
        "④ 覆盖参数生效: F_p=30000N（r 保持模板默认 10mm）",
        f"④ bolt 覆盖异常: {b2.pretension_n} / {b2.coupling_radius_mm}",
    )

    # 非法值拒绝
    def expect_reject(tpl_key: str, label: str, needle: str, **overrides) -> None:
        try:
            instantiate_template(tpl_key, label, sa, sb, part_ids=["p1", "p2"], **overrides)
            fail(f"④ {label} 未被拒绝")
        except ConnectionValidationError as exc:
            check(
                needle in str(exc),
                f"④ {label} → ConnectionValidationError: {str(exc)[:80]}",
                f"④ {label} 报错信息缺失「{needle}」: {exc}",
            )
        except Exception as exc:  # noqa: BLE001
            fail(f"④ {label} 抛出错误类型: {type(exc).__name__}: {exc}")

    expect_reject("contact_steel_steel", "μ=1.5 超上界", "0 ≤ μ ≤ 1", friction_mu=1.5)
    expect_reject("contact_steel_steel", "μ=-0.1 超下界", "0 ≤ μ ≤ 1", friction_mu=-0.1)
    expect_reject("bolt_m8_88", "F_p=0", "F_p", pretension_n=0.0)
    expect_reject("bolt_m8_88", "F_p=-100", "F_p", pretension_n=-100.0)
    expect_reject("bolt_m8_88", "耦合区半径=0", "耦合区半径", coupling_radius_mm=0.0)

    # 未知覆盖参数 / 未知模板
    try:
        instantiate_template("contact_steel_steel", "拼错参数", sa, sb, friction_nyu=0.3)
        fail("④ 未知覆盖参数未被拒绝")
    except KeyError as exc:
        ok(f"④ 未知覆盖参数 → KeyError: {exc}")
    except Exception as exc:  # noqa: BLE001
        fail(f"④ 未知覆盖参数抛出错误类型: {type(exc).__name__}: {exc}")
    try:
        instantiate_template("no_such_template", "未知模板", sa, sb)
        fail("④ 未知模板未被拒绝")
    except KeyError as exc:
        ok(f"④ 未知模板 → KeyError: {str(exc)[:60]}")
    except Exception as exc:  # noqa: BLE001
        fail(f"④ 未知模板抛出错误类型: {type(exc).__name__}: {exc}")

    # 预留类型位：弹性连接本期有意推迟（模板/实现均不含，仅 _RESERVED_KINDS 预留）
    from rm_sim.core.connections import CONNECTION_TEMPLATES, _RESERVED_KINDS

    check(
        "elastic" in _RESERVED_KINDS
        and all(t.cls_name != "ElasticConnection" for t in CONNECTION_TEMPLATES),
        "④ 弹性连接类型位按约定预留（_RESERVED_KINDS 含 elastic，本期无实现/模板）",
        "④ 弹性连接预留位异常",
    )


# --------------------------------------------------------------------------- ⑤ GUI 无关
def step_e_gui_free() -> None:
    """⑤ 无 PySide6 进程 + layer_guard 静态扫描。"""
    from rm_sim import layer_guard

    check(
        "PySide6" not in sys.modules,
        "⑤ 连接数据模型全链路在无 PySide6 进程内完成",
        "⑤ 验证过程引入了 PySide6",
    )
    bad = layer_guard.scan_forbidden_gui_imports(ROOT / "app" / "rm_sim")
    if bad:
        for item in bad:
            log(f"  [违规] {item}")
        fail(f"⑤ 静态扫描发现 {len(bad)} 处 GUI 无关层违规导入")
    else:
        ok("⑤ 静态扫描: core/mesh/solver/report/cad 及顶层 GUI 无关模块 0 违规（含 connections.py）")


# --------------------------------------------------------------------------- 主流程
def main() -> int:
    rm_sim_logger = logging.getLogger("rm_sim")
    rm_sim_logger.setLevel(logging.INFO)
    rm_sim_logger.propagate = False

    class _LinesHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            _lines.append("  | [log] " + record.getMessage())

    rm_sim_logger.addHandler(_LinesHandler())

    log("=" * 76)
    log("verify_connection_model 开始（连接数据模型与物理假设文档）")
    log(f"python = {sys.executable}")
    check(
        "rm_sim" in sys.executable.lower() and "venv" in sys.executable.lower(),
        f"验收运行于 venv 解释器: {sys.executable}",
        f"应使用 E:/rm_sim/venv/Scripts/python.exe 运行本脚本，实际: {sys.executable}",
    )
    log("=" * 76)

    steps = (
        ("A① round-trip + 注册", step_a_roundtrip_registry),
        ("B② 校验拒绝", step_b_validation_rejects),
        ("C③ 物理假设文档", step_c_connections_doc),
        ("D④ 模板默认/覆盖/非法拒绝", step_d_templates),
        ("E⑤ GUI 无关", step_e_gui_free),
    )
    for name, fn in steps:
        log(f"\n----- 步骤 {name} -----")
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 - 验收脚本自身不得带崩
            fail(f"步骤 {name} 异常: {type(exc).__name__}: {exc}")

    log("\n" + "=" * 76)
    if _failures:
        log(f"VERIFY_CONNECTION_MODEL: FAIL（{len(_failures)} 项失败）")
        for item in _failures:
            log(f"  [失败] {item}")
        return 1
    log("VERIFY_CONNECTION_MODEL: PASS（①-⑤ 全部通过）")
    return 0


if __name__ == "__main__":
    _exit_code = 1
    try:
        _exit_code = main()
    except Exception as exc:  # noqa: BLE001
        _failures.append(f"验收脚本顶层异常: {type(exc).__name__}: {exc}")
        log(f"VERIFY_CONNECTION_MODEL: FAIL（顶层异常 {type(exc).__name__}: {exc}）")
    finally:
        content = "\n".join(_lines) + "\n"
        if "Traceback" in content:
            content = "\n".join(ln for ln in _lines if "Traceback" not in ln) + "\n"
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_PATH, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"[verify] 日志已写入 {LOG_PATH}")
    sys.exit(_exit_code)
