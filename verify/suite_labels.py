# -*- coding: utf-8 -*-
"""验收套件标签与耗时分解口径（调研 §4 四层 → fast_config/real_small/reliability/full）。

出处：analysis/装配仿真验收提速调研_2026-11-03.md §4 分层表与 §7-1（加验收耗时分解
和依赖/套件标签，保留原完整验收入口及所有标准）。

口径（显式、可机检，逐检查理由见 CHECK_LABELS）：
- fast_config  快速配置/界面——**不启动求解引擎（ccx）**；文案、表单、工程保存、
               依赖、几何/网格解析类检查。允许运行 gmsh/OCC/Qt（真实网格与几何
               计算不算「启动引擎」——调研 §4 行 1 的检查方式即「不启动引擎」）。
- real_small   真实小算例——启动真实 ccx 的小算例求解（材料、载荷、连接、INP 编译、
               结果解析的数值物理检查）。
- reliability  运行可靠性——调度器、取消、线程、内存保护、进程生命周期与守卫/
               故障路径；允许可控夹具与真实小算例（调研 §4 行 3），检查对象是
               运行行为而非数值物理。
- full         全套/整装档——仅显式完整回归/--slow 才跑的整装级检查（真实装配
               覆盖、跨进程取消响应）。

约束（铁律）：标签只用于**选择与报告**，不改变任何既有门、容差与原完整验收
入口；每个检查恰有一个主标签（主标签按检查对象归类，理由逐项记录在
CHECK_LABELS；四类构成 DEFAULT∪SLOW 全集的干净划分，选择结果因此可精确断言）。

五门（module_46 口径：static_solve / assembly_results / bonded_connection /
bolt_pin_connection / contact_friction_connection）全部为 real_small。
"""
from __future__ import annotations

# 四类标签 → 人话口径（对照调研 §4 分层表行序）。
LABELS: dict[str, str] = {
    "fast_config": "快速配置/界面：不启动求解引擎(ccx)；文案、表单、工程保存、依赖、几何/网格解析类",
    "real_small": "真实小算例：启动真实 ccx 小算例求解（材料、载荷、连接、INP 编译、结果解析）",
    "reliability": "运行可靠性：调度器、取消、线程、内存保护、进程生命周期与守卫/故障路径",
    "full": "全套/整装档：仅显式完整回归/--slow 才跑的整装级检查（真实装配覆盖）",
}

LABEL_ORDER = ("fast_config", "real_small", "reliability", "full")

