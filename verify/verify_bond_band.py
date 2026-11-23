"""绑定带：间隙必须能绑上、超带必须显式拒绝、整张面必须裁到贴合区。

四组真实 CCX / 真实几何：
A 两个贴合面留 0.05 mm 间隙，带宽 0.1 mm → 全部绑上、坐标不被移动、反力平衡；
B 同一几何带宽 0.02 mm → 生成求解文件即显式报错（不静默留一堆未绑定节点）；
C 大基板顶面（整张 CAD 面）只被小块底面覆盖局部，带宽 0.1 mm → 从面裁到贴合区、
  未绑定 0、反力平衡，且求解文件记录裁掉多少面片；
D 同 C 但带宽 0（旧行为：整张面 + 求解器默认搜索半径）→ 仍报未绑定，
  证明裁剪不是把诊断门关掉，而是真的把非贴合面片排除在 *TIE 之外。
"""
from pathlib import Path
import json
import re
import sys
import numpy as np
from assembly_check_utils import ROOT, evidence_dir


def make_contact_model(work, name, gap_mm, partial=False):
    """L 形基体 + 立方块；gap_mm 是两贴合面之间的法向间隙。

    partial=False：插入块底面完全落在基体顶面内（整面贴合）；
    partial=True：独立大基板，插入块只覆盖顶面一小块（整张从面明显大于贴合区）。
    """
    import gmsh
    from rm_sim import gmsh_runtime
    from rm_sim.cad.assembly_import import import_assembly
    source = work / name
    with gmsh_runtime.session():
        gmsh.model.add(name)
        if partial:
            base = gmsh.model.occ.addBox(0, 0, -4, 20, 4, 8)
            gmsh.model.occ.addBox(0, 4 + gap_mm, -2, 6, 6, 4)
        else:
            first = gmsh.model.occ.addBox(0, 0, -4, 10, 4, 8)
            second = gmsh.model.occ.addBox(0, 4, -4, 4, 6, 8)
            gmsh.model.occ.fuse([(3, first)], [(3, second)])
            gmsh.model.occ.addBox(4, 4 + gap_mm, 0, 6, 6, 4)
        gmsh.model.occ.synchronize()
        gmsh.write(str(source))
        gmsh.model.remove()
    return import_assembly(source)


def build_project(model, work, source, label, *, partial=False):
    from rm_sim.core.project import Project, Constraint, TractionLoad, LoadCase
    from rm_sim.core.connections import BondedConnection, ConnectionSide
    from rm_sim.core.face_identity import fingerprint_from_face
    from rm_sim.core.materials import set_part_material
    from rm_sim.cad.assembly_import import sync_parts_from_model

    project = Project(name=label, project_dir=str(work))
    sync_parts_from_model(project, model)
    project.study.geometry_file = str(source)
    for part in project.parts:
        set_part_material(project, part.part_id, 'Q235')
    base, insert = sorted(project.parts, key=lambda p: -p.fingerprint.volume_mm3)

    def faces(part):
        return [f for f in model.faces if f.solid_tag == model.solids[part.solid_index].tag]

    base_faces, insert_faces = faces(base), faces(insert)
    if partial:
        # 从面=基体整张顶面（远大于贴合区），主面=小块底面（局部贴合块）
        slave = next(f for f in base_faces if f.normal[1] > .999 and abs(f.center[1] - 4) < 1e-6)
        master = next(f for f in insert_faces if f.normal[1] < -.999)
        fixed = next(f for f in base_faces if f.normal[1] < -.999 and abs(f.center[1]) < 1e-6)
        side_a, side_b = (base.part_id, slave), (insert.part_id, master)
        load_part = insert.part_id
    else:
        master = next(f for f in base_faces if f.normal[1] > .999 and abs(f.center[1] - 4) < 1e-6)
        slave = next(f for f in insert_faces if f.normal[1] < -.999)
        fixed = next(f for f in base_faces if f.normal[2] < -.999)
        side_a, side_b = (insert.part_id, slave), (base.part_id, master)
        load_part = insert.part_id
    project.connections.append(BondedConnection(name='bonded pair',
        side_a=ConnectionSide(side_a[0], fingerprint_from_face(side_a[1])),
        side_b=ConnectionSide(side_b[0], fingerprint_from_face(side_b[1]))))
    load = next(f for f in insert_faces if f.normal[2] > .999) if not partial else \
        next(f for f in insert_faces if f.normal[1] > .999)
    project.study.load_cases = [LoadCase(name='100 N', constraints=[
        Constraint(name='base fixed', face_tag=fixed.tag, face=fingerprint_from_face(fixed),
                   part_id=base.part_id)],
        loads=[TractionLoad(name='100 N', face_tag=load.tag, face=fingerprint_from_face(load),
                            part_id=load_part, total_force_n=100., direction=(0., 0., -1.))])]
    return project


