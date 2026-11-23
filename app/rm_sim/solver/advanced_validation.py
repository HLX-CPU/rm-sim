"""Configuration validation only: never import CAD, launch an engine or claim physical verification."""
import math
import numpy as np
from ..core.advanced import *


def validate_studies(project):
    issues=[];studies=[s for s in project.advanced_studies if s.enabled]
    by_id={s.id:s for s in studies};parts={p.part_id:p for p in project.parts}
    def error(path,message):issues.append(dict(level='error',path=path,message=message))
    def finite(x):
        return isinstance(x,(int,float)) and not isinstance(x,bool) and math.isfinite(x)
    def positive(x,path,zero=False):
        if not finite(x) or (x<0 if zero else x<=0):error(path,'需要有限'+('非负' if zero else '正')+'数')
    def vector(x,n,path):
        try:ok=len(x)==n and all(finite(v) for v in x)
        except TypeError:ok=False
        if not ok:error(path,f'需要{n}个有限数值')
        return ok
    def frame(f,path):
        vector(f.origin_mm,3,path+'.origin_mm')
        if vector(f.quaternion_wxyz,4,path+'.quaternion') and not np.isclose(np.linalg.norm(f.quaternion_wxyz),1.,atol=1e-6,rtol=0):
            error(path,'姿态四元数须为单位四元数，顺序w,x,y,z')
    def timing(t,path):
        for name in ('duration_s','initial_step_s','min_step_s','max_step_s','output_step_s'):positive(getattr(t,name),path+'.'+name)
        if all(finite(getattr(t,n)) for n in ('min_step_s','initial_step_s','max_step_s','duration_s')):
            if not 0<t.min_step_s<=t.initial_step_s<=t.max_step_s<=t.duration_s:error(path,'需要0<最小步长≤初始步长≤最大步长≤持续时间')
        if finite(t.output_step_s) and finite(t.duration_s) and t.output_step_s>t.duration_s:error(path,'输出间隔不得超过持续时间')
    def history(h,path):
        if h.unit not in ('factor','N','Nmm','mm','rad','mm/s','rad/s','mm/s2'):error(path,'未知历程单位')
        if h.outside not in ('hold','zero','error'):error(path,'未知历程边界策略')
        if len(h.samples)<2:error(path,'历程至少需要两个采样点')
        last=-math.inf
        for row in h.samples:
            if not vector(row,2,path+'.samples'):continue
            if row[0]<0 or row[0]<=last:error(path,'时间须非负且严格递增')
            last=row[0]
    def loadcase(index,path):
        if not isinstance(index,int) or isinstance(index,bool) or not 0<=index<len(project.study.load_cases):
            error(path,'激励工况索引无效');return
        case=project.study.load_cases[index]
        if not case.loads:error(path,'需要先在工况中设置激励载荷')
        for ld in case.loads:
            try:ld.validate()
            except ValueError as exc:error(path,str(exc))
            if type(ld).__name__!='GravityLoad' and (getattr(ld,'face_tag',0)<=0 or getattr(ld,'face',None) is None):error(path,'激励载荷尚未保存几何面身份')
    def dep(s,other,kind,path):
        found=by_id.get(other)
        if not found or found.kind!=kind or not isinstance(found.settings,SETTINGS[kind]):
            error(path,f'需要引用已启用且设置有效的{kind}研究');return None
        return found
    if len(by_id)!=len(studies):error('studies','研究ID重复')
    for study in studies:
        path=study.id or '(empty id)';opt=study.settings
        if not study.id or not study.name.strip():error(path,'研究ID和名称不能为空')
        if study.kind not in SETTINGS or not isinstance(opt,SETTINGS.get(study.kind,type(None))):
            error(path,'研究类型与设置类型不匹配');continue
        for parent in study.depends_on:
            if parent not in by_id:error(path,'依赖研究不存在或未启用：'+parent)
        if study.kind=='modal':
            if not isinstance(opt.modes,int) or isinstance(opt.modes,bool) or not 1<=opt.modes<=500:error(path,'模态阶数须为1–500的整数')
            if opt.boundary not in ('project_fixed','free_free'):error(path,'未知模态边界模式')
            if opt.boundary=='project_fixed':
                if not isinstance(opt.boundary_case_index,int) or not 0<=opt.boundary_case_index<len(project.study.load_cases):error(path,'模态边界工况索引无效')
                elif not any(c.kind=='fixed' and c.face is not None for c in project.study.load_cases[opt.boundary_case_index].constraints):error(path,'固定边界模态需要所选工况中的固定面身份')
            if opt.prestress_case and opt.prestress_case not in {r.name for r in project.cases}:error(path,'预应力源case不存在')
        elif study.kind=='harmonic':
            dep(study,opt.modal_study_id,'modal',path)
            positive(opt.start_hz,path+'.start_hz',zero=True);positive(opt.stop_hz,path+'.stop_hz')
            if finite(opt.start_hz) and finite(opt.stop_hz) and opt.stop_hz<=opt.start_hz:error(path,'终止频率须大于起始频率')
            if opt.spacing not in ('linear','log'):error(path,'频率网格须为linear或log')
            if opt.spacing=='log' and finite(opt.start_hz) and opt.start_hz<=0:error(path,'对数扫频起始频率须大于0')
            if not isinstance(opt.points,int) or isinstance(opt.points,bool) or not 2<=opt.points<=100000:error(path,'频率采样点数须为2–100000的整数')
            positive(opt.damping_ratio,path+'.damping_ratio',zero=True)
            if not finite(opt.phase_deg):error(path,'相位须为有限角度')
            loadcase(opt.load_case_index,path)
        elif study.kind in ('transient','nonlinear'):
            timing(opt.time,path+'.time');history(opt.amplitude,path+'.amplitude')
            if opt.amplitude.unit!='factor':error(path,'结构激励幅值历程须为无量纲factor')
            loadcase(opt.load_case_index,path)
            if study.kind=='transient':
                vector(opt.initial_velocity_mm_s,3,path+'.initial_velocity')
                if opt.method not in ('implicit','explicit'):error(path,'未知瞬态积分类型')
                transfer=opt.load_transfer
                if transfer:
                    source=dep(study,transfer.mechanism_study_id,'mechanism',path)
                    if source and transfer.source_body_id not in {b.id for b in source.settings.bodies}:error(path,'传递源刚体不存在')
                    if transfer.target_part_id not in parts:error(path,'结构载荷目标部件不存在')
                    if vector(transfer.rotation,9,path+'.rotation'):
                        r=np.array(transfer.rotation).reshape(3,3)
                        if not np.allclose(r.T@r,np.eye(3),atol=1e-8) or not np.isclose(np.linalg.det(r),1.,atol=1e-8):error(path,'载荷变换须为右手正交旋转矩阵')
                    for n in ('translation_mm','source_point_mm','target_point_mm'):vector(getattr(transfer,n),3,path+'.'+n)
            else:
                if opt.procedure not in ('static','implicit_dynamic','explicit_dynamic'):error(path,'未知非线性分析过程')
                if opt.plasticity_enabled and not opt.plastic_curves:error(path,'启用塑性需要真应力—等效塑性应变曲线')
                seen=set()
                for curve in opt.plastic_curves:
                    if curve.part_id not in parts or curve.part_id in seen:error(path,'塑性曲线目标部件不存在或重复')
                    seen.add(curve.part_id)
                    if len(curve.points)<2:error(path,'塑性曲线至少需要两个点')
                    last=-math.inf
                    for i,row in enumerate(curve.points):
                        if not vector(row,2,path+'.plastic_curve'):continue
                        strain,stress=row
                        if strain<0 or strain<=last or (i==0 and strain!=0) or stress<=0:error(path,'塑性应变须从0严格递增，真应力须为正')
                        last=strain
                    if not curve.provenance.strip():error(path,'塑性曲线须注明数据来源')
                d=opt.damage
                if d.enabled:
                    if not opt.plasticity_enabled:error(path,'损伤框架要求显式启用塑性并提供标定数据')
                    if d.model!='ductile_scalar':error(path,'未知损伤模型')
                    positive(d.failure_plastic_strain,path+'.failure_strain')
                    positive(d.fracture_energy_n_mm,path+'.fracture_energy')
                    if not d.calibration_source.strip():error(path,'损伤需要标定来源及断裂能正则化，不能从屈服强度猜测')
        elif study.kind=='mechanism':
            timing(opt.time,path+'.time');vector(opt.gravity_mm_s2,3,path+'.gravity')
            if opt.contact_method not in ('NSC','SMC'):error(path,'接触方法须为NSC或SMC')
            bodies={b.id:b for b in opt.bodies};hs={h.id:h for h in opt.histories}
            if len({j.id for j in opt.joints})!=len(opt.joints) or any(not j.id for j in opt.joints):error(path,'关节ID不能为空或重复')
            if len({s.id for s in opt.springs})!=len(opt.springs) or any(not s.id for s in opt.springs):error(path,'弹簧ID不能为空或重复')
            if not bodies:error(path,'机构研究至少需要一个刚体')
            if len(bodies)!=len(opt.bodies) or 'ground' in bodies:error(path,'刚体ID重复或使用了保留名ground')
            if len(hs)!=len(opt.histories):error(path,'驱动历程ID重复')
            for h in opt.histories:history(h,path+'.history.'+h.id)
            graph={bid:set() for bid in ['ground',*bodies]}
            for b in opt.bodies:
                if not b.id:error(path,'刚体ID不能为空')
                frame(b.frame,path+'.body.'+b.id)
                if b.part_id not in parts:error(path,'刚体需要绑定工程部件：'+b.id)
                if b.mass_source=='explicit':
                    positive(b.mass_kg,path+'.mass')
                    if vector(b.inertia_kg_mm2,6,path+'.inertia'):
                        xx,yy,zz,xy,xz,yz=b.inertia_kg_mm2
                        eigen=np.linalg.eigvalsh([[xx,xy,xz],[xy,yy,yz],[xz,yz,zz]])
                        if eigen[0]<=0 or eigen[-1]>eigen[:2].sum()+1e-9:error(path,'惯量须正定并满足主惯量三角不等式')
                elif b.mass_source!='cad_density':error(path,'未知质量/惯量来源')
                if b.fixed:graph['ground'].add(b.id);graph[b.id].add('ground')
            for joint in opt.joints:
                a,b=joint.body_a,joint.body_b
                if a not in graph or b not in graph or a==b:error(path,'关节两端须为不同的有效刚体/ground');continue
                graph[a].add(b);graph[b].add(a);frame(joint.frame_world,path+'.joint.'+joint.id)
                if joint.kind not in ('revolute','prismatic','fixed','spherical'):error(path,'未知关节类型')
                if joint.limits is not None:
                    if vector(joint.limits,2,path+'.joint.limits') and joint.limits[0]>joint.limits[1]:error(path,'关节下限大于上限')
                    if joint.kind not in ('revolute','prismatic'):error(path,'此关节类型没有一维限位')
                if joint.drive!='none':
                    units={('revolute','position'):'rad',('revolute','velocity'):'rad/s',('revolute','effort'):'Nmm',
                           ('prismatic','position'):'mm',('prismatic','velocity'):'mm/s',('prismatic','effort'):'N'}
                    expected=units.get((joint.kind,joint.drive));h=hs.get(joint.history_id)
                    if not expected or not h or h.unit!=expected:error(path,'关节驱动类型/历程单位不匹配')
            for spring in opt.springs:
                if spring.body_a not in graph or spring.body_b not in graph or spring.body_a==spring.body_b:error(path,'弹簧连接对象无效')
                vector(spring.anchor_a_mm,3,path+'.spring.anchor_a');vector(spring.anchor_b_mm,3,path+'.spring.anchor_b')
                positive(spring.rest_length_mm,path+'.spring.rest_length',zero=True)
                positive(spring.stiffness_n_mm,path+'.spring.stiffness');positive(spring.damping_n_s_mm,path+'.spring.damping',zero=True)
            for contact in opt.contacts:
                if contact.body_a not in graph or contact.body_b not in graph or contact.body_a==contact.body_b:error(path,'接触对对象无效')
                positive(contact.friction,path+'.friction',zero=True)
                if not finite(contact.restitution) or not 0<=contact.restitution<=1:error(path,'恢复系数须在0到1之间')
                if contact.geometry not in ('cad_convex_hull','cad_mesh'):error(path,'未知碰撞几何来源')
                frame(contact.ground_frame,path+'.ground_frame')
            reached={'ground'};pending=['ground']
            while pending:
                for other in graph[pending.pop()]-reached:reached.add(other);pending.append(other)
            if not opt.allow_free_bodies and set(bodies)-reached:error(path,'存在未连接地面的刚体；若有意自由运动须显式允许')
    graph={s.id:set(s.depends_on) for s in studies}
    for s in studies:
        if s.kind=='harmonic' and isinstance(s.settings,HarmonicSettings):graph[s.id].add(s.settings.modal_study_id)
        if s.kind=='transient' and isinstance(s.settings,TransientSettings) and s.settings.load_transfer:graph[s.id].add(s.settings.load_transfer.mechanism_study_id)
    active=set();done=set()
    def visit(key):
        if key in active:error(key,'研究依赖存在循环');return
        if key in done or key not in graph:return
        active.add(key)
        for parent in graph[key]:visit(parent)
        active.remove(key);done.add(key)
    for key in graph:visit(key)
    return issues