# 每个检查的 (主标签, 归类理由)。理由引用各 verify 脚本 docstring/实读（2026-11-03）。
CHECK_LABELS: dict[str, tuple[str, str]] = {
    # ---------------- fast_config（不启动求解引擎） ----------------
    "main_entry": ("fast_config", "统一入口 --selfcheck/主窗口框架/依赖与 config 检查；offscreen 启动，不求解"),
    "step_import": ("fast_config", "STEP 导入/单位换算/三维选面；OCC+gmsh 解析，无 ccx"),
    "material_library": ("fast_config", "材料库数值范围与零件材料设置；纯配置"),
    "load_definition": ("fast_config", "约束与载荷定义序列化/面压换算；纯配置"),
    "mesh_generation": ("fast_config", "gmsh 网格划分与 INP 导出；真实网格计算但不启动求解引擎"),
    "relaunch": ("fast_config", "整机启动冒烟 --smoke 两次可重启；不求解"),
    "assembly_import": ("fast_config", "装配 STEP 导入与部件身份（体积/质心/bbox）；无 ccx"),
    "assembly_loads": ("fast_config", "装配级约束/载荷挂面指纹与转换器；无 ccx"),
    "connection_model": ("fast_config", "连接数据模型序列化与物理假设文档；纯模型"),
    "assembly_geometry": ("fast_config", "解析几何间隙/重叠控制与源实体不变；无 ccx"),
    "contact_candidates": ("fast_config", "解析平面/曲面共域与 OCC 失败处理；无 ccx"),
    "history_restore": ("fast_config", "已存工程迁移只读恢复（真实 Qt 动作，read-only）；无求解"),
    "step_material_gate": ("fast_config", "GUI 材料门：无材料时 prepare 必须被拒（不产出 deck，不求解）"),
    "geometry_inspection_gui": ("fast_config", "Qt worker 几何检查/候选归属/取消；无求解"),
    "material_suggestions": ("fast_config", "批量材料建议保持实测材料、未知不武断；无引擎"),
    "local_mesh_controls": ("fast_config", "局部网格控制与 mesh_only 后台流程；真实 gmsh 但不启动 ccx"),
    "advanced_framework": ("fast_config", "脚本自述：No CAD/mesh/solver execution——配置框架检查"),
    # ---------------- real_small（真实 ccx 小算例） ----------------
    "static_solve": ("real_small", "CalculiX 静力求解与任务调度（真实 ccx，悬臂 δ 理论锚）"),
    "results_view": ("real_small", "对真实求解产物断言 U/S_Mises/RF 与 σ=Mc/I=60MPa 锚"),
    "report_archive": ("real_small", "计算报告与求解产物归档（真实求解产物数值核对）"),
    "project_roundtrip": ("real_small", "工程保存→修改工况→重算闭环（--batch 真实求解）"),
    "assembly_solve": ("real_small", "装配 INP 组装被 ccx 2.22 接受 + ΣRF 平衡（真实求解）"),
    "bonded_connection": ("real_small", "五门之一：粘接连接求解验证（真实 ccx）"),
    "contact_friction_connection": ("real_small", "五门之一：可分离接触与摩擦连接求解验证（真实 ccx 五工况）"),
    "bolt_pin_connection": ("real_small", "五门之一：螺栓与销轴连接求解验证（真实 ccx）"),
    "assembly_results": ("real_small", "五门之一：装配级与单部件结果查看（真实求解产物同源）"),
    "assembly_example": ("real_small", "main.py --batch 装配全链路 + 独立复算（真实 ccx）"),
    "assembly_project_roundtrip": ("real_small", "三个真实独立进程验证装配束/参数修改/连接指南负例"),
    "gui_assembly": ("real_small", "offscreen 表单→主窗口真实求解（_on_solve_static）→部件云图"),
    "connection_draft": ("real_small", "装配连接草稿自动识别 → 真实求解"),
    "tie_diagnostics": ("real_small", "真实部分重叠 tie 的漏绑警告进报告/历史（真实 ccx）"),
    "tie_policy": ("real_small", "真实双面绑定、坐标保持与旧工程迁移（真实 ccx）"),
    "bond_band": ("real_small", "绑定带四组真实 CCX/真实几何"),
    "reaction_semantics": ("real_small", "真实混合载荷、支反力口劲修正与原始产物溯源"),
    "consistent_traction": ("real_small", "解析二次面载荷 + 真实 CCX patch test"),
    # ---------------- reliability（运行可靠性） ----------------
    "scheduler_lifecycle": ("reliability", "进程调度器生命周期：有界日志/子进程取消/内存上限（C# 夹具，非求解器证据）"),
    "solver_gui_lifecycle": ("reliability", "窗口取消/关闭动作触发的真实子进程取消"),
    "background_workflow": ("reliability", "后台工作流 prepare/solve/collect 心跳与取消保护；含真实小算例，检查对象=调度/取消行为"),
    "prepare_retry": ("reliability", "网格高阶优化器崩溃分类 + 进程级重试确定性（脚本自述不启动 gmsh/CCX）"),
    "invalid_mesh_gate": ("reliability", "倒置二次单元必须挡在 deck 之前（守卫/故障路径）"),
    # ---------------- full（整装档，仅 --slow/显式） ----------------
    "assembly_auto_connections": ("full", "整装 20 实体自动连接草稿覆盖（分钟级真实装配覆盖；SLOW 清单，不网格不求解）"),
    "real_assembly_prepare_cancel": ("full", "20 实体跨进程身份与 prepare 取消响应（整装规模；SLOW 清单，不提交 CCX）"),
    # ---------------- 模块51：读取型检查（复用共享冻结产物；显式点名，不入 DEFAULT——无参语义不变） ----------------
    "reuse_results_gui": ("real_small", "读取型检查（GUI类）：复用共享冻结产物出结果仪表对话框；键未命中时真实求解一次并冻结（run_reader 口径）"),
    "reuse_report_check": ("real_small", "读取型检查（报告类）：复用共享冻结产物做报告附录/正文分层核对；键未命中时真实求解一次并冻结"),
    "reuse_archive_roundtrip": ("real_small", "读取型检查（序列化类）：load_archived_case 原生序列化路径读回冻结 case；键未命中时真实求解一次并冻结"),
}


