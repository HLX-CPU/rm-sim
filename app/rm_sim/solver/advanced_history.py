"""Unit and load-history contracts, independent of any dynamic solver."""
import numpy as np


def evaluate_history(history,times):
    values=np.asarray(history.samples,dtype=float);t=np.asarray(times,dtype=float)
    if (values.ndim!=2 or values.shape[1]!=2 or len(values)<2 or not np.isfinite(values).all()
            or np.any(np.diff(values[:,0])<=0) or not np.isfinite(t).all()):
        raise ValueError('历程需要有限数据与严格递增时间')
    outside=(t<values[0,0])|(t>values[-1,0])
    if history.outside=='error' and outside.any():raise ValueError('请求时间超出历程范围')
    if history.outside not in ('hold','zero','error'):raise ValueError('未知历程边界策略')
    result=np.interp(t,values[:,0],values[:,1])
    if history.outside=='zero':result=np.where(outside,0.,result)
    return result


def transfer_wrench(force_n,moment_nm,rotation,translation_mm,source_point_mm,target_point_mm):
    """SI source torque Nm -> structural Nmm, including reference-point shift."""
    r=np.asarray(rotation,dtype=float).reshape(3,3)
    f=np.asarray(force_n,dtype=float);m=np.asarray(moment_nm,dtype=float)
    t,source,target=(np.asarray(x,dtype=float) for x in (translation_mm,source_point_mm,target_point_mm))
    if any(a.shape!=(3,) or not np.isfinite(a).all() for a in (f,m,t,source,target)):
        raise ValueError('力、矩与作用点须是有限三维向量')
    if not np.allclose(r.T@r,np.eye(3),atol=1e-8) or not np.isclose(np.linalg.det(r),1.,atol=1e-8):
        raise ValueError('载荷变换需要右手正交旋转')
    transformed=r@f
    return transformed,r@(1000.*m)+np.cross(r@source+t-target,transformed)


def frequency_grid(settings):
    if settings.spacing=='linear':return np.linspace(settings.start_hz,settings.stop_hz,settings.points)
    if settings.spacing=='log' and settings.start_hz>0:return np.geomspace(settings.start_hz,settings.stop_hz,settings.points)
    raise ValueError('频率网格设置无效')


def mechanism_si_payload(settings):
    """Adapter input only; missing CAD-derived mass/inertia remain unresolved."""
    from ..core.store import to_dict
    def frame(f):return {'origin_m':(np.array(f.origin_mm)*.001).tolist(),'quaternion_wxyz':list(f.quaternion_wxyz)}
    bodies=[dict(id=b.id,part_id=b.part_id,fixed=b.fixed,frame=frame(b.frame),mass_source=b.mass_source,
        mass_kg=b.mass_kg if b.mass_source=='explicit' else None,
        inertia_kg_m2=(np.asarray(b.inertia_kg_mm2)*1e-6).tolist() if b.mass_source=='explicit' and b.inertia_kg_mm2 else None)
        for b in settings.bodies]
    joints=[]
    for j in settings.joints:
        limits=None if j.limits is None else (np.array(j.limits)*(.001 if j.kind=='prismatic' else 1.)).tolist()
        joints.append(dict(id=j.id,kind=j.kind,body_a=j.body_a,body_b=j.body_b,frame_world=frame(j.frame_world),
            limits=limits,drive=j.drive,history_id=j.history_id))
    histories=[]
    scales={'factor':(1.,'factor'),'N':(1.,'N'),'Nmm':(.001,'Nm'),'mm':(.001,'m'),
            'mm/s':(.001,'m/s'),'mm/s2':(.001,'m/s2'),'rad':(1.,'rad'),'rad/s':(1.,'rad/s')}
    for h in settings.histories:
        scale,unit=scales[h.unit]
        histories.append(dict(id=h.id,unit=unit,samples=[[t,v*scale] for t,v in h.samples],outside=h.outside))
    springs=[dict(id=s.id,body_a=s.body_a,body_b=s.body_b,
        anchor_a_m=(np.array(s.anchor_a_mm)*.001).tolist(),anchor_b_m=(np.array(s.anchor_b_mm)*.001).tolist(),
        rest_length_m=s.rest_length_mm*.001,stiffness_n_m=s.stiffness_n_mm*1000.,damping_n_s_m=s.damping_n_s_mm*1000.) for s in settings.springs]
    contacts=[dict(body_a=c.body_a,body_b=c.body_b,friction=c.friction,restitution=c.restitution,
        geometry=c.geometry,ground_frame=frame(c.ground_frame)) for c in settings.contacts]
    return dict(units='m-kg-s-N',gravity_m_s2=(np.array(settings.gravity_mm_s2)*.001).tolist(),bodies=bodies,
        joints=joints,histories=histories,springs=springs,contacts=contacts,contact_method=settings.contact_method,
        unresolved_mass_properties=[b.id for b in settings.bodies if b.mass_source=='cad_density'],
        state='adapter_input_only',solver_executed=False)
