# -*- coding: utf-8 -*-
"""Markdown 计算报告生成（GUI 无关层，report 层，纯 dict 输入）。

ctx 契约（由 solver.archive.build_archive_context 组装，键与结构见该函数）：
- 正文数值**打印位数固定**（本模块 BODY_FORMATS），验收按
  |正文值 − results.json| ≤ 0.5×10^(−打印位数) 分层核对——避免
  「0.190532 vs 0.1905315 差 5e-7」这类按全精度门误爆的情形；
- 附录 A 的 ```json 代码块与 results.json 文件**同源同文**（全精度），
  1e-9 门打在附录数值上。
"""
from __future__ import annotations

import json
from typing import Any

# 正文打印位数（键 → 小数位数）；缺失键不打印
BODY_FORMATS = {
    "tip_uy_mm": 6,
    "max_u_mag_mm": 6,
    "max_mises_mpa": 4,
    "min_sicn": 4,
}


def build_results_json(ctx: dict[str, Any]) -> str:
    """全精度 results.json 文本（正文附录与落盘文件同源同文）。"""
    return json.dumps(ctx, ensure_ascii=False, indent=2)


def _fmt(key: str, ctx: dict[str, Any]) -> str:
    places = BODY_FORMATS[key]
    for section in ("results", "mesh"):
        if key in ctx.get(section, {}):
            return f"{float(ctx[section][key]):.{places}f}"
    raise KeyError(f"BODY_FORMATS 键 {key!r} 在 ctx.results/ctx.mesh 中均不存在")


