"""无排除区域的双材料解析补丁算例：均匀轴压、允许泊松收缩、ν/E一致。"""
import json
from pathlib import Path
import numpy as np
from assembly_check_utils import ROOT,evidence_dir


def main():
    from rm_sim.cad.assembly_import import import_assembly,sync_parts_from_model
    from rm_sim.core.project import Project,Material,Constraint,LoadCase,PressureLoad
    from rm_sim.core.face_identity import fingerprint_from_face
    from rm_sim.core.connections import BondedConnection,ConnectionSide
    from rm_sim.mesh.mesher import generate_tet10_mesh
    from rm_sim.solver.deck import build_assembly_case_inp,parse_mesh_inp,_part_face_maps,NEUTRAL_FACE_TO_P
    from rm_sim.solver.tasks import SolveScheduler
    from rm_sim.config import load_config,get_ccx_path
    from rm_sim.solver.fields import build_result_fields,part_field_views
    work=evidence_dir('assembly_exact_patch')
    model=import_assembly(ROOT/'samples/assembly_serial_rods.step')
    try:
        project=Project();sync_parts_from_model(project,model)
        # 假设材料，非真实钢/铝：ν/E相同，压缩时横向应变在界面兼容。
        for p,name,e,nu in zip(project.parts,['验证材料A','验证材料B'],[210000.,70000.],[.3,.1]):
            p.material_name=name;p.material_snapshot=Material(name=name,e_modulus_mpa=e,poisson=nu)
        fs=[[f for f in model.faces if f.solid_tag==s.tag] for s in model.solids]
        fixed=next(f for f in fs[0] if f.normal[0]<-.999)
        load=next(f for f in fs[1] if f.normal[0]>.999)
        ia=next(f for f in fs[0] if f.normal[0]>.999);ib=next(f for f in fs[1] if f.normal[0]<-.999)
        side=lambda pid,f:ConnectionSide(pid,fingerprint_from_face(f))
        project.connections=[BondedConnection(name='解析界面',side_a=side('p1',ia),side_b=side('p2',ib))]
        case=LoadCase(constraints=[Constraint(name='轴向固定',face_tag=fixed.tag,part_id='p1')],
                      loads=[PressureLoad(name='均匀轴压',face_tag=load.tag,part_id='p2',pressure_mpa=10.)])
        nsets={'FIXEDX':fixed.tag,'LOADFACE':load.tag}
        for k,faces in enumerate(fs,1):
            nsets[f'SYM_Y{k}']=next(f for f in faces if f.normal[1]<-.999).tag
            nsets[f'SYM_Z{k}']=next(f for f in faces if f.normal[2]<-.999).tag
        mesh_path=work/'mesh.inp'
        generate_tet10_mesh(model,2.,nset_faces=nsets,part_elsets={'PART1':model.solids[0].tag,'PART2':model.solids[1].tag},inp_path=mesh_path)
        mesh_text=mesh_path.read_text(encoding='utf-8')
        deck=build_assembly_case_inp(mesh_text,project,case,model,nset_face_tags=nsets)
        base=deck.split('** ===== 边界条件')[0]
        # 法向固定只限制Ux；两侧对称面允许精确泊松收缩。生产GUI的「全固定」并不等价。
        cards=['*BOUNDARY','FIXEDX, 1, 1, 0.0','SYM_Y1, 2, 2, 0.0','SYM_Y2, 2, 2, 0.0',
               'SYM_Z1, 3, 3, 0.0','SYM_Z2, 3, 3, 0.0','*STEP','*STATIC','*DLOAD']
        parsed=parse_mesh_inp(mesh_text);ids=sorted(parsed['elems'])
        rows,face_map=_part_face_maps(project,model,parsed)[2]
        cards += [f'{ids[rows[ei]]}, {NEUTRAL_FACE_TO_P[li]}, 10.0' for ei,li in face_map[load.tag]]
        cards += ['*NODE PRINT, NSET=FIXEDX','RF','*NODE FILE','U, RF','*EL FILE','S','*END STEP','']
        scheduler=SolveScheduler(get_ccx_path(load_config()),max_workers=1)
        task=scheduler.submit('patch',base+'\n'.join(cards),work/'cases');task=scheduler.wait_task(task.task_id,timeout=120)
        assert task.state=='completed',task.summary()
        fields=build_result_fields(sorted(parsed['nodes']),task.results,task.frd_path,fixed_set='FIXEDX')
        views=part_field_views(fields,{n:set(parsed['elsets'][n]) for n in ('PART1','PART2')},parsed['elems'])
        values={}
        for name,v in views.items():
            assert np.all(np.abs(v.s_mises-10.)<.1),(name,float(v.s_mises.min()),float(v.s_mises.max()))
            values[name]={'nodes':len(v.node_ids),'min_mises':float(v.s_mises.min()),'max_mises':float(v.s_mises.max())}
        assert abs(fields.rf_sum[0]-1000)<.01
        row={int(n):i for i,n in enumerate(fields.node_ids)}
        ux=np.mean([fields.u[row[n],0] for n in parsed['nsets']['LOADFACE']])
        theory=-1000*(100/(210000*100)+100/(70000*100))
        assert abs(ux/theory-1)<.001
        (work/'verification.json').write_text(json.dumps({'status':'PASS','parts':values,'tip_ux':ux,'theory_ux':theory,
                   'scope':'假设材料ν/E一致、允许横向收缩的解析补丁；不代替真实钢铝全固定局部峰值判据'},ensure_ascii=False,indent=2),encoding='utf-8')
        print('VERIFY_ASSEMBLY_PATCH: PASS 所有节点（无排除）Mises=10MPa±1%，位移与反力解析门通过',flush=True)
    finally:model.close()


if __name__=='__main__':main()
