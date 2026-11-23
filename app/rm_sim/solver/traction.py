"""Uniform global-direction surface traction as consistent C3D4/C3D10 nodal forces."""
from collections import Counter
import numpy as np
from .convert import TET_LOCAL_FACES,CloadPlan,ConversionError
from .nodal_loads import tetra_shape

_FACE_NODES=((0,2,1,6,5,4),(0,1,3,4,8,7),(1,2,3,5,9,8),(0,3,2,7,9,6))
_REFERENCE=np.array(((0.,0.,0.),(1.,0.,0.),(0.,1.,0.),(0.,0.,1.)))


def consistent_traction(load,mesh,node_ids,element_ids=None):
    load.validate()
    selected=set(map(int,node_ids));ids=list(mesh['elems'] if element_ids is None else element_ids)
    if not selected:raise ConversionError('一致面力没有加载节点')
    counts=Counter(tuple(sorted(mesh['elems'][eid][k] for k in f)) for eid in ids for f in TET_LOCAL_FACES)
    entries=[];covered=set()
    for eid in ids:
        conn=mesh['elems'][eid]
        if len(conn) not in (4,10):raise ConversionError('一致面力仅支持C3D4/C3D10')
        for li,face in enumerate(_FACE_NODES):
            local=face if len(conn)==10 else face[:3]
            nodes={conn[i] for i in local}
            if nodes<=selected and counts[tuple(sorted(conn[i] for i in TET_LOCAL_FACES[li]))]==1:
                entries.append((eid,li));covered.update(nodes)
    if not entries or covered!=selected:
        raise ConversionError('加载NSET未被所属部件的完整外部单元面唯一覆盖，请检查加载面')
    g,w=np.polynomial.legendre.leggauss(3);g,w=(g+1)/2,w/2
    uv=np.array([(a,(1-a)*b) for a in g for b in g])
    weights=np.array([wa*wb*(1-a) for a,wa in zip(g,w) for wb in w])
    integrals={n:0. for n in selected};area=0.;first_moment=np.zeros(3)
    for count in (4,10):
        for li,face in enumerate(TET_LOCAL_FACES):
            a,b,c=_REFERENCE[list(face)];basis=np.column_stack((b-a,c-a))
            shape,dn=tetra_shape(a+uv@basis.T,count)
            dn2=np.einsum('qai,ij->qaj',dn,basis)
            batch_ids=[e for e,f in entries if f==li and len(mesh['elems'][e])==count]
            for start in range(0,len(batch_ids),512):
                conn=[mesh['elems'][e] for e in batch_ids[start:start+512]]
                xyz=np.asarray([[mesh['nodes'][n] for n in row] for row in conn])
                tangent=np.einsum('qai,eaj->eqij',dn2,xyz)
                measure=np.linalg.norm(np.cross(tangent[:,:,0],tangent[:,:,1]),axis=2)
                if not np.isfinite(measure).all() or (measure<=0).any():raise ConversionError('加载单元面面积无效')
                nodal=np.einsum('qa,eq,q->ea',shape,measure,weights)
                area+=float(np.einsum('eq,q->',measure,weights))
                first_moment+=np.einsum('qk,ekj,eq,q->j',shape,xyz,measure,weights)
                for row,values in zip(conn,nodal):
                    for nid,value in zip(row,values):
                        if nid in integrals:integrals[nid]+=float(value)
    if not np.isfinite(area) or area<=0:raise ConversionError('加载面总面积无效')
    direction=np.asarray(load.direction,dtype=float);direction/=np.linalg.norm(direction)
    nodes=tuple(sorted(selected))
    forces=np.asarray([integrals[n]/area*load.total_force_n*direction for n in nodes])
    plan=CloadPlan(face_tag=int(load.face_tag),node_ids=nodes,forces=forces,
        direction_unit=tuple(direction),total_force_n=float(load.total_force_n),part_id=str(load.part_id))
    return plan,dict(distribution='consistent_area',area_mm2=area,centroid_mm=(first_moment/area).tolist(),
        faces=len(entries),integration='quadratic isoparametric triangle, 9 points')