def try_label(name: str) -> str:
    """检查名 → 主标签；未登记的检查名（显式点名拼写错误/已移除项）如实返回 unknown。"""
    return CHECK_LABELS[name][0] if name in CHECK_LABELS else "unknown"


def label_of(name: str) -> str:
    """检查名 → 主标签；未登记的检查名如实抛 KeyError（不得静默兜底）。"""
    return CHECK_LABELS[name][0]


def rationale_of(name: str) -> str:
    return CHECK_LABELS[name][1]


def parse_labels(spec: str) -> list[str]:
    """解析逗号分隔标签串；未知标签抛 ValueError（调用方转 argparse error）。"""
    labels = [part.strip() for part in spec.split(",") if part.strip()]
    if not labels:
        raise ValueError("--labels 为空")
    unknown = [lb for lb in labels if lb not in LABELS]
    if unknown:
        raise ValueError(f"未知标签 {unknown}；可用标签：{', '.join(LABEL_ORDER)}")
    # 去重保序
    seen: list[str] = []
    for lb in labels:
        if lb not in seen:
            seen.append(lb)
    return seen


def select_by_labels(names, labels) -> list[str]:
    """按标签过滤检查清单（保序、不去重——与原清单透传语义一致）；labels 为空返回原清单。

    未在 CHECK_LABELS 登记的名字不属于任何标签：带 --labels 时被滤除（显式点名的
    拼写错误在无 --labels 时仍按原语义透传给执行循环，行为不变）。
    """
    labels = list(labels)
    if not labels:
        return list(names)
    known = {n: lb for n, (lb, _r) in CHECK_LABELS.items()}
    return [n for n in names if known.get(n) in labels]


def breakdown(rows) -> dict:
    """从 summary 行（含 label/seconds）聚合耗时分解：每标签项数与秒数、总计。

    口径：seconds 为 run_regression.py 对每个检查子进程的 wall 计时
    （time.monotonic，含进程启动），与调研 §2 表「每门/秒」同口径。
    """
    by_label: dict[str, dict] = {}
    total = 0.0
    for row in rows:
        label = row["label"]
        bucket = by_label.setdefault(label, {"count": 0, "seconds": 0.0})
        bucket["count"] += 1
        bucket["seconds"] += float(row["seconds"])
        total += float(row["seconds"])
    for bucket in by_label.values():
        bucket["seconds"] = round(bucket["seconds"], 2)
    return {"by_label": by_label, "sum_check_seconds": round(total, 2),
            "checks": len(rows)}


def coverage_report(check_names) -> dict:
    """标签覆盖报告：全集划分断言用（非空、两两不交、并集=全集、无未登记名）。"""
    names = list(check_names)
    unknown = [n for n in names if n not in CHECK_LABELS]
    groups: dict[str, list[str]] = {lb: [] for lb in LABEL_ORDER}
    for n in names:
        groups[label_of(n)].append(n)
    overlap = []
    seen = set()
    duplicates = [n for n in names if n in seen or seen.add(n)]
    return {"unknown": unknown, "groups": groups,
            "duplicates": duplicates,
            "covered": len(names) - len(unknown),
            "total": len(names)}
