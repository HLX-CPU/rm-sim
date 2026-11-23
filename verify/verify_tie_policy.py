"""Real two-face bond, coordinate preservation, old-project migration and draft indexing."""
from pathlib import Path
import json
import re
import sys
import numpy as np
from assembly_check_utils import ROOT, evidence_dir


def main():
    import gmsh
    from PySide6.QtWidgets import QApplication
    from rm_sim import gmsh_runtime
    from rm_sim.cad.assembly_import import import_assembly, sync_parts_from_model
    from rm_sim.core.project import Project, Constraint, TractionLoad, LoadCase
    from rm_sim.core.connections import (BondedConnection, PinConnection, ConnectionSide,
                                          consistent_draft_directions)
    from rm_sim.core.face_identity import fingerprint_from_face
    from rm_sim.core.store import to_dict, from_dict
    from rm_sim.core.materials import set_part_material
    from rm_sim.solver.assembly_workflow import prepare_analysis
    from rm_sim.solver.tasks import SolveScheduler
    from rm_sim.solver.diagnostics import collect_tie_diagnostics
    from rm_sim.config import get_ccx_path, load_config
    from rm_sim.gui.connection_draft_dialog import AssemblyConnectionDraftDialog
    app = QApplication.instance() or QApplication([])
    sys.path.insert(0, str(ROOT / 'probes'))
    from analyze_tie_node_movement import read_frd_coordinates
    work = evidence_dir('tie_policy')
    source = work / 'two_face_bond.step'
    with gmsh_runtime.session():
        gmsh.model.add('L_base_and_insert')
        # Keep supports away from TIE endpoints: CCX's RF output excludes MPC
        # contributions at shared SPC/master nodes and is not a balance oracle there.
        first = gmsh.model.occ.addBox(0, 0, -4, 10, 4, 8)
        second = gmsh.model.occ.addBox(0, 4, -4, 4, 6, 8)
        gmsh.model.occ.fuse([(3, first)], [(3, second)])
        gmsh.model.occ.addBox(4, 4, 0, 6, 6, 4)
        gmsh.model.occ.synchronize()
        gmsh.write(str(source)); gmsh.model.remove()
    model = import_assembly(source)
    dialog = None
    try:
        project = Project(name='Two orthogonal bonded patches', project_dir=str(work))
        sync_parts_from_model(project, model)
        project.study.geometry_file = str(source)
        for part in project.parts:
            set_part_material(project, part.part_id, 'Q235')
        base, insert = sorted(project.parts, key=lambda p: -p.fingerprint.volume_mm3)
        def faces(part):
            return [f for f in model.faces if f.solid_tag == model.solids[part.solid_index].tag]
        base_faces, insert_faces = faces(base), faces(insert)
        for axis in (0, 1):
            slave = next(f for f in insert_faces if f.normal[axis] < -.999)
            master = next(f for f in base_faces if f.normal[axis] > .999 and abs(f.center[axis] - 4) < 1e-6)
            project.connections.append(BondedConnection(name=f'patch_{axis}',
                side_a=ConnectionSide(insert.part_id, fingerprint_from_face(slave)),
                side_b=ConnectionSide(base.part_id, fingerprint_from_face(master))))
        for cls in (BondedConnection, PinConnection):
            conn = cls(name='policy', side_a=project.connections[0].side_a, side_b=project.connections[0].side_b)
            assert conn.adjust_nodes is False
            for value in (False, True):
                conn.adjust_nodes = value
                assert from_dict(to_dict(conn)) == conn
            legacy = to_dict(conn); del legacy['adjust_nodes']
            assert from_dict(legacy).adjust_nodes is True
            conn.adjust_nodes = 'NO'
            try: conn.validate()
            except ValueError: pass
            else: raise AssertionError('Non-boolean adjustment accepted')
        fixed = next(f for f in base_faces if f.normal[2] < -.999)
        load = next(f for f in insert_faces if f.normal[2] > .999)
        project.study.load_cases = [LoadCase(name='100 N compression',
            constraints=[Constraint(name='base', face_tag=fixed.tag, face=fingerprint_from_face(fixed), part_id=base.part_id)],
            loads=[TractionLoad(name='100 N', face_tag=load.tag, face=fingerprint_from_face(load), part_id=insert.part_id,
                                total_force_n=100., direction=(0., 0., -1.))])]
        prepared = prepare_analysis(project, model, work / 'mesh.inp', size=1.5)
        assert len(re.findall(r'^\*TIE,', prepared['deck'], re.MULTILINE)) == 1
        assert '*TIE, NAME=C1_TIE, ADJUST=NO' in prepared['deck']
        assert '** TIE group connections: 1, 2' in prepared['deck']
        scheduler = SolveScheduler(get_ccx_path(load_config()), max_workers=1)
        task = scheduler.submit('two_face', prepared['deck'], work / 'cases')
        scheduler.wait_task(task.task_id, timeout=60)
        assert task.state == 'completed', task.summary()
        diag = collect_tie_diagnostics(task.case_dir, task.job_name)
        assert not diag['requires_review'], diag
        coords, rounding = read_frd_coordinates(task.frd_path)
        mesh = prepared['mesh']
        for node, original in mesh['nodes'].items():
            assert np.all(np.abs(np.asarray(coords[node]) - original) <= np.asarray(rounding[node]) + 1e-11)
        from rm_sim.solver.fields import build_result_fields
        fields = build_result_fields(sorted(mesh['nodes']), task.results, task.frd_path, fixed_set=prepared['fixed_sets'])
        assert np.linalg.norm(fields.rf_sum - np.array([0., 0., 100.])) < 1., fields.rf_sum
        # Largest patch direction is retained for every patch in an old cached draft.
        rows = [dict(part_a='p1', part_b='p2', common_area_mm2=1., slave_side='a', recommended='bonded'),
                dict(part_a='p1', part_b='p2', common_area_mm2=10., slave_side='b', recommended='bonded')]
        normalized = consistent_draft_directions({'candidates': rows})
        assert all(r['slave_side'] == 'b' for r in normalized['candidates'])
        assert rows[0]['slave_side'] == 'a', 'Normalization mutated input'
        dialog = AssemblyConnectionDraftDialog({'candidates': rows})
        dialog._checks[1].setChecked(False)
        dialog._combos[0].setCurrentIndex(1)  # Contact on largest patch, original index 1.
        dialog._on_apply()
        assert dialog.accepted == [1] and dialog.kinds[1] == 'contact'
        dialog.close(); app.processEvents()
        record = {'task': task.summary(), 'diagnostics': diag, 'reaction_n': fields.rf_sum.tolist(),
                  'frd_coordinates_preserved_to_output_precision': True, 'legacy_adjust_yes_preserved': True,
                  'draft_sort_selection_original_index': dialog.accepted}
        (work / 'verification.json').write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
    finally:
        # Qt objects must die while their application is alive, rather than in
        # Python's unspecified interpreter-shutdown order with OCC/VTK loaded.
        from PySide6.QtCore import QCoreApplication, QEvent
        if dialog is not None:
            dialog.close()
            dialog.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()
        model.close()
        print('TIE_POLICY_MODEL_CLOSED', flush=True)
        app.shutdown()
        print('TIE_POLICY_QT_SHUTDOWN', flush=True)
    print('VERIFY_TIE_POLICY: PASS', work, flush=True)


if __name__ == '__main__': main()