def solve(work, prepared, tag, timeout=120):
    from rm_sim.solver.tasks import SolveScheduler
    from rm_sim.config import get_ccx_path, load_config
    scheduler = SolveScheduler(get_ccx_path(load_config()), max_workers=1)
    task = scheduler.submit(tag, prepared['deck'], work / 'cases')
    scheduler.wait_task(task.task_id, timeout=timeout)
    assert task.state == 'completed', task.summary()
    return task


def reaction(prepared, task):
    from rm_sim.solver.fields import build_result_fields
    return build_result_fields(sorted(prepared['mesh']['nodes']), task.results, task.frd_path,
                               fixed_set=prepared['fixed_sets'])


def synthetic_side(square_size=2.0, z=0.0):
    """两块共面三角形组成的方形面（纯几何，用于钉死节点级裁剪语义）。"""
    from types import SimpleNamespace
    side_nodes = ((1, 2, 3), (1, 3, 4))
    points = {1: (0.0, 0.0, z), 2: (square_size, 0.0, z),
              3: (square_size, square_size, z), 4: (0.0, square_size, z)}
    facets = ((0, 0), (1, 0))
    triangles = np.asarray([[points[1], points[2], points[3]],
                            [points[1], points[3], points[4]]], dtype=float)
    return SimpleNamespace(facets=facets,
                           facet_nodes={key: nodes for key, nodes in zip(facets, side_nodes)},
                           points=points, triangles=triangles)


