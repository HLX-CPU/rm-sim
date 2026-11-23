"""A real partial-overlap tie must surface its missed-node warnings in reports/history."""
from pathlib import Path
import hashlib
import json
import numpy as np
from assembly_check_utils import ROOT, evidence_dir, hashes


def main():
    import gmsh
    from rm_sim import gmsh_runtime
    from rm_sim.cad.assembly_import import import_assembly, sync_parts_from_model
    from rm_sim.core.project import Project, Constraint, LoadCase, TractionLoad
    from rm_sim.core.connections import BondedConnection, ConnectionSide
    from rm_sim.core.face_identity import fingerprint_from_face
    from rm_sim.core.materials import set_part_material
    from rm_sim.solver.assembly_workflow import prepare_analysis, finish_analysis
    from rm_sim.solver.diagnostics import collect_tie_diagnostics
    from rm_sim.solver.tasks import SolveScheduler
    from rm_sim.solver.history import load_archived_case
    from rm_sim.config import get_ccx_path,load_config
    work = evidence_dir('tie_diagnostics')
    source=work/'partial_overlap.step'
    with gmsh_runtime.session():
        gmsh.model.add('partial_overlap')
        gmsh.model.occ.addBox(0,0,0,10,10,10)
        gmsh.model.occ.addBox(5,10,0,10,10,10)
        gmsh.model.occ.synchronize();gmsh.write(str(source))
        gmsh.model.remove()
    model=import_assembly(source)
    try:
        project=Project(name='Partial overlap diagnostic fixture',project_dir=str(work))
        sync_parts_from_model(project,model)
        project.study.geometry_file=str(source)
        project.study.mesh_size_mm=2.5
        for part in project.parts: set_part_material(project,part.part_id,'Q235')
        low,high=sorted(project.parts,key=lambda p:p.fingerprint.centroid[1])
        def face(part,sign):
            return next(f for f in model.faces if f.solid_tag==part.solid_tag and sign*f.normal[1]>.999)
        a,b=face(low,1),face(high,-1)
        fixed,load=face(low,-1),face(high,1)
        project.connections=[BondedConnection(name='Deliberately partial overlap',
            side_a=ConnectionSide(low.part_id,fingerprint_from_face(a)),
            side_b=ConnectionSide(high.part_id,fingerprint_from_face(b)))]
        project.study.load_cases=[LoadCase(name='100N diagnostic fixture',
            constraints=[Constraint(name='fixed',face_tag=fixed.tag,face=fingerprint_from_face(fixed),part_id=low.part_id)],
            loads=[TractionLoad(name='100N',face_tag=load.tag,face=fingerprint_from_face(load),part_id=high.part_id,
                                total_force_n=100.,direction=(0.,-1.,0.))])]
        # 本夹具要检验的是「求解器真的报了未绑定节点时，诊断/报告/历史是否
        # 如实呈现」，所以显式关闭绑定带（band=0 = 旧行为：整张 CAD 面参与
        # *TIE、不写 POSITION TOLERANCE）。产品默认的绑定带会把这张大从面裁到
        # 贴合区、因而不产生警告——那条路径由 verify_bond_band 的 C/D 组把守。
        prepared=prepare_analysis(project,model,work/'mesh.inp',bond_band_mm=0)
        assert 'POSITION TOLERANCE' not in prepared['deck']
        scheduler=SolveScheduler(get_ccx_path(load_config()),max_workers=1)
        task=scheduler.submit('partial',prepared['deck'],work/'cases')
        scheduler.wait_task(task.task_id,timeout=60)
        assert task.state=='completed',task.summary()
        completed=finish_analysis(project,task,prepared)
        diagnostics=completed['summary']['solver_diagnostics']
        assert diagnostics['requires_review'] and diagnostics['reported_missed_nodes']>0, diagnostics
        assert any(c['reported_missed_nodes']>0 for c in diagnostics['connections'])
        assert np.linalg.norm(completed['fields'].rf_sum-np.array([0.,100.,0.]))<.01
        ctx=json.loads(completed['archive']['results_json'].read_text(encoding='utf-8'))
        report=completed['archive']['report_md'].read_text(encoding='utf-8')
        assert ctx['solver_diagnostics']==diagnostics and '连接警告：需要检查' in report
        assert '不能据此判定连接或结构合格' in report
        assert ctx['resource_usage']['peak_job_memory_bytes']>0
        before=hashes(task.case_dir)
        restored=load_archived_case(project,task.case_dir.name)
        assert restored['context']['solver_diagnostics']['reported_missed_nodes']==diagnostics['reported_missed_nodes']
        assert hashes(task.case_dir)==before,'history inspection modified raw artifacts'
        # Inspect the actual DS incomplete case independently; no source artifact writes.
        historical=Path('E:/rm_sim/verification/qr_asm_static_c12m4_20261102_134045/cases/case_001')
        old=collect_tie_diagnostics(historical,'assembly_full')
        assert old['reported_missed_nodes']==6759,old
        (work/'verification.json').write_text(json.dumps({'real_solver':diagnostics,'historical_failed_case':old},
            ensure_ascii=False,indent=2),encoding='utf-8')
        print('VERIFY_TIE_DIAGNOSTICS: PASS',work,flush=True)
    finally:model.close()


if __name__=='__main__':main()