def build_report_md(ctx: dict[str, Any]) -> str:
    """由上下文生成 Markdown 计算报告。"""
    mat = ctx["material"]
    mesh = ctx["mesh"]
    res = ctx["results"]
    y = ctx["yield_check"]
    lc = ctx["load_case"]
    lines: list[str] = []
    lines.append(f"# 计算报告：{ctx['job_name']}")
    lines.append("")
    lines.append(f"- 生成时间: {ctx['created']}")
    lines.append(
        f"- 求解状态: {ctx['state']}"
        f"（判据 = 日志无 ERROR、含 'Job finished' 且结果解析成功；退出码 {ctx['exit_code']} 不作判据）"
    )
    lines.append("")
    diagnostics = ctx.get('solver_diagnostics',{})
    if diagnostics.get('requires_review'):
        lines += ['## 连接警告：需要检查', '',
                  '- 计算结束不代表连接检查通过；当前结果不能据此判定连接或结构合格。',
                  '- 请检查主从面、位置和覆盖，原始节点集合保留在本工况目录。']
        if diagnostics.get('reported_missed_nodes'):
            lines.append(f"- 求解器报告 {diagnostics['reported_missed_nodes']} 个不同编号的从面节点未绑定。")
        # SPC 自由度已固定的诊断编号单列（模块47）：求解器正常行为，不与未绑定混算。
        dof_numbers = diagnostics.get('dof_inactive_node_numbers') or []
        if dof_numbers:
            lines.append(f"- 另有 {diagnostics.get('dof_inactive_nodes', len(dof_numbers))} 个节点因 SPC 自由度已被固定约束"
                         "而跳过绑定（求解器正常行为，单列不与未绑定混算）。")
            lines.append(f"- SPC 自由度已固定诊断编号（{len(dof_numbers)} 个）：{dof_numbers}")
        for warning in diagnostics.get('warning_files',[]):
            lines.append(f"- {warning['file']}：{warning['reported_nodes']} 个节点；SHA256 {warning['sha256']}")
        for error in diagnostics.get('read_errors',[]):
            lines.append(f"- 无法完整检查 {error['file']}：{error['error']}")
        solver_errors = diagnostics.get('solver_errors', {})
        if solver_errors.get('count'):
            lines.append(f"- 求解日志含 {solver_errors['count']} 条 ERROR；即使出现结束标记也不接受本结果。")
            for sample in solver_errors.get('samples', []):
                lines.append(f"- {solver_errors['file']} 第 {sample['line']} 行：{sample['message']}")
        lines += ['',diagnostics['interpretation'],'']
    # 连接复核决策状态（模块47）：needs_review 的连接逐条呈现决策（未决策会
    # 被求解入口拦截；已决策的连接其求解器警告可能仍在（保留粘接），呈现为已复核）。
    review_rows = [c for c in (ctx.get('assembly', {}) or {}).get('connections', [])
                   if c.get('needs_review')]
    if review_rows:
        lines += ['## 连接复核决策', '',
                  '- needs_review = 上次求解该连接从面出现未绑定节点；决策枚举：'
                  '保留粘接 / 改为可分离接触 / 删除连接；未决策的连接会被求解入口拦截。']
        from ..core.connections import REVIEW_DECISION_LABELS
        for row in review_rows:
            decision = row.get('review_decision', 'pending')
            label = REVIEW_DECISION_LABELS.get(decision, decision)
            stamp = row.get('review_decided_at') or ''
            suffix = f"（{stamp}）" if stamp else ""
            lines.append(f"- 「{row.get('name')}」：{label}{suffix}；"
                         f"关联节点数：从面 {row.get('review_evidence', {}).get('last_solve', {}).get('slave_surface_nodes', '—')}"
                         f" / 未绑定 {row.get('review_evidence', {}).get('last_solve', {}).get('reported_missed_nodes', '—')}。")
        lines.append("")
    lines.append("## 1. 模型")
    lines.append("")
    lines.append(f"- 几何文件: {ctx.get('geometry_file', '（未记录）')}")
    lines.append(
        f"- 单元: {mesh['element_count']} × {mesh['element_type']}"
        f"（{mesh['order']} 阶四面体，MeshSize {mesh['mesh_size_mm']:g} mm）"
    )
    lines.append(f"- 节点: {mesh['node_count']}")
    resources = ctx.get('resource_usage',{})
    if resources.get('peak_job_memory_bytes') is not None:
        lines.append(f"- 求解进程树峰值提交内存: {resources['peak_job_memory_bytes']/1024**2:.2f} MiB（不是工作集）")
        if resources.get('memory_limit_bytes') is not None:
            lines.append(f"- 本次进程树内存预算: {resources['memory_limit_bytes']/1024**2:.2f} MiB")
    provenance = ctx.get("model_provenance", {})
    if provenance:
        lines += ["", "### 来源与分析假设", ""]
        for key, value in provenance.items():
            if key == "assumptions":
                lines.extend(f"- {item}" for item in value)
            elif key != "body_mapping":
                lines.append(f"- {key}: {value}")
    lines.append("")
    lines.append("## 2. 材料")
    lines.append("")
    lines.append(f"- 名称: **{mat['name']}**")
    lines.append(
        f"- 弹性模量 E = {mat['e_modulus_mpa']:.6g} MPa；"
        f"泊松比 ν = {mat['poisson']:g}；密度 ρ = {mat['density_t_mm3']:g} t/mm³"
    )
    yv = mat.get("yield_strength_mpa")
    lines.append(
        "- 屈服强度: " + ("（未提供）" if yv is None else f"{yv:.6g} MPa")
    )
    lines.append("")
    lines.append("## 3. 网格统计（含质量指标）")
    quality=ctx.get('mesh_quality',{})
    if quality:
        p=quality['sicn_percentiles']
        lines += ['',f"- SICN分布：最小 {p['0']:.6g}；P1 {p['1']:.6g}；P5 {p['5']:.6g}；中位 {p['50']:.6g}。",
                  f"- 非正Jacobian单元：{quality['nonpositive_jacobian_count']}；质量提示不替代应力收敛验收。"]
        for c in quality['recipe'].get('local_controls',[]):
            lines.append(f"- 局部网格：{c['name']}；{c['kind']}；部件 {c['part_id']}；目标 {c['size_mm']:g} mm；过渡 {c['transition_mm']:g} mm。")
        lines += ['', '| 最差单元（最多列20个） | 部件 | SICN | 定位坐标 mm |', '|---|---|---|---|']
        for e in quality['worst_elements'][:20]:
            lines.append(f"| {e['element']} | {e['part']} | {e['sicn']:.6g} | {e['center_mm']} |")
        lines += ['', quality['center_note']+'。完整质量记录见附录。','']
    if "assembly" in ctx:
        assembly = ctx["assembly"]
        lines += ["", "### 部件材料与连接假设", "", "| 部件 | 材料 | E [MPa] | 最大应力 [MPa] | 屈服强度 [MPa] |", "|---|---|---|---|---|"]
        for p in assembly["parts"]:
            m = p["material"]
            lines.append(f"| {p['part_id']} {p['name']} | {m['name']} | {m['e_modulus_mpa']:g} | {p['max_mises']:.6g} | {m.get('yield_strength_mpa')} |")
        lines += ["", assembly["assumptions"]]
        for c in assembly["connections"]:
            lines.append(f"- {c['name']}: {c['type']}，{c['side_a']['part_id']} ↔ {c['side_b']['part_id']}；μ={c.get('friction_mu', '不适用')}")
        band = assembly.get("bond_band_mm")
        if band is not None:
            if band > 0:
                lines.append(
                    f"- 绑定带 {band:g} mm：从面按节点级裁剪，只把「投影落在主面内、且法向距离"
                    f"≤带宽」的节点写进 *TIE（TYPE=NODE）；位置容差 0.07 mm（实测上下界），"
                    "ADJUST=NO 不移动节点。"
                    "逐连接的贴合覆盖（可绑节点/整张面节点、贴合区外节点的最近距离）见工况 INP"
                    " 的「** 绑定带」注释；贴合区外的节点不参与绑定，因此不会产生未绑定警告，"
                    "但也不能据此认为整张 CAD 面都贴合。"
                )
            else:
                lines.append(
                    "- 绑定带已关闭（band=0，旧行为）：整张 CAD 面参与 *TIE、不写 POSITION TOLERANCE；"
                    "大面中远离贴合区的从面节点会被求解器报为未绑定。"
                )
        for v in assembly["interface_pressure"]:
            lines.append(f"- 连接 {v['connection']} / {v.get('side', 'SA')}：合力 {v.get('force_n')} N；法向压应力积分 {v['normal_force_n']:.6g} N；最小压应力 {v['min_pressure_mpa']:.6g} MPa。{v['method']}")
    lines.append("")
    lines.append("| 项 | 值 |")
    lines.append("|---|---|")
    lines.append(f"| 单元数 | {mesh['element_count']} |")
    lines.append(f"| 节点数 | {mesh['node_count']} |")
    lines.append(f"| 单元类型 | {mesh['element_type']} |")
    lines.append(f"| MeshSize [mm] | {mesh['mesh_size_mm']:g} |")
    lines.append(f"| min SICN | {_fmt('min_sicn', ctx)} |")
    lines.append(f"| mean SICN | {mesh['mean_sicn']:.4f} |")
    lines.append(f"| 负 Jacobian 单元数 | {mesh['negative_jacobian_count']} |")
    lines.append("")
    lines.append("## 4. 工况")
    lines.append("")
    lines.append(f"- 工况名: {lc['name']}")
    lines.append("- 约束:")
    for s in lc["constraints"]:
        lines.append(f"  - {s}")
    lines.append("- 载荷:")
    for s in lc["loads"]:
        lines.append(f"  - {s}")
    lines.append(
        "- 固定约束为**理想固定**简化假设（面节点三向平动为零，"
        "无转动自由度与施加柔性），结果偏刚，须随结果一并评估。"
    )
    lines.append("")
    lines.append("## 5. 结果（正文为人类可读位数；机器精度见附录 A）")
    lines.append("")
    lines.append("| 量 | 值 |")
    lines.append("|---|---|")
    lines.append(f"| 端部竖向位移 Uy [mm] | {_fmt('tip_uy_mm', ctx)} |")
    lines.append(f"| 最大合位移 \\\\|U\\\\| [mm] | {_fmt('max_u_mag_mm', ctx)} |")
    lines.append(f"| 最大 von Mises [MPa] | {_fmt('max_mises_mpa', ctx)} |")
    rf = res["rf_sum_n"]
    reaction = ctx.get('reaction_processing',{})
    label = '支反力' if reaction.get('supported') else '原始RF（节点外力）'
    lines.append(f"| {label} ΣFx [N] | {rf[0]:.6f} |")
    lines.append(f"| {label} ΣFy [N] | {rf[1]:.6f} |")
    lines.append(f"| {label} ΣFz [N] | {rf[2]:.6f} |")
    lines.append("")
    lines += ['### 反力口径','']
    if reaction.get('supported'):
        lines += ['- 支反力 = DAT原始RF − 固定节点上的已施加载荷。',
                  f"- 原始RF合力 [N]：{reaction['raw_rf_sum_n']}",
                  f"- 固定节点已施加载荷合力 [N]：{reaction['applied_at_fixed_n']}",
                  f"- 载荷重建：{reaction['integration']}",'']
    else:
        lines += ['- '+reaction.get('reason','缺少已验证的载荷重建口径；当前按原始节点外力显示。'),'']
    if "equilibrium" in ctx:
        eq = ctx["equilibrium"]
        ap = eq["applied_n"]
        lines.append("### 反力平衡")
        lines.append("")
        lines.append(
            f"- 施加: {eq['applied_desc']}；合力 ΣF_applied = "
            f"({ap[0]:.6f}, {ap[1]:.6f}, {ap[2]:.6f}) N"
        )
        lines.append(
            f"- 支反力: ΣF = ({eq['rf_sum_n'][0]:.6f}, {eq['rf_sum_n'][1]:.6f}, "
            f"{eq['rf_sum_n'][2]:.6f}) N"
        )
        lines.append(
            f"- 平衡残差 |ΣRF + ΣF_applied| = {eq['residual']:.3e} N"
            "（残差受离散积分、约束插值与求解精度影响；全精度见附录 A）"
        )
        lines.append("")
    lines.append("### 超屈服判定")
    lines.append("")
    if y["yield_mpa"] is None:
        lines.append("- 材料未提供屈服强度，无法判定。")
    else:
        verdict = "已超屈服" if y["exceeded"] else "未超屈服"
        margin = "未定义（零应力）" if y["margin"] is None else f"{y['margin']:.2f}×"
        lines.append(
            f"- max von Mises = {y['max_mises_mpa']:.4f} MPa vs 屈服 {y['yield_mpa']:.4f} MPa"
            f" → **{verdict}**（安全裕度 {margin}）"
        )
        lines.append(
            "- 注：本分析为线弹性；若超屈服，本结果仅提示强度不足，"
            "弹塑性评估属后续模块。"
        )
    lines.append("")
    png = ctx.get("png", {})
    if png:
        lines.append("## 6. 云图")
        lines.append("")
        if "mises" in png:
            lines.append("![von Mises 云图](mises.png)")
        if "displaced" in png:
            lines.append("![位移云图](displaced.png)")
        lines.append("")
    lines.append("## 附录 A：机器精度数据（与 results.json 同源同文，1e-9 门）")
    lines.append("")
    lines.append("```json")
    lines.append(build_results_json(ctx))
    lines.append("```")
    lines.append("")
    return "\n".join(lines)