def main():
    from rm_sim.solver.assembly_workflow import prepare_analysis
    from rm_sim.solver.diagnostics import collect_tie_diagnostics
    from rm_sim.solver.deck import DeckError
    from rm_sim.solver.bond_band import select_tie_nodes
    sys.path.insert(0, str(ROOT / 'probes'))
    from analyze_tie_node_movement import read_frd_coordinates

    work = evidence_dir('bond_band')
    record = {}
    models = []
    try:
        # ---- E：贴合区小于一个网格面片 → 节点级只绑能投影的那个节点（纯几何） ----
        slave = synthetic_side(2.0)
        master = synthetic_side(0.5, z=0.02)  # 只有 (0,0) 角落在主面内
        selection = select_tie_nodes(slave, master, 0.1)
        assert selection['slave_nodes'] == (1,), selection
        assert selection['slave_total'] == 4 and selection['outside'] == 3, selection
        record['partial_coverage'] = {'slave_nodes': list(selection['slave_nodes']),
                                      'slave_total': selection['slave_total'],
                                      'outside': selection['outside']}
        # ---- F：完全不重叠 → 没有任何可绑节点（deck 会显式拒绝，不静默） ------
        far = synthetic_side(2.0, z=5.0)
        empty = select_tie_nodes(slave, far, 0.1)
        assert not empty['slave_nodes'] and empty['outside'] == empty['slave_total'], empty
        record['no_overlap'] = {'tieable': empty['tieable'], 'slave_total': empty['slave_total']}

        # ---- A：0.05 mm 间隙，带宽 0.1 mm -------------------------------------
        model = make_contact_model(work, 'gapped.step', 0.05)
        models.append(model)
        project = build_project(model, work, work / 'gapped.step', 'gapped bond')
        prepared = prepare_analysis(project, model, work / 'gapped_mesh.inp', size=1.5, bond_band_mm=0.1)
        # 0.05 mm 间隙 → 位置容差按 bond_band 的实测上下界给（当前 0.07）。
        assert 'POSITION TOLERANCE=0.07' in prepared['deck'], prepared['deck'][-2000:]
        assert 'TYPE=NODE' in prepared['deck'], prepared['deck'][-2000:]
        task = solve(work, prepared, 'gapped')
        diag = collect_tie_diagnostics(task.case_dir, task.job_name, mesh=prepared['mesh'])
        assert diag['untied_nodes'] == 0, diag
        assert not diag['requires_review'], diag
        coords, rounding = read_frd_coordinates(task.frd_path)
        for node, original in prepared['mesh']['nodes'].items():
            assert np.all(np.abs(np.asarray(coords[node]) - original) <= np.asarray(rounding[node]) + 1e-11), node
        fields = reaction(prepared, task)
        assert np.linalg.norm(fields.rf_sum - np.array([0., 0., 100.])) < 1., fields.rf_sum
        u = np.asarray(fields.u)
        record['gapped'] = {'untied': diag['untied_nodes'], 'reaction': fields.rf_sum.tolist(),
                            'max_displacement_mm': float(np.max(np.abs(u))),
                            'coordinates_preserved': True, 'case': task.case_dir.name}
        assert np.max(np.abs(u)) < 1.0, '绑定未传力：位移异常'

        # ---- B：带宽小于间隙 → 必须显式拒绝 ---------------------------------
        try:
            prepare_analysis(project, model, work / 'too_tight_mesh.inp', size=1.5, bond_band_mm=0.02)
        except (DeckError, ValueError, OSError) as exc:
            record['too_tight_refused'] = str(exc)[:300]
        else:
            raise AssertionError('带宽小于间隙时仍生成了求解文件')

        # ---- C：整张从面只局部贴合，带宽 0.1 mm → 只绑贴合区节点、0 警告 ------
        model2 = make_contact_model(work, 'partial.step', 0.0, partial=True)
        models.append(model2)
        project2 = build_project(model2, work, work / 'partial.step', 'partial contact bond', partial=True)
        prepared2 = prepare_analysis(project2, model2, work / 'partial_mesh.inp', size=1.5, bond_band_mm=0.1)
        deck2 = prepared2['deck']
        # 共面贴合（间距 0）：从面仍是节点集，容差按实测上下界给 0.07。
        tie_line = next(line for line in deck2.splitlines() if line.startswith('*TIE,'))
        assert 'POSITION TOLERANCE=0.07' in tie_line, tie_line
        assert '*SURFACE, NAME=C1_SAT, TYPE=NODE' in deck2, deck2[-2000:]
        band_lines = [line for line in deck2.splitlines() if line.startswith('** 绑定带')]
        assert band_lines, deck2[-2000:]
        covered = re.search(r'从面 A (\d+) 节点里 (\d+) 个投影进主面', band_lines[0])
        assert covered, band_lines
        total_a, tied_a = int(covered.group(1)), int(covered.group(2))
        assert 0 < tied_a < total_a, f'整张从面没有被裁剪：{tied_a}/{total_a}'
        task2 = solve(work, prepared2, 'partial')
        diag2 = collect_tie_diagnostics(task2.case_dir, task2.job_name, mesh=prepared2['mesh'])
        assert diag2['untied_nodes'] == 0, diag2
        assert not diag2['requires_review'], diag2
        fields2 = reaction(prepared2, task2)
        assert np.linalg.norm(fields2.rf_sum - np.array([0., 0., 100.])) < 1., fields2.rf_sum
        record['partial_overlap'] = {'untied': diag2['untied_nodes'], 'reaction': fields2.rf_sum.tolist(),
                                     'tied_slave_nodes': tied_a, 'total_slave_nodes': total_a,
                                     'case': task2.case_dir.name}
        record['band_lines'] = band_lines

        # ---- D：同一几何不裁（旧行为）→ 仍报未绑定 ---------------------------
        prepared3 = prepare_analysis(project2, model2, work / 'legacy_mesh.inp', size=1.5, bond_band_mm=0)
        assert 'POSITION TOLERANCE' not in prepared3['deck']
        assert '** 绑定带' not in prepared3['deck']
        task3 = solve(work, prepared3, 'legacy')
        diag3 = collect_tie_diagnostics(task3.case_dir, task3.job_name, mesh=prepared3['mesh'])
        assert diag3['untied_nodes'] > 0 and diag3['requires_review'], diag3
        assert diag3['untied_nodes'] < diag3['reported_missed_nodes'] + 1, diag3
        record['legacy_no_band'] = {'untied': diag3['untied_nodes'],
                                    'reported_missed': diag3['reported_missed_nodes'],
                                    'case': task3.case_dir.name}

        (work / 'verification.json').write_text(json.dumps(record, ensure_ascii=False, indent=2),
                                                encoding='utf-8')
        print('VERIFY_BOND_BAND: PASS', work, flush=True)
    finally:
        for model in models:
            model.close()


if __name__ == '__main__':
    main()
