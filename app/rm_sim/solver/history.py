"""Restore archived static results from their own INP/DAT/FRD/VTU, read-only."""
from __future__ import annotations

import json
import hashlib
from pathlib import Path
import re
import numpy as np

from .deck import parse_mesh_inp
from .results import load_results
from .fields import build_result_fields, part_field_views


def load_archived_case(project, case_name):
    if not re.fullmatch(r"case_\d+", case_name):
        raise ValueError("Invalid case name")
    record = next((r for r in project.cases if r.name == case_name), None)
    if record is None:
        raise ValueError("Case is not indexed in this project: " + case_name)
    if not re.fullmatch(r"[A-Za-z0-9_-]+", record.job_name):
        raise ValueError("Invalid job name")
    root = (Path(project.project_dir) / 'cases').resolve()
    directory = (root / case_name).resolve()
    if directory.parent != root:
        raise ValueError("Case path escapes project")
    ctx = json.loads((directory/'results.json').read_text(encoding='utf-8'))
    if ctx.get('state') != 'completed' or not ctx.get('job_finished'):
        raise ValueError("Archived solve is not completed")
    job = directory / record.job_name
    text = job.with_suffix('.inp').read_text(encoding='utf-8')
    mesh = parse_mesh_inp(text)
    from .diagnostics import collect_tie_diagnostics
    # Recompute in memory for legacy archives too; never rewrite their reports.
    ctx['solver_diagnostics'] = collect_tie_diagnostics(directory,record.job_name,mesh=mesh)
    results = load_results(job.with_suffix('.dat'),job.with_suffix('.frd'))
    fixed = []
    boundary = False
    for raw in text.splitlines():
        line=raw.strip()
        if not line or line.startswith('**'):continue
        if line.startswith('*'):
            boundary=line.upper().split(',')[0]=='*BOUNDARY'
        elif boundary:
            name=line.split(',')[0].strip().upper()
            if name in mesh['nsets'] and name not in fixed:
                fixed.append(name)
    if not fixed:
        raise ValueError("No archived external constraint sets found")
    increments = sorted((p for p in directory.glob(record.job_name+'.*.vtu')
                         if p.stem.rsplit('.',1)[-1].isdigit()),key=lambda p:int(p.stem.rsplit('.',1)[-1]))
    vtu = increments[-1] if increments else job.with_suffix('.vtu')
    if not vtu.is_file():
        raise ValueError("Archived VTU missing; cannot restore cloud fields")
    nodes = sorted({n for conn in mesh['elems'].values() for n in conn})
    fields=build_result_fields(nodes,results,job.with_suffix('.frd'),fixed_set=fixed,vtu_path=vtu,
                               inp_text=text,mesh=mesh)
    frd_u=np.asarray([results.frd_disp[n] for n in nodes])
    if not np.allclose(fields.u,frd_u,rtol=2e-5,atol=1e-8):
        raise ValueError("VTU and FRD displacements disagree; archived fields cannot be used")
    summary=ctx['results']
    stored_reaction = ctx.get('reaction_processing',{})
    if stored_reaction.get('inp_sha256'):
        with job.with_suffix('.inp').open('rb') as stream:
            if hashlib.file_digest(stream,'sha256').hexdigest()!=stored_reaction['inp_sha256']:
                raise ValueError('Archived input hash disagrees with reaction provenance')
    # Older summaries stored RF (external force), not corrected support reactions.
    # Verify their original quantity first, then publish a corrected in-memory view.
    comparison_rf = (fields.rf_sum if stored_reaction.get('version') else
                     np.sum(list(fields.raw_rf_table.values()),axis=0))
    if (not np.isclose(fields.max_u_mag(),summary['max_u_mag_mm'],rtol=1e-5,atol=1e-9)
            or not np.isclose(float(fields.s_mises.max()),summary['max_mises_mpa'],rtol=1e-5,atol=1e-8)
            or not np.allclose(comparison_rf,summary['rf_sum_n'],rtol=1e-5,atol=1e-7)):
        raise ValueError("Archived summary disagrees with raw fields")
    ctx['archived_rf_sum_n'] = list(summary['rf_sum_n'])
    ctx['results'] = {**summary,'rf_sum_n':fields.rf_sum.tolist()}
    ctx['reaction_processing'] = fields.source['reaction']
    ctx['history_reaction_recalculated'] = not bool(stored_reaction.get('version'))
    fields.source['reaction']['historical_recalculation'] = ctx['history_reaction_recalculated']
    reaction = fields.source['reaction']
    if reaction.get('supported'):
        applied = np.asarray(reaction['applied_total_n'])
        ctx['equilibrium'] = {'applied_desc':'从本历史工况INP重算一致节点载荷',
            'applied_n':applied.tolist(),'rf_sum_n':fields.rf_sum.tolist(),
            'residual':float(np.linalg.norm(applied+fields.rf_sum)), 'reaction_corrected':True}
    else:ctx.pop('equilibrium',None)
    sets={name:set(ids) for name,ids in mesh['elsets'].items() if re.fullmatch(r'PART\d+',name)}
    return {'mesh':mesh,'fields':fields,'views':part_field_views(fields,sets,mesh['elems']),
            'context':ctx,'archive':{'case_dir':directory,'results_json':directory/'results.json',
                                   'report_md':directory/'report.md'},'case_name':case_name}
